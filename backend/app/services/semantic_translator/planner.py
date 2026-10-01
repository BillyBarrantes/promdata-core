import json
from typing import Any, Optional, List
import pandas as pd

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
    extract_domain_residue,
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
from app.services.semantic_translator.metric_archetype import (
    build_metric_coverage_metadata,
    classify_metric_families,
    compute_metric_features,
    count_metric_features,
    cumulative_aggregation,
    has_accumulation_lexicon,
    is_cumulative_metric,
    is_non_additive,
    select_metrics_for_broad_analysis,
)
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
    if cardinality > 12:
        preferred = ["treemap", "bar_chart", "pie_chart"]
    elif cardinality > 6:
        preferred = ["bar_chart", "treemap", "pie_chart"]

    for candidate in preferred:
        if candidate != primary_visual:
            return candidate
    return "bar_chart"


def _resolve_abstract_count_metric(
    surface_prompt: str,
    columns: list[str],
    schema_profile: dict | None = None,
    candidate_df: pd.DataFrame | None = None,
) -> str | None:
    """Resuelve métricas abstractas de conteo (ej. 'cantidad de empleados')
    a la columna ID del dataset, forzando aggregation=count.

    Cascada de 3 fallbacks:
    1. Coincidencia por prefijo (id_, id)
    2. Coincidencia por rol semántico (role=identifier)
    3. Cardinalidad real desde candidate_df (>80% unique en columna string)
    100% schema-agnostic: el paso 3 funciona aunque schema_profile sea vacío.
    """
    if not surface_prompt or not columns:
        return None
    prompt_lower = surface_prompt.lower().strip()
    count_keywords = {"cantidad", "número", "numero", "total", "conteo", "cuenta", "num"}
    if not any(kw in prompt_lower for kw in count_keywords):
        return None

    # Fallback 1: columna con prefijo id_
    for col in columns:
        col_lower = col.lower().strip()
        if col_lower.startswith("id_") or col_lower == "id":
            role = (schema_profile or {}).get(col, {}).get("role") if schema_profile else None
            if role in ("identifier", None):
                return col
        if col_lower in ("identificador", "identificateur", "employee_id", "user_id", "usuario_id"):
            return col

    # Fallback 2: columna marcada como identifier en schema_profile
    for col in columns:
        role = (schema_profile or {}).get(col, {}).get("role") if schema_profile else None
        if role == "identifier":
            return col

    # Fallback 3: cardinalidad real desde candidate_df (>80% unique)
    if candidate_df is not None and not candidate_df.empty:
        id_candidates: list[tuple[int, str]] = []
        for col in columns:
            if col not in candidate_df.columns:
                continue
            if not pd.api.types.is_string_dtype(candidate_df[col]):
                continue
            try:
                nunique = int(candidate_df[col].nunique())
                total = len(candidate_df)
                if nunique > 1 and total > 0 and (nunique / total) > 0.8:
                    id_candidates.append((nunique, col))
            except Exception:
                continue
        if id_candidates:
            id_candidates.sort(key=lambda x: x[0], reverse=True)
            return id_candidates[0][1]

    return None


def _build_trend_from_distribution_contract(
    contract: dict, columns: list[str],
    positive_filters: list, negative_filters: list,
    metric_column: str, ranking_metric_column: str | None,
    ranking_direction: str, metric_unit, metric_label: str,
    schema_profile: dict,
) -> list[AnalysisPlan]:
    """[V2.2] Redirige un contrato distribution+requires_time a trend con split_dimension.

    Cuando el router detecta 'distribución por períodos' (ej: "empleados
    activos por cargo para cada período"), la respuesta correcta NO es
    un total agregado sino una evolución temporal con split por dimensión.
    """
    date_column = resolve_contract_column(
        contract.get("time_axis"), columns,
        schema_profile=schema_profile, allowed_roles={"date"},
    )
    if not date_column:
        date_column = pick_primary_date_column(
            columns, schema_profile=schema_profile,
        )
    if not date_column:
        return []

    dimension_column = resolve_contract_column(
        contract.get("dimension"), columns,
        schema_profile=schema_profile, allowed_roles={"dimension", "identifier"},
    )

    top_n = contract.get("top_n")
    if top_n is None and dimension_column:
        cardinality = int(schema_profile.get(dimension_column, {}).get("cardinality") or 0)
        top_n = min(cardinality, 10) if 0 < cardinality <= 12 else 10

    split_limit = max(2, min(int(top_n), 15)) if top_n else None

    grain = str(contract.get("grain") or "month")
    visual_protocol = VisualProtocol.LINE
    date_label = humanize_column_alias(date_column)
    column_aliases = {metric_column: metric_label, date_column: date_label}
    if dimension_column:
        column_aliases[dimension_column] = humanize_column_alias(dimension_column)

    return [
        AnalysisPlan(
            main_intent={
                "type": "trend",
                "rationale": "Ejecuto contrato distribution+requires_time como trend con split_dimension.",
                "filters": positive_filters,
                    "positive_filters": positive_filters,
                    "negative_filters": negative_filters,
                    "metric_unit": metric_unit.value if isinstance(metric_unit, MetricUnit) else MetricUnit.NUMBER.value,
                    "visual_protocol": visual_protocol.value,
                    "date_column": date_column,
                    "value_column": metric_column,
                    "plot_metric": metric_column,
                    "ranking_metric": ranking_metric_column,
                    "ranking_direction": ranking_direction,
                    "grain": grain,
                    "fill_missing": True,
                    "split_dimension": dimension_column,
                    "split_limit": split_limit,
                    "top_n_aggregation_mode": "split",
            },
            title=f"Evolución de {metric_label} por {date_label}",
            column_aliases=column_aliases,
            metric_polarity=MetricPolarity.NEUTRAL,
        )
    ]


