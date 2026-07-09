import json
from typing import Any, Optional, List

from app.core.config import settings

_MULTI_SHEET_INSTRUCTION = """
    📋 [MULTI-HOJA] ANÁLISIS CROSS-SHEET:
    - `primary_frame_id`: la hoja BASE del análisis (la hoja de "partida").
      Ej: "cruza 2022 con 2023" → primary_frame_id: "sheet::2022"
    - `related_frame_ids`: SOLO las hojas que necesitas ADICIONALES a la principal.
      Ej: "cruza 2022 con 2023" → related_frame_ids: ["sheet::2023"]
    - Si solo necesitas la hoja principal, DEJA `related_frame_ids` VACÍO
      y `primary_frame_id` en "primary" (default).
    - Si el prompt menciona años, regiones o nombres de pestañas,
      úsalos para decidir los frame_ids. Usa formato `sheet::{nombre}`.
    - `join_keys`: columnas que el usuario menciona EXPLÍCITAMENTE como llave de cruce.
      Ej: "cruza usando la columna Placa Unidad como llave" → join_keys: ["placa_unidad"]
      Ej: "cruce por DNI" → join_keys: ["dni"]
      Si el contexto muestra "Claves de JOIN detectadas", úsalas SOLO si el prompt
      las menciona o son la única opción razonable.
      DEJA `join_keys` VACÍO si el prompt no menciona una llave de cruce específica.
    - ⚠️ `pre_aggregation` (CRÍTICO PARA DATOS TRANSACCIONALES):
      Si el prompt implica COMPARAR, CALCULAR VARIACIÓN o DIFERENCIA entre hojas/años,
      y sospechas que el dataset es transaccional (múltiples filas por entidad, ej:
      registros diarios por vehículo, ventas diarias por producto), DEBES especificar
      `pre_aggregation` para consolidar los datos ANTES del cruce.
      Ej: "cruza 2022 con 2025 y calcula la variación de Km por vehículo"
      → pre_aggregation: {
          group_by: ["placa_unidad"],
          metrics: ["km_recorridos"],
          aggregation: "sum"
        }
      Esto agrupa 43,800 filas diarias en 120 totales por vehículo antes del JOIN.
      Si el dataset YA tiene una fila por entidad (ej: inventario por almacén),
      DEJA `pre_aggregation` VACÍO.
    - 🔄 COMPARACIÓN ENTRE HOJAS (VARIACIÓN / DIFERENCIA):
      Si el prompt pide "variación", "diferencia", "comparar" o "delta" entre dos
      hojas/años, NO necesitas hacer nada especial con las métricas. El motor de
      ejecución detectará automáticamente las columnas del año destino y calculará:
      columna_destino - columna_origen, mostrando la variación neta por entidad.
      Solo asegúrate de que: (1) primary_frame_id = año BASE,
      (2) related_frame_ids = [año COMPARADO], y (3) el metric/primary_metric
      sea la columna que quieres comparar (ej: 'km_recorridos' o 'gasto_combustible_s').
      NO especifiques la diferencia como una métrica aparte; el motor la calcula.
      ⚠️ IMPORTANTE para comparaciones multi-entidad:
      - NO uses "Top N" como límite a menos que el usuario lo pida explícitamente.
        El motor mostrará automáticamente TODOS los datos en una tabla comparativa.
      - Para distribuciones (Plan 2 y 3), mantén las mismas dimensiones del Plan 1
        para que la comparación sea coherente. Ej: si el Plan 1 compara por placa_unidad,
        el Plan 2 puede distribuir la variación por tipo_unidad.
      - TODOS los gráficos de la Triple Vista deben apuntar a la comparación
        (2021 vs 2025), no a un solo año. Cada plan debe reflejar la esencia
        comparativa del prompt original.
"""
from app.core.semantic_grammar import (
    AnalysisPlan,
    DataFilter,
    DescriptiveIntent,
    DiagnosticIntent,
    DistributionIntent,
    FilterOperator,
    MetricUnit,
    MetricPolarity,
    TimeTrendIntent,
    VisualProtocol,
)
from app.core.structured_logging import emit_structured_log
from app.services.ai_response_cache import build_cache_key, get_cached_json, set_cached_json
from app.services.metric_semantics import infer_metric_unit_from_column_name
from app.services.visual_recommendation_engine import extract_prompt_visual_requests
from app.services.semantic_translator.core import (
    apply_top_n_rollup_mode_to_plans,
    build_default_latest_snapshot_filters,
    contains_explicit_continuity_marker,
    extract_axis_segment,
    extract_primary_dimension_segment,
    extract_top_limit,
    has_meaningful_temporal_axis,
    humanize_column_alias,
    is_top_n_rollup_request,
    looks_broad_analysis_request,
    looks_dimension_analysis_request,
    mentions_generic_visual_request,
    mentions_temporal_language,
    normalize_surface_text,
    pick_best_dimension_column,
    pick_primary_date_column,
    resolve_segment_columns,
    should_default_to_latest_snapshot,
)
from app.services.semantic_translator.router import route_prompt_with_semantic_router
from app.services.semantic_translator.validator import (
    apply_direction_guard_to_distribution_plans,
    detect_prompt_complexity,
    fast_path_unresolved_constraints,
    finalize_plans,
    generate_translator_plans_with_model,
    infer_default_metric_column,
    is_quota_translator_model_error,
    is_recoverable_translator_model_error,
    normalize_router_filters,
    resolve_contract_column,
    select_translator_fallback_model,
)


def select_default_distribution_visual(
    dimension_column: str,
    schema_profile: dict | None = None,
) -> str:
    schema_profile = schema_profile or {}
    cardinality = int(schema_profile.get(dimension_column, {}).get("cardinality") or 0)
    if cardinality and cardinality > 12:
        return "treemap"
    return "bar_chart"


def select_alternate_distribution_visual(
    dimension_column: str,
    primary_visual: str | None,
    schema_profile: dict | None = None,
) -> str:
    schema_profile = schema_profile or {}
    cardinality = int(schema_profile.get(dimension_column, {}).get("cardinality") or 0)
    preferred = ["pie_chart", "bar_chart", "treemap"]
    if cardinality > 6:
        preferred = ["bar_chart", "treemap", "pie_chart"]
    elif cardinality > 12:
        preferred = ["treemap", "bar_chart", "pie_chart"]

    for candidate in preferred:
        if candidate != primary_visual:
            return candidate
    return "bar_chart"


