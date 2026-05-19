from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.config import settings


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _coerce_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _coerce_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _candidate_report_paths() -> list[Path]:
    candidates: list[Path] = []
    for raw_path in (
        settings.CANONICAL_TABULAR_CANARY_SHADOW_REPORT_PATH,
        settings.CANONICAL_TABULAR_CANARY_FALLBACK_REPORT_PATH,
    ):
        normalized = _normalize_text(raw_path)
        if not normalized:
            continue
        path = Path(normalized)
        if path not in candidates:
            candidates.append(path)
    return candidates


def _load_shadow_report() -> tuple[dict[str, Any], Path | None]:
    for report_path in _candidate_report_paths():
        if not report_path.exists() or not report_path.is_file():
            continue
        try:
            payload = json.loads(report_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(payload, dict):
            return payload, report_path
    return {}, None


def _extract_shadow_summary(payload: dict[str, Any]) -> dict[str, Any]:
    for key in ("telemetry_shadow_runtime", "shadow_runtime", "summary"):
        candidate = _as_dict(payload.get(key))
        if candidate.get("alignment_counts") or candidate.get("divergence_by_prompt_type"):
            return candidate
    if payload.get("alignment_counts") or payload.get("divergence_by_prompt_type"):
        return payload
    return {}


def _max_divergence(summary: dict[str, Any]) -> float | None:
    scores: list[float] = []
    for bucket_key in (
        "divergence_by_prompt_type",
        "divergence_by_visual_family",
        "divergence_by_file_name",
    ):
        for row in _as_list(summary.get(bucket_key)):
            score = _coerce_float(_as_dict(row).get("divergence_score"))
            if score is not None:
                scores.append(score)
    if not scores:
        return None
    return max(scores)


def _alignment_rate(summary: dict[str, Any]) -> float | None:
    observed_count = _coerce_int(summary.get("observed_count"))
    if observed_count <= 0:
        return None
    alignment_counts = _as_dict(summary.get("alignment_counts"))
    high_alignment = _coerce_int(alignment_counts.get("high_alignment"))
    return high_alignment / observed_count


def _component_flag_checks() -> dict[str, bool]:
    return {
        "canonical_native_tabular_extraction": bool(settings.CANONICAL_NATIVE_TABULAR_EXTRACTION_ENABLED),
        "canonical_ibis_preview_runtime": bool(settings.CANONICAL_IBIS_PREVIEW_RUNTIME_ENABLED),
        "canonical_analytical_contract_adapter": bool(settings.CANONICAL_ANALYTICAL_CONTRACT_ADAPTER_ENABLED),
        "canonical_shadow_metric_validity_gate": bool(settings.CANONICAL_SHADOW_METRIC_VALIDITY_GATE_ENABLED),
        "canonical_shadow_query_runtime": bool(settings.CANONICAL_SHADOW_QUERY_RUNTIME_ENABLED),
    }


def build_canonical_tabular_canary_health() -> dict[str, Any]:
    report_payload, report_path = _load_shadow_report()
    shadow_summary = _extract_shadow_summary(report_payload)
    component_checks = _component_flag_checks()
    prerequisites_ready = all(component_checks.values())

    observed_count = _coerce_int(shadow_summary.get("observed_count"))
    alignment_rate = _alignment_rate(shadow_summary)
    max_divergence_score = _max_divergence(shadow_summary)
    require_shadow_evidence = bool(settings.CANONICAL_TABULAR_CANARY_REQUIRE_SHADOW_EVIDENCE)

    shadow_evidence_ready = True
    shadow_evidence_reason = "shadow_evidence_not_required"
    if require_shadow_evidence:
        shadow_evidence_ready = (
            observed_count >= max(int(settings.CANONICAL_TABULAR_CANARY_MIN_OBSERVED_TASKS), 1)
            and alignment_rate is not None
            and alignment_rate >= float(settings.CANONICAL_TABULAR_CANARY_MIN_ALIGNMENT_RATE)
            and max_divergence_score is not None
            and max_divergence_score <= float(settings.CANONICAL_TABULAR_CANARY_MAX_DIVERGENCE_SCORE)
        )
        if observed_count < max(int(settings.CANONICAL_TABULAR_CANARY_MIN_OBSERVED_TASKS), 1):
            shadow_evidence_reason = "insufficient_observed_tasks"
        elif alignment_rate is None:
            shadow_evidence_reason = "alignment_rate_missing"
        elif alignment_rate < float(settings.CANONICAL_TABULAR_CANARY_MIN_ALIGNMENT_RATE):
            shadow_evidence_reason = "alignment_rate_below_threshold"
        elif max_divergence_score is None:
            shadow_evidence_reason = "divergence_score_missing"
        elif max_divergence_score > float(settings.CANONICAL_TABULAR_CANARY_MAX_DIVERGENCE_SCORE):
            shadow_evidence_reason = "divergence_score_above_threshold"
        else:
            shadow_evidence_reason = "shadow_evidence_healthy"

    router_enabled = bool(settings.CANONICAL_TABULAR_CANARY_ROUTER_ENABLED)
    functional_switch_enabled = bool(settings.CANONICAL_TABULAR_CANARY_FUNCTIONAL_SWITCH_ENABLED)
    fail_open_enabled = bool(settings.CANONICAL_TABULAR_CANARY_FAIL_OPEN_ENABLED)
    ready_for_functional_canary = (
        router_enabled
        and functional_switch_enabled
        and fail_open_enabled
        and prerequisites_ready
        and shadow_evidence_ready
    )

    if not router_enabled:
        status = "disabled"
        summary = "Canary router deshabilitado."
    elif router_enabled and not functional_switch_enabled:
        status = "dry_run"
        summary = "Canary router listo para decidir, pero el switch funcional sigue apagado."
    elif ready_for_functional_canary:
        status = "ready"
        summary = "Canary tabular listo para activación funcional controlada."
    else:
        status = "blocked"
        summary = "Canary tabular bloqueado por health gate; fallback a legacy requerido."

    return {
        "status": status,
        "summary": summary,
        "router_enabled": router_enabled,
        "functional_switch_enabled": functional_switch_enabled,
        "fail_open_enabled": fail_open_enabled,
        "prerequisites_ready": prerequisites_ready,
        "shadow_evidence_required": require_shadow_evidence,
        "shadow_evidence_ready": shadow_evidence_ready,
        "shadow_evidence_reason": shadow_evidence_reason,
        "ready_for_functional_canary": ready_for_functional_canary,
        "component_checks": component_checks,
        "shadow_evidence": {
            "report_path": str(report_path) if report_path else None,
            "observed_count": observed_count,
            "alignment_counts": _as_dict(shadow_summary.get("alignment_counts")),
            "alignment_rate": alignment_rate,
            "max_divergence_score": max_divergence_score,
            "latest_observed_at": shadow_summary.get("latest_observed_at"),
            "stability_by_window": _as_list(shadow_summary.get("stability_by_window")),
        },
        "thresholds": {
            "min_observed_tasks": int(settings.CANONICAL_TABULAR_CANARY_MIN_OBSERVED_TASKS),
            "min_alignment_rate": float(settings.CANONICAL_TABULAR_CANARY_MIN_ALIGNMENT_RATE),
            "max_divergence_score": float(settings.CANONICAL_TABULAR_CANARY_MAX_DIVERGENCE_SCORE),
        },
    }
