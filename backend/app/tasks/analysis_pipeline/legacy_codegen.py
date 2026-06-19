# En: backend/app/tasks/analysis_pipeline/legacy_codegen.py
"""
Legacy analysis pipeline — extracted from orchestrator.py (execute_legacy_task)
and analysis_tasks.py (generar_analisis).

Contains the full legacy flow: memory routing, data loading, semantic translation,
plan execution, and the code-generation-based generar_analisis function.
"""
import json
import re
import unicodedata
import warnings
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Any

import numpy as np
import pandas as pd

from app.core.config import settings
from app.core.gemini_client import genai
from app.core.langfuse_client import record_llm_call, record_llm_event
from app.core.serializers import CustomEncoder, convert_keys_to_str
from app.core.structured_logging import emit_structured_log
from app.core.prompt_format_override import (
    detect_format_override_from_prompt as core_detect_format_override_from_prompt,
    normalize_prompt_rules as core_normalize_prompt_rules,
)
from app.services.analysis_explainability import build_analysis_explainability
from app.services.analysis_diagnostic_context import build_enterprise_diagnostic_context
from app.services.analysis_memory_context import (
    apply_parent_context_to_placeholder_filters,
    build_parent_memory_context_text,
    build_result_semantic_context,
    load_parent_analysis_context,
)
from app.services.ai_response_cache import build_cache_key, get_cached_json, set_cached_json
from app.services.auto_analyst import AutoAnalyst
from app.services.chart_factory import ChartFactory
from app.services.data_engine import DataEngine
from app.services.document_rag import build_knowledge_context_block, resolve_user_team_id, search_knowledge_documents
from app.services.ibis_engine import IbisEngine
from app.services.metric_semantics import align_plan_metrics_with_prompt
from app.services.predictive_engine import PredictiveEngine
from app.services.semantic_translator import SemanticTranslator
from app.services.smart_table_builder import (
    should_use_smart_table,
    should_offer_hybrid_smart_table,
    echarts_to_smart_table,
)
from app.services.visual_recommendation_engine import (
    build_visual_governance,
    extract_prompt_visual_requests,
    normalize_visual_id,
    resolve_visual_protocol_value,
    should_enable_visual_probe_mode,
)
from app.core.semantic_grammar import AnalysisPlan, VisualProtocol

from app.tasks.analysis_pipeline.data_loader import (
    load_dataset_for_task, clean_business_terms, fetch_team_glossary,
    detect_data_dna, forecast_series, detect_anomalies, analyze_key_drivers,
    detect_header_row, preprocess_dataframe, get_dataframe_from_storage,
)
from app.tasks.analysis_pipeline.memory_router import (
    resolve_memory_for_task, guardar_insight_aprendido,
    _fetch_institutional_knowledge_payload, _evaluate_institutional_compliance,
    _fetch_institutional_knowledge_context, _extract_institutional_rules,
    _extract_numeric_observations, _build_compliance_metric_context,
    _force_markdown_action_block,
)
from app.tasks.analysis_pipeline.plan_generator import (
    build_semantic_context, translate_plans, inject_literal_filters,
    build_widget_query_contract, coerce_plan_for_forced_heatmap,
    detect_format_override_from_prompt, select_narrative_model_name,
    _recursive_round, _normalize_prompt_token, _pick_first_metric_column,
    _pick_heatmap_axes, coerce_chart_rows_to_table_rows,
    should_force_smart_table_from_prompt, planificar_estrategia,
    _normalize_prompt_rules, _humanize_table_key,
)
from app.tasks.analysis_pipeline.chart_generator import (
    _process_chart_bridge, _inject_fallback_charts, build_chart_config,
)
from app.tasks.analysis_pipeline.plan_executor import execute_plans
from app.tasks.analysis_pipeline.narrative_generator import generate_chart_narrative

try:
    import sklearn
    from sklearn.ensemble import IsolationForest, RandomForestRegressor
    from statsmodels.tsa.holtwinters import ExponentialSmoothing
except ImportError:
    pass
warnings.filterwarnings("ignore")


