from __future__ import annotations

import hashlib
from typing import Any

from app.core.config import settings


_TABULAR_EXTENSIONS = {"csv", "xlsx", "xls"}


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _extension(file_name: str) -> str:
    normalized = _normalize_text(file_name).lower()
    if "." not in normalized:
        return ""
    return normalized.rsplit(".", 1)[-1]


def _parse_csv_ids(raw_value: str) -> set[str]:
    return {
        token
        for token in (
            _normalize_text(chunk)
            for chunk in str(raw_value or "").split(",")
        )
        if token
    }


def _stable_bucket(*parts: Any) -> int:
    seed = "|".join(_normalize_text(value) for value in parts if _normalize_text(value))
    if not seed:
        return 0
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 100


def _normalize_percent(value: Any) -> int:
    try:
        parsed = int(value or 0)
    except Exception:
        return 0
    return max(0, min(parsed, 100))


def build_canonical_tabular_canary_route(
    *,
    task_id: str,
    file_id: str,
    file_name: str,
    user_id: str | None,
    team_id: str | None,
    prompt: str | None,
    health_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_task_id = _normalize_text(task_id)
    normalized_file_id = _normalize_text(file_id)
    normalized_file_name = _normalize_text(file_name)
    normalized_user_id = _normalize_text(user_id)
    normalized_team_id = _normalize_text(team_id)
    file_extension = _extension(normalized_file_name)
    router_enabled = bool(settings.CANONICAL_TABULAR_CANARY_ROUTER_ENABLED)
    functional_switch_enabled = bool(settings.CANONICAL_TABULAR_CANARY_FUNCTIONAL_SWITCH_ENABLED)
    fail_open_enabled = bool(settings.CANONICAL_TABULAR_CANARY_FAIL_OPEN_ENABLED)
    traffic_percent = _normalize_percent(settings.CANONICAL_TABULAR_CANARY_TRAFFIC_PERCENT)
    normalized_prompt = _normalize_text(prompt)
    bucket_value = _stable_bucket(
        settings.CANONICAL_TABULAR_CANARY_BUCKET_SALT,
        normalized_team_id,
        normalized_user_id,
        normalized_file_id,
        normalized_task_id,
    )

    allowlisted_files = _parse_csv_ids(settings.CANONICAL_TABULAR_CANARY_ALLOWLIST_FILE_IDS)
    allowlisted_users = _parse_csv_ids(settings.CANONICAL_TABULAR_CANARY_ALLOWLIST_USER_IDS)
    allowlisted_teams = _parse_csv_ids(settings.CANONICAL_TABULAR_CANARY_ALLOWLIST_TEAM_IDS)

    requested_runtime = "legacy"
    effective_runtime = "legacy"
    decision_mode = "router_disabled"
    decision_reason = "canonical_tabular_canary_router_disabled"
    allowlist_match: str | None = None
    eligible = file_extension in _TABULAR_EXTENSIONS

    if router_enabled and eligible:
        if normalized_file_id and normalized_file_id in allowlisted_files:
            requested_runtime = "universal_tabular"
            decision_mode = "allowlist_file"
            decision_reason = "file_id_allowlisted"
            allowlist_match = "file_id"
        elif normalized_team_id and normalized_team_id in allowlisted_teams:
            requested_runtime = "universal_tabular"
            decision_mode = "allowlist_team"
            decision_reason = "team_id_allowlisted"
            allowlist_match = "team_id"
        elif normalized_user_id and normalized_user_id in allowlisted_users:
            requested_runtime = "universal_tabular"
            decision_mode = "allowlist_user"
            decision_reason = "user_id_allowlisted"
            allowlist_match = "user_id"
        elif traffic_percent > 0 and bucket_value < traffic_percent:
            requested_runtime = "universal_tabular"
            decision_mode = "traffic_percent"
            decision_reason = "stable_hash_bucket_selected"
        else:
            decision_mode = "legacy_default"
            decision_reason = "traffic_not_selected"
    elif router_enabled and not eligible:
        decision_mode = "unsupported_format"
        decision_reason = "file_extension_not_tabular"

    health = dict(health_summary or {})
    health_status = _normalize_text(health.get("status")) or "unknown"
    ready_for_functional_canary = bool(health.get("ready_for_functional_canary"))

    if requested_runtime != "universal_tabular":
        effective_runtime = "legacy"
    elif not functional_switch_enabled:
        effective_runtime = "legacy"
        decision_reason = "functional_switch_disabled"
    elif ready_for_functional_canary:
        effective_runtime = "universal_tabular"
        decision_reason = "canary_health_gate_passed"
    elif fail_open_enabled:
        effective_runtime = "legacy"
        decision_reason = "health_gate_blocked_fail_open"
    else:
        effective_runtime = "legacy"
        decision_reason = "health_gate_blocked"

    return {
        "task_id": normalized_task_id,
        "file_id": normalized_file_id,
        "file_name": normalized_file_name,
        "user_id": normalized_user_id or None,
        "team_id": normalized_team_id or None,
        "prompt_preview": normalized_prompt[:120] if normalized_prompt else None,
        "router_enabled": router_enabled,
        "functional_switch_enabled": functional_switch_enabled,
        "fail_open_enabled": fail_open_enabled,
        "eligible": eligible,
        "file_extension": file_extension,
        "traffic_percent": traffic_percent,
        "bucket_value": bucket_value,
        "allowlist_match": allowlist_match,
        "requested_runtime": requested_runtime,
        "effective_runtime": effective_runtime,
        "fallback_runtime": "legacy",
        "decision_mode": decision_mode,
        "decision_reason": decision_reason,
        "health_status": health_status,
        "health_ready_for_functional_canary": ready_for_functional_canary,
    }
