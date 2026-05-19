from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.supabase_client import get_supabase_service_client
from app.services.canonical_canary_health import build_canonical_tabular_canary_health
from app.services.canonical_canary_router import build_canonical_tabular_canary_route
from app.services.canonical_shadow_runtime_observer import (
    _classify_prompt_type,
    _normalize_prompt,
)


_TABULAR_EXTENSIONS = {"csv", "xlsx", "xls"}


def _extension(file_name: str) -> str:
    normalized = str(file_name or "").strip().lower()
    if "." not in normalized:
        return ""
    return normalized.rsplit(".", 1)[-1]


def _coerce_positive_int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name) or default)
    except Exception:
        return int(default)
    return max(value, 1)


def _execute(query: Any) -> Any:
    return query.execute()


def _enable_canary_dry_run() -> None:
    settings.CANONICAL_TABULAR_CANARY_ROUTER_ENABLED = True
    settings.CANONICAL_TABULAR_CANARY_FUNCTIONAL_SWITCH_ENABLED = False
    settings.CANONICAL_TABULAR_CANARY_FAIL_OPEN_ENABLED = True
    settings.CANONICAL_TABULAR_CANARY_TRAFFIC_PERCENT = max(
        0,
        min(int(os.getenv("CANARY_DRY_RUN_TRAFFIC_PERCENT") or 5), 100),
    )
    if os.getenv("CANARY_DRY_RUN_ALLOWLIST_TEAM_IDS") is not None:
        settings.CANONICAL_TABULAR_CANARY_ALLOWLIST_TEAM_IDS = str(
            os.getenv("CANARY_DRY_RUN_ALLOWLIST_TEAM_IDS") or ""
        )
    if os.getenv("CANARY_DRY_RUN_ALLOWLIST_USER_IDS") is not None:
        settings.CANONICAL_TABULAR_CANARY_ALLOWLIST_USER_IDS = str(
            os.getenv("CANARY_DRY_RUN_ALLOWLIST_USER_IDS") or ""
        )
    if os.getenv("CANARY_DRY_RUN_ALLOWLIST_FILE_IDS") is not None:
        settings.CANONICAL_TABULAR_CANARY_ALLOWLIST_FILE_IDS = str(
            os.getenv("CANARY_DRY_RUN_ALLOWLIST_FILE_IDS") or ""
        )


def _fetch_uploaded_file_map(service_client: Any) -> dict[str, dict[str, Any]]:
    response = _execute(
        service_client.table("uploaded_files")
        .select("id, user_id, team_id, file_name, storage_path, created_at")
    )
    rows = [dict(row) for row in list(response.data or [])]
    return {
        str(row["id"]): row
        for row in rows
        if _extension(str(row.get("file_name") or "")) in _TABULAR_EXTENSIONS
    }


