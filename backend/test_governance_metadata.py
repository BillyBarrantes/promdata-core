from __future__ import annotations

from app.services.governance import (
    build_document_governance_metadata,
    stamp_report_content_governance,
)


def main() -> None:
    report_content = stamp_report_content_governance(
        content={"layout": {"x": 0, "y": 0, "w": 6, "h": 4}},
        user_id="user-1",
        team_id="team-1",
        file_id="file-1",
        presentation_id="presentation-1",
        revision_kind="create",
    )
    governance = report_content.get("governance") or {}
    assert governance["revision"] == 1
    assert governance["content_revision"] == 1
    assert governance["layout_revision"] == 0
    assert governance["access_scope"] == "user_team_bound"

    updated_content = stamp_report_content_governance(
        content=report_content,
        user_id="user-1",
        team_id="team-1",
        file_id="file-1",
        presentation_id="presentation-1",
        revision_kind="layout_update",
        increment_layout_revision=True,
    )
    updated_governance = updated_content.get("governance") or {}
    assert updated_governance["revision"] == 2
    assert updated_governance["content_revision"] == 1
    assert updated_governance["layout_revision"] == 1

    document_metadata = build_document_governance_metadata(
        metadata={"ingestion_mode": "async"},
        user_id="user-1",
        team_id="team-1",
        revision_kind="create",
    )
    doc_governance = document_metadata.get("governance") or {}
    assert doc_governance["revision"] == 1
    assert doc_governance["index_revision"] == 0

    indexed_metadata = build_document_governance_metadata(
        metadata=document_metadata,
        user_id="user-1",
        team_id="team-1",
        revision_kind="index",
        increment_index_revision=True,
    )
    indexed_governance = indexed_metadata.get("governance") or {}
    assert indexed_governance["revision"] == 2
    assert indexed_governance["index_revision"] == 1
    print("ok")


if __name__ == "__main__":
    main()
