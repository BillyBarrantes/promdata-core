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
import unicodedata
from datetime import datetime, timedelta
from typing import Any
from dateutil import parser as dateparser

from app.core.structured_logging import emit_structured_log

# ── Mapeo de nombres de meses (ES + EN + PT) ───────────────────────────
_MONTH_NAMES: dict[str, int] = {
    # Español
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
    "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
    # Español — variantes regionales
    "setiembre": 9,
    # Abreviaciones ES
    "ene": 1, "feb": 2, "mar": 3, "abr": 4,
    "may": 5, "jun": 6, "jul": 7, "ago": 8,
    "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12,
    # English
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    # Abreviaciones EN
    "jan": 1, "apr": 4, "aug": 8, "sept": 9, "dec": 12,
    # Portugués (claves sin acento: el texto se pliega antes de buscar)
    "janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4,
    "maio": 5, "junho": 6, "julho": 7, "agosto": 8,
    "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
    # Abreviaciones PT
    "fev": 2, "mai": 5, "out": 10, "dez": 12,
}

# Días por mes (no bisiesto; el filtro <= funciona igual)
_MONTH_DAYS = {
    1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30,
    7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31,
}


def _fold_accents(text: str) -> str:
    """Minúsculas + remoción de acentos/diacríticos de TODO el alfabeto latino.

    Cubre ES/EN/PT (á é í ó ú ü ñ ã õ ç à â ê ô y más) vía descomposición NFKD.
    """
    lowered = unicodedata.normalize("NFKD", str(text or "").lower())
    return "".join(ch for ch in lowered if not unicodedata.combining(ch))


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
    cleaned = _fold_accents(str(token or "")).strip().rstrip(".,;:")
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


def _correct_iso_year(
    value_iso: Any,
    column: str,
    schema_profile: dict[str, Any] | None,
) -> Any:
    """[F1.4] Corrige el año de un endpoint ISO 'between' si está fuera del rango
    real del dataset (misma lógica que el caso ISO simple, para no dejar pasar
    años alucinados en rangos)."""
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", str(value_iso).strip())
    if not match:
        return value_iso
    filter_year = int(match.group(1))
    min_year, max_year = _infer_dataset_year_range(schema_profile)
    if min_year and max_year and min_year <= filter_year <= max_year:
        return value_iso
    dataset_year = _infer_dataset_year(column, schema_profile)
    if dataset_year and filter_year != dataset_year:
        return f"{dataset_year}-{match.group(2)}-{match.group(3)}"
    return value_iso


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

    # ── Caso 0: Periodo ISO week (2021-W30, semana 30 del 2021) ───────
    # Un valor de semana se expande SIEMPRE a rango [lunes..domingo] ISO,
    # sin importar el operador (==, in, between). Domain-agnostic.
    if isinstance(value, str):
        week_range = _parse_week_token(value, column, schema_profile)
        if week_range:
            lo, hi = week_range
            print(
                f"🧹 [TEMPORAL RESOLVER] Semana ISO resuelta: '{value}' → "
                f"['>= {lo}', '<= {hi}']"
            )
            return [
                {"column": column, "operator": ">=", "value": lo},
                {"column": column, "operator": "<=", "value": hi},
            ]

    # ── Caso 0b: Trimestre / quincena / año (MEJORA 2) ────────────────
    # Solo para selección (==, in): expandir a rango ISO. Evita que un valor
    # como "2021-Q3" o "2021" llegue crudo a DuckDB (0 filas silencioso).
    if isinstance(value, str) and op in {"==", "=", "in", "eq", "equals", "in_list"}:
        for _period_parser in (_parse_quarter_token, _parse_quincena_token, _parse_year_token):
            _period_range = _period_parser(value, column, schema_profile)
            if _period_range:
                lo, hi = _period_range
                print(
                    f"🧹 [TEMPORAL RESOLVER] Periodo resuelto: '{value}' → "
                    f"['>= {lo}', '<= {hi}']"
                )
                return [
                    {"column": column, "operator": ">=", "value": lo},
                    {"column": column, "operator": "<=", "value": hi},
                ]

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
                # [F1.4] Rango cruzando fin de año (ej: "noviembre and febrero"):
                # si el mes final es menor que el inicial, pertenece al año siguiente.
                hi_year = year + 1 if hi_month < lo_month else year
                hi = _month_range_iso(hi_year, hi_month)[1]
            else:
                # [F1.4] Aplicar corrección de año a endpoints ISO del between.
                lo = _correct_iso_year(lo, column, schema_profile)
                hi = _correct_iso_year(hi, column, schema_profile)
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
    # [F1.4] Los meses no contiguos (ej: "enero y junio") se aproximan a un rango
    # continuo porque el motor Ibis no soporta membership por mes sin un filtro
    # derivado. Se LOGGEA para que la aproximación sea visible (antes silenciosa).
    if set(resolved_months) != set(range(min_month, max_month + 1)):
        emit_structured_log(
            "temporal_resolver_noncontiguous_months",
            level="warning",
            column=column,
            months=resolved_months,
            note="meses no contiguos: aproximado a rango continuo",
        )
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


