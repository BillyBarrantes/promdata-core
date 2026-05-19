from __future__ import annotations

import json
import os
from pathlib import Path
from time import sleep
from typing import Any

from app.core.config import settings
from app.core.supabase_client import get_supabase_service_client
from app.services.canonical_shadow_runtime_observer import (
    build_live_runtime_summary,
    observe_canonical_shadow_runtime,
)
from app.services.enterprise_telemetry import (
    list_enterprise_telemetry_events,
    summarize_shadow_runtime_telemetry,
)


_TABULAR_EXTENSIONS = {"csv", "xlsx", "xls"}


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
    settings.CANONICAL_SHADOW_TRAFFIC_MIRROR_ENABLED = True


def _coerce_positive_int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name) or default)
    except Exception:
        return int(default)
    return max(value, 1)


def _window_days_options() -> list[int]:
    raw_value = str(os.getenv("SHADOW_STABILITY_WINDOWS_DAYS") or "1,7,30").strip()
    values: list[int] = []
    for chunk in raw_value.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            parsed = int(chunk)
        except Exception:
            continue
        if parsed > 0 and parsed not in values:
            values.append(parsed)
    return values or [1, 7, 30]


def _execute_with_retry(query: Any, *, retries: int = 3, wait_seconds: float = 1.0) -> Any:
    last_error: Exception | None = None
    for attempt in range(1, max(retries, 1) + 1):
        try:
            return query.execute()
        except Exception as exc:
            last_error = exc
            if attempt >= retries:
                raise
            sleep(wait_seconds * attempt)
    raise last_error or RuntimeError("query_retry_failed")


def _fetch_uploaded_file_map(service_client: Any) -> dict[str, dict[str, Any]]:
    response = _execute_with_retry(
        service_client.table("uploaded_files")
        .select("id, user_id, team_id, file_name, storage_path, created_at")
    )
    rows = [dict(row) for row in list(response.data or [])]
    return {
        str(row["id"]): row
        for row in rows
        if _extension(str(row.get("file_name") or "")) in _TABULAR_EXTENSIONS
    }


def _parse_results_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _fetch_latest_completed_tasks(service_client: Any, uploaded_file_map: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    task_limit = _coerce_positive_int_env("SHADOW_TRAFFIC_TASK_LIMIT", 36)
    max_tasks_per_file = _coerce_positive_int_env("SHADOW_TRAFFIC_MAX_TASKS_PER_FILE", 6)
    fetch_limit = max(task_limit * max_tasks_per_file * 3, 80)
    response = _execute_with_retry(
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


def _collect_shadow_runtime_telemetry(
    *,
    uploaded_file_map: dict[str, dict[str, Any]],
    results: list[dict[str, Any]],
    service_client: Any,
) -> dict[str, Any]:
    selected_task_ids = {
        str(item.get("task_id") or "")
        for item in results
        if str(item.get("task_id") or "")
    }
    selected_user_ids = {
        str(uploaded_file_map[str(item["file_id"])].get("user_id") or "")
        for item in results
        if str(item.get("file_id") or "") in uploaded_file_map
        and str(uploaded_file_map[str(item["file_id"])].get("user_id") or "")
    }
    latest_rows_by_task_metric: dict[tuple[str, str], dict[str, Any]] = {}
    for user_id in sorted(selected_user_ids):
        user_rows = list_enterprise_telemetry_events(
            user_id=user_id,
            service_client=service_client,
            window_days=_coerce_positive_int_env("SHADOW_TELEMETRY_WINDOW_DAYS", 30),
        ) or []
        for row in user_rows:
            dimensions = dict(row.get("dimensions") or {}) if isinstance(row.get("dimensions"), dict) else {}
            task_id = str(dimensions.get("task_id") or "").strip()
            if task_id not in selected_task_ids:
                continue
            metric_name = str(row.get("metric_name") or "").strip()
            key = (task_id, metric_name)
            existing = latest_rows_by_task_metric.get(key)
            if existing is None or str(row.get("created_at") or "") >= str(existing.get("created_at") or ""):
                latest_rows_by_task_metric[key] = dict(row)
    return summarize_shadow_runtime_telemetry(
        metric_rows=list(latest_rows_by_task_metric.values()),
        allowed_task_ids=selected_task_ids,
        window_days_options=_window_days_options(),
    )


def _fetch_task_results_json(service_client: Any, task_id: str) -> dict[str, Any]:
    response = _execute_with_retry(
        service_client.table("analysis_tasks")
        .select("results_json")
        .eq("id", task_id)
        .single()
    )
    payload = dict(response.data or {}) if getattr(response, "data", None) else {}
    return _parse_results_json(payload.get("results_json"))


def main() -> int:
    _enable_shadow_flags()
    service_client = get_supabase_service_client()
    uploaded_file_map = _fetch_uploaded_file_map(service_client)
    task_rows = _fetch_latest_completed_tasks(service_client, uploaded_file_map)

    results: list[dict[str, Any]] = []
    for task_row in task_rows:
        file_id = str(task_row["file_id"])
        uploaded_row = uploaded_file_map[file_id]
        file_name = str(uploaded_row.get("file_name") or "")
        try:
            final_struct = _fetch_task_results_json(service_client, str(task_row["id"]))
            live_summary = build_live_runtime_summary(
                status=str(task_row.get("status") or ""),
                prompt=str(task_row.get("prompt") or ""),
                final_struct=final_struct,
                dataset_contract={},
                live_duration_ms=None,
            )
            observer_summary = observe_canonical_shadow_runtime(
                task_id=str(task_row["id"]),
                file_id=file_id,
                prompt=str(task_row.get("prompt") or ""),
                live_summary=live_summary,
                uploaded_file_row=uploaded_row,
                service_client=service_client,
            )
            results.append(
                {
                    "task_id": task_row["id"],
                    "file_id": file_id,
                    "file_name": file_name,
                    "created_at": task_row.get("created_at"),
                    "observer": observer_summary,
                }
            )
            divergence = observer_summary.get("divergence", {}) if isinstance(observer_summary, dict) else {}
            print(
                f"[shadow-traffic] {file_name} | observer={observer_summary.get('observer_status')} "
                f"| alignment={divergence.get('alignment_grade')} "
                f"| mismatches={len(divergence.get('mismatches') or [])}"
            )
        except Exception as exc:
            results.append(
                {
                    "task_id": task_row["id"],
                    "file_id": file_id,
                    "file_name": file_name,
                    "created_at": task_row.get("created_at"),
                    "error": str(exc),
                }
            )
            print(f"[shadow-traffic] {file_name} | error={exc}")

    report = {
        "selection": {
            "task_limit": _coerce_positive_int_env("SHADOW_TRAFFIC_TASK_LIMIT", 36),
            "max_tasks_per_file": _coerce_positive_int_env("SHADOW_TRAFFIC_MAX_TASKS_PER_FILE", 6),
            "telemetry_window_days": _coerce_positive_int_env("SHADOW_TELEMETRY_WINDOW_DAYS", 30),
            "stability_windows_days": _window_days_options(),
        },
        "task_count": len(results),
        "file_count": len({str(item.get("file_id") or "") for item in results if str(item.get("file_id") or "")}),
        "files": results,
        "telemetry_shadow_runtime": _collect_shadow_runtime_telemetry(
            uploaded_file_map=uploaded_file_map,
            results=results,
            service_client=service_client,
        ),
    }
    output_path = Path(
        os.getenv("SHADOW_TRAFFIC_REPORT_PATH") or "/tmp/promdata_real_shadow_traffic_report.json"
    )
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[shadow-traffic] report={output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
