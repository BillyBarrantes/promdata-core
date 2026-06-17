from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.structured_logging import emit_structured_log
from app.services.canonical_analytical_contract_adapter import get_selected_candidate_dataframe
from app.services.canonical_dark_runtime_orchestrator import (
    run_canonical_dark_pipeline_for_uploaded_file,
)
from app.services.canonical_shadow_format_comparator import build_shadow_format_readiness_summary
from app.services.canonical_shadow_query_runner import (
    CanonicalShadowQueryExecution,
    _blocked_execution_result,
    _blocked_plan_metrics,
    _build_glossary_context,
    _build_topology_context,
    _get_ibis_engine_cls,
    _persist_shadow_candidate,
    _protected_columns,
    _summarize_execution_result,
    _summarize_plan,
)
from app.services.canonical_tabular_canary_executor import _build_final_struct
from app.services.analysis_memory_context import (
    apply_parent_context_to_placeholder_filters,
    build_parent_memory_context_text,
    load_parent_analysis_context,
    unwrap_prompt_payload,
)
from app.services.semantic_translator import SemanticTranslator


@dataclass
class CanonicalTabularProductionExecutionResult:
    status: str
    final_struct: dict[str, Any]
    dataset_contract: dict[str, Any]
    cleaning_notes: Any
    execution: CanonicalShadowQueryExecution


def _build_readiness_summary(pipeline_result: Any) -> dict[str, Any]:
    return build_shadow_format_readiness_summary(
        file_name=str(pipeline_result.canonical_bundle_summary.get("file_name") or ""),
        pipeline_summary={
            "pipeline_status": pipeline_result.metadata.get("pipeline_status"),
        },
        bundle_summary=pipeline_result.canonical_bundle_summary,
        materialized_summary=pipeline_result.materialized_bundle_summary,
        preview_summary=pipeline_result.preview_runtime_summary,
        analytical_summary=pipeline_result.analytical_adapter_summary,
        runtime_comparison_summary=pipeline_result.runtime_comparison_summary,
    )


def _selected_candidate_id(pipeline_result: Any) -> str:
    analytical_bundle = getattr(
        getattr(pipeline_result, "analytical_adapter_runtime", None),
        "analytical_bundle",
        None,
    )
    return str(getattr(analytical_bundle, "selected_candidate_id", "") or "").strip()


