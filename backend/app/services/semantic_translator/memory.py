"""
Semantic Translator — Memory Module (Fase 0.1, Paso 5/5)

[REFACTOR 2026-06-10] Este archivo es parte de la Operacion Refactor
documentada en AGENTS.md §15.1 Plan 1 / Fase 0.1.

Responsabilidad: Memoria de sesion y continuidad conversacional.

Metodos extraidos desde core.py:
  - should_bypass_memory_context (originalmente linea 2487) — publico,
    usado por analysis_tasks.py para decidir si inyectar contexto de
    memoria
  - _classify_memory_intent (originalmente linea 2980) — clasifica
    intent de memoria (continue / change / new_analysis)
  - is_visual_replacement_request (originalmente linea 3077) — publico,
    usado por analysis_tasks.py para detectar reemplazos visuales
  - evaluate_continuity (originalmente linea 3245) — publico, usado
    por analysis_tasks.py para evaluar continuidad entre prompts

Estado actual: METODOS YA EXTRAIDOS. En core.py, los metodos son
ahora delegadores de una linea que llaman a las funciones
module-level de este archivo. La compatibilidad hacia atras se
mantiene porque los metodos siguen existiendo en la clase
SemanticTranslator (los call sites no requieren cambios).

Regla de oro: prohibido romper funcionalidades existentes. Todos
los tests existentes y los 3 call sites siguen funcionando sin
cambios.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from app.core.config import settings
from app.core.semantic_grammar import DataFilter
from app.services.metric_semantics import normalize_semantic_text
from app.services.visual_recommendation_engine import extract_prompt_visual_requests


# ============================================================================
# Memoria de sesion — funciones module-level
# ============================================================================
# Estas funciones eran originalmente metodos privados/publicos de
# SemanticTranslator en core.py. Se extrajeron para reducir el tamaño
# del monolito. Los metodos en core.py ahora delegan a estas
# funciones pasando `self` (la instancia de SemanticTranslator) como
# primer parametro.
#
# Esto preserva el acceso a otros metodos de la clase via `instance`
# (e.g., `instance._normalize_surface_text(prompt)`).


def should_bypass_memory_context(
    instance: Any,
    prompt: str,
    columns: list[str],
    schema_profile: dict | None = None,
) -> bool:
    """Determina si el prompt puede bypassear la inyeccion de contexto
    de memoria (porque ya tiene suficiente informacion explicita)."""
    if not settings.DETERMINISTIC_VISUAL_FASTPATH_ENABLED:
        return False

    surface_prompt = instance._normalize_surface_text(prompt)
    if not surface_prompt:
        return False
    if instance.is_visual_replacement_request(prompt):
        return False
    if instance._contains_explicit_continuity_marker(surface_prompt):
        return False

    requested_visuals = extract_prompt_visual_requests(prompt)
    if not requested_visuals and not instance._mentions_generic_visual_request(surface_prompt):
        return False

    schema_profile = schema_profile or {}
    if "scatter_plot" in requested_visuals:
        return bool(
            instance._extract_axis_segment(surface_prompt, "x")
            and instance._extract_axis_segment(surface_prompt, "y")
        )

    dimension_segment = None
    por_match = re.search(r"\bpor\s+(.+?)(?=$|,)", surface_prompt, flags=re.IGNORECASE)
    if por_match:
        dimension_segment = por_match.group(1)

    dimension_candidates = instance._resolve_segment_columns(
        dimension_segment or surface_prompt,
        columns,
        schema_profile=schema_profile,
        allowed_roles={"dimension", "identifier"},
    )
    date_candidates = instance._resolve_segment_columns(
        surface_prompt,
        columns,
        schema_profile=schema_profile,
        allowed_roles={"date"},
    )
    metric_candidates = instance._resolve_segment_columns(
        surface_prompt,
        columns,
        schema_profile=schema_profile,
        allowed_roles={"metric"},
    )
    if not metric_candidates:
        default_metric = instance._infer_default_metric_column(
            surface_prompt,
            columns,
            schema_profile=schema_profile,
        )
        if default_metric:
            metric_candidates = [default_metric]

    if requested_visuals and requested_visuals[0] in {"line_chart", "area_chart"}:
        return bool(date_candidates and metric_candidates)

    return bool((dimension_candidates and metric_candidates) or (date_candidates and metric_candidates))


def is_visual_replacement_request(instance: Any, prompt: str) -> bool:
    """Detecta si el usuario quiere reemplazar el visual actual sin
    cambiar el analisis subyacente (e.g., 'cambia el grafico a barra')."""
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


def _classify_memory_intent(instance: Any, prompt: str, memory_context: str) -> str:
    """
    Clasifica la intencion del usuario en el contexto de memoria.
    Retorna una instruccion especifica para Gemini basada en el tipo detectado.
    Si no hay memoria, retorna cadena vacia (analisis estandar).
    """
    if not memory_context:
        return ""

    prompt_lower = prompt.lower().strip()

    if instance.is_visual_replacement_request(prompt):
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

    # --- COMPLEMENT: Angulos nuevos sobre el mismo tema ---
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

    # --- COMPARE: Analisis comparativo ---
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

    # --- DEFAULT: Hay memoria pero el prompt no matchea ningun patron ---
    # Podria ser un tema nuevo o un prompt ambiguo → dejar que Gemini decida
    print(f"🧠 [INTENT CLASSIFIER] Tipo: DEFAULT (memoria presente, sin patrón específico)")
    return ""


def evaluate_continuity(instance: Any, current_prompt: str, previous_prompt: str) -> bool:
    """Determina si el prompt actual es continuacion del anterior o un tema nuevo."""
    if not previous_prompt:
        return False

    curr_lower = current_prompt.lower().strip()
    prev_lower = previous_prompt.lower().strip()
    current_visuals = extract_prompt_visual_requests(current_prompt)
    previous_visuals = extract_prompt_visual_requests(previous_prompt)
    visual_replacement_request = instance.is_visual_replacement_request(current_prompt)

    def _normalize(text: str) -> str:
        return re.sub(r'\s+', ' ', re.sub(r'[^\w\s]', ' ', text.lower())).strip()

    # Repetir exactamente el mismo prompt es un rerun, no un drill-down.
    if _normalize(curr_lower) == _normalize(prev_lower):
        return False

    # 1. Keywords de RUPTURA explicita → SIEMPRE cortan
    keywords_ruptura = ['hola', 'gracias', 'nuevo analisis', 'olvida', 'inicio', 'cambiando de tema']
    if any(k in curr_lower for k in keywords_ruptura):
        return False

    # 2. Keywords de CONTINUIDAD explicita → SIEMPRE mantienen
    keywords_continuidad = [
        'y por', 'ahora por', 'profundiza', 'detalla', 'filtra', 'borra',
        'mas detalle', 'más detalle', 'ver graficos', 'amplía', 'expande',
        'desglosa', 'drill', 'zoom', 'muestra más'
    ]
    if any(k in curr_lower for k in keywords_continuidad):
        return True

    if current_visuals and visual_replacement_request:
        return True

    if current_visuals and not visual_replacement_request:
        if not previous_visuals:
            return False
        if current_visuals != previous_visuals:
            return False

    if instance._looks_self_contained_visual_request(current_prompt):
        return False

    # 3. Prompts cortos sin sujeto analitico → probablemente drill-down
    if len(curr_lower.split()) <= 4:
        if current_visuals:
            return False
        return True

    # 4. DETECCION DE CAMBIO DE TEMA por sujeto analitico
    # Extraemos palabras tematicas (sustantivos clave del negocio)
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

    # Si hay interseccion tematica → continuidad
    overlap = curr_topics & prev_topics
    if overlap:
        return True

    # Si ambos tienen temas pero NO comparten ninguno → cambio de tema
    if curr_topics and prev_topics and not overlap:
        return False

    # Default: mantener continuidad (beneficio de la duda)
    return True


# [COMPAT] Re-export del symbolo publico principal.
from app.services.semantic_translator.core import (  # noqa: F401,E402
    SemanticTranslator,
)

__all__ = ["SemanticTranslator", "should_bypass_memory_context"]