def build_plan_from_router_contract(
    router_decision: dict[str, Any],
    columns: list[str],
    schema_profile: dict | None = None,
    dataset_contract: dict[str, Any] | None = None,
) -> Optional[List[AnalysisPlan]]:
    if not settings.DETERMINISTIC_VISUAL_FASTPATH_ENABLED:
        return None

    schema_profile = schema_profile or {}
    dataset_contract = dataset_contract or {}
    contract = router_decision.get("semantic_contract") or {}
    if not isinstance(contract, dict):
        return None

    intent = str(contract.get("intent") or router_decision.get("detected_intent") or "unknown")
    metric_column = resolve_contract_column(
        contract.get("plot_metric") or contract.get("metric"),
        columns, schema_profile=schema_profile, allowed_roles={"metric"},
    )
    if not metric_column:
        contract_metric_hint = str(contract.get("plot_metric") or contract.get("metric") or "").strip()
        if contract_metric_hint and contract_metric_hint in columns:
            metric_column = contract_metric_hint
    if not metric_column:
        metric_column = infer_default_metric_column(
            str(contract.get("metric") or ""), columns, schema_profile=schema_profile,
        )
    if not metric_column:
        return None

    ranking_metric_column = resolve_contract_column(
        contract.get("ranking_metric"), columns,
        schema_profile=schema_profile, allowed_roles={"metric"},
    )
    ranking_direction = str(contract.get("ranking_direction") or "desc").strip().lower()
    if ranking_direction not in {"desc", "asc"}:
        ranking_direction = "desc"

    positive_filters = normalize_router_filters(
        contract.get("positive_filters"), columns, schema_profile=schema_profile,
    )
    negative_filters = normalize_router_filters(
        contract.get("negative_filters"), columns, schema_profile=schema_profile,
    )

    metric_unit = infer_metric_unit_from_column_name(metric_column)
    metric_label = humanize_column_alias(metric_column)

    if intent == "trend":
        date_column = resolve_contract_column(
            contract.get("time_axis"), columns,
            schema_profile=schema_profile, allowed_roles={"date"},
        )
        if not date_column:
            date_column = pick_primary_date_column(
                columns, schema_profile=schema_profile, dataset_contract=dataset_contract,
            )
        if not date_column:
            return None

        series_mode = str(contract.get("series_mode") or "none")
        top_n = contract.get("top_n")

        if not top_n and series_mode in {"split", "sum"}:
            for pf in positive_filters:
                pf_op = str(
                    getattr(pf.get("operator"), "value", pf.get("operator")) or ""
                ).strip().lower() if isinstance(pf, dict) else ""
                pf_val = pf.get("value") if isinstance(pf, dict) else None
                if pf_op == "in" and isinstance(pf_val, list) and len(pf_val) >= 2:
                    top_n = len(pf_val)
                    print(f"🔄 [SPLIT INFERENCE] top_n inferido de filtro IN: {pf.get('column')} IN {pf_val} → top_n={top_n}")
                    break

        split_dimension: str | None = None
        split_limit: int | None = None
        if top_n and series_mode in {"split", "sum"}:
            split_dimension = resolve_contract_column(
                contract.get("dimension"), columns,
                schema_profile=schema_profile, allowed_roles={"dimension", "identifier"},
            )
            if not split_dimension:
                return None
            split_limit = max(2, min(int(top_n), 15))

        visual_protocol = VisualProtocol.AREA if contract.get("visual_protocol") == "area_chart" else VisualProtocol.LINE
        date_label = humanize_column_alias(date_column)
        column_aliases = {metric_column: metric_label, date_column: date_label}
        if split_dimension:
            column_aliases[split_dimension] = humanize_column_alias(split_dimension)

        return [
            AnalysisPlan(
                main_intent={
                    "type": "trend",
                    "rationale": "Ejecuto el contrato semántico simple emitido por el router.",
                    "filters": positive_filters,
                    "negative_filters": negative_filters,
                    "metric_unit": metric_unit.value if isinstance(metric_unit, MetricUnit) else MetricUnit.NUMBER.value,
                    "visual_protocol": visual_protocol.value,
                    "date_column": date_column,
                    "value_column": metric_column,
                    "plot_metric": metric_column,
                    "ranking_metric": ranking_metric_column,
                    "ranking_direction": ranking_direction,
                    "grain": str(contract.get("grain") or "month"),
                    "fill_missing": True,
                    "split_dimension": split_dimension,
                    "split_limit": split_limit,
                    "top_n_aggregation_mode": series_mode if series_mode in {"split", "sum"} else "split",
                },
                title=f"Evolución de {metric_label} por {date_label}",
                column_aliases=column_aliases,
                metric_polarity=MetricPolarity.NEUTRAL,
            )
        ]

    if intent == "distribution":
        dimension_column = resolve_contract_column(
            contract.get("dimension"), columns,
            schema_profile=schema_profile, allowed_roles={"dimension", "identifier"},
        )
        if not dimension_column:
            return None
        limit = contract.get("top_n")
        if limit is None:
            cardinality = int(schema_profile.get(dimension_column, {}).get("cardinality") or 0)
            limit = cardinality if 0 < cardinality <= 12 else 10
        visual_protocol = {
            "pie_chart": VisualProtocol.PIE,
            "treemap": VisualProtocol.TREEMAP,
            "funnel_chart": VisualProtocol.FUNNEL,
        }.get(str(contract.get("visual_protocol") or ""), VisualProtocol.BAR)

        group_by_columns: list[str] = []
        for group_hint in list(contract.get("group_by") or []):
            resolved_group = resolve_contract_column(
                str(group_hint), columns,
                schema_profile=schema_profile, allowed_roles={"dimension", "identifier", "date"},
            )
            if resolved_group and resolved_group != dimension_column and resolved_group not in group_by_columns:
                group_by_columns.append(resolved_group)
        dimension_label = humanize_column_alias(dimension_column)
        plans = [
            AnalysisPlan(
                main_intent={
                    "type": "distribution",
                    "rationale": "Ejecuto el contrato semántico simple emitido por el router.",
                    "filters": positive_filters,
                    "negative_filters": negative_filters,
                    "metric_unit": metric_unit.value if isinstance(metric_unit, MetricUnit) else MetricUnit.NUMBER.value,
                    "visual_protocol": visual_protocol.value,
                    "dimension": dimension_column,
                    "metric": metric_column,
                    "plot_metric": metric_column,
                    "ranking_metric": ranking_metric_column,
                    "ranking_direction": ranking_direction,
                    "limit": int(limit),
                    "group_by": group_by_columns or None,
                    "barmode": "stacked",
                },
                title=f"{metric_label} por {dimension_label}",
                column_aliases={metric_column: metric_label, dimension_column: dimension_label},
                metric_polarity=MetricPolarity.NEUTRAL,
            )
        ]
        return apply_direction_guard_to_distribution_plans(plans, schema_profile)

    if intent == "descriptive":
        dimension_column = resolve_contract_column(
            contract.get("dimension"), columns,
            schema_profile=schema_profile, allowed_roles={"dimension", "identifier", "date"},
        )
        group_by_columns: list[str] = []
        for group_hint in list(contract.get("group_by") or []):
            resolved_group = resolve_contract_column(
                str(group_hint), columns,
                schema_profile=schema_profile, allowed_roles={"dimension", "identifier", "date"},
            )
            if resolved_group and resolved_group not in group_by_columns:
                group_by_columns.append(resolved_group)
        if not dimension_column and group_by_columns:
            dimension_column = group_by_columns[0]
            group_by_columns = [c for c in group_by_columns if c != dimension_column]

        top_n = contract.get("top_n")
        has_segmented_request = bool(
            dimension_column or group_by_columns or (isinstance(top_n, int) and top_n > 0)
        )
        if has_segmented_request and dimension_column:
            limit = top_n if isinstance(top_n, int) and top_n > 0 else None
            if limit is None:
                cardinality = int(schema_profile.get(dimension_column, {}).get("cardinality") or 0)
                limit = cardinality if 0 < cardinality <= 12 else 10
            visual_protocol = {
                "pie_chart": VisualProtocol.PIE,
                "treemap": VisualProtocol.TREEMAP,
                "funnel_chart": VisualProtocol.FUNNEL,
                "bar_chart": VisualProtocol.BAR,
                "line_chart": VisualProtocol.LINE,
                "area_chart": VisualProtocol.AREA,
            }.get(str(contract.get("visual_protocol") or ""), VisualProtocol.BAR)
            if visual_protocol == VisualProtocol.KPI:
                visual_protocol = VisualProtocol.BAR
            dimension_label = humanize_column_alias(dimension_column)
            return [
                AnalysisPlan(
                    main_intent={
                        "type": "distribution",
                        "rationale": "Ejecuto el contrato semántico simple segmentado emitido por el router.",
                        "filters": positive_filters,
                        "negative_filters": negative_filters,
                        "metric_unit": metric_unit.value if isinstance(metric_unit, MetricUnit) else MetricUnit.NUMBER.value,
                        "visual_protocol": visual_protocol.value,
                        "dimension": dimension_column,
                        "metric": metric_column,
                        "plot_metric": metric_column,
                        "ranking_metric": ranking_metric_column,
                        "ranking_direction": ranking_direction,
                        "limit": int(limit),
                        "group_by": group_by_columns or None,
                        "barmode": "stacked",
                    },
                    title=f"{metric_label} por {dimension_label}",
                    column_aliases={metric_column: metric_label, dimension_column: dimension_label},
                    metric_polarity=MetricPolarity.NEUTRAL,
                )
            ]

        return [
            AnalysisPlan(
                main_intent=DescriptiveIntent(
                    rationale="Ejecuto el contrato semántico simple emitido por el router.",
                    filters=positive_filters,
                    negative_filters=negative_filters,
                    metrics=[metric_column],
                    metric_unit=metric_unit if isinstance(metric_unit, MetricUnit) else MetricUnit.NUMBER,
                    aggregation=str(contract.get("aggregation") or "sum"),
                    visual_protocol=VisualProtocol.KPI,
                ),
                title=f"{metric_label} Total",
                column_aliases={metric_column: metric_label},
                metric_polarity=MetricPolarity.NEUTRAL,
            )
        ]

    return None