_SYMBOLIC_TEMPORAL_TOKENS = {
    "latest", "last", "ultimo", "actual", "recent", "hoy", "today", "max", "min",
}
_ISO_TEMPORAL_VALUE_RE = re.compile(
    r"^\d{4}-\d{2}(-\d{2})?([ tT]\d{2}:\d{2}(:\d{2})?)?$"
)
_RAW_TEMPORAL_HINT_RE = re.compile(
    r"\b(dia|dias|semana|semanas|mes|meses|trimestre|trimestres|quincena|quincenas|"
    r"ano|anio|year|quarter)\b"
    r"|\bw\d{1,2}\b|\bq[1-4]\b"
)
_MONTH_WORD_TOKENS = set(_MONTH_NAMES.keys())


def _looks_like_raw_temporal_token(token: str) -> bool:
    """¿El token parece una expresión temporal que no logró resolverse?"""
    text = _fold_accents(str(token or "")).strip()
    if not text:
        return False
    if _RAW_TEMPORAL_HINT_RE.search(text):
        return True
    return any(part in _MONTH_WORD_TOKENS for part in re.split(r"[\s,;]+", text))


def _normalize_filter_list(
    filters: list[Any],
    schema_profile: dict,
    *,
    negate_ranges: bool = False,
) -> tuple[list[Any], bool, list[dict[str, Any]]]:
    """Normaliza una lista de filtros temporales.

    - Resuelve meses/semanas/trimestres/quincenas/años/rangos a ISO.
    - `negate_ranges=True` (negative_filters): un rango [lo,hi] se emite como UN
      único filtro `between` para que el motor lo niegue correctamente
      (~(col>=lo & col<=hi)); no se puede descomponer porque la negación se
      aplica por AND.
    - Descarta (con log) valores temporales crudos irresolubles para evitar el
      filtro silencioso que devuelve 0 filas (anti-0-silencioso).

    Retorna (nueva_lista, modificado, descartados).
    """
    if not filters:
        return list(filters or []), False, []

    from app.core.semantic_grammar import DataFilter

    out: list[Any] = []
    modified = False
    dropped: list[dict[str, Any]] = []

    for filt in filters:
        col = str(getattr(filt, 'column', '') or '').strip()
        operator_raw = getattr(filt, 'operator', None)
        operator = (
            str(operator_raw.value).strip()
            if hasattr(operator_raw, 'value')
            else str(operator_raw or '==').strip()
        )
        value = getattr(filt, 'value', None)

        if not col or value is None or not _is_temporal_profile_col(schema_profile, col):
            out.append(filt)
            continue

        resolved = resolve_temporal_filter_value(
            col, operator, value, schema_profile=schema_profile
        )
        if resolved:
            modified = True
            if negate_ranges and len(resolved) == 2:
                lo = resolved[0].get("value")
                hi = resolved[1].get("value")
                try:
                    out.append(DataFilter.model_validate(
                        {"column": col, "operator": "between", "value": [lo, hi]}
                    ))
                except Exception:
                    out.append(filt)
            else:
                for rf_dict in resolved:
                    try:
                        out.append(DataFilter.model_validate(rf_dict))
                    except Exception:
                        out.append(rf_dict)
            continue

        if isinstance(value, str):
            token = value.strip()
            folded = _fold_accents(token)
            if folded in _SYMBOLIC_TEMPORAL_TOKENS or _ISO_TEMPORAL_VALUE_RE.match(token):
                out.append(filt)
                continue
            if _looks_like_raw_temporal_token(token):
                modified = True
                dropped.append({"column": col, "operator": operator, "value": value})
                continue
        out.append(filt)

    # Dedup preservando orden (evita predicados duplicados filters↔positive_filters)
    deduped: list[Any] = []
    seen: set[tuple[str, str, str]] = set()
    for filt in out:
        c = str(getattr(filt, 'column', '') or '').strip().lower()
        o_raw = getattr(filt, 'operator', None)
        o = (str(o_raw.value) if hasattr(o_raw, 'value') else str(o_raw or '')).strip().lower()
        v = str(getattr(filt, 'value', '') or '').strip().lower()
        key = (c, o, v)
        if key in seen:
            modified = True
            continue
        seen.add(key)
        deduped.append(filt)

    return deduped, modified, dropped


