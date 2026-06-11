from app.core.gemini_client import genai
from app.core.langfuse_client import record_llm_call
import json
import re
import unicodedata
from json import JSONDecoder, JSONDecodeError
from typing import Any, List, Optional, Dict
from app.core.config import settings
from app.core.semantic_grammar import (
    AnalysisPlan,
    DataFilter,
    DescriptiveIntent,
    DiagnosticIntent,
    DistributionIntent,
    FilterOperator,
    MetricPolarity,
    MetricUnit,
    TimeTrendIntent,
    VisualProtocol,
)
from app.core.structured_logging import emit_structured_log
from app.services.ai_response_cache import build_cache_key, get_cached_json, set_cached_json
from app.services.metric_semantics import infer_metric_unit_from_column_name, normalize_semantic_text
from app.services.visual_recommendation_engine import extract_prompt_visual_requests

# @deprecated("Eliminado por cirugía de sesgos domain-agnostic — mayo 2026")
# Las agrupaciones semánticas hardcodeadas generaban favoritismo hacia
# dominios logísticos (almacen, warehouse, lote) y penalizaban otros
# dominios (RRHH, finanzas, transporte). El SemanticTranslator ahora
# depende estrictamente del canonical_schema_profiler: cardinalidad,
# dtype y densidad determinan el rol de cada columna.
_DIMENSION_SEMANTIC_GROUPS: dict[str, set[str]] = {}

