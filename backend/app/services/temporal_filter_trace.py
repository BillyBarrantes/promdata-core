"""F1 — Linaje verificable de filtros temporales.

Versión: f1-v1.

Expone, de forma estrictamente aditiva, la causa, autoridad, predicado y
efecto de cada filtro temporal aplicado por el motor Ibis. No modifica la
semántica de ejecución ni infiere dominios ni nombres de columnas: la
temporalidad se decide por pertenencia a un conjunto de columnas ya
autorizado por el contrato semántico.

El builder es una función pura. Solo recibe filtros ya resueltos, el flag
del snapshot guard, el conjunto de columnas temporales y dos conteos
(``rows_before`` / ``rows_after``) calculados por Ibis. No escanea datos.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


TRACE_VERSION = "f1-v1"

_MAX_STR_LEN = 120
_MAX_LIST_ITEMS = 20


class FilterOrigin(str, Enum):
    """De dónde proviene un filtro temporal."""

    USER_REQUESTED = "user_requested"
    LEGACY_INFERENCE = "legacy_inference"
    UNKNOWN = "unknown"


class FilterAuthority(str, Enum):
    """Qué grado de autoridad respalda el filtro temporal."""

    EXPLICIT = "explicit"
    INFERRED = "inferred"
    UNKNOWN = "unknown"


class FilterEffect(str, Enum):
    """Efecto observable del filtro sobre el conteo de filas del plan."""

    ROWS_REMOVED = "rows_removed"
    NO_ROWS_REMOVED = "no_rows_removed"
    UNKNOWN = "unknown"


def _coerce_enum(enum_cls: type[Enum], value: Any, default: Enum) -> Enum:
    """Coerciona un valor a un enum; si es inválido retorna el default.

    Evita que una cadena desconocida (p. ej. de un productor futuro) rompa la
    construcción de la traza. La ausencia de autoridad se representa como
    ``unknown``, nunca como un valor afirmativo.
    """

    if isinstance(value, enum_cls):
        return value
    try:
        return enum_cls(str(value).strip().lower())
    except (ValueError, TypeError):
        return default


def _sanitize_value(value: Any) -> Any:
    """Sanea el valor de un predicado para la traza.

    No contiene valores crudos de filas; solo el predicado del plan ya
    autorizado en el resultado. Se truncan cadenas y listas para acotar el
    payload.
    """

    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, str):
        return value if len(value) <= _MAX_STR_LEN else value[: _MAX_STR_LEN - 3] + "..."
    if isinstance(value, (list, tuple, set)):
        raw = list(value)
        sanitized = [_sanitize_value(v) for v in raw[:_MAX_LIST_ITEMS]]
        if len(raw) > _MAX_LIST_ITEMS:
            sanitized.append(f"... (+{len(raw) - _MAX_LIST_ITEMS})")
        return sanitized
    text = str(value)
    return text if len(text) <= _MAX_STR_LEN else text[: _MAX_STR_LEN - 3] + "..."


def _extract_filter_fields(filter_obj: Any) -> tuple[str, str, Any]:
    """Extrae (column, operator, value) de un DataFilter o de un dict."""

    if isinstance(filter_obj, dict):
        column = filter_obj.get("column")
        operator = filter_obj.get("operator")
        value = filter_obj.get("value")
    else:
        column = getattr(filter_obj, "column", None)
        operator = getattr(filter_obj, "operator", None)
        value = getattr(filter_obj, "value", None)
    operator_str = str(getattr(operator, "value", operator) or "==").strip()
    return str(column or "").strip(), operator_str, value


class TemporalFilterTraceItem(BaseModel):
    """Un filtro temporal individual dentro de la traza de un plan."""

    column: str = ""
    operator: str = ""
    value: Any = None
    origin: FilterOrigin = FilterOrigin.UNKNOWN
    authority: FilterAuthority = FilterAuthority.UNKNOWN
    is_temporal: bool = True
    rows_before: int | None = None
    rows_after: int | None = None
    effect: FilterEffect = FilterEffect.UNKNOWN

    @field_validator("origin", mode="before")
    @classmethod
    def _validate_origin(cls, value: Any) -> FilterOrigin:
        return _coerce_enum(FilterOrigin, value, FilterOrigin.UNKNOWN)

    @field_validator("authority", mode="before")
    @classmethod
    def _validate_authority(cls, value: Any) -> FilterAuthority:
        return _coerce_enum(FilterAuthority, value, FilterAuthority.UNKNOWN)

    @field_validator("effect", mode="before")
    @classmethod
    def _validate_effect(cls, value: Any) -> FilterEffect:
        return _coerce_enum(FilterEffect, value, FilterEffect.UNKNOWN)

    @field_validator("value", mode="before")
    @classmethod
    def _validate_value(cls, value: Any) -> Any:
        return _sanitize_value(value)


class TemporalFilterTrace(BaseModel):
    """Traza de linaje temporal de un plan (aditiva, sin efecto en ejecución)."""

    version: str = TRACE_VERSION
    rows_before: int | None = None
    rows_after: int | None = None
    cut_applied: bool = False
    authority: FilterAuthority = FilterAuthority.UNKNOWN
    contract_state: str | None = None
    items: list[TemporalFilterTraceItem] = Field(default_factory=list)

    @field_validator("authority", mode="before")
    @classmethod
    def _validate_authority(cls, value: Any) -> FilterAuthority:
        return _coerce_enum(FilterAuthority, value, FilterAuthority.UNKNOWN)


def build_temporal_filter_trace(
    *,
    filter_lists: dict[str, list[Any]] | None = None,
    snapshot_guard_applied: bool = False,
    temporal_columns: set[str] | None = None,
    rows_before: int | None = None,
    rows_after: int | None = None,
    contract_state: str | None = None,
) -> TemporalFilterTrace:
    """Construye la traza de linaje temporal de un plan.

    Parámetros
    ----------
    filter_lists:
        Diccionario con las listas ya resueltas del intent
        (``filters`` / ``positive_filters`` / ``negative_filters``).
    snapshot_guard_applied:
        ``True`` si el snapshot guard legacy (F0) aplicó el corte implícito
        ``is_latest_snapshot == True``.
    temporal_columns:
        Conjunto de columnas temporales autorizadas por el contrato semántico.
        Solo los filtros sobre estas columnas se incluyen en la traza.
    rows_before / rows_after:
        Conteos del plan antes y después de aplicar los predicados temporales.
    contract_state:
        Estado del contrato semántico (informativo). La ausencia de autoridad
        se representa como ``unknown``.

    Nota: ``rows_before`` / ``rows_after`` son conteos a nivel de plan (un
    único conteo base + el conteo final ya calculado por la ejecución); no se
    escanea el dataset una vez por filtro.
    """

    temporal_cols = {
        str(c).strip().lower() for c in (temporal_columns or set()) if str(c).strip()
    }
    lists = filter_lists or {}

    effect = FilterEffect.UNKNOWN
    if isinstance(rows_before, int) and isinstance(rows_after, int):
        effect = (
            FilterEffect.ROWS_REMOVED
            if rows_after < rows_before
            else FilterEffect.NO_ROWS_REMOVED
        )

    items: list[TemporalFilterTraceItem] = []

    if snapshot_guard_applied:
        items.append(
            TemporalFilterTraceItem(
                column="is_latest_snapshot",
                operator="==",
                value=True,
                origin=FilterOrigin.LEGACY_INFERENCE,
                authority=FilterAuthority.INFERRED,
                is_temporal=True,
                rows_before=rows_before,
                rows_after=rows_after,
                effect=effect,
            )
        )

    for list_name in ("filters", "positive_filters", "negative_filters"):
        for raw_filter in lists.get(list_name) or []:
            column, operator, value = _extract_filter_fields(raw_filter)
            if not column:
                continue
            # Membresía estricta: solo se trazan filtros sobre columnas temporales
            # ya autorizadas por el contrato. Si el contrato no declara ninguna
            # columna temporal, no hay linaje temporal que reportar.
            if column.strip().lower() not in temporal_cols:
                continue
            items.append(
                TemporalFilterTraceItem(
                    column=column,
                    operator=operator,
                    value=value,
                    origin=FilterOrigin.USER_REQUESTED,
                    authority=FilterAuthority.EXPLICIT,
                    is_temporal=True,
                    rows_before=rows_before,
                    rows_after=rows_after,
                    effect=effect,
                )
            )

    cut_applied = bool(snapshot_guard_applied)
    if cut_applied:
        authority = FilterAuthority.INFERRED
    elif any(item.authority == FilterAuthority.EXPLICIT for item in items):
        authority = FilterAuthority.EXPLICIT
    else:
        authority = FilterAuthority.UNKNOWN

    return TemporalFilterTrace(
        version=TRACE_VERSION,
        rows_before=rows_before if isinstance(rows_before, int) else None,
        rows_after=rows_after if isinstance(rows_after, int) else None,
        cut_applied=cut_applied,
        authority=authority,
        contract_state=contract_state,
        items=items,
    )
