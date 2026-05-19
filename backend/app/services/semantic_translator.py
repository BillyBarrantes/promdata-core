import google.generativeai as genai
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

genai.configure(api_key=settings.GEMINI_API_KEY)

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
    def _extract_json_code_block(raw_text: str) -> str:
        fenced_match = re.search(r"```(?:json)?\s*(.*?)\s*```", raw_text, flags=re.IGNORECASE | re.DOTALL)
        return fenced_match.group(1).strip() if fenced_match else raw_text.strip()

    @staticmethod
    def _split_json_documents(raw_text: str) -> list[dict | list]:
        """
        Parser tolerante para respuestas Gemini con múltiples documentos JSON
        concatenados o con texto residual.
        """
        decoder = JSONDecoder()
        text = raw_text.strip()
        docs: list[dict | list] = []
        cursor = 0

        while cursor < len(text):
            while cursor < len(text) and text[cursor] in " \t\r\n,;":
                cursor += 1
            if cursor >= len(text):
                break
            if text[cursor] not in "[{":
                cursor += 1
                continue
            try:
                parsed, end = decoder.raw_decode(text, cursor)
                if isinstance(parsed, (dict, list)):
                    docs.append(parsed)
                cursor = max(end, cursor + 1)
            except JSONDecodeError:
                cursor += 1

        return docs

    @staticmethod
    def _parse_translator_payload(raw_text: str) -> dict | list:
        """
        Devuelve dict/list válido incluso cuando Gemini devuelve JSON + ruido.
        """
        candidate = SemanticTranslator._extract_json_code_block(raw_text)

        try:
            return json.loads(candidate)
        except JSONDecodeError:
            docs = SemanticTranslator._split_json_documents(candidate)
            if not docs:
                raise
            if len(docs) == 1:
                return docs[0]
            return docs

    @staticmethod
    def _schema_fingerprint(
        columns: list[str],
        schema_profile: dict | None = None,
        dataset_contract: dict[str, Any] | None = None,
    ) -> str:
        return build_cache_key(
            "semantic_router_schema",
            {
                "columns": list(columns or []),
                "schema_profile": schema_profile or {},
                "dataset_contract": dataset_contract or {},
            },
        )

    @staticmethod
    def _normalize_semantic_router_decision(payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            payload = {}

        route = str(payload.get("route") or "COMPLEJO").strip().upper()
        if route not in {"SIMPLE", "COMPLEJO"}:
            route = "COMPLEJO"

        try:
            confidence = float(payload.get("confidence", 0.0))
        except Exception:
            confidence = 0.0
        confidence = max(0.0, min(confidence, 1.0))

        reason_codes = payload.get("reason_codes") or []
        if not isinstance(reason_codes, list):
            reason_codes = [str(reason_codes)]
        normalized_reason_codes = [
            normalize_semantic_text(str(code)).replace(" ", "_")
            for code in reason_codes
            if str(code or "").strip()
        ]

        detected_intent = normalize_semantic_text(str(payload.get("detected_intent") or "unknown")).replace(" ", "_")
        if not detected_intent:
            detected_intent = "unknown"

        requires_time = bool(payload.get("requires_time", False))
        original_route = route
        if confidence < SemanticTranslator.SEMANTIC_ROUTER_CONFIDENCE_THRESHOLD:
            route = "COMPLEJO"
            if "low_confidence" not in normalized_reason_codes:
                normalized_reason_codes.append("low_confidence")
        if any(code in SemanticTranslator.SEMANTIC_ROUTER_COMPLEX_REASON_CODES for code in normalized_reason_codes):
            route = "COMPLEJO"
            if "conservative_policy" not in normalized_reason_codes:
                normalized_reason_codes.append("conservative_policy")

        semantic_contract = SemanticTranslator._normalize_router_semantic_contract(
            payload.get("semantic_contract") or payload.get("contract") or {},
            detected_intent=detected_intent,
            requires_time=requires_time,
        )
        return {
            "route": route,
            "confidence": confidence,
            "detected_intent": detected_intent,
            "requires_time": requires_time,
            "reason_codes": normalized_reason_codes,
            "original_route": original_route,
            "semantic_contract": semantic_contract,
        }

    @staticmethod
    def _normalize_router_semantic_contract(
        payload: Any,
        detected_intent: str = "unknown",
        requires_time: bool = False,
    ) -> dict[str, Any]:
        if not isinstance(payload, dict):
            payload = {}

        def _clean_text(value: Any) -> str | None:
            text = str(value or "").strip()
            if not text or text.lower() in {"none", "null", "all", "todos", "total", "global"}:
                return None
            return text

        intent = normalize_semantic_text(str(payload.get("intent") or detected_intent or "unknown")).replace(" ", "_")
        if intent not in {"trend", "distribution", "descriptive", "diagnostic", "predictive"}:
            intent = detected_intent if detected_intent in {"trend", "distribution", "descriptive", "diagnostic", "predictive"} else "unknown"

        raw_series_mode = normalize_semantic_text(
            str(
                payload.get("series_mode")
                or payload.get("top_n_aggregation_mode")
                or payload.get("aggregation_mode")
                or "none"
            )
        ).replace(" ", "_")
        series_mode_aliases = {
            "multi_series": "split",
            "per_item": "split",
            "each": "split",
            "separate": "split",
            "separado": "split",
            "desglosado": "split",
            "consolidated": "sum",
            "consolidado": "sum",
            "rollup": "sum",
            "combined": "sum",
            "single_series": "sum",
            "total": "sum",
        }
        series_mode = series_mode_aliases.get(raw_series_mode, raw_series_mode)
        if series_mode not in {"split", "sum", "none"}:
            series_mode = "none"

        try:
            top_n_value = payload.get("top_n")
            top_n = int(top_n_value) if top_n_value not in (None, "", False) else None
            if top_n is not None:
                top_n = max(1, min(top_n, 50))
        except Exception:
            top_n = None

        return {
            "intent": intent,
            "metric": _clean_text(payload.get("metric") or payload.get("metric_hint") or payload.get("value_column")),
            "plot_metric": _clean_text(payload.get("plot_metric") or payload.get("display_metric")),
            "ranking_metric": _clean_text(payload.get("ranking_metric") or payload.get("sort_metric") or payload.get("rank_metric")),
            "ranking_direction": normalize_semantic_text(str(payload.get("ranking_direction") or "desc")).replace(" ", "_") or "desc",
            "time_axis": _clean_text(payload.get("time_axis") or payload.get("date_column") or payload.get("time_dimension")),
            "dimension": _clean_text(payload.get("dimension") or payload.get("split_dimension") or payload.get("group_by")),
            "group_by": payload.get("group_by") if isinstance(payload.get("group_by"), list) else [],
            "positive_filters": payload.get("positive_filters") if isinstance(payload.get("positive_filters"), list) else [],
            "negative_filters": payload.get("negative_filters") if isinstance(payload.get("negative_filters"), list) else [],
            "top_n": top_n,
            "series_mode": series_mode,
            "grain": normalize_semantic_text(str(payload.get("grain") or "month")).replace(" ", "_") or "month",
            "aggregation": normalize_semantic_text(str(payload.get("aggregation") or "sum")).replace(" ", "_") or "sum",
            "visual_protocol": normalize_semantic_text(str(payload.get("visual_protocol") or "")).replace(" ", "_") or None,
            "requires_time": bool(payload.get("requires_time", requires_time)),
        }

    @staticmethod
    def _route_prompt_with_semantic_router(
        prompt: str,
        columns: list[str],
        schema_profile: dict | None = None,
        dataset_contract: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        surface_prompt = SemanticTranslator._normalize_surface_text(prompt)
        schema_fingerprint = SemanticTranslator._schema_fingerprint(
            columns,
            schema_profile=schema_profile,
            dataset_contract=dataset_contract,
        )
        router_cache_key = build_cache_key(
            "semantic_router",
            {
                "prompt": surface_prompt,
                "schema_fingerprint": schema_fingerprint,
            },
        )
        cached_decision = get_cached_json("semantic_router", router_cache_key)
        if isinstance(cached_decision, dict):
            normalized_cached_decision = SemanticTranslator._normalize_semantic_router_decision(cached_decision)
            emit_structured_log(
                "semantic_router_cache_hit",
                route=normalized_cached_decision.get("route"),
                confidence=normalized_cached_decision.get("confidence"),
                detected_intent=normalized_cached_decision.get("detected_intent"),
            )
            return normalized_cached_decision

        router_schema = {
            "route": "SIMPLE|COMPLEJO",
            "confidence": "float 0.0-1.0",
            "detected_intent": "trend|distribution|descriptive|diagnostic|predictive|unknown",
            "requires_time": "boolean",
            "reason_codes": "list[str]",
            "semantic_contract": {
                "intent": "trend|distribution|descriptive|diagnostic|predictive|unknown",
                "metric": "métrica principal si no hay diferencia entre ranking y gráfico",
                "plot_metric": "métrica que el usuario quiere ver/graficar",
                "ranking_metric": "métrica usada para ordenar o seleccionar Top N; null si es igual a plot_metric",
                "ranking_direction": "desc|asc",
                "time_axis": "nombre o concepto del eje temporal; null si no aplica",
                "dimension": "nombre o concepto de dimensión; null para totales globales",
                "group_by": "list[str] dimensiones adicionales solicitadas",
                "positive_filters": "list[{column, operator, value}] para filtros inclusivos",
                "negative_filters": "list[{column, operator, value}] para exclusiones explícitas",
                "top_n": "integer|null",
                "series_mode": "split|sum|none",
                "grain": "month|week|day|quarter|year|null",
                "aggregation": "sum|avg|count|min|max",
                "visual_protocol": "line_chart|bar_chart|pie_chart|treemap|kpi|null",
                "requires_time": "boolean",
            },
        }
        router_instruction = f"""
        ERES EL ROUTER SEMÁNTICO DE PROMDATA.
        Tu única tarea es clasificar el riesgo de interpretación del prompt.
        No analices datos, no generes planes y no expliques fuera del JSON.

        Devuelve JSON estricto compatible con:
        {json.dumps(router_schema, ensure_ascii=False)}

        Usa route="SIMPLE" solo si la intención humana es única, directa y puede resolverse con un plan determinístico.
        Usa route="COMPLEJO" si hay negaciones, exclusiones, instrucciones de separación/consolidación,
        señales mixtas, múltiples vistas, ambigüedad, filtros compuestos, ranking por métrica diferente,
        causa raíz o baja confianza.
        Ante duda, route="COMPLEJO".
        Siempre llena semantic_contract. Si el usuario pide un total global por tiempo, dimension=null y series_mode="none".
        Si pide Top N con una línea por elemento, series_mode="split". Si pide Top N consolidado, series_mode="sum".
        Si el usuario pide "graficar X pero ordenar por Y", usa plot_metric=X y ranking_metric=Y.
        Si el usuario excluye valores, usa negative_filters con operator="not_in" o "!=".

        COLUMNAS: {list(columns or [])}
        SCHEMA_FINGERPRINT: {schema_fingerprint}
        """
        try:
            model = genai.GenerativeModel(
                model_name=settings.NARRATIVE_FAST_MODEL_NAME,
                generation_config={"response_mime_type": "application/json", "temperature": 0.0},
            )
            response = model.generate_content(f"{router_instruction}\n\nPROMPT: {prompt}")
            parsed_decision = SemanticTranslator._parse_translator_payload(response.text.strip())
            normalized_decision = SemanticTranslator._normalize_semantic_router_decision(parsed_decision)
            set_cached_json(
                "semantic_router",
                router_cache_key,
                normalized_decision,
                settings.SEMANTIC_TRANSLATOR_CACHE_TTL_SECONDS,
            )
            emit_structured_log(
                "semantic_router_decision",
                route=normalized_decision.get("route"),
                original_route=normalized_decision.get("original_route"),
                confidence=normalized_decision.get("confidence"),
                detected_intent=normalized_decision.get("detected_intent"),
                requires_time=normalized_decision.get("requires_time"),
                reason_codes=normalized_decision.get("reason_codes"),
            )
            return normalized_decision
        except Exception as router_error:
            emit_structured_log(
                "semantic_router_error",
                level="warning",
                error=str(router_error)[:200],
            )
            return {
                "route": "COMPLEJO",
                "confidence": 0.0,
                "detected_intent": "unknown",
                "requires_time": False,
                "reason_codes": ["router_error", "conservative_policy"],
                "original_route": "COMPLEJO",
            }

    @staticmethod
    def _normalize_surface_text(value: str | None) -> str:
        raw = str(value or "")
        candidate = raw.strip()
        if candidate.startswith("{") and candidate.endswith("}"):
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, dict):
                    payload_text = parsed.get("text")
                    if isinstance(payload_text, str) and payload_text.strip():
                        raw = payload_text
            except Exception:
                pass

        normalized = unicodedata.normalize("NFKD", raw)
        normalized = "".join(char for char in normalized if not unicodedata.combining(char))
        normalized = re.sub(r"\s+", " ", normalized).strip().lower()
        return normalized

    @staticmethod
    def _humanize_column_alias(column_name: str) -> str:
        humanized = str(column_name or "").replace("_", " ").strip()
        return humanized.title() if humanized else "Valor"

    @staticmethod
    def _semantic_groups_for_text(value: str | None) -> set[str]:
        """Domain-agnostic: retorna siempre vacío. Las agrupaciones semánticas
        hardcodeadas fueron eliminadas por sesgo hacia dominios logísticos.
        La resolución de dimensiones ahora se basa en match directo del
        prompt contra nombres de columna + schema profiling."""
        return set()

    @staticmethod
    def _dimension_semantic_alignment_score(segment_norm: str, column_norm: str) -> int:
        """Domain-agnostic: ya no aplica bonificación ni penalización por
        agrupaciones semánticas hardcodeadas. Retorna 0 neutral para que
        el scoring dependa del match textual directo y del schema profile."""
        return 0

    @staticmethod
    def _should_default_to_latest_snapshot(
        surface_prompt: str,
        dataset_contract: dict[str, Any] | None = None,
        schema_profile: dict | None = None,
    ) -> bool:
        dataset_contract = dataset_contract or {}
        schema_profile = schema_profile or {}
        if not dataset_contract:
            return False

        if any(
            marker in surface_prompt
            for marker in (
                "historico",
                "historial",
                "evolucion",
                "tendencia",
                "compar",
                "versus",
                " vs ",
                " contra ",
                " entre ",
                " desde ",
                " hasta ",
                " mensual",
                " semanal",
                " anual",
            )
        ):
            return False

        time_axis = str(dataset_contract.get("time_axis") or "").strip()
        date_columns = [str(value) for value in list(dataset_contract.get("date_columns") or []) if str(value or "").strip()]
        if not time_axis and not date_columns:
            return False

        if bool(dataset_contract.get("snapshot_guard_allowed")):
            return True

        dataset_mode = str(dataset_contract.get("dataset_mode") or "").strip().lower()
        if dataset_mode in {"snapshot", "hybrid"}:
            return True

        if time_axis:
            cardinality = int(schema_profile.get(time_axis, {}).get("cardinality") or 0)
            if cardinality > 1:
                return True

        return len(date_columns) >= 1

    @staticmethod
    def _build_default_latest_snapshot_filters(
        surface_prompt: str,
        columns: list[str],
        dataset_contract: dict[str, Any] | None = None,
        schema_profile: dict | None = None,
    ) -> list[DataFilter]:
        if not SemanticTranslator._should_default_to_latest_snapshot(
            surface_prompt,
            dataset_contract=dataset_contract,
            schema_profile=schema_profile,
        ):
            return []

        available_columns = set(columns or [])
        if "is_latest_snapshot" in available_columns:
            return [
                DataFilter(
                    column="is_latest_snapshot",
                    operator=FilterOperator.EQUALS,
                    value="True",
                )
            ]

        dataset_contract = dataset_contract or {}
        time_axis = str(dataset_contract.get("time_axis") or "").strip()
        if time_axis and time_axis in available_columns:
            return [
                DataFilter(
                    column=time_axis,
                    operator=FilterOperator.EQUALS,
                    value="latest",
                )
            ]

        return []

    @staticmethod
    def _extract_axis_segment(surface_prompt: str, axis_name: str) -> str | None:
        patterns = {
            "x": [
                r"\bx\s+(?:sea|=)\s*(.+?)(?=(?:,\s*y\s+(?:sea|=)|\s+y\s+(?:sea|=)|,\s*(?:y\s+)?color\s+por|$))",
            ],
            "y": [
                r"\by\s+(?:sea|=)\s*(.+?)(?=(?:,\s*(?:y\s+)?color\s+por|$))",
            ],
            "color": [
                r"\b(?:y\s+)?color\s+por\s*(.+?)(?=$)",
                r"\bcolou?r\s+by\s*(.+?)(?=$)",
                r"\bagrupad[oa]\s+por\s*(.+?)(?=$)",
            ],
        }
        for pattern in patterns.get(axis_name, []):
            match = re.search(pattern, surface_prompt, flags=re.IGNORECASE | re.DOTALL)
            if match:
                return match.group(1).strip(" .,:;")
        return None

    @staticmethod
    def _resolve_segment_columns(
        segment: str | None,
        columns: list[str],
        schema_profile: dict | None = None,
        allowed_roles: set[str] | None = None,
    ) -> list[str]:
        if not segment:
            return []

        schema_profile = schema_profile or {}
        segment_norm = normalize_semantic_text(segment)
        compact_segment = segment_norm.replace(" ", "")
        ranked: list[tuple[int, str]] = []

        for column_name in columns:
            role = schema_profile.get(column_name, {}).get("role")
            if allowed_roles and role not in allowed_roles:
                continue

            col_norm = normalize_semantic_text(str(column_name).replace("_", " "))
            compact_col = col_norm.replace(" ", "")
            score = 0

            if compact_col and compact_col in compact_segment:
                score += 120 + len(compact_col)

            col_tokens = [token for token in col_norm.split() if len(token) > 1]
            overlap = sum(1 for token in col_tokens if token in segment_norm)
            score += overlap * 20

            if role == "date" and any(term in segment_norm for term in ("fecha", "date", "venc", "caduc", "expir", "prefercons")):
                score += 5
            if role == "metric" and any(term in segment_norm for term in ("stock", "cantidad", "unidades", "valor", "monto", "ventas", "cajas", "piezas")):
                score += 5
            # Domain-agnostic: no se bonifica por términos hardcodeados de dominio.
            # El scoring de dimensiones depende del match textual directo y del
            # schema profile (cardinalidad, dtype, densidad).

            if score > 0:
                ranked.append((score, column_name))

        ranked.sort(key=lambda item: (-item[0], item[1]))
        resolved: list[str] = []
        for _, column_name in ranked:
            if column_name not in resolved:
                resolved.append(column_name)
        return resolved

    @staticmethod
    def _extract_top_limit(surface_prompt: str) -> int | None:
        """Extrae el límite numérico del prompt del usuario.
        Soporta variantes: 'top 10', 'los 10 materiales', 'las 5 categorías',
        'primeros 15', 'mejores 20', 'principales 8'."""
        patterns = [
            r"\btop\s+(\d{1,3})\b",
            r"\blos\s+(\d{1,3})\b",
            r"\blas\s+(\d{1,3})\b",
            r"\bprimeros?\s+(\d{1,3})\b",
            r"\bmejores?\s+(\d{1,3})\b",
            r"\bprincipales?\s+(\d{1,3})\b",
            r"\b(\d{1,3})\s+(?:materiales|productos|items|categorias|clientes|empleados|registros|elementos)\b",
            r"\b(\d{1,3})\s+(?:mas|más|mayor|mayores|menor|menores)\b",
        ]
        for pattern in patterns:
            match = re.search(pattern, surface_prompt, flags=re.IGNORECASE)
            if match:
                try:
                    return max(1, min(int(match.group(1)), 50))
                except Exception:
                    continue
        return None

    @staticmethod
    def _is_top_n_rollup_request(surface_prompt: str) -> bool:
        if not surface_prompt:
            return False
        has_top_n = SemanticTranslator._extract_top_limit(surface_prompt) is not None
        if not has_top_n:
            return False

        direct_markers = (
            "suma total",
            "suma del total",
            "total mensual",
            "suma del top",
            "sum of top",
            "total del top",
            "total de los top",
            "totales de los",
            "total of top",
            "combined top",
            "acumulado del top",
            "acumulado de los top",
        )
        if any(marker in surface_prompt for marker in direct_markers):
            return True

        has_aggregate_word = any(
            marker in surface_prompt
            for marker in ("suma", "sum", "acumulad", "total", "totales")
        )
        has_temporal = SemanticTranslator._mentions_temporal_language(surface_prompt)
        if has_top_n and has_aggregate_word and has_temporal:
            return True

        # Ej: "no me des la evolución de cada producto, dame la suma ..."
        if has_top_n and has_aggregate_word and re.search(r"\bno\b.*\bcada\b", surface_prompt):
            return True

        return bool(
            re.search(
                r"\b(?:suma|sum|acumulad[oa]|total(?:es)?)\s+(?:del?|de los)\s+(?:top\s+)?\d{1,3}\b",
                surface_prompt,
                flags=re.IGNORECASE,
            )
        )

    @staticmethod
    def _mentions_generic_visual_request(surface_prompt: str) -> bool:
        return any(
            marker in surface_prompt
            for marker in (
                "grafico",
                "grafica",
                "chart",
                "visual",
            )
        )

    @staticmethod
    def _contains_explicit_continuity_marker(surface_prompt: str) -> bool:
        return any(
            marker in surface_prompt
            for marker in (
                "y por",
                "ahora por",
                "profundiza",
                "detalla",
                "desglosa",
                "drill",
                "zoom",
                "mas detalle",
                "más detalle",
                "muestra mas",
                "muestra más",
                "compara",
                "versus",
                "vs",
                "contra",
            )
        )

    @staticmethod
    def _mentions_temporal_language(surface_prompt: str) -> bool:
        return any(
            marker in surface_prompt
            for marker in (
                "fecha",
                "date",
                "tiempo",
                "temporal",
                "periodo",
                "periodos",
                "periodos",
                "dia",
                "dias",
                "semana",
                "semanal",
                "mes",
                "meses",
                "mensual",
                "anio",
                "ano",
                "anual",
                "historico",
                "historial",
                "evolucion",
                "tendencia",
            )
        )

    @staticmethod
    def _contains_analysis_language(surface_prompt: str) -> bool:
        return any(
            marker in surface_prompt
            for marker in (
                "analisis",
                "analiza",
                "analysis",
                "analyze",
                "overview",
                "dashboard",
                "resumen",
                "summary",
                "reporte",
                "report",
                "informe",
                "detalle",
                "desglose",
                "comportamiento",
                "resultado",
                "performance",
                "desempeno",
                "desempeño",
            )
        )

    @staticmethod
    def _infer_default_metric_column(
        surface_prompt: str,
        columns: list[str],
        schema_profile: dict | None = None,
    ) -> str | None:
        schema_profile = schema_profile or {}
        metric_candidates = [
            column_name
            for column_name in columns
            if schema_profile.get(column_name, {}).get("role") == "metric"
        ]
        if not metric_candidates:
            return None
        if len(metric_candidates) == 1:
            return metric_candidates[0]

        compact_prompt = surface_prompt.replace(" ", "")
        ranked: list[tuple[int, str]] = []
        for column_name in metric_candidates:
            col_norm = normalize_semantic_text(str(column_name).replace("_", " "))
            compact_col = col_norm.replace(" ", "")
            score = 0

            if compact_col and compact_col in compact_prompt:
                score += 100 + len(compact_col)

            score += sum(10 for token in col_norm.split() if len(token) > 1 and token in surface_prompt)

            if any(
                keyword in col_norm
                for keyword in (
                    "stock",
                    "cantidad",
                    "venta",
                    "ingreso",
                    "importe",
                    "monto",
                    "precio",
                    "costo",
                    "volumen",
                    "unidades",
                    "piezas",
                )
            ):
                score += 4

            ranked.append((score, column_name))

        ranked.sort(key=lambda item: (-item[0], item[1]))
        return ranked[0][1] if ranked else None

    @staticmethod
    def _resolve_contract_column(
        hint: str | None,
        columns: list[str],
        schema_profile: dict | None = None,
        allowed_roles: set[str] | None = None,
    ) -> str | None:
        if not hint:
            return None

        schema_profile = schema_profile or {}
        if hint in columns:
            role = schema_profile.get(hint, {}).get("role")
            if not allowed_roles or role in allowed_roles:
                return hint

        candidates = SemanticTranslator._resolve_segment_columns(
            hint,
            columns,
            schema_profile=schema_profile,
            allowed_roles=allowed_roles,
        )
        return candidates[0] if candidates else None

    @staticmethod
    def _normalize_router_filters(
        raw_filters: Any,
        columns: list[str],
        schema_profile: dict | None = None,
    ) -> list[dict[str, Any]]:
        if not isinstance(raw_filters, list):
            return []

        schema_profile = schema_profile or {}
        normalized_filters: list[dict[str, Any]] = []
        for filter_row in raw_filters:
            if not isinstance(filter_row, dict):
                continue

            raw_column = str(filter_row.get("column") or "").strip()
            if not raw_column:
                continue

            resolved_column = SemanticTranslator._resolve_contract_column(
                raw_column,
                columns,
                schema_profile=schema_profile,
            )
            if not resolved_column:
                continue

            value = filter_row.get("value")
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            if isinstance(value, list):
                clean_values = []
                for item in value:
                    if item is None:
                        continue
                    if isinstance(item, str) and not item.strip():
                        continue
                    clean_values.append(item)
                if not clean_values:
                    continue
                value = clean_values

            operator = filter_row.get("operator") or "=="
            try:
                validated = DataFilter.model_validate(
                    {
                        "column": resolved_column,
                        "operator": operator,
                        "value": value,
                    }
                )
            except Exception:
                continue
            normalized_filters.append(validated.model_dump(mode="json"))

        return normalized_filters

    @staticmethod
    def _build_plan_from_router_contract(
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
        metric_column = SemanticTranslator._resolve_contract_column(
            contract.get("plot_metric") or contract.get("metric"),
            columns,
            schema_profile=schema_profile,
            allowed_roles={"metric"},
        )
        if not metric_column:
            metric_column = SemanticTranslator._infer_default_metric_column(
                str(contract.get("metric") or ""),
                columns,
                schema_profile=schema_profile,
            )
        if not metric_column:
            return None

        ranking_metric_column = SemanticTranslator._resolve_contract_column(
            contract.get("ranking_metric"),
            columns,
            schema_profile=schema_profile,
            allowed_roles={"metric"},
        )
        ranking_direction = str(contract.get("ranking_direction") or "desc").strip().lower()
        if ranking_direction not in {"desc", "asc"}:
            ranking_direction = "desc"

        positive_filters = SemanticTranslator._normalize_router_filters(
            contract.get("positive_filters"),
            columns,
            schema_profile=schema_profile,
        )
        negative_filters = SemanticTranslator._normalize_router_filters(
            contract.get("negative_filters"),
            columns,
            schema_profile=schema_profile,
        )

        metric_unit = infer_metric_unit_from_column_name(metric_column)
        metric_label = SemanticTranslator._humanize_column_alias(metric_column)

        if intent == "trend":
            date_column = SemanticTranslator._resolve_contract_column(
                contract.get("time_axis"),
                columns,
                schema_profile=schema_profile,
                allowed_roles={"date"},
            )
            if not date_column:
                date_column = SemanticTranslator._pick_primary_date_column(
                    columns,
                    schema_profile=schema_profile,
                    dataset_contract=dataset_contract,
                )
            if not date_column:
                return None

            series_mode = str(contract.get("series_mode") or "none")
            top_n = contract.get("top_n")

            # [V4] Si series_mode=split pero top_n=null (usuario especificó valores exactos
            # como "almacenes 130 y 400"), inferir top_n del tamaño de la lista IN del filtro.
            # Sin esto, split_dimension nunca se asigna y IbisEngine genera una sola línea.
            if not top_n and series_mode in {"split", "sum"}:
                for pf in positive_filters:
                    pf_op = str(
                        getattr(pf.get("operator"), "value", pf.get("operator")) or ""
                    ).strip().lower() if isinstance(pf, dict) else ""
                    pf_val = pf.get("value") if isinstance(pf, dict) else None
                    if pf_op == "in" and isinstance(pf_val, list) and len(pf_val) >= 2:
                        top_n = len(pf_val)
                        print(
                            f"🔄 [SPLIT INFERENCE] top_n inferido de filtro IN: "
                            f"{pf.get('column')} IN {pf_val} → top_n={top_n}"
                        )
                        break

            split_dimension: str | None = None
            split_limit: int | None = None
            if top_n and series_mode in {"split", "sum"}:
                split_dimension = SemanticTranslator._resolve_contract_column(
                    contract.get("dimension"),
                    columns,
                    schema_profile=schema_profile,
                    allowed_roles={"dimension", "identifier"},
                )
                if not split_dimension:
                    return None
                split_limit = max(2, min(int(top_n), 15))

            visual_protocol = VisualProtocol.AREA if contract.get("visual_protocol") == "area_chart" else VisualProtocol.LINE
            date_label = SemanticTranslator._humanize_column_alias(date_column)
            column_aliases = {metric_column: metric_label, date_column: date_label}
            if split_dimension:
                column_aliases[split_dimension] = SemanticTranslator._humanize_column_alias(split_dimension)

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
            dimension_column = SemanticTranslator._resolve_contract_column(
                contract.get("dimension"),
                columns,
                schema_profile=schema_profile,
                allowed_roles={"dimension", "identifier"},
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
                resolved_group = SemanticTranslator._resolve_contract_column(
                    str(group_hint),
                    columns,
                    schema_profile=schema_profile,
                    allowed_roles={"dimension", "identifier", "date"},
                )
                if (
                    resolved_group
                    and resolved_group != dimension_column
                    and resolved_group not in group_by_columns
                ):
                    group_by_columns.append(resolved_group)
            dimension_label = SemanticTranslator._humanize_column_alias(dimension_column)
            return [
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

        if intent == "descriptive":
            dimension_column = SemanticTranslator._resolve_contract_column(
                contract.get("dimension"),
                columns,
                schema_profile=schema_profile,
                allowed_roles={"dimension", "identifier", "date"},
            )
            group_by_columns: list[str] = []
            for group_hint in list(contract.get("group_by") or []):
                resolved_group = SemanticTranslator._resolve_contract_column(
                    str(group_hint),
                    columns,
                    schema_profile=schema_profile,
                    allowed_roles={"dimension", "identifier", "date"},
                )
                if resolved_group and resolved_group not in group_by_columns:
                    group_by_columns.append(resolved_group)
            if not dimension_column and group_by_columns:
                dimension_column = group_by_columns[0]
                group_by_columns = [
                    column_name for column_name in group_by_columns if column_name != dimension_column
                ]

            top_n = contract.get("top_n")
            has_segmented_request = bool(
                dimension_column
                or group_by_columns
                or (isinstance(top_n, int) and top_n > 0)
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
                dimension_label = SemanticTranslator._humanize_column_alias(dimension_column)
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

    @staticmethod
    def _select_default_distribution_visual(
        dimension_column: str,
        schema_profile: dict | None = None,
    ) -> str:
        schema_profile = schema_profile or {}
        cardinality = int(schema_profile.get(dimension_column, {}).get("cardinality") or 0)
        if cardinality and cardinality > 12:
            return "treemap"
        return "bar_chart"

    @staticmethod
    def _select_alternate_distribution_visual(
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

    @staticmethod
    def _looks_broad_analysis_request(prompt: str) -> bool:
        surface_prompt = SemanticTranslator._normalize_surface_text(prompt)
        if not surface_prompt:
            return False
        if SemanticTranslator.is_visual_replacement_request(prompt):
            return False
        if SemanticTranslator._contains_explicit_continuity_marker(surface_prompt):
            return False
        if extract_prompt_visual_requests(prompt):
            return False
        if SemanticTranslator._mentions_generic_visual_request(surface_prompt):
            return False

        structural_markers = (
            " por ",
            " vs ",
            " versus ",
            " x sea ",
            " y sea ",
            " top ",
            " filtro ",
            " filtra ",
            " entre ",
            " desde ",
            " hasta ",
            " mensual",
            " semanal",
            " anual",
            " historico",
            " historial",
            " evolucion",
            " tendencia",
        )
        padded_prompt = f" {surface_prompt} "
        if any(marker in padded_prompt for marker in structural_markers):
            return False

        if not SemanticTranslator._contains_analysis_language(surface_prompt):
            return False

        token_count = len(surface_prompt.split())
        scope_markers = (
            "completo",
            "completa",
            "general",
            "global",
            "overview",
            "dashboard",
            "resumen",
            "summary",
            "reporte",
            "report",
            "informe",
        )
        return token_count <= 8 or any(marker in surface_prompt for marker in scope_markers)

    @staticmethod
    def _extract_primary_dimension_segment(surface_prompt: str) -> str | None:
        top_match = re.search(r"\btop\s+\d{1,3}\s+(.+?)\s+por\s+(.+?)(?=$|,)", surface_prompt, flags=re.IGNORECASE)
        if top_match:
            return top_match.group(1).strip(" .,:;")

        de_por_match = re.search(r"\bde\s+(.+?)\s+por\s+(.+?)(?=$|,)", surface_prompt, flags=re.IGNORECASE)
        if de_por_match:
            return de_por_match.group(2).strip(" .,:;")

        por_match = re.search(
            r"\bpor\s+(.+?)(?=$|,|\s+con\s+|\s+usando\s+|\s+para\s+|\s+del\s+|\s+de\s+)",
            surface_prompt,
            flags=re.IGNORECASE,
        )
        if por_match:
            return por_match.group(1).strip(" .,:;")
        return None

    @staticmethod
    def _pick_primary_date_column(
        columns: list[str],
        schema_profile: dict | None = None,
        dataset_contract: dict[str, Any] | None = None,
    ) -> str | None:
        schema_profile = schema_profile or {}
        dataset_contract = dataset_contract or {}

        time_axis = dataset_contract.get("time_axis")
        if (
            isinstance(time_axis, str)
            and time_axis in columns
            and schema_profile.get(time_axis, {}).get("role") == "date"
        ):
            return time_axis

        date_candidates = [
            column_name
            for column_name in columns
            if schema_profile.get(column_name, {}).get("role") == "date"
        ]
        ranked = sorted(
            date_candidates,
            key=lambda column_name: (
                -int(schema_profile.get(column_name, {}).get("cardinality") or 0),
                column_name,
            ),
        )
        return ranked[0] if ranked else None

    @staticmethod
    def _pick_best_dimension_column(
        prompt: str,
        columns: list[str],
        schema_profile: dict | None = None,
        exclude: set[str] | None = None,
    ) -> str | None:
        schema_profile = schema_profile or {}
        exclude = exclude or set()
        surface_prompt = SemanticTranslator._normalize_surface_text(prompt)
        compact_prompt = surface_prompt.replace(" ", "")
        ranked: list[tuple[int, str]] = []

        for column_name in columns:
            if column_name in exclude:
                continue

            info = schema_profile.get(column_name, {})
            role = info.get("role")
            if role not in {"dimension", "identifier"}:
                continue

            cardinality = int(info.get("cardinality") or 0)
            if cardinality <= 1:
                continue

            # --- Prompt-match detection (antes de aplicar penalties) ---
            col_norm = normalize_semantic_text(str(column_name).replace("_", " "))
            compact_col = col_norm.replace(" ", "")
            has_direct_prompt_match = bool(compact_col and compact_col in compact_prompt)
            token_overlap = sum(1 for token in col_norm.split() if len(token) > 1 and token in surface_prompt)

            # --- Role scoring: si el usuario menciona explícitamente la columna,
            # el role "identifier" NO recibe penalización (user intent > heuristic) ---
            score = 0
            if role == "dimension":
                score += 40
            elif has_direct_prompt_match or token_overlap >= 1:
                score += 20  # Identifier mencionado por el usuario: bonificación moderada
            else:
                score -= 10  # Identifier no mencionado: penalización estándar

            # --- Cardinality scoring: reducido cuando hay match explícito ---
            if cardinality <= 12:
                score += 20 if has_direct_prompt_match else 35
            elif cardinality <= 30:
                score += 20 if has_direct_prompt_match else 25
            elif cardinality <= 100:
                score += 12
            elif cardinality <= 300:
                score += 4
            else:
                score -= 8

            cardinality_ratio = float(info.get("cardinality_ratio") or 0.0)
            if cardinality_ratio >= 0.9:
                score -= 12
            elif cardinality_ratio <= 0.2:
                score += 6

            # --- Prompt match bonus (dominante) ---
            if has_direct_prompt_match:
                score += 80 + len(compact_col)

            score += token_overlap * 12

            ranked.append((score, column_name))

        ranked.sort(key=lambda item: (-item[0], item[1]))
        return ranked[0][1] if ranked else None

    @staticmethod
    def _looks_dimension_analysis_request(prompt: str) -> bool:
        surface_prompt = SemanticTranslator._normalize_surface_text(prompt)
        if not surface_prompt:
            return False
        if SemanticTranslator.is_visual_replacement_request(prompt):
            return False
        if SemanticTranslator._contains_explicit_continuity_marker(surface_prompt):
            return False
        if extract_prompt_visual_requests(prompt):
            return False
        if SemanticTranslator._mentions_generic_visual_request(surface_prompt):
            return False
        if not SemanticTranslator._contains_analysis_language(surface_prompt):
            return False
        return SemanticTranslator._extract_primary_dimension_segment(surface_prompt) is not None

    @staticmethod
    def _has_meaningful_temporal_axis(
        date_column: str | None,
        schema_profile: dict | None = None,
    ) -> bool:
        if not date_column:
            return False
        schema_profile = schema_profile or {}
        cardinality = int(schema_profile.get(date_column, {}).get("cardinality") or 0)
        return cardinality > 1

    @staticmethod
    def _build_dimension_analysis_bundle(
        prompt: str,
        columns: list[str],
        schema_profile: dict | None = None,
        dataset_contract: dict[str, Any] | None = None,
    ) -> Optional[List[AnalysisPlan]]:
        if not settings.DETERMINISTIC_VISUAL_FASTPATH_ENABLED:
            return None
        if not SemanticTranslator._looks_dimension_analysis_request(prompt):
            return None

        schema_profile = schema_profile or {}
        dataset_contract = dataset_contract or {}
        surface_prompt = SemanticTranslator._normalize_surface_text(prompt)
        default_snapshot_filters = SemanticTranslator._build_default_latest_snapshot_filters(
            surface_prompt,
            columns,
            dataset_contract=dataset_contract,
            schema_profile=schema_profile,
        )

        dimension_segment = SemanticTranslator._extract_primary_dimension_segment(surface_prompt)
        dimension_candidates = SemanticTranslator._resolve_segment_columns(
            dimension_segment or surface_prompt,
            columns,
            schema_profile=schema_profile,
            allowed_roles={"dimension", "identifier"},
        )
        if not dimension_candidates:
            return None

        primary_dimension = dimension_candidates[0]
        if int(schema_profile.get(primary_dimension, {}).get("cardinality") or 0) <= 1:
            return None

        metric_column = SemanticTranslator._infer_default_metric_column(
            surface_prompt,
            columns,
            schema_profile=schema_profile,
        )
        if not metric_column:
            return None

        metric_unit = infer_metric_unit_from_column_name(metric_column)
        metric_label = SemanticTranslator._humanize_column_alias(metric_column)
        primary_label = SemanticTranslator._humanize_column_alias(primary_dimension)
        primary_cardinality = int(schema_profile.get(primary_dimension, {}).get("cardinality") or 0)
        primary_limit = primary_cardinality if 0 < primary_cardinality <= 12 else 10
        primary_visual = SemanticTranslator._select_default_distribution_visual(
            primary_dimension,
            schema_profile=schema_profile,
        )

        aliases = {
            metric_column: metric_label,
            primary_dimension: primary_label,
        }
        plans: list[AnalysisPlan] = [
            AnalysisPlan(
                main_intent=DistributionIntent(
                    rationale=(
                        "Priorizo la dimensión solicitada por el usuario como eje principal "
                        "para ordenar el análisis alrededor de la categoría pedida."
                    ),
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

        date_column = SemanticTranslator._pick_primary_date_column(
            columns,
            schema_profile=schema_profile,
            dataset_contract=dataset_contract,
        )
        if SemanticTranslator._has_meaningful_temporal_axis(date_column, schema_profile=schema_profile):
            date_label = SemanticTranslator._humanize_column_alias(date_column)
            trend_aliases = aliases.copy()
            trend_aliases[date_column] = date_label
            plans.append(
                AnalysisPlan(
                    main_intent=TimeTrendIntent(
                        rationale=(
                            "Completo la vista por dimensión con evolución temporal real para "
                            "mostrar si el comportamiento cambia entre periodos del dataset."
                        ),
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

        secondary_dimension = SemanticTranslator._pick_best_dimension_column(
            surface_prompt,
            columns,
            schema_profile=schema_profile,
            exclude={primary_dimension},
        )
        if secondary_dimension:
            secondary_visual = SemanticTranslator._select_alternate_distribution_visual(
                secondary_dimension,
                primary_visual,
                schema_profile=schema_profile,
            )
            secondary_label = SemanticTranslator._humanize_column_alias(secondary_dimension)
            secondary_cardinality = int(schema_profile.get(secondary_dimension, {}).get("cardinality") or 0)
            secondary_limit = secondary_cardinality if 0 < secondary_cardinality <= 12 else 10
            secondary_aliases = {metric_column: metric_label, secondary_dimension: secondary_label}
            plans.append(
                AnalysisPlan(
                    main_intent=DistributionIntent(
                        rationale=(
                            "Añado una segunda dimensión complementaria para contextualizar la "
                            "lectura principal sin depender del planner generativo."
                        ),
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
                        rationale=(
                            "Completo el bundle con un KPI global para conservar referencia de "
                            "magnitud cuando faltan ejes suficientes para una tercera vista."
                        ),
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

    @staticmethod
    def _build_macro_analysis_bundle(
        prompt: str,
        columns: list[str],
        schema_profile: dict | None = None,
        dataset_contract: dict[str, Any] | None = None,
    ) -> Optional[List[AnalysisPlan]]:
        if not settings.DETERMINISTIC_VISUAL_FASTPATH_ENABLED:
            return None
        if not SemanticTranslator._looks_broad_analysis_request(prompt):
            return None

        schema_profile = schema_profile or {}
        dataset_contract = dataset_contract or {}
        surface_prompt = SemanticTranslator._normalize_surface_text(prompt)
        default_snapshot_filters = SemanticTranslator._build_default_latest_snapshot_filters(
            surface_prompt,
            columns,
            dataset_contract=dataset_contract,
            schema_profile=schema_profile,
        )

        metric_column = SemanticTranslator._infer_default_metric_column(
            surface_prompt,
            columns,
            schema_profile=schema_profile,
        )
        if not metric_column:
            return None

        metric_unit = infer_metric_unit_from_column_name(metric_column)
        metric_label = SemanticTranslator._humanize_column_alias(metric_column)
        plans: list[AnalysisPlan] = []
        aliases = {metric_column: metric_label}

        descriptive_title = f"{metric_label} Total"
        if dataset_contract.get("snapshot_guard_allowed"):
            descriptive_title += " (Corte Actual)"
        plans.append(
            AnalysisPlan(
                main_intent=DescriptiveIntent(
                    rationale=(
                        "Priorizo un KPI global para abrir el análisis con la magnitud base "
                        "más representativa del dataset."
                    ),
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

        primary_visual: str | None = None
        date_column = SemanticTranslator._pick_primary_date_column(
            columns,
            schema_profile=schema_profile,
            dataset_contract=dataset_contract,
        )
        if SemanticTranslator._has_meaningful_temporal_axis(date_column, schema_profile=schema_profile):
            date_label = SemanticTranslator._humanize_column_alias(date_column)
            trend_aliases = aliases.copy()
            trend_aliases[date_column] = date_label
            plans.append(
                AnalysisPlan(
                    main_intent=TimeTrendIntent(
                        rationale=(
                            "Agrego una lectura temporal para revelar tendencia y cambio "
                            "cuando el dataset ofrece un eje cronológico real."
                        ),
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

        primary_dimension = SemanticTranslator._pick_best_dimension_column(
            surface_prompt,
            columns,
            schema_profile=schema_profile,
        )
        if primary_dimension:
            primary_visual = SemanticTranslator._select_default_distribution_visual(
                primary_dimension,
                schema_profile=schema_profile,
            )
            dimension_label = SemanticTranslator._humanize_column_alias(primary_dimension)
            dimension_cardinality = int(schema_profile.get(primary_dimension, {}).get("cardinality") or 0)
            limit = dimension_cardinality if 0 < dimension_cardinality <= 12 else 10
            dist_aliases = aliases.copy()
            dist_aliases[primary_dimension] = dimension_label
            plans.append(
                AnalysisPlan(
                    main_intent=DistributionIntent(
                        rationale=(
                            "Incluyo una vista de concentración para identificar qué categorías "
                            "explican el peso operativo dominante."
                        ),
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

        if len(plans) < 3:
            secondary_dimension = SemanticTranslator._pick_best_dimension_column(
                surface_prompt,
                columns,
                schema_profile=schema_profile,
                exclude={primary_dimension} if primary_dimension else set(),
            )
            if secondary_dimension:
                secondary_visual = SemanticTranslator._select_alternate_distribution_visual(
                    secondary_dimension,
                    primary_visual,
                    schema_profile=schema_profile,
                )
                secondary_label = SemanticTranslator._humanize_column_alias(secondary_dimension)
                secondary_cardinality = int(schema_profile.get(secondary_dimension, {}).get("cardinality") or 0)
                secondary_limit = secondary_cardinality if 0 < secondary_cardinality <= 12 else 10
                secondary_aliases = aliases.copy()
                secondary_aliases[secondary_dimension] = secondary_label
                plans.append(
                    AnalysisPlan(
                        main_intent=DistributionIntent(
                            rationale=(
                                "Completo el paquete con una segunda vista categórica para aportar "
                                "otra dimensión explicativa sin depender del planner generativo."
                            ),
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

    @staticmethod
    def _looks_self_contained_visual_request(prompt: str) -> bool:
        surface_prompt = SemanticTranslator._normalize_surface_text(prompt)
        if not surface_prompt:
            return False
        if SemanticTranslator.is_visual_replacement_request(prompt):
            return False
        if SemanticTranslator._contains_explicit_continuity_marker(surface_prompt):
            return False

        has_visual_language = (
            SemanticTranslator._mentions_generic_visual_request(surface_prompt)
            or bool(extract_prompt_visual_requests(prompt))
        )
        if not has_visual_language:
            return False

        return bool(
            re.search(r"\bpor\s+[a-z0-9_ ]{3,}", surface_prompt)
            or re.search(r"\bde\s+[a-z0-9_ ]+\s+por\s+[a-z0-9_ ]{3,}", surface_prompt)
            or (
                SemanticTranslator._extract_axis_segment(surface_prompt, "x")
                and SemanticTranslator._extract_axis_segment(surface_prompt, "y")
            )
        )

    @staticmethod
    def _build_explicit_scatter_plan(
        prompt: str,
        columns: list[str],
        schema_profile: dict | None = None,
    ) -> Optional[List[AnalysisPlan]]:
        if not settings.DETERMINISTIC_VISUAL_FASTPATH_ENABLED:
            return None

        requested_visuals = extract_prompt_visual_requests(prompt)
        if "scatter_plot" not in requested_visuals:
            return None

        surface_prompt = SemanticTranslator._normalize_surface_text(prompt)
        if " x " not in f" {surface_prompt} " or " y " not in f" {surface_prompt} ":
            return None

        schema_profile = schema_profile or {}
        x_segment = SemanticTranslator._extract_axis_segment(surface_prompt, "x")
        y_segment = SemanticTranslator._extract_axis_segment(surface_prompt, "y")
        color_segment = SemanticTranslator._extract_axis_segment(surface_prompt, "color")

        if not x_segment or not y_segment:
            return None

        x_date_candidates = SemanticTranslator._resolve_segment_columns(
            x_segment,
            columns,
            schema_profile=schema_profile,
            allowed_roles={"date"},
        )
        x_metric_candidates = SemanticTranslator._resolve_segment_columns(
            x_segment,
            columns,
            schema_profile=schema_profile,
            allowed_roles={"metric"},
        )
        y_metric_candidates = SemanticTranslator._resolve_segment_columns(
            y_segment,
            columns,
            schema_profile=schema_profile,
            allowed_roles={"metric"},
        )
        color_candidates = SemanticTranslator._resolve_segment_columns(
            color_segment,
            columns,
            schema_profile=schema_profile,
            allowed_roles={"dimension", "identifier"},
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

        title = f"Dispersión de {SemanticTranslator._humanize_column_alias(y_metric)}"
        if len(x_date_candidates) >= 2:
            title += " vs. Días al Vencimiento"
        else:
            title += f" vs. {SemanticTranslator._humanize_column_alias(scatter_metrics[0])}"
        if dimension_col:
            title += f" por {SemanticTranslator._humanize_column_alias(dimension_col)}"

        aliases = {
            column_name: SemanticTranslator._humanize_column_alias(column_name)
            for column_name in [*scatter_metrics, dimension_col]
            if column_name
        }
        plan = AnalysisPlan(
            main_intent=DiagnosticIntent(
                rationale=(
                    "Priorizo una vista relacional explícita para medir dispersión y contraste "
                    "entre la métrica operativa y la variable pedida por el usuario."
                ),
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
            prompt=prompt[:200],
            visual="scatter_plot",
            metrics=scatter_metrics,
            dimension=dimension_col,
        )
        return [plan]

    @staticmethod
    def _build_explicit_trend_plan(
        prompt: str,
        columns: list[str],
        schema_profile: dict | None = None,
        allow_non_visual_prompt: bool = False,
    ) -> Optional[List[AnalysisPlan]]:
        if not settings.DETERMINISTIC_VISUAL_FASTPATH_ENABLED:
            return None

        requested_visuals = extract_prompt_visual_requests(prompt)
        surface_prompt = SemanticTranslator._normalize_surface_text(prompt)
        generic_visual_request = SemanticTranslator._mentions_generic_visual_request(surface_prompt)
        if not requested_visuals and not generic_visual_request and not allow_non_visual_prompt:
            return None

        requested_visual = requested_visuals[0] if requested_visuals else "line_chart"
        if requested_visual not in {"line_chart", "area_chart"}:
            return None
        if not requested_visuals and not SemanticTranslator._mentions_temporal_language(surface_prompt):
            return None

        schema_profile = schema_profile or {}
        x_segment = SemanticTranslator._extract_axis_segment(surface_prompt, "x")
        y_segment = SemanticTranslator._extract_axis_segment(surface_prompt, "y")

        date_candidates = SemanticTranslator._resolve_segment_columns(
            x_segment or surface_prompt,
            columns,
            schema_profile=schema_profile,
            allowed_roles={"date"},
        )
        metric_candidates = SemanticTranslator._resolve_segment_columns(
            y_segment or surface_prompt,
            columns,
            schema_profile=schema_profile,
            allowed_roles={"metric"},
        )

        if not date_candidates or not metric_candidates:
            de_por_match = re.search(r"\bde\s+(.+?)\s+por\s+(.+?)(?=$|,)", surface_prompt, flags=re.IGNORECASE)
            if de_por_match:
                if not metric_candidates:
                    metric_candidates = SemanticTranslator._resolve_segment_columns(
                        de_por_match.group(1),
                        columns,
                        schema_profile=schema_profile,
                        allowed_roles={"metric"},
                    )
                if not date_candidates:
                    date_candidates = SemanticTranslator._resolve_segment_columns(
                        de_por_match.group(2),
                        columns,
                        schema_profile=schema_profile,
                        allowed_roles={"date"},
                    )

        if not date_candidates and SemanticTranslator._mentions_temporal_language(surface_prompt):
            fallback_date_column = SemanticTranslator._pick_primary_date_column(
                columns,
                schema_profile=schema_profile,
                dataset_contract={},
            )
            if fallback_date_column:
                date_candidates = [fallback_date_column]

        if not metric_candidates:
            default_metric = SemanticTranslator._infer_default_metric_column(
                surface_prompt,
                columns,
                schema_profile=schema_profile,
            )
            if default_metric:
                metric_candidates = [default_metric]

        if not date_candidates or not metric_candidates:
            return None

        date_column = date_candidates[0]
        metric_column = metric_candidates[0]
        explicit_top_limit = SemanticTranslator._extract_top_limit(surface_prompt)
        split_dimension: str | None = None
        split_limit: int | None = None
        top_n_aggregation_mode = "split"

        if explicit_top_limit is not None:
            split_segment = SemanticTranslator._extract_primary_dimension_segment(surface_prompt)
            top_segment_match = re.search(
                r"\btop\s+\d{1,3}\s+(.+?)(?=$|,|\s+con\s+|\s+de\s+|\s+en\s+|\s+para\s+)",
                surface_prompt,
                flags=re.IGNORECASE,
            )
            if top_segment_match:
                top_segment = top_segment_match.group(1).strip(" .,:;")
                if not split_segment or split_segment in {"fecha", "date", "periodo", "periodos", "tiempo"}:
                    split_segment = top_segment
            split_segment = split_segment or surface_prompt
            split_candidates = SemanticTranslator._resolve_segment_columns(
                split_segment,
                columns,
                schema_profile=schema_profile,
                allowed_roles={"dimension", "identifier"},
            )
            for candidate in split_candidates:
                if candidate not in {date_column, metric_column}:
                    split_dimension = candidate
                    break
            if not split_dimension:
                fallback_split_dimension = SemanticTranslator._pick_best_dimension_column(
                    surface_prompt,
                    columns,
                    schema_profile=schema_profile,
                    exclude={date_column, metric_column},
                )
                if fallback_split_dimension:
                    split_dimension = fallback_split_dimension
            if split_dimension:
                split_limit = max(2, min(int(explicit_top_limit), 15))
                if SemanticTranslator._is_top_n_rollup_request(surface_prompt):
                    top_n_aggregation_mode = "sum"

        metric_unit = infer_metric_unit_from_column_name(metric_column)
        visual_protocol = VisualProtocol.LINE if requested_visual == "line_chart" else VisualProtocol.AREA

        metric_label = SemanticTranslator._humanize_column_alias(metric_column)
        date_label = SemanticTranslator._humanize_column_alias(date_column)
        if split_dimension and split_limit:
            split_label = SemanticTranslator._humanize_column_alias(split_dimension)
            if top_n_aggregation_mode == "sum":
                title = f"Evolución de {metric_label} (Suma Top {split_limit} {split_label}) por {date_label}"
            else:
                title = f"Evolución de {metric_label} por {split_label} (Top {split_limit})"
        else:
            title = f"Evolución de {metric_label} por {date_label}"

        plan = AnalysisPlan(
            main_intent={
                "type": "trend",
                "rationale": (
                    "Priorizo una lectura temporal explícita para seguir la evolución de la métrica "
                    "sobre el eje de tiempo pedido por el usuario."
                ),
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
                **({split_dimension: SemanticTranslator._humanize_column_alias(split_dimension)} if split_dimension else {}),
            },
            metric_polarity=MetricPolarity.NEUTRAL,
        )
        emit_structured_log(
            "semantic_translator_fast_path_hit",
            prompt=prompt[:200],
            visual=requested_visual,
            date_column=date_column,
            metric=metric_column,
            split_dimension=split_dimension,
            split_limit=split_limit,
            top_n_aggregation_mode=top_n_aggregation_mode,
        )
        return [plan]

    @staticmethod
    def _build_explicit_distribution_plan(
        prompt: str,
        columns: list[str],
        schema_profile: dict | None = None,
        dataset_contract: dict[str, Any] | None = None,
    ) -> Optional[List[AnalysisPlan]]:
        if not settings.DETERMINISTIC_VISUAL_FASTPATH_ENABLED:
            return None

        requested_visuals = extract_prompt_visual_requests(prompt)
        surface_prompt = SemanticTranslator._normalize_surface_text(prompt)
        generic_visual_request = SemanticTranslator._mentions_generic_visual_request(surface_prompt)
        if not requested_visuals and not generic_visual_request:
            return None

        requested_visual = requested_visuals[0] if requested_visuals else None
        if requested_visual and requested_visual not in {"bar_chart", "pie_chart", "treemap", "funnel_chart"}:
            return None

        schema_profile = schema_profile or {}
        dataset_contract = dataset_contract or {}
        explicit_top_limit = SemanticTranslator._extract_top_limit(surface_prompt)
        top_requested = explicit_top_limit is not None
        default_snapshot_filters = SemanticTranslator._build_default_latest_snapshot_filters(
            surface_prompt,
            columns,
            dataset_contract=dataset_contract,
            schema_profile=schema_profile,
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

        dimension_candidates = SemanticTranslator._resolve_segment_columns(
            dimension_segment or surface_prompt,
            columns,
            schema_profile=schema_profile,
            allowed_roles={"dimension", "identifier"},
        )
        metric_candidates = SemanticTranslator._resolve_segment_columns(
            metric_segment or surface_prompt,
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

        if not dimension_candidates or not metric_candidates:
            return None

        dimension_column = dimension_candidates[0]
        metric_column = metric_candidates[0]
        cardinality = int(schema_profile.get(dimension_column, {}).get("cardinality") or 0)
        limit = explicit_top_limit
        if limit is None:
            if cardinality and cardinality <= 12:
                limit = cardinality
            else:
                limit = 10

        selected_visual = requested_visual or SemanticTranslator._select_default_distribution_visual(
            dimension_column,
            schema_profile=schema_profile,
        )
        metric_unit = infer_metric_unit_from_column_name(metric_column)
        visual_protocol = {
            "bar_chart": VisualProtocol.BAR,
            "pie_chart": VisualProtocol.PIE,
            "treemap": VisualProtocol.TREEMAP,
            "funnel_chart": VisualProtocol.FUNNEL,
        }[selected_visual]

        if top_requested:
            title = (
                f"Top {limit} {SemanticTranslator._humanize_column_alias(dimension_column)} "
                f"por {SemanticTranslator._humanize_column_alias(metric_column)}"
            )
        else:
            title = (
                f"{SemanticTranslator._humanize_column_alias(metric_column)} "
                f"por {SemanticTranslator._humanize_column_alias(dimension_column)}"
            )

        plan = AnalysisPlan(
            main_intent={
                "type": "distribution",
                "rationale": (
                    "Priorizo una vista de concentración explícita para ordenar las categorías "
                    "según la métrica solicitada y exponer el ranking dominante."
                ),
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
                metric_column: SemanticTranslator._humanize_column_alias(metric_column),
                dimension_column: SemanticTranslator._humanize_column_alias(dimension_column),
            },
            metric_polarity=MetricPolarity.NEUTRAL,
        )
        emit_structured_log(
            "semantic_translator_fast_path_hit",
            prompt=prompt[:200],
            visual=selected_visual,
            metric=metric_column,
            dimension=dimension_column,
            limit=limit,
        )
        return [plan]

    @staticmethod
    def _build_deterministic_visual_plan(
        prompt: str,
        columns: list[str],
        schema_profile: dict | None = None,
        dataset_contract: dict[str, Any] | None = None,
        allow_non_visual_prompt: bool = False,
    ) -> Optional[List[AnalysisPlan]]:
        builders = (
            lambda p, c, s, d: SemanticTranslator._build_explicit_scatter_plan(p, c, s),
            lambda p, c, s, d: SemanticTranslator._build_explicit_trend_plan(
                p,
                c,
                s,
                allow_non_visual_prompt=allow_non_visual_prompt,
            ),
            lambda p, c, s, d: SemanticTranslator._build_explicit_distribution_plan(p, c, s, d),
        )
        for builder in builders:
            plans = builder(prompt, columns, schema_profile, dataset_contract)
            if plans:
                return plans
        return None

    @staticmethod
    def _apply_top_n_rollup_mode_to_plans(
        prompt: str,
        plans: list[AnalysisPlan],
    ) -> list[AnalysisPlan]:
        emit_structured_log(
            "semantic_translator_legacy_rollup_postprocessor_disabled",
            prompt=prompt[:200],
            plan_count=len(plans or []),
        )
        return plans

    @staticmethod
    def _detect_prompt_complexity(surface_prompt: str) -> dict[str, Any]:
        """
        Clasificador local de complejidad. No reemplaza al planner profundo:
        solo decide si un fast-path determinístico tiene evidencia suficiente
        para resolver instrucciones restrictivas sin perder intención.
        """
        if not surface_prompt:
            return {
                "score": 0,
                "is_complex": False,
                "has_top_n": False,
                "has_temporal": False,
                "requires_rollup": False,
                "has_negated_split": False,
                "has_restrictive_marker": False,
            }

        has_top_n = SemanticTranslator._extract_top_limit(surface_prompt) is not None
        has_temporal = SemanticTranslator._mentions_temporal_language(surface_prompt)
        requires_rollup = SemanticTranslator._is_top_n_rollup_request(surface_prompt)
        has_negated_split = bool(
            re.search(
                r"\bno\b.{0,80}\b(?:cada|individual|separad[ao]s?|desglosad[ao]s?|lineas?|series?)\b",
                surface_prompt,
                flags=re.IGNORECASE,
            )
        )
        has_restrictive_marker = any(
            marker in surface_prompt
            for marker in (
                "pero",
                "solo",
                "solamente",
                "exclusivamente",
                "excepto",
                "salvo",
                "sin ",
                "en lugar de",
                "no muestres",
                "no me des",
                "no mostrar",
                "dame la suma",
                "consolid",
                "agrupad",
                "suma total",
            )
        )

        score = 0
        score += 2 if has_top_n and has_temporal else 0
        score += 3 if requires_rollup else 0
        score += 2 if has_negated_split else 0
        score += 1 if has_restrictive_marker else 0
        score += 1 if len(surface_prompt.split()) >= 18 else 0

        return {
            "score": score,
            "is_complex": score >= 3,
            "has_top_n": has_top_n,
            "has_temporal": has_temporal,
            "requires_rollup": requires_rollup,
            "has_negated_split": has_negated_split,
            "has_restrictive_marker": has_restrictive_marker,
        }

    @staticmethod
    def _fast_path_unresolved_constraints(
        prompt: str,
        plans: list[AnalysisPlan] | None,
    ) -> list[str]:
        surface_prompt = SemanticTranslator._normalize_surface_text(prompt)
        complexity = SemanticTranslator._detect_prompt_complexity(surface_prompt)
        if not complexity.get("is_complex"):
            return []

        plans = list(plans or [])
        trend_plans = [
            plan
            for plan in plans
            if getattr(getattr(plan, "main_intent", None), "type", None) == "trend"
        ]
        unresolved: list[str] = []

        if complexity["has_temporal"] and complexity["has_top_n"] and not trend_plans:
            unresolved.append("temporal_top_n_requires_trend")

        if complexity["requires_rollup"]:
            satisfied_rollup = any(
                getattr(plan.main_intent, "split_dimension", None)
                and getattr(plan.main_intent, "split_limit", None)
                and getattr(plan.main_intent, "top_n_aggregation_mode", None) == "sum"
                for plan in trend_plans
            )
            if not satisfied_rollup:
                unresolved.append("top_n_rollup_not_satisfied")

        if complexity["has_negated_split"]:
            split_mode_used = any(
                getattr(plan.main_intent, "split_dimension", None)
                and getattr(plan.main_intent, "top_n_aggregation_mode", "split") != "sum"
                for plan in trend_plans
            )
            if split_mode_used:
                unresolved.append("negated_split_not_satisfied")

        return unresolved

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
                emit_structured_log(
                    "semantic_translator_cache_hit",
                    prompt=prompt[:200],
                    plan_count=len(restored_plans),
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
        model = genai.GenerativeModel(
            model_name='gemini-3-flash-preview', # Potencia para entender protocolos
            generation_config={"response_mime_type": "application/json", "temperature": 0.0}
        )

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
            response = model.generate_content(f"{system_instruction}\n\nUSUARIO: {prompt}")
            clean_json = response.text.strip()
            print(f"🕵️ [SEMANTIC STRATEGIST] Protocolo Activado: {clean_json[:200]}...") 
            # 🎯 [FASE 3B] Multi-Plan: Aceptamos Dict o Lista, retornamos SIEMPRE lista.
            parsed_data = SemanticTranslator._parse_translator_payload(clean_json)
            plans: List[AnalysisPlan] = []
            
            if isinstance(parsed_data, list):
                # Triple Vista / Multi-Plan: Validar cada plan individualmente
                for i, item in enumerate(parsed_data[:5]):  # Max 5 planes [FASE 3C: subido de 3]
                    try:
                        # 🛡️ [FASE 4] ANTI-ALUCINACIÓN (Pre-Flight Filter)
                        # Filtramos cualquier columna inventada por Gemini antes de intentar validarla
                        if 'main_intent' in item:
                            intent = item['main_intent']
                            
                            # Limpieza de Group By
                            if 'group_by' in intent and isinstance(intent['group_by'], list):
                                intent['group_by'] = [c for c in intent['group_by'] if c in columns]
                                
                            # Limpieza de Metrics
                            if 'metrics' in intent and isinstance(intent['metrics'], list):
                                intent['metrics'] = [c for c in intent['metrics'] if c in columns]
                            elif 'primary_metric' in intent and isinstance(intent['primary_metric'], str):
                                if intent['primary_metric'] not in columns:
                                    intent['primary_metric'] = None
                                    
                            # Limpieza de Filtros
                            if 'filters' in intent and isinstance(intent['filters'], list):
                                intent['filters'] = [f for f in intent['filters'] if isinstance(f, dict) and f.get('column') in columns]

                            if 'negative_filters' in intent and isinstance(intent['negative_filters'], list):
                                intent['negative_filters'] = [
                                    f for f in intent['negative_filters']
                                    if isinstance(f, dict) and f.get('column') in columns
                                ]

                            for metric_field in ('plot_metric', 'ranking_metric'):
                                if metric_field in intent and isinstance(intent[metric_field], str):
                                    if intent[metric_field] not in columns:
                                        intent[metric_field] = None
                                
                            # Limpieza Predictiva Temporal
                            if 'time_dimension' in intent and isinstance(intent['time_dimension'], str):
                                if intent['time_dimension'] not in columns:
                                    intent['time_dimension'] = None
                                    
                            # Limpieza Diagnóstica (value_column)
                            if 'value_column' in intent and isinstance(intent['value_column'], str):
                                if intent['value_column'] not in columns:
                                    intent['value_column'] = None

                            # Validaciones restrictivas: Si Pydantic exige al menos un elemento o falla, se descartará el plan automáticamente en Pydantic.
                            
                        # Limpieza de Filters Globales de Charting
                        if 'filters' in item and isinstance(item['filters'], list):
                            item['filters'] = [f for f in item['filters'] if isinstance(f, dict) and f.get('column') in columns]

                        plans.append(AnalysisPlan.model_validate(item))
                        print(f"✅ [MULTI-PLAN] Plan {i+1} validado: {item.get('title', 'Sin título')[:60]}")
                    except Exception as val_e:
                        print(f"⚠️ [MULTI-PLAN] Plan {i+1} inválido (Alucinación bloqueada o schema roto): {val_e}")
            else:
                # Plan único (caso más común)
                if isinstance(parsed_data, dict) and 'main_intent' in parsed_data:
                    intent = parsed_data['main_intent']
                    if isinstance(intent, dict):
                        if 'filters' in intent and isinstance(intent['filters'], list):
                            intent['filters'] = [f for f in intent['filters'] if isinstance(f, dict) and f.get('column') in columns]
                        if 'negative_filters' in intent and isinstance(intent['negative_filters'], list):
                            intent['negative_filters'] = [
                                f for f in intent['negative_filters']
                                if isinstance(f, dict) and f.get('column') in columns
                            ]
                        if 'group_by' in intent and isinstance(intent['group_by'], list):
                            intent['group_by'] = [c for c in intent['group_by'] if c in columns]
                        if 'metrics' in intent and isinstance(intent['metrics'], list):
                            intent['metrics'] = [c for c in intent['metrics'] if c in columns]
                        for metric_field in ('plot_metric', 'ranking_metric', 'value_column', 'metric', 'dimension', 'date_column'):
                            if metric_field in intent and isinstance(intent[metric_field], str):
                                if intent[metric_field] not in columns:
                                    intent[metric_field] = None
                plans.append(AnalysisPlan.model_validate(parsed_data))
            
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
    def _detect_literal_filters(prompt: str, dimension_values: Dict[str, list]) -> List[DataFilter]:
        """
        Escanea el prompt del usuario buscando tokens que coincidan con valores REALES
        del dataset en columnas dimensionales. Retorna filtros obligatorios.
        
        Args:
            prompt: Texto del prompt del usuario
            dimension_values: Dict {columna: [valores_únicos]} de columnas categóricas
        
        Returns:
            Lista de DataFilter con filtros detectados
        """
        if not dimension_values:
            return []
        
        detected_filters: List[DataFilter] = []
        
        # Stopwords que NUNCA deben matchear como valores de dato
        stopwords = {
            'un', 'una', 'el', 'la', 'los', 'las', 'de', 'del', 'en', 'por',
            'para', 'con', 'que', 'como', 'se', 'al', 'es', 'son', 'fue',
            'analisis', 'análisis', 'analiza', 'realiza', 'muestra', 'dame',
            'quiero', 'haz', 'grafico', 'gráfico', 'total', 'promedio',
            'tendencia', 'evolución', 'evolucion', 'distribución', 'distribucion',
            'profundiza', 'detalla', 'compara', 'nuevo', 'distinto',
            'más', 'mas', 'cual', 'cuál', 'datos', 'información', 'informacion',
            'ubicación', 'ubicacion', 'almacén', 'almacen', 'material', 'producto',
            'tipo', 'categoría', 'categoria', 'stock', 'cantidad', 'precio',
            'and', 'the', 'for', 'with', 'from', 'this', 'that'
        }
        
        # Tokenizar prompt: palabras y frases entre comillas
        # Primero buscar frases entrecomilladas (máxima prioridad)
        quoted_phrases = re.findall(r'["\']([^"\']+)["\']', prompt)
        
        # Luego tokens individuales (palabras de >2 caracteres que no sean stopwords)
        raw_tokens = prompt.split()
        clean_tokens = [
            t.strip('.,;:!?()[]{}"\'')
            for t in raw_tokens
            if len(t.strip('.,;:!?()[]{}"\'' )) > 2 and t.lower().strip('.,;:!?()[]{}"\'' ) not in stopwords
        ]
        
        # Combinar: frases entrecomilladas primero, luego tokens
        search_terms = [(phrase, True) for phrase in quoted_phrases] + [(token, False) for token in clean_tokens]
        
        matched_columns = set()  # Evitar duplicados por columna
        
        # 🧠 [FASE 4B] Dynamic Cardinality Indexer
        # Pre-procesamiento Optimizado: Convertir listas a SETS para búsqueda O(1)
        # Solo procesamos columnas que no hayamos matcheado aún (lazy)
        
        # Para evitar recalcular sets en cada token, lo hacemos por demanda o pre-calculamos.
        # Dado que son < 10k items, pre-calcular todo es rápido (<50ms).
        # Estructura: value_upper -> (original_value, col_name)
        # Manejo de colisiones: Si un valor existe en 2 filas, priorizamos la primera (o la más corta?)
        # Mejor estrategia: scan token vs each column set.
        
        columns_sets = {}
        for col, vals in dimension_values.items():
            # Filtramos None y convertimos a Upper Set
            if vals:
                columns_sets[col] = {str(v).upper(): v for v in vals if v is not None}
                
        for term, is_quoted in search_terms:
            term_upper = term.upper().strip()
            if len(term_upper) < 2:
                continue
                
            for col_name, val_map in columns_sets.items():
                if col_name in matched_columns:
                    continue  # Ya matcheamos esta columna
                
                # 🚀 Fase 1: Búsqueda O(1) en Hash Map — coincidencia exacta
                if term_upper in val_map:
                    original_value = val_map[term_upper]
                    
                    detected_filters.append(
                        DataFilter(
                            column=col_name,
                            operator=FilterOperator.EQUALS,
                            value=str(original_value)
                        )
                    )
                    matched_columns.add(col_name)
                    print(f"🎯 [LITERAL FILTER] Match exacto: '{term}' → {col_name} == '{original_value}'")
                    break  # Un token solo puede matchear una columna

                # 🔍 [V2] Fase 2: Fuzzy-Form Matching — plural/singular
                # Si el token no hace match exacto, buscar si algún valor del dataset
                # es prefijo del token o viceversa (diferencia máxima de 3 caracteres).
                # Estrategia schema-agnostic: no hardcodea reglas del idioma,
                # solo compara longitudes y prefijos para capturar:
                #   "egresos" → "Egreso", "ingresos" → "Ingreso", "ventas" → "Venta"
                # Solo aplica si el token es "suficientemente largo" (>4 chars) para
                # evitar falsos positivos con palabras cortas.
                if not is_quoted and len(term_upper) > 4:
                    best_match: str | None = None
                    best_diff: int = 4  # Máximo de diferencia de caracteres aceptable
                    for candidate_upper, candidate_original in val_map.items():
                        len_term = len(term_upper)
                        len_cand = len(candidate_upper)
                        len_diff = abs(len_term - len_cand)
                        if len_diff >= best_diff:
                            continue
                        # Verificar que el más corto es prefijo del más largo
                        shorter = term_upper if len_term <= len_cand else candidate_upper
                        longer = candidate_upper if len_term <= len_cand else term_upper
                        if longer.startswith(shorter):
                            best_match = candidate_original
                            best_diff = len_diff

                    if best_match is not None:
                        detected_filters.append(
                            DataFilter(
                                column=col_name,
                                operator=FilterOperator.EQUALS,
                                value=str(best_match)
                            )
                        )
                        matched_columns.add(col_name)
                        print(
                            f"🎯 [LITERAL FILTER] Match fuzzy-form: '{term}' → "
                            f"{col_name} == '{best_match}' (dif={best_diff} chars)"
                        )
                        break  # Un token solo puede matchear una columna
        
        if detected_filters:
            print(f"🎯 [LITERAL FILTER] {len(detected_filters)} filtro(s) detectado(s)")
        
        return detected_filters

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