def build_canonical_tabular_production_execution(
    *,
    file_id: str,
    pipeline_result: Any,
    prompt: str | None = None,
    service_client: Any | None = None,
    max_plans: int = 3,
) -> CanonicalShadowQueryExecution:
    """Execute the user-facing tabular path without Canary/Shadow strategy bundles.

    The production executor keeps the canonical extraction/contract layer, then
    sends the prompt directly to SemanticTranslator and Ibis. Shadow visual
    parity bundles are intentionally absent from this path.
    """
    readiness_summary = _build_readiness_summary(pipeline_result)
    candidate_df = get_selected_candidate_dataframe(pipeline_result.analytical_adapter_runtime)
    selected_candidate_id = _selected_candidate_id(pipeline_result)

    if candidate_df is None:
        return CanonicalShadowQueryExecution(
            pipeline_result=pipeline_result,
            readiness_summary=readiness_summary,
            query_prompt=None,
            prompt_strategy=None,
            plans=[],
            plan_summaries=[],
            execution_summaries=[],
            execution_results=[],
            metadata={
                "file_id": file_id,
                "candidate_id": None,
                "shadow_query_status": "no_candidate",
                "production_query_status": "no_candidate",
            },
        )

    actual_prompt, parent_task_id = unwrap_prompt_payload(prompt)
    schema_profile = dict((getattr(candidate_df, "attrs", {}) or {}).get("schema_profile", {}) or {})
    dataset_contract = dict((getattr(candidate_df, "attrs", {}) or {}).get("semantic_contract", {}) or {})
    parent_context = load_parent_analysis_context(
        service_client=service_client,
        parent_task_id=parent_task_id,
        file_id=file_id,
        columns=list(candidate_df.columns),
    )
    plans = SemanticTranslator.translate(
        actual_prompt,
        list(candidate_df.columns),
        _build_glossary_context(candidate_df),
        _build_topology_context(candidate_df),
        memory_context=build_parent_memory_context_text(parent_context),
        schema_profile=schema_profile,
        dataset_contract=dataset_contract,
    ) or []
    plans = apply_parent_context_to_placeholder_filters(
        plans=plans,
        parent_context=parent_context,
    )

    # ═══════════════════════════════════════════════════════════════════════════
    # [V3] LITERAL FILTER INDEXER — Corrección de filtros contra el dataset real
    # ═══════════════════════════════════════════════════════════════════════════
    # El LLM puede emitir valores de filtro con variaciones lingüísticas (ej.
    # "egresos" en plural cuando el dato real es "Egreso" en singular).
    # Este indexer detecta esas discrepancias usando el Fuzzy-Form Matching
    # de SemanticTranslator y reemplaza el valor del filtro ANTES de que Ibis
    # ejecute la query. Es schema-agnostic: funciona con cualquier archivo.
    # ═══════════════════════════════════════════════════════════════════════════
    _literal_filter_catalog: dict[str, list[str]] = dict(
        (getattr(candidate_df, "attrs", {}) or {}).get("literal_filter_catalog", {}) or {}
    )
    if _literal_filter_catalog and plans and actual_prompt:
        try:
            _detected_literals = SemanticTranslator._detect_literal_filters(
                str(actual_prompt), _literal_filter_catalog
            )
            if _detected_literals:
                _SUPPORTED_IBIS_OPS = {
                    "==", "!=", "in", "not_in", "contains",
                    "ilike", "like", "starts_with", "ends_with",
                    "not_contains", "not_like", ">", "<", ">=", "<=",
                }
                for plan in plans:
                    intent_filters = list(getattr(plan.main_intent, "filters", []) or [])
                    for lf in _detected_literals:
                        # Buscar si Gemini ya emitió un filtro para esta columna
                        gemini_match = next(
                            (f for f in intent_filters if f.column == lf.column),
                            None,
                        )
                        if gemini_match is None:
                            # Columna no filtrada por Gemini → inyectar filtro literal
                            intent_filters.append(lf)
                            print(
                                f"🔄 [LITERAL FILTER → INJECT] "
                                f"Nuevo filtro: {lf.column} {getattr(lf.operator, 'value', lf.operator)} {lf.value}"
                            )
                        else:
                            # Columna ya filtrada por Gemini → verificar compatibilidad
                            gemini_op = str(
                                getattr(gemini_match.operator, "value", gemini_match.operator) or ""
                            ).strip()
                            if gemini_op not in _SUPPORTED_IBIS_OPS:
                                # Operador no soportado → reemplazar con filtro literal
                                intent_filters.remove(gemini_match)
                                intent_filters.append(lf)
                                print(
                                    f"🔄 [LITERAL FILTER → REPLACE] "
                                    f"Operador '{gemini_op}' no soportado. "
                                    f"Reemplazado: {lf.column} {getattr(lf.operator, 'value', lf.operator)} {lf.value}"
                                )
                            elif gemini_op in {"in", "not_in"} and isinstance(gemini_match.value, list):
                                # [V4] GUARD: Filtro multi-valor (IN/NOT_IN con lista) del LLM
                                # es una decisión analítica superior. NUNCA degradar a ==.
                                # Ej: tipo_almacen IN ["130","400"] → NO reemplazar por == "130"
                                print(
                                    f"✅ [LITERAL FILTER → SKIP] Filtro multi-valor preservado: "
                                    f"{gemini_match.column} {gemini_op} {gemini_match.value}"
                                )
                            elif str(gemini_match.value).upper() != str(lf.value).upper():
                                # Valor difiere (ej. 'egresos' vs 'Egreso') → reemplazar
                                intent_filters.remove(gemini_match)
                                intent_filters.append(lf)
                                print(
                                    f"🔄 [LITERAL FILTER → REPLACE] "
                                    f"Valor corregido: '{gemini_match.value}' → {lf.value} "
                                    f"en columna '{lf.column}'"
                                )
                    plan.main_intent.filters = intent_filters
        except Exception as _lf_err:
            # El indexer nunca debe bloquear la ejecución — es best-effort
            print(f"⚠️ [LITERAL FILTER] Error no-fatal en indexer canónico: {_lf_err}")

    bounded_plans = list(plans[: max(int(max_plans or 0), 1)])
    plan_summaries = [_summarize_plan(plan, index + 1) for index, plan in enumerate(bounded_plans)]

    shadow_file_id, parquet_path = _persist_shadow_candidate(
        candidate_df,
        file_id=file_id,
        candidate_id=selected_candidate_id,
    )
    execution_summaries: list[dict[str, Any]] = []
    execution_results: list[dict[str, Any]] = []
    if parquet_path:
        protected_cols = _protected_columns(candidate_df)
        ibis_engine_cls = _get_ibis_engine_cls()
        for index, plan in enumerate(bounded_plans, start=1):
            blocked_metrics = _blocked_plan_metrics(plan, candidate_df)
            if blocked_metrics:
                blocked_result = _blocked_execution_result(
                    plan,
                    index=index,
                    error=(
                        "Production Metric Guard bloqueó el plan: las métricas "
                        f"{blocked_metrics} no son agregables en el contrato canónico."
                    ),
                    blocked_metrics=blocked_metrics,
                )
                execution_summaries.append(blocked_result)
                execution_results.append(dict(blocked_result))
                continue
            result = ibis_engine_cls.execute_plan(
                parquet_path,
                plan,
                protected_cols=protected_cols,
                recipe_mode=True,
            )
            execution_summaries.append(_summarize_execution_result(plan, result, index))
            execution_results.append(dict(result) if isinstance(result, dict) else {"error": "invalid_execution_result"})

    success_count = sum(1 for row in execution_summaries if row.get("status") == "success")
    production_query_status = (
        "query_executed"
        if execution_summaries and success_count == len(execution_summaries)
        else "partial_query_success"
        if execution_summaries and success_count > 0
        else "query_failed"
        if bounded_plans
        else "no_plans"
    )

    emit_structured_log(
        "canonical_tabular_production_query_executed",
        file_id=file_id,
        candidate_id=selected_candidate_id,
        readiness_grade=readiness_summary.get("readiness_grade"),
        prompt_strategy="production_semantic_translator",
        plan_count=len(bounded_plans),
        success_count=success_count,
        production_query_status=production_query_status,
    )

    return CanonicalShadowQueryExecution(
        pipeline_result=pipeline_result,
        readiness_summary=readiness_summary,
        query_prompt=actual_prompt,
        prompt_strategy="production_semantic_translator",
        plans=bounded_plans,
        plan_summaries=plan_summaries,
        execution_summaries=execution_summaries,
        execution_results=execution_results,
        metadata={
            "file_id": file_id,
            "candidate_id": selected_candidate_id,
            "shadow_file_id": shadow_file_id,
            "shadow_parquet_path": parquet_path,
            "shadow_query_status": production_query_status,
            "production_query_status": production_query_status,
            "parent_task_id": parent_task_id,
            "parent_context_filter_count": len(list((parent_context or {}).get("filters") or [])),
        },
    )