_MAX_MULTI_SERIES_CARDINALITY = 15
_FALLBACK_MULTI_SERIES_LIMIT = 5


def _resolve_multi_series_limit(
    dimension_column: str,
    schema_profile: dict[str, Any],
    candidate_df: pd.DataFrame | None = None,
    requested_limit: int | None = None,
) -> int:
    """Return a bounded, data-driven series limit for dimensional visuals.

    A requested Top-N is authoritative. Otherwise the schema cardinality is
    used so a low-cardinality dimension (for example, five regions) retains
    every requested comparison series. The bounded fallback prevents an
    unknown/high-cardinality identifier from producing an unreadable chart.
    """
    if isinstance(requested_limit, int) and requested_limit > 0:
        return max(2, min(requested_limit, _MAX_MULTI_SERIES_CARDINALITY))

    cardinality = int(schema_profile.get(dimension_column, {}).get("cardinality") or 0)
    if cardinality < 2 and candidate_df is not None and dimension_column in candidate_df.columns:
        cardinality = int(candidate_df[dimension_column].nunique(dropna=True) or 0)

    if cardinality >= 2:
        return min(cardinality, _MAX_MULTI_SERIES_CARDINALITY)
    return _FALLBACK_MULTI_SERIES_LIMIT


def _resolve_contract_trend_split(
    contract: dict[str, Any],
    columns: list[str],
    schema_profile: dict[str, Any],
    candidate_df: pd.DataFrame | None = None,
    requested_limit: int | None = None,
) -> tuple[str | None, int | None]:
    """Map a semantic ``split`` contract to a safe Ibis trend projection.

    ``series_mode=split`` is an explicit user-facing contract, independent of
    Top-N. Requiring Top-N here collapses valid requests such as "one line per
    region" into a total. ``sum`` intentionally remains a single aggregate
    unless it includes a Top-N selection.
    """
    if str(contract.get("series_mode") or "none") != "split":
        return None, None

    split_dimension = resolve_contract_column(
        contract.get("dimension"),
        columns,
        schema_profile=schema_profile,
        allowed_roles={"dimension", "identifier"},
    )
    if not split_dimension:
        return None, None

    return split_dimension, _resolve_multi_series_limit(
        split_dimension,
        schema_profile,
        candidate_df=candidate_df,
        requested_limit=requested_limit,
    )