def build_dimension_analysis_bundle(
    prompt: str,
    columns: list[str],
    schema_profile: dict | None = None,
    dataset_contract: dict[str, Any] | None = None,
) -> Optional[List[AnalysisPlan]]:
    if not settings.DETERMINISTIC_VISUAL_FASTPATH_ENABLED:
        return None
    if not looks_dimension_analysis_request(prompt):
        return None

    schema_profile = schema_profile or {}
    dataset_contract = dataset_contract or {}
    surface_prompt = normalize_surface_text(prompt)
    default_snapshot_filters = build_default_latest_snapshot_filters(
        surface_prompt, columns,
        dataset_contract=dataset_contract, schema_profile=schema_profile,
    )

    dimension_segment = extract_primary_dimension_segment(surface_prompt)
    dimension_candidates = resolve_segment_columns(
        dimension_segment or surface_prompt, columns,
        schema_profile=schema_profile, allowed_roles={"dimension", "identifier"},
    )
    if not dimension_candidates:
        return None

    primary_dimension = dimension_candidates[0]
    if int(schema_profile.get(primary_dimension, {}).get("cardinality") or 0) <= 1:
        return None

    metric_column = infer_default_metric_column(surface_prompt, columns, schema_profile=schema_profile)
    if not metric_column:
        return None

    metric_unit = infer_metric_unit_from_column_name(metric_column)
    metric_label = humanize_column_alias(metric_column)
    primary_label = humanize_column_alias(primary_dimension)
    primary_cardinality = int(schema_profile.get(primary_dimension, {}).get("cardinality") or 0)
    primary_limit = primary_cardinality if 0 < primary_cardinality <= 12 else 10
    primary_visual = select_default_distribution_visual(primary_dimension, schema_profile=schema_profile)

    aliases = {metric_column: metric_label, primary_dimension: primary_label}
    plans: list[AnalysisPlan] = [
        AnalysisPlan(
            main_intent=DistributionIntent(
                rationale="Priorizo la dimensión solicitada por el usuario como eje principal para ordenar el análisis alrededor de la categoría pedida.",
                filters=default_snapshot_filters,
                dimension=primary_dimension,
                metric=metric_column,
                limit=primary_limit,
                metric_unit=metric_unit if isinstance(metric_unit, MetricUnit) else MetricUnit.NUMBER,
                visual_protocol=VisualProtocol(primary_visual),
            ),
            title=f"Top {primary_limit} {primary_label} por {metric_label}",
            column_aliases=aliases.copy(),
            metric_polarity=MetricPolarity.NEUTRAL,
        )
    ]

    date_column = pick_primary_date_column(
        columns, schema_profile=schema_profile, dataset_contract=dataset_contract,
    )
    if has_meaningful_temporal_axis(date_column, schema_profile=schema_profile):
        date_label = humanize_column_alias(date_column)
        trend_aliases = aliases.copy()
        trend_aliases[date_column] = date_label
        plans.append(
            AnalysisPlan(
                main_intent=TimeTrendIntent(
                    rationale="Completo la vista por dimensión con evolución temporal real para mostrar si el comportamiento cambia entre periodos del dataset.",
                    date_column=date_column,
                    value_column=metric_column,
                    metric_unit=metric_unit if isinstance(metric_unit, MetricUnit) else MetricUnit.NUMBER,
                    visual_protocol=VisualProtocol.LINE,
                ),
                title=f"Evolución de {metric_label} por {date_label}",
                column_aliases=trend_aliases,
                metric_polarity=MetricPolarity.NEUTRAL,
            )
        )

    secondary_dimension = pick_best_dimension_column(
        surface_prompt, columns,
        schema_profile=schema_profile, exclude={primary_dimension},
    )
    if secondary_dimension:
        secondary_visual = select_alternate_distribution_visual(
            secondary_dimension, primary_visual, schema_profile=schema_profile,
        )
        secondary_label = humanize_column_alias(secondary_dimension)
        secondary_cardinality = int(schema_profile.get(secondary_dimension, {}).get("cardinality") or 0)
        secondary_limit = secondary_cardinality if 0 < secondary_cardinality <= 12 else 10
        secondary_aliases = {metric_column: metric_label, secondary_dimension: secondary_label}
        plans.append(
            AnalysisPlan(
                main_intent=DistributionIntent(
                    rationale="Añado una segunda dimensión complementaria para contextualizar la lectura principal sin depender del planner generativo.",
                    filters=default_snapshot_filters,
                    dimension=secondary_dimension,
                    metric=metric_column,
                    limit=secondary_limit,
                    metric_unit=metric_unit if isinstance(metric_unit, MetricUnit) else MetricUnit.NUMBER,
                    visual_protocol=VisualProtocol(secondary_visual),
                ),
                title=f"Distribución de {metric_label} por {secondary_label}",
                column_aliases=secondary_aliases,
                metric_polarity=MetricPolarity.NEUTRAL,
            )
        )

    if len(plans) < 3:
        kpi_title = f"{metric_label} Total"
        if dataset_contract.get("snapshot_guard_allowed"):
            kpi_title += " (Corte Actual)"
        plans.append(
            AnalysisPlan(
                main_intent=DescriptiveIntent(
                    rationale="Completo el bundle con un KPI global para conservar referencia de magnitud cuando faltan ejes suficientes para una tercera vista.",
                    filters=default_snapshot_filters,
                    metrics=[metric_column],
                    metric_unit=metric_unit if isinstance(metric_unit, MetricUnit) else MetricUnit.NUMBER,
                    aggregation="sum",
                    visual_protocol=VisualProtocol.KPI,
                ),
                title=kpi_title,
                column_aliases={metric_column: metric_label},
                metric_polarity=MetricPolarity.NEUTRAL,
            )
        )

    emit_structured_log(
        "semantic_translator_dimension_bundle_fast_path_hit",
        prompt=prompt[:200],
        plan_count=len(plans[:3]),
        metric=metric_column,
        primary_dimension=primary_dimension,
        date_column=date_column,
        dataset_mode=dataset_contract.get("dataset_mode"),
    )
    return plans[:3]


