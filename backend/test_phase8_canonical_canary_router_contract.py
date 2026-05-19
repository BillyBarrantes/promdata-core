from app.core.config import settings
from app.services.canonical_canary_router import build_canonical_tabular_canary_route


def test_canary_router_defaults_to_legacy_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "CANONICAL_TABULAR_CANARY_ROUTER_ENABLED", False)
    monkeypatch.setattr(settings, "CANONICAL_TABULAR_CANARY_FUNCTIONAL_SWITCH_ENABLED", False)

    route = build_canonical_tabular_canary_route(
        task_id="task-1",
        file_id="file-1",
        file_name="ventas.xlsx",
        user_id="user-1",
        team_id="team-1",
        prompt="Analiza ventas por canal",
        health_summary={"status": "disabled", "ready_for_functional_canary": False},
    )

    assert route["requested_runtime"] == "legacy"
    assert route["effective_runtime"] == "legacy"
    assert route["decision_mode"] == "router_disabled"


def test_canary_router_allowlists_but_keeps_legacy_when_switch_is_off(monkeypatch):
    monkeypatch.setattr(settings, "CANONICAL_TABULAR_CANARY_ROUTER_ENABLED", True)
    monkeypatch.setattr(settings, "CANONICAL_TABULAR_CANARY_FUNCTIONAL_SWITCH_ENABLED", False)
    monkeypatch.setattr(settings, "CANONICAL_TABULAR_CANARY_ALLOWLIST_TEAM_IDS", "team-1")

    route = build_canonical_tabular_canary_route(
        task_id="task-2",
        file_id="file-2",
        file_name="rrhh.csv",
        user_id="user-1",
        team_id="team-1",
        prompt="Analiza headcount por área",
        health_summary={"status": "dry_run", "ready_for_functional_canary": False},
    )

    assert route["requested_runtime"] == "universal_tabular"
    assert route["effective_runtime"] == "legacy"
    assert route["decision_mode"] == "allowlist_team"
    assert route["decision_reason"] == "functional_switch_disabled"


def test_canary_router_routes_to_universal_when_health_gate_passes(monkeypatch):
    monkeypatch.setattr(settings, "CANONICAL_TABULAR_CANARY_ROUTER_ENABLED", True)
    monkeypatch.setattr(settings, "CANONICAL_TABULAR_CANARY_FUNCTIONAL_SWITCH_ENABLED", True)
    monkeypatch.setattr(settings, "CANONICAL_TABULAR_CANARY_ALLOWLIST_USER_IDS", "user-2")

    route = build_canonical_tabular_canary_route(
        task_id="task-3",
        file_id="file-3",
        file_name="finanzas.xlsx",
        user_id="user-2",
        team_id="team-9",
        prompt="Realiza un análisis completo del flujo de caja",
        health_summary={"status": "ready", "ready_for_functional_canary": True},
    )

    assert route["requested_runtime"] == "universal_tabular"
    assert route["effective_runtime"] == "universal_tabular"
    assert route["decision_reason"] == "canary_health_gate_passed"


def test_canary_router_fails_open_to_legacy_when_health_gate_blocks(monkeypatch):
    monkeypatch.setattr(settings, "CANONICAL_TABULAR_CANARY_ROUTER_ENABLED", True)
    monkeypatch.setattr(settings, "CANONICAL_TABULAR_CANARY_FUNCTIONAL_SWITCH_ENABLED", True)
    monkeypatch.setattr(settings, "CANONICAL_TABULAR_CANARY_TRAFFIC_PERCENT", 100)
    monkeypatch.setattr(settings, "CANONICAL_TABULAR_CANARY_FAIL_OPEN_ENABLED", True)

    route = build_canonical_tabular_canary_route(
        task_id="task-4",
        file_id="file-4",
        file_name="operaciones.xlsx",
        user_id="user-4",
        team_id="team-4",
        prompt="Analiza tiempos por turno",
        health_summary={"status": "blocked", "ready_for_functional_canary": False},
    )

    assert route["requested_runtime"] == "universal_tabular"
    assert route["effective_runtime"] == "legacy"
    assert route["decision_reason"] == "health_gate_blocked_fail_open"