def build_plan_from_router_contract(
    router_decision: dict[str, Any],
    columns: list[str],
    schema_profile: dict | None = None,
    dataset_contract: dict[str, Any] | None = None,
    candidate_df: pd.DataFrame | None = None,
) -> Optional[List[AnalysisPlan]]:
    if not settings.DETERMINISTIC_VISUAL_FASTPATH_ENABLED:
        return None

    schema_profile = schema_profile or {}
    dataset_contract = dataset_contract or {}
    contract = router_decision.get("semantic_contract") or {}
    if not isinstance(contract, dict):
        return None

    intent = str(contract.get("intent") or router_decision.get("detected_intent") or "unknown")

    # Step 1: Try exact column match
    contract_metric_hint = str(contract.get("plot_metric") or contract.get("metric") or "").strip()
    if contract_metric_hint and contract_metric_hint in columns:
        metric_column = contract_metric_hint
    else:
        metric_column = None

    # Step 2: [V9] Resolver metrica abstracta de conteo (ej. "cantidad de empleados")
    # Se ejecuta ANTES del fuzzy match con role filter para evitar que
    # prompts de conteo abstracto ("cantidad de empleados") sean interceptados
    # por columnas metricas ("nivel_desempeno" por la keyword "cantidad").
    if not metric_column:
        metric_column = _resolve_abstract_count_metric(
            str(contract.get("metric") or ""), columns,
            schema_profile=schema_profile, candidate_df=candidate_df,
        )

    # Step 3: Fuzzy column resolution with role filter
    if not metric_column:
        metric_column = resolve_contract_column(
            contract.get("plot_metric") or contract.get("metric"),
            columns, schema_profile=schema_profile, allowed_roles={"metric"},
        )

    # Step 4: Infer default metric column
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

        split_dimension, split_limit = _resolve_contract_trend_split(
            contract,
            columns,
            schema_profile,
            candidate_df=candidate_df,
            requested_limit=top_n if isinstance(top_n, int) else None,
        )
        if top_n and series_mode == "sum":
            split_dimension = resolve_contract_column(
                contract.get("dimension"), columns,
                schema_profile=schema_profile, allowed_roles={"dimension", "identifier"},
            )
            if not split_dimension:
                return None
            split_limit = _resolve_multi_series_limit(
                split_dimension,
                schema_profile,
                candidate_df=candidate_df,
                requested_limit=int(top_n),
            )

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
                    "positive_filters": positive_filters,
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
        # [V2.2] Redirect: distribution + requires_time → trend with split_dimension
        if contract.get("requires_time") or contract.get("time_axis"):
            print(f"🔄 [DISTRIBUTION→TREND] Redirigiendo distribución con tiempo a trend+split "
                  f"(dimension={contract.get('dimension')}, time_axis={contract.get('time_axis')})")
            return _build_trend_from_distribution_contract(
                contract, columns, positive_filters, negative_filters,
                metric_column, ranking_metric_column, ranking_direction,
                metric_unit, metric_label, schema_profile,
            )

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
                    "positive_filters": positive_filters,
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
        # [V2.3] Redirect: descriptive + requires_time + dimension → trend with split.
        # Mirror of the distribution redirect (line ~487). When the LLM router
        # classifies a dimensional+temporal prompt as "descriptive" (e.g.
        # "análisis por región del 2025"), the temporal signal (requires_time)
        # combined with a resolved dimension indicates the user wants an
        # evolution chart with one line per category, not a static bar total.
        # This redirect is schema-agnostic: it works for any dataset, any
        # dimension column, any metric — no hardcoded names or keywords.
        _descriptive_has_temporal = bool(
            contract.get("requires_time") or contract.get("time_axis")
        )
        _descriptive_dimension_hint = contract.get("dimension")
        if _descriptive_has_temporal and _descriptive_dimension_hint:
            _resolved_dim = resolve_contract_column(
                _descriptive_dimension_hint, columns,
                schema_profile=schema_profile, allowed_roles={"dimension", "identifier"},
            )
            if _resolved_dim:
                print(
                    f"🔄 [DESCRIPTIVE→TREND] Redirigiendo descriptive con tiempo a trend+split "
                    f"(dimension={_descriptive_dimension_hint}, "
                    f"time_axis={contract.get('time_axis')}, "
                    f"requires_time={contract.get('requires_time')})"
                )
                return _build_trend_from_distribution_contract(
                    contract, columns, positive_filters, negative_filters,
                    metric_column, ranking_metric_column, ranking_direction,
                    metric_unit, metric_label, schema_profile,
                )

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
                "positive_filters": positive_filters,
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

    if len(dimension_candidates) == 1:
        primary_dimension = dimension_candidates[0]
    else:
        norm_segment = (dimension_segment or "").strip().lower().replace("_", " ")
        exact = [
            c for c in dimension_candidates
            if c.strip().lower() == norm_segment or c.strip().lower().replace("_", " ") == norm_segment
        ]
        if len(exact) == 1:
            primary_dimension = exact[0]
        else:
            dim_roles = [c for c in dimension_candidates if schema_profile.get(c, {}).get("role") == "dimension"]
            if len(dim_roles) == 1:
                primary_dimension = dim_roles[0]
            else:
                primary_dimension = dimension_candidates[0] if dimension_segment else None

    if not primary_dimension:
        return None
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
        # [FIX C-A 2026-09] El sufijo debe reflejar el filtro REALMENTE inyectado.
        # hybrid tiene snapshot_guard_allowed=True pero NO recibe filtro latest
        # (A+ L1) → su KPI es histórico, no un corte.
        if default_snapshot_filters:
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
    candidate_df: pd.DataFrame | None = None,
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

    # [FIX B2 2026-09] Un trend sobre el eje de periodo describe la EVOLUCIÓN
    # entre cortes: NO debe recibir el filtro 'latest' (colapsaría la serie a un
    # único punto). El snapshot guard del Ibis engine ya omite el auto-filtro
    # para trends sobre el time_axis (ADR-TEMPORAL-001 / T4). El KPI y la
    # distribución sí conservan el corte actual.
    trend_filters: list = []

    metric_cols = [
        col for col in columns
        if schema_profile.get(col, {}).get("role") == "metric"
    ]

    coverage_meta: dict[str, Any] | None = None
    selected_metrics: list[str] = []
    features: dict[str, Any] = {}

    # Candidatos a clave de documento: identificadores de alta cardinalidad
    # (cabecera↔detalle) usados para confirmar no aditividad estructural.
    identifier_cols = [
        col for col in columns
        if col in (candidate_df.columns if candidate_df is not None else [])
        and (
            schema_profile.get(col, {}).get("role") == "identifier"
            or float(schema_profile.get(col, {}).get("cardinality_ratio") or 0.0) >= 0.5
        )
    ]

    if candidate_df is not None and not candidate_df.empty:
        # [#4 2026-09] Eje temporal primario para evaluar monotonía (señal de
        # acumulado). Domain-agnostic: se resuelve por contrato/schema, no por nombre.
        _cumulative_time_col = pick_primary_date_column(
            columns, schema_profile=schema_profile, dataset_contract=dataset_contract,
        )
        features = compute_metric_features(
            candidate_df, metric_cols,
            identifier_cols=identifier_cols,
            time_col=_cumulative_time_col,
        )
        families = classify_metric_families(features)
        selected_metrics = select_metrics_for_broad_analysis(
            metric_cols, features, families, surface_prompt,
        )
        # [P1.4 2026-09] No aditividad estructural: si la métrica preferida por
        # relevancia es a nivel documento y existe otra claramente aditiva,
        # promovemos la aditiva al KPI (evita el ~N× de inflación por re-conteo).
        non_additive_signals = {
            m: count_signals
            for m, count_signals in count_metric_features(features).items()
            if is_non_additive(features.get(m))
        }

        # [#4 2026-09] Métricas acumuladas: dos señales fail-closed (monotonía
        # temporal AND léxico genérico de acumulación en nombre o prompt).
        # La corrección de la agregación (sum→max/min) la aplica el guard de
        # ejecución del production executor (cubre TODAS las rutas); aquí solo
        # se exponen para cobertura y se emite telemetría de la clase.
        _cumulative_signals = {
            m: count_signals
            for m, count_signals in count_metric_features(features).items()
            if is_cumulative_metric(features.get(m), m, prompt)
        }
        _structural_only = [
            m for m, feat in features.items()
            if feat.get("cumulative_monotonic_direction") in ("inc", "dec")
            and m not in _cumulative_signals
        ]
        if _structural_only:
            emit_structured_log(
                "semantic_cumulative_signal_insufficient",
                prompt=prompt[:160],
                structural_only_metrics=[
                    {
                        "metric": m,
                        "direction": features[m].get("cumulative_monotonic_direction"),
                        "lexicon": has_accumulation_lexicon(m),
                    }
                    for m in _structural_only[:5]
                ],
            )
        if len(selected_metrics) >= 2 and is_non_additive(features.get(selected_metrics[0])):
            additive_alt = next(
                (m for m in selected_metrics[1:] if not is_non_additive(features.get(m))),
                None,
            )
            if additive_alt:
                emit_structured_log(
                    "semantic_translator_non_additive_metric_deprioritized",
                    prompt=prompt[:160],
                    deprioritized_metric=selected_metrics[0],
                    promoted_metric=additive_alt,
                    signals=non_additive_signals.get(selected_metrics[0], {}),
                )
                selected_metrics = [additive_alt] + [
                    m for m in selected_metrics if m != additive_alt
                ]
        coverage_meta = build_metric_coverage_metadata(
            selected_metrics, metric_cols, families,
            non_additive_metrics=non_additive_signals,
            cumulative_metrics=_cumulative_signals,
        )
    else:
        single = infer_default_metric_column(surface_prompt, columns, schema_profile=schema_profile)
        if single:
            selected_metrics = [single]
    if not selected_metrics:
        emit_structured_log(
            "semantic_translator_macro_empty",
            prompt=prompt[:200],
            reason="no_metric_columns",
        )
        return []

    primary_metric = selected_metrics[0]
    primary_is_non_additive = is_non_additive(features.get(primary_metric))
    # [TIER 0 2026-09] 'mean' no existe en el Literal de DescriptiveIntent.
    # El valor canónico es 'avg'. Además el canonicalizador de semantic_grammar
    # protege la frontera, pero aquí mantenemos la coherencia semántica.
    primary_aggregation = "avg" if primary_is_non_additive else "sum"
    metric_unit = infer_metric_unit_from_column_name(primary_metric)
    primary_label = humanize_column_alias(primary_metric)
    plans: list[AnalysisPlan] = []
    primary_aliases = {primary_metric: primary_label}

    descriptive_title = f"{primary_label} Total"
    if primary_is_non_additive:
        # Degradación honesta: no hay métrica aditiva alternativa.
        descriptive_title = f"{primary_label} (promedio por registro)"
    # [FIX C-A 2026-09] Fiel al filtro real: hybrid no recibe corte → sin sufijo.
    if default_snapshot_filters:
        descriptive_title += " (Corte Actual)"
    plans.append(
        AnalysisPlan(
            main_intent=DescriptiveIntent(
                rationale=(
                    "Priorizo un KPI global para abrir el análisis con la magnitud base más representativa del dataset."
                    if not primary_is_non_additive
                    else "La métrica principal es un atributo a nivel documento (valor repetido): se promedia en vez de sumar para evitar el re-conteo."
                ),
                filters=default_snapshot_filters,
                metrics=[primary_metric],
                metric_unit=metric_unit if isinstance(metric_unit, MetricUnit) else MetricUnit.NUMBER,
                aggregation=primary_aggregation,
                visual_protocol=VisualProtocol.KPI,
            ),
            title=descriptive_title,
            column_aliases=primary_aliases.copy(),
            metric_polarity=MetricPolarity.NEUTRAL,
        )
    )

    date_column = pick_primary_date_column(
        columns, schema_profile=schema_profile, dataset_contract=dataset_contract,
    )
    has_date = has_meaningful_temporal_axis(date_column, schema_profile=schema_profile)

    # [FIX 2026-09 coherencia] El trend del dashboard macro describe SIEMPRE una
    # métrica cuyo total reconcilia con el KPI. Se usa la métrica secundaria SOLO
    # si es aditiva (una segunda familia legítima). Si la secundaria es a nivel
    # documento (no aditiva, p.ej. un monto repetido en cada línea), graficarla
    # produciría una "evolución" de promedios que no cuadra con nada → colapsa a
    # la métrica primaria.
    trend_metric = primary_metric
    trend_aggregation = primary_aggregation
    if len(selected_metrics) > 1 and has_date:
        second_candidate = selected_metrics[1]
        if not is_non_additive(features.get(second_candidate)):
            trend_metric = second_candidate
            trend_aggregation = "sum"

    if has_date:
        date_label = humanize_column_alias(date_column)
        trend_label = humanize_column_alias(trend_metric)
        trend_unit = infer_metric_unit_from_column_name(trend_metric)
        trend_who = (
            "una segunda familia métrica aditiva"
            if trend_metric != primary_metric
            else "la métrica principal"
        )
        trend_title = f"Evolución de {trend_label} por {date_label}"
        if trend_aggregation == "avg":
            # Divulgación honesta: no se está sumando, se promedia (métrica de documento).
            trend_title += " (promedio por registro)"
        plans.append(
            AnalysisPlan(
                main_intent=TimeTrendIntent(
                    rationale=(
                        f"Agrego una lectura temporal de {trend_who} para revelar "
                        "tendencia y cambio cuando el dataset ofrece un eje cronológico real."
                    ),
                    date_column=date_column,
                    value_column=trend_metric,
                    metric_unit=trend_unit if isinstance(trend_unit, MetricUnit) else MetricUnit.NUMBER,
                    aggregation=trend_aggregation,
                    visual_protocol=VisualProtocol.LINE,
                    filters=trend_filters,
                ),
                title=trend_title,
                column_aliases={trend_metric: trend_label, date_column: date_label},
                metric_polarity=MetricPolarity.NEUTRAL,
            )
        )

    if len(selected_metrics) > 2:
        third_metric = selected_metrics[2]
        third_unit = infer_metric_unit_from_column_name(third_metric)
        third_label = humanize_column_alias(third_metric)
        third_aliases = {third_metric: third_label}
        third_is_non_additive = is_non_additive(features.get(third_metric))
        third_title = (
            f"{third_label} (promedio por registro)"
            if third_is_non_additive
            else f"{third_label} Total"
        )
        plans.append(
            AnalysisPlan(
                main_intent=DescriptiveIntent(
                    rationale="Incluyo un KPI adicional de una tercera familia métrica para maximizar la cobertura del análisis broad.",
                    filters=default_snapshot_filters,
                    metrics=[third_metric],
                    metric_unit=third_unit if isinstance(third_unit, MetricUnit) else MetricUnit.NUMBER,
                    aggregation="avg" if third_is_non_additive else "sum",
                    visual_protocol=VisualProtocol.KPI,
                ),
                title=third_title,
                column_aliases=third_aliases,
                metric_polarity=MetricPolarity.NEUTRAL,
            )
        )

    primary_dimension = pick_best_dimension_column(
        surface_prompt, columns, schema_profile=schema_profile,
    )
    if primary_dimension and len(plans) < 3:
        primary_visual = select_default_distribution_visual(primary_dimension, schema_profile=schema_profile)
        dimension_label = humanize_column_alias(primary_dimension)
        dimension_cardinality = int(schema_profile.get(primary_dimension, {}).get("cardinality") or 0)
        limit = dimension_cardinality if 0 < dimension_cardinality <= 12 else 10
        dist_aliases = primary_aliases.copy()
        dist_aliases[primary_dimension] = dimension_label
        plans.append(
            AnalysisPlan(
                main_intent=DistributionIntent(
                    rationale="Incluyo una vista de concentración para identificar qué categorías explican el peso operativo dominante.",
                    filters=default_snapshot_filters,
                    dimension=primary_dimension,
                    metric=primary_metric,
                    limit=limit,
                    metric_unit=metric_unit if isinstance(metric_unit, MetricUnit) else MetricUnit.NUMBER,
                    visual_protocol=VisualProtocol(primary_visual),
                ),
                title=f"{primary_label} por {dimension_label}",
                column_aliases=dist_aliases,
                metric_polarity=MetricPolarity.NEUTRAL,
            )
        )

    if not plans:
        emit_structured_log(
            "semantic_translator_macro_empty",
            prompt=prompt[:200],
            reason="no_plans_built",
        )
        return []

    bounded = plans[:3]
    if coverage_meta and bounded:
        bounded[0].coverage_metadata = coverage_meta

    emit_structured_log(
        "semantic_translator_macro_fast_path_hit",
        prompt=prompt[:200],
        plan_count=len(bounded),
        metrics=selected_metrics,
        families_found=(coverage_meta or {}).get("families_found", 0),
        date_column=date_column,
        primary_dimension=primary_dimension,
        dataset_mode=dataset_contract.get("dataset_mode"),
    )
    return bounded


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


