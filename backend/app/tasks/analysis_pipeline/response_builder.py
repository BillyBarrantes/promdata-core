"""Response construction — extracted from orchestrator.py."""

import json
from typing import Any

import pandas as pd

from app.core.arrow_utils import (
    evaluate_records_arrow_transport,
    evaluate_dataframe_arrow_transport,
    records_to_arrow_base64,
    dataframe_to_arrow_base64,
)
from app.core.serializers import CustomEncoder, convert_keys_to_str
from app.core.structured_logging import emit_structured_log
from app.services.analysis_traceability import build_traceability_payload
from app.services.analysis_memory_context import build_result_semantic_context
from app.services.data_engine import DataEngine


def build_final_response_struct(
    *,
    response: Any,
    parquet_path: Any,
    main_df: Any,
    file_id: str,
    task_id: str,
    user_id: Any,
    prompt: str,
    actual_prompt: str,
    parent_task_id: Any,
    memory_router_decision: str,
    format_override: dict,
    schema_profile: dict,
    currency_meta: dict,
    institutional_snippets: list,
    traceability_plan_entries: list,
    plans_result: list,
    status: str,
    final_error_message: str | None,
) -> tuple[str, str, dict]:
    """
    Build the final response struct from the accumulated response items.
    Returns (status, json_output, final_struct).
    """
    json_output: str = ""
    final_struct: dict[str, Any] = {}

    try:
        final_struct = {
            "analysis": "",
            "metrics": {},
            "chart_options": [],
            "data": [],
            "recommendations": [],
            "explainability": [],
        }

        if isinstance(response, list):
            for item in response:
                if item.get('type') == 'mensaje_resumen':
                    final_struct['analysis'] += item.get('content', '') + "\n\n"
                elif item.get('type') == 'metricas_clave':
                    metrics_payload = item.get('data', {})
                    if isinstance(metrics_payload, dict) and metrics_payload:
                        final_struct['metrics'].update(metrics_payload)
                elif item.get('type') == 'configuracion_echarts':
                    chart_opt = item.get('option', {})
                    if chart_opt:
                        final_struct['chart_options'].append(chart_opt)
                elif item.get('type') == 'tabla_datos':
                    raw_data = item.get('data', [])
                    arrow_decision = evaluate_records_arrow_transport(raw_data) if raw_data else None
                    if arrow_decision:
                        emit_structured_log(
                            "arrow_transport_decision",
                            payload_kind="tabla_datos",
                            mode=arrow_decision["mode"],
                            forced=False,
                            reason=arrow_decision["reason"],
                            rows=len(raw_data),
                            cols=arrow_decision["column_count"],
                            estimated_bytes=arrow_decision["estimated_bytes"],
                        )
                    if raw_data and arrow_decision and arrow_decision['use_arrow']:
                        try:
                            final_struct['arrow_data'] = records_to_arrow_base64(raw_data)
                            final_struct['arrow_row_count'] = len(raw_data)
                            final_struct['data'] = []
                        except Exception:
                            final_struct['data'] = raw_data
                    else:
                        final_struct['data'] = raw_data
                elif item.get('type') == 'smart_table':
                    st_data = item.get('data', [])
                    arrow_decision = evaluate_records_arrow_transport(st_data) if st_data else None
                    if arrow_decision:
                        emit_structured_log(
                            "arrow_transport_decision",
                            payload_kind="smart_table",
                            mode=arrow_decision["mode"],
                            forced=False,
                            reason=arrow_decision["reason"],
                            rows=len(st_data),
                            cols=arrow_decision["column_count"],
                            estimated_bytes=arrow_decision["estimated_bytes"],
                        )
                    if st_data and arrow_decision and arrow_decision['use_arrow']:
                        try:
                            item['arrow_data'] = records_to_arrow_base64(st_data)
                        except Exception:
                            pass
                    final_struct['chart_options'].append(item)
                elif item.get('type') == 'recomendaciones':
                    final_struct['recommendations'].extend(item.get('data', []))
                elif item.get('type') == 'explicabilidad_analitica':
                    payload = item.get('data')
                    if isinstance(payload, dict) and payload:
                        final_struct['explainability'].append(payload)
                elif item.get('type') == 'error':
                    final_struct['analysis'] += f"⚠️ ERROR: {item.get('content')}\n"

        if isinstance(response, dict) and 'status' in response:
            final_struct['analysis'] = str(response)

        try:
            if parquet_path:
                snapshot_df = main_df if isinstance(main_df, pd.DataFrame) and not main_df.empty else None
                cached_snapshot_arrow = DataEngine.load_cached_snapshot_arrow(file_id) if hasattr(DataEngine, 'load_cached_snapshot_arrow') else None

                if snapshot_df is None:
                    snapshot_df = pd.read_parquet(parquet_path)

                if not snapshot_df.empty:
                    arrow_decision = evaluate_dataframe_arrow_transport(snapshot_df)
                    emit_structured_log(
                        "arrow_transport_decision",
                        payload_kind="snapshot",
                        mode="arrow",
                        forced=True,
                        reason=arrow_decision["reason"],
                        rows=len(snapshot_df),
                        cols=arrow_decision["column_count"],
                        estimated_bytes=arrow_decision["estimated_bytes"],
                    )
                    if cached_snapshot_arrow:
                        final_struct['snapshot_arrow'] = cached_snapshot_arrow
                    else:
                        final_struct['snapshot_arrow'] = dataframe_to_arrow_base64(snapshot_df)
                        if hasattr(DataEngine, 'persist_cached_snapshot_arrow'):
                            DataEngine.persist_cached_snapshot_arrow(file_id, final_struct['snapshot_arrow'])
                    final_struct['snapshot_row_count'] = len(snapshot_df)
                    final_struct['snapshot_columns'] = list(snapshot_df.columns)
        except Exception:
            pass

        final_struct['traceability'] = build_traceability_payload(
            task_id=task_id,
            file_id=file_id,
            user_id=user_id,
            raw_prompt=prompt,
            actual_prompt=actual_prompt,
            parent_task_id=parent_task_id,
            memory_decision=memory_router_decision,
            format_override=format_override,
            schema_profile=schema_profile,
            currency_meta=currency_meta,
            institutional_snippets=institutional_snippets,
            plan_entries=traceability_plan_entries,
            final_struct=final_struct,
            semantic_context=build_result_semantic_context(
                plans=plans_result,
                schema_profile=schema_profile,
            ),
            status=status,
            error_message=final_error_message,
        )

        json_output = json.dumps(final_struct, cls=CustomEncoder)
    except Exception:
        json_output = json.dumps({"analysis": str(response)}, default=str)

    return status, json_output, final_struct
