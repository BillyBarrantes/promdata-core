import re
from typing import Any, Dict, List

from app.core.config import settings
from app.core.semantic_grammar import DataFilter, FilterOperator
from app.services.visual_recommendation_engine import extract_prompt_visual_requests
from app.services.semantic_translator.core import (
    normalize_surface_text,
    mentions_generic_visual_request,
    contains_explicit_continuity_marker,
    extract_axis_segment,
    looks_self_contained_visual_request,
    resolve_segment_columns,
)
from app.services.semantic_translator.validator import infer_default_metric_column


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


def should_bypass_memory_context(
    prompt: str,
    columns: list[str],
    schema_profile: dict | None = None,
) -> bool:
    if not settings.DETERMINISTIC_VISUAL_FASTPATH_ENABLED:
        return False

    surface_prompt = normalize_surface_text(prompt)
    if not surface_prompt:
        return False
    if is_visual_replacement_request(prompt):
        return False
    if contains_explicit_continuity_marker(surface_prompt):
        return False

    requested_visuals = extract_prompt_visual_requests(prompt)
    if not requested_visuals and not mentions_generic_visual_request(surface_prompt):
        return False

    schema_profile = schema_profile or {}
    if "scatter_plot" in requested_visuals:
        return bool(
            extract_axis_segment(surface_prompt, "x") and extract_axis_segment(surface_prompt, "y")
        )

    dimension_segment = None
    por_match = re.search(r"\bpor\s+(.+?)(?=$|,)", surface_prompt, flags=re.IGNORECASE)
    if por_match:
        dimension_segment = por_match.group(1)

    dimension_candidates = resolve_segment_columns(
        dimension_segment or surface_prompt, columns,
        schema_profile=schema_profile, allowed_roles={"dimension", "identifier"},
    )
    date_candidates = resolve_segment_columns(
        surface_prompt, columns,
        schema_profile=schema_profile, allowed_roles={"date"},
    )
    metric_candidates = resolve_segment_columns(
        surface_prompt, columns,
        schema_profile=schema_profile, allowed_roles={"metric"},
    )
    if not metric_candidates:
        default_metric = infer_default_metric_column(surface_prompt, columns, schema_profile=schema_profile)
        if default_metric:
            metric_candidates = [default_metric]

    if requested_visuals and requested_visuals[0] in {"line_chart", "area_chart"}:
        return bool(date_candidates and metric_candidates)

    return bool((dimension_candidates and metric_candidates) or (date_candidates and metric_candidates))


def classify_memory_intent(prompt: str, memory_context: str) -> str:
    if not memory_context:
        return ""

    prompt_lower = prompt.lower().strip()

    if is_visual_replacement_request(prompt):
        print(f"🧠 [INTENT CLASSIFIER] Tipo: VISUAL_REPLACEMENT")
        return """--- 🎨 MODO REEMPLAZO VISUAL (Detectado por Ibis) ---
    El usuario quiere conservar el MISMO análisis base y solo cambiar la representación visual.
    REGLAS ESTRICTAS:
    - MANTÉN el mismo tema, métricas, filtros y granularidad del análisis anterior.
    - PROHIBIDO heredar títulos decorativos o narrativas del gráfico previo.
    - OBLIGATORIO: respeta el nuevo visual solicitado como prioridad principal.
    - Si el visual nuevo no aplica al shape de datos, explica el bloqueo en vez de inventar otro visual."""

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
        prev_dim = "Unknown"
        match = re.search(r"Agrupado por: \[?([a-zA-Z0-9_ ]+)\]?", memory_context)
        if match:
            prev_dim = match.group(1).strip()

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

    print(f"🧠 [INTENT CLASSIFIER] Tipo: DEFAULT (memoria presente, sin patrón específico)")
    return ""


