from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.supabase_client import get_supabase_service_client
from app.services.canonical_analytical_contract_adapter import get_selected_candidate_dataframe
from app.services.canonical_canary_health import build_canonical_tabular_canary_health
from app.services.canonical_canary_router import build_canonical_tabular_canary_route
from app.services.canonical_shadow_query_runner import summarize_canonical_shadow_query_execution
from app.services.canonical_shadow_runtime_observer import (
    _classify_prompt_type,
    _normalize_prompt,
    build_live_runtime_summary,
    build_shadow_live_divergence_summary,
)
from app.services.canonical_tabular_canary_executor import execute_canonical_tabular_canary_analysis


_TABULAR_EXTENSIONS = {"csv", "xlsx", "xls"}


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _extension(file_name: str) -> str:
    normalized = _normalize_text(file_name).lower()
    if "." not in normalized:
        return ""
    return normalized.rsplit(".", 1)[-1]


def _coerce_positive_int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name) or default)
    except Exception:
        return int(default)
    return max(value, 1)


def _enable_functional_canary_flags() -> None:
    settings.CANONICAL_TABULAR_CANARY_ROUTER_ENABLED = True
    settings.CANONICAL_TABULAR_CANARY_FUNCTIONAL_SWITCH_ENABLED = True
    settings.CANONICAL_TABULAR_CANARY_FAIL_OPEN_ENABLED = True
    settings.CANONICAL_TABULAR_CANARY_TRAFFIC_PERCENT = 0
    settings.CANONICAL_NATIVE_TABULAR_EXTRACTION_ENABLED = True
    settings.CANONICAL_IBIS_PREVIEW_RUNTIME_ENABLED = True
    settings.CANONICAL_ANALYTICAL_CONTRACT_ADAPTER_ENABLED = True
    settings.CANONICAL_SHADOW_METRIC_VALIDITY_GATE_ENABLED = True
    settings.CANONICAL_SHADOW_QUERY_RUNTIME_ENABLED = True


def _execute(query: Any) -> Any:
    return query.execute()


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
    task_limit = _coerce_positive_int_env("CANARY_FUNCTIONAL_TASK_LIMIT", 12)
    max_tasks_per_file = _coerce_positive_int_env("CANARY_FUNCTIONAL_MAX_TASKS_PER_FILE", 4)
    fetch_limit = max(task_limit * max_tasks_per_file * 3, 80)
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


def _fetch_task_results_json(service_client: Any, task_id: str) -> dict[str, Any]:
    response = _execute(
        service_client.table("analysis_tasks")
        .select("results_json")
        .eq("id", task_id)
        .single()
    )
    payload = dict(response.data or {}) if getattr(response, "data", None) else {}
    return _parse_results_json(payload.get("results_json"))


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


def _pick_allowlist_team_id(rows: list[dict[str, Any]], uploaded_file_map: dict[str, dict[str, Any]]) -> str:
    explicit = _normalize_text(os.getenv("CANARY_FUNCTIONAL_ALLOWLIST_TEAM_ID"))
    if explicit:
        return explicit
    for row in rows:
        uploaded_row = uploaded_file_map.get(str(row.get("file_id") or ""))
        if not uploaded_row:
            continue
        team_id = _normalize_text(uploaded_row.get("team_id"))
        if team_id:
            return team_id
    return ""


