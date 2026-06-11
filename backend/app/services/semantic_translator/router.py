"""
Semantic Translator — Router Module (Fase 0.1, Paso 2/5)

[REFACTOR 2026-06-11] Este archivo es parte de la Operacion Refactor
documentada en AGENTS.md §15.1 Plan 1 / Fase 0.1.

Responsabilidad: Clasificacion de intent, deteccion de patrones linguisticos y resolucion de segmentos del prompt.

Funciones module-level extraidas desde core.py. Los metodos en core.py
ahora delegan a estas funciones pasando `SemanticTranslator` como
primer parametro (la "instance") — un requisito del patron de
delegacion que mantiene la API publica intacta.

Regla de oro: prohibido romper funcionalidades existentes. Todos los
tests existentes y los 3 call sites siguen funcionando sin cambios
porque la API publica de SemanticTranslator permanece intacta.
"""

from __future__ import annotations

import json
import re
import unicodedata
from json.decoder import JSONDecodeError, JSONDecoder
from typing import Any, Dict, List, Optional

from app.core.semantic_grammar import AnalysisPlan, DataFilter, FilterOperator
from app.services.metric_semantics import normalize_semantic_text
from app.services.visual_recommendation_engine import extract_prompt_visual_requests
from app.services.semantic_translator.core import SemanticTranslator


# ============================================================================
# Funciones module-level para router
# ============================================================================
# Cada funcion toma `instance` como primer parametro (la clase
# SemanticTranslator) por compatibilidad con el patron de delegacion.
# Las funciones son static en su naturaleza original (no usan estado de
# instancia); el parametro `instance` se ignora en el cuerpo.


def normalize_surface_text(instance, value: str | None):
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


def humanize_column_alias(instance, column_name: str):
    humanized = str(column_name or "").replace("_", " ").strip()
    return humanized.title() if humanized else "Valor"


def semantic_groups_for_text(instance, value: str | None):
    """Domain-agnostic: retorna siempre vacío. Las agrupaciones semánticas
    hardcodeadas fueron eliminadas por sesgo hacia dominios logísticos.
    La resolución de dimensiones ahora se basa en match directo del
    prompt contra nombres de columna + schema profiling."""
    return set()


def dimension_semantic_alignment_score(instance, segment_norm: str, column_norm: str):
    """Domain-agnostic: ya no aplica bonificación ni penalización por
    agrupaciones semánticas hardcodeadas. Retorna 0 neutral para que
    el scoring dependa del match textual directo y del schema profile."""
    return 0


def extract_axis_segment(instance, surface_prompt: str, axis_name: str):
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


def resolve_segment_columns(instance, segment: str | None, columns: list[str], schema_profile: dict | None = None, allowed_roles: set[str] | None = None):
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


def extract_top_limit(instance, surface_prompt: str):
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


def is_top_n_rollup_request(instance, surface_prompt: str):
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


def mentions_generic_visual_request(instance, surface_prompt: str):
    return any(
        marker in surface_prompt
        for marker in (
            "grafico",
            "grafica",
            "chart",
            "visual",
        )
    )


def contains_explicit_continuity_marker(instance, surface_prompt: str):
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


def mentions_temporal_language(instance, surface_prompt: str):
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


def contains_analysis_language(instance, surface_prompt: str):
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


def infer_default_metric_column(instance, surface_prompt: str, columns: list[str], schema_profile: dict | None = None):
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


def select_default_distribution_visual(instance, dimension_column: str, schema_profile: dict | None = None):
    schema_profile = schema_profile or {}
    cardinality = int(schema_profile.get(dimension_column, {}).get("cardinality") or 0)
    if cardinality and cardinality > 12:
        return "treemap"
    return "bar_chart"


def select_alternate_distribution_visual(instance, dimension_column: str, primary_visual: str | None, schema_profile: dict | None = None):
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


def looks_broad_analysis_request(instance, prompt: str):
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


def extract_primary_dimension_segment(instance, surface_prompt: str):
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


def looks_dimension_analysis_request(instance, prompt: str):
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


def has_meaningful_temporal_axis(instance, date_column: str | None, schema_profile: dict | None = None):
    if not date_column:
        return False
    schema_profile = schema_profile or {}
    cardinality = int(schema_profile.get(date_column, {}).get("cardinality") or 0)
    return cardinality > 1


def looks_self_contained_visual_request(instance, prompt: str):
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


def detect_prompt_complexity(instance, surface_prompt: str):
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


def fast_path_unresolved_constraints(instance, prompt: str, plans: list[AnalysisPlan] | None):
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

