import re
import unicodedata
from typing import Any

from app.core.semantic_grammar import MetricUnit


def normalize_semantic_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    normalized = normalized.lower()
    normalized = re.sub(r"[^a-z0-9_ ]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def infer_metric_unit_from_column_name(metric_name: str | None, currency_meta: dict[str, Any] | None = None) -> MetricUnit:
    normalized = normalize_semantic_text(metric_name or "")
    if not normalized:
        return MetricUnit.NUMBER

    currency_code = normalize_semantic_text((currency_meta or {}).get("code", ""))
    currency_symbol = str((currency_meta or {}).get("symbol", "") or "").strip().lower()

    percentage_terms = ["porcentaje", "percent", "share", "ratio", "margen", "growth", "variacion_pct", "variacion_porcentual"]
    currency_terms = [
        "venta", "ventas", "revenue", "ingreso", "ingresos", "facturacion", "monto",
        "importe", "price", "precio", "cost", "costo", "coste", "amount", "billing", "billings"
    ]
    quantity_terms = [
        "cantidad", "cantidad_vendida", "stock", "unidades", "unidad", "units",
        "qty", "volumen", "piezas", "pieza", "cajas", "conteo", "count"
    ]

    if "%" in str(metric_name or "") or any(term in normalized for term in percentage_terms):
        return MetricUnit.PERCENTAGE

    if (
        any(term in normalized for term in currency_terms)
        or (currency_code and currency_code in normalized)
        or (currency_symbol and currency_symbol in str(metric_name or "").lower())
    ):
        return MetricUnit.CURRENCY

    if any(term in normalized for term in quantity_terms):
        return MetricUnit.QUANTITY

    return MetricUnit.NUMBER


def infer_prompt_metric_preference(prompt_text: str) -> MetricUnit | None:
    normalized = normalize_semantic_text(prompt_text)
    if not normalized:
        return None

    currency_terms = ["ventas", "venta", "ingresos", "ingreso", "facturacion", "monto", "importe", "revenue", "billing"]
    quantity_terms = ["cantidad", "cantidades", "unidades", "unidad", "stock", "volumen", "piezas", "cajas", "articulos"]
    percentage_terms = ["porcentaje", "participacion", "share", "ratio", "margen", "variacion porcentual", "growth"]

    currency_score = sum(1 for term in currency_terms if term in normalized)
    quantity_score = sum(1 for term in quantity_terms if term in normalized)
    percentage_score = sum(1 for term in percentage_terms if term in normalized)

    if currency_score == 0 and quantity_score == 0 and percentage_score == 0:
        return None

    if currency_score >= quantity_score and currency_score >= percentage_score:
        return MetricUnit.CURRENCY
    if quantity_score >= currency_score and quantity_score >= percentage_score:
        return MetricUnit.QUANTITY
    return MetricUnit.PERCENTAGE


def score_metric_candidate(
    metric_name: str,
    desired_unit: MetricUnit | None,
    prompt_text: str,
    currency_meta: dict[str, Any] | None = None,
) -> tuple[int, MetricUnit]:
    inferred_unit = infer_metric_unit_from_column_name(metric_name, currency_meta)
    normalized_metric = normalize_semantic_text(metric_name)
    normalized_prompt = normalize_semantic_text(prompt_text)

    score = 0
    if desired_unit and inferred_unit == desired_unit:
        score += 100

    if inferred_unit == MetricUnit.CURRENCY:
        for term in ("total", "venta", "ventas", "revenue", "ingreso", "facturacion", "importe", "monto"):
            if term in normalized_metric:
                score += 10
        if "precio" in normalized_metric:
            score -= 6
    elif inferred_unit == MetricUnit.QUANTITY:
        for term in ("cantidad", "stock", "unidades", "qty", "volumen", "piezas", "cajas"):
            if term in normalized_metric:
                score += 10

    prompt_tokens = [token for token in normalized_prompt.split() if len(token) > 2]
    score += sum(2 for token in prompt_tokens if token in normalized_metric)

    return score, inferred_unit


def get_plan_primary_metric(intent: Any) -> str | None:
    if getattr(intent, "metric", None):
        return str(intent.metric)
    if getattr(intent, "value_column", None):
        return str(intent.value_column)
    metrics = list(getattr(intent, "metrics", None) or [])
    return str(metrics[0]) if metrics else None


def set_plan_primary_metric(intent: Any, metric_name: str) -> None:
    if hasattr(intent, "metric") and getattr(intent, "metric", None):
        intent.metric = metric_name
    if hasattr(intent, "value_column") and getattr(intent, "value_column", None):
        intent.value_column = metric_name
    if hasattr(intent, "metrics"):
        metrics = list(getattr(intent, "metrics", None) or [])
        if metrics:
            metrics[0] = metric_name
            deduped = []
            for item in metrics:
                if item and item not in deduped:
                    deduped.append(item)
            intent.metrics = deduped


def align_plan_metrics_with_prompt(
    plans: list[Any],
    prompt_text: str,
    schema_profile: dict[str, Any] | None,
    currency_meta: dict[str, Any] | None = None,
) -> list[Any]:
    schema_profile = schema_profile or {}
    metric_columns = [
        col_name for col_name, info in schema_profile.items()
        if info.get("role") == "metric"
    ]
    if not metric_columns or not plans:
        return plans

    desired_unit = infer_prompt_metric_preference(prompt_text)

    for plan in plans:
        intent = getattr(plan, "main_intent", None)
        if not intent:
            continue

        current_metric = get_plan_primary_metric(intent)
        current_unit = infer_metric_unit_from_column_name(current_metric, currency_meta)

        if hasattr(intent, "metrics") and len(list(getattr(intent, "metrics", None) or [])) >= 2:
            if hasattr(intent, "metric_unit"):
                intent.metric_unit = current_unit
            continue

        if desired_unit and current_unit != desired_unit:
            ranked_candidates = sorted(
                (
                    (candidate, *score_metric_candidate(candidate, desired_unit, prompt_text, currency_meta))
                    for candidate in metric_columns
                ),
                key=lambda item: item[1],
                reverse=True,
            )
            if ranked_candidates:
                best_candidate, best_score, best_unit = ranked_candidates[0]
                if best_score >= 100 and best_candidate != current_metric:
                    set_plan_primary_metric(intent, best_candidate)
                    current_unit = best_unit

        if hasattr(intent, "metric_unit"):
            intent.metric_unit = current_unit

    return plans