def build_macro_analysis_bundle(
    prompt: str,
    columns: list[str],
    schema_profile: dict | None = None,
    dataset_contract: dict[str, Any] | None = None,
) -> Optional[List[AnalysisPlan]]:
    if not settings.DETERMINISTIC_VISUAL_FASTPATH_ENABLED:
        return None
    if not looks_broad_analysis_request(prompt):
        return None

    schema_profile = schema_profile or {}
    dataset_contract = dataset_contract or {}
    surface_prompt = normalize_surface_text(prompt)
    default_snapshot_filters = build_default_latest_snapshot_filters(
        surface_prompt, columns,
        dataset_contract=dataset_contract, schema_profile=schema_profile,
    )

    metric_column = infer_default_metric_column(surface_prompt, columns, schema_profile=schema_profile)
    if not metric_column:
        return None

    metric_unit = infer_metric_unit_from_column_name(metric_column)
    metric_label = humanize_column_alias(metric_column)
    plans: list[AnalysisPlan] = []
    aliases = {metric_column: metric_label}

    descriptive_title = f"{metric_label} Total"
    if dataset_contract.get("snapshot_guard_allowed"):
        descriptive_title += " (Corte Actual)"
    plans.append(
        AnalysisPlan(
            main_intent=DescriptiveIntent(
                rationale="Priorizo un KPI global para abrir el análisis con la magnitud base más representativa del dataset.",
                filters=default_snapshot_filters,
                metrics=[metric_column],
                metric_unit=metric_unit if isinstance(metric_unit, MetricUnit) else MetricUnit.NUMBER,
                aggregation="sum",
                visual_protocol=VisualProtocol.KPI,
            ),
            title=descriptive_title,
            column_aliases=aliases.copy(),
            metric_polarity=MetricPolarity.NEUTRAL,
        )
    )

    date_column = pick_primary_date_column(
        columns, schema_profile=schema_profile, dataset_contract=dataset_contract,
    )
    if has_meaningful_temporal_axis(date_column, schema_profile=schema_profile):
        date_label = humanize_column_alias(date_column)
        trend_aliases = aliases.copy()
        trend_aliases[date_column] = date_label
        plans.append(
            AnalysisPlan(
                main_intent=TimeTrendIntent(
                    rationale="Agrego una lectura temporal para revelar tendencia y cambio cuando el dataset ofrece un eje cronológico real.",
                    date_column=date_column,
                    value_column=metric_column,
                    metric_unit=metric_unit if isinstance(metric_unit, MetricUnit) else MetricUnit.NUMBER,
                    visual_protocol=VisualProtocol.LINE,
                ),
                title=f"Evolución de {metric_label} por {date_label}",
                column_aliases=trend_aliases,
                metric_polarity=MetricPolarity.NEUTRAL,
            )
        )

    primary_dimension = pick_best_dimension_column(
        surface_prompt, columns, schema_profile=schema_profile,
    )
    if primary_dimension:
        primary_visual = select_default_distribution_visual(primary_dimension, schema_profile=schema_profile)
        dimension_label = humanize_column_alias(primary_dimension)
        dimension_cardinality = int(schema_profile.get(primary_dimension, {}).get("cardinality") or 0)
        limit = dimension_cardinality if 0 < dimension_cardinality <= 12 else 10
        dist_aliases = aliases.copy()
        dist_aliases[primary_dimension] = dimension_label
        plans.append(
            AnalysisPlan(
                main_intent=DistributionIntent(
                    rationale="Incluyo una vista de concentración para identificar qué categorías explican el peso operativo dominante.",
                    filters=default_snapshot_filters,
                    dimension=primary_dimension,
                    metric=metric_column,
                    limit=limit,
                    metric_unit=metric_unit if isinstance(metric_unit, MetricUnit) else MetricUnit.NUMBER,
                    visual_protocol=VisualProtocol(primary_visual),
                ),
                title=f"{metric_label} por {dimension_label}",
                column_aliases=dist_aliases,
                metric_polarity=MetricPolarity.NEUTRAL,
            )
        )
    else:
        primary_visual = None

    if len(plans) < 3:
        secondary_dimension = pick_best_dimension_column(
            surface_prompt, columns,
            schema_profile=schema_profile,
            exclude={primary_dimension} if primary_dimension else set(),
        )
        if secondary_dimension:
            secondary_visual = select_alternate_distribution_visual(
                secondary_dimension, primary_visual, schema_profile=schema_profile,
            )
            secondary_label = humanize_column_alias(secondary_dimension)
            secondary_cardinality = int(schema_profile.get(secondary_dimension, {}).get("cardinality") or 0)
            secondary_limit = secondary_cardinality if 0 < secondary_cardinality <= 12 else 10
            secondary_aliases = aliases.copy()
            secondary_aliases[secondary_dimension] = secondary_label
            plans.append(
                AnalysisPlan(
                    main_intent=DistributionIntent(
                        rationale="Completo el paquete con una segunda vista categórica para aportar otra dimensión explicativa sin depender del planner generativo.",
                        filters=default_snapshot_filters,
                        dimension=secondary_dimension,
                        metric=metric_column,
                        limit=secondary_limit,
                        metric_unit=metric_unit if isinstance(metric_unit, MetricUnit) else MetricUnit.NUMBER,
                        visual_protocol=VisualProtocol(secondary_visual),
                    ),
                    title=f"Top {secondary_limit} {secondary_label} por {metric_label}",
                    column_aliases=secondary_aliases,
                    metric_polarity=MetricPolarity.NEUTRAL,
                )
            )

    if not plans:
        return None

    emit_structured_log(
        "semantic_translator_macro_fast_path_hit",
        prompt=prompt[:200],
        plan_count=len(plans),
        metric=metric_column,
        date_column=date_column,
        primary_dimension=primary_dimension,
        dataset_mode=dataset_contract.get("dataset_mode"),
    )
    return plans[:3]


def build_explicit_scatter_plan(
    prompt: str,
    columns: list[str],
    schema_profile: dict | None = None,
) -> Optional[List[AnalysisPlan]]:
    if not settings.DETERMINISTIC_VISUAL_FASTPATH_ENABLED:
        return None

    requested_visuals = extract_prompt_visual_requests(prompt)
    if "scatter_plot" not in requested_visuals:
        return None

    surface_prompt = normalize_surface_text(prompt)
    if " x " not in f" {surface_prompt} " or " y " not in f" {surface_prompt} ":
        return None

    schema_profile = schema_profile or {}
    x_segment = extract_axis_segment(surface_prompt, "x")
    y_segment = extract_axis_segment(surface_prompt, "y")
    color_segment = extract_axis_segment(surface_prompt, "color")

    if not x_segment or not y_segment:
        return None

    x_date_candidates = resolve_segment_columns(
        x_segment, columns, schema_profile=schema_profile, allowed_roles={"date"},
    )
    x_metric_candidates = resolve_segment_columns(
        x_segment, columns, schema_profile=schema_profile, allowed_roles={"metric"},
    )
    y_metric_candidates = resolve_segment_columns(
        y_segment, columns, schema_profile=schema_profile, allowed_roles={"metric"},
    )
    color_candidates = resolve_segment_columns(
        color_segment, columns, schema_profile=schema_profile, allowed_roles={"dimension", "identifier"},
    )

    if not y_metric_candidates:
        return None

    y_metric = y_metric_candidates[0]
    scatter_metrics: list[str] = []
    if len(x_date_candidates) >= 2:
        scatter_metrics.extend(x_date_candidates[:2])
    elif x_metric_candidates:
        scatter_metrics.append(x_metric_candidates[0])
    elif x_date_candidates:
        scatter_metrics.append(x_date_candidates[0])

    if not scatter_metrics:
        return None

    if y_metric not in scatter_metrics:
        scatter_metrics.append(y_metric)

    dimension_col = color_candidates[0] if color_candidates else None
    metric_unit = infer_metric_unit_from_column_name(y_metric)

    title = f"Dispersión de {humanize_column_alias(y_metric)}"
    if len(x_date_candidates) >= 2:
        title += " vs. Días al Vencimiento"
    else:
        title += f" vs. {humanize_column_alias(scatter_metrics[0])}"
    if dimension_col:
        title += f" por {humanize_column_alias(dimension_col)}"

    aliases = {
        column_name: humanize_column_alias(column_name)
        for column_name in [*scatter_metrics, dimension_col]
        if column_name
    }
    plan = AnalysisPlan(
        main_intent=DiagnosticIntent(
            rationale="Priorizo una vista relacional explícita para medir dispersión y contraste entre la métrica operativa y la variable pedida por el usuario.",
            metric=y_metric,
            metrics=scatter_metrics,
            dimension=dimension_col,
            metric_unit=metric_unit if isinstance(metric_unit, MetricUnit) else MetricUnit.NUMBER,
            visual_protocol=VisualProtocol.SCATTER,
        ),
        title=title,
        column_aliases=aliases,
        metric_polarity=MetricPolarity.NEUTRAL,
    )
    emit_structured_log(
        "semantic_translator_fast_path_hit",
        prompt=prompt[:200], visual="scatter_plot",
        metrics=scatter_metrics, dimension=dimension_col,
    )
    return [plan]