def _fetch_recent_completed_tasks(
    service_client: Any,
    uploaded_file_map: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    task_limit = _coerce_positive_int_env("CANARY_DRY_RUN_TASK_LIMIT", 60)
    max_tasks_per_file = _coerce_positive_int_env("CANARY_DRY_RUN_MAX_TASKS_PER_FILE", 8)
    fetch_limit = max(task_limit * max_tasks_per_file * 3, 120)
    response = _execute(
        service_client.table("analysis_tasks")
        .select("id, file_id, prompt, status, created_at")
        .eq("status", "completed")
        .order("created_at", desc=True)
        .limit(fetch_limit)
    )
    rows = [dict(row) for row in list(response.data or [])]
    selected_rows: list[dict[str, Any]] = []
    tasks_per_file: dict[str, int] = {}
    for row in rows:
        file_id = str(row.get("file_id") or "")
        if not file_id or file_id not in uploaded_file_map:
            continue
        current_count = tasks_per_file.get(file_id, 0)
        if current_count >= max_tasks_per_file:
            continue
        selected_rows.append(row)
        tasks_per_file[file_id] = current_count + 1
        if len(selected_rows) >= task_limit:
            break
    return selected_rows


def _increment(counter: dict[str, int], key: str) -> None:
    counter[key] = int(counter.get(key, 0)) + 1


def _bucket_summary(items: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for item in items:
        bucket = str(item.get(key) or "unknown")
        entry = buckets.setdefault(
            bucket,
            {
                key: bucket,
                "observed_count": 0,
                "requested_universal_count": 0,
                "effective_universal_count": 0,
                "fallback_count": 0,
            },
        )
        entry["observed_count"] += 1
        if item.get("requested_runtime") == "universal_tabular":
            entry["requested_universal_count"] += 1
        if item.get("effective_runtime") == "universal_tabular":
            entry["effective_universal_count"] += 1
        if item.get("requested_runtime") != item.get("effective_runtime"):
            entry["fallback_count"] += 1

    rows = list(buckets.values())
    for row in rows:
        observed_count = max(int(row["observed_count"]), 1)
        row["candidate_ratio"] = round(row["requested_universal_count"] / observed_count, 4)
        row["effective_universal_ratio"] = round(row["effective_universal_count"] / observed_count, 4)
        row["fallback_ratio"] = round(row["fallback_count"] / observed_count, 4)
    return sorted(rows, key=lambda row: (-int(row["observed_count"]), str(row[key])))


def main() -> int:
    _enable_canary_dry_run()
    service_client = get_supabase_service_client()
    uploaded_file_map = _fetch_uploaded_file_map(service_client)
    task_rows = _fetch_recent_completed_tasks(service_client, uploaded_file_map)
    health_summary = build_canonical_tabular_canary_health()

    results: list[dict[str, Any]] = []
    requested_runtime_counts: dict[str, int] = {}
    effective_runtime_counts: dict[str, int] = {}
    decision_mode_counts: dict[str, int] = {}

    for task_row in task_rows:
        file_id = str(task_row.get("file_id") or "")
        uploaded_row = uploaded_file_map[file_id]
        prompt = _normalize_prompt(task_row.get("prompt"))
        prompt_type = _classify_prompt_type(prompt, {})
        route = build_canonical_tabular_canary_route(
            task_id=str(task_row.get("id") or ""),
            file_id=file_id,
            file_name=str(uploaded_row.get("file_name") or ""),
            user_id=str(uploaded_row.get("user_id") or ""),
            team_id=str(uploaded_row.get("team_id") or ""),
            prompt=prompt,
            health_summary=health_summary,
        )
        result_row = {
            "task_id": str(task_row.get("id") or ""),
            "file_id": file_id,
            "file_name": str(uploaded_row.get("file_name") or ""),
            "team_id": str(uploaded_row.get("team_id") or "") or "unknown",
            "user_id": str(uploaded_row.get("user_id") or "") or "unknown",
            "created_at": task_row.get("created_at"),
            "prompt_type": prompt_type,
            "requested_runtime": route.get("requested_runtime"),
            "effective_runtime": route.get("effective_runtime"),
            "decision_mode": route.get("decision_mode"),
            "decision_reason": route.get("decision_reason"),
            "bucket_value": route.get("bucket_value"),
            "traffic_percent": route.get("traffic_percent"),
        }
        results.append(result_row)
        _increment(requested_runtime_counts, str(route.get("requested_runtime") or "unknown"))
        _increment(effective_runtime_counts, str(route.get("effective_runtime") or "unknown"))
        _increment(decision_mode_counts, str(route.get("decision_mode") or "unknown"))

    report = {
        "selection": {
            "task_limit": _coerce_positive_int_env("CANARY_DRY_RUN_TASK_LIMIT", 60),
            "max_tasks_per_file": _coerce_positive_int_env("CANARY_DRY_RUN_MAX_TASKS_PER_FILE", 8),
            "traffic_percent": int(settings.CANONICAL_TABULAR_CANARY_TRAFFIC_PERCENT),
            "allowlist_team_ids": settings.CANONICAL_TABULAR_CANARY_ALLOWLIST_TEAM_IDS,
            "allowlist_user_ids": settings.CANONICAL_TABULAR_CANARY_ALLOWLIST_USER_IDS,
            "allowlist_file_ids": settings.CANONICAL_TABULAR_CANARY_ALLOWLIST_FILE_IDS,
        },
        "canary_health": health_summary,
        "task_count": len(results),
        "file_count": len({str(item.get("file_id") or "") for item in results}),
        "requested_runtime_counts": requested_runtime_counts,
        "effective_runtime_counts": effective_runtime_counts,
        "decision_mode_counts": decision_mode_counts,
        "candidate_count": sum(1 for item in results if item.get("requested_runtime") == "universal_tabular"),
        "fallback_count": sum(1 for item in results if item.get("requested_runtime") != item.get("effective_runtime")),
        "distribution_by_prompt_type": _bucket_summary(results, "prompt_type"),
        "distribution_by_file_name": _bucket_summary(results, "file_name"),
        "distribution_by_team_id": _bucket_summary(results, "team_id"),
        "tasks": results,
    }
    output_path = Path(
        os.getenv("CANARY_DRY_RUN_REPORT_PATH") or "/tmp/promdata_real_canary_dry_run_report.json"
    )
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[canary-dry-run] report={output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
