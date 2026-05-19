import json

from app.core.config import settings
from app.services.canonical_canary_health import build_canonical_tabular_canary_health


def _healthy_shadow_report() -> dict:
    return {
        "telemetry_shadow_runtime": {
            "observed_count": 18,
            "alignment_counts": {"high_alignment": 18},
            "divergence_by_prompt_type": [
                {"prompt_type": "complete_analysis", "divergence_score": 0.0}
            ],
            "divergence_by_visual_family": [
                {"requested_visual_family": "bar_chart", "divergence_score": 0.0}
            ],
            "latest_observed_at": "2026-05-09T00:00:00+00:00",
            "stability_by_window": [{"window_days": 7, "observed_count": 18}],
        }
    }


def test_canary_health_is_disabled_by_default(monkeypatch, tmp_path):
    empty_report_path = tmp_path / "missing.json"
    monkeypatch.setattr(settings, "CANONICAL_TABULAR_CANARY_ROUTER_ENABLED", False)
    monkeypatch.setattr(settings, "CANONICAL_TABULAR_CANARY_FUNCTIONAL_SWITCH_ENABLED", False)
    monkeypatch.setattr(settings, "CANONICAL_TABULAR_CANARY_SHADOW_REPORT_PATH", str(empty_report_path))
    monkeypatch.setattr(settings, "CANONICAL_TABULAR_CANARY_FALLBACK_REPORT_PATH", str(empty_report_path))

    summary = build_canonical_tabular_canary_health()

    assert summary["status"] == "disabled"
    assert summary["ready_for_functional_canary"] is False


def test_canary_health_becomes_ready_with_healthy_shadow_evidence(monkeypatch, tmp_path):
    report_path = tmp_path / "shadow-report.json"
    report_path.write_text(json.dumps(_healthy_shadow_report()), encoding="utf-8")

    monkeypatch.setattr(settings, "CANONICAL_TABULAR_CANARY_ROUTER_ENABLED", True)
    monkeypatch.setattr(settings, "CANONICAL_TABULAR_CANARY_FUNCTIONAL_SWITCH_ENABLED", True)
    monkeypatch.setattr(settings, "CANONICAL_TABULAR_CANARY_REQUIRE_SHADOW_EVIDENCE", True)
    monkeypatch.setattr(settings, "CANONICAL_TABULAR_CANARY_SHADOW_REPORT_PATH", str(report_path))
    monkeypatch.setattr(settings, "CANONICAL_TABULAR_CANARY_FALLBACK_REPORT_PATH", str(report_path))
    monkeypatch.setattr(settings, "CANONICAL_NATIVE_TABULAR_EXTRACTION_ENABLED", True)
    monkeypatch.setattr(settings, "CANONICAL_IBIS_PREVIEW_RUNTIME_ENABLED", True)
    monkeypatch.setattr(settings, "CANONICAL_ANALYTICAL_CONTRACT_ADAPTER_ENABLED", True)
    monkeypatch.setattr(settings, "CANONICAL_SHADOW_METRIC_VALIDITY_GATE_ENABLED", True)
    monkeypatch.setattr(settings, "CANONICAL_SHADOW_QUERY_RUNTIME_ENABLED", True)

    summary = build_canonical_tabular_canary_health()

    assert summary["status"] == "ready"
    assert summary["shadow_evidence_ready"] is True
    assert summary["ready_for_functional_canary"] is True


def test_canary_health_blocks_when_divergence_is_above_threshold(monkeypatch, tmp_path):
    report_path = tmp_path / "shadow-report-divergent.json"
    payload = _healthy_shadow_report()
    payload["telemetry_shadow_runtime"]["divergence_by_prompt_type"] = [
        {"prompt_type": "generic_analysis", "divergence_score": 0.25}
    ]
    report_path.write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setattr(settings, "CANONICAL_TABULAR_CANARY_ROUTER_ENABLED", True)
    monkeypatch.setattr(settings, "CANONICAL_TABULAR_CANARY_FUNCTIONAL_SWITCH_ENABLED", True)
    monkeypatch.setattr(settings, "CANONICAL_TABULAR_CANARY_REQUIRE_SHADOW_EVIDENCE", True)
    monkeypatch.setattr(settings, "CANONICAL_TABULAR_CANARY_SHADOW_REPORT_PATH", str(report_path))
    monkeypatch.setattr(settings, "CANONICAL_TABULAR_CANARY_FALLBACK_REPORT_PATH", str(report_path))
    monkeypatch.setattr(settings, "CANONICAL_NATIVE_TABULAR_EXTRACTION_ENABLED", True)
    monkeypatch.setattr(settings, "CANONICAL_IBIS_PREVIEW_RUNTIME_ENABLED", True)
    monkeypatch.setattr(settings, "CANONICAL_ANALYTICAL_CONTRACT_ADAPTER_ENABLED", True)
    monkeypatch.setattr(settings, "CANONICAL_SHADOW_METRIC_VALIDITY_GATE_ENABLED", True)
    monkeypatch.setattr(settings, "CANONICAL_SHADOW_QUERY_RUNTIME_ENABLED", True)

    summary = build_canonical_tabular_canary_health()

    assert summary["status"] == "blocked"
    assert summary["shadow_evidence_ready"] is False
    assert summary["shadow_evidence_reason"] == "divergence_score_above_threshold"