def run_legacy_analysis_pipeline(
    *,
    sb: Any,
    task_id: str,
    file_id: str,
    prompt: str,
    user_token: str,
    user_id: Any,
) -> dict[str, Any]:
    """
    Execute the full legacy analysis pipeline.

    Returns a dict with all results needed by the orchestrator:
    - response, status, actual_prompt, parent_task_id, memory_router_decision,
      format_override, schema_profile, currency_meta, institutional_snippets,
      traceability_plan_entries, plans_result, main_df, dataset_contract,
      cleaning_notes, parquet_path
    """
    code_dna = None
    parent_analysis_summary = None
    parent_task_id = None
    memory_router_decision = "fresh"
    traceability_plan_entries: list[dict[str, Any]] = []
    schema_profile: dict[str, Any] = {}
    currency_meta: dict[str, Any] = {}
    dataset_contract: dict[str, Any] = {}
    cleaning_notes: Any = []
    institutional_snippets: list[Any] = []
    actual_prompt = prompt
    format_override = {"enabled": False}
    explicit_visual_requests: list[str] = []
    visual_probe_mode = False
    main_df = None
    parent_structured_context: dict[str, Any] | None = None
    prev_prompt_text = ""
    institutional_context = ""
    parquet_path = ""
    plans_result: list = []

    try:
        actual_prompt = prompt

        # ── Memory router ──
        try:
            prompt_data = json.loads(actual_prompt)
            if isinstance(prompt_data, dict):
                actual_prompt = prompt_data.get('text', actual_prompt)
                parent_id = prompt_data.get('parent_id')
                parent_task_id = parent_id
                format_override = detect_format_override_from_prompt(actual_prompt)

                if parent_id:
                    parent_task = sb.table('analysis_tasks').select('prompt, results_json').eq('id', parent_id).single().execute()
                    if parent_task.data:
                        raw_prev = parent_task.data.get('prompt', '')
                        try:
                            prev_prompt_text = json.loads(raw_prev).get('text', raw_prev)
                        except Exception:
                            prev_prompt_text = raw_prev

                        should_keep_memory = SemanticTranslator.evaluate_continuity(actual_prompt, prev_prompt_text)
                        if should_keep_memory:
                            memory_router_decision = "keep"
                            emit_structured_log(
                                "memory_router_decision", decision="keep",
                                prompt=actual_prompt[:160], parent_prompt=prev_prompt_text[:160],
                            )
                            results_raw = parent_task.data.get('results_json')
                            if isinstance(results_raw, str):
                                try:
                                    results_raw = json.loads(results_raw)
                                except Exception:
                                    results_raw = None

                            parent_analysis_summary = {
                                "prev_prompt": prev_prompt_text,
                                "prev_titles": [],
                                "prev_analysis": "",
                            }
                            if isinstance(results_raw, dict):
                                for chart_opt in results_raw.get('chart_options', []):
                                    if isinstance(chart_opt, dict):
                                        title_obj = chart_opt.get('title', {})
                                        if isinstance(title_obj, dict):
                                            t = title_obj.get('text', '')
                                            if t:
                                                parent_analysis_summary["prev_titles"].append(t)
                                        elif isinstance(title_obj, str) and title_obj:
                                            parent_analysis_summary["prev_titles"].append(title_obj)
                                analysis_text = results_raw.get('analysis', '')
                                if analysis_text:
                                    parent_analysis_summary["prev_analysis"] = analysis_text[:300]
                        else:
                            memory_router_decision = "reset"
                            emit_structured_log(
                                "memory_router_decision", decision="reset",
                                prompt=actual_prompt[:160], parent_prompt=prev_prompt_text[:160],
                            )
                            code_dna = None

        except Exception:
            pass

        institutional_context, institutional_snippets = _fetch_institutional_knowledge_payload(
            supabase_client=sb, user_id=user_id, query=actual_prompt,
        )

        if not format_override.get('enabled'):
            format_override = detect_format_override_from_prompt(actual_prompt)
            if format_override.get('enabled'):
                emit_structured_log(
                    "format_override_activated",
                    prompt=actual_prompt[:200],
                    renderer=format_override.get("renderer"),
                    reason=format_override.get("reason"),
                    single_plan=format_override.get("single_plan"),
                )

        explicit_visual_requests = extract_prompt_visual_requests(actual_prompt)
        visual_probe_mode = should_enable_visual_probe_mode(actual_prompt, explicit_visual_requests)
        if explicit_visual_requests and not format_override.get("enabled"):
            emit_structured_log(
                "explicit_visual_request_detected",
                prompt=actual_prompt[:200],
                requested_visuals=explicit_visual_requests,
            )
        if visual_probe_mode:
            emit_structured_log(
                "visual_probe_mode_enabled",
                prompt=actual_prompt[:200],
                requested_visuals=explicit_visual_requests,
            )

        # ── Glossary + file reading ──
        glossary_map = {}
        if user_id:
            try:
                glossary_map = fetch_team_glossary(sb)
            except Exception:
                pass

        cached_dataset = DataEngine.load_cached_dataset(file_id)
        if cached_dataset:
            main_df, parquet_path, cached_sidecar = cached_dataset
            topology_rules = getattr(main_df, 'attrs', {}).get('topology_rules', {}) or cached_sidecar.get('_topology_rules', {}) or {}
            schema_profile = getattr(main_df, 'attrs', {}).get('schema_profile', {}) or cached_sidecar.get('_schema_profile', {}) or {}
            currency_meta = getattr(main_df, 'attrs', {}).get('currency_meta', {}) or cached_sidecar.get('_currency_meta', {}) or {}
            dataset_contract = getattr(main_df, 'attrs', {}).get('semantic_contract', {}) or {}
            cleaning_notes = getattr(main_df, 'attrs', {}).get('cleaning_notes', '') or cached_sidecar.get('_cleaning_notes', '')
            emit_structured_log(
                "data_engine_cache_hit", file_id=file_id,
                rows=len(main_df), cols=len(main_df.columns), parquet_path=parquet_path,
            )
        else:
            resp = sb.table('uploaded_files').select('storage_path, file_name').eq('id', file_id).single().execute()
            file_bytes = sb.storage.from_('dash-uploads').download(resp.data['storage_path'])
            raw_dfs = DataEngine.read_file(file_bytes, resp.data['file_name'])
            _clean_result = DataEngine.unify_and_clean(raw_dfs, glossary_map)
            if len(_clean_result) == 5:
                main_df, topology_rules, cleaning_notes, currency_meta, schema_profile = _clean_result
            else:
                main_df, topology_rules, cleaning_notes, currency_meta = _clean_result
                schema_profile = {}
            dataset_contract = getattr(main_df, 'attrs', {}).get('semantic_contract', {}) or {}
            parquet_path = DataEngine.commit_to_parquet(main_df, file_id)

        adn = detect_data_dna(main_df) if main_df is not None else {}

        # ── Hybrid brain (Semantic Kernel) ──
        ibis_response = None

        if parquet_path and not code_dna:
            cached_topology_context = getattr(main_df, 'attrs', {}).get('translator_context_summary', '') if main_df is not None else ""
            topology_context = cached_topology_context or str(topology_rules)
            enriched_summary = {}
            if schema_profile and not cached_topology_context:
                for col, info in schema_profile.items():
                    role_tag = info['role']
                    if role_tag == 'dimension':
                        cardinality = int(info.get('cardinality') or 0)
                        if cardinality > 50:
                            role_tag = f"dimension [ENTITY/ID] (Card: {cardinality})"
                        else:
                            role_tag = f"dimension [ATTRIBUTE] (Card: {cardinality})"
                    enriched_summary[col] = f"{info['type']} | Role: {role_tag}"
                topology_context = f"SCHEMA (Semantic Tags): {enriched_summary}\nTOPOLOGY: {topology_rules}"

            if institutional_context and not visual_probe_mode:
                topology_context += f"\n{institutional_context}"

            if dataset_contract:
                topology_context += (
                    "\nDATASET_CONTRACT: "
                    f"mode={dataset_contract.get('dataset_mode')} | "
                    f"snapshot_guard_allowed={dataset_contract.get('snapshot_guard_allowed')} | "
                    f"time_axis={dataset_contract.get('time_axis')} | "
                    f"entity_key={dataset_contract.get('entity_key')}"
                )
                evidence = dataset_contract.get('evidence', {})
                topology_context += (
                    "\nDATASET_EVIDENCE: "
                    f"avg_rows_per_period={evidence.get('avg_rows_per_period')} | "
                    f"rows_at_max_ratio={evidence.get('rows_at_max_ratio')} | "
                    f"metric_at_max_ratio={evidence.get('metric_at_max_ratio')} | "
                    f"repeated_entity_ratio={evidence.get('repeated_entity_ratio')} | "
                    f"snapshot_score={evidence.get('snapshot_score')} | "
                    f"flow_score={evidence.get('flow_score')}"
                )

            cached_reference_date = getattr(main_df, 'attrs', {}).get('reference_date') if main_df is not None else None
            if cached_reference_date:
                ref_date = str(cached_reference_date)
            elif dataset_contract.get('snapshot_guard_allowed'):
                date_cols_for_ref = [c for c, info in schema_profile.items() if info.get('role') == 'date']
                if date_cols_for_ref:
                    ref_col = date_cols_for_ref[0]
                    try:
                        ref_date = str(main_df[ref_col].max().date())
                    except Exception:
                        ref_date = str(pd.Timestamp.now().date())
                else:
                    ref_date = str(pd.Timestamp.now().date())
            else:
                ref_date = str(pd.Timestamp.now().date())

            topology_context += f"\nFECHA_REFERENCIA_DATASET: {ref_date}"
            topology_context += "\nINSTRUCCIÓN: Usa FECHA_REFERENCIA_DATASET como 'hoy' para filtros temporales relativos (ej: 'próximo a vencer' = fecaduc < FECHA_REFERENCIA + 90 días). Para filtros de fecha, usa el formato ISO: YYYY-MM-DD."
            if institutional_context and institutional_context not in topology_context and not visual_probe_mode:
                topology_context += f"\n{institutional_context}"

            memory_text = ""
            if parent_analysis_summary and parent_analysis_summary.get('prev_prompt'):
                analysis_snippet = parent_analysis_summary.get('prev_analysis', '')[:200]
                visual_replacement_request = SemanticTranslator.is_visual_replacement_request(actual_prompt)
                if visual_replacement_request:
                    memory_text = (
                        f"ANÁLISIS ANTERIOR DEL USUARIO: '{parent_analysis_summary['prev_prompt']}'\n"
                        f"RESUMEN NARRATIVO PREVIO: {analysis_snippet}\n"
                        "REGLA: conserva solo el contexto analítico y los filtros; "
                        "NO heredes títulos ni tipo de gráfico del análisis anterior."
                    )
                elif format_override.get('enabled'):
                    memory_text = (
                        f"ANÁLISIS ANTERIOR DEL USUARIO: '{parent_analysis_summary['prev_prompt']}'\n"
                        f"RESUMEN NARRATIVO PREVIO: {analysis_snippet}\n"
                        f"REGLA: usa la memoria solo para CONTEXTO ANALÍTICO, no para heredar formato visual."
                    )
                else:
                    titles_str = ' | '.join(parent_analysis_summary['prev_titles']) if parent_analysis_summary.get('prev_titles') else 'No disponibles'
                    memory_text = (
                        f"ANÁLISIS ANTERIOR DEL USUARIO: '{parent_analysis_summary['prev_prompt']}'\n"
                        f"GRÁFICOS GENERADOS: [{titles_str}]\n"
                        f"RESUMEN NARRATIVO PREVIO: {analysis_snippet}"
                    )

            if memory_text and SemanticTranslator.should_bypass_memory_context(
                actual_prompt, list(main_df.columns), schema_profile=schema_profile,
            ):
                memory_text = ""
                parent_analysis_summary = None
                memory_router_decision = "self_contained_bypass"
                emit_structured_log(
                    "memory_context_bypassed",
                    reason="self_contained_prompt",
                    prompt=actual_prompt[:160],
                    parent_prompt=prev_prompt_text[:160],
                )

            parent_structured_context = load_parent_analysis_context(
                service_client=sb, parent_task_id=parent_task_id,
                file_id=file_id, columns=list(main_df.columns),
            )
            structured_memory_text = build_parent_memory_context_text(parent_structured_context)
            if structured_memory_text:
                memory_text = (
                    f"{memory_text}\n\n{structured_memory_text}".strip()
                    if memory_text else structured_memory_text
                )
                emit_structured_log(
                    "analysis_parent_context_injected",
                    task_id=task_id, file_id=file_id,
                    parent_task_id=parent_task_id,
                    filter_count=len(list((parent_structured_context or {}).get("filters") or [])),
                )

            memory_instruction = SemanticTranslator._classify_memory_intent(actual_prompt, memory_text)
            if format_override.get('enabled'):
                format_instruction = format_override.get('translator_instruction', '')
                memory_instruction = (
                    f"{memory_instruction}\n{format_instruction}".strip()
                    if memory_instruction else format_instruction
                )
            else:
                format_instruction = ""

            dimension_values = getattr(main_df, 'attrs', {}).get('literal_filter_catalog', {}) if main_df is not None else {}
            if dimension_values:
                pass
            elif schema_profile:
                dimension_values = {}
                for col_name, col_info in schema_profile.items():
                    if col_info.get('role') == 'dimension' and col_name in main_df.columns:
                        try:
                            nunique = main_df[col_name].nunique()
                            sample_len = main_df[col_name].dropna().astype(str).str.len().mean()
                            limit = 1000
                            if sample_len < 50:
                                limit = 10000
                            if nunique <= limit:
                                unique_vals = main_df[col_name].dropna().unique().tolist()
                                dimension_values[col_name] = unique_vals
                        except Exception:
                            pass

            literal_filters = SemanticTranslator._detect_literal_filters(actual_prompt, dimension_values)

            plans_result = SemanticTranslator.translate(
                actual_prompt, list(main_df.columns),
                str(glossary_map), topology_context,
                memory_context=memory_text,
                memory_instruction=memory_instruction,
                format_instruction=format_instruction,
                schema_profile=schema_profile,
                dataset_contract=dataset_contract,
            )

            if not plans_result:
                plans_result = []
            elif not isinstance(plans_result, list):
                plans_result = [plans_result]
            plans_result = apply_parent_context_to_placeholder_filters(
                plans=plans_result, parent_context=parent_structured_context,
            )
            plans_result = align_plan_metrics_with_prompt(
                plans_result, actual_prompt, schema_profile, currency_meta,
            )

            if format_override.get('enabled') and format_override.get('single_plan') and len(plans_result) > 1:
                plans_result = plans_result[:1]

            if explicit_visual_requests and not format_override.get("enabled"):
                if len(explicit_visual_requests) == 1 and len(plans_result) > 1:
                    plans_result = plans_result[:1]

                for plan_idx, plan in enumerate(plans_result):
                    forced_visual = explicit_visual_requests[min(plan_idx, len(explicit_visual_requests) - 1)]
                    canonical_visual = normalize_visual_id(forced_visual)
                    protocol_visual = resolve_visual_protocol_value(canonical_visual)
                    try:
                        plan.main_intent.visual_protocol = VisualProtocol(protocol_visual)
                    except Exception:
                        pass

            _SUPPORTED_IBIS_OPS: set = {"==", "!=", "in", "not_in", "contains",
                                         "ilike", "like", "starts_with", "ends_with",
                                         "not_contains", "not_like",
                                         ">", "<", ">=", "<="}
            if literal_filters and plans_result:
                for plan in plans_result:
                    for lf in literal_filters:
                        gemini_filter = next(
                            (f for f in plan.main_intent.filters if f.column == lf.column),
                            None
                        )
                        if gemini_filter is None:
                            plan.main_intent.filters.append(lf)
                        else:
                            gemini_op = str(getattr(gemini_filter.operator, 'value', gemini_filter.operator) or '').strip()
                            if gemini_op not in _SUPPORTED_IBIS_OPS:
                                plan.main_intent.filters.remove(gemini_filter)
                                plan.main_intent.filters.append(lf)
                            elif gemini_op in {"in", "not_in"} and isinstance(gemini_filter.value, list):
                                pass
                            elif str(gemini_filter.value).upper() != str(lf.value).upper():
                                plan.main_intent.filters.remove(gemini_filter)
                                plan.main_intent.filters.append(lf)

            ibis_response = execute_plans(
                plans_result=plans_result,
                parquet_path=parquet_path,
                schema_profile=schema_profile,
                main_df=main_df,
                actual_prompt=actual_prompt,
                format_override=format_override,
                currency_meta=currency_meta,
                file_id=file_id,
                task_id=task_id,
                explicit_visual_requests=explicit_visual_requests,
                topology_rules=topology_rules,
                institutional_context=institutional_context,
                institutional_snippets=institutional_snippets,
                visual_probe_mode=visual_probe_mode,
                traceability_plan_entries=traceability_plan_entries,
            )

            return {
                "response": ibis_response,
                "status": "completed",
                "actual_prompt": actual_prompt,
                "parent_task_id": parent_task_id,
                "memory_router_decision": memory_router_decision,
                "format_override": format_override,
                "schema_profile": schema_profile,
                "currency_meta": currency_meta,
                "institutional_snippets": institutional_snippets,
                "traceability_plan_entries": traceability_plan_entries,
                "plans_result": plans_result,
                "main_df": main_df,
                "dataset_contract": dataset_contract,
                "cleaning_notes": cleaning_notes,
                "parquet_path": parquet_path,
            }

    except Exception as e:
        return {
            "response": [{"type": "error", "content": f"Error del sistema: {str(e)}"}],
            "status": "failed",
            "actual_prompt": actual_prompt,
            "parent_task_id": parent_task_id,
            "memory_router_decision": memory_router_decision,
            "format_override": format_override,
            "schema_profile": schema_profile,
            "currency_meta": currency_meta,
            "institutional_snippets": institutional_snippets,
            "traceability_plan_entries": traceability_plan_entries,
            "plans_result": plans_result,
            "main_df": main_df,
            "dataset_contract": dataset_contract,
            "cleaning_notes": cleaning_notes,
            "parquet_path": parquet_path,
        }