def execute_canonical_tabular_production_analysis(
    *,
    file_id: str,
    prompt: str | None,
    service_client: Any,
    uploaded_file_row: dict[str, Any] | None = None,
    mime_type: str | None = None,
    max_plans: int = 3,
) -> CanonicalTabularProductionExecutionResult:
    pipeline_result = run_canonical_dark_pipeline_for_uploaded_file(
        file_id=file_id,
        service_client=service_client,
        uploaded_file_row=uploaded_file_row,
        mime_type=mime_type,
    )
    execution = build_canonical_tabular_production_execution(
        file_id=file_id,
        pipeline_result=pipeline_result,
        prompt=prompt,
        service_client=service_client,
        max_plans=max_plans,
    )
    successful_count = sum(1 for row in execution.execution_summaries if row.get("status") == "success")
    # [V3] Extraer tipo de error dominante para que el Big Data Shield
    # pueda distinguir errores lógicos (empty_result) de errores reales.
    _dominant_error = next(
        (str(row.get("error") or "") for row in execution.execution_summaries if row.get("error")),
        "",
    )
    # [V3] Relajar la puerta: aceptar partial_query_success si hay ≥1 plan exitoso.
    # _build_final_struct() ya filtra resultados con error (línea 361-362),
    # así que solo los gráficos buenos llegan al frontend.
    if successful_count <= 0:
        raise RuntimeError(
            f"canonical_production_not_ready:{execution.metadata.get('production_query_status')}:{successful_count}:{_dominant_error}"
        )
    final_struct, dataset_contract, cleaning_notes = _build_final_struct(execution)
    final_struct.setdefault("traceability", {})
    final_struct["traceability"]["runtime"] = "canonical_tabular_production"
    final_struct["traceability"]["prompt_strategy"] = execution.prompt_strategy
    return CanonicalTabularProductionExecutionResult(
        status="completed",
        final_struct=final_struct,
        dataset_contract=dataset_contract,
        cleaning_notes=cleaning_notes,
        execution=execution,
    )
