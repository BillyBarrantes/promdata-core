from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from app.core.canonical_artifacts import (
    CanonicalAnalyticalCandidate,
    CanonicalAnalyticalContractBundle,
    CanonicalMaterializationStatus,
)
from app.core.config import settings
from app.services.canonical_ibis_preview_runtime import CanonicalIbisPreviewRuntime
from app.services.canonical_header_normalizer import compact_header_semantic_text
from app.services.canonical_schema_profiler import build_canonical_schema_profile
from app.services.canonical_shadow_metric_validity_gate import apply_canonical_shadow_metric_validity_gate
from app.services.data_engine import DataEngine


_SUSPICIOUS_METRIC_FRAGMENTS = {
    "key",
    "id",
    "code",
    "label",
    "phone",
    "fax",
    "zip",
    "zipcode",
    "email",
    "url",
    "geometry",
    "geolocation",
    "address",
    "manager",
}
_TRUSTED_METRIC_FRAGMENTS = {
    "amount",
    "total",
    "price",
    "cost",
    "revenue",
    "sale",
    "sales",
    "stock",
    "qty",
    "quantity",
    "count",
    "hours",
    "duration",
    "calories",
    "burn",
    "margin",
    "discount",
    "income",
    "weight",
    "size",
    "score",
    "rate",
    "percent",
    "pct",
}


def is_canonical_analytical_contract_adapter_enabled() -> bool:
    return settings.CANONICAL_ANALYTICAL_CONTRACT_ADAPTER_ENABLED


@dataclass
class CanonicalAnalyticalAdapterRuntime:
    analytical_bundle: CanonicalAnalyticalContractBundle
    candidate_dataframes: dict[str, pd.DataFrame]
    metadata: dict[str, Any]


def _table_kind_priority(table_name: str) -> int:
    if table_name.startswith("derived__"):
        return 3
    if table_name.startswith("primary__"):
        return 2
    if table_name.startswith("related__"):
        return 1
    return 0


def _normalized_semantic_text(value: str) -> str:
    return compact_header_semantic_text(value)


def _metric_signal_summary(candidate: CanonicalAnalyticalCandidate) -> dict[str, Any]:
    contract = candidate.dataset_contract if isinstance(candidate.dataset_contract, dict) else {}
    schema_profile = candidate.schema_profile if isinstance(candidate.schema_profile, dict) else {}
    metric_columns = [str(value) for value in list(contract.get("metric_columns") or []) if str(value or "").strip()]
    trusted_metric_columns: list[str] = []
    suspicious_metric_columns: list[str] = []

    for column_name in metric_columns:
        name_text = _normalized_semantic_text(column_name)
        info = schema_profile.get(column_name, {}) if isinstance(schema_profile.get(column_name), dict) else {}
        cardinality_ratio = float(info.get("cardinality_ratio") or 0.0)
        suspicious = any(fragment in name_text for fragment in _SUSPICIOUS_METRIC_FRAGMENTS)
        trusted = any(fragment in name_text for fragment in _TRUSTED_METRIC_FRAGMENTS)

        if not trusted and cardinality_ratio >= 0.95:
            suspicious = True
        if suspicious:
            suspicious_metric_columns.append(column_name)
            continue
        if trusted or cardinality_ratio < 0.85:
            trusted_metric_columns.append(column_name)
            continue
        suspicious_metric_columns.append(column_name)

    return {
        "metric_columns": metric_columns,
        "trusted_metric_columns": trusted_metric_columns,
        "suspicious_metric_columns": suspicious_metric_columns,
        "trusted_metric_count": len(trusted_metric_columns),
        "suspicious_metric_count": len(suspicious_metric_columns),
    }


