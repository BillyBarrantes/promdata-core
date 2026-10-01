from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from typing import Any, Final, Literal

from pydantic import BaseModel, Field

from app.core.semantic_grammar import FilterOperator

ANALYTICAL_CONTRACT_VERSION: Final[str] = "1.0"
DatasetContractState = Literal[
    "confirmed_file_contract",
    "trusted_tenant_template",
    "bootstrap_verified",
    "legacy_inferred",
    "unknown",
]

# F3: TTL por defecto del bootstrap verificado (7 días) si no se configura otro.
DEFAULT_BOOTSTRAP_TTL_HOURS: Final[int] = 168


def _as_aware_utc(value: datetime | None) -> datetime:
    base = value or datetime.now(timezone.utc)
    if base.tzinfo is None:
        return base.replace(tzinfo=timezone.utc)
    return base.astimezone(timezone.utc)


def compute_bootstrap_expiry(
    now: datetime | None = None,
    ttl_hours: int = DEFAULT_BOOTSTRAP_TTL_HOURS,
) -> str:
    """Fecha ISO de expiración del bootstrap (UTC). TTL siempre ≥ 1h."""
    try:
        hours = max(1, int(ttl_hours))
    except (TypeError, ValueError):
        hours = DEFAULT_BOOTSTRAP_TTL_HOURS
    return (_as_aware_utc(now) + timedelta(hours=hours)).isoformat()


def is_bootstrap_expired(expires_at: str | None, now: datetime | None = None) -> bool:
    """`True` si el bootstrap expiró.

    Un `expires_at` ausente significa "no hay bootstrap" → no expirado.
    Un `expires_at` ilegible se trata como expirado (fail-closed): fuerza
    reinferencia en vez de conceder autoridad con una fecha corrupta.
    """
    if not expires_at:
        return False
    try:
        expiry = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return True
    return _as_aware_utc(now) >= _as_aware_utc(expiry)


class FilterSpec(BaseModel):
    column: str
    operator: FilterOperator
    value: Any
    origin: Literal["user", "llm", "system"]


class DatasetContractV1(BaseModel):
    dataset_mode: str = "undetermined"
    snapshot_guard_allowed: bool = False
    contract_state: DatasetContractState = "unknown"
    # F0 reserva el modelo para F3, pero no entrega autoridad a bootstrap alguno.
    bootstrap_expires_at: str | None = None
    bootstrap_scope: dict[str, Any] = Field(default_factory=dict)
    time_axis: str | None = None
    # [MEJORA 6 2026-09] Vocabulario temporal explícito (aditivo, retro-compatible):
    # snapshot_axis = eje de corte (alias semántico de time_axis);
    # event_axes = fechas accesorias/evento (distintas del eje de corte).
    snapshot_axis: str | None = None
    event_axes: list[str] = Field(default_factory=list)
    reference_date: str | None = None
    # [Fase 1.1 2026-09] Eje temporal ORDINAL (mes-texto, "2021-07", "2021-W30").
    # Aditivo: `time_axis`/`date_columns` no cambian. `time_axis_kind` ∈
    # {"date", "ordinal", None}; `ordinal_axis_order` es el orden cronológico.
    ordinal_axis: str | None = None
    ordinal_axis_order: list[str] = Field(default_factory=list)
    time_axis_kind: str | None = None
    # [Fase 1.2 2026-09] Señales estructurales por métrica (data-driven),
    # p. ej. {"ventas": {"cumulative_monotonic_direction": "inc"}}.
    metric_semantics: dict[str, Any] = Field(default_factory=dict)
    date_columns: list[str] = Field(default_factory=list)
    metric_columns: list[str] = Field(default_factory=list)
    dimension_columns: list[str] = Field(default_factory=list)
    identifier_columns: list[str] = Field(default_factory=list)
    entity_key: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)


