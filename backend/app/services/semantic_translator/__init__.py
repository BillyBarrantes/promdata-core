from typing import Any, Optional, List, Dict

from app.core.semantic_grammar import AnalysisPlan, DataFilter
from app.services.semantic_translator.planner import translate
from app.services.semantic_translator.memory import (
    should_bypass_memory_context,
    is_visual_replacement_request,
    classify_memory_intent,
    detect_literal_filters,
    evaluate_continuity,
)
from app.services.semantic_translator.validator import (
    extract_json_code_block,
    split_json_documents,
    parse_translator_payload,
    is_recoverable_translator_model_error,
    is_quota_translator_model_error,
    select_translator_fallback_model,
    sanitize_translator_payload_item,
    plans_from_translator_payload,
    generate_translator_plans_with_model,
    schema_fingerprint,
    normalize_semantic_router_decision,
    normalize_router_semantic_contract,
    infer_default_metric_column,
    resolve_contract_column,
    normalize_router_filters,
    finalize_plans,
    apply_direction_guard_to_distribution_plans,
    detect_prompt_complexity,
    fast_path_unresolved_constraints,
)
from app.services.semantic_translator.router import route_prompt_with_semantic_router
from app.services.semantic_translator.core import (
    _DIMENSION_SEMANTIC_GROUPS,
    SEMANTIC_ROUTER_CONFIDENCE_THRESHOLD,
    SEMANTIC_ROUTER_COMPLEX_REASON_CODES,
    normalize_surface_text,
    humanize_column_alias,
    semantic_groups_for_text,
    dimension_semantic_alignment_score,
    should_default_to_latest_snapshot,
    build_default_latest_snapshot_filters,
    extract_axis_segment,
    resolve_segment_columns,
    extract_top_limit,
    is_top_n_rollup_request,
    mentions_generic_visual_request,
    contains_explicit_continuity_marker,
    mentions_temporal_language,
    contains_analysis_language,
    extract_primary_dimension_segment,
    pick_primary_date_column,
    pick_best_dimension_column,
    looks_broad_analysis_request,
    looks_dimension_analysis_request,
    has_meaningful_temporal_axis,
    looks_self_contained_visual_request,
    apply_top_n_rollup_mode_to_plans,
)
from app.services.semantic_translator.planner import (
    select_default_distribution_visual,
    select_alternate_distribution_visual,
    build_plan_from_router_contract,
    build_dimension_analysis_bundle,
    build_macro_analysis_bundle,
    build_explicit_scatter_plan,
    build_explicit_trend_plan,
    build_explicit_distribution_plan,
    build_deterministic_visual_plan,
)


class SemanticTranslator:
    SEMANTIC_ROUTER_CONFIDENCE_THRESHOLD = SEMANTIC_ROUTER_CONFIDENCE_THRESHOLD
    SEMANTIC_ROUTER_COMPLEX_REASON_CODES = SEMANTIC_ROUTER_COMPLEX_REASON_CODES

    _extract_json_code_block = staticmethod(extract_json_code_block)
    _split_json_documents = staticmethod(split_json_documents)
    _parse_translator_payload = staticmethod(parse_translator_payload)
    _is_recoverable_translator_model_error = staticmethod(is_recoverable_translator_model_error)
    _is_quota_translator_model_error = staticmethod(is_quota_translator_model_error)
    _select_translator_fallback_model = staticmethod(select_translator_fallback_model)
    _sanitize_translator_payload_item = staticmethod(sanitize_translator_payload_item)
    _plans_from_translator_payload = staticmethod(plans_from_translator_payload)
    _generate_translator_plans_with_model = staticmethod(generate_translator_plans_with_model)
    _schema_fingerprint = staticmethod(schema_fingerprint)
    _normalize_semantic_router_decision = staticmethod(normalize_semantic_router_decision)
    _normalize_router_semantic_contract = staticmethod(normalize_router_semantic_contract)
    _route_prompt_with_semantic_router = staticmethod(route_prompt_with_semantic_router)
    _normalize_surface_text = staticmethod(normalize_surface_text)
    _humanize_column_alias = staticmethod(humanize_column_alias)
    _semantic_groups_for_text = staticmethod(semantic_groups_for_text)
    _dimension_semantic_alignment_score = staticmethod(dimension_semantic_alignment_score)
    _should_default_to_latest_snapshot = staticmethod(should_default_to_latest_snapshot)
    _build_default_latest_snapshot_filters = staticmethod(build_default_latest_snapshot_filters)
    _extract_axis_segment = staticmethod(extract_axis_segment)
    _resolve_segment_columns = staticmethod(resolve_segment_columns)
    _extract_top_limit = staticmethod(extract_top_limit)
    _is_top_n_rollup_request = staticmethod(is_top_n_rollup_request)
    _mentions_generic_visual_request = staticmethod(mentions_generic_visual_request)
    _contains_explicit_continuity_marker = staticmethod(contains_explicit_continuity_marker)
    _mentions_temporal_language = staticmethod(mentions_temporal_language)
    _contains_analysis_language = staticmethod(contains_analysis_language)
    _infer_default_metric_column = staticmethod(infer_default_metric_column)
    _resolve_contract_column = staticmethod(resolve_contract_column)
    _normalize_router_filters = staticmethod(normalize_router_filters)
    _build_plan_from_router_contract = staticmethod(build_plan_from_router_contract)
    _select_default_distribution_visual = staticmethod(select_default_distribution_visual)
    _select_alternate_distribution_visual = staticmethod(select_alternate_distribution_visual)
    _looks_broad_analysis_request = staticmethod(looks_broad_analysis_request)
    _extract_primary_dimension_segment = staticmethod(extract_primary_dimension_segment)
    _pick_primary_date_column = staticmethod(pick_primary_date_column)
    _pick_best_dimension_column = staticmethod(pick_best_dimension_column)
    _looks_dimension_analysis_request = staticmethod(looks_dimension_analysis_request)
    _has_meaningful_temporal_axis = staticmethod(has_meaningful_temporal_axis)
    _build_dimension_analysis_bundle = staticmethod(build_dimension_analysis_bundle)
    _build_macro_analysis_bundle = staticmethod(build_macro_analysis_bundle)
    _looks_self_contained_visual_request = staticmethod(looks_self_contained_visual_request)
    _build_explicit_scatter_plan = staticmethod(build_explicit_scatter_plan)
    _build_explicit_trend_plan = staticmethod(build_explicit_trend_plan)
    _build_explicit_distribution_plan = staticmethod(build_explicit_distribution_plan)
    _build_deterministic_visual_plan = staticmethod(build_deterministic_visual_plan)
    _apply_top_n_rollup_mode_to_plans = staticmethod(apply_top_n_rollup_mode_to_plans)
    _detect_prompt_complexity = staticmethod(detect_prompt_complexity)
    _fast_path_unresolved_constraints = staticmethod(fast_path_unresolved_constraints)
    _finalize_plans = staticmethod(finalize_plans)
    _apply_direction_guard_to_distribution_plans = staticmethod(apply_direction_guard_to_distribution_plans)
    _classify_memory_intent = staticmethod(classify_memory_intent)
    _detect_literal_filters = staticmethod(detect_literal_filters)
    should_bypass_memory_context = staticmethod(should_bypass_memory_context)
    is_visual_replacement_request = staticmethod(is_visual_replacement_request)
    evaluate_continuity = staticmethod(evaluate_continuity)
    translate = staticmethod(translate)