def _candidate_score(candidate: CanonicalAnalyticalCandidate) -> tuple[float, dict[str, Any]]:
    contract = candidate.dataset_contract if isinstance(candidate.dataset_contract, dict) else {}
    schema_profile = candidate.schema_profile if isinstance(candidate.schema_profile, dict) else {}
    metric_summary = _metric_signal_summary(candidate)
    trusted_metric_count = int(metric_summary["trusted_metric_count"])
    suspicious_metric_count = int(metric_summary["suspicious_metric_count"])
    metric_count = len(metric_summary["metric_columns"])

    has_metric = 1.0 if metric_count else 0.0
    has_date = 1.0 if list(contract.get("date_columns") or []) else 0.0
    has_dimension = 1.0 if list(contract.get("dimension_columns") or []) else 0.0
    status_score = {
        CanonicalMaterializationStatus.READY: 1.0,
        CanonicalMaterializationStatus.PREVIEW_ONLY: 0.8,
        CanonicalMaterializationStatus.DEFERRED: 0.2,
        CanonicalMaterializationStatus.EMPTY: 0.0,
    }.get(candidate.status, 0.0)
    table_kind_score = _table_kind_priority(candidate.table_name) / 3.0
    row_score = min(int(candidate.row_count or 0) / 50.0, 1.0)
    column_score = min(int(candidate.column_count or 0) / 12.0, 1.0)
    schema_score = min(len(schema_profile) / 12.0, 1.0)
    trusted_metric_score = min(trusted_metric_count / 3.0, 1.0)
    suspicious_metric_ratio = suspicious_metric_count / max(metric_count, 1)
    dimension_count = len(list(contract.get("dimension_columns") or []))
    derived_penalty = 0.0
    if candidate.table_name.startswith("derived__") and trusted_metric_count == 0:
        derived_penalty += 0.25
    if candidate.table_name.startswith("derived__") and trusted_metric_count <= 1 and dimension_count >= max((trusted_metric_count + 1) * 6, 12):
        derived_penalty += 0.1
    date_without_trusted_metric_penalty = 0.15 if has_date and trusted_metric_count == 0 else 0.0
    suspicious_metric_penalty = min(suspicious_metric_ratio * 0.2, 0.2)
    standalone_trusted_bonus = 0.05 if not candidate.table_name.startswith("derived__") and trusted_metric_count > 0 else 0.0

    score = (
        status_score * 0.25
        + has_metric * 0.12
        + has_date * 0.15
        + has_dimension * 0.1
        + table_kind_score * 0.15
        + row_score * 0.1
        + column_score * 0.03
        + schema_score * 0.02
        + trusted_metric_score * 0.13
        + standalone_trusted_bonus
        - suspicious_metric_penalty
        - date_without_trusted_metric_penalty
        - derived_penalty
    )
    diagnostics = {
        "status_score": round(status_score, 4),
        "has_metric": bool(has_metric),
        "has_date": bool(has_date),
        "has_dimension": bool(has_dimension),
        "table_kind_score": round(table_kind_score, 4),
        "row_score": round(row_score, 4),
        "column_score": round(column_score, 4),
        "schema_score": round(schema_score, 4),
        "trusted_metric_count": trusted_metric_count,
        "suspicious_metric_count": suspicious_metric_count,
        "trusted_metric_columns": metric_summary["trusted_metric_columns"],
        "suspicious_metric_columns": metric_summary["suspicious_metric_columns"],
        "trusted_metric_score": round(trusted_metric_score, 4),
        "suspicious_metric_penalty": round(suspicious_metric_penalty, 4),
        "date_without_trusted_metric_penalty": round(date_without_trusted_metric_penalty, 4),
        "derived_penalty": round(derived_penalty, 4),
        "standalone_trusted_bonus": round(standalone_trusted_bonus, 4),
        "score": round(score, 4),
    }
    return score, diagnostics


def _attach_dataframe_contract_attrs(
    dataframe: pd.DataFrame,
    *,
    schema_profile: dict[str, Any],
    topology_rules: dict[str, Any],
    dataset_contract: dict[str, Any],
    currency_meta: dict[str, Any],
    literal_filter_catalog: dict[str, Any],
    translator_context_summary: str,
    reference_date: str | None,
    temporal_report: dict[str, Any],
    shadow_metric_gate: dict[str, Any],
) -> pd.DataFrame:
    dataframe.attrs["schema_profile"] = schema_profile
    dataframe.attrs["topology_rules"] = topology_rules
    dataframe.attrs["semantic_contract"] = dataset_contract
    dataframe.attrs["currency_meta"] = currency_meta
    dataframe.attrs["literal_filter_catalog"] = literal_filter_catalog
    dataframe.attrs["translator_context_summary"] = translator_context_summary
    dataframe.attrs["reference_date"] = reference_date
    dataframe.attrs["canonical_temporal_profile"] = temporal_report
    dataframe.attrs["shadow_metric_gate"] = shadow_metric_gate
    return dataframe


def _build_candidate_for_table(
    *,
    table_name: str,
    dataframe: pd.DataFrame,
    source_ids: list[str],
    status: CanonicalMaterializationStatus,
) -> tuple[CanonicalAnalyticalCandidate, pd.DataFrame]:
    working_df = dataframe.copy()
    source_ids = [str(value) for value in list(working_df.attrs.get("canonical_source_ids") or source_ids) if str(value or "").strip()]
    table_kind = str(working_df.attrs.get("canonical_table_kind") or "")

    if working_df.empty and len(working_df.columns) == 0:
        candidate = CanonicalAnalyticalCandidate(
            candidate_id=table_name,
            table_name=table_name,
            source_ids=source_ids,
            status=CanonicalMaterializationStatus.EMPTY,
            row_count=0,
            column_count=0,
            metadata={"reason": "empty_dataframe"},
        )
        return candidate, working_df

    working_df, schema_profile, temporal_report = build_canonical_schema_profile(working_df)
    working_df, schema_profile, shadow_metric_gate = apply_canonical_shadow_metric_validity_gate(
        working_df,
        schema_profile,
    )
    currency_meta = DataEngine._detect_currency(working_df)
    topology_rules = DataEngine._detect_topology(working_df, schema_profile)
    dataset_contract = DataEngine._infer_dataset_semantic_contract(working_df, schema_profile, topology_rules)
    literal_filter_catalog = DataEngine._build_literal_filter_catalog(working_df, schema_profile)
    translator_context_summary = DataEngine._build_translator_context_summary(schema_profile, topology_rules)
    reference_date = DataEngine._detect_reference_date(working_df, schema_profile, dataset_contract)

    if reference_date:
        schema_profile["_dataset_year"] = int(reference_date[:4])

    working_df = _attach_dataframe_contract_attrs(
        working_df,
        schema_profile=schema_profile,
        topology_rules=topology_rules,
        dataset_contract=dataset_contract,
        currency_meta=currency_meta,
        literal_filter_catalog=literal_filter_catalog,
        translator_context_summary=translator_context_summary,
        reference_date=reference_date,
        temporal_report=temporal_report,
        shadow_metric_gate=shadow_metric_gate,
    )

    candidate = CanonicalAnalyticalCandidate(
        candidate_id=table_name,
        table_name=table_name,
        source_ids=source_ids,
        status=status,
        row_count=int(len(working_df.index)),
        column_count=int(len(working_df.columns)),
        schema_profile=schema_profile,
        topology_rules=topology_rules,
        dataset_contract=dataset_contract,
        currency_meta=currency_meta,
        literal_filter_catalog=literal_filter_catalog,
        translator_context_summary=translator_context_summary,
        reference_date=reference_date,
        metadata={
            "table_kind_priority": _table_kind_priority(table_name),
            "canonical_table_kind": table_kind,
            "shadow_metric_gate": shadow_metric_gate,
        },
    )
    candidate.metadata["temporal_profile"] = temporal_report
    return candidate, working_df


