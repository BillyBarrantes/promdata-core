"""
evidence_bundle.py — Constructor de EvidenceBundleV1 para trazabilidad determinista de widgets y análisis.
══════════════════════════════════════════════════════════════════════════════════════════════════════════
Genera el paquete de evidencia auditable vinculado a cada cálculo de Ibis/DuckDB para consumo
del frontend (Evidence Drawer) y verificación de integridad matemática.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from typing import Any
import uuid

from app.core.analytical_contract import EvidenceBundleV1, FilterSpec


def build_evidence_bundle(
    plan: Any,
    ibis_output: dict[str, Any],
    dataset_version: str = "1.0",
    query_id: str | None = None,
) -> EvidenceBundleV1:
    """Construye un EvidenceBundleV1 con hash determinista reproducible y hechos auditables."""
    intent = getattr(plan, "main_intent", None)

    # 1. Plan hash determinista
    plan_dict = {
        "title": getattr(plan, "title", ""),
        "metric_polarity": getattr(plan, "metric_polarity", "neutral"),
        "intent_type": getattr(intent, "type", ""),
        "metrics": getattr(intent, "metrics", []) or ([getattr(intent, "metric")] if getattr(intent, "metric", None) else []),
        "dimension": getattr(intent, "dimension", None),
    }
    plan_serialized = json.dumps(plan_dict, sort_keys=True, default=str)
    plan_hash = hashlib.sha256(plan_serialized.encode("utf-8")).hexdigest()[:16]

    # 2. Hechos calculados (hard_facts)
    hard_facts = ibis_output.get("hard_facts") or {}
    if not isinstance(hard_facts, dict):
        hard_facts = {}

    # 3. Filtros aplicados
    filters_applied: list[dict[str, Any]] = []
    if hasattr(intent, "filters") and intent.filters:
        for f in intent.filters:
            col = str(getattr(f, "column", ""))
            op = getattr(f, "operator", "==")
            val = getattr(f, "value", None)
            filters_applied.append(
                FilterSpec(
                    column=col,
                    operator=getattr(op, "value", str(op)),
                    value=val,
                    origin="user" if not getattr(f, "is_system", False) else "system",
                ).model_dump(mode="json")
            )

    # 4. Datos del query canónico
    raw_sql = ibis_output.get("sql") or ibis_output.get("query")
    sql_canonical = str(raw_sql) if raw_sql else None

    # 5. Conteo de filas
    data_rows = ibis_output.get("data") or []
    row_count = len(data_rows) if isinstance(data_rows, list) else 0

    resolved_query_id = query_id or str(ibis_output.get("query_id") or f"qry_{uuid.uuid4().hex[:8]}")

    return EvidenceBundleV1(
        evidence_id=f"evi_{uuid.uuid4().hex[:12]}",
        query_id=resolved_query_id,
        plan_hash=plan_hash,
        dataset_version=dataset_version,
        computed_facts=hard_facts,
        row_count=row_count,
        execution_timestamp=datetime.utcnow().isoformat(),
        sql_canonical_query=sql_canonical,
        metadata={"filters_applied": filters_applied},
    )
