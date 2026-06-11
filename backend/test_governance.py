from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException

from app.services.governance import (
    build_document_governance_metadata,
    get_user_presentation_scope_or_404,
    get_user_report_scope_or_404,
    get_user_uploaded_file_scope_or_404,
    stamp_report_content_governance,
)


@dataclass
class FakeResponse:
    data: list[dict]


class FakeQuery:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = list(rows)
        self._filters: list[tuple[str, object]] = []
        self._limit: int | None = None

    def select(self, _columns: str) -> "FakeQuery":
        return self

    def eq(self, column: str, value: object) -> "FakeQuery":
        self._filters.append((column, value))
        return self

    def limit(self, value: int) -> "FakeQuery":
        self._limit = value
        return self

    def execute(self) -> FakeResponse:
        rows = self._rows
        for column, value in self._filters:
            rows = [row for row in rows if row.get(column) == value]
        if self._limit is not None:
            rows = rows[: self._limit]
        return FakeResponse(data=rows)


class FakeServiceClient:
    def __init__(self, tables: dict[str, list[dict]]) -> None:
        self._tables = tables

    def table(self, name: str) -> FakeQuery:
        return FakeQuery(self._tables.get(name, []))


def main() -> None:
    created_content = stamp_report_content_governance(
        content={"layout": {"x": 0, "y": 0, "w": 4, "h": 3}},
        user_id="00000000-0000-4000-8000-000000000001",
        team_id="00000000-0000-4000-8000-000000000002",
        file_id="file-1",
        presentation_id="presentation-1",
        revision_kind="create",
    )
    created_governance = created_content["governance"]
    assert created_governance["revision"] == 1
    assert created_governance["content_revision"] == 1
    assert created_governance["layout_revision"] == 0

    moved_content = stamp_report_content_governance(
        content=created_content,
        user_id="00000000-0000-4000-8000-000000000001",
        team_id="00000000-0000-4000-8000-000000000002",
        file_id="file-1",
        presentation_id="presentation-1",
        revision_kind="layout_update",
        increment_layout_revision=True,
    )
    moved_governance = moved_content["governance"]
    assert moved_governance["revision"] == 2
    assert moved_governance["content_revision"] == 1
    assert moved_governance["layout_revision"] == 1

    document_created = build_document_governance_metadata(
        metadata={"ingestion_mode": "async"},
        user_id="00000000-0000-4000-8000-000000000001",
        team_id="00000000-0000-4000-8000-000000000002",
        revision_kind="create",
    )
    document_retry = build_document_governance_metadata(
        metadata=document_created,
        user_id="00000000-0000-4000-8000-000000000001",
        team_id="00000000-0000-4000-8000-000000000002",
        revision_kind="retry_queue",
        increment_revision=True,
    )
    document_indexed = build_document_governance_metadata(
        metadata=document_retry,
        user_id="00000000-0000-4000-8000-000000000001",
        team_id="00000000-0000-4000-8000-000000000002",
        revision_kind="index",
        increment_index_revision=True,
    )
    assert document_created["governance"]["revision"] == 1
    assert document_retry["governance"]["revision"] == 2
    assert document_retry["governance"]["index_revision"] == 0
    assert document_indexed["governance"]["revision"] == 3
    assert document_indexed["governance"]["index_revision"] == 1

    fake_client = FakeServiceClient({
        "uploaded_files": [
            {
                "id": "file-1",
                "user_id": "00000000-0000-4000-8000-000000000001",
                "team_id": "00000000-0000-4000-8000-000000000002",
                "file_name": "dataset.xlsx",
                "storage_path": "user-1/dataset.xlsx",
                "created_at": "2026-04-25T00:00:00Z",
            }
        ],
        "presentations": [
            {
                "id": "presentation-1",
                "user_id": "00000000-0000-4000-8000-000000000001",
                "file_id": "file-1",
                "name": "Tablero",
                "created_at": "2026-04-25T00:00:00Z",
            }
        ],
        "saved_reports": [
            {
                "id": "report-1",
                "user_id": "00000000-0000-4000-8000-000000000001",
                "file_id": "file-1",
                "presentation_id": "presentation-1",
                "title": "Widget",
                "content": created_content,
                "created_at": "2026-04-25T00:00:00Z",
            }
        ],
    })

    uploaded = get_user_uploaded_file_scope_or_404(
        user_id="00000000-0000-4000-8000-000000000001",
        team_id="00000000-0000-4000-8000-000000000002",
        file_id="file-1",
        service_client=fake_client,
    )
    presentation = get_user_presentation_scope_or_404(
        user_id="00000000-0000-4000-8000-000000000001",
        team_id="00000000-0000-4000-8000-000000000002",
        presentation_id="presentation-1",
        service_client=fake_client,
    )
    report = get_user_report_scope_or_404(
        user_id="00000000-0000-4000-8000-000000000001",
        team_id="00000000-0000-4000-8000-000000000002",
        report_id="report-1",
        service_client=fake_client,
    )
    assert uploaded["id"] == "file-1"
    assert presentation["id"] == "presentation-1"
    assert report["id"] == "report-1"

    try:
        get_user_uploaded_file_scope_or_404(
            user_id="00000000-0000-4000-8000-000000000001",
            team_id="team-x",
            file_id="file-1",
            service_client=fake_client,
        )
    except HTTPException as error:
        assert error.status_code == 403
    else:
        raise AssertionError("Se esperaba HTTPException 403 para scope cruzado.")

    print("ok")


if __name__ == "__main__":
    main()
