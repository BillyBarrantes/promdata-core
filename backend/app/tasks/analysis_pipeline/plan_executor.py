"""Plan execution loop — extracted from orchestrator.py."""

from typing import Any

import pandas as pd

from app.core.structured_logging import emit_structured_log
from app.services.analysis_traceability import build_traceability_plan_entry
from app.services.chart_factory import ChartFactory
from app.services.ibis_engine import IbisEngine
from app.services.predictive_engine import PredictiveEngine
from app.tasks.analysis_pipeline.chart_generator import build_chart_config
from app.tasks.analysis_pipeline.narrative_generator import generate_chart_narrative
from app.tasks.analysis_pipeline.plan_generator import (
    _recursive_round,
    build_widget_query_contract,
    coerce_chart_rows_to_table_rows,
    coerce_plan_for_forced_heatmap,
)


def execute_plans(
    *,
    plans_result: list,
    parquet_path: str,
    schema_profile: dict,
    main_df: pd.DataFrame,
    actual_prompt: str,
    format_override: dict,
    currency_meta: dict,
    file_id: str,
    task_id: str,
    explicit_visual_requests: list,
    topology_rules: dict,
    institutional_context: str,
    institutional_snippets: list,
    visual_probe_mode: str,
    traceability_plan_entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Execute all plans in the loop: IbisEngine, output routing, chart/narrative generation.
    Mutates traceability_plan_entries in-place. Returns ibis_response.
    Raises if no results produced.
    """
    ibis_response: list[dict[str, Any]] = []

    protected_cols: list[str] = []
    if schema_profile:
        protected_cols = [
            col for col, info in schema_profile.items()
            if info.get('role') in ['dimension', 'identifier'] or info.get('type') in ['categorical', 'id']
        ]

    for plan_idx, plan in enumerate(plans_result):
        plan = coerce_plan_for_forced_heatmap(
            plan=plan,
            prompt_text=actual_prompt,
            schema_profile=schema_profile,
        )
        plans_result[plan_idx] = plan
        base_query_contract = build_widget_query_contract(plan, schema_profile)
        trace_plan_entry = build_traceability_plan_entry(
            plan=plan,
            schema_profile=schema_profile,
            query_contract=base_query_contract,
        )

        if plan.glossary_hint:
            ibis_response.append({
                "type": "recomendaciones",
                "data": [f"📖 {plan.glossary_hint}"]
            })
            trace_plan_entry["execution"] = {
                "status": "skipped",
                "reason": "glossary_hint",
                "message": plan.glossary_hint,
            }
            traceability_plan_entries.append(trace_plan_entry)
            continue

        ibis_output = IbisEngine.execute_plan(parquet_path, plan, protected_cols=protected_cols)

        if ibis_output and "error" not in ibis_output:
            execution_summary = {
                "status": "success",
                "output_type": ibis_output.get('type'),
                "row_count": len(ibis_output.get('data', [])) if isinstance(ibis_output.get('data'), list) else 0,
                "applied_visual": None,
                "smart_table": False,
            }

            prompt_lower = plan.title.lower() if plan.title else ""
            is_predictive = 'predictive' in str(plan.main_intent.type) or any(
                x in prompt_lower for x in ['proyecci', 'futuro', 'pronostic', 'predicci', 'tendencia futura']
            )

            if is_predictive:
                if schema_profile:
                    date_cols = [c for c, info in schema_profile.items() if info['type'] == 'temporal']
                    num_cols = [c for c, info in schema_profile.items() if info['role'] == 'metric']
                else:
                    date_cols = [c for c in main_df.columns if pd.api.types.is_datetime64_any_dtype(main_df[c])]
                    num_cols = [c for c in main_df.columns if pd.api.types.is_numeric_dtype(main_df[c])]

                if date_cols and num_cols:
                    value_col = num_cols[0]
                    if hasattr(plan.main_intent, 'value_column') and plan.main_intent.value_column in num_cols:
                        value_col = plan.main_intent.value_column
                    elif hasattr(plan.main_intent, 'metrics') and plan.main_intent.metrics:
                        for m in plan.main_intent.metrics:
                            if m in num_cols:
                                value_col = m
                                break

                    aggregation = 'sum'
                    col_topology = topology_rules.get(value_col, '')
                    if 'SNAPSHOT' in col_topology:
                        aggregation = 'last'

                    raw_forecast = PredictiveEngine.forecast_series(
                        main_df,
                        date_cols[0],
                        value_col,
                        aggregation_method=aggregation
                    )
                    if raw_forecast:
                        formatted_forecast = []
                        for item in raw_forecast:
                            formatted_forecast.append({
                                "name": item['date'],
                                "value": item['value'],
                                "extra_info": {
                                    "type": item['type'],
                                    "lower_ci": item.get('lower_ci'),
                                    "upper_ci": item.get('upper_ci')
                                }
                            })
                        ibis_output['data'] = formatted_forecast
                        ibis_output['chart_type'] = 'line_chart'
                        plan.title = f"{plan.title} (Proyección)"

            if 'data' in ibis_output:
                ibis_output['data'] = _recursive_round(ibis_output['data'], 2)

            filtered_granular_df = None

            if ibis_output.get('type') == 'echarts':
                if format_override.get('enabled') and format_override.get('renderer') == 'tabla_datos':
                    table_rows = coerce_chart_rows_to_table_rows(ibis_output.get('data', []), plan)
                    ibis_response.append({
                        "type": "tabla_datos",
                        "title": plan.title,
                        "data": table_rows,
                    })
                    execution_summary["output_type"] = "table"
                    execution_summary["row_count"] = len(table_rows)
                    execution_summary["format_override_renderer"] = format_override.get('renderer')
                    trace_plan_entry["execution"] = execution_summary
                    traceability_plan_entries.append(trace_plan_entry)
                    continue

                filtered_granular_df = ibis_output.pop('filtered_granular_df', None)

                # ── Chart building (extracted) ──
                chart_items, chart_exec = build_chart_config(
                    plan=plan, ibis_output=ibis_output,
                    plan_idx=plan_idx,
                    explicit_visual_requests=explicit_visual_requests,
                    format_override=format_override,
                    currency_meta=currency_meta,
                    actual_prompt=actual_prompt,
                    filtered_granular_df=filtered_granular_df,
                    schema_profile=schema_profile,
                )
                execution_summary.update(chart_exec)

                if chart_exec.get("status") == "blocked":
                    trace_plan_entry["execution"] = execution_summary
                    traceability_plan_entries.append(trace_plan_entry)
                    ibis_response.extend(chart_items)
                    continue

                ibis_response.extend(chart_items)

            elif ibis_output.get('type') == 'table':
                execution_summary["output_type"] = "table"
                ibis_response.append({
                    "type": "tabla_datos",
                    "title": plan.title,
                    "data": ibis_output['data'],
                })

            elif ibis_output.get('type') == 'kpi':
                execution_summary["output_type"] = "kpi"
                metrics_data = ibis_output.get('data', {})
                execution_summary["row_count"] = len(metrics_data) if isinstance(metrics_data, dict) else 0
                ibis_response.append({
                    "type": "metricas_clave",
                    "data": metrics_data,
                })

                if len(metrics_data) == 1:
                    key = list(metrics_data.keys())[0]
                    val = list(metrics_data.values())[0]
                    if isinstance(val, (int, float)):
                        metric_unit = getattr(plan.main_intent, 'metric_unit', None)
                        is_percentage = metric_unit == 'percentage' or (0 <= val <= 100)
                        if is_percentage:
                            gauge_opt = ChartFactory.build_gauge_chart(key, val)
                            ibis_response.append({
                                "type": "configuracion_echarts",
                                "title": key,
                                "option": gauge_opt,
                            })

            # ── Narrative generation (extracted) ──
            narrative_items = generate_chart_narrative(
                plan=plan,
                ibis_output=ibis_output,
                currency_meta=currency_meta,
                institutional_context=institutional_context,
                institutional_snippets=institutional_snippets,
                visual_probe_mode=visual_probe_mode,
                filtered_granular_df=filtered_granular_df,
                schema_profile=schema_profile,
                actual_prompt=actual_prompt,
                file_id=file_id,
                task_id=task_id,
            )
            ibis_response.extend(narrative_items)

            trace_plan_entry["execution"] = execution_summary
            traceability_plan_entries.append(trace_plan_entry)
        else:
            trace_plan_entry["execution"] = {
                "status": "error",
                "output_type": ibis_output.get('type') if isinstance(ibis_output, dict) else None,
                "error": str(ibis_output.get('error'))[:240] if isinstance(ibis_output, dict) else "unknown",
            }
            traceability_plan_entries.append(trace_plan_entry)

    if not ibis_response:
        raise Exception("IbisEngine no devolvió resultados válidos ni gráficos.")

    return ibis_response
