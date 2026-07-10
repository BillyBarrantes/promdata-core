"""
[V1] NLP Temporal Alignment Resolver.

Resuelve expresiones temporales crudas que el LLM produce en los filtros
(nombres de meses, rangos "between", expresiones relativas) a fechas ISO
que ibis_engine._build_filter_expression puede procesar nativamente.

Se invoca desde normalize_router_filters (validator.py) como paso de
post-procesamiento ANTES de la validación DataFilter.

Principios:
- Degradación segura: si no puede resolver, retorna el filtro original.
- Domain-agnostic: no asume ningún schema específico.
- Año inferido: usa schema_profile para detectar el rango temporal real
  del dataset en vez de asumir el año actual.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any
from dateutil import parser as dateparser

# ── Mapeo de nombres de meses (ES + EN) ────────────────────────────────
_MONTH_NAMES: dict[str, int] = {
    # Español
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
    "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
    # Abreviaciones ES
    "ene": 1, "feb": 2, "mar": 3, "abr": 4,
    "may": 5, "jun": 6, "jul": 7, "ago": 8,
    "sep": 9, "oct": 10, "nov": 11, "dic": 12,
    # English
    "january": 1, "february": 2, "march": 3, "april": 4,
    "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    # Abreviaciones EN
    "jan": 1, "feb": 2, "apr": 4,
    "jun": 6, "aug": 8,
    "sept": 9, "nov": 11, "dec": 12,
}

# Días por mes (no bisiesto; el filtro <= funciona igual)
_MONTH_DAYS = {
    1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30,
    7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31,
}


def _extract_year_from_any_string(raw: Any) -> int | None:
    """Extrae el año de cualquier string de fecha usando parser flexible.

    Tolerante a ISO (2021-01-15), latino (15/01/2021), US (01/15/2021),
    texto (January 15, 2021), etc. Retorna None si no puede extraer.
    """
    if raw is None:
        return None
    try:
        dt = dateparser.parse(str(raw).strip(), fuzzy=True)
        if dt is not None:
            return dt.year
    except (ValueError, TypeError, OverflowError, AttributeError):
        pass
    return None


def _extract_year_from_col_profile(
    column: str,
    schema_profile: dict[str, Any] | None,
) -> int | None:
    """Extrae el año de min/max de una columna específica en el schema_profile."""
    if not schema_profile or column not in schema_profile:
        return None
    col_meta = schema_profile.get(column)
    if not isinstance(col_meta, dict):
        return None
    for key in ("max", "min"):
        raw_val = col_meta.get(key)
        if not raw_val:
            continue
        year = _extract_year_from_any_string(raw_val)
        if year is not None:
            return year
    return None


# ═══════════════════════════════════════════════════════════════════
# ADR-TEMPORAL-002: _dataset_year Contract Cascade
# Date: 2026-07-01
# Status: ACCEPTED — DO NOT MODIFY without test_temporal_fortress.py GREEN
#
# DECISION: _infer_dataset_year() tiene una cascada de 4 niveles:
#   1. Columna especifica del filtro (min/max)
#   2. Cualquier columna role="time"
#   3. Cualquier columna con min/max ISO
#   4. _dataset_year inyectado desde _detect_reference_date()
#
# RAZON: El LLM alucina anos ISO (2023) cuando el dataset es de 2021.
# El paso 4 es el fallback critico que usa reference_date estructural
# (no nombres de columnas) para corregir la alucinacion.
#
# RIESGO DE ALTERAR: Si se elimina el paso 4 o se cambia la cascada,
# los filtros temporales de la ruta SIMPLE apuntaran al ano equivocado
# y retornaran DataFrames vacios. Regresion silenciosa.
#
# INYECCION: canonical_analytical_contract_adapter.py:255
#   schema_profile["_dataset_year"] = int(reference_date[:4])
#
# VALIDACION: test_temporal_fortress.py (T1, T2, T7)
# ═══════════════════════════════════════════════════════════════════
def _infer_dataset_year(
    column: str,
    schema_profile: dict[str, Any] | None,
) -> int | None:
    """
    Infiere el año del dataset desde el schema_profile con cascada de prioridad:

    1. Columna específica del filtro (min/max más precisos)
    2. Cualquier columna marcada como role="time" (fuente temporal más confiable)
    3. Cualquier columna con min/max ISO (último recurso)

    Retorna None si no puede inferir (fallback a año actual del sistema).
    """
    # 1. Intentar la columna específica del filtro
    year = _extract_year_from_col_profile(column, schema_profile)
    if year:
        return year

    if not schema_profile:
        return None

    # 2. Fallback: buscar columnas con role="time" (más confiable que cualquier columna)
    for col_name, col_meta in schema_profile.items():
        if not isinstance(col_meta, dict):
            continue
        if col_meta.get("role") == "time":
            year = _extract_year_from_col_profile(col_name, schema_profile)
            if year:
                return year

    # 3. Último recurso: cualquier columna con min/max ISO
    for col_name in schema_profile:
        year = _extract_year_from_col_profile(col_name, schema_profile)
        if year:
            return year

    # 4. Final fallback: _dataset_year inyectado en tiempo de contrato
    #     desde _detect_reference_date() — cubre cuando el schema_profile
    #     no tiene min/max (planner stage) pero el reference_date existe.
    dataset_year = schema_profile.get("_dataset_year") if schema_profile else None
    if dataset_year is not None:
        return int(dataset_year)

    return None


def _infer_dataset_year_range(
    schema_profile: dict[str, Any] | None,
) -> tuple[int | None, int | None]:
    """
    Extrae (min_year, max_year) del dataset desde el schema_profile.

    Busca en orden de prioridad:
    1. Claves top-level _dataset_year_min / _dataset_year_max
       (inyectadas por canonical_analytical_contract_adapter.py desde datos reales)
    2. Columnas con role="time" o role="date" que tengan min/max en el perfil
    3. Columnas con dtype date/timestamp/datetime que tengan min/max

    Esto asegura que el Fortress preventivo en resolve_temporal_filter_value
    se active incluso sin etiquetas semánticas en los nombres de columna.
    """
    if not schema_profile:
        return None, None

    # Prioridad 1: rangos inyectados desde datos reales
    _min = schema_profile.get("_dataset_year_min")
    _max = schema_profile.get("_dataset_year_max")
    if _min is not None and _max is not None:
        return int(_min), int(_max)

    years: list[int] = []
    for col_name, col_meta in schema_profile.items():
        if not isinstance(col_meta, dict):
            continue
        role = str(col_meta.get("role", "")).lower()
        col_dtype = str(col_meta.get("dtype", "")).lower()
        is_temporal = (
            role in ("time", "date")
            or 'date' in col_dtype
            or 'timestamp' in col_dtype
            or 'datetime' in col_dtype
        )
        if not is_temporal:
            continue
        for key in ("min", "max"):
            val = col_meta.get(key)
            if val:
                year = _extract_year_from_any_string(val)
                if year is not None:
                    years.append(year)
    if not years:
        return None, None
    return min(years), max(years)


def _parse_month_token(token: str) -> int | None:
    """Convierte un token a número de mes, o None si no es un mes."""
    cleaned = token.strip().lower().rstrip(".,;:")
    return _MONTH_NAMES.get(cleaned)


def _month_range_iso(year: int, month: int) -> tuple[str, str]:
    """Retorna (primer_dia, ultimo_dia) en ISO para un mes dado."""
    first = f"{year:04d}-{month:02d}-01"
    last_day = _MONTH_DAYS.get(month, 30)
    # Ajuste bisiesto para febrero
    if month == 2 and (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)):
        last_day = 29
    last = f"{year:04d}-{month:02d}-{last_day:02d}"
    return first, last


def resolve_temporal_filter_value(
    column: str,
    operator: str,
    value: Any,
    schema_profile: dict[str, Any] | None = None,
) -> list[dict[str, Any]] | None:
    """
    Intenta resolver un filtro temporal crudo a filtros ISO.

    Retorna:
    - Lista de dicts {column, operator, value} si resolvió
    - None si no pudo resolver (el llamador preserva el filtro original)

    Casos soportados:
    1. op="in", value="junio, julio" → rango >= YYYY-06-01, <= YYYY-07-31
    2. op="in", value=["junio", "julio"] → rango >= YYYY-06-01, <= YYYY-07-31
    3. op="==", value="junio" → rango >= YYYY-06-01, <= YYYY-06-30
    4. op="between", value="2021-06-01 and 2021-07-31" → >= y <=
    """
    if value is None:
        return None

    op = str(operator).strip().lower()
    year = _infer_dataset_year(column, schema_profile) or datetime.now().year

    # ── Caso 1: between con " and " ──────────────────────────────────
    if op == "between" and isinstance(value, str) and " and " in value.lower():
        parts = re.split(r"\s+and\s+", value, flags=re.IGNORECASE)
        if len(parts) == 2:
            lo, hi = parts[0].strip(), parts[1].strip()
            # Resolver si son meses
            lo_month = _parse_month_token(lo)
            hi_month = _parse_month_token(hi)
            if lo_month and hi_month:
                lo = _month_range_iso(year, lo_month)[0]
                hi = _month_range_iso(year, hi_month)[1]
            print(
                f"🧹 [TEMPORAL RESOLVER] between expandido: "
                f"'{column}' → ['>= {lo}', '<= {hi}']"
            )
            return [
                {"column": column, "operator": ">=", "value": lo},
                {"column": column, "operator": "<=", "value": hi},
            ]

    # ── Caso 3: ISO date con año fuera del rango del dataset ──────────
    if op in (">=", "<=", "==", ">", "<") and isinstance(value, str):
        iso_match = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", value.strip())
        if iso_match:
            filter_year = int(iso_match.group(1))
            # [FIX 2026-07-04] Verificar rango antes de corregir.
            # Si el año está dentro del rango del dataset, NO corregirlo.
            # Solo corregir años que están fuera de los límites reales.
            min_year, max_year = _infer_dataset_year_range(schema_profile)
            if min_year and max_year and min_year <= filter_year <= max_year:
                # Año dentro del rango del dataset → preservar (no es alucinación)
                return None  # Dejar que el filtro original pase sin cambios

            dataset_year = _infer_dataset_year(column, schema_profile)
            if dataset_year and filter_year != dataset_year:
                corrected = (
                    f"{dataset_year}-{iso_match.group(2)}-{iso_match.group(3)}"
                )
                print(
                    f"📅 [TEMPORAL RESOLVER] Año ISO corregido: "
                    f"{value} → {corrected} (dataset_year={dataset_year})"
                )
                return [{"column": column, "operator": op, "value": corrected}]

    # ── Caso 2: in/== con nombre(s) de mes ───────────────────────────
    # Preparar lista de tokens
    tokens: list[str] = []
    if isinstance(value, list):
        tokens = [str(v).strip() for v in value]
    elif isinstance(value, str):
        # Split por coma o " y " / " and "
        tokens = re.split(r"[,]\s*|\s+y\s+|\s+and\s+", value)
        tokens = [t.strip() for t in tokens if t.strip()]

    if not tokens:
        return None

    # Intentar resolver cada token como mes
    resolved_months: list[int] = []
    for token in tokens:
        month = _parse_month_token(token)
        if month:
            resolved_months.append(month)

    if not resolved_months:
        return None  # Ningún token es un mes → no resolver

    # Si no TODOS los tokens son meses, no resolver (evitar parcialidad)
    if len(resolved_months) != len(tokens):
        return None

    # Construir rango desde el mes mínimo hasta el mes máximo
    min_month = min(resolved_months)
    max_month = max(resolved_months)
    range_start = _month_range_iso(year, min_month)[0]
    range_end = _month_range_iso(year, max_month)[1]

    print(
        f"🧹 [TEMPORAL RESOLVER] Meses resueltos: "
        f"{tokens} → ['>= {range_start}', '<= {range_end}'] (año={year})"
    )
    return [
        {"column": column, "operator": ">=", "value": range_start},
        {"column": column, "operator": "<=", "value": range_end},
    ]


def normalize_intent_temporal_filters(
    intent: Any,
    schema_profile: dict | None = None,
) -> Any:
    """
    Normaliza filtros temporales en un intent corrigiendo años ISO alucinados
    por el LLM. Punto de intercepción universal para rutas SIMPLE y COMPLEJO.

    Aplica `resolve_temporal_filter_value` a cada filtro del intent cuya
    columna sea temporal según la misma lógica estructural de
    `validator.is_temporal_col` (role, type, dtype).

    Retorna un nuevo intent con los filtros corregidos via `model_copy`.
    Si no se requiere corrección, retorna el intent original sin modificar.
    """
    if not schema_profile or not hasattr(intent, 'filters') or not intent.filters:
        return intent

    corrected_filters: list[Any] = []
    modified = False

    for filt in intent.filters:
        col = str(getattr(filt, 'column', '') or '').strip()
        if not col:
            corrected_filters.append(filt)
            continue

        operator_raw = getattr(filt, 'operator', None)
        operator = (
            str(operator_raw.value).strip()
            if hasattr(operator_raw, 'value')
            else str(operator_raw or '==').strip()
        )
        value = getattr(filt, 'value', None)
        if value is None:
            corrected_filters.append(filt)
            continue

        col_meta = schema_profile.get(col, {})
        col_role = col_meta.get('role') if isinstance(col_meta, dict) else None
        col_dtype = str(col_meta.get('dtype', '')).lower() if isinstance(col_meta, dict) else ''
        col_meta_type = str(col_meta.get('type', '')).lower() if isinstance(col_meta, dict) else ''
        is_temporal = (
            col_role == 'time'
            or col_meta_type == 'temporal'
            or 'date' in col_dtype
            or 'timestamp' in col_dtype
            or 'datetime' in col_dtype
        )

        if not is_temporal:
            corrected_filters.append(filt)
            continue

        resolved = resolve_temporal_filter_value(
            col, operator, value, schema_profile=schema_profile
        )
        if not resolved:
            corrected_filters.append(filt)
            continue

        modified = True
        for rf_dict in resolved:
            try:
                from app.core.semantic_grammar import DataFilter

                corrected_filters.append(DataFilter.model_validate(rf_dict))
            except Exception:
                corrected_filters.append(rf_dict)

    if not modified:
        return intent

    try:
        return intent.model_copy(update={'filters': corrected_filters})
    except AttributeError:
        return intent
