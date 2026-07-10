"""Chart orchestration — extracted from orchestrator.py."""

from typing import Any

from app.core.arrow_utils import (
    evaluate_dataframe_arrow_transport,
    dataframe_to_arrow_base64,
)
from app.core.structured_logging import emit_structured_log
from app.services.chart_factory import ChartFactory
from app.services.smart_table_builder import (
    should_use_smart_table,
    should_offer_hybrid_smart_table,
    echarts_to_smart_table,
)
from app.services.visual_recommendation_engine import build_visual_governance
from app.tasks.analysis_pipeline.plan_generator import (
    coerce_chart_rows_to_table_rows,
    build_widget_query_contract,
    should_force_smart_table_from_prompt,
)


def build_chart_config(
    *,
    plan: Any,
    ibis_output: dict,
    plan_idx: int,
    explicit_visual_requests: list[str],
    format_override: dict,
    currency_meta: dict | None,
    actual_prompt: str,
    filtered_granular_df: Any,
    schema_profile: dict,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Build chart option from ibis_output and apply smart-table logic.
    Returns (items_to_append, execution_summary_updates).
    """
    items: list[dict[str, Any]] = []
    exec_updates: dict[str, Any] = {}

    chart_opt: dict | None = {}
    requested_visual_locked = bool(explicit_visual_requests) and not format_override.get("enabled")
    locked_visual = (
        explicit_visual_requests[min(plan_idx, len(explicit_visual_requests) - 1)]
        if requested_visual_locked
        else None
    )
    requested_chart_type = locked_visual or ibis_output.get('chart_type', 'bar')
    visual_governance = build_visual_governance(
        plan,
        ibis_output,
        requested_chart_type,
        requested_visual_locked=requested_visual_locked,
    )
    c_type = visual_governance.get('applied_visual', requested_chart_type)
    exec_updates["applied_visual"] = c_type
    exec_updates["requested_visual_locked"] = requested_visual_locked

    if visual_governance.get("strict_rejection"):
        blocked_reason = visual_governance.get("blocked_reason") or "El visual solicitado no cumple el contrato técnico del dataset."
        blocked_label = visual_governance.get("requested_label", requested_chart_type)
        suggested_label = visual_governance.get("recommended_label")
        recommendation = (
            f"No pude renderizar {blocked_label} para '{plan.title}'. "
            f"Motivo: {blocked_reason}"
        )
        if suggested_label:
            recommendation += f" Visual sugerido: {suggested_label}."

        exec_updates["status"] = "blocked"
        exec_updates["output_type"] = "visual_request_blocked"
        exec_updates["blocked_reason"] = blocked_reason
        items.append({"type": "recomendaciones", "data": [recommendation]})
        emit_structured_log(
            "visual_request_blocked",
            plan_title=plan.title,
            requested_visual=visual_governance.get("requested_visual"),
            blocked_reason=blocked_reason,
            recommended_visual=visual_governance.get("recommended_visual"),
        )
        return items, exec_updates

    # ── Chart type switch (14 types) ──
    metric_unit = getattr(plan.main_intent, 'metric_unit', None)
    use_currency = currency_meta if metric_unit == 'currency' else None

    if c_type in ['line', 'line_chart']:
        chart_opt = ChartFactory.build_line_chart(
            plan.title, ibis_output['data'],
            currency_meta=use_currency, area=False,
        )
    elif c_type in ['area', 'area_chart']:
        chart_opt = ChartFactory.build_line_chart(
            plan.title, ibis_output['data'],
            currency_meta=use_currency, area=True,
        )
    elif c_type in ['bar', 'bar_chart']:
        chart_opt = ChartFactory.build_bar_chart(
            plan.title, ibis_output['data'],
            currency_meta=use_currency, barmode=ibis_output.get('barmode'),
        )
    elif c_type in ['stacked_bar', 'stacked_bar_chart']:
        chart_opt = ChartFactory.build_bar_chart(
            plan.title, ibis_output['data'],
            currency_meta=use_currency, barmode='stacked',
        )
    elif c_type in ['pie', 'pie_chart']:
        chart_opt = ChartFactory.build_pie_chart(
            plan.title, ibis_output['data'],
            currency_meta=use_currency,
        )
    elif c_type in ['waterfall', 'waterfall_chart']:
        chart_opt = ChartFactory.create_chart('waterfall', plan.title, ibis_output['data'])
    elif c_type in ['funnel', 'funnel_chart']:
        chart_opt = ChartFactory.build_funnel_chart(
            plan.title, ibis_output['data'],
            currency_meta=use_currency,
        )
    elif c_type in ['boxplot', 'boxplot_chart']:
        chart_opt = ChartFactory.build_boxplot(
            plan.title, ibis_output['data'],
            currency_meta=use_currency, outliers=ibis_output.get('outliers'),
        )
    elif c_type in ['scatter', 'scatter_plot', 'scatter_chart']:
        scatter_x = ibis_output.get('x_axis', ibis_output.get('x_label', ''))
        scatter_y = ibis_output.get('y_axis', ibis_output.get('y_label', ''))
        chart_opt = ChartFactory.create_chart('scatter', plan.title, ibis_output['data'],
            x_label=scatter_x, y_label=scatter_y)
    elif c_type in ['bubble', 'bubble_chart']:
        bubble_x = ibis_output.get('x_axis', ibis_output.get('x_label', ''))
        bubble_y = ibis_output.get('y_axis', ibis_output.get('y_label', ''))
        chart_opt = ChartFactory.create_chart(
            'bubble', plan.title, ibis_output['data'],
            x_label=bubble_x, y_label=bubble_y,
        )
    elif c_type in ['treemap', 'treemap_chart']:
        chart_opt = ChartFactory.create_chart('treemap', plan.title, ibis_output['data'])
    elif c_type in ['histogram', 'histogram_chart']:
        chart_opt = ChartFactory.create_chart('histogram', plan.title, ibis_output['data'])
    elif c_type in ['heatmap', 'heatmap_chart']:
        heatmap_x = ibis_output.get('x_axis', ibis_output.get('x_label', ''))
        heatmap_y = ibis_output.get('y_axis', ibis_output.get('y_label', ''))
        chart_opt = ChartFactory.create_chart(
            'heatmap', plan.title, ibis_output['data'],
            x_label=heatmap_x, y_label=heatmap_y,
        )
    elif c_type in ['gauge', 'gauge_chart']:
        chart_opt = ChartFactory.build_gauge_chart(plan.title, ibis_output['data'])
    elif c_type in ['gantt', 'gantt_chart']:
        chart_opt = ChartFactory.build_gantt_chart(plan.title, ibis_output['data'])
    elif c_type in ['dual_axis', 'dual_axis_chart']:
        chart_data = ibis_output['data']
        categories = [d['name'] for d in chart_data]
        bar_data = [d['value'] for d in chart_data]
        line_data = [d.get('extra_info', {}).get('secondary_value', d.get('extra_info', {}).get('growth', d.get('extra_info', {}).get('yoy', 0))) for d in chart_data]
        clean_line = []
        for v in line_data:
            if isinstance(v, str) and '%' in v:
                try:
                    clean_line.append(float(v.replace('%', '')))
                except Exception:
                    clean_line.append(0)
            elif isinstance(v, (int, float)):
                clean_line.append(float(v))
            else:
                clean_line.append(0)
        bar_name = plan.column_aliases.get(getattr(plan.main_intent, 'metric', ''), 'Volumen')
        line_name = 'Variación %'
        chart_opt = ChartFactory.build_dual_axis_chart(
            plan.title, categories, bar_data, clean_line,
            bar_name=bar_name, line_name=line_name,
        )
    elif c_type in ['combo', 'combo_chart']:
        chart_data = ibis_output['data']
        categories = [d['name'] for d in chart_data]
        bar_data = [d['value'] for d in chart_data]
        line_data = [d.get('extra_info', {}).get('secondary_value', d.get('extra_info', {}).get('growth', d.get('extra_info', {}).get('yoy', 0))) for d in chart_data]
        clean_line = []
        for v in line_data:
            if isinstance(v, str) and '%' in v:
                try:
                    clean_line.append(float(v.replace('%', '')))
                except Exception:
                    clean_line.append(0)
            elif isinstance(v, (int, float)):
                clean_line.append(float(v))
            else:
                clean_line.append(0)
        bar_name = plan.column_aliases.get(getattr(plan.main_intent, 'metric', ''), 'Volumen')
        line_name = 'Variación %'
        chart_opt = ChartFactory.build_dual_axis_chart(
            plan.title, categories, bar_data, clean_line,
            bar_name=bar_name, line_name=line_name,
        )
    else:
        if requested_visual_locked:
            chart_opt = None
        else:
            chart_opt = ChartFactory.build_bar_chart(
                plan.title, ibis_output['data'],
                currency_meta=use_currency,
            )

    if isinstance(chart_opt, dict) and chart_opt.get("error"):
        emit_structured_log(
            "chart_factory_invalid_option",
            plan_title=plan.title,
            chart_type=c_type,
            error=str(chart_opt.get("error"))[:240],
        )
        chart_opt = None

    if chart_opt:
        chart_opt['visual_governance'] = visual_governance
        chart_opt['visual_source_payload'] = {
            "title": plan.title,
            "chart_type": c_type,
            "requested_chart_type": requested_chart_type,
            "rows": ibis_output.get('data', []),
            "x_label": ibis_output.get('x_axis', ibis_output.get('x_label')),
            "y_label": ibis_output.get('y_axis', ibis_output.get('y_label')),
            "barmode": ibis_output.get('barmode'),
            "metric_unit": metric_unit,
        }

        if visual_governance.get("override_applied"):
            emit_structured_log(
                "visual_governance_override_applied",
                plan_title=plan.title,
                requested_visual=visual_governance.get("requested_visual"),
                recommended_visual=visual_governance.get("recommended_visual"),
                applied_visual=visual_governance.get("applied_visual"),
                blocked_reason=visual_governance.get("blocked_reason"),
            )
        elif visual_governance.get("requested_visual") != visual_governance.get("recommended_visual"):
            emit_structured_log(
                "visual_governance_advisory_emitted",
                plan_title=plan.title,
                requested_visual=visual_governance.get("requested_visual"),
                recommended_visual=visual_governance.get("recommended_visual"),
                applied_visual=visual_governance.get("applied_visual"),
            )

        widget_query_contract = build_widget_query_contract(plan, schema_profile)
        if widget_query_contract:
            chart_opt['query_contract'] = widget_query_contract

        if filtered_granular_df is not None and not filtered_granular_df.empty:
            try:
                granular_arrow_decision = evaluate_dataframe_arrow_transport(filtered_granular_df)
                emit_structured_log(
                    "arrow_transport_decision",
                    payload_kind="chart_granular",
                    mode="arrow",
                    forced=True,
                    reason=granular_arrow_decision["reason"],
                    rows=len(filtered_granular_df),
                    cols=granular_arrow_decision["column_count"],
                    estimated_bytes=granular_arrow_decision["estimated_bytes"],
                )
                chart_opt['granular_arrow'] = dataframe_to_arrow_base64(filtered_granular_df)
            except Exception:
                pass

        force_smart_table = should_force_smart_table_from_prompt(actual_prompt)
        hybrid_smart_table = should_offer_hybrid_smart_table(chart_opt)
        use_smart_table = False if requested_visual_locked and c_type != "smart_table" else (
            force_smart_table
            or should_use_smart_table(chart_opt)
            or hybrid_smart_table
        )

        if use_smart_table:
            exec_updates["smart_table"] = True
            default_view_mode = 'chart' if hybrid_smart_table and not force_smart_table else 'table'
            smart_payload = echarts_to_smart_table(
                chart_opt,
                plan.title,
                default_view_mode=default_view_mode,
            )
            if smart_payload.get('row_count', 0) > 0:
                exec_updates["output_type"] = "smart_table"
                exec_updates["row_count"] = int(smart_payload.get('row_count') or 0)
                if 'granular_arrow' in chart_opt:
                    smart_payload['granular_arrow'] = chart_opt['granular_arrow']
                if widget_query_contract:
                    smart_payload['query_contract'] = widget_query_contract
                items.append(smart_payload)
            else:
                exec_updates["smart_table"] = False
                if 'recipe_sql' in ibis_output:
                    chart_opt['recipe_sql'] = ibis_output['recipe_sql']
                    chart_opt['recipe_visual_protocol'] = ibis_output.get('recipe_visual_protocol')
                items.append({
                    "type": "configuracion_echarts",
                    "title": plan.title,
                    "option": chart_opt,
                })
        else:
            exec_updates["output_type"] = "echarts"
            if 'recipe_sql' in ibis_output:
                chart_opt['recipe_sql'] = ibis_output['recipe_sql']
                chart_opt['recipe_visual_protocol'] = ibis_output.get('recipe_visual_protocol')
            items.append({
                "type": "configuracion_echarts",
                "title": plan.title,
                "option": chart_opt,
            })

    return items, exec_updates


# ── Legacy functions (preserved for backward compatibility) ──


def _process_chart_bridge(results: dict, hard_facts: dict) -> tuple[dict, bool]:
    """Legacy chart bridge — processes chart_data from the plan."""
    ai_generated_charts = False
    if isinstance(results, dict) and results.get('chart_data'):
        if 'injected_charts' not in results:
            results['injected_charts'] = []

        raw_data = results['chart_data']
        charts_to_process = {}

        is_single_chart = 'series' in raw_data or 'xAxis' in raw_data or 'yAxis' in raw_data

        if is_single_chart:
            charts_to_process = {"Análisis Visual": raw_data}
        else:
            charts_to_process = raw_data

        for key, option in charts_to_process.items():
            clean_title = key.replace('_', ' ').replace('chart', '').title().strip()
            if not isinstance(option, dict):
                continue
            results['injected_charts'].append({
                "type": "configuracion_echarts",
                "title": clean_title,
                "option": option
            })
        ai_generated_charts = True

    if not ai_generated_charts and hard_facts and isinstance(results, dict):
        results = _inject_fallback_charts(results, hard_facts, False)

    return results, ai_generated_charts


def _inject_fallback_charts(results: dict, hard_facts: dict, ai_generated_charts: bool) -> dict:
    """Fallback chart injection from hard_facts."""
    if not results.get('injected_charts'):
        if 'injected_charts' not in results:
            results['injected_charts'] = []

        if hard_facts.get('pareto'):
            p = hard_facts['pareto'][0]
            if 'chart_data' in p:
                opt = ChartFactory.build_pareto_chart(f"Pareto Global: {p['dimension']}", p['chart_data'])
                results['injected_charts'].append({
                    "type": "configuracion_echarts",
                    "title": f"Concentración ({p['dimension']})",
                    "option": opt
                })

        if hard_facts.get('tendencias'):
            for t in hard_facts['tendencias']:
                if 'chart_data' in t:
                    opt = ChartFactory.build_line_chart(f"Tendencia Global: {t['metrica']}", t['chart_data'])
                    results['injected_charts'].append({
                        "type": "configuracion_echarts",
                        "title": "Evolución Histórica",
                        "option": opt
                    })

    return results


def _build_chart_from_template(item: dict) -> dict | None:
    """Build a single chart specification from a template (legacy)."""
    if not isinstance(item, dict):
        return None
    return ChartFactory.create_chart(
        item.get('chart_type', 'bar'),
        item.get('title', 'Untitled'),
        item.get('data', []),
    )