def detect_literal_filters(prompt: str, dimension_values: Dict[str, list]) -> List[DataFilter]:
    if not dimension_values:
        return []

    detected_filters: List[DataFilter] = []

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

    quoted_phrases = re.findall(r'["\']([^"\']+)["\']', prompt)

    raw_tokens = prompt.split()
    clean_tokens = [
        t.strip('.,;:!?()[]{}"\'')
        for t in raw_tokens
        if len(t.strip('.,;:!?()[]{}"\'')) > 2 and t.lower().strip('.,;:!?()[]{}"\'') not in stopwords
    ]

    search_terms = [(phrase, True) for phrase in quoted_phrases] + [(token, False) for token in clean_tokens]

    matched_pairs: set[tuple[str, str]] = set()

    columns_sets = {}
    for col, vals in dimension_values.items():
        if vals:
            columns_sets[col] = {str(v).upper(): v for v in vals if v is not None}

    for term, is_quoted in search_terms:
        term_upper = term.upper().strip()
        if len(term_upper) < 2:
            continue

        for col_name, val_map in columns_sets.items():
            if (col_name, term_upper) in matched_pairs:
                continue

            if term_upper in val_map:
                original_value = val_map[term_upper]
                detected_filters.append(
                    DataFilter(column=col_name, operator=FilterOperator.EQUALS, value=str(original_value))
                )
                matched_pairs.add((col_name, term_upper))
                print(f"🎯 [LITERAL FILTER] Match exacto: '{term}' → {col_name} == '{original_value}'")
                break

            if not is_quoted and len(term_upper) > 4:
                best_match: str | None = None
                best_diff: int = 4
                for candidate_upper, candidate_original in val_map.items():
                    len_term = len(term_upper)
                    len_cand = len(candidate_upper)
                    len_diff = abs(len_term - len_cand)
                    if len_diff >= best_diff:
                        continue
                    shorter = term_upper if len_term <= len_cand else candidate_upper
                    longer = candidate_upper if len_term <= len_cand else term_upper
                    if longer.startswith(shorter):
                        best_match = candidate_original
                        best_diff = len_diff

                if best_match is not None:
                    detected_filters.append(
                        DataFilter(column=col_name, operator=FilterOperator.EQUALS, value=str(best_match))
                    )
                    matched_pairs.add((col_name, best_match.upper()))
                    break

    if detected_filters:
        print(f"🎯 [LITERAL FILTER] {len(detected_filters)} filtro(s) detectado(s)")

    collapsed: List[DataFilter] = []
    col_values: dict[str, set[str]] = {}
    for f in detected_filters:
        op = str(getattr(f.operator, "value", f.operator) or "").strip()
        if op == "==":
            col = str(f.column or "")
            val = str(f.value or "")
            col_values.setdefault(col, set()).add(val)
        else:
            collapsed.append(f)
    for col, vals in col_values.items():
        if len(vals) == 1:
            collapsed.append(DataFilter(column=col, operator=FilterOperator.EQUALS, value=list(vals)[0]))
        else:
            collapsed.append(DataFilter(column=col, operator=FilterOperator.IN_LIST, value=sorted(vals)))

    detected_filters = collapsed

    return detected_filters


def evaluate_continuity(current_prompt: str, previous_prompt: str) -> bool:
    if not previous_prompt:
        return False

    curr_lower = current_prompt.lower().strip()
    prev_lower = previous_prompt.lower().strip()
    current_visuals = extract_prompt_visual_requests(current_prompt)
    previous_visuals = extract_prompt_visual_requests(previous_prompt)
    visual_replacement_request_flag = is_visual_replacement_request(current_prompt)

    def _normalize(text: str) -> str:
        return re.sub(r'\s+', ' ', re.sub(r'[^\w\s]', ' ', text.lower())).strip()

    if _normalize(curr_lower) == _normalize(prev_lower):
        return False

    keywords_ruptura = ['hola', 'gracias', 'nuevo analisis', 'olvida', 'inicio', 'cambiando de tema']
    if any(k in curr_lower for k in keywords_ruptura):
        return False

    keywords_continuidad = [
        'y por', 'ahora por', 'profundiza', 'detalla', 'filtra', 'borra',
        'mas detalle', 'más detalle', 'ver graficos', 'amplía', 'expande',
        'desglosa', 'drill', 'zoom', 'muestra más'
    ]
    if any(k in curr_lower for k in keywords_continuidad):
        return True

    if current_visuals and visual_replacement_request_flag:
        return True

    if current_visuals and not visual_replacement_request_flag:
        if not previous_visuals:
            return False
        if current_visuals != previous_visuals:
            return False

    if looks_self_contained_visual_request(current_prompt):
        return False

    if len(curr_lower.split()) <= 4:
        if current_visuals:
            return False
        return True

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
        return {w for w in words if len(w) > 2}

    curr_topics = extract_topics(curr_lower)
    prev_topics = extract_topics(prev_lower)

    overlap = curr_topics & prev_topics
    if overlap:
        return True

    if curr_topics and prev_topics and not overlap:
        return False

    return True