class TemporalContractV1(BaseModel):
    temporal_resolution: str | None = None
    date_range: tuple[str | None, str | None] | None = None
    dataset_year: int | None = None
    min_date: str | None = None
    max_date: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AnalyticalContract(BaseModel):
    contract_version: str = ANALYTICAL_CONTRACT_VERSION
    dataset_contract: DatasetContractV1 = Field(default_factory=DatasetContractV1)
    filters: list[FilterSpec] = Field(default_factory=list)
    temporal_contract: TemporalContractV1 | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_legacy_dict(
        cls,
        dataset_contract: dict[str, Any] | None = None,
        filters: list[dict[str, Any]] | None = None,
        temporal_contract: dict[str, Any] | None = None,
    ) -> AnalyticalContract:
        dc = dataset_contract or {}
        raw_contract_state = str(dc.get("contract_state", "unknown"))
        contract_state: DatasetContractState = (
            raw_contract_state
            if raw_contract_state in {
                "confirmed_file_contract",
                "trusted_tenant_template",
                "bootstrap_verified",
                "legacy_inferred",
                "unknown",
            }
            else "unknown"
        )
        obj = cls(
            dataset_contract=DatasetContractV1(
                dataset_mode=str(dc.get("dataset_mode", "undetermined")),
                snapshot_guard_allowed=bool(dc.get("snapshot_guard_allowed", False)),
                contract_state=contract_state,
                bootstrap_expires_at=dc.get("bootstrap_expires_at"),
                bootstrap_scope=dict(dc.get("bootstrap_scope", {}) or {}),
                time_axis=dc.get("time_axis"),
                snapshot_axis=dc.get("snapshot_axis") or dc.get("time_axis"),
                event_axes=list(dc.get("event_axes", [])),
                reference_date=dc.get("reference_date"),
                ordinal_axis=dc.get("ordinal_axis"),
                ordinal_axis_order=list(dc.get("ordinal_axis_order", []) or []),
                time_axis_kind=dc.get("time_axis_kind"),
                metric_semantics=dict(dc.get("metric_semantics", {}) or {}),
                date_columns=list(dc.get("date_columns", [])),
                metric_columns=list(dc.get("metric_columns", [])),
                dimension_columns=list(dc.get("dimension_columns", [])),
                identifier_columns=list(dc.get("identifier_columns", [])),
                entity_key=dc.get("entity_key"),
                evidence=dict(dc.get("evidence", {})),
            ),
            filters=[
                FilterSpec(
                    column=f.get("column", ""),
                    operator=f.get("operator", "=="),
                    value=f.get("value"),
                    origin=f.get("origin", "system"),
                )
                for f in (filters or [])
            ],
            temporal_contract=TemporalContractV1(
                **(temporal_contract or {})
            ) if temporal_contract else None,
        )
        if dc.get("version"):
            obj.metadata["legacy_version"] = dc["version"]

        if "canonical_dimensions" in dc:
            obj.metadata["canonical_dimensions"] = dc["canonical_dimensions"]

        return obj

    def to_legacy_dataset_contract(self) -> dict[str, Any]:
        dc = self.dataset_contract
        legacy = {
            "version": self.metadata.get("legacy_version", "phase_1_foundation"),
            "dataset_mode": dc.dataset_mode,
            "snapshot_guard_allowed": dc.snapshot_guard_allowed,
            "contract_state": dc.contract_state,
            "bootstrap_expires_at": dc.bootstrap_expires_at,
            "bootstrap_scope": dict(dc.bootstrap_scope),
            "time_axis": dc.time_axis,
            "snapshot_axis": dc.snapshot_axis or dc.time_axis,
            "event_axes": list(dc.event_axes),
            "reference_date": dc.reference_date,
            "ordinal_axis": dc.ordinal_axis,
            "ordinal_axis_order": list(dc.ordinal_axis_order),
            "time_axis_kind": dc.time_axis_kind,
            "metric_semantics": dict(dc.metric_semantics),
            "date_columns": list(dc.date_columns),
            "metric_columns": list(dc.metric_columns),
            "dimension_columns": list(dc.dimension_columns),
            "identifier_columns": list(dc.identifier_columns),
            "entity_key": dc.entity_key,
            "evidence": dict(dc.evidence),
        }
        if "canonical_dimensions" in self.metadata:
            legacy["canonical_dimensions"] = self.metadata["canonical_dimensions"]
        return legacy

    def to_legacy_filters(self) -> list[dict[str, Any]]:
        return [
            {
                "column": f.column,
                "operator": f.operator,
                "value": copy.deepcopy(f.value),
                "origin": f.origin,
            }
            for f in self.filters
        ]


# ═══════════════════════════════════════════════════════════════════════════════
# FASE 0 — CONTRATOS TIPADOS ADITIVOS V1 (Fortress Standard)
# ═══════════════════════════════════════════════════════════════════════════════

QueryAnalyticalState = Literal["valid", "clarification_required", "blocked"]
ColumnResolutionStatus = Literal["resolved", "ambiguous", "missing", "incompatible"]


class ColumnResolution(BaseModel):
    column_name: str | None = None
    status: ColumnResolutionStatus = "resolved"
    candidates: list[str] = Field(default_factory=list)
    reason: str | None = None


class QueryAnalyticalContractV1(BaseModel):
    contract_version: str = "1.0"
    query_id: str
    file_id: str
    intent_type: str
    metrics: list[ColumnResolution] = Field(default_factory=list)
    dimensions: list[ColumnResolution] = Field(default_factory=list)
    filters: list[FilterSpec] = Field(default_factory=list)
    temporal_spec: TemporalContractV1 | None = None
    state: QueryAnalyticalState = "valid"
    clarification_prompt: str | None = None
    block_reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_executable(self) -> bool:
        return self.state == "valid"



class EvidenceBundleV1(BaseModel):
    evidence_id: str
    query_id: str
    plan_hash: str
    dataset_version: str
    computed_facts: dict[str, Any] = Field(default_factory=dict)
    row_count: int = 0
    execution_timestamp: str = ""
    sql_canonical_query: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class VisualContractV1(BaseModel):
    chart_type: str
    x_axis: str | None = None
    y_axis: str | None = None
    series: list[dict[str, Any]] = Field(default_factory=list)
    format_currency: str | None = None
    format_percentage: bool = False
    evidence_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class InteractionContractV1(BaseModel):
    dataset_version: str = "1.0"
    base_filters: dict[str, Any] = Field(default_factory=dict)
    active_filters: dict[str, Any] = Field(default_factory=dict)
    snapshot_complete: bool = True
    recomputation_policy: Literal["local", "backend_required"] = "local"
    evidence_id: str | None = None


class UploadFlowContractV1(BaseModel):
    flow_type: Literal["direct_storage_rls"] = "direct_storage_rls"
    bucket_name: str = "dash-uploads"
    auth_rule: str = "auth.uid() = user_id"
    storage_path_pattern: str = "{user_id}/{timestamp}_{file_name}"
    validation_stage: str = "celery_pre_ingest"
    max_size_bytes: int = 52428800  # 50 MB
