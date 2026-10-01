"""
interaction_engine.py — Motor de Interacción Dinámica y Filtros Cruzados.
══════════════════════════════════════════════════════════════════════════
Gestiona la evaluación de políticas de recálculo ('local' vs 'backend_required'),
la reconciliación consistente de filtros interactivos y la detección de invalidaciones
de snapshot temporal.
"""
from __future__ import annotations

from typing import Any, Literal
import pandas as pd

from app.core.analytical_contract import InteractionContractV1
from app.core.structured_logging import emit_structured_log


def evaluate_interaction_policy(
    *,
    base_filters: dict[str, Any] | None = None,
    active_filters: dict[str, Any] | None = None,
    schema_profile: dict[str, Any] | None = None,
    dataset_contract: dict[str, Any] | None = None,
    requested_changes: dict[str, Any] | None = None,
    evidence_id: str | None = None,
) -> InteractionContractV1:
    """
    Evalúa si una interacción del usuario (click en gráfico, cambio de selector)
    puede resolverse localmente en el frontend/WASM o requiere viaje al backend.
    """
    base_filters = base_filters or {}
    active_filters = active_filters or {}
    schema_profile = schema_profile or {}
    dataset_contract = dataset_contract or {}
    requested_changes = requested_changes or {}

    time_axis = dataset_contract.get("time_axis")
    is_snapshot = (
        dataset_contract.get("dataset_mode") == "snapshot"
        or dataset_contract.get("snapshot_guard_allowed") is True
    )

    recomputation_policy: Literal["local", "backend_required"] = "local"
    snapshot_complete: bool = True

    # 1. Si hay cambios de métrica o agregación solicitados -> requiere backend
    if "metric" in requested_changes or "aggregation" in requested_changes:
        recomputation_policy = "backend_required"

    # 2. Si se solicita pronóstico, anomalías o modelo predictivo -> requiere backend
    if requested_changes.get("predictive") or requested_changes.get("forecast"):
        recomputation_policy = "backend_required"

    # 3. Si se altera el eje temporal en un dataset de tipo snapshot
    # Cambiar la fecha de corte invalida el Snapshot Guard actual y requiere recálculo
    if is_snapshot and time_axis:
        if (
            time_axis in active_filters
            or "fecha_corte" in active_filters
            or "date_cutoff" in requested_changes
        ):
            snapshot_complete = False
            recomputation_policy = "backend_required"

    # 4. Si se solicitan filtros sobre columnas inexistentes o no perfiladas
    for col in active_filters:
        if schema_profile and col not in schema_profile:
            recomputation_policy = "backend_required"

    return InteractionContractV1(
        dataset_version="1.0",
        base_filters=base_filters,
        active_filters=active_filters,
        snapshot_complete=snapshot_complete,
        recomputation_policy=recomputation_policy,
        evidence_id=evidence_id,
    )


def reconcile_interaction_filters(
    base_filters: dict[str, Any] | None,
    active_filters: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Reconcilia los filtros base (provenientes del query original) con los
    filtros interactivos (clicks del usuario en la UI), resolviendo conflictos.
    """
    merged: dict[str, Any] = {}
    base = base_filters or {}
    active = active_filters or {}

    # Agregar filtros base
    for k, v in base.items():
        if v is not None and v != "":
            merged[k] = v

    # Los filtros interactivos tienen prioridad sobre la misma columna
    for k, v in active.items():
        if v is None or v == "" or (isinstance(v, list) and len(v) == 0):
            # Si el usuario desmarcó el filtro interactivo, se remueve
            merged.pop(k, None)
        else:
            merged[k] = v

    return merged


def apply_interaction_filters_to_dataframe(
    df: pd.DataFrame,
    filters: dict[str, Any],
) -> pd.DataFrame:
    """
    Aplica deterministamente los filtros reconciliados a un DataFrame en memoria.
    """
    if df.empty or not filters:
        return df

    filtered_df = df.copy()
    for col, val in filters.items():
        if col not in filtered_df.columns:
            continue

        if isinstance(val, (list, tuple, set)):
            # Filtro IN / multiselección
            filtered_df = filtered_df[filtered_df[col].isin(val)]
        elif isinstance(val, dict):
            # Operadores explícitos: {'>=': 100, '<=': 500}
            for op, op_val in val.items():
                if op in ("==", "="):
                    filtered_df = filtered_df[filtered_df[col] == op_val]
                elif op == "!=":
                    filtered_df = filtered_df[filtered_df[col] != op_val]
                elif op == ">":
                    filtered_df = filtered_df[filtered_df[col] > op_val]
                elif op == ">=":
                    filtered_df = filtered_df[filtered_df[col] >= op_val]
                elif op == "<":
                    filtered_df = filtered_df[filtered_df[col] < op_val]
                elif op == "<=":
                    filtered_df = filtered_df[filtered_df[col] <= op_val]
        else:
            # Igualdad escalar directa
            filtered_df = filtered_df[filtered_df[col] == val]

    return filtered_df