def build_explicit_trend_plan(
    prompt: str,
    columns: list[str],
    schema_profile: dict | None = None,
    allow_non_visual_prompt: bool = False,
) -> Optional[List[AnalysisPlan]]:
    import re

    if not settings.DETERMINISTIC_VISUAL_FASTPATH_ENABLED:
        return None

    requested_visuals = extract_prompt_visual_requests(prompt)
    surface_prompt = normalize_surface_text(prompt)
    generic_visual_request = mentions_generic_visual_request(surface_prompt)
    if not requested_visuals and not generic_visual_request and not allow_non_visual_prompt:
        return None

    requested_visual = requested_visuals[0] if requested_visuals else "line_chart"
    if requested_visual not in {"line_chart", "area_chart"}:
        return None
    if not requested_visuals and not mentions_temporal_language(surface_prompt):
        return None

    schema_profile = schema_profile or {}
    x_segment = extract_axis_segment(surface_prompt, "x")
    y_segment = extract_axis_segment(surface_prompt, "y")

    date_candidates = resolve_segment_columns(
        x_segment or surface_prompt, columns,
        schema_profile=schema_profile, allowed_roles={"date"},
    )
    metric_candidates = resolve_segment_columns(
        y_segment or surface_prompt, columns,
        schema_profile=schema_profile, allowed_roles={"metric"},
    )

    if not date_candidates or not metric_candidates:
        de_por_match = re.search(r"\bde\s+(.+?)\s+por\s+(.+?)(?=$|,)", surface_prompt, flags=re.IGNORECASE)
        if de_por_match:
            if not metric_candidates:
                metric_candidates = resolve_segment_columns(
                    de_por_match.group(1), columns,
                    schema_profile=schema_profile, allowed_roles={"metric"},
                )
            if not date_candidates:
                date_candidates = resolve_segment_columns(
                    de_por_match.group(2), columns,
                    schema_profile=schema_profile, allowed_roles={"date"},
                )

    if not date_candidates and mentions_temporal_language(surface_prompt):
        fallback_date_column = pick_primary_date_column(columns, schema_profile=schema_profile, dataset_contract={})
        if fallback_date_column:
            date_candidates = [fallback_date_column]

    if not metric_candidates:
        default_metric = infer_default_metric_column(surface_prompt, columns, schema_profile=schema_profile)
        if default_metric:
            metric_candidates = [default_metric]

    if not date_candidates or not metric_candidates:
        return None

    date_column = date_candidates[0]
    metric_column = metric_candidates[0]
    explicit_top_limit = extract_top_limit(surface_prompt)
    split_dimension: str | None = None
    split_limit: int | None = None
    top_n_aggregation_mode = "split"

    if explicit_top_limit is not None:
        split_segment = extract_primary_dimension_segment(surface_prompt)
        top_segment_match = re.search(
            r"\btop\s+\d{1,3}\s+(.+?)(?=$|,|\s+con\s+|\s+de\s+|\s+en\s+|\s+para\s+)",
            surface_prompt, flags=re.IGNORECASE,
        )
        if top_segment_match:
            top_segment = top_segment_match.group(1).strip(" .,:;")
            if not split_segment or split_segment in {"fecha", "date", "periodo", "periodos", "tiempo"}:
                split_segment = top_segment
        split_segment = split_segment or surface_prompt
        split_candidates = resolve_segment_columns(
            split_segment, columns,
            schema_profile=schema_profile, allowed_roles={"dimension", "identifier"},
        )
        for candidate in split_candidates:
            if candidate not in {date_column, metric_column}:
                split_dimension = candidate
                break
        if not split_dimension:
            fallback_split_dimension = pick_best_dimension_column(
                surface_prompt, columns,
                schema_profile=schema_profile, exclude={date_column, metric_column},
            )
            if fallback_split_dimension:
                split_dimension = fallback_split_dimension
        if split_dimension:
            split_limit = max(2, min(int(explicit_top_limit), 15))
            if is_top_n_rollup_request(surface_prompt):
                top_n_aggregation_mode = "sum"

    metric_unit = infer_metric_unit_from_column_name(metric_column)
    visual_protocol = VisualProtocol.LINE if requested_visual == "line_chart" else VisualProtocol.AREA

    metric_label = humanize_column_alias(metric_column)
    date_label = humanize_column_alias(date_column)
    if split_dimension and split_limit:
        split_label = humanize_column_alias(split_dimension)
        if top_n_aggregation_mode == "sum":
            title = f"Evolución de {metric_label} (Suma Top {split_limit} {split_label}) por {date_label}"
        else:
            title = f"Evolución de {metric_label} por {split_label} (Top {split_limit})"
    else:
        title = f"Evolución de {metric_label} por {date_label}"

    plan = AnalysisPlan(
        main_intent={
            "type": "trend",
            "rationale": "Priorizo una lectura temporal explícita para seguir la evolución de la métrica sobre el eje de tiempo pedido por el usuario.",
            "filters": [],
            "metric_unit": metric_unit.value if isinstance(metric_unit, MetricUnit) else MetricUnit.NUMBER.value,
            "visual_protocol": visual_protocol.value,
            "date_column": date_column,
            "value_column": metric_column,
            "grain": "month",
            "fill_missing": True,
            "split_dimension": split_dimension,
            "split_limit": split_limit,
            "top_n_aggregation_mode": top_n_aggregation_mode,
        },
        title=title,
        column_aliases={
            metric_column: metric_label,
            date_column: date_label,
            **({split_dimension: humanize_column_alias(split_dimension)} if split_dimension else {}),
        },
        metric_polarity=MetricPolarity.NEUTRAL,
    )
    emit_structured_log(
        "semantic_translator_fast_path_hit",
        prompt=prompt[:200], visual=requested_visual,
        date_column=date_column, metric=metric_column,
        split_dimension=split_dimension, split_limit=split_limit,
        top_n_aggregation_mode=top_n_aggregation_mode,
    )
    return [plan]


def build_explicit_distribution_plan(
    prompt: str,
    columns: list[str],
    schema_profile: dict | None = None,
    dataset_contract: dict[str, Any] | None = None,
) -> Optional[List[AnalysisPlan]]:
    import re

    if not settings.DETERMINISTIC_VISUAL_FASTPATH_ENABLED:
        return None

    requested_visuals = extract_prompt_visual_requests(prompt)
    surface_prompt = normalize_surface_text(prompt)
    generic_visual_request = mentions_generic_visual_request(surface_prompt)
    if not requested_visuals and not generic_visual_request:
        return None

    requested_visual = requested_visuals[0] if requested_visuals else None
    if requested_visual and requested_visual not in {"bar_chart", "pie_chart", "treemap", "funnel_chart"}:
        return None

    schema_profile = schema_profile or {}
    dataset_contract = dataset_contract or {}
    explicit_top_limit = extract_top_limit(surface_prompt)
    top_requested = explicit_top_limit is not None
    default_snapshot_filters = build_default_latest_snapshot_filters(
        surface_prompt, columns,
        dataset_contract=dataset_contract, schema_profile=schema_profile,
    )

    dimension_segment = None
    metric_segment = None

    top_match = re.search(r"\btop\s+\d{1,3}\s+(.+?)\s+por\s+(.+?)(?=$|,)", surface_prompt, flags=re.IGNORECASE)
    if top_match:
        dimension_segment = top_match.group(1)
        metric_segment = top_match.group(2)

    if not dimension_segment or not metric_segment:
        de_por_match = re.search(r"\bde\s+(.+?)\s+por\s+(.+?)(?=$|,)", surface_prompt, flags=re.IGNORECASE)
        if de_por_match:
            metric_segment = metric_segment or de_por_match.group(1)
            dimension_segment = dimension_segment or de_por_match.group(2)

    if not dimension_segment:
        por_match = re.search(r"\bpor\s+(.+?)(?=$|,)", surface_prompt, flags=re.IGNORECASE)
        if por_match:
            dimension_segment = por_match.group(1)

    dimension_candidates = resolve_segment_columns(
        dimension_segment or surface_prompt, columns,
        schema_profile=schema_profile, allowed_roles={"dimension", "identifier"},
    )
    metric_candidates = resolve_segment_columns(
        metric_segment or surface_prompt, columns,
        schema_profile=schema_profile, allowed_roles={"metric"},
    )

    if not metric_candidates:
        default_metric = infer_default_metric_column(surface_prompt, columns, schema_profile=schema_profile)
        if default_metric:
            metric_candidates = [default_metric]

    if not dimension_candidates or not metric_candidates:
        return None

    dimension_column = dimension_candidates[0]
    metric_column = metric_candidates[0]
    cardinality = int(schema_profile.get(dimension_column, {}).get("cardinality") or 0)
    limit = explicit_top_limit
    if limit is None:
        limit = cardinality if cardinality and cardinality <= 12 else 10

    selected_visual = requested_visual or select_default_distribution_visual(
        dimension_column, schema_profile=schema_profile,
    )
    metric_unit = infer_metric_unit_from_column_name(metric_column)
    visual_protocol = {
        "bar_chart": VisualProtocol.BAR,
        "pie_chart": VisualProtocol.PIE,
        "treemap": VisualProtocol.TREEMAP,
        "funnel_chart": VisualProtocol.FUNNEL,
    }[selected_visual]

    if top_requested:
        title = f"Top {limit} {humanize_column_alias(dimension_column)} por {humanize_column_alias(metric_column)}"
    else:
        title = f"{humanize_column_alias(metric_column)} por {humanize_column_alias(dimension_column)}"

    plan = AnalysisPlan(
        main_intent={
            "type": "distribution",
            "rationale": "Priorizo una vista de concentración explícita para ordenar las categorías según la métrica solicitada y exponer el ranking dominante.",
            "filters": [row.model_dump(mode="json") for row in default_snapshot_filters],
            "metric_unit": metric_unit.value if isinstance(metric_unit, MetricUnit) else MetricUnit.NUMBER.value,
            "visual_protocol": visual_protocol.value,
            "dimension": dimension_column,
            "metric": metric_column,
            "limit": limit,
            "group_by": None,
            "barmode": "stacked",
        },
        title=title,
        column_aliases={
            metric_column: humanize_column_alias(metric_column),
            dimension_column: humanize_column_alias(dimension_column),
        },
        metric_polarity=MetricPolarity.NEUTRAL,
    )
    emit_structured_log(
        "semantic_translator_fast_path_hit",
        prompt=prompt[:200], visual=selected_visual,
        metric=metric_column, dimension=dimension_column, limit=limit,
    )
    plans = [plan]
    return apply_direction_guard_to_distribution_plans(plans, schema_profile)