def normalize_intent_temporal_filters(
    intent: Any,
    schema_profile: dict | None = None,
) -> Any:
    """
    Normaliza los filtros temporales de TODAS las listas del intent
    (`filters`, `positive_filters`, `negative_filters`): resuelve periodos
    crudos a ISO (meses, semanas, trimestres, quincenas, años, rangos) y
    descarta valores irresolubles (anti-0-silencioso).

    Punto de intercepción universal para rutas SIMPLE y COMPLEJO.

    Retorna un nuevo intent con las listas corregidas vía `model_copy`.
    Si no se requiere corrección, retorna el intent original sin modificar.
    """
    if not schema_profile or not hasattr(intent, 'filters'):
        return intent

    lists = {
        "filters": list(getattr(intent, 'filters', []) or []),
        "positive_filters": list(getattr(intent, 'positive_filters', []) or []),
        "negative_filters": list(getattr(intent, 'negative_filters', []) or []),
    }
    if not any(lists.values()):
        return intent

    updates: dict[str, Any] = {}
    all_dropped: list[dict[str, Any]] = []
    for attr_name, current in lists.items():
        new_list, changed, dropped = _normalize_filter_list(
            current,
            schema_profile,
            negate_ranges=(attr_name == "negative_filters"),
        )
        if changed:
            updates[attr_name] = new_list
        if dropped:
            all_dropped.extend(dropped)

    if all_dropped:
        emit_structured_log(
            "temporal_filter_unresolvable_dropped",
            level="warning",
            dropped=all_dropped[:10],
        )
        print(
            "🧹 [TEMPORAL RESOLVER] Filtros temporales irresolubles "
            f"descartados (anti-0-silencioso): {all_dropped[:10]}"
        )

    if not updates:
        return intent

    try:
        return intent.model_copy(update=updates)
    except AttributeError:
        return intent


# ═══════════════════════════════════════════════════════════════════════
# [P2 2026-09] Periodos ISO-week → rango de fechas
# ═══════════════════════════════════════════════════════════════════════

_ISO_WEEK_RE = re.compile(r"\b(\d{4})[-_ ]?w(\d{1,2})\b")
_WEEK_ONLY_RE = re.compile(r"\bw(\d{1,2})\b")
_SEMANA_RE = re.compile(r"\bsemana\s*(\d{1,2})\s*(?:de|del|de el|/)?\s*(\d{4})?\b")


def _iso_week_range(year: int, week: int) -> tuple[str, str] | None:
    """Retorna (lunes, domingo) ISO de una semana ISO-8601, o None si inválida."""
    if not (1 <= int(week) <= 53):
        return None
    try:
        start = datetime.fromisocalendar(int(year), int(week), 1).date()
        end = datetime.fromisocalendar(int(year), int(week), 7).date()
    except (ValueError, TypeError):
        return None
    return start.isoformat(), end.isoformat()