_CORTE_FILTER_TOKENS = {"latest", "last", "ultimo", "último", "actual", "recent", "hoy"}


def _filters_contain_corte(filters: Any, dataset_contract: dict[str, Any] | None) -> bool:
    """[C-A 2026-09] ¿Los filtros del plan aplican un corte temporal real?

    Solo cuenta un token de corte sobre una columna temporal (o el flag
    `is_latest_snapshot`). Evita marcar '(Corte Actual)' por filtros categóricos
    de valor 'actual' y por datasets hybrid que no reciben corte (A+ L1)."""
    contract = dataset_contract or {}
    date_cols = {str(c).strip().lower() for c in (contract.get("date_columns") or []) if str(c).strip()}
    if str(contract.get("time_axis") or "").strip():
        date_cols.add(str(contract.get("time_axis")).strip().lower())
    date_cols.add("is_latest_snapshot")
    for f in (filters or []):
        if isinstance(f, dict):
            col = str(f.get("column") or "").strip().lower()
            value = f.get("value")
        else:
            col = str(getattr(f, "column", "") or "").strip().lower()
            value = getattr(f, "value", None)
        if col in date_cols and str(value or "").strip().lower() in _CORTE_FILTER_TOKENS:
            return True
    return False


def _build_complementary_dashboard_plans(
    primary_plans: list[AnalysisPlan],
    router_decision: dict[str, Any],
    columns: list[str],
    schema_profile: dict[str, Any],
    dataset_contract: dict[str, Any],
) -> list[AnalysisPlan]:
    """[V2.3] Enrich a SIMPLE-path primary plan into a complete dashboard.

    Implements the Triple Vista contract for deterministic plans:

    +-------------------+-------------------------------+-------------------------+
    | Primary type      | Complementary (Plan 2)        | Diagnostic (Plan 3)     |
    +-------------------+-------------------------------+-------------------------+
    | trend             | distribution by dimension     | KPI: metric total       |
    | distribution      | trend over time               | KPI: metric total       |
    +-------------------+-------------------------------+-------------------------+

    Schema-agnostic: uses column roles from ``schema_profile``, not names.
    Does NOT enrich simple KPIs or plans that already have ≥ 3 items.
    """
    if len(primary_plans) >= 3:
        return primary_plans[:3]

    contract = router_decision.get("semantic_contract") or {}

    # ── Extract primary plan metadata ───────────────────────────────────
    primary_intent = primary_plans[0].main_intent
    if isinstance(primary_intent, dict):
        primary_type = primary_intent.get("type", "")
        primary_metric = (
            primary_intent.get("value_column")
            or primary_intent.get("metric")
            or primary_intent.get("plot_metric")
        )
        primary_dimension = (
            primary_intent.get("split_dimension")
            or primary_intent.get("dimension")
        )
        primary_date = primary_intent.get("date_column")
        plan_filters = (
            primary_intent.get("filters")
            or primary_intent.get("positive_filters")
            or []
        )
    else:
        primary_type = getattr(primary_intent, "type", "")
        primary_metric = (
            getattr(primary_intent, "value_column", None)
            or getattr(primary_intent, "metric", None)
        )
        primary_dimension = (
            getattr(primary_intent, "split_dimension", None)
            or getattr(primary_intent, "dimension", None)
        )
        primary_date = getattr(primary_intent, "date_column", None)
        plan_filters = getattr(primary_intent, "filters", None) or []

    # ── Guard: don't enrich simple KPIs or unresolvable plans ───────────
    if primary_type == "descriptive" and not primary_dimension:
        return primary_plans
    if not primary_metric:
        return primary_plans

    metric_unit = infer_metric_unit_from_column_name(primary_metric)
    metric_label = humanize_column_alias(primary_metric)
    enriched: list[AnalysisPlan] = list(primary_plans)

    # ── Plan 2: Complementary view ──────────────────────────────────────
    if primary_type == "trend":
        # Trend primary → complement with distribution by dimension
        dim_hint = primary_dimension or contract.get("dimension")
        resolved_dim = None
        if dim_hint:
            resolved_dim = resolve_contract_column(
                dim_hint, columns,
                schema_profile=schema_profile,
                allowed_roles={"dimension", "identifier"},
            )
        if not resolved_dim:
            resolved_dim = pick_best_dimension_column(
                "", columns, schema_profile=schema_profile,
            )
        if resolved_dim:
            dim_label = humanize_column_alias(resolved_dim)
            cardinality = int(
                schema_profile.get(resolved_dim, {}).get("cardinality") or 0
            )
            limit = cardinality if 0 < cardinality <= 12 else 10
            dist_visual = select_default_distribution_visual(
                resolved_dim, schema_profile=schema_profile,
            )
            enriched.append(
                AnalysisPlan(
                    main_intent=DistributionIntent(
                        rationale=(
                            "Concentración por dimensión: muestra qué categorías "
                            "explican la mayor parte de la magnitud operativa."
                        ),
                        filters=plan_filters,
                        dimension=resolved_dim,
                        metric=primary_metric,
                        limit=limit,
                        metric_unit=(
                            metric_unit
                            if isinstance(metric_unit, MetricUnit)
                            else MetricUnit.NUMBER
                        ),
                        visual_protocol=VisualProtocol(dist_visual),
                    ),
                    title=f"{metric_label} por {dim_label}",
                    column_aliases={
                        primary_metric: metric_label,
                        resolved_dim: dim_label,
                    },
                    metric_polarity=MetricPolarity.NEUTRAL,
                )
            )

    elif primary_type == "distribution":
        # Distribution primary → complement with trend over time
        date_column = primary_date or pick_primary_date_column(
            columns,
            schema_profile=schema_profile,
            dataset_contract=dataset_contract,
        )
        if date_column and has_meaningful_temporal_axis(
            date_column, schema_profile=schema_profile,
        ):
            date_label = humanize_column_alias(date_column)
            enriched.append(
                AnalysisPlan(
                    main_intent=TimeTrendIntent(
                        rationale=(
                            "Evolución temporal: revela tendencias y estacionalidad "
                            "detrás de la distribución estática."
                        ),
                        date_column=date_column,
                        value_column=primary_metric,
                        metric_unit=(
                            metric_unit
                            if isinstance(metric_unit, MetricUnit)
                            else MetricUnit.NUMBER
                        ),
                        visual_protocol=VisualProtocol.LINE,
                        filters=plan_filters,
                    ),
                    title=f"Evolución de {metric_label} por {date_label}",
                    column_aliases={
                        primary_metric: metric_label,
                        date_column: date_label,
                    },
                    metric_polarity=MetricPolarity.NEUTRAL,
                )
            )

    # ── Plan 3: KPI diagnostic ──────────────────────────────────────────
    if len(enriched) < 3:
        kpi_title = f"{metric_label} Total"
        # [FIX C-A 2026-09] Fiel al filtro real del plan: hybrid no recibe corte → sin sufijo.
        if _filters_contain_corte(plan_filters, dataset_contract):
            kpi_title += " (Corte Actual)"
        enriched.append(
            AnalysisPlan(
                main_intent=DescriptiveIntent(
                    rationale=(
                        "KPI diagnóstico: magnitud total para contextualizar "
                        "la escala del análisis."
                    ),
                    filters=plan_filters,
                    metrics=[primary_metric],
                    metric_unit=(
                        metric_unit
                        if isinstance(metric_unit, MetricUnit)
                        else MetricUnit.NUMBER
                    ),
                    aggregation="sum",
                    visual_protocol=VisualProtocol.KPI,
                ),
                title=kpi_title,
                column_aliases={primary_metric: metric_label},
                metric_polarity=MetricPolarity.NEUTRAL,
            )
        )

    emit_structured_log(
        "semantic_translator_simple_path_enriched",
        primary_type=primary_type,
        primary_metric=primary_metric,
        primary_dimension=primary_dimension,
        plan_count=len(enriched),
    )
    return enriched[:3]


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
    candidate_df: pd.DataFrame | None = None,
) -> Optional[List[AnalysisPlan]]:
    # [SAFE BYPASS MACRO — 0 tokens de traducción para consultas genéricas]
    # [P0.3 2026-09] Telemetría de la clase: registra la decisión del gate y el
    # residuo de dominio. Permite detectar en producción cualquier prompt con
    # conceptos de negocio que estuviera siendo atendido por el fast-path.
    macro_gate_is_broad = looks_broad_analysis_request(prompt)
    emit_structured_log(
        "semantic_broad_gate_decision",
        prompt=prompt[:160],
        is_broad=macro_gate_is_broad,
        domain_residue=extract_domain_residue(prompt)[:10],
    )
    if macro_gate_is_broad:
        # [TIER 0 2026-09 firebreak] Un error de construcción del bundle macro
        # NUNCA debe matar la tarea: se degrada a la ruta unificada/LLM.
        try:
            macro_bundle_plans = build_macro_analysis_bundle(
                prompt=prompt,
                columns=list(columns or []),
                schema_profile=schema_profile,
                dataset_contract=dataset_contract,
                candidate_df=candidate_df,
            )
            if macro_bundle_plans:
                result = finalize_plans(macro_bundle_plans, schema_profile, dataset_contract=dataset_contract, prompt=prompt)
                if result and macro_bundle_plans[0].coverage_metadata:
                    result[0].coverage_metadata = macro_bundle_plans[0].coverage_metadata
                emit_structured_log(
                    "semantic_translator_macro_bypass_activated",
                    prompt=prompt[:180],
                    plan_count=len(result),
                )
                return result
        except Exception as macro_exc:
            emit_structured_log(
                "semantic_translator_macro_bundle_error",
                level="error",
                prompt=prompt[:180],
                error_type=type(macro_exc).__name__,
                error=str(macro_exc)[:300],
            )

    # [CAPA 4: FAST-PATH UNIFICADO - Router + Planner en 1 solo round-trip]
    if getattr(settings, "UNIFIED_SEMANTIC_TRANSLATOR_ENABLED", True):
        try:
            from app.services.semantic_translator.unified_translator import unified_translate
            unified_res = unified_translate(
                prompt=prompt,
                columns=list(columns or []),
                glossary_context=glossary_context,
                topology_context=topology_context,
                memory_context=memory_context,
                memory_instruction=memory_instruction,
                format_instruction=format_instruction,
                schema_profile=schema_profile,
                dataset_contract=dataset_contract,
                related_frames_context=related_frames_context,
                candidate_df=candidate_df,
            )
            if unified_res:
                unified_decision, unified_plans = unified_res
                if unified_plans:
                    emit_structured_log(
                        "semantic_translator_unified_path_accepted",
                        prompt=prompt[:180],
                        plan_count=len(unified_plans),
                        route=unified_decision.get("route"),
                    )
                    return unified_plans
        except Exception as unified_exc:
            emit_structured_log(
                "semantic_translator_unified_path_fallback",
                level="info",
                prompt=prompt[:180],
                error=str(unified_exc)[:200],
            )

    router_decision = route_prompt_with_semantic_router(
        prompt, list(columns or []),
        schema_profile=schema_profile, dataset_contract=dataset_contract,
    )
    use_simple_runtime = router_decision.get("route") == "SIMPLE"
    force_deep_planner = False

    if use_simple_runtime:
        # [TIER 0 2026-09 firebreak] Un fallo al construir/enriquecer el contrato
        # SIMPLE degrada al planificador profundo en vez de matar la tarea.
        try:
            fast_path_plans = build_plan_from_router_contract(
                router_decision, list(columns or []),
                schema_profile=schema_profile, dataset_contract=dataset_contract,
                candidate_df=candidate_df,
            )
            if fast_path_plans:
                # [V2.3] Enrich SIMPLE path with complementary dashboard views.
                # Deterministic Triple Vista: add distribution + KPI (or trend + KPI)
                # so the user receives a complete analytical dashboard without the
                # latency of the LLM deep planner.
                enriched_plans = _build_complementary_dashboard_plans(
                    fast_path_plans, router_decision, list(columns or []),
                    schema_profile=schema_profile or {},
                    dataset_contract=dataset_contract or {},
                )
                emit_structured_log(
                    "semantic_translator_simple_contract_accepted",
                    prompt=prompt[:200], confidence=router_decision.get("confidence"),
                    detected_intent=router_decision.get("detected_intent"),
                    semantic_contract=router_decision.get("semantic_contract"),
                    plan_count=len(enriched_plans),
                    enriched=len(enriched_plans) > len(fast_path_plans),
                )
                return finalize_plans(enriched_plans, schema_profile, dataset_contract=dataset_contract, prompt=prompt)
        except Exception as simple_exc:
            emit_structured_log(
                "semantic_translator_simple_contract_error",
                level="error",
                prompt=prompt[:180],
                error_type=type(simple_exc).__name__,
                error=str(simple_exc)[:300],
            )
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
            return finalize_plans(dimension_bundle_plans, schema_profile, dataset_contract=dataset_contract, prompt=prompt)

        macro_bundle_plans = build_macro_analysis_bundle(
            prompt, list(columns or []),
            schema_profile=schema_profile, dataset_contract=dataset_contract,
            candidate_df=candidate_df,
        )
        if macro_bundle_plans:
            result = finalize_plans(macro_bundle_plans, schema_profile, dataset_contract=dataset_contract, prompt=prompt)
            if macro_bundle_plans and macro_bundle_plans[0].coverage_metadata:
                result[0].coverage_metadata = macro_bundle_plans[0].coverage_metadata
            return result
    elif router_decision.get("route") == "COMPLEJO":
        reason_codes = router_decision.get("reason_codes") or []
        if "broad_analysis" in reason_codes:
            macro_bundle_plans = build_macro_analysis_bundle(
                prompt, list(columns or []),
                schema_profile=schema_profile, dataset_contract=dataset_contract,
                candidate_df=candidate_df,
            )
            if macro_bundle_plans:
                result = finalize_plans(macro_bundle_plans, schema_profile, dataset_contract=dataset_contract, prompt=prompt)
                if macro_bundle_plans and macro_bundle_plans[0].coverage_metadata:
                    result[0].coverage_metadata = macro_bundle_plans[0].coverage_metadata
                emit_structured_log(
                    "semantic_translator_broad_deterministic_accepted",
                    prompt=prompt[:200],
                    plan_count=len(result),
                    reason_codes=reason_codes,
                )
                return result

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
            return finalize_plans(restored_plans, schema_profile, dataset_contract=dataset_contract, prompt=prompt)
        except Exception as cache_restore_error:
            emit_structured_log(
                "semantic_translator_cache_restore_error",
                level="warning", error=str(cache_restore_error)[:200],
            )

    schema_json = json.dumps(AnalysisPlan.model_json_schema(), separators=(",", ":"), ensure_ascii=False)
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

     6. 📊 TRIPLE VISTA (Dashboard Automático) — [FASE 3C], [V2.2 PRIORIDAD]:
        - REGLA DE ORO: El Plan 1 DEBE responder EXACTAMENTE la pregunta principal del usuario.
          Los planes 2 y 3 son complementarios y NUNCA deben sacrificar la calidad del Plan 1.
        - Si el prompt contiene "tendencia", "evolución", "año tras año", "período", "mensual",
          "temporal" o similar, el Plan 1 DEBE ser de tipo "trend" con visual_protocol "line_chart".
        - Para análisis generales, debes generar obligatoriamente 3 planes complementarios:
          1) Vista Principal: El análisis EXACTO que pidió el usuario (OBLIGATORIO, siempre presente).
          2) Vista Complementaria (OPCIONAL): Un análisis que ENRIQUEZCA el primero con otra perspectiva.
             Ej: si el principal es "trend" → complementa con "distribution" del top.
             Ej: si el principal es "descriptive" → complemento con "trend" de la métrica.
          3) Vista Diagnóstica (OPCIONAL): Análisis que revele CAUSAS o ANOMALÍAS.
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

      10. 🎯 DIVERSIDAD OBLIGATORIA EN TRIPLE VISTA — [V2.3]:
        - CUANDO generes 3 planes, se SUGIERE diversificar visual_protocol entre ellos
          (evitar 3 line_chart idénticos). Sin embargo, la REGLA DE ORO (Plan 1 = respuesta
          principal) PREVALECE sobre la diversidad visual.
        - Si el Plan 1 requiere line_chart (por ser tendencia), está BIEN que Plan 1 y Plan 2
          tengan line_chart siempre que respondan preguntas diferentes.
        - DIVERSIFICA métricas y dimensiones entre planes, no solo el título.

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

     13. 🚫 PROHIBICIÓN DE SÍMBOLOS MONETARIOS EN MÉTRICAS DE CONTEO:
        - PROHIBIDO usar símbolos monetarios (S/, $, €, etc.) en métricas que sean
          conteos de registros, id_empleado, cantidad de personas, o cualquier
          agregación COUNT/distinct count.
        - Las columnas con valores numéricos que representan escalas (como
          'nivel_desempeno', 'puntuacion', 'rating', 'score') son escalas numéricas,
          NO montos monetarios. No uses S/, $, € para ellas.
        - REGLA: solo usa símbolo monetario si la TOPOLOGÍA o el GLOSARIO indican
          explícitamente que la columna es de tipo currency/dinero.

     14. 📝 TÍTULOS EN LENGUAJE NATURAL (No nombres de columna crudos):
        - Los títulos de los planes DEBEN ser frases en lenguaje natural que el
          usuario entienda de inmediato, NO concatenaciones de nombres técnicos.
        - Ejemplo correcto: "Evolución del Desempeño en Ventas"
        - Ejemplo incorrecto: "Tendencia de nivel_desempeno por departamento"
        - Usa los alias humanizados del `column_aliases` para construir los títulos.
        - Si el alias no existe, infiere un nombre natural a partir del contexto.
        - EVITA a toda costa mostrar nombres de columnas crudos (con guiones bajos
          o nombres técnicos) en los títulos.

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
            # ═══════════════════════════════════════════════════════════
            # COMPLEJO→SIMPLE fallback: el LLM generó planes inválidos
            # (Pydantic rechazó todos). Intentar construir un plan
            # determinista desde el router_contract usando columnas
            # REALES del dataset (no alucinadas por el LLM).
            # ═══════════════════════════════════════════════════════════
            router_contract_plans = build_plan_from_router_contract(
                router_decision, list(columns or []),
                schema_profile=schema_profile, dataset_contract=dataset_contract,
                candidate_df=candidate_df,
            )
            if router_contract_plans:
                set_cached_json(
                    "semantic_translator", translator_cache_key,
                    [plan.model_dump(mode="json") for plan in router_contract_plans],
                    settings.SEMANTIC_TRANSLATOR_CACHE_TTL_SECONDS,
                )
                emit_structured_log(
                    "semantic_translator_hallucination_fallback_to_router_contract",
                    prompt=prompt[:200], primary_model=primary_model_name,
                    plan_count=len(router_contract_plans),
                    reason="all_llm_plans_invalid",
                )
                return finalize_plans(router_contract_plans, schema_profile, dataset_contract=dataset_contract, prompt=prompt)
            emit_structured_log(
                "semantic_translator_no_plans",
                level="error",
                prompt=prompt[:200],
                reason="empty_llm_and_no_router_contract",
                primary_model=primary_model_name,
            )
            return None

        set_cached_json(
            "semantic_translator", translator_cache_key,
            [plan.model_dump(mode="json") for plan in plans],
            settings.SEMANTIC_TRANSLATOR_CACHE_TTL_SECONDS,
        )
        return finalize_plans(plans, schema_profile, dataset_contract=dataset_contract, prompt=prompt)

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
                    return finalize_plans(router_contract_plans, schema_profile, dataset_contract=dataset_contract, prompt=prompt)

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
                        return finalize_plans(fallback_plans, schema_profile, dataset_contract=dataset_contract, prompt=prompt)
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
                return finalize_plans(router_contract_plans, schema_profile, dataset_contract=dataset_contract, prompt=prompt)

        emit_structured_log(
            "semantic_translator_no_plans",
            level="error",
            prompt=prompt[:200],
            reason="terminal_no_recoverable_path",
            error_type=type(e).__name__,
            error=str(e)[:300],
            recoverable=is_recoverable_translator_model_error(e),
        )
        return None
