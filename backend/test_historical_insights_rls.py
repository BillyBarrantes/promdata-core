import ast
from pathlib import Path
from types import SimpleNamespace

from app.tasks.analysis_pipeline import memory_router


class _InsertQuery:
    def __init__(self, recorded_rows: list[dict]) -> None:
        self.recorded_rows = recorded_rows

    def insert(self, row: dict) -> "_InsertQuery":
        self.recorded_rows.append(row)
        return self

    def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self.recorded_rows)


class _AuthenticatedClient:
    def __init__(self, recorded_rows: list[dict]) -> None:
        self.recorded_rows = recorded_rows

    def table(self, table_name: str) -> _InsertQuery:
        assert table_name == "historical_insights"
        return _InsertQuery(self.recorded_rows)


class _ServiceRoleClient:
    def table(self, table_name: str) -> None:
        raise AssertionError(f"Service-role client must not access {table_name}")


def test_insight_write_uses_the_authenticated_user_client(monkeypatch) -> None:
    recorded_rows: list[dict] = []
    seen_tokens: list[str] = []
    authenticated_client = _AuthenticatedClient(recorded_rows)

    monkeypatch.setattr(memory_router, "get_embedding", lambda _text: [0.1, 0.2])

    def _build_user_client(access_token: str) -> _AuthenticatedClient:
        seen_tokens.append(access_token)
        return authenticated_client

    monkeypatch.setattr(memory_router, "get_supabase_user_client", _build_user_client)

    memory_router.guardar_insight_aprendido(
        _ServiceRoleClient(),
        "owner-user-id",
        "Insight del usuario",
        "SELECT 1",
        {"source": "analysis"},
        user_access_token="owner-jwt",
    )

    assert seen_tokens == ["owner-jwt"]
    assert len(recorded_rows) == 1
    assert recorded_rows[0]["user_id"] == "owner-user-id"


def test_insight_write_fails_closed_without_user_token(monkeypatch) -> None:
    def _unexpected_embedding(_text: str) -> list[float]:
        raise AssertionError("Do not prepare or write an insight without a user JWT")

    def _unexpected_client(_access_token: str) -> _AuthenticatedClient:
        raise AssertionError("Do not construct a client without a user JWT")

    monkeypatch.setattr(memory_router, "get_embedding", _unexpected_embedding)
    monkeypatch.setattr(memory_router, "get_supabase_user_client", _unexpected_client)

    memory_router.guardar_insight_aprendido(
        _ServiceRoleClient(),
        "owner-user-id",
        "Insight del usuario",
        "SELECT 1",
        {},
    )


def test_legacy_success_path_propagates_the_user_token() -> None:
    source = Path(__file__).parent / "app/tasks/analysis_pipeline/legacy_codegen.py"
    module = ast.parse(source.read_text(encoding="utf-8"))
    pipeline = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "generar_analisis"
    )
    calls = [
        node
        for node in ast.walk(pipeline)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "guardar_insight_aprendido"
    ]

    assert calls
    assert any(
        keyword.arg == "user_access_token"
        and isinstance(keyword.value, ast.Name)
        and keyword.value.id == "user_token"
        for call in calls
        for keyword in call.keywords
    )