def main() -> int:
    _enable_functional_canary_flags()
    service_client = get_supabase_service_client()
    uploaded_file_map = _fetch_uploaded_file_map(service_client)
    task_rows = _fetch_recent_completed_tasks(service_client, uploaded_file_map)
    allowlist_team_id = _pick_allowlist_team_id(task_rows, uploaded_file_map)
    settings.CANONICAL_TABULAR_CANARY_ALLOWLIST_TEAM_IDS = allowlist_team_id
    health_summary = build_canonical_tabular_canary_health()

    results: list[dict[str, Any]] = []
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
        if route.get("effective_runtime") != "universal_tabular":
            continue

        live_final_struct = _fetch_task_results_json(service_client, str(task_row.get("id") or ""))
        live_summary = build_live_runtime_summary(
            status=str(task_row.get("status") or ""),
            prompt=prompt,
            final_struct=live_final_struct,
            dataset_contract={},
            live_duration_ms=None,
        )
        try:
            canary_result = execute_canonical_tabular_canary_analysis(
                file_id=file_id,
                prompt=prompt,
                service_client=service_client,
                uploaded_file_row=uploaded_row,
                prompt_type=prompt_type,
                requested_visual_family=None,
                max_plans=max(int(settings.CANONICAL_SHADOW_TRAFFIC_MIRROR_MAX_PLANS or 3), 1),
            )
            shadow_summary = summarize_canonical_shadow_query_execution(canary_result.execution)
            candidate_df = get_selected_candidate_dataframe(
                canary_result.execution.pipeline_result.analytical_adapter_runtime
            )
            attrs = getattr(candidate_df, "attrs", {}) or {} if candidate_df is not None else {}
            candidate_contract = {
                "dataset_mode": _normalize_text((attrs.get("semantic_contract") or {}).get("dataset_mode")) or None,
                "time_axis": _normalize_text((attrs.get("semantic_contract") or {}).get("time_axis")) or None,
            }
            divergence = build_shadow_live_divergence_summary(
                live_summary=live_summary,
                shadow_summary=shadow_summary,
                shadow_candidate_contract=candidate_contract,
            )
            results.append(
                {
                    "task_id": str(task_row.get("id") or ""),
                    "file_id": file_id,
                    "file_name": str(uploaded_row.get("file_name") or ""),
                    "team_id": str(uploaded_row.get("team_id") or ""),
                    "prompt_type": prompt_type,
                    "route": route,
                    "execution_status": canary_result.status,
                    "prompt_strategy": canary_result.execution.prompt_strategy,
                    "candidate_id": canary_result.execution.metadata.get("candidate_id"),
                    "shadow_query_status": canary_result.execution.metadata.get("shadow_query_status"),
                    "live_chart_count": live_summary.get("chart_count"),
                    "canary_chart_count": len(list(canary_result.final_struct.get("chart_options") or [])),
                    "divergence": divergence,
                }
            )
        except Exception as exc:
            results.append(
                {
                    "task_id": str(task_row.get("id") or ""),
                    "file_id": file_id,
                    "file_name": str(uploaded_row.get("file_name") or ""),
                    "team_id": str(uploaded_row.get("team_id") or ""),
                    "prompt_type": prompt_type,
                    "route": route,
                    "execution_status": "fallback_would_trigger",
                    "error": str(exc),
                }
            )

    alignment_counts: dict[str, int] = {}
    fallback_count = 0
    for item in results:
        alignment = _normalize_text((item.get("divergence") or {}).get("alignment_grade"))
        if alignment:
            alignment_counts[alignment] = int(alignment_counts.get(alignment, 0)) + 1
        if item.get("execution_status") == "fallback_would_trigger":
            fallback_count += 1

    report = {
        "selection": {
            "task_limit": _coerce_positive_int_env("CANARY_FUNCTIONAL_TASK_LIMIT", 12),
            "max_tasks_per_file": _coerce_positive_int_env("CANARY_FUNCTIONAL_MAX_TASKS_PER_FILE", 4),
            "allowlist_team_id": allowlist_team_id,
        },
        "canary_health": health_summary,
        "task_count": len(results),
        "alignment_counts": alignment_counts,
        "fallback_count": fallback_count,
        "executions": results,
    }
    output_path = Path(
        os.getenv("CANARY_FUNCTIONAL_REPORT_PATH") or "/tmp/promdata_real_canary_functional_report.json"
    )
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[canary-functional] report={output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
