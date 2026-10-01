"""Eje temporal ORDINAL — detección y orden cronológico (L1, contract-first).

[Semantic Layer Contract-First · Fase 1.1 2026-09]

Muchos archivos reales expresan el período como TEXTO (``"Enero"``, ``"ene-2021"``,
``"2021-07"``, ``"2021-W30"``, ``"Q3 2021"``). El classifier de esquema los ve
como ``role="dimension"`` (no son ``datetime``), así que el contrato no emitía
eje temporal y toda la maquinaria temporal (guard de acumulados, grano, orden)
quedaba ciega.

Este módulo detecta, con evidencia de VALORES (schema-agnostic, sin nombres de
columna ni vocabulario de dominio), qué columna es un eje temporal ordinal y
cuál es el orden cronológico de sus valores. El contrato (L1) lo persiste como
``ordinal_axis`` / ``ordinal_axis_order`` / ``time_axis_kind`` y los consumidores
lo heredan.

Nada aquí conoce tenants, dominios ni nombres de columna: solo se inspeccionan
los strings.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Optional

import numpy as np
import pandas as pd

# Mapa canónico de meses ES + EN + PT (claves sin acento: el texto se pliega
# antes de buscar). Fuente única de vocabulario temporal ordinal.
MONTH_NAMES: dict[str, int] = {
    # Español
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
    "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
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
    # Portugués
    "janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4,
    "maio": 5, "junho": 6, "julho": 7, "agosto": 8,
    "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
    # Abreviaciones PT
    "fev": 2, "mai": 5, "out": 10, "dez": 12,
}

_ISO_PERIOD_RE = re.compile(r"^(\d{4})[-/.](\d{1,2})$")
_ISO_WEEK_RE = re.compile(r"^(\d{4})[-/]?w(\d{1,2})$")
_QUARTER_RE_A = re.compile(r"^(\d{4})[-/ ]?q([1-4])$")
_QUARTER_RE_B = re.compile(r"^q([1-4])[-/ ]?(\d{4})$")
_STANDALONE_QUARTER_RE = re.compile(
    r"^([1-4])(?:er|to|do|ro|ra|ta|º|°)?\s*trimestre(?:\s*(?:de|del)?\s*(\d{4}))?$"
)
_YEAR_RE = re.compile(r"^(\d{4})$")

# Fuerza de la granularidad: un mes con nombre o un período ISO son señal
# fuerte; un año suelto es señal débil (puede confundirse con un código).
_KIND_STRENGTH: dict[str, float] = {
    "month": 1.0,
    "iso_period": 1.0,
    "quarter": 0.95,
    "week": 0.9,
    "year": 0.5,
}

_MIN_POINTS = 3
_MONOTONIC_RATIO_THRESHOLD = 0.98


def fold_text(text: Any) -> str:
    """Minúsculas + sin acentos (NFKD) para comparación robusta ES/EN/PT."""
    normalized = unicodedata.normalize("NFKD", str(text or "").lower())
    return "".join(ch for ch in normalized if not unicodedata.combining(ch)).strip()


def _normalize_year(token: str) -> Optional[int]:
    if not token or not token.isdigit():
        return None
    if len(token) == 4:
        return int(token)
    if len(token) == 2:
        value = int(token)
        return 2000 + value if value <= 50 else 1900 + value
    return None


def _parse(value: Any) -> Optional[tuple[tuple[int, int, int], str]]:
    """Devuelve ``((year, month, sub), kind)`` o ``None`` si no es un período.

    ``year`` puede ser 0 cuando el valor no trae año (p. ej. ``"Enero"``): se
    ordena por posición dentro del año, que es lo relevante para una serie.
    """
    text = fold_text(value)
    if not text:
        return None

    match = _ISO_PERIOD_RE.match(text)
    if match:
        year, month = int(match.group(1)), int(match.group(2))
        if 1 <= month <= 12:
            return (year, month, 0), "iso_period"

    match = _ISO_WEEK_RE.match(text)
    if match:
        return (int(match.group(1)), 0, int(match.group(2))), "week"

    match = _QUARTER_RE_A.match(text) or _QUARTER_RE_B.match(text)
    if match:
        groups = match.groups()
        year = int(groups[0]) if groups[0] and len(groups[0]) == 4 else int(groups[1])
        quarter = int(groups[1]) if groups[0] and len(groups[0]) == 4 else int(groups[0])
        return (year, (quarter - 1) * 3 + 1, 0), "quarter"

    match = _STANDALONE_QUARTER_RE.match(text)
    if match:
        quarter = int(match.group(1))
        year = _normalize_year(match.group(2) or "") or 0
        return (year, (quarter - 1) * 3 + 1, 0), "quarter"

    match = _YEAR_RE.match(text)
    if match:
        return (int(match.group(1)), 0, 0), "year"

    # Mes con/sin año: "Enero", "ene-2021", "2021/Ene", "Enero 2021".
    tokens = [tok for tok in re.split(r"[-/\s.]+", text) if tok]
    if not tokens:
        return None
    month: Optional[int] = None
    year = 0
    for tok in tokens:
        if tok in MONTH_NAMES:
            month = MONTH_NAMES[tok]
        else:
            candidate_year = _normalize_year(tok)
            if candidate_year is not None:
                year = candidate_year
    if month is None:
        return None
    return (year, month, 0), "month"


def parse_period_key(value: Any) -> Optional[tuple[int, int, int]]:
    """Clave cronológica comparable de un valor de período, o ``None``."""
    parsed = _parse(value)
    return parsed[0] if parsed else None


def period_kind(value: Any) -> Optional[str]:
    """Tipo de período ('month'|'iso_period'|'quarter'|'week'|'year') o ``None``."""
    parsed = _parse(value)
    return parsed[1] if parsed else None


def order_by_period(values: Any) -> list[str]:
    """Ordena valores únicos cronológicamente (los no parseables al final)."""
    unique: list[str] = []
    seen: set[str] = set()
    for raw in values:
        text = str(raw).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        unique.append(text)
    parsed = [(parse_period_key(v), v) for v in unique]
    parseable = sorted([p for p in parsed if p[0] is not None], key=lambda item: item[0])
    unparseable = [v for key, v in parsed if key is None]
    return [v for _, v in parseable] + unparseable


def series_monotonic_direction(ordered_values: pd.Series) -> Optional[str]:
    """Dirección monótona ('inc'/'dec') de una serie YA ordenada, o ``None``.

    Mismo umbral que ``metric_archetype.monotonic_direction`` (fail-closed ante
    series cortas, constantes o no monótonas).
    """
    if ordered_values is None:
        return None
    numeric = pd.to_numeric(ordered_values, errors="coerce").dropna()
    if not numeric.empty:
        numeric = numeric[np.isfinite(numeric.to_numpy(dtype=np.float64))]
    if len(numeric) < _MIN_POINTS:
        return None
    diffs = numeric.diff().dropna()
    if diffs.empty:
        return None
    scale = max(float(numeric.abs().max()), 1.0)
    eps = 1e-9 * scale
    up = int((diffs > eps).sum())
    down = int((diffs < -eps).sum())
    total = up + down
    if total == 0:
        return None
    if up / total >= _MONOTONIC_RATIO_THRESHOLD:
        return "inc"
    if down / total >= _MONOTONIC_RATIO_THRESHOLD:
        return "dec"
    return None


def detect_ordinal_axis(
    df: pd.DataFrame,
    schema_profile: dict[str, Any] | None = None,
    exclude: Any = (),
    *,
    max_cardinality: int = 80,
    min_coverage: float = 0.9,
) -> tuple[Optional[str], list[str]]:
    """Detecta la columna-eje temporal ordinal y el orden cronológico de sus valores.

    Domain-agnostic: no usa nombres de columna, solo parseabilidad de los
    valores. La columna debe ser de texto, con ≥2 y ≤``max_cardinality`` valores
    distintos, ≥``min_coverage`` parseables como período, y al menos 2 claves
    cronológicas distintas.
    """
    exclude_set = {str(value) for value in (exclude or [])}
    profile = schema_profile or {}
    best_col: Optional[str] = None
    best_order: list[str] = []
    best_score = 0.0

    if df is None or df.empty:
        return None, []

    for column in df.columns:
        name = str(column)
        if name.startswith("_") or name == "is_latest_snapshot" or name in exclude_set:
            continue
        info = profile.get(column, {}) if isinstance(profile, dict) else {}
        if str(info.get("role") or "").lower() == "date":
            continue
        try:
            if pd.api.types.is_numeric_dtype(df[column]):
                continue
        except Exception:
            continue

        series = df[column].dropna().astype(str).str.strip()
        series = series[series != ""]
        if series.empty:
            continue
        unique = list(dict.fromkeys(series.tolist()))
        if not (2 <= len(unique) <= max_cardinality):
            continue

        parsed: dict[str, tuple[int, int, int]] = {}
        kinds: dict[str, int] = {}
        for value in unique:
            result = _parse(value)
            if result is None:
                continue
            parsed[value] = result[0]
            kinds[result[1]] = kinds.get(result[1], 0) + 1
        coverage = len(parsed) / max(len(unique), 1)
        if coverage < min_coverage:
            continue
        distinct_keys = {key for key in parsed.values()}
        if len(distinct_keys) < 2:
            continue

        dominant_kind = max(kinds.items(), key=lambda item: item[1])[0] if kinds else "year"
        strength = _KIND_STRENGTH.get(dominant_kind, 0.5)
        score = coverage * strength * min(len(distinct_keys) / 3.0, 1.0)
        if score > best_score:
            ordered = [value for value, _ in sorted(parsed.items(), key=lambda item: item[1])]
            unparseable = [value for value in unique if value not in parsed]
            best_col = name
            best_order = ordered + unparseable
            best_score = score

    return best_col, best_order
