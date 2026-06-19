# En: backend/app/tasks/analysis_pipeline/payload_shedder.py
"""Payload shedding functions — extracted from analysis_tasks.py."""

from typing import Any
import json

from app.core.structured_logging import emit_structured_log
from app.core.serializers import CustomEncoder
from app.core.config import settings


def _strip_payload_fields(final_struct: dict[str, Any], fields: tuple[str, ...]) -> list[str]:
    stripped: list[str] = []
    for key in fields:
        if key in final_struct:
            del final_struct[key]
            stripped.append(key)
    if "granular_arrow" in fields:
        for chart_opt in final_struct.get("chart_options", []):
            if isinstance(chart_opt, dict) and "granular_arrow" in chart_opt:
                del chart_opt["granular_arrow"]
                stripped.append("granular_arrow")
    return stripped


def _apply_progressive_soft_shedding(final_struct: dict[str, Any], soft_limit_bytes: int) -> tuple[str, int, list[str]]:
    original_json = json.dumps(final_struct, cls=CustomEncoder)
    original_bytes = len(original_json)
    if not soft_limit_bytes or original_bytes <= soft_limit_bytes:
        return original_json, original_bytes, []

    stripped: list[str] = []
    json_output = original_json
    for fields in (("granular_arrow",), ("arrow_data",), ("snapshot_arrow",)):
        stripped.extend(_strip_payload_fields(final_struct, fields))
        json_output = json.dumps(final_struct, cls=CustomEncoder)
        if len(json_output) <= soft_limit_bytes:
            break
    return json_output, original_bytes, stripped


def save_analysis_with_payload_shedding(sb, task_id: str, runtime_result: Any) -> None:
    soft_limit_bytes = max(int(getattr(settings, "UNIVERSAL_TABULAR_RESULT_SOFT_LIMIT_BYTES", 0) or 0), 0)
    json_output, original_bytes, stripped = _apply_progressive_soft_shedding(
        runtime_result.final_struct if hasattr(runtime_result, 'final_struct') else runtime_result.get('final_struct', {}),
        soft_limit_bytes,
    )
    if stripped:
        emit_structured_log(
            "analysis_result_payload_soft_shedding_applied",
            task_id=task_id,
            soft_limit_bytes=soft_limit_bytes,
            original_bytes=original_bytes,
            resulting_bytes=len(json_output),
            stripped_fields=sorted(set(stripped)),
        )
    try:
        status = runtime_result.status if hasattr(runtime_result, 'status') else runtime_result.get('status', 'completed')
        sb.table('analysis_tasks').update(
            {'status': status, 'results_json': json_output}
        ).eq('id', task_id).execute()
        return
    except Exception as save_error:
        final_struct = runtime_result.final_struct if hasattr(runtime_result, 'final_struct') else runtime_result.get('final_struct', {})
        _stripped = _strip_payload_fields(
            final_struct,
            ("snapshot_arrow", "arrow_data", "granular_arrow"),
        )
        if not _stripped:
            raise
        json_output = json.dumps(final_struct, cls=CustomEncoder)
        status = runtime_result.status if hasattr(runtime_result, 'status') else runtime_result.get('status', 'completed')
        sb.table('analysis_tasks').update(
            {'status': status, 'results_json': json_output}
        ).eq('id', task_id).execute()