def _parse_week_token(
    token: str,
    column: str,
    schema_profile: dict[str, Any] | None = None,
) -> tuple[str, str] | None:
    """Parsea '2021-W30', 'W30', 'semana 30 del 2021' → rango ISO lunes-domingo.

    El año, cuando no viene en el token, se infiere del dataset (no del sistema).
    """
    text = _fold_accents(str(token or ""))
    match = _ISO_WEEK_RE.search(text)
    if match:
        return _iso_week_range(int(match.group(1)), int(match.group(2)))
    match = _SEMANA_RE.search(text)
    if match:
        week = int(match.group(1))
        if match.group(2):
            year = int(match.group(2))
        else:
            year = _infer_dataset_year(column, schema_profile) or datetime.now().year
        return _iso_week_range(year, week)
    match = _WEEK_ONLY_RE.search(text)
    if match:
        year = _infer_dataset_year(column, schema_profile) or datetime.now().year
        return _iso_week_range(year, int(match.group(1)))
    return None


# ═══════════════════════════════════════════════════════════════════════
# [MEJORA 2 2026-09] Trimestre / quincena / año → rango ISO
# ═══════════════════════════════════════════════════════════════════════

_QUARTER_WORD_TO_NUMBER = {
    "primer": 1, "primero": 1, "primera": 1, "1er": 1, "1ro": 1, "1ra": 1,
    "segundo": 2, "segunda": 2, "2do": 2, "2da": 2,
    "tercer": 3, "tercero": 3, "tercera": 3, "3er": 3, "3ro": 3, "3ra": 3,
    "cuarto": 4, "cuarta": 4, "4to": 4, "4ta": 4,
}
_QUARTER_MONTHS = {1: (1, 3), 2: (4, 6), 3: (7, 9), 4: (10, 12)}
_QUARTER_YQ_RE = re.compile(r"\b(\d{4})[-_ ]?q([1-4])\b")
_QUARTER_QY_RE = re.compile(r"\bq([1-4])[-_ ]?(\d{4})\b")
_QUARTER_Q_ONLY_RE = re.compile(r"\bq([1-4])\b")
_QUARTER_WORD_RE = re.compile(
    r"\b(primer|primero|primera|segundo|segunda|tercer|tercero|tercera|cuarto|cuarta|"
    r"1er|1ro|1ra|2do|2da|3er|3ro|3ra|4to|4ta)\s+trimestre(?:\s+(?:de\s+)?(\d{4}))?\b"
)
_QUARTER_NUM_RE = re.compile(
    r"\btrimestre\s*(?:n[°º]?\s*)?([1-4])(?:\s+(?:de\s+)?(\d{4}))?\b"
)
_QUINCENA_FIRST_RE = re.compile(
    r"\b(primera|primer|1ra|1era|inicio)\s+quincena"
    r"(?:\s+(?:de\s+|del\s+)?([a-z]+))?(?:\s+(?:de\s+|del\s+)?(\d{4}))?\b"
)
_QUINCENA_SECOND_RE = re.compile(
    r"\b(segunda|segundo|2da|2era|final)\s+quincena"
    r"(?:\s+(?:de\s+|del\s+)?([a-z]+))?(?:\s+(?:de\s+|del\s+)?(\d{4}))?\b"
)


def _parse_quarter_token(
    token: str,
    column: str,
    schema_profile: dict[str, Any] | None = None,
) -> tuple[str, str] | None:
    """Parsea '2021-Q3', 'Q3 2021', 'tercer trimestre de 2021' → rango ISO."""
    text = _fold_accents(str(token or ""))
    quarter: int | None = None
    year: int | None = None
    match = _QUARTER_YQ_RE.search(text)
    if match:
        year, quarter = int(match.group(1)), int(match.group(2))
    if quarter is None:
        match = _QUARTER_QY_RE.search(text)
        if match:
            quarter, year = int(match.group(1)), int(match.group(2))
    if quarter is None:
        match = _QUARTER_WORD_RE.search(text)
        if match:
            quarter = _QUARTER_WORD_TO_NUMBER.get(match.group(1))
            if match.group(2):
                year = int(match.group(2))
    if quarter is None:
        match = _QUARTER_NUM_RE.search(text)
        if match:
            quarter = int(match.group(1))
            if match.group(2):
                year = int(match.group(2))
    if quarter is None:
        match = _QUARTER_Q_ONLY_RE.search(text)
        if match:
            quarter = int(match.group(1))
    if quarter is None or not (1 <= quarter <= 4):
        return None
    if year is None:
        year = _infer_dataset_year(column, schema_profile) or datetime.now().year
    start_month, end_month = _QUARTER_MONTHS[quarter]
    return _month_range_iso(year, start_month)[0], _month_range_iso(year, end_month)[1]


