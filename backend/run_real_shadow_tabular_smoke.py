from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.supabase_client import get_supabase_service_client
from app.services.canonical_shadow_query_runner import (
    run_canonical_shadow_query_for_uploaded_file,
    summarize_canonical_shadow_query_execution,
)
from app.services.canonical_shadow_format_comparator import summarize_shadow_corpus_readiness


_TABULAR_EXTENSIONS = {"csv", "xlsx", "xls"}
_MIME_BY_EXTENSION = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xls": "application/vnd.ms-excel",
}


def _extension(file_name: str) -> str:
    normalized = str(file_name or "").strip().lower()
    if "." not in normalized:
        return ""
    return normalized.rsplit(".", 1)[-1]


def _enable_shadow_flags() -> None:
    settings.CANONICAL_NATIVE_TABULAR_EXTRACTION_ENABLED = True
    settings.CANONICAL_IBIS_PREVIEW_RUNTIME_ENABLED = True
    settings.CANONICAL_ANALYTICAL_CONTRACT_ADAPTER_ENABLED = True
    settings.CANONICAL_SHADOW_METRIC_VALIDITY_GATE_ENABLED = True
    settings.CANONICAL_DARK_RUNTIME_ORCHESTRATOR_ENABLED = True
    settings.CANONICAL_SHADOW_QUERY_RUNTIME_ENABLED = True


def _fetch_real_tabular_rows(service_client: Any) -> list[dict[str, Any]]:
    response = (
        service_client.table("uploaded_files")
        .select("id, user_id, team_id, file_name, storage_path, created_at")
        .execute()
    )
    rows = [dict(row) for row in list(response.data or [])]
    filtered_rows = [
        row
        for row in rows
        if _extension(str(row.get("file_name") or "")) in _TABULAR_EXTENSIONS
    ]
    filtered_rows.sort(key=lambda row: str(row.get("file_name") or "").lower())
    return filtered_rows


def main() -> int:
    _enable_shadow_flags()
    service_client = get_supabase_service_client()
    uploaded_rows = _fetch_real_tabular_rows(service_client)

    results: list[dict[str, Any]] = []
    readiness_rows: list[dict[str, Any]] = []
    for row in uploaded_rows:
        file_name = str(row.get("file_name") or "")
        ext = _extension(file_name)
        try:
            execution = run_canonical_shadow_query_for_uploaded_file(
                file_id=str(row["id"]),
                service_client=service_client,
                uploaded_file_row=row,
                mime_type=_MIME_BY_EXTENSION.get(ext),
            )
            summary = summarize_canonical_shadow_query_execution(execution)
            readiness_rows.append(dict(execution.readiness_summary))
            results.append(
                {
                    "file_id": row["id"],
                    "file_name": file_name,
                    "storage_path": row.get("storage_path"),
                    "created_at": row.get("created_at"),
                    "readiness": execution.readiness_summary,
                    "shadow_query": summary,
                }
            )
            print(
                f"[shadow-tabular] {file_name} | readiness={execution.readiness_summary.get('readiness_grade')} "
                f"| score={execution.readiness_summary.get('readiness_score')} "
                f"| query={summary.get('shadow_query_status')} "
                f"| plans={summary.get('plan_count')}"
            )
        except Exception as exc:
            results.append(
                {
                    "file_id": row["id"],
                    "file_name": file_name,
                    "storage_path": row.get("storage_path"),
                    "created_at": row.get("created_at"),
                    "error": str(exc),
                }
            )
            print(f"[shadow-tabular] {file_name} | error={exc}")

    report = {
        "file_count": len(results),
        "files": results,
        "corpus_readiness": summarize_shadow_corpus_readiness(readiness_rows),
    }
    output_path = Path("/tmp/promdata_real_shadow_tabular_report.json")
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[shadow-tabular] report={output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
