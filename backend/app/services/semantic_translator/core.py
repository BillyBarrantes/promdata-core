import json
import re
import unicodedata
from typing import Any, Optional

from app.core.config import settings
from app.core.semantic_grammar import (
    AnalysisPlan,
    DataFilter,
    FilterOperator,
    MetricUnit,
    MetricPolarity,
    VisualProtocol,
    DescriptiveIntent,
    DistributionIntent,
    TimeTrendIntent,
)
from app.core.structured_logging import emit_structured_log
from app.services.metric_semantics import normalize_semantic_text
from app.services.visual_recommendation_engine import extract_prompt_visual_requests

# @deprecated("Eliminado por cirugía de sesgos domain-agnostic — mayo 2026")
_DIMENSION_SEMANTIC_GROUPS: dict[str, set[str]] = {}

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


def normalize_surface_text(value: str | None) -> str:
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


def humanize_column_alias(column_name: str) -> str:
    humanized = str(column_name or "").replace("_", " ").strip()
    return humanized.title() if humanized else "Valor"


def semantic_groups_for_text(value: str | None) -> set[str]:
    return set()


def dimension_semantic_alignment_score(segment_norm: str, column_norm: str) -> int:
    return 0


def should_default_to_latest_snapshot(
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


def build_default_latest_snapshot_filters(
    surface_prompt: str,
    columns: list[str],
    dataset_contract: dict[str, Any] | None = None,
    schema_profile: dict | None = None,
) -> list[DataFilter]:
    if not should_default_to_latest_snapshot(
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


def extract_axis_segment(surface_prompt: str, axis_name: str) -> str | None:
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


def resolve_segment_columns(
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

        if score > 0:
            ranked.append((score, column_name))

    ranked.sort(key=lambda item: (-item[0], item[1]))
    resolved: list[str] = []
    for _, column_name in ranked:
        if column_name not in resolved:
            resolved.append(column_name)
    return resolved


def extract_top_limit(surface_prompt: str) -> int | None:
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


def is_top_n_rollup_request(surface_prompt: str) -> bool:
    if not surface_prompt:
        return False
    has_top_n = extract_top_limit(surface_prompt) is not None
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
    has_temporal = mentions_temporal_language(surface_prompt)
    if has_top_n and has_aggregate_word and has_temporal:
        return True

    if has_top_n and has_aggregate_word and re.search(r"\bno\b.*\bcada\b", surface_prompt):
        return True

    return bool(
        re.search(
            r"\b(?:suma|sum|acumulad[oa]|total(?:es)?)\s+(?:del?|de los)\s+(?:top\s+)?\d{1,3}\b",
            surface_prompt,
            flags=re.IGNORECASE,
        )
    )


def mentions_generic_visual_request(surface_prompt: str) -> bool:
    return any(
        marker in surface_prompt
        for marker in (
            "grafico",
            "grafica",
            "chart",
            "visual",
        )
    )


def contains_explicit_continuity_marker(surface_prompt: str) -> bool:
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


def mentions_temporal_language(surface_prompt: str) -> bool:
    return any(
        marker in surface_prompt
        for marker in (
            "fecha",
            "date",
            "tiempo",
            "temporal",
            "periodo",
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


def contains_analysis_language(surface_prompt: str) -> bool:
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


def extract_primary_dimension_segment(surface_prompt: str) -> str | None:
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


def pick_primary_date_column(
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


def pick_best_dimension_column(
    prompt: str,
    columns: list[str],
    schema_profile: dict | None = None,
    exclude: set[str] | None = None,
) -> str | None:
    schema_profile = schema_profile or {}
    exclude = exclude or set()
    surface_prompt = normalize_surface_text(prompt)
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

        col_norm = normalize_semantic_text(str(column_name).replace("_", " "))
        compact_col = col_norm.replace(" ", "")
        has_direct_prompt_match = bool(compact_col and compact_col in compact_prompt)
        token_overlap = sum(1 for token in col_norm.split() if len(token) > 1 and token in surface_prompt)

        score = 0
        if role == "dimension":
            score += 40
        elif has_direct_prompt_match or token_overlap >= 1:
            score += 20
        else:
            score -= 10

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

        if has_direct_prompt_match:
            score += 80 + len(compact_col)

        score += token_overlap * 12

        ranked.append((score, column_name))

    ranked.sort(key=lambda item: (-item[0], item[1]))
    return ranked[0][1] if ranked else None


def looks_broad_analysis_request(prompt: str) -> bool:
    surface_prompt = normalize_surface_text(prompt)
    if not surface_prompt:
        return False

    from app.services.semantic_translator.memory import is_visual_replacement_request

    if is_visual_replacement_request(prompt):
        return False
    if contains_explicit_continuity_marker(surface_prompt):
        return False
    if extract_prompt_visual_requests(prompt):
        return False
    if mentions_generic_visual_request(surface_prompt):
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

    if not contains_analysis_language(surface_prompt):
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


def looks_dimension_analysis_request(prompt: str) -> bool:
    surface_prompt = normalize_surface_text(prompt)
    if not surface_prompt:
        return False

    from app.services.semantic_translator.memory import is_visual_replacement_request

    if is_visual_replacement_request(prompt):
        return False
    if contains_explicit_continuity_marker(surface_prompt):
        return False
    if extract_prompt_visual_requests(prompt):
        return False
    if mentions_generic_visual_request(surface_prompt):
        return False
    if not contains_analysis_language(surface_prompt):
        return False
    return extract_primary_dimension_segment(surface_prompt) is not None


def has_meaningful_temporal_axis(
    date_column: str | None,
    schema_profile: dict | None = None,
) -> bool:
    if not date_column:
        return False
    schema_profile = schema_profile or {}
    cardinality = int(schema_profile.get(date_column, {}).get("cardinality") or 0)
    return cardinality > 1


def looks_self_contained_visual_request(prompt: str) -> bool:
    surface_prompt = normalize_surface_text(prompt)
    if not surface_prompt:
        return False

    from app.services.semantic_translator.memory import is_visual_replacement_request

    if is_visual_replacement_request(prompt):
        return False
    if contains_explicit_continuity_marker(surface_prompt):
        return False

    has_visual_language = (
        mentions_generic_visual_request(surface_prompt)
        or bool(extract_prompt_visual_requests(prompt))
    )
    if not has_visual_language:
        return False

    return bool(
        re.search(r"\bpor\s+[a-z0-9_ ]{3,}", surface_prompt)
        or re.search(r"\bde\s+[a-z0-9_ ]+\s+por\s+[a-z0-9_ ]{3,}", surface_prompt)
        or (
            extract_axis_segment(surface_prompt, "x")
            and extract_axis_segment(surface_prompt, "y")
        )
    )


def apply_top_n_rollup_mode_to_plans(
    prompt: str,
    plans: list[AnalysisPlan],
) -> list[AnalysisPlan]:
    emit_structured_log(
        "semantic_translator_legacy_rollup_postprocessor_disabled",
        prompt=prompt[:200],
        plan_count=len(plans or []),
    )
    return plans