def _parse_quincena_token(
    token: str,
    column: str,
    schema_profile: dict[str, Any] | None = None,
) -> tuple[str, str] | None:
    """Parsea 'primera quincena de julio 2021' / 'segunda quincena de 2021-07'."""
    text = _fold_accents(str(token or ""))
    match = _QUINCENA_SECOND_RE.search(text)
    is_second = bool(match)
    if not match:
        match = _QUINCENA_FIRST_RE.search(text)
    if not match:
        return None
    month_token = match.group(2) or None
    year_token = match.group(3) or None
    year = int(year_token) if year_token else None
    month: int | None = None
    if month_token:
        month = _parse_month_token(month_token)
        iso_month = re.match(r"^(\d{4})-(\d{1,2})$", month_token)
        if month is None and iso_month:
            year = year or int(iso_month.group(1))
            month = int(iso_month.group(2))
    if month is None:
        iso_month = re.search(r"\b(\d{4})-(\d{1,2})\b", text)
        if iso_month:
            year = year or int(iso_month.group(1))
            month = int(iso_month.group(2))
    if month is None or not (1 <= month <= 12):
        return None
    if year is None:
        year = _infer_dataset_year(column, schema_profile) or datetime.now().year
    first, last = _month_range_iso(year, month)
    if is_second:
        return f"{year:04d}-{month:02d}-16", last
    return first, f"{year:04d}-{month:02d}-15"


def _parse_year_token(
    token: str,
    column: str,
    schema_profile: dict[str, Any] | None = None,
) -> tuple[str, str] | None:
    """Parsea un año suelto '2021' → rango [2021-01-01, 2021-12-31]."""
    match = re.fullmatch(r"(\d{4})", _fold_accents(str(token or "")).strip())
    if not match:
        return None
    year = int(match.group(1))
    if not (1900 <= year <= 2100):
        return None
    return f"{year:04d}-01-01", f"{year:04d}-12-31"


# ═══════════════════════════════════════════════════════════════════════
# [P1 2026-09] Ventanas temporales relativas ("próximos/últimos N ...")
# ═══════════════════════════════════════════════════════════════════════
# El LLM suele emitir solo la cota inferior de una ventana relativa
# ("próximos 60 días" → fecaduc >= referencia) o ninguna. Estas funciones
# sintetizan la ventana completa de forma DETERMINISTA anclada al
# reference_date estructural del dataset. Domain-agnostic.

_RELATIVE_WINDOW_RE = re.compile(
    r"\b(proxim[oa]s?|siguientes?|next|upcoming|ultim[oa]s?|anteriores?|previous|last|pasados?)"
    r"\s+(\d{1,3})\s*(dias?|days?|semanas?|weeks?|meses|mes|months?)\b"
)
_WINDOW_FUTURE_MARKERS = ("proxim", "siguiente", "next", "upcoming")


def detect_relative_window(prompt: str) -> dict[str, Any] | None:
    """Detecta 'próximos/últimos N días|semanas|meses' (ES/EN, con acentos)."""
    if not prompt:
        return None
    text = _fold_accents(prompt)
    match = _RELATIVE_WINDOW_RE.search(text)
    if not match:
        return None
    marker, quantity_raw, unit = match.group(1), match.group(2), match.group(3)
    try:
        quantity = int(quantity_raw)
    except (TypeError, ValueError):
        return None
    if quantity <= 0:
        return None
    if unit.startswith(("dia", "day")):
        unit_key = "dia"
    elif unit.startswith(("semana", "week")):
        unit_key = "semana"
    else:
        unit_key = "mes"
    direction = "next" if any(m in marker for m in _WINDOW_FUTURE_MARKERS) else "last"
    return {"direction": direction, "quantity": quantity, "unit": unit_key}