class SemanticTranslator:
    """
    El 'Estratega de Protocolos' V6. 
    Capacidades: Traducción + Router de Continuidad + INYECCIÓN DE PROTOCOLOS ANALÍTICOS.
    """

    SEMANTIC_ROUTER_CONFIDENCE_THRESHOLD = 0.85
    SEMANTIC_ROUTER_COMPLEX_REASON_CODES = {
        "ambiguous",
        "broad_analysis",
        "comparison",
        "consolidation",
        "contradiction",
        "exclusion",
        "exclusion_logic",
        "mixed_intent",
        "negation",
        "per_item",
        "ranking_metric_mismatch",
        "restriction",
        "requires_planner",
        "top_n_rollup",
    }

    @staticmethod
    def _extract_json_code_block(*args, **kwargs):
        # [REFACTOR 2026-06-11] Delegador. La logica vive en
        # app.services.semantic_translator.validator.extract_json_code_block.
        from app.services.semantic_translator.validator import extract_json_code_block as _impl
        return _impl(SemanticTranslator, *args, **kwargs)
    @staticmethod
    def _split_json_documents(*args, **kwargs):
        # [REFACTOR 2026-06-11] Delegador. La logica vive en
        # app.services.semantic_translator.validator.split_json_documents.
        from app.services.semantic_translator.validator import split_json_documents as _impl
        return _impl(SemanticTranslator, *args, **kwargs)
    @staticmethod
    def _parse_translator_payload(*args, **kwargs):
        # [REFACTOR 2026-06-11] Delegador. La logica vive en
        # app.services.semantic_translator.validator.parse_translator_payload.
        from app.services.semantic_translator.validator import parse_translator_payload as _impl
        return _impl(SemanticTranslator, *args, **kwargs)
    @staticmethod
    def _is_recoverable_translator_model_error(*args, **kwargs):
        # [REFACTOR 2026-06-11] Delegador. La logica vive en
        # app.services.semantic_translator.validator.is_recoverable_translator_model_error.
        from app.services.semantic_translator.validator import is_recoverable_translator_model_error as _impl
        return _impl(SemanticTranslator, *args, **kwargs)
    @staticmethod
    def _is_quota_translator_model_error(*args, **kwargs):
        # [REFACTOR 2026-06-11] Delegador. La logica vive en
        # app.services.semantic_translator.validator.is_quota_translator_model_error.
        from app.services.semantic_translator.validator import is_quota_translator_model_error as _impl
        return _impl(SemanticTranslator, *args, **kwargs)
    @staticmethod
    def _select_translator_fallback_model(*args, **kwargs):
        # [REFACTOR 2026-06-11] Delegador. La logica vive en
        # app.services.semantic_translator.validator.select_translator_fallback_model.
        from app.services.semantic_translator.validator import select_translator_fallback_model as _impl
        return _impl(SemanticTranslator, *args, **kwargs)
    @staticmethod
    def _sanitize_translator_payload_item(*args, **kwargs):
        # [REFACTOR 2026-06-11] Delegador. La logica vive en
        # app.services.semantic_translator.validator.sanitize_translator_payload_item.
        from app.services.semantic_translator.validator import sanitize_translator_payload_item as _impl
        return _impl(SemanticTranslator, *args, **kwargs)
    @staticmethod
    def _plans_from_translator_payload(*args, **kwargs):
        # [REFACTOR 2026-06-11] Delegador. La logica vive en
        # app.services.semantic_translator.validator.plans_from_translator_payload.
        from app.services.semantic_translator.validator import plans_from_translator_payload as _impl
        return _impl(SemanticTranslator, *args, **kwargs)
    @staticmethod
    def _generate_translator_plans_with_model(*args, **kwargs):
        # [REFACTOR 2026-06-11] Delegador. La logica vive en
        # app.services.semantic_translator.validator.generate_translator_plans_with_model.
        from app.services.semantic_translator.validator import generate_translator_plans_with_model as _impl
        return _impl(SemanticTranslator, *args, **kwargs)
    @staticmethod
    def _schema_fingerprint(*args, **kwargs):
        # [REFACTOR 2026-06-11] Delegador. La logica vive en
        # app.services.semantic_translator.planner.schema_fingerprint.
        from app.services.semantic_translator.planner import schema_fingerprint as _impl
        return _impl(SemanticTranslator, *args, **kwargs)
    @staticmethod
    def _normalize_semantic_router_decision(*args, **kwargs):
        # [REFACTOR 2026-06-11] Delegador. La logica vive en
        # app.services.semantic_translator.planner.normalize_semantic_router_decision.
        from app.services.semantic_translator.planner import normalize_semantic_router_decision as _impl
        return _impl(SemanticTranslator, *args, **kwargs)
    @staticmethod
    def _normalize_router_semantic_contract(*args, **kwargs):
        # [REFACTOR 2026-06-11] Delegador. La logica vive en
        # app.services.semantic_translator.planner.normalize_router_semantic_contract.
        from app.services.semantic_translator.planner import normalize_router_semantic_contract as _impl
        return _impl(SemanticTranslator, *args, **kwargs)
    @staticmethod
    def _route_prompt_with_semantic_router(*args, **kwargs):
        # [REFACTOR 2026-06-11] Delegador. La logica vive en
        # app.services.semantic_translator.planner.route_prompt_with_semantic_router.
        from app.services.semantic_translator.planner import route_prompt_with_semantic_router as _impl
        return _impl(SemanticTranslator, *args, **kwargs)
    @staticmethod
    def _normalize_surface_text(*args, **kwargs):
        # [REFACTOR 2026-06-11] Delegador. La logica vive en
        # app.services.semantic_translator.router.normalize_surface_text.
        from app.services.semantic_translator.router import normalize_surface_text as _impl
        return _impl(SemanticTranslator, *args, **kwargs)
    @staticmethod
    def _humanize_column_alias(*args, **kwargs):
        # [REFACTOR 2026-06-11] Delegador. La logica vive en
        # app.services.semantic_translator.router.humanize_column_alias.
        from app.services.semantic_translator.router import humanize_column_alias as _impl
        return _impl(SemanticTranslator, *args, **kwargs)
    @staticmethod
    def _semantic_groups_for_text(*args, **kwargs):
        # [REFACTOR 2026-06-11] Delegador. La logica vive en
        # app.services.semantic_translator.router.semantic_groups_for_text.
        from app.services.semantic_translator.router import semantic_groups_for_text as _impl
        return _impl(SemanticTranslator, *args, **kwargs)
    @staticmethod
    def _dimension_semantic_alignment_score(*args, **kwargs):
        # [REFACTOR 2026-06-11] Delegador. La logica vive en
        # app.services.semantic_translator.router.dimension_semantic_alignment_score.
        from app.services.semantic_translator.router import dimension_semantic_alignment_score as _impl
        return _impl(SemanticTranslator, *args, **kwargs)
    @staticmethod
    def _should_default_to_latest_snapshot(*args, **kwargs):
        # [REFACTOR 2026-06-11] Delegador. La logica vive en
        # app.services.semantic_translator.planner.should_default_to_latest_snapshot.
        from app.services.semantic_translator.planner import should_default_to_latest_snapshot as _impl
        return _impl(SemanticTranslator, *args, **kwargs)
    @staticmethod
    def _build_default_latest_snapshot_filters(*args, **kwargs):
        # [REFACTOR 2026-06-11] Delegador. La logica vive en
        # app.services.semantic_translator.planner.build_default_latest_snapshot_filters.
        from app.services.semantic_translator.planner import build_default_latest_snapshot_filters as _impl
        return _impl(SemanticTranslator, *args, **kwargs)
    @staticmethod
    def _extract_axis_segment(*args, **kwargs):
        # [REFACTOR 2026-06-11] Delegador. La logica vive en
        # app.services.semantic_translator.router.extract_axis_segment.
        from app.services.semantic_translator.router import extract_axis_segment as _impl
        return _impl(SemanticTranslator, *args, **kwargs)
    @staticmethod
    def _resolve_segment_columns(*args, **kwargs):
        # [REFACTOR 2026-06-11] Delegador. La logica vive en
        # app.services.semantic_translator.router.resolve_segment_columns.
        from app.services.semantic_translator.router import resolve_segment_columns as _impl
        return _impl(SemanticTranslator, *args, **kwargs)
    @staticmethod
    def _extract_top_limit(*args, **kwargs):
        # [REFACTOR 2026-06-11] Delegador. La logica vive en
        # app.services.semantic_translator.router.extract_top_limit.
        from app.services.semantic_translator.router import extract_top_limit as _impl
        return _impl(SemanticTranslator, *args, **kwargs)
    @staticmethod
    def _is_top_n_rollup_request(*args, **kwargs):
        # [REFACTOR 2026-06-11] Delegador. La logica vive en
        # app.services.semantic_translator.router.is_top_n_rollup_request.
        from app.services.semantic_translator.router import is_top_n_rollup_request as _impl
        return _impl(SemanticTranslator, *args, **kwargs)
    @staticmethod
    def _mentions_generic_visual_request(*args, **kwargs):
        # [REFACTOR 2026-06-11] Delegador. La logica vive en
        # app.services.semantic_translator.router.mentions_generic_visual_request.
        from app.services.semantic_translator.router import mentions_generic_visual_request as _impl
        return _impl(SemanticTranslator, *args, **kwargs)
    @staticmethod
    def _contains_explicit_continuity_marker(*args, **kwargs):
        # [REFACTOR 2026-06-11] Delegador. La logica vive en
        # app.services.semantic_translator.router.contains_explicit_continuity_marker.
        from app.services.semantic_translator.router import contains_explicit_continuity_marker as _impl
        return _impl(SemanticTranslator, *args, **kwargs)
    @staticmethod
    def _mentions_temporal_language(*args, **kwargs):
        # [REFACTOR 2026-06-11] Delegador. La logica vive en
        # app.services.semantic_translator.router.mentions_temporal_language.
        from app.services.semantic_translator.router import mentions_temporal_language as _impl
        return _impl(SemanticTranslator, *args, **kwargs)
    @staticmethod
    def _contains_analysis_language(*args, **kwargs):
        # [REFACTOR 2026-06-11] Delegador. La logica vive en
        # app.services.semantic_translator.router.contains_analysis_language.
        from app.services.semantic_translator.router import contains_analysis_language as _impl
        return _impl(SemanticTranslator, *args, **kwargs)
    @staticmethod
    def _infer_default_metric_column(*args, **kwargs):
        # [REFACTOR 2026-06-11] Delegador. La logica vive en
        # app.services.semantic_translator.router.infer_default_metric_column.
        from app.services.semantic_translator.router import infer_default_metric_column as _impl
        return _impl(SemanticTranslator, *args, **kwargs)
    @staticmethod
    def _resolve_contract_column(*args, **kwargs):
        # [REFACTOR 2026-06-11] Delegador. La logica vive en
        # app.services.semantic_translator.planner.resolve_contract_column.
        from app.services.semantic_translator.planner import resolve_contract_column as _impl
        return _impl(SemanticTranslator, *args, **kwargs)
    @staticmethod
    def _normalize_router_filters(*args, **kwargs):
        # [REFACTOR 2026-06-11] Delegador. La logica vive en
        # app.services.semantic_translator.planner.normalize_router_filters.
        from app.services.semantic_translator.planner import normalize_router_filters as _impl
        return _impl(SemanticTranslator, *args, **kwargs)
    @staticmethod
    def _build_plan_from_router_contract(*args, **kwargs):
        # [REFACTOR 2026-06-11] Delegador. La logica vive en
        # app.services.semantic_translator.planner.build_plan_from_router_contract.
        from app.services.semantic_translator.planner import build_plan_from_router_contract as _impl
        return _impl(SemanticTranslator, *args, **kwargs)
    @staticmethod
    def _select_default_distribution_visual(*args, **kwargs):
        # [REFACTOR 2026-06-11] Delegador. La logica vive en
        # app.services.semantic_translator.router.select_default_distribution_visual.
        from app.services.semantic_translator.router import select_default_distribution_visual as _impl
        return _impl(SemanticTranslator, *args, **kwargs)
    @staticmethod
    def _select_alternate_distribution_visual(*args, **kwargs):
        # [REFACTOR 2026-06-11] Delegador. La logica vive en
        # app.services.semantic_translator.router.select_alternate_distribution_visual.
        from app.services.semantic_translator.router import select_alternate_distribution_visual as _impl
        return _impl(SemanticTranslator, *args, **kwargs)
    @staticmethod
    def _looks_broad_analysis_request(*args, **kwargs):
        # [REFACTOR 2026-06-11] Delegador. La logica vive en
        # app.services.semantic_translator.router.looks_broad_analysis_request.
        from app.services.semantic_translator.router import looks_broad_analysis_request as _impl
        return _impl(SemanticTranslator, *args, **kwargs)
    @staticmethod
    def _extract_primary_dimension_segment(*args, **kwargs):
        # [REFACTOR 2026-06-11] Delegador. La logica vive en
        # app.services.semantic_translator.router.extract_primary_dimension_segment.
        from app.services.semantic_translator.router import extract_primary_dimension_segment as _impl
        return _impl(SemanticTranslator, *args, **kwargs)
    @staticmethod
    def _pick_primary_date_column(*args, **kwargs):
        # [REFACTOR 2026-06-11] Delegador. La logica vive en
        # app.services.semantic_translator.planner.pick_primary_date_column.
        from app.services.semantic_translator.planner import pick_primary_date_column as _impl
        return _impl(SemanticTranslator, *args, **kwargs)
    @staticmethod
    def _pick_best_dimension_column(*args, **kwargs):
        # [REFACTOR 2026-06-11] Delegador. La logica vive en
        # app.services.semantic_translator.planner.pick_best_dimension_column.
        from app.services.semantic_translator.planner import pick_best_dimension_column as _impl
        return _impl(SemanticTranslator, *args, **kwargs)
    @staticmethod
    def _looks_dimension_analysis_request(*args, **kwargs):
        # [REFACTOR 2026-06-11] Delegador. La logica vive en
        # app.services.semantic_translator.router.looks_dimension_analysis_request.
        from app.services.semantic_translator.router import looks_dimension_analysis_request as _impl
        return _impl(SemanticTranslator, *args, **kwargs)
    @staticmethod
    def _has_meaningful_temporal_axis(*args, **kwargs):
        # [REFACTOR 2026-06-11] Delegador. La logica vive en
        # app.services.semantic_translator.router.has_meaningful_temporal_axis.
        from app.services.semantic_translator.router import has_meaningful_temporal_axis as _impl
        return _impl(SemanticTranslator, *args, **kwargs)
    @staticmethod
    def _build_dimension_analysis_bundle(*args, **kwargs):
        # [REFACTOR 2026-06-11] Delegador. La logica vive en
        # app.services.semantic_translator.planner.build_dimension_analysis_bundle.
        from app.services.semantic_translator.planner import build_dimension_analysis_bundle as _impl
        return _impl(SemanticTranslator, *args, **kwargs)
    @staticmethod
    def _build_macro_analysis_bundle(*args, **kwargs):
        # [REFACTOR 2026-06-11] Delegador. La logica vive en
        # app.services.semantic_translator.planner.build_macro_analysis_bundle.
        from app.services.semantic_translator.planner import build_macro_analysis_bundle as _impl
        return _impl(SemanticTranslator, *args, **kwargs)
    @staticmethod
    def _looks_self_contained_visual_request(*args, **kwargs):
        # [REFACTOR 2026-06-11] Delegador. La logica vive en
        # app.services.semantic_translator.router.looks_self_contained_visual_request.
        from app.services.semantic_translator.router import looks_self_contained_visual_request as _impl
        return _impl(SemanticTranslator, *args, **kwargs)
    @staticmethod
    def _build_explicit_scatter_plan(*args, **kwargs):
        # [REFACTOR 2026-06-11] Delegador. La logica vive en
        # app.services.semantic_translator.planner.build_explicit_scatter_plan.
        from app.services.semantic_translator.planner import build_explicit_scatter_plan as _impl
        return _impl(SemanticTranslator, *args, **kwargs)
    @staticmethod
    def _build_explicit_trend_plan(*args, **kwargs):
        # [REFACTOR 2026-06-11] Delegador. La logica vive en
        # app.services.semantic_translator.planner.build_explicit_trend_plan.
        from app.services.semantic_translator.planner import build_explicit_trend_plan as _impl
        return _impl(SemanticTranslator, *args, **kwargs)
    @staticmethod
    def _build_explicit_distribution_plan(*args, **kwargs):
        # [REFACTOR 2026-06-11] Delegador. La logica vive en
        # app.services.semantic_translator.planner.build_explicit_distribution_plan.
        from app.services.semantic_translator.planner import build_explicit_distribution_plan as _impl
        return _impl(SemanticTranslator, *args, **kwargs)
    @staticmethod
    def _build_deterministic_visual_plan(*args, **kwargs):
        # [REFACTOR 2026-06-11] Delegador. La logica vive en
        # app.services.semantic_translator.planner.build_deterministic_visual_plan.
        from app.services.semantic_translator.planner import build_deterministic_visual_plan as _impl
        return _impl(SemanticTranslator, *args, **kwargs)
    @staticmethod
    def _apply_top_n_rollup_mode_to_plans(*args, **kwargs):
        # [REFACTOR 2026-06-11] Delegador. La logica vive en
        # app.services.semantic_translator.planner.apply_top_n_rollup_mode_to_plans.
        from app.services.semantic_translator.planner import apply_top_n_rollup_mode_to_plans as _impl
        return _impl(SemanticTranslator, *args, **kwargs)
    @staticmethod
    def _detect_prompt_complexity(*args, **kwargs):
        # [REFACTOR 2026-06-11] Delegador. La logica vive en
        # app.services.semantic_translator.router.detect_prompt_complexity.
        from app.services.semantic_translator.router import detect_prompt_complexity as _impl
        return _impl(SemanticTranslator, *args, **kwargs)
    @staticmethod
    def _fast_path_unresolved_constraints(*args, **kwargs):
        # [REFACTOR 2026-06-11] Delegador. La logica vive en
        # app.services.semantic_translator.router.fast_path_unresolved_constraints.
        from app.services.semantic_translator.router import fast_path_unresolved_constraints as _impl
        return _impl(SemanticTranslator, *args, **kwargs)
    @staticmethod
    def should_bypass_memory_context(
        prompt: str,
        columns: list[str],
        schema_profile: dict | None = None,
    ) -> bool:
        if not settings.DETERMINISTIC_VISUAL_FASTPATH_ENABLED:
            return False

        surface_prompt = SemanticTranslator._normalize_surface_text(prompt)
        if not surface_prompt:
            return False
        if SemanticTranslator.is_visual_replacement_request(prompt):
            return False
        if SemanticTranslator._contains_explicit_continuity_marker(surface_prompt):
            return False

        requested_visuals = extract_prompt_visual_requests(prompt)
        if not requested_visuals and not SemanticTranslator._mentions_generic_visual_request(surface_prompt):
            return False

        schema_profile = schema_profile or {}
        if "scatter_plot" in requested_visuals:
            return bool(
                SemanticTranslator._extract_axis_segment(surface_prompt, "x")
                and SemanticTranslator._extract_axis_segment(surface_prompt, "y")
            )

        dimension_segment = None
        por_match = re.search(r"\bpor\s+(.+?)(?=$|,)", surface_prompt, flags=re.IGNORECASE)
        if por_match:
            dimension_segment = por_match.group(1)

        dimension_candidates = SemanticTranslator._resolve_segment_columns(
            dimension_segment or surface_prompt,
            columns,
            schema_profile=schema_profile,
            allowed_roles={"dimension", "identifier"},
        )
        date_candidates = SemanticTranslator._resolve_segment_columns(
            surface_prompt,
            columns,
            schema_profile=schema_profile,
            allowed_roles={"date"},
        )
        metric_candidates = SemanticTranslator._resolve_segment_columns(
            surface_prompt,
            columns,
            schema_profile=schema_profile,
            allowed_roles={"metric"},
        )
        if not metric_candidates:
            default_metric = SemanticTranslator._infer_default_metric_column(
                surface_prompt,
                columns,
                schema_profile=schema_profile,
            )
            if default_metric:
                metric_candidates = [default_metric]

        if requested_visuals and requested_visuals[0] in {"line_chart", "area_chart"}:
            return bool(date_candidates and metric_candidates)

        return bool((dimension_candidates and metric_candidates) or (date_candidates and metric_candidates))
    
    @staticmethod
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
    ) -> Optional[List[AnalysisPlan]]:
        router_decision = SemanticTranslator._route_prompt_with_semantic_router(
            prompt,
            list(columns or []),
            schema_profile=schema_profile,
            dataset_contract=dataset_contract,
        )
        use_simple_runtime = router_decision.get("route") == "SIMPLE"
        force_deep_planner = False

        if use_simple_runtime:
            fast_path_plans = SemanticTranslator._build_plan_from_router_contract(
                router_decision,
                list(columns or []),
                schema_profile=schema_profile,
                dataset_contract=dataset_contract,
            )
            if fast_path_plans:
                emit_structured_log(
                    "semantic_translator_simple_contract_accepted",
                    prompt=prompt[:200],
                    confidence=router_decision.get("confidence"),
                    detected_intent=router_decision.get("detected_intent"),
                    semantic_contract=router_decision.get("semantic_contract"),
                    plan_count=len(fast_path_plans),
                )
                return fast_path_plans
            force_deep_planner = True
            emit_structured_log(
                "semantic_translator_simple_contract_delegated",
                prompt=prompt[:200],
                router_decision=router_decision,
            )
        else:
            emit_structured_log(
                "semantic_translator_complex_route_selected",
                prompt=prompt[:200],
                confidence=router_decision.get("confidence"),
                detected_intent=router_decision.get("detected_intent"),
                reason_codes=router_decision.get("reason_codes"),
            )
            force_deep_planner = True

        if not force_deep_planner:
            dimension_bundle_plans = SemanticTranslator._build_dimension_analysis_bundle(
                prompt,
                list(columns or []),
                schema_profile=schema_profile,
                dataset_contract=dataset_contract,
            )
            if dimension_bundle_plans:
                return dimension_bundle_plans

            macro_bundle_plans = SemanticTranslator._build_macro_analysis_bundle(
                prompt,
                list(columns or []),
                schema_profile=schema_profile,
                dataset_contract=dataset_contract,
            )
            if macro_bundle_plans:
                return macro_bundle_plans

        translator_cache_key = build_cache_key(
            "semantic_translator",
            {
                "prompt": prompt,
                "columns": list(columns or []),
                "glossary_context": glossary_context,
                "topology_context": topology_context,
                "memory_context": memory_context,
                "memory_instruction": memory_instruction,
                "format_instruction": format_instruction,
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
                    if isinstance(dataset_contract, dict)
                    else ""
                )
                _tx_metrics = []
                for _p in restored_plans:
                    _m = getattr(_p, "metric", None)
                    if _m:
                        _tx_metrics.append(str(_m))
                emit_structured_log(
                    "semantic_translator_cache_hit",
                    prompt=prompt[:200],
                    plan_count=len(restored_plans),
                    file_id=_tx_file_id or None,
                    plan_metrics=_tx_metrics[:5],
                    cache_key_prefix=translator_cache_key[:16],
                )
                print(f"⚡ [SEMANTIC TRANSLATOR CACHE] Hit ({len(restored_plans)} planes)")
                return restored_plans
            except Exception as cache_restore_error:
                emit_structured_log(
                    "semantic_translator_cache_restore_error",
                    level="warning",
                    error=str(cache_restore_error)[:200],
                )

        # 1. Configuración: Usamos el modelo más potente para que entienda la estrategia compleja
        schema_json = json.dumps(AnalysisPlan.model_json_schema(), indent=2)
        router_context_json = json.dumps(router_decision, ensure_ascii=False, sort_keys=True)
        primary_model_name = str(settings.AI_MODEL_NAME or "").strip()

        # 2. PROMPT DE PROTOCOLOS DINÁMICOS (CEREBRO V8 — Schema-Agnostic + Glossary Intelligence)
        system_instruction = f"""
        ERES EL ESTRATEGA DE DATOS SENIOR DE PROMDATA (BIG DATA ARCHITECT).
        
        TUS HERRAMIENTAS: 
        - COLUMNAS DISPONIBLES: {columns}
        - CONTEXTO GLOSARIO: {glossary_context}
        - TOPOLOGÍA (Tipos de Datos): {topology_context}
        - SEMANTIC_ROUTER_DECISION (CONTRATO DE ALTA PRIORIDAD): {router_context_json}

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
            plans = SemanticTranslator._generate_translator_plans_with_model(
                primary_model_name,
                _translator_input,
                list(columns or []),
            )
            
            if not plans:
                print("⚠️ [TRANSLATOR] No se pudo validar ningún plan.")
                return None
            
            set_cached_json(
                "semantic_translator",
                translator_cache_key,
                [plan.model_dump(mode="json") for plan in plans],
                settings.SEMANTIC_TRANSLATOR_CACHE_TTL_SECONDS,
            )
            print(f"🧠 [SEMANTIC KERNEL] {len(plans)} plan(es) validado(s)")
            return plans
            
        except Exception as e:
            if SemanticTranslator._is_recoverable_translator_model_error(e):
                emit_structured_log(
                    "semantic_translator_primary_model_recoverable_error",
                    level="warning",
                    error=str(e)[:300],
                    primary_model=primary_model_name,
                    fallback_model=SemanticTranslator._select_translator_fallback_model(primary_model_name),
                    reason_codes=router_decision.get("reason_codes"),
                )
                fallback_model_name = SemanticTranslator._select_translator_fallback_model(primary_model_name)
                quota_error = SemanticTranslator._is_quota_translator_model_error(e)
                router_contract_plans = None
                if quota_error:
                    router_contract_plans = SemanticTranslator._build_plan_from_router_contract(
                        router_decision,
                        list(columns or []),
                        schema_profile=schema_profile,
                        dataset_contract=dataset_contract,
                    )
                    if router_contract_plans:
                        set_cached_json(
                            "semantic_translator",
                            translator_cache_key,
                            [plan.model_dump(mode="json") for plan in router_contract_plans],
                            settings.SEMANTIC_TRANSLATOR_CACHE_TTL_SECONDS,
                        )
                        emit_structured_log(
                            "semantic_translator_router_contract_fallback_accepted",
                            prompt=prompt[:200],
                            primary_model=primary_model_name,
                            fallback_model=fallback_model_name,
                            plan_count=len(router_contract_plans),
                            reason_codes=router_decision.get("reason_codes"),
                            fallback_priority="quota_first",
                        )
                        return router_contract_plans

                if fallback_model_name:
                    try:
                        fallback_plans = SemanticTranslator._generate_translator_plans_with_model(
                            fallback_model_name,
                            _translator_input,
                            list(columns or []),
                        )
                        if fallback_plans:
                            set_cached_json(
                                "semantic_translator",
                                translator_cache_key,
                                [plan.model_dump(mode="json") for plan in fallback_plans],
                                settings.SEMANTIC_TRANSLATOR_CACHE_TTL_SECONDS,
                            )
                            emit_structured_log(
                                "semantic_translator_model_fallback_accepted",
                                prompt=prompt[:200],
                                primary_model=primary_model_name,
                                fallback_model=fallback_model_name,
                                plan_count=len(fallback_plans),
                            )
                            return fallback_plans
                    except Exception as fallback_error:
                        emit_structured_log(
                            "semantic_translator_model_fallback_error",
                            level="warning",
                            error=str(fallback_error)[:300],
                            primary_model=primary_model_name,
                            fallback_model=fallback_model_name,
                        )

                if router_contract_plans is None:
                    router_contract_plans = SemanticTranslator._build_plan_from_router_contract(
                        router_decision,
                        list(columns or []),
                        schema_profile=schema_profile,
                        dataset_contract=dataset_contract,
                    )
                if router_contract_plans:
                    set_cached_json(
                        "semantic_translator",
                        translator_cache_key,
                        [plan.model_dump(mode="json") for plan in router_contract_plans],
                        settings.SEMANTIC_TRANSLATOR_CACHE_TTL_SECONDS,
                    )
                    emit_structured_log(
                        "semantic_translator_router_contract_fallback_accepted",
                        prompt=prompt[:200],
                        primary_model=primary_model_name,
                        fallback_model=fallback_model_name,
                        plan_count=len(router_contract_plans),
                        reason_codes=router_decision.get("reason_codes"),
                    )
                    return router_contract_plans

            print(f"⚠️ [TRANSLATOR ERROR]: {e}")
            return None

    # 🧠 [FASE 3F] COMPONENTE 1: Intent Classifier (Determinístico, <1ms)
    @staticmethod
    def _classify_memory_intent(prompt: str, memory_context: str) -> str:
        """
        Clasifica la intención del usuario en el contexto de memoria.
        Retorna una instrucción específica para Gemini basada en el tipo detectado.
        Si no hay memoria, retorna cadena vacía (análisis estándar).
        """
        if not memory_context:
            return ""
        
        prompt_lower = prompt.lower().strip()

        if SemanticTranslator.is_visual_replacement_request(prompt):
            print(f"🧠 [INTENT CLASSIFIER] Tipo: VISUAL_REPLACEMENT")
            return """--- 🎨 MODO REEMPLAZO VISUAL (Detectado por Ibis) ---
        El usuario quiere conservar el MISMO análisis base y solo cambiar la representación visual.
        REGLAS ESTRICTAS:
        - MANTÉN el mismo tema, métricas, filtros y granularidad del análisis anterior.
        - PROHIBIDO heredar títulos decorativos o narrativas del gráfico previo.
        - OBLIGATORIO: respeta el nuevo visual solicitado como prioridad principal.
        - Si el visual nuevo no aplica al shape de datos, explica el bloqueo en vez de inventar otro visual."""
        
        # --- DRILL_DOWN: Profundizar en la misma data ---
        kw_drill_down = [
            'profundiza', 'detalla', 'amplía', 'amplia', 'más detalle', 'mas detalle',
            'zoom', 'desglose', 'desglosa', 'explica más', 'explica mas', 'ahonda',
            'más información', 'mas informacion', 'más datos', 'mas datos',
            'dame más', 'dame mas', 'granular', 'a fondo', 'en detalle',
            'drill', 'deeper', 'profundizar', 'analiza más', 'analiza mas',
            'cuéntame más', 'cuentame mas', 'dime más', 'dime mas',
            'quiero saber más', 'quiero saber mas', 'expandir', 'expande',
            'más a fondo', 'mas a fondo', 'va más allá', 've más allá',
            'y por', 'ahora por', 'muestra más', 'muestra mas'
        ]
        if any(kw in prompt_lower for kw in kw_drill_down):
            print(f"🧠 [INTENT CLASSIFIER] Tipo: DRILL_DOWN")
            
            # [FASE 4C] DYNAMIC TOPOLOGY EXCLUSION
            # Extract previous grouping dimension from memory to force shift
            # Memory format usually contains: "Agrupado por: [Campo]"
            prev_dim = "Unknown"
            match = re.search(r"Agrupado por: \[?([a-zA-Z0-9_ ]+)\]?", memory_context)
            if match:
                prev_dim = match.group(1).strip()
                print(f"🧠 [TOPOLOGY EXCLUSION] Dimension previa detectada: '{prev_dim}'")

            return f"""--- 🎯 MODO DRILL-DOWN (Detectado por Ibis) ---
        El usuario quiere PROFUNDIZAR en el análisis anterior, NO un análisis nuevo.
        REGLAS ESTRICTAS:
        - MANTÉN el mismo tema, filtros y métricas del análisis anterior.
        - 🚫 CONSTRAINT: NO AGRUPES POR '{prev_dim}'. (Ya se usó).
        - ✅ OBLIGATORIO: Busca OTRA dimensión en el dataset (ej: Lote, Vendedor, Cliente, Ubicación).
        - Si antes usaste '{prev_dim}', AHORA usa la siguiente dimensión disponible con mayor granularidad.
        - Busca dimensiones correlacionadas que expliquen "por qué" pasa esto.
        - Aumenta GRANULARIDAD: si antes fue Top 10 → ahora desglosa ESOS 10 items en sub-categorías.
        - Los títulos DEBEN reflejar profundización (ej: 'Detalle por [Nueva Dimensión]: [tema anterior]')."""
        
        # --- COMPLEMENT: Ángulos nuevos sobre el mismo tema ---
        kw_complement = [
            'nuevo análisis', 'nuevo analisis', 'distinto', 'diferente',
            'otro ángulo', 'otro angulo', 'otra perspectiva', 'algo diferente',
            'complementa', 'alternativa', 'otro enfoque', 'nueva perspectiva',
            'muéstrame otro', 'muestrame otro', 'desde otro punto',
            'información distinta', 'informacion distinta', 'datos distintos',
            'new', 'different', 'qué más hay', 'que mas hay'
        ]
        if any(kw in prompt_lower for kw in kw_complement):
            print(f"🧠 [INTENT CLASSIFIER] Tipo: COMPLEMENT")
            return """--- 🔄 MODO COMPLEMENTARIO (Detectado por Ibis) ---
        El usuario quiere análisis NUEVOS sobre el mismo tema, NO profundización.
        REGLAS ESTRICTAS:
        - USA dimensiones y métricas DISTINTAS a las del análisis anterior.
        - PROHIBIDO repetir títulos, ángulos o perspectivas similares.
        - Genera 3 perspectivas completamente frescas que COMPLEMENTEN lo ya analizado.
        - Ejemplo: si antes se analizó por almacén → ahora por material, por fecha, por ubicación."""
        
        # --- COMPARE: Análisis comparativo ---
        kw_compare = [
            'compara', 'comparación', 'comparacion', 'vs', 'versus', 'contra',
            'diferencia entre', 'lado a lado', 'antes y después', 'antes y despues',
            'cómo se compara', 'como se compara', 'qué cambió', 'que cambio',
            'evolución de', 'evolucion de', 'compare'
        ]
        if any(kw in prompt_lower for kw in kw_compare):
            print(f"🧠 [INTENT CLASSIFIER] Tipo: COMPARE")
            return """--- ⚖️ MODO COMPARATIVO (Detectado por Ibis) ---
        El usuario quiere COMPARAR datos del análisis anterior.
        REGLAS ESTRICTAS:
        - Genera análisis lado a lado (periodos, categorías, segmentos).
        - Usa visual_protocol dual_axis_chart cuando haya 2 métricas con escalas distintas.
        - Los títulos deben reflejar comparación (ej: 'Almacén 130 vs 400: Stock por Material')."""
        
        # --- DEFAULT: Hay memoria pero el prompt no matchea ningún patrón ---
        # Podría ser un tema nuevo o un prompt ambiguo → dejar que Gemini decida
        print(f"🧠 [INTENT CLASSIFIER] Tipo: DEFAULT (memoria presente, sin patrón específico)")
        return ""

    @staticmethod
    def is_visual_replacement_request(prompt: str) -> bool:
        prompt_lower = prompt.lower().strip()
        if not prompt_lower:
            return False

        continuity_markers = [
            "mantén este mismo análisis",
            "manten este mismo analisis",
            "mantén el mismo análisis",
            "manten el mismo analisis",
            "sin perder filtros",
            "mismo analisis",
            "mismo análisis",
            "cambia el gráfico actual",
            "cambia el grafico actual",
            "cámbialo a",
            "cambialo a",
            "reemplaza visual",
            "reemplaza el gráfico",
            "reemplaza el grafico",
            "transforma este análisis",
            "transforma este analisis",
            "usa el mismo análisis",
            "usa el mismo analisis",
            "conserva el análisis actual",
            "conserva el analisis actual",
            "no cambies la pregunta",
            "solo transforma este análisis",
            "solo transforma este analisis",
        ]
        return any(marker in prompt_lower for marker in continuity_markers)
    
    # 🧠 [FASE 3F] COMPONENTE 2: Literal Filter Detector (Determinístico, <1ms)
    @staticmethod
    def _detect_literal_filters(*args, **kwargs):
        # [REFACTOR 2026-06-11] Delegador. La logica vive en
        # app.services.semantic_translator.planner.detect_literal_filters.
        from app.services.semantic_translator.planner import detect_literal_filters as _impl
        return _impl(SemanticTranslator, *args, **kwargs)
    # 🧠 [FASE 3F] Router de Continuidad Inteligente
    @staticmethod
    def evaluate_continuity(current_prompt: str, previous_prompt: str) -> bool:
        """Determina si el prompt actual es continuación del anterior o un tema nuevo."""
        if not previous_prompt: return False
        
        curr_lower = current_prompt.lower().strip()
        prev_lower = previous_prompt.lower().strip()
        current_visuals = extract_prompt_visual_requests(current_prompt)
        previous_visuals = extract_prompt_visual_requests(previous_prompt)
        visual_replacement_request = SemanticTranslator.is_visual_replacement_request(current_prompt)

        def _normalize(text: str) -> str:
            return re.sub(r'\s+', ' ', re.sub(r'[^\w\s]', ' ', text.lower())).strip()

        # Repetir exactamente el mismo prompt es un rerun, no un drill-down.
        if _normalize(curr_lower) == _normalize(prev_lower):
            return False
        
        # 1. Keywords de RUPTURA explícita → SIEMPRE cortan
        keywords_ruptura = ['hola', 'gracias', 'nuevo analisis', 'olvida', 'inicio', 'cambiando de tema']
        if any(k in curr_lower for k in keywords_ruptura): return False
        
        # 2. Keywords de CONTINUIDAD explícita → SIEMPRE mantienen
        keywords_continuidad = [
            'y por', 'ahora por', 'profundiza', 'detalla', 'filtra', 'borra',
            'mas detalle', 'más detalle', 'ver graficos', 'amplía', 'expande',
            'desglosa', 'drill', 'zoom', 'muestra más'
        ]
        if any(k in curr_lower for k in keywords_continuidad): return True

        if current_visuals and visual_replacement_request:
            return True

        if current_visuals and not visual_replacement_request:
            if not previous_visuals:
                return False
            if current_visuals != previous_visuals:
                return False

        if SemanticTranslator._looks_self_contained_visual_request(current_prompt):
            return False

        # 3. Prompts cortos sin sujeto analítico → probablemente drill-down
        if len(curr_lower.split()) <= 4:
            if current_visuals:
                return False
            return True
        
        # 4. DETECCIÓN DE CAMBIO DE TEMA por sujeto analítico
        # Extraemos palabras temáticas (sustantivos clave del negocio)
        stopwords = {
            'un', 'una', 'el', 'la', 'los', 'las', 'de', 'del', 'en', 'por',
            'para', 'con', 'que', 'como', 'se', 'al', 'es', 'son', 'fue',
            'realiza', 'analiza', 'calcula', 'muestra', 'genera', 'dame',
            'quiero', 'necesito', 'haz', 'análisis', 'analisis', 'gráfico',
            'grafico', 'cuánto', 'cuanto', 'cuántos', 'cuantos', 'total',
            'evolución', 'evolucion', 'tendencia', 'distribución', 'distribucion',
            'a', 'y', 'o', 'e', 'u', 'más', 'mas', 'cual', 'cuál'
        }
        
        def extract_topics(text):
            words = set(text.split()) - stopwords
            # Filtrar palabras muy cortas
            return {w for w in words if len(w) > 2}
        
        curr_topics = extract_topics(curr_lower)
        prev_topics = extract_topics(prev_lower)
        
        # Si hay intersección temática → continuidad
        overlap = curr_topics & prev_topics
        if overlap:
            return True
        
        # Si ambos tienen temas pero NO comparten ninguno → cambio de tema
        if curr_topics and prev_topics and not overlap:
            return False
        
        # Default: mantener continuidad (beneficio de la duda)
        return True