def generar_analisis(
    dfs: dict, prompt: str, audit_log: list, user_token: str,
    supabase_client, user_id, parent_context: str, glossary_map: dict,
    topology_rules: dict, cleaning_notes: str, currency_meta: dict = {},
    prev_code_override=None,
):
    """
    Legacy code-generation analysis function.
    Preserved for backward compatibility with orchestrator imports.
    """
    model_name = settings.AI_MODEL_NAME
    config_json = {"temperature": 0.2, "top_p": 0.95, "max_output_tokens": 8192, "response_mime_type": "application/json"}
    config_code = {"temperature": 0.1, "top_p": 0.95, "max_output_tokens": 8192}

    model = genai.GenerativeModel(model_name=model_name)
    main_df = list(dfs.values())[0]
    adn = detect_data_dna(main_df)
    cols = list(main_df.columns)

    if prev_code_override:
        facts_json = json.dumps({"INFO": "DATOS GLOBALES OCULTOS POR MEMORIA"}, indent=2)
        hard_facts = {}
    else:
        hard_facts = AutoAnalyst.analyze(main_df, currency_meta=currency_meta)
        facts_json = json.dumps(hard_facts, indent=2, ensure_ascii=False)

    glossary_text = "\n".join([f"- '{k}': {v}" for k, v in glossary_map.items()]) if glossary_map else "Sin glosario."
    topology_text = "\n".join([f"- COLUMNA '{k}': {v}" for k, v in topology_rules.items()]) if topology_rules else "Sin reglas detectadas."

    data_info = f"""
    [METADATA CRÍTICA]
    Columnas Reales: {cols}
    [NOTAS DE LIMPIEZA AUTOMÁTICA - LEER]:
    {cleaning_notes}
    (Si el usuario pide un código, revisa si fue normalizado a MAYÚSCULAS arriba).
    [REGLAS FÍSICAS (TOPOLOGÍA)]:
    {topology_text}
    """

    txt_memoria = "Sin contexto previo. Es una nueva conversación."

    if prev_code_override:
        txt_memoria = (
            "⚠️ ATENCIÓN CRÍTICA: El usuario está en un 'Drill-Down' (Profundización). "
            "Ya existe un FILTRO ACTIVO en el código previo (ver prev_code_override). "
            "La intención del usuario es PROFUNDIZAR en los datos YA FILTRADOS. "
            "NO CAMBIES DE TEMA NI DE CATEGORÍA a menos que se pida explícitamente."
        )

    plan = planificar_estrategia(model, prompt, data_info, adn, glossary_text, config_json, facts_json, memory_context=txt_memoria)

    base_template = "    df = dfs['principal_unificado'].copy()"

    code_to_inject = prev_code_override if prev_code_override else ""

    if code_to_inject:
        clean_prev = code_to_inject.replace("import pandas as pd", "").replace("def execute_analysis(dfs):", "").strip()

        base_template = (
            f"    # [ESTADO RECUPERADO DE MEMORIA]\n"
            f"    # El DataFrame 'df' YA INICIA FILTRADO por tu ejecución anterior:\n"
            f"    df = dfs['principal_unificado'].copy()\n"
            f"    {clean_prev}\n"
            f"    # [FIN ESTADO PREVIO] --------------------------------\n"
            f"    # INSTRUCCIÓN DE SEGURIDAD (VÁLVULA DE ESCAPE): \n"
            f"    # Si el usuario pide 'Analizar Todo', 'Ver Global' o cambia de tema, \n"
            f"    # IGNORA el filtro de arriba y REINICIA 'df' con: df = dfs['principal_unificado'].copy()"
        )

    code_prompt = (
        f"Eres un **Python Data Scientist Senior & Consultor de Negocios**.\n"
        f"OBJETIVO: {plan.get('intencion')}\n\n"
        f"# [CONTEXTO GLOBAL (REFERENCIA)]:\n"
        f"{facts_json}\n"
        f"⚠️ INSTRUCCIÓN CRÍTICA: Estos hechos son globales. Si el usuario pregunta por algo específico (ej: 'Saldos', 'Zona Norte'), IGNORA el contexto global y GENERA CÓDIGO para filtrar el DataFrame específicamente.\n\n"
        f"GLOSARIO: {glossary_text}\n"
        f"REGLAS TOPOLÓGICAS: {topology_text}\n"
        f"DATA INFO: {data_info}\n"
        f"CONTEXTO PREVIO: {parent_context}\n\n"
        f"INSTRUCCIONES DE FORMATO Y ESTILO (OBLIGATORIAS):\n"
        f"1. **CERO HTML:** Está PROHIBIDO usar etiquetas como <b>, <br>. Usa SOLO Markdown (*cursiva*, **negrita**).\n"
        f"2. **IDIOMA:** Todo texto visible (títulos, ejes, análisis) DEBE estar en **ESPAÑOL DE NEGOCIOS**.\n"
        f"3. **PROFUNDIDAD:** No solo describas. Calcula desviaciones, impactos y explora el 'por qué'.\n\n"
        f"Genera la función `execute_analysis(dfs)` que retorna un JSON.\n\n"
        f"REGLAS DE ORO PARA CÓDIGO PYTHON:\n"
        f"1. **FILTRADO OBLIGATORIO:** Si el objetivo menciona una categoría, almacén o fecha, tu código DEBE empezar filtrando: `df = df[df['columna'] == 'Valor']`.\n"
        f"2. **SEPARACIÓN VISUAL:** PROHIBIDO mezclar métricas incompatibles (ej. Evolución vs Pareto) en un solo gráfico. Genera múltiples claves en 'chart_data' (ej. 'chart_trend', 'chart_pareto').\n"
        f"3. **SNAPSHOTS:** Para stocks/saldos, usa siempre: `df[df['Fecha'] == df['Fecha'].max()]`.\n"
        f"4. **FORMATO GRÁFICO (ECHARTS SIMPLE):**\n"
        f"   - Tu salida 'chart_data' debe ser un diccionario de configs ECharts.\n"
        f"   - NO uses librerías visuales (plotly/matplotlib). Solo estructuras de datos Python.\n"
        f"   - Usa listas simples para 'data'. No objetos complejos.\n\n"
        f"   EJEMPLO Barras/Líneas:\n"
        f"   {{\n"
        f"       'title': {{ 'text': 'Ventas', 'left': 'center' }},\n"
        f"       'tooltip': {{ 'trigger': 'axis' }},\n"
        f"       'xAxis': {{ 'type': 'category', 'data': ['Ene', 'Feb'] }},\n"
        f"       'yAxis': {{ 'type': 'value' }},\n"
        f"       'series': [ {{ 'data': [100, 200], 'type': 'bar', 'name': 'Total' }} ]\n"
        f"   }}\n\n"
        f"   EJEMPLO Pie:\n"
        f"   {{\n"
        f"       'title': {{ 'text': 'Zona', 'left': 'center' }},\n"
        f"       'tooltip': {{ 'trigger': 'item' }},\n"
        f"       'series': [ {{ 'type': 'pie', 'data': [ {{'value': 10, 'name': 'A'}}, {{'value': 20, 'name': 'B'}} ] }} ]\n"
        f"   }}\n\n"
        f"```python\n"
        f"import pandas as pd\n"
        f"import numpy as np\n"
        f"def execute_analysis(dfs):\n"
        f"{base_template}\n"
        f"    results = {{}}\n"
        f"    try:\n"
        f"        # Lógica de análisis...\n"
        f"        results = {{ 'status': 'success', 'chart_data': {{...}}, 'summary': {{...}} }}\n"
        f"    except Exception as e:\n"
        f"        results = {{ 'status': 'error', 'message': str(e) }}\n"
        f"    return results\n"
        f"```"
    )

    max_retries = 2
    results = {"status": "error", "message": "No iniciado"}
    code = None
    error_msg = None

    for attempt in range(max_retries):
        try:
            current_prompt = code_prompt
            if attempt > 0 and error_msg:
                current_prompt += f"\n\nATENCIÓN: El intento anterior falló con este error: {error_msg}. CORRIGE EL CÓDIGO."

            resp_code = None
            with record_llm_call(
                "code_generation", model_name=model_name, prompt=current_prompt,
                trace_id=None, trace_name="generar_analisis",
                metadata={"attempt": attempt + 1},
            ) as lf_span:
                resp_code = model.generate_content(current_prompt, generation_config=config_code)
                lf_span["output"] = resp_code.text
            text_response = resp_code.text
            match = re.search(r'```python(.*?)```', text_response, re.DOTALL)
            code = match.group(1).strip() if match else text_response.replace('```python', '').replace('```', '').strip()

            safe_globals = {
                'pd': pd, 'np': np, 'dfs': dfs,
                'forecast_series': forecast_series,
                'detect_anomalies': detect_anomalies,
                'analyze_key_drivers': analyze_key_drivers,
                'final_results': {}
            }
            exec(f"{code}\n\nfinal_results = execute_analysis(dfs)", safe_globals)
            results = safe_globals.get('final_results', {})

            if results.get('status') == 'success':
                break
            else:
                error_msg = results.get('message', 'Error lógico desconocido')

        except Exception as e:
            error_msg = str(e)
            results = {"status": "error", "message": error_msg}

    # Chart bridge
    results, ai_generated_charts = _process_chart_bridge(results, hard_facts if 'hard_facts' in locals() else {})

    # Fallback injection
    if not ai_generated_charts and 'hard_facts' in locals() and hard_facts and isinstance(results, dict):
        results = _inject_fallback_charts(results, hard_facts, False)

    if results.get('status') == 'error':
        return [{"type": "error_analitico", "content": results.get('message')}]
    if user_id and results.get('status') == 'success':
        guardar_insight_aprendido(supabase_client, user_id, f"Analysis: {prompt}", code, adn)

    # Synthesis visual
    hydrated = []
    if isinstance(results, dict) and results.get('injected_charts'):
        hydrated.extend(results['injected_charts'])

    instruccion_visual = ""
    if hydrated:
        instruccion_visual = 'NO generes objetos "chart_template". Solo genera "mensaje_resumen". Los gráficos ya fueron inyectados.'
    else:
        instruccion_visual = 'Si es necesario, puedes generar objetos "chart_template" para visualizar datos.'

    syn_prompt = f"""
    Actúa como un **Consultor de Negocios Senior**.

    CONTEXTO TÉCNICO: Se han generado los siguientes datos y gráficos (Python):
    INPUT: {json.dumps(results, cls=CustomEncoder)[:50000]}

    TU MISIÓN OBLIGATORIA:
    1. Interpreta los datos con visión de negocio.
    2. Genera un objeto "mensaje_resumen" que tenga la siguiente ESTRUCTURA DE ALTO IMPACTO (Formato Markdown):
       - **Titular del Hallazgo:** Una frase potente que resuma lo más importante.
       - **Análisis Detallado:** Mínimo 2 párrafos explicando qué pasó, por qué pasó y qué significan los números.
       - **Recomendación:** Una acción concreta basada en el dato.

    3. {instruccion_visual} (Si ya hay gráficos inyectados, úsalos como evidencia en tu texto).

    PROTOCOLO DE TRANSPARENCIA NARRATIVA (OBLIGATORIO):
    - TRAZABILIDAD: PROHIBIDO usar términos opacos como "segmento principal", "grupo líder". Si agrupas elementos, NÓMBRALOS individualmente. Ej: "Los 3 productos principales (A, B y C) suman X".
    - JUSTIFICACIÓN DE UNIVERSOS: Si citas un total, especifica a qué corresponde y diferéncialos del total global. Ej: "De las 5,386,970 unidades totales, los Top 10 productos concentran 2,839,784".
    - LENGUAJE DE NEGOCIO: Escribe para un gerente o analista junior. CERO jerga de Data Science. Si haces una operación matemática, explícala con palabras simples.
    - INTEGRIDAD DE UNIDADES: Si la métrica es física (Stock, Cantidad, Volumen, Unidades), PROHIBIDO usar símbolos de moneda ($, €) o términos financieros (capital, portafolio, ingresos). Usa moneda SOLO si la métrica implica dinero (Precio, Costo, Venta, Ingreso, Monto).

    FORMATO DE SALIDA (JSON Puro):
    [
      {{
        "type": "mensaje_resumen",
        "content": "### 🚀 Titular Impactante\\n\\n**Análisis:** Aquí el texto profundo...\\n\\n💡 **Recomendación:** ..."
      }}
    ]
    """

    try:
        with record_llm_call(
            "synthesis", model_name=model_name, prompt=syn_prompt,
            trace_id=None, trace_name="generar_analisis",
        ) as lf_span:
            final = model.generate_content(syn_prompt, generation_config=config_json)
            lf_span["output"] = final.text
        text = final.text[final.text.find('['):final.text.rfind(']') + 1]
        final_json = json.loads(text)

        for item in final_json:
            if item.get('type') == 'chart_template':
                opt = {}
                tpl = item.get('template')
                data = item.get('data', [])
                title = item.get('title', '')

                if tpl == 'bar_chart':
                    opt = ChartFactory.build_bar_chart(title, data)
                elif tpl == 'line_chart':
                    opt = ChartFactory.build_line_chart(title, data)
                elif tpl == 'pie_chart':
                    opt = ChartFactory.build_pie_chart(title, data)
                elif tpl == 'dual_axis_chart':
                    opt = ChartFactory.build_dual_axis_chart(title, item.get('categories', []), item.get('bar_data', []), item.get('line_data', []), "Volumen", "Tendencia")

                if "error" not in opt:
                    hydrated.append({"type": "configuracion_echarts", "title": title, "option": opt})
            else:
                hydrated.append(item)

    except Exception as e:
        if not hydrated:
            return [{"type": "error", "content": f"Error visual: {str(e)}"}]

    if code:
        hydrated.append({
            "type": "internal_code_context",
            "content": code,
            "dna": adn
        })

    return hydrated