def _window_bounds(
    window: dict[str, Any],
    reference_date: Any,
) -> tuple[str, str] | None:
    """Cotiza [lo, hi] ISO de la ventana anclada en reference_date."""
    if not reference_date:
        return None
    try:
        parsed = dateparser.parse(str(reference_date).strip())
    except (ValueError, TypeError, OverflowError):
        return None
    if parsed is None:
        return None
    anchor = parsed.date()
    quantity = int(window.get("quantity") or 0)
    unit = str(window.get("unit") or "")
    if quantity <= 0:
        return None
    if unit == "mes":
        from dateutil.relativedelta import relativedelta
        delta = relativedelta(months=quantity)
    elif unit == "semana":
        delta = timedelta(days=7 * quantity)
    else:
        delta = timedelta(days=quantity)
    if window.get("direction") == "next":
        low, high = anchor, anchor + delta
    else:
        low, high = anchor - delta, anchor
    return low.isoformat(), high.isoformat()


def _is_temporal_profile_col(
    schema_profile: dict[str, Any] | None,
    column: str,
) -> bool:
    if not schema_profile or column not in schema_profile:
        return False
    meta = schema_profile.get(column)
    if not isinstance(meta, dict):
        return False
    role = str(meta.get("role") or "").lower()
    meta_type = str(meta.get("type") or "").lower()
    dtype = str(meta.get("dtype") or "").lower()
    return (
        role in ("time", "date")
        or meta_type == "temporal"
        or "date" in dtype
        or "timestamp" in dtype
        or "datetime" in dtype
    )


def apply_relative_time_windows(
    plans: list[Any],
    prompt: str,
    reference_date: Any,
    schema_profile: dict[str, Any] | None = None,
    time_axis: str | None = None,
) -> list[Any]:
    """Fuerza la ventana completa para 'próximos/últimos N ...'.

    Reglas:
    - Solo reescribe columnas que el plan YA filtra temporalmente; nunca
      inventa columnas (evita adivinar la columna de vencimiento).
    - Si el plan filtra además el `time_axis` (corte snapshot), se preserva
      ese corte y la ventana se aplica a la columna temporal accesoria.
    - Degradación segura: ante cualquier duda, retorna los planes intactos.
    """
    window = detect_relative_window(prompt)
    if not window:
        return plans
    bounds = _window_bounds(window, reference_date)
    if not bounds:
        return plans
    low, high = bounds

    from app.core.semantic_grammar import DataFilter

    updated_plans: list[Any] = []
    for plan in list(plans or []):
        intent = getattr(plan, "main_intent", None)
        if intent is None:
            updated_plans.append(plan)
            continue
        current_filters = list(getattr(intent, "filters", []) or [])
        if not current_filters:
            updated_plans.append(plan)
            continue

        temporal_columns: list[str] = []
        for filt in current_filters:
            col = str(getattr(filt, "column", "") or "").strip()
            if col and _is_temporal_profile_col(schema_profile, col) and col not in temporal_columns:
                temporal_columns.append(col)
        if not temporal_columns:
            updated_plans.append(plan)
            continue

        # Preferir columnas accesorias (vencimiento) sobre el eje de corte.
        non_axis = [c for c in temporal_columns if c != (time_axis or "")]
        targets = non_axis or temporal_columns

        kept = [
            filt for filt in current_filters
            if str(getattr(filt, "column", "") or "").strip() not in targets
        ]
        new_filters = list(kept)
        for col in targets:
            try:
                new_filters.append(DataFilter.model_validate(
                    {"column": col, "operator": ">=", "value": low}
                ))
                new_filters.append(DataFilter.model_validate(
                    {"column": col, "operator": "<=", "value": high}
                ))
            except Exception:
                new_filters = None
                break
        if new_filters is None:
            updated_plans.append(plan)
            continue

        try:
            new_intent = intent.model_copy(update={"filters": new_filters})
            updated_plans.append(plan.model_copy(update={"main_intent": new_intent}))
        except AttributeError:
            updated_plans.append(plan)

    print(
        f"🕐 [TEMPORAL WINDOW] '{window['direction']} {window['quantity']} "
        f"{window['unit']}' → [{low}, {high}] (ref={reference_date})"
    )
    return updated_plans
