"""Resolución adaptativa de granularidad temporal — pura y domain-agnostic.

Problema que resuelve (clase, no instancia): un plan puede pedir grain=MONTH
sobre un dataset cuyo rango temporal real es de días/semanas. El resultado es
una serie con < 2 puntos (o un único punto), que no es una serie y produce
conclusiones falsas ("tendencia decreciente" sobre 1 punto).

Regla universal: la granularidad se decide por la **estructura de los datos**
(rango temporal + periodos distintos), nunca por el nombre de la columna ni por
el dominio del cliente. Si el usuario pidió explícitamente una granularidad
("evolución mensual"), se respeta.

Módulo sin dependencias de servicios (solo el enum TimeGrain) para poder usarse
tanto en el planificador como en el motor de ejecución.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

from app.core.semantic_grammar import TimeGrain

# Un gráfico de línea necesita al menos 2 puntos para ser una serie válida.
MIN_SERIES_POINTS = 2

# Orden de fina → gruesa.
_GRAIN_ORDER: tuple[TimeGrain, ...] = (
    TimeGrain.DAY,
    TimeGrain.WEEK,
    TimeGrain.MONTH,
    TimeGrain.QUARTER,
    TimeGrain.YEAR,
)

_DAYS_PER_GRAIN: dict[TimeGrain, float] = {
    TimeGrain.DAY: 1.0,
    TimeGrain.WEEK: 7.0,
    TimeGrain.MONTH: 30.4375,
    TimeGrain.QUARTER: 91.3125,
    TimeGrain.YEAR: 365.25,
}

# Palabras de granularidad explícita (ES/EN/PT). Si el prompt la fija, no se adapta.
_EXPLICIT_GRAIN_TOKENS: tuple[tuple[TimeGrain, tuple[str, ...]], ...] = (
    (TimeGrain.DAY, ("diario", "diaria", "diarios", "diarias", "por dia", "cada dia", "daily")),
    (TimeGrain.WEEK, ("semanal", "semanales", "por semana", "cada semana", "weekly", "semanalmente")),
    (TimeGrain.MONTH, ("mensual", "mensuales", "por mes", "cada mes", "monthly", "mensal", "mensais")),
    (TimeGrain.QUARTER, ("trimestral", "trimestrales", "por trimestre", "quarterly")),
    (TimeGrain.YEAR, ("anual", "anuales", "por anio", "por ano", "cada anio", "yearly", "annual")),
)


def _fold_grain_text(text: str) -> str:
    """Minúsculas + sin acentos (cubre ES/EN/PT: diário→diario, mês→mes)."""
    lowered = unicodedata.normalize("NFKD", str(text or "").lower())
    return "".join(ch for ch in lowered if not unicodedata.combining(ch))


def detect_explicit_grain(surface_prompt: str | None) -> Optional[TimeGrain]:
    """Detecta si el usuario fijó una granularidad concreta en el prompt."""
    text = _fold_grain_text(surface_prompt).strip()
    if not text:
        return None
    normalized = re.sub(r"\s+", " ", text)
    for grain, tokens in _EXPLICIT_GRAIN_TOKENS:
        if any(token in normalized for token in tokens):
            return grain
    return None


def estimate_periods(
    grain: TimeGrain,
    span_days: Optional[float],
    distinct_dates: Optional[int],
) -> Optional[float]:
    """Estima cuántos periodos produciría `grain` sobre el rango observado.

    Para DAY se prefiere el conteo real de fechas distintas (más fiel que el
    rango, que sobreestima con datos dispersos).
    """
    if grain == TimeGrain.DAY:
        if distinct_dates is not None and distinct_dates > 0:
            return float(distinct_dates)
        return float(span_days) if span_days and span_days > 0 else None
    if span_days is None or span_days <= 0:
        return None
    return float(span_days) / _DAYS_PER_GRAIN.get(grain, 1.0)


def is_grain_viable(
    grain: TimeGrain,
    span_days: Optional[float],
    distinct_dates: Optional[int],
    *,
    min_points: int = MIN_SERIES_POINTS,
) -> bool:
    periods = estimate_periods(grain, span_days, distinct_dates)
    return periods is not None and periods >= min_points


def resolve_time_grain(
    requested: TimeGrain,
    span_days: Optional[float],
    distinct_dates: Optional[int],
    *,
    prompt_explicit: Optional[TimeGrain] = None,
) -> TimeGrain:
    """Devuelve la granularidad más gruesa que aún produzca una serie válida.

    - Si el usuario pidió una granularidad explícita, se respeta tal cual.
    - Si la granularidad pedida ya produce >= `MIN_SERIES_POINTS`, no se toca.
    - Si colapsa a < 2 puntos, se refina (WEEK/DAY) hasta encontrar la más
      gruesa viable; si ninguna alcanza, se devuelve DAY (el llamador decide si
      la serie es viable o debe degradarse a KPI).
    """
    if prompt_explicit is not None:
        return prompt_explicit
    if requested not in _GRAIN_ORDER:
        return requested
    # [P3 2026-09] Span desconocido != colapso. Antes, un eje sin rango conocido
    # (p. ej. un event_axis que no es el time_axis del contrato) declaraba
    # MONTH/WEEK "no viables" y refinaba a DAY, generando cientos de puntos que
    # degradaban el combo a Smart Table. Sin evidencia de colapso se respeta el
    # grano pedido; el doble cerrojo de ejecución (`_resolve_runtime_time_grain`,
    # con el span REAL de DuckDB) decide con datos reales.
    if span_days is None:
        return requested
    if is_grain_viable(requested, span_days, distinct_dates):
        return requested

    requested_index = _GRAIN_ORDER.index(requested)
    finer = tuple(reversed(_GRAIN_ORDER[:requested_index]))
    for candidate in finer:
        if is_grain_viable(candidate, span_days, distinct_dates):
            return candidate
    return TimeGrain.DAY
