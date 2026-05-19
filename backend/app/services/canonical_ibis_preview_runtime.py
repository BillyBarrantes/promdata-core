from __future__ import annotations

from dataclasses import dataclass
import importlib
from typing import Any

import pandas as pd

from app.core.canonical_artifacts import (
    CanonicalMaterializationStatus,
    CanonicalMaterializedBundle,
    CanonicalMaterializedFrame,
    CanonicalMaterializedView,
)
from app.core.config import settings


def is_canonical_ibis_preview_runtime_enabled() -> bool:
    return settings.CANONICAL_IBIS_PREVIEW_RUNTIME_ENABLED


@dataclass
class CanonicalIbisPreviewRuntime:
    """
    Runtime preview desacoplado para materializaciones canónicas.
    No participa del pipeline activo; sirve para validar datasets canónicos
    con pandas + DuckDB/Ibis antes de una integración controlada.
    """

    connection: Any
    tables: dict[str, Any]
    dataframes: dict[str, pd.DataFrame]
    metadata: dict[str, Any]


def _load_ibis_module() -> Any | None:
    try:
        return importlib.import_module("ibis")
    except Exception:
        return None


def _frame_to_dataframe(frame: CanonicalMaterializedFrame) -> pd.DataFrame:
    dataframe = pd.DataFrame(frame.records, columns=list(frame.column_names)) if frame.records else pd.DataFrame(columns=list(frame.column_names))
    dataframe.attrs["canonical_source_ids"] = [frame.frame_id]
    dataframe.attrs["canonical_table_kind"] = "frame"
    dataframe.attrs["canonical_relation_type"] = frame.relation_type
    return dataframe


def _view_to_dataframe(view: CanonicalMaterializedView) -> pd.DataFrame:
    dataframe = pd.DataFrame(view.records, columns=list(view.column_names)) if view.records else pd.DataFrame(columns=list(view.column_names))
    dataframe.attrs["canonical_source_ids"] = list(view.source_frame_ids)
    dataframe.attrs["canonical_table_kind"] = str(view.view_type or "derived")
    return dataframe


def _register_dataframe(
    *,
    connection: Any,
    registry: dict[str, Any],
    frames: dict[str, pd.DataFrame],
    table_name: str,
    dataframe: pd.DataFrame,
) -> None:
    working_df = dataframe.copy()
    working_df.attrs = dict(getattr(dataframe, "attrs", {}) or {})
    for column_name in list(working_df.columns):
        series = working_df[column_name]
        if series.empty:
            continue
        try:
            if bool(series.isna().all()):
                working_df[column_name] = series.astype("string")
        except Exception:
            continue
    frames[table_name] = working_df
    if connection is None:
        registry[table_name] = working_df
        return
    if len(list(working_df.columns)) == 0:
        # DuckDB/Ibis no acepta tablas sin columnas; preservamos la entrada en
        # modo pandas para que el adapter runtime pueda clasificarla como vacía.
        registry[table_name] = working_df
        return
    registry[table_name] = connection.create_table(table_name, working_df, overwrite=True)


def build_canonical_ibis_preview_runtime(
    materialized_bundle: CanonicalMaterializedBundle,
) -> CanonicalIbisPreviewRuntime:
    ibis_module = _load_ibis_module()
    connection = ibis_module.duckdb.connect() if ibis_module is not None else None
    tables: dict[str, Any] = {}
    dataframes: dict[str, pd.DataFrame] = {}

    if materialized_bundle.primary_frame is not None:
        table_name = f"primary__{materialized_bundle.primary_frame.frame_id}"
        _register_dataframe(
            connection=connection,
            registry=tables,
            frames=dataframes,
            table_name=table_name,
            dataframe=_frame_to_dataframe(materialized_bundle.primary_frame),
        )

    for frame in materialized_bundle.related_frames:
        table_name = f"related__{frame.frame_id}"
        _register_dataframe(
            connection=connection,
            registry=tables,
            frames=dataframes,
            table_name=table_name,
            dataframe=_frame_to_dataframe(frame),
        )

    for view in materialized_bundle.derived_views:
        table_name = f"derived__{view.view_id}"
        _register_dataframe(
            connection=connection,
            registry=tables,
            frames=dataframes,
            table_name=table_name,
            dataframe=_view_to_dataframe(view),
        )

    return CanonicalIbisPreviewRuntime(
        connection=connection,
        tables=tables,
        dataframes=dataframes,
        metadata={
            "primary_frame_id": materialized_bundle.primary_frame_id,
            "bundle_status": materialized_bundle.status.value,
            "table_count": len(tables),
            "dataframe_count": len(dataframes),
            "preview_backend": "ibis_duckdb" if connection is not None else "pandas_fallback",
        },
    )