def build_deterministic_visual_plan(
    prompt: str,
    columns: list[str],
    schema_profile: dict | None = None,
    dataset_contract: dict[str, Any] | None = None,
    allow_non_visual_prompt: bool = False,
) -> Optional[List[AnalysisPlan]]:
    builders = (
        lambda p, c, s, d: build_explicit_scatter_plan(p, c, s),
        lambda p, c, s, d: build_explicit_trend_plan(
            p, c, s, allow_non_visual_prompt=allow_non_visual_prompt,
        ),
        lambda p, c, s, d: build_explicit_distribution_plan(p, c, s, d),
    )
    for builder in builders:
        plans = builder(prompt, columns, schema_profile, dataset_contract)
        if plans:
            return plans
    return None


def translate(
    prompt: str,
    columns: list,
    glossary_context: str,
    topology_context: str,
    memory_context: str = "",
    memory_instruction: str = "",
    format_instruction: str = "",
    schema_profile: dict | None = None,
    dataset_contract: dict[str, Any] | None = None,
    related_frames_context: str = "",
) -> Optional[List[AnalysisPlan]]:
    router_decision = route_prompt_with_semantic_router(
        prompt, list(columns or []),
        schema_profile=schema_profile, dataset_contract=dataset_contract,
    )
    use_simple_runtime = router_decision.get("route") == "SIMPLE"
    force_deep_planner = False

    if use_simple_runtime:
        fast_path_plans = build_plan_from_router_contract(
            router_decision, list(columns or []),
            schema_profile=schema_profile, dataset_contract=dataset_contract,
        )
        if fast_path_plans:
            emit_structured_log(
                "semantic_translator_simple_contract_accepted",
                prompt=prompt[:200], confidence=router_decision.get("confidence"),
                detected_intent=router_decision.get("detected_intent"),
                semantic_contract=router_decision.get("semantic_contract"),
                plan_count=len(fast_path_plans),
            )
            return finalize_plans(fast_path_plans, schema_profile)
        force_deep_planner = True
        emit_structured_log(
            "semantic_translator_simple_contract_delegated",
            prompt=prompt[:200], router_decision=router_decision,
        )
    else:
        emit_structured_log(
            "semantic_translator_complex_route_selected",
            prompt=prompt[:200], confidence=router_decision.get("confidence"),
            detected_intent=router_decision.get("detected_intent"),
            reason_codes=router_decision.get("reason_codes"),
        )
        force_deep_planner = True

    if not force_deep_planner:
        dimension_bundle_plans = build_dimension_analysis_bundle(
            prompt, list(columns or []),
            schema_profile=schema_profile, dataset_contract=dataset_contract,
        )
        if dimension_bundle_plans:
            return finalize_plans(dimension_bundle_plans, schema_profile)

        macro_bundle_plans = build_macro_analysis_bundle(
            prompt, list(columns or []),
            schema_profile=schema_profile, dataset_contract=dataset_contract,
        )
        if macro_bundle_plans:
            return finalize_plans(macro_bundle_plans, schema_profile)

    translator_cache_key = build_cache_key(
        "semantic_translator", {
            "prompt": prompt,
            "columns": list(columns or []),
            "glossary_context": glossary_context,
            "topology_context": topology_context,
            "memory_context": memory_context,
            "memory_instruction": memory_instruction,
            "format_instruction": format_instruction,
            "related_frames_context": related_frames_context,
            "semantic_router_decision": router_decision,
            "translator_contract_version": "semantic_router_v2",
        },
    )
    cached_plans = get_cached_json("semantic_translator", translator_cache_key)
    if isinstance(cached_plans, list) and cached_plans:
        try:
            restored_plans = [AnalysisPlan.model_validate(item) for item in cached_plans]
            _tx_file_id = (
                str((dataset_contract or {}).get("file_id") or "").strip()
                if isinstance(dataset_contract, dict) else ""
            )
            _tx_metrics = []
            for _p in restored_plans:
                _m = getattr(_p, "metric", None)
                if _m:
                    _tx_metrics.append(str(_m))
            emit_structured_log(
                "semantic_translator_cache_hit",
                prompt=prompt[:200], plan_count=len(restored_plans),
                file_id=_tx_file_id or None, plan_metrics=_tx_metrics[:5],
                cache_key_prefix=translator_cache_key[:16],
            )
            return finalize_plans(restored_plans, schema_profile)
        except Exception as cache_restore_error:
            emit_structured_log(
                "semantic_translator_cache_restore_error",
                level="warning", error=str(cache_restore_error)[:200],
            )

    schema_json = json.dumps(AnalysisPlan.model_json_schema(), indent=2)
    router_context_json = json.dumps(router_decision, ensure_ascii=False, sort_keys=True)
    primary_model_name = str(settings.AI_MODEL_NAME or "").strip()

    system_instruction = f"""
    ERES EL ESTRATEGA DE DATOS SENIOR DE PROMDATA (BIG DATA ARCHITECT).

    TUS HERRAMIENTAS:
    - COLUMNAS DISPONIBLES: {columns}
    - CONTEXTO GLOSARIO: {glossary_context}
    - TOPOLOGÍA (Tipos de Datos): {topology_context}
    - SEMANTIC_ROUTER_DECISION (CONTRATO DE ALTA PRIORIDAD): {router_context_json}
    {related_frames_context if related_frames_context else ''}
    {_MULTI_SHEET_INSTRUCTION if related_frames_context else ''}
    --- 🔒 CONTRATO DEL ROUTER SEMÁNTICO ---
    - Debes respetar `detected_intent`, `reason_codes` y `semantic_contract` como señales superiores al texto suelto.
    - Si `reason_codes` contiene `multi_series`, `per_item` o `top_n_filter` con intención trend,
      conserva series separadas usando `top_n_aggregation_mode="split"`.
    - Si `semantic_contract.series_mode="sum"`, consolida en una sola serie.
    - Si `semantic_contract.series_mode="split"`, NO consolides aunque el prompt contenga palabras como "total" o "totales".
    - Si `reason_codes` contiene `exclusion_logic`, transforma cada exclusión en `negative_filters`.
    - Si `reason_codes` contiene `ranking_metric_mismatch`, separa SIEMPRE `plot_metric` de `ranking_metric`.
    - `plot_metric` es la métrica que se muestra; `ranking_metric` es la métrica para elegir/ordenar Top N.
    - No uses regex ni inferencias léxicas para filtros: todo filtro debe salir como `filters` o `negative_filters`.

    --- 🧠 TUS 5 INTENCIONES DISPONIBLES (ELIGE LA CORRECTA) ---

    A. "descriptive" → KPIs, agregaciones, comparaciones estructurales.
       - Usa cuando: "total de X", "promedio de Y", "desglose por Z"

    B. "trend" → Evolución temporal, crecimiento, estacionalidad.
       - Usa cuando: "evolución de X", "tendencia mensual", "histórico"
       - Si el usuario pide Top N sobre una serie temporal, usa `split_dimension` y `split_limit`.
       - Si además pide consolidar/sumar el Top N o niega series individuales ("no me des cada producto"),
         usa `top_n_aggregation_mode="sum"` para generar UNA sola serie temporal agregada del Top N.
       - Si pide comparar cada elemento del Top N, usa `top_n_aggregation_mode="split"`.

    C. "distribution" → Top N, Pareto, frecuencia, concentración.
       - Usa cuando: "top 10", "distribución de X", "ranking"
       - No conviertas un Top N temporal en ranking estático si el usuario menciona mes, fecha,
         evolución, tendencia, histórico o "por cada periodo"; en ese caso la intención es "trend".

    D. "diagnostic" → Variabilidad, correlación, outliers, embudo.
       - Usa cuando: "¿por qué?", "variabilidad", "correlación entre X e Y", "outliers"
       - visual_protocol: 'boxplot' para variabilidad, 'scatter_plot' para correlación, 'funnel_chart' para conversión

    E. "predictive" → Forecast, anomalías, proyecciones.
       - Usa cuando: "proyección", "pronóstico", "forecast", "predecir", "anomalías", "¿qué pasará?"

    --- 🚀 MISIONES CRÍTICAS (ÚSALAS SIEMPRE) ---

    1. 🧠 INFERENCIA SEMÁNTICA "ON-THE-FLY" (Humanizador):
       - Tu DEBER es llenar el diccionario `column_aliases`.
       - Analiza el idioma del USUARIO (Español/Inglés) y traduce los nombres técnicos.
       - Ejemplo: Si la columna es 'totalRevenue' y el usuario habla español -> 'Ingresos Totales'.
       - REGLA: En los campos 'title' y 'rationale', USA SOLO LOS ALIAS HUMANOS.
       - ECONOMÍA ESTRICTA PARA `rationale`: máximo 2 líneas o 35 palabras.
       - `rationale` debe explicar SOLO el porqué del enfoque analítico.
       - PROHIBIDO repetir métricas, filtros, cifras concretas o listas de columnas en `rationale`.
       - Usa lenguaje ejecutivo, directo y breve. Si necesitas formato, usa viñetas cortas.

    2. 👁️ MATRIZ DE PROTOCOLOS VISUALES (Elige el Gráfico Perfecto):
       - REGLA SUPREMA: SI EL USUARIO PIDE UN TIPO DE GRÁFICO (ej: "Quiero Torta"), OBEDECE.
       - Si no pide nada, usa la NATURALEZA MATEMÁTICA:
       * TIEMPO + MÉTRICA → 'line_chart' (o 'area_chart' si acumulado).
       * DENSIDAD o COMPOSICIÓN → 'treemap'.
       * CONVERSIÓN o PROCESO → 'funnel_chart'.
       * CATEGORÍAS < 5 → 'bar_chart' o 'pie_chart'. > 5 → 'treemap'.
       * FLUJO FINANCIERO → 'waterfall'.
       * CORRELACIÓN (2 métricas) → 'scatter_plot'.
       * DISTRIBUCIÓN ESTADÍSTICA → 'histogram'.
       * VARIABILIDAD / OUTLIERS → 'boxplot'.
       * INTENSIDAD (Matriz) → 'heatmap'.

    3. 💰 DETECCIÓN DE UNIDAD (Moneda vs Cantidad):
       - Mira la TOPOLOGÍA: si dice "UNIT: PERCENTAGE" -> `metric_unit`: "percentage".
       - Si el SCHEMA indica 'numeric(metric)' y no hay más info, usa "number".

    4. 📖 INTELIGENCIA DE GLOSARIO (Mapeo Semántico de Columnas):
       - El GLOSARIO contiene definiciones del negocio escritas por el usuario.
       - REGLA CRÍTICA: Cuando el usuario mencione un concepto (ej: "productos pronto a vencer",
         "fechas de caducidad", "vencimiento"), BUSCA EN EL GLOSARIO si algún término mapea
         a una columna específica.
       - Ejemplo: Si el glosario dice {{'fecaduc_feprefercons': 'Fecha de caducidad de los materiales'}},
         y el usuario pide "productos pronto a vencer", DEBES usar la columna 'fecaduc_feprefercons'
         en tus filtros (ej: comparar con la fecha actual para encontrar próximos a vencer).
       - PARA COLUMNAS CON NOMBRES LEGIBLES (ej: 'fecha_vencimiento', 'stock_disponible'):
         Infiere su significado directamente del nombre, sin necesitar glosario.
       - PARA COLUMNAS CON NOMBRES CRÍPTICOS (ej: 'fecaduc_feprefercons', 'tp_alm'):
         SOLO úsalas si el GLOSARIO las define. NO adivines su significado.
       - 📅 FILTROS TEMPORALES RELATIVOS: Cuando el usuario pida "próximo a vencer", "por vencer",
         "deadlines", etc., busca FECHA_REFERENCIA_DATASET en la TOPOLOGÍA. Usa esa fecha como "hoy"
         y crea un filtro con operador "<" sobre la columna de vencimiento.
         Ejemplo: Si FECHA_REFERENCIA_DATASET=2021-07-31 y la columna de vencimiento es 'fecaduc_feprefercons',
         crea un filtro: {{"column": "fecaduc_feprefercons", "operator": "<", "value": "2021-10-31"}}
         (90 días después de la referencia). Esto filtra productos que vencen ANTES de esa fecha.

    5. 🛡️ PROTOCOLO ANTI-ALUCINACIÓN:
       - Si el usuario pide un análisis pero NO ENCUENTRAS una columna que corresponda al concepto
         (ni por nombre legible ni por glosario), NO inventes un análisis genérico.
       - En su lugar, llena el campo "glossary_hint" con un mensaje claro:
         Ejemplo: "No encontré una columna relacionada con 'fechas de vencimiento'.
         Sugiero agregar al Glosario qué columna contiene esta información."
       - NUNCA hagas un análisis diferente al que pidió el usuario. Si no puedes hacerlo, usa glossary_hint.

    6. 📊 TRIPLE VISTA (Dashboard Automático) — [FASE 3C]:
       - Para análisis generales, DEBES generar EXACTAMENTE 3 planes complementarios:
         1) Vista Principal: El análisis EXACTO que pidió el usuario.
         2) Vista Complementaria: Un análisis que ENRIQUEZCA el primero con otra perspectiva.
            Ej: si el principal es "trend" → complementa con "distribution" del top.
            Ej: si el principal es "descriptive" → complemento con "trend" de la métrica.
         3) Vista Diagnóstica: Análisis que revele CAUSAS o ANOMALÍAS.
            REGLA DE GRÁFICO PARA DIAGNÓSTICA:
            - NO uses boxplot por defecto. Solo boxplot si el usuario pide variabilidad, outliers, dispersión o boxplot explícitamente.
            - Si el principal es trend → diagnóstica con barras Top N de los drivers de cambio.
            - Si el principal es distribution → diagnóstica con línea temporal del top 1.
            - Si el principal es descriptive → diagnóstica con distribución por categoría (barras).
            - Si el principal es predictive → complementaria con dual_axis (histórico vs variación %).
              Diagnóstica con distribución Top N de los items que más impulsan el cambio proyectado.
              Los planes complementarios DEBEN estar vinculados al pronóstico, NO ser análisis genéricos del dataset.
       - EXCEPCIONES (generar UN solo plan):
         a) El usuario pide explícitamente un solo gráfico ("quiero un pie chart")
         b) El prompt es una pregunta simple de KPI ("cuánto vendimos")
       - Si el usuario PIDE explícitamente N gráficos (ej: "dame 4 gráficos"), genera EXACTAMENTE N planes.
       - OUTPUT: Array JSON `[{{plan1}}, {{plan2}}, {{plan3}}]` o un solo objeto JSON `{{plan1}}`.

    7. 🔄 GRÁFICOS COMBINADOS (Dual-Axis):
       - Cuando el análisis involucre DOS MÉTRICAS con ESCALAS DISTINTAS (ej: Volumen absoluto + % Variación),
         usa visual_protocol: 'dual_axis_chart'.
       - Si el usuario pide "comparar X vs Y" donde una es valor absoluto y otra porcentaje → DUAL AXIS.
       - Ej: Stock (miles) vs Variación % → dual_axis_chart (barras izq + línea der).

    8. 👑 SOBERANÍA DEL USUARIO (Chart Type Override) — [FASE 3C]:
       - Si el usuario NOMBRA un tipo de gráfico específico ("barras", "lineal", "pie", "scatter"),
         OBLIGATORIO usar ese visual_protocol. Tu rol es aconsejar, no bloquear.
       - Si el usuario pide MÚLTIPLES tipos ("barras y lineal"), genera UN plan POR CADA tipo mencionado.
         Ej: "barras y lineal de stock" → [{{plan con bar_chart}}, {{plan con line_chart}}].
       - Mapeo de nombres comunes:
         barras/columnas = bar_chart | lineal/línea/tendencia = line_chart
         pastel/torta/pie = pie_chart | dispersión/scatter = scatter_plot
         área = area_chart | caja/boxplot = boxplot | embudo/funnel = funnel_chart

    9. 🧭 POLARIDAD DE MÉTRICA (Contexto de Negocio) — [FASE 3D]:
       - Para CADA plan, clasifica `metric_polarity` según la INTENCIÓN del prompt:
         * "favorable": métricas que el negocio quiere MAXIMIZAR (ventas, ingresos, producción, satisfacción, eficiencia)
         * "unfavorable": métricas que el negocio quiere MINIMIZAR (vencimientos, merma, errores, deudas, devoluciones, quejas, accidentes, desperdicio)
         * "neutral": métricas informativas sin dirección preferida (stock general, conteo, distribución, inventario)
       - IMPORTANTE: Infiere la polaridad del CONTEXTO del prompt, no solo del nombre de la columna.
         Ej: "productos a vencer" → unfavorable | "producción mensual" → favorable | "stock por almacén" → neutral

    10. 🎯 DIVERSIDAD OBLIGATORIA EN TRIPLE VISTA — [FASE 3E]:
       - Los 3 planes DEBEN tener TIPOS DE GRÁFICO VISUAL DISTINTOS (visual_protocol diferente).
       - PROHIBIDO: 2 planes con el mismo visual_protocol (ej: dos line_chart, dos bar_chart).
       - Si el principal es line_chart → complementario con bar_chart, pie_chart, dual_axis_chart o treemap.
       - DIVERSIFICA métricas y dimensiones entre planes, no solo el título.
       - Ejemplo INCORRECTO: [line_chart stock total, line_chart stock diario, bar_chart top]
       - Ejemplo CORRECTO:   [line_chart stock mensual, bar_chart top 10 almacenes, pie_chart distribución %]

    11. 🧾 SOBERANÍA DE FORMATO (Kill Switch por Solicitud):
       - Si recibes una INSTRUCCIÓN EXPLÍCITA de formato para ESTA solicitud (ej: "solo tabla", "sin gráficos", "datos crudos"),
         DEBES respetarla solo en esta petición.
       - En ese caso:
         * NO generes triple vista automática.
         * NO uses memoria previa para imponer formato visual.
         * Puedes conservar la intención analítica (descriptive/trend/distribution), pero asume que la salida final será TABULAR.
         * Genera EXACTAMENTE 1 plan.

    12. 🧠 CONTRATO SEMÁNTICO DEL DATASET (Obligatorio):
       - Lee `DATASET_CONTRACT` y `DATASET_EVIDENCE` dentro de la TOPOLOGÍA.
       - Si `mode=flow`, PROHIBIDO colapsar el análisis a la última fecha por defecto.
         Solo usa "último", "actual", "latest" o corte reciente si el usuario lo pide explícitamente.
       - Si `mode=snapshot` y `snapshot_guard_allowed=True`, puedes asumir que la vista natural
         del negocio es el último corte para stock, saldos, inventario o estado actual,
         salvo que el usuario pida un rango temporal distinto.
       - Si `mode=hybrid`, no inventes filtros de última foto; prioriza filtros explícitos del usuario
         y solo usa snapshot cuando el concepto de negocio sea estado/corte.
       - Si existe `time_axis` y observas múltiples cortes temporales en el contrato, por defecto interpreta
         análisis descriptivos/distributivos como "último corte" salvo que el usuario pida historia, comparación o tendencia.
       - La presencia de columnas de fecha NO implica snapshot. El contrato manda.

    --- 🧠 MEMORIA Y REGLAS DE NEGOCIO --- [FASE 3F]
    - Si `DATASET_CONTRACT.mode=snapshot`, usa el último corte como referencia natural solo cuando el análisis sea de estado actual.
    - Si `DATASET_CONTRACT.mode=flow`, trata las fechas como una serie transaccional completa y NO inventes un filtro al último corte.
    - MEMORIA DE SESIÓN: {memory_context if memory_context else 'Sin contexto previo. Nueva conversación.'}
    {memory_instruction if memory_instruction else '- Si el usuario hace un análisis COMPLETAMENTE NUEVO (tema diferente al anterior): IGNORA la memoria de sesión y genera planes frescos.'}
    {format_instruction if format_instruction else '- FORMATO: sin restricción explícita. Mantén el instinto visual por defecto.'}

    OUTPUT: Genera estrictamente un JSON válido compatible con el siguiente Schema:
    {schema_json}
    """

    try:
        _translator_input = f"{system_instruction}\n\nUSUARIO: {prompt}"
        plans = generate_translator_plans_with_model(
            primary_model_name, _translator_input, list(columns or []),
        )

        if not plans:
            return None

        set_cached_json(
            "semantic_translator", translator_cache_key,
            [plan.model_dump(mode="json") for plan in plans],
            settings.SEMANTIC_TRANSLATOR_CACHE_TTL_SECONDS,
        )
        return finalize_plans(plans, schema_profile)

    except Exception as e:
        if is_recoverable_translator_model_error(e):
            fallback_model_name = select_translator_fallback_model(primary_model_name)
            quota_error = is_quota_translator_model_error(e)
            router_contract_plans = None
            if quota_error:
                router_contract_plans = build_plan_from_router_contract(
                    router_decision, list(columns or []),
                    schema_profile=schema_profile, dataset_contract=dataset_contract,
                )
                if router_contract_plans:
                    set_cached_json(
                        "semantic_translator", translator_cache_key,
                        [plan.model_dump(mode="json") for plan in router_contract_plans],
                        settings.SEMANTIC_TRANSLATOR_CACHE_TTL_SECONDS,
                    )
                    emit_structured_log(
                        "semantic_translator_router_contract_fallback_accepted",
                        prompt=prompt[:200], primary_model=primary_model_name,
                        fallback_model=fallback_model_name,
                        plan_count=len(router_contract_plans),
                        reason_codes=router_decision.get("reason_codes"),
                        fallback_priority="quota_first",
                    )
                    return finalize_plans(router_contract_plans, schema_profile)

            if fallback_model_name:
                try:
                    fallback_plans = generate_translator_plans_with_model(
                        fallback_model_name, _translator_input, list(columns or []),
                    )
                    if fallback_plans:
                        set_cached_json(
                            "semantic_translator", translator_cache_key,
                            [plan.model_dump(mode="json") for plan in fallback_plans],
                            settings.SEMANTIC_TRANSLATOR_CACHE_TTL_SECONDS,
                        )
                        emit_structured_log(
                            "semantic_translator_model_fallback_accepted",
                            prompt=prompt[:200], primary_model=primary_model_name,
                            fallback_model=fallback_model_name,
                            plan_count=len(fallback_plans),
                        )
                        return finalize_plans(fallback_plans, schema_profile)
                except Exception as fallback_error:
                    emit_structured_log(
                        "semantic_translator_model_fallback_error",
                        level="warning", error=str(fallback_error)[:300],
                        primary_model=primary_model_name,
                        fallback_model=fallback_model_name,
                    )

            if router_contract_plans is None:
                router_contract_plans = build_plan_from_router_contract(
                    router_decision, list(columns or []),
                    schema_profile=schema_profile, dataset_contract=dataset_contract,
                )
            if router_contract_plans:
                set_cached_json(
                    "semantic_translator", translator_cache_key,
                    [plan.model_dump(mode="json") for plan in router_contract_plans],
                    settings.SEMANTIC_TRANSLATOR_CACHE_TTL_SECONDS,
                )
                emit_structured_log(
                    "semantic_translator_router_contract_fallback_accepted",
                    prompt=prompt[:200], primary_model=primary_model_name,
                    fallback_model=fallback_model_name,
                    plan_count=len(router_contract_plans),
                    reason_codes=router_decision.get("reason_codes"),
                )
                return finalize_plans(router_contract_plans, schema_profile)

        return None