def build_canonical_analytical_adapter_runtime(
    preview_runtime: CanonicalIbisPreviewRuntime,
) -> CanonicalAnalyticalAdapterRuntime:
    candidates: list[CanonicalAnalyticalCandidate] = []
    candidate_dataframes: dict[str, pd.DataFrame] = {}
    materialization_status = str(preview_runtime.metadata.get("bundle_status") or "").strip().lower()
    default_status = (
        CanonicalMaterializationStatus.READY
        if materialization_status == CanonicalMaterializationStatus.READY.value
        else CanonicalMaterializationStatus.PREVIEW_ONLY
        if materialization_status == CanonicalMaterializationStatus.PREVIEW_ONLY.value
        else CanonicalMaterializationStatus.DEFERRED
        if materialization_status == CanonicalMaterializationStatus.DEFERRED.value
        else CanonicalMaterializationStatus.EMPTY
    )

    for table_name, dataframe in preview_runtime.dataframes.items():
        source_id = table_name.split("__", 1)[1] if "__" in table_name else table_name
        candidate, working_df = _build_candidate_for_table(
            table_name=table_name,
            dataframe=dataframe,
            source_ids=[source_id],
            status=default_status,
        )
        score, diagnostics = _candidate_score(candidate)
        candidate.metadata["selection_score"] = round(score, 4)
        candidate.metadata["selection_diagnostics"] = diagnostics
        candidates.append(candidate)
        candidate_dataframes[candidate.candidate_id] = working_df

    candidates.sort(
        key=lambda candidate: (
            -float(candidate.metadata.get("selection_score") or 0.0),
            candidate.table_name,
        )
    )
    selected_candidate_id = candidates[0].candidate_id if candidates else None

    analytical_bundle = CanonicalAnalyticalContractBundle(
        selected_candidate_id=selected_candidate_id,
        candidates=candidates,
        metadata={
            "candidate_count": len(candidates),
            "preview_backend": preview_runtime.metadata.get("preview_backend"),
        },
    )
    return CanonicalAnalyticalAdapterRuntime(
        analytical_bundle=analytical_bundle,
        candidate_dataframes=candidate_dataframes,
        metadata={
            "selected_candidate_id": selected_candidate_id,
            "candidate_count": len(candidates),
        },
    )


def summarize_canonical_analytical_adapter_runtime(
    adapter_runtime: CanonicalAnalyticalAdapterRuntime,
) -> dict[str, Any]:
    bundle = adapter_runtime.analytical_bundle
    candidates = []
    for candidate in bundle.candidates:
        candidates.append(
            {
                "candidate_id": candidate.candidate_id,
                "table_name": candidate.table_name,
                "status": candidate.status.value,
                "row_count": candidate.row_count,
                "column_count": candidate.column_count,
                "dataset_mode": candidate.dataset_contract.get("dataset_mode"),
                "time_axis": candidate.dataset_contract.get("time_axis"),
                "metric_count": len(candidate.dataset_contract.get("metric_columns") or []),
                "dimension_count": len(candidate.dataset_contract.get("dimension_columns") or []),
                "selection_score": candidate.metadata.get("selection_score"),
            }
        )
    return {
        "selected_candidate_id": bundle.selected_candidate_id,
        "candidate_count": len(bundle.candidates),
        "preview_backend": bundle.metadata.get("preview_backend"),
        "candidates": candidates,
    }


def get_selected_candidate_dataframe(
    adapter_runtime: CanonicalAnalyticalAdapterRuntime,
) -> pd.DataFrame | None:
    selected_candidate_id = adapter_runtime.analytical_bundle.selected_candidate_id
    if not selected_candidate_id:
        return None
    return adapter_runtime.candidate_dataframes.get(selected_candidate_id)