def describe_canonical_ibis_preview_runtime(runtime: CanonicalIbisPreviewRuntime) -> dict[str, Any]:
    table_summaries: list[dict[str, Any]] = []
    for table_name, dataframe in runtime.dataframes.items():
        table_summaries.append(
            {
                "table_name": table_name,
                "row_count": int(len(dataframe.index)),
                "column_count": int(len(dataframe.columns)),
                "columns": list(dataframe.columns),
            }
        )
    table_summaries.sort(key=lambda item: item["table_name"])
    return {
        **dict(runtime.metadata),
        "tables": table_summaries,
    }


def execute_canonical_preview_table(
    runtime: CanonicalIbisPreviewRuntime,
    *,
    table_name: str,
    limit: int = 20,
) -> dict[str, Any]:
    if table_name not in runtime.tables:
        raise ValueError(f"La tabla preview solicitada no existe: {table_name}")

    table_expr = runtime.tables[table_name]
    safe_limit = max(int(limit or 0), 0) or 20
    ibis_module = _load_ibis_module()
    if hasattr(table_expr, "limit") and hasattr(table_expr, "execute"):
        preview_df = table_expr.limit(safe_limit).execute()
    else:
        preview_df = table_expr.head(safe_limit).copy()
    sql = ""
    if ibis_module is not None and hasattr(table_expr, "limit"):
        try:
            sql = str(ibis_module.to_sql(table_expr.limit(safe_limit), dialect="duckdb"))
        except Exception:
            sql = ""

    return {
        "table_name": table_name,
        "limit": safe_limit,
        "row_count": int(len(preview_df.index)),
        "column_count": int(len(preview_df.columns)),
        "columns": list(preview_df.columns),
        "rows": preview_df.to_dict(orient="records"),
        "sql": sql,
    }


def select_default_preview_table_name(runtime: CanonicalIbisPreviewRuntime) -> str | None:
    if not runtime.tables:
        return None
    preferred_prefixes = ("derived__", "primary__", "related__")
    for prefix in preferred_prefixes:
        for table_name in runtime.tables:
            if table_name.startswith(prefix):
                return table_name
    return next(iter(runtime.tables.keys()))


def summarize_materialized_bundle_status(
    materialized_bundle: CanonicalMaterializedBundle,
) -> dict[str, Any]:
    preview_ready_tables = 0
    deferred_tables = 0

    all_statuses = []
    if materialized_bundle.primary_frame is not None:
        all_statuses.append(materialized_bundle.primary_frame.status)
    all_statuses.extend(frame.status for frame in materialized_bundle.related_frames)
    all_statuses.extend(view.status for view in materialized_bundle.derived_views)

    for status in all_statuses:
        if status in {CanonicalMaterializationStatus.READY, CanonicalMaterializationStatus.PREVIEW_ONLY}:
            preview_ready_tables += 1
        elif status == CanonicalMaterializationStatus.DEFERRED:
            deferred_tables += 1

    return {
        "bundle_status": materialized_bundle.status.value,
        "primary_frame_id": materialized_bundle.primary_frame_id,
        "preview_ready_tables": preview_ready_tables,
        "deferred_tables": deferred_tables,
        "derived_view_count": len(materialized_bundle.derived_views),
    }
