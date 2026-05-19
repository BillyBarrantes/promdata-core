from __future__ import annotations

from typing import Any
import math


INTENT_LABELS = {
    "descriptive": "descriptive",
    "trend": "trend",
    "distribution": "distribution",
    "diagnostic": "diagnostic",
    "predictive": "predictive",
}


VISUAL_LABELS = {
    "bar_chart": "bar chart",
    "line_chart": "line chart",
    "area_chart": "area chart",
    "pie_chart": "pie chart",
    "scatter_plot": "scatter plot",
    "histogram": "histogram",
    "heatmap": "heatmap",
    "waterfall": "waterfall",
    "treemap": "treemap",
    "funnel_chart": "funnel chart",
    "boxplot": "boxplot",
    "kpi_card": "kpi card",
    "dual_axis_chart": "dual axis chart",
}


def _humanize_column(column_name: str | None, aliases: dict[str, str]) -> str | None:
    if not column_name:
        return None
    alias = aliases.get(column_name)
    if alias:
        return alias
    text = str(column_name).replace("_", " ").strip()
    return text[:1].upper() + text[1:] if text else None


def _compact_text(value: str | None, *, max_sentences: int = 2, max_chars: int = 220) -> str:
    text = str(value or "").replace("\n", " ").strip()
    if not text:
        return ""

    parts = [part.strip() for part in text.split(".") if part.strip()]
    compact = ". ".join(parts[:max_sentences]).strip()
    if compact and not compact.endswith("."):
        compact += "."

    if len(compact) > max_chars:
        compact = compact[: max_chars - 1].rstrip() + "…"
    return compact


def _serialize_filters(filters: list[Any], aliases: dict[str, str]) -> list[str]:
    serialized: list[str] = []
    for item in filters or []:
        column = _humanize_column(getattr(item, "column", None), aliases) or str(getattr(item, "column", "") or "").strip()
        operator = str(getattr(item, "operator", "") or "").strip()
        value = getattr(item, "value", None)
        if isinstance(value, list):
            value_repr = ", ".join(str(part) for part in value)
        else:
            value_repr = str(value)
        if column and operator and value_repr:
            serialized.append(f"{column} {operator} {value_repr}")
    return serialized


def _build_methodology(intent: Any) -> str:
    intent_type = str(getattr(intent, "type", "") or "").strip()
    if intent_type == "predictive":
        horizon = getattr(intent, "horizon", None)
        grain = getattr(getattr(intent, "grain", None), "value", None) or getattr(intent, "grain", None)
        return f"Proyeccion con Holt-Winters sobre el historico disponible, horizonte {horizon or 0} y grano {grain or 'n/a'}."
    if intent_type == "trend":
        return "Serie temporal agregada para medir evolucion y cambios."
    if intent_type == "distribution":
        return "Comparacion por categorias para detectar concentracion y ranking."
    if intent_type == "diagnostic":
        return "Analisis diagnostico de dispersion, relacion o embudo segun la configuracion detectada."
    return "Agregacion descriptiva deterministica sobre la vista solicitada."


def _extract_evidence(ibis_output: dict[str, Any], aliases: dict[str, str]) -> list[str]:
    hard_facts = ibis_output.get("hard_facts") if isinstance(ibis_output.get("hard_facts"), dict) else {}
    evidence: list[str] = []

    top_name = hard_facts.get("top_1_name")
    top_val = hard_facts.get("top_1_val")
    top_share = hard_facts.get("top_1_share")
    if top_name is not None and top_val is not None:
        share_suffix = f" ({top_share}% del total)" if top_share is not None else ""
        evidence.append(f"Lider detectado: {top_name} con valor {top_val}{share_suffix}.")

    overall_growth = hard_facts.get("overall_growth_pct")
    trend = hard_facts.get("trend")
    if overall_growth is not None:
        trend_suffix = f" y direccion {trend}" if trend else ""
        evidence.append(f"Cambio total observado: {overall_growth}%{trend_suffix}.")

    peak_period = hard_facts.get("peak_period")
    peak_value = hard_facts.get("peak_value")
    if peak_period is not None and peak_value is not None:
        evidence.append(f"Pico identificado en {peak_period} con valor {peak_value}.")

    trough_period = hard_facts.get("trough_period")
    trough_value = hard_facts.get("trough_value")
    if trough_period is not None and trough_value is not None:
        evidence.append(f"Piso identificado en {trough_period} con valor {trough_value}.")

    total_outliers = hard_facts.get("total_outliers")
    if total_outliers is not None:
        evidence.append(f"Outliers detectados: {total_outliers}.")

    correlation = hard_facts.get("correlation")
    if correlation is not None:
        evidence.append(f"Correlacion calculada: {correlation}.")

    if evidence:
        return evidence[:3]

    rows = ibis_output.get("data") if isinstance(ibis_output.get("data"), list) else []
    for row in rows[:3]:
        if not isinstance(row, dict):
            continue
        name = row.get("name")
        value = row.get("value")
        if name is not None and value is not None:
            evidence.append(f"Punto observado: {name} con valor {value}.")
            continue

        pairs = []
        for key, value in row.items():
            label = _humanize_column(str(key), aliases) or str(key)
            pairs.append(f"{label}: {value}")
        if pairs:
            evidence.append("Registro observado: " + ", ".join(pairs[:3]) + ".")
    return evidence[:3]


def _build_limitations(*, intent: Any, ibis_output: dict[str, Any], filters: list[str], evidence: list[str]) -> list[str]:
    limitations: list[str] = []
    rows = ibis_output.get("data") if isinstance(ibis_output.get("data"), list) else []
    if len(rows) <= 1:
        limitations.append("La base analizada para esta vista es pequena; la robustez comparativa es limitada.")
    if not filters:
        limitations.append("No se aplicaron filtros explicitos; la lectura corresponde al universo completo disponible.")

    intent_type = str(getattr(intent, "type", "") or "").strip()
    if intent_type == "predictive":
        limitations.append("La proyeccion depende del historico disponible y no incorpora eventos externos no observados en la serie.")
    elif intent_type == "diagnostic" and not evidence:
        limitations.append("El analisis diagnostico no encontro evidencia suficiente para conclusiones fuertes.")

    error_message = ibis_output.get("error")
    if error_message:
        limitations.append(f"El motor reporto una condicion de borde: {error_message}.")

    return limitations[:3]


def _extract_numeric_signal_count(value: Any) -> int:
    count = 0

    def _walk(node: Any) -> None:
        nonlocal count
        if isinstance(node, bool) or node is None:
            return
        if isinstance(node, (int, float)):
            if math.isfinite(float(node)):
                count += 1
            return
        if isinstance(node, dict):
            for child in node.values():
                _walk(child)
            return
        if isinstance(node, list):
            for child in node:
                _walk(child)

    _walk(value)
    return count


def _has_valid_numeric_observation(row: Any) -> bool:
    return _extract_numeric_signal_count(row) > 0


def _score_temporal_coverage(intent: Any, rows: list[Any], hard_facts: dict[str, Any]) -> float:
    intent_type = str(getattr(intent, "type", "") or "").strip()
    if intent_type not in {"trend", "predictive"}:
        return 0.7 if rows else 0.35

    total_periods = hard_facts.get("total_periods")
    if isinstance(total_periods, (int, float)) and float(total_periods) > 0:
        periods = int(total_periods)
    else:
        observed_labels = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            label = row.get("name") or row.get("periodo")
            if label is not None:
                observed_labels.add(str(label))
        periods = len(observed_labels)

    if periods >= 12:
        return 1.0
    if periods >= 6:
        return 0.85
    if periods >= 3:
        return 0.65
    if periods >= 2:
        return 0.5
    return 0.3


def _score_valid_point_ratio(rows: list[Any]) -> float:
    if not rows:
        return 0.2
    valid_points = sum(1 for row in rows if _has_valid_numeric_observation(row))
    return round(max(0.2, min(1.0, valid_points / len(rows))), 2)


def _score_universe_density(intent: Any, rows: list[Any]) -> float:
    row_count = len(rows)
    requested_limit = getattr(intent, "limit", None)
    if isinstance(requested_limit, int) and requested_limit > 0:
        return round(max(0.3, min(1.0, row_count / requested_limit)), 2)
    if row_count >= 20:
        return 1.0
    if row_count >= 10:
        return 0.85
    if row_count >= 5:
        return 0.7
    if row_count >= 2:
        return 0.55
    return 0.35


def _score_filter_consistency(filters: list[str], rows: list[Any]) -> float:
    if not filters:
        return 0.7 if rows else 0.35
    return 0.95 if rows else 0.25


def _score_statistical_strength(intent: Any, hard_facts: dict[str, Any], evidence: list[str]) -> float:
    intent_type = str(getattr(intent, "type", "") or "").strip()
    signal_count = _extract_numeric_signal_count(hard_facts)

    if intent_type == "predictive":
        if signal_count >= 8:
            return 0.95
        if signal_count >= 5:
            return 0.8
        if signal_count >= 3:
            return 0.65
        return 0.45

    if intent_type == "diagnostic":
        correlation = hard_facts.get("correlation")
        total_outliers = hard_facts.get("total_outliers")
        if correlation is not None or total_outliers is not None:
            return 0.85
        if signal_count >= 3:
            return 0.7
        return 0.45

    if signal_count >= 6:
        return 0.9
    if signal_count >= 3:
        return 0.75
    if evidence:
        return 0.6
    return 0.4


def _build_confidence(*, intent: Any, evidence: list[str], filters: list[str], ibis_output: dict[str, Any], compliance_result: dict[str, Any]) -> dict[str, Any]:
    rows = ibis_output.get("data") if isinstance(ibis_output.get("data"), list) else []
    hard_facts = ibis_output.get("hard_facts") if isinstance(ibis_output.get("hard_facts"), dict) else {}

    factors = {
        "temporal_coverage": round(_score_temporal_coverage(intent, rows, hard_facts), 2),
        "valid_point_ratio": round(_score_valid_point_ratio(rows), 2),
        "universe_density": round(_score_universe_density(intent, rows), 2),
        "filter_consistency": round(_score_filter_consistency(filters, rows), 2),
        "statistical_strength": round(_score_statistical_strength(intent, hard_facts, evidence), 2),
    }

    weighted_score = (
        factors["temporal_coverage"] * 0.22
        + factors["valid_point_ratio"] * 0.2
        + factors["universe_density"] * 0.18
        + factors["filter_consistency"] * 0.15
        + factors["statistical_strength"] * 0.25
    )

    if compliance_result.get("matched"):
        weighted_score += 0.03

    score = max(0.0, min(0.97, round(weighted_score, 2)))

    if score >= 0.8:
        level = "high"
    elif score >= 0.6:
        level = "medium"
    else:
        level = "low"

    return {"score": score, "level": level, "factors": factors}


def _build_conclusion_gate(
    *,
    intent: Any,
    confidence: dict[str, Any],
    evidence: list[str],
    ibis_output: dict[str, Any],
    diagnostic_context: dict[str, Any],
    compliance_result: dict[str, Any],
    analysis_guardrails: dict[str, Any],
) -> dict[str, Any]:
    rows = ibis_output.get("data") if isinstance(ibis_output.get("data"), list) else []
    hard_facts = ibis_output.get("hard_facts") if isinstance(ibis_output.get("hard_facts"), dict) else {}
    factors = confidence.get("factors") if isinstance(confidence.get("factors"), dict) else {}
    intent_type = str(getattr(intent, "type", "") or "").strip()
    score = float(confidence.get("score") or 0.0)
    sample_size = _resolve_sample_size(rows, hard_facts)
    evidence_count = len(evidence)
    error_message = str(ibis_output.get("error") or "").strip()

    reason_codes: list[str] = []
    fatal_reasons: set[str] = set()

    if error_message:
        reason_codes.append("engine_edge_condition")
        fatal_reasons.add("engine_edge_condition")
    if not rows:
        reason_codes.append("no_observed_rows")
        fatal_reasons.add("no_observed_rows")
    if sample_size and sample_size < 3:
        reason_codes.append("sample_too_small")
        fatal_reasons.add("sample_too_small")
    elif sample_size and sample_size < 8:
        reason_codes.append("sample_limited")

    valid_point_ratio = float(factors.get("valid_point_ratio") or 0.0)
    if valid_point_ratio < 0.45:
        reason_codes.append("low_valid_point_ratio")
        fatal_reasons.add("low_valid_point_ratio")

    statistical_strength = float(factors.get("statistical_strength") or 0.0)
    if statistical_strength < 0.45:
        reason_codes.append("weak_statistical_support")
        fatal_reasons.add("weak_statistical_support")
    elif statistical_strength < 0.65:
        reason_codes.append("moderate_statistical_support")

    temporal_coverage = float(factors.get("temporal_coverage") or 0.0)
    if intent_type in {"trend", "predictive"} and temporal_coverage < 0.5:
        reason_codes.append("insufficient_temporal_coverage")
        fatal_reasons.add("insufficient_temporal_coverage")
    elif intent_type in {"trend", "predictive"} and temporal_coverage < 0.7:
        reason_codes.append("partial_temporal_coverage")

    if evidence_count == 0:
        reason_codes.append("no_concrete_evidence")
        fatal_reasons.add("no_concrete_evidence")
    elif evidence_count == 1:
        reason_codes.append("limited_evidence")

    driver_relations = diagnostic_context.get("driver_relations") if isinstance(diagnostic_context.get("driver_relations"), list) else []
    signal_count = 0
    if isinstance(hard_facts.get("correlation"), (int, float)):
        signal_count += 1
    if isinstance(hard_facts.get("total_outliers"), (int, float)) and float(hard_facts.get("total_outliers") or 0.0) > 0:
        signal_count += 1
    if driver_relations:
        signal_count += 1

    if intent_type == "diagnostic" and signal_count == 0:
        reason_codes.append("diagnostic_signal_missing")
        fatal_reasons.add("diagnostic_signal_missing")

    forecast_guard = analysis_guardrails.get("forecast_viability") if isinstance(analysis_guardrails.get("forecast_viability"), dict) else {}
    if str(forecast_guard.get("status") or "").strip() == "blocked":
        reason_codes.append("forecast_not_viable")
        fatal_reasons.add("forecast_not_viable")
    elif str(forecast_guard.get("status") or "").strip() == "guarded":
        reason_codes.append("forecast_viability_limited")

    correlation_guard = analysis_guardrails.get("correlation_relevance") if isinstance(analysis_guardrails.get("correlation_relevance"), dict) else {}
    if str(correlation_guard.get("status") or "").strip() == "blocked":
        reason_codes.append("relationship_not_relevant")
        fatal_reasons.add("relationship_not_relevant")
    elif str(correlation_guard.get("status") or "").strip() == "guarded":
        reason_codes.append("relationship_support_limited")

    separability_guard = analysis_guardrails.get("diagnostic_separability") if isinstance(analysis_guardrails.get("diagnostic_separability"), dict) else {}
    if str(separability_guard.get("status") or "").strip() == "blocked":
        reason_codes.append("segment_separability_insufficient")
        fatal_reasons.add("segment_separability_insufficient")
    elif str(separability_guard.get("status") or "").strip() == "guarded":
        reason_codes.append("segment_separability_limited")

    if compliance_result.get("matched"):
        reason_codes.append("institutional_rule_support")

    if fatal_reasons:
        decision = "insufficient_evidence"
        narrative_mode = "insufficient"
        summary = "La base disponible no soporta una conclusión fuerte; el sistema debe declarar evidencia insuficiente o limitarse a observaciones mínimas."
    elif score >= 0.8 and evidence_count >= 2:
        decision = "allow_strong_conclusion"
        narrative_mode = "firm"
        summary = "La evidencia observada permite una conclusión firme sobre patrones o prioridades, sin convertir asociación en causalidad."
    else:
        decision = "cautionary_conclusion"
        narrative_mode = "guarded"
        summary = "La lectura es utilizable, pero debe expresarse con lenguaje prudente y sin sobre-extender inferencias."

    return {
        "decision": decision,
        "narrative_mode": narrative_mode,
        "strong_claims_allowed": decision == "allow_strong_conclusion",
        "causal_language_allowed": False,
        "reasons": reason_codes[:5],
        "summary": summary,
    }


def _build_probable_causes(
    *,
    hard_facts: dict[str, Any],
    diagnostic_context: dict[str, Any],
    conclusion_gate: dict[str, Any],
) -> list[dict[str, Any]]:
    decision = str(conclusion_gate.get("decision") or "").strip()
    if decision == "insufficient_evidence":
        return []

    causes: list[dict[str, Any]] = []

    segment_pressure = diagnostic_context.get("segment_pressure") if isinstance(diagnostic_context.get("segment_pressure"), dict) else {}
    if segment_pressure:
        top_segments = segment_pressure.get("top_segments") if isinstance(segment_pressure.get("top_segments"), list) else []
        if top_segments:
            causes.append(
                {
                    "code": "segment_concentration",
                    "confidence": "medium",
                    "summary": f"La presión parece concentrarse en {top_segments[0].get('name')} dentro de {segment_pressure.get('dimension_label') or 'la dimensión principal'}.",
                }
            )

    segment_divergence = diagnostic_context.get("segment_divergence") if isinstance(diagnostic_context.get("segment_divergence"), dict) else {}
    if segment_divergence and float(segment_divergence.get("divergence_score") or 0.0) >= 0.45:
        causes.append(
            {
                "code": "segment_divergence",
                "confidence": "medium",
                "summary": str(segment_divergence.get("summary") or "Un segmento se está separando materialmente del comportamiento medio."),
            }
        )

    driver_relations = diagnostic_context.get("driver_relations") if isinstance(diagnostic_context.get("driver_relations"), list) else []
    if driver_relations:
        relation = driver_relations[0]
        relation_confidence = "high" if str(relation.get("strength") or "") == "strong" and decision == "allow_strong_conclusion" else "medium"
        causes.append(
            {
                "code": "metric_driver_linkage",
                "confidence": relation_confidence,
                "summary": f"{relation.get('label') or relation.get('column')} se mueve de forma {relation.get('direction') or 'observable'} con la métrica principal (corr={relation.get('correlation')}).",
            }
        )

    total_outliers = hard_facts.get("total_outliers")
    if isinstance(total_outliers, (int, float)) and float(total_outliers) > 0:
        causes.append(
            {
                "code": "outlier_distortion",
                "confidence": "medium",
                "summary": f"Los {int(total_outliers)} valores atípicos detectados pueden estar amplificando parte del comportamiento observado.",
            }
        )

    overall_growth = hard_facts.get("overall_growth_pct")
    if isinstance(overall_growth, (int, float)) and abs(float(overall_growth)) >= 15:
        causes.append(
            {
                "code": "trend_acceleration",
                "confidence": "medium" if decision == "allow_strong_conclusion" else "low",
                "summary": f"El cambio acumulado de {round(float(overall_growth), 2)}% sugiere una aceleración material del patrón analizado.",
            }
        )

    return causes[:3]


def _resolve_sample_size(rows: list[Any], hard_facts: dict[str, Any]) -> int:
    raw_candidates = (
        hard_facts.get("sample_size"),
        hard_facts.get("total_periods"),
        len(rows),
    )
    for candidate in raw_candidates:
        if isinstance(candidate, (int, float)) and float(candidate) > 0:
            return int(candidate)
    return 0


def _build_analysis_guardrails(
    *,
    intent: Any,
    hard_facts: dict[str, Any],
    confidence: dict[str, Any],
    rows: list[Any],
    diagnostic_context: dict[str, Any],
) -> dict[str, Any]:
    intent_type = str(getattr(intent, "type", "") or "").strip()
    factors = confidence.get("factors") if isinstance(confidence.get("factors"), dict) else {}
    sample_size = _resolve_sample_size(rows, hard_facts)
    confidence_score = float(confidence.get("score") or 0.0)

    forecast_viability: dict[str, Any] = {
        "status": "not_applicable",
        "reasons": [],
        "summary": "No aplica un control específico de proyección para esta lectura.",
    }
    if intent_type == "predictive":
        total_periods = hard_facts.get("total_periods")
        if not isinstance(total_periods, (int, float)) or float(total_periods) <= 0:
            total_periods = sample_size
        total_periods = int(total_periods or 0)

        forecast_points = hard_facts.get("forecast_points")
        forecast_point_count = int(forecast_points) if isinstance(forecast_points, (int, float)) and float(forecast_points) > 0 else None
        temporal_coverage = float(factors.get("temporal_coverage") or 0.0)

        forecast_reasons: list[str] = []
        if total_periods < 6:
            forecast_reasons.append("short_history")
        elif total_periods < 9:
            forecast_reasons.append("limited_history")
        if forecast_point_count == 0:
            forecast_reasons.append("no_projected_points")
        elif isinstance(forecast_point_count, int) and forecast_point_count < 2:
            forecast_reasons.append("thin_projection_horizon")
        if temporal_coverage < 0.5:
            forecast_reasons.append("low_temporal_coverage")
        elif temporal_coverage < 0.7:
            forecast_reasons.append("partial_temporal_coverage")
        if confidence_score < 0.55:
            forecast_reasons.append("low_overall_confidence")

        if {"short_history", "no_projected_points", "low_temporal_coverage"} & set(forecast_reasons):
            forecast_status = "blocked"
            forecast_summary = "La proyección no debe tratarse como forecast confiable porque la serie o el horizonte observable no alcanzan soporte mínimo."
        elif forecast_reasons:
            forecast_status = "guarded"
            forecast_summary = "La proyección es utilizable solo como señal tentativa; conviene expresarla con cautela por historial o cobertura limitados."
        else:
            forecast_status = "clear"
            forecast_summary = "La proyección cuenta con soporte temporal suficiente para narrarse como señal prospectiva utilizable."

        forecast_viability = {
            "status": forecast_status,
            "reasons": forecast_reasons[:4],
            "summary": forecast_summary,
            "total_periods": total_periods,
            "forecast_points": forecast_point_count,
        }

    correlation_relevance: dict[str, Any] = {
        "status": "not_applicable",
        "reasons": [],
        "summary": "No aplica un control específico de correlación para esta lectura.",
    }
    correlation_value = hard_facts.get("correlation")
    if isinstance(correlation_value, (int, float)):
        correlation_float = float(correlation_value)
        abs_correlation = abs(correlation_float)
        correlation_reasons: list[str] = []

        if sample_size < 8:
            correlation_reasons.append("sample_too_small")
        elif sample_size < 12:
            correlation_reasons.append("sample_limited")
        if abs_correlation < 0.4:
            correlation_reasons.append("weak_correlation")
        elif abs_correlation < 0.55:
            correlation_reasons.append("moderate_correlation")

        if {"sample_too_small", "weak_correlation"} & set(correlation_reasons):
            correlation_status = "blocked"
            correlation_summary = "La relación observada no debe narrarse como hallazgo relevante porque la fuerza o el soporte muestral son insuficientes."
        elif correlation_reasons:
            correlation_status = "guarded"
            correlation_summary = "La relación observada puede mencionarse como pista, pero no como vínculo validado."
        else:
            correlation_status = "clear"
            correlation_summary = "La relación observada tiene fuerza y soporte suficientes para tratarse como señal diagnóstica utilizable."

        correlation_relevance = {
            "status": correlation_status,
            "reasons": correlation_reasons[:4],
            "summary": correlation_summary,
            "correlation": round(correlation_float, 3),
            "sample_size": sample_size,
        }

    diagnostic_separability: dict[str, Any] = {
        "status": "not_applicable",
        "reasons": [],
        "summary": "No aplica un control específico de separabilidad segmentaria para esta lectura.",
    }
    segment_divergence = diagnostic_context.get("segment_divergence") if isinstance(diagnostic_context.get("segment_divergence"), dict) else {}
    divergence_score = _to_finite_float(segment_divergence.get("divergence_score")) if segment_divergence else None
    if intent_type == "diagnostic" and divergence_score is not None:
        separability_reasons: list[str] = []
        if divergence_score < 0.25:
            separability_reasons.append("low_segment_separation")
        elif divergence_score < 0.45:
            separability_reasons.append("moderate_segment_separation")

        if "low_segment_separation" in separability_reasons:
            separability_status = "blocked"
            separability_summary = "La separación entre segmentos todavía es demasiado baja para sostener un diagnóstico segmentario fuerte."
        elif separability_reasons:
            separability_status = "guarded"
            separability_summary = "Existe una separación observable entre segmentos, pero todavía conviene tratarla como diferencia emergente."
        else:
            separability_status = "clear"
            separability_summary = "La distancia entre segmentos es suficiente para narrar una divergencia operativa material."

        diagnostic_separability = {
            "status": separability_status,
            "reasons": separability_reasons[:3],
            "summary": separability_summary,
            "divergence_score": round(divergence_score, 2),
            "dimension": segment_divergence.get("dimension"),
        }

    guard_statuses = [
        str(forecast_viability.get("status") or "").strip(),
        str(correlation_relevance.get("status") or "").strip(),
        str(diagnostic_separability.get("status") or "").strip(),
    ]
    if "blocked" in guard_statuses:
        overall_status = "blocked"
    elif "guarded" in guard_statuses:
        overall_status = "guarded"
    else:
        overall_status = "clear"

    summaries = [
        str(forecast_viability.get("summary") or "").strip(),
        str(correlation_relevance.get("summary") or "").strip(),
        str(diagnostic_separability.get("summary") or "").strip(),
    ]
    applicable_summaries = [summary for summary in summaries if summary and "No aplica" not in summary]
    if overall_status == "blocked":
        overall_summary = applicable_summaries[0] if applicable_summaries else "La evidencia especializada no soporta una lectura fuerte para este tipo de análisis."
    elif overall_status == "guarded":
        overall_summary = applicable_summaries[0] if applicable_summaries else "La lectura requiere un tono prudente por soporte especializado parcial."
    else:
        overall_summary = applicable_summaries[0] if applicable_summaries else "La lectura especializada no presenta bloqueos relevantes."

    return {
        "overall_status": overall_status,
        "summary": overall_summary,
        "forecast_viability": forecast_viability,
        "correlation_relevance": correlation_relevance,
        "diagnostic_separability": diagnostic_separability,
    }


def _normalize_signal_score(value: float, *, soft_cap: float) -> float:
    if soft_cap <= 0:
        return 0.0
    return round(max(0.0, min(1.0, value / soft_cap)), 2)


def _classify_signal_level(score: float) -> str:
    if score >= 0.8:
        return "high"
    if score >= 0.55:
        return "medium"
    return "low"


def _build_assumptions(*, intent: Any, filters: list[str], rows: list[Any], hard_facts: dict[str, Any]) -> list[str]:
    assumptions: list[str] = []
    intent_type = str(getattr(intent, "type", "") or "").strip()
    sample_size = _resolve_sample_size(rows, hard_facts)

    if filters:
        assumptions.append("Se asume que los filtros aplicados representan el universo de decisión relevante para este análisis.")
    else:
        assumptions.append("Se asume que el universo completo disponible es comparable entre categorías o periodos.")

    if intent_type in {"trend", "predictive"}:
        assumptions.append("Se asume continuidad del patrón histórico observado y ausencia de choques externos no reflejados en la serie.")
    elif intent_type == "diagnostic":
        assumptions.append("La señal diagnóstica se interpreta como asociación operativa y no como causalidad demostrada.")
    else:
        assumptions.append("Se asume consistencia básica entre las categorías agregadas para comparar magnitudes sin sesgos estructurales mayores.")

    if sample_size and sample_size < 12:
        assumptions.append("El tamaño de muestra disponible es acotado; lecturas finas pueden cambiar al ampliar la base analizada.")

    return assumptions[:3]


def _build_suggested_action(
    *,
    intent: Any,
    hard_facts: dict[str, Any],
    confidence: dict[str, Any],
    compliance_result: dict[str, Any],
    metric_polarity: str,
    diagnostic_context: dict[str, Any],
    analysis_guardrails: dict[str, Any],
) -> str:
    mandated_action = _compact_text(compliance_result.get("action"), max_sentences=1, max_chars=120)
    if mandated_action:
        return mandated_action

    forecast_guard = analysis_guardrails.get("forecast_viability") if isinstance(analysis_guardrails.get("forecast_viability"), dict) else {}
    if str(forecast_guard.get("status") or "").strip() == "blocked":
        return "Ampliar la serie histórica antes de usar esta proyección como base de decisión y evitar tratarla como forecast validado."

    correlation_guard = analysis_guardrails.get("correlation_relevance") if isinstance(analysis_guardrails.get("correlation_relevance"), dict) else {}
    if str(correlation_guard.get("status") or "").strip() == "blocked":
        return "No usar esta relación como driver operativo todavía; ampliar muestra o revisar variables antes de escalar una hipótesis."

    separability_guard = analysis_guardrails.get("diagnostic_separability") if isinstance(analysis_guardrails.get("diagnostic_separability"), dict) else {}
    if str(separability_guard.get("status") or "").strip() == "blocked":
        return "Evitar acciones segmentadas fuertes hasta confirmar una separación material más estable entre segmentos."

    driver_relations = diagnostic_context.get("driver_relations") if isinstance(diagnostic_context.get("driver_relations"), list) else []
    if driver_relations:
        top_relation = driver_relations[0]
        correlation = float(top_relation.get("correlation") or 0.0)
        if abs(correlation) >= 0.75:
            return f"Validar el vínculo con {top_relation.get('label') or top_relation.get('column')} para confirmar si puede operar como driver temprano de la métrica principal."

    segment_pressure = diagnostic_context.get("segment_pressure") if isinstance(diagnostic_context.get("segment_pressure"), dict) else {}
    if segment_pressure:
        concentration_score = float(segment_pressure.get("concentration_score") or 0.0)
        top_segments = segment_pressure.get("top_segments") if isinstance(segment_pressure.get("top_segments"), list) else []
        if concentration_score >= 0.7 and top_segments:
            return f"Revisar el segmento líder {top_segments[0].get('name')} porque concentra la mayor presión observable dentro de {segment_pressure.get('dimension_label') or 'la dimensión principal'}."

    total_outliers = hard_facts.get("total_outliers")
    if isinstance(total_outliers, (int, float)) and float(total_outliers) > 0:
        return "Validar los registros atípicos antes de escalar decisiones y confirmar si responden a errores de carga o a eventos operativos reales."

    correlation = hard_facts.get("correlation")
    if isinstance(correlation, (int, float)) and abs(float(correlation)) >= 0.7:
        return "Profundizar en el driver correlacionado para validar si puede usarse como palanca operativa o alerta temprana."

    top_share = hard_facts.get("top_1_share")
    if isinstance(top_share, (int, float)) and float(top_share) >= 45:
        return "Revisar la dependencia del líder detectado y preparar una lectura de respaldo por segmento para evitar concentración operativa."

    growth = hard_facts.get("overall_growth_pct")
    if isinstance(growth, (int, float)):
        growth_value = float(growth)
        if (metric_polarity == "favorable" and growth_value < -8) or (metric_polarity == "unfavorable" and growth_value > 8):
            return "Abrir revisión de causa raíz y contrastar el cambio contra segmentos, periodos o drivers operativos antes de ejecutar una acción mayor."
        if (metric_polarity == "favorable" and growth_value > 8) or (metric_polarity == "unfavorable" and growth_value < -8):
            return "Documentar los factores que explican la mejora y validar si el patrón puede replicarse sin degradar otras métricas."

    if float(confidence.get("score") or 0) < 0.6:
        return "Recolectar más evidencia o ampliar la ventana analizada antes de convertir esta lectura en una decisión ejecutiva."

    intent_type = str(getattr(intent, "type", "") or "").strip()
    if intent_type == "predictive":
        return "Monitorear la próxima ventana temporal para confirmar si la proyección se sostiene y ajustar capacidad o cobertura si el patrón persiste."

    return "Usar esta señal como prioridad de monitoreo y contrastarla con el siguiente corte operativo antes de mover políticas o presupuesto."


def _build_diagnostic_signals(
    *,
    intent: Any,
    hard_facts: dict[str, Any],
    confidence: dict[str, Any],
    metric_polarity: str,
    rows: list[Any],
    diagnostic_context: dict[str, Any],
) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    sample_size = max(1, _resolve_sample_size(rows, hard_facts))

    top_share = hard_facts.get("top_1_share")
    if isinstance(top_share, (int, float)):
        share_value = float(top_share)
        signal_score = _normalize_signal_score(share_value, soft_cap=60)
        signals.append(
            {
                "code": "concentration",
                "label": "Concentracion",
                "score": signal_score,
                "level": _classify_signal_level(signal_score),
                "summary": f"El lider concentra {round(share_value, 1)}% del total analizado.",
            }
        )

    growth = hard_facts.get("overall_growth_pct")
    if isinstance(growth, (int, float)):
        growth_value = float(growth)
        signal_score = _normalize_signal_score(abs(growth_value), soft_cap=35)
        if signal_score > 0:
            if metric_polarity == "unfavorable":
                status = "pressure" if growth_value > 0 else "improvement"
            else:
                status = "improvement" if growth_value > 0 else "pressure"
            signals.append(
                {
                    "code": "trend_shift",
                    "label": "Cambio de tendencia",
                    "score": signal_score,
                    "level": _classify_signal_level(signal_score),
                    "status": status,
                    "summary": f"La metrica muestra un cambio acumulado de {round(growth_value, 2)}%.",
                }
            )

    peak_value = hard_facts.get("peak_value")
    trough_value = hard_facts.get("trough_value")
    if isinstance(peak_value, (int, float)) and isinstance(trough_value, (int, float)) and float(trough_value) > 0:
        ratio = float(peak_value) / float(trough_value)
        signal_score = _normalize_signal_score(max(0.0, ratio - 1.0), soft_cap=2.5)
        if signal_score > 0:
            signals.append(
                {
                    "code": "volatility",
                    "label": "Volatilidad",
                    "score": signal_score,
                    "level": _classify_signal_level(signal_score),
                    "summary": f"La relacion pico/valle alcanza {round(ratio, 2)}x.",
                }
            )

    total_outliers = hard_facts.get("total_outliers")
    if isinstance(total_outliers, (int, float)) and float(total_outliers) > 0:
        outlier_ratio = float(total_outliers) / float(sample_size)
        signal_score = _normalize_signal_score(outlier_ratio, soft_cap=0.25)
        signals.append(
            {
                "code": "outlier_pressure",
                "label": "Presion por outliers",
                "score": signal_score,
                "level": _classify_signal_level(signal_score),
                "summary": f"Se detectaron {int(total_outliers)} valores atipicos dentro de la muestra observada.",
            }
        )

    correlation = hard_facts.get("correlation")
    if isinstance(correlation, (int, float)):
        corr_value = float(correlation)
        signal_score = round(max(0.0, min(1.0, abs(corr_value))), 2)
        signals.append(
            {
                "code": "relationship_strength",
                "label": "Fuerza relacional",
                "score": signal_score,
                "level": _classify_signal_level(signal_score),
                "summary": f"La relacion observada presenta una correlacion de {round(corr_value, 3)}.",
            }
        )

    confidence_gap = round(1 - float(confidence.get("score") or 0), 2)
    if confidence_gap >= 0.3:
        signals.append(
            {
                "code": "support_gap",
                "label": "Soporte analitico",
                "score": confidence_gap,
                "level": _classify_signal_level(confidence_gap),
                "summary": "La robustez estadistica o la cobertura de datos aun limitan la fuerza de esta lectura.",
            }
        )

    segment_pressure = diagnostic_context.get("segment_pressure") if isinstance(diagnostic_context.get("segment_pressure"), dict) else {}
    if segment_pressure:
        concentration_score = round(max(0.0, min(1.0, float(segment_pressure.get("concentration_score") or 0.0))), 2)
        if concentration_score > 0:
            signals.append(
                {
                    "code": "segment_pressure",
                    "label": "Presion por segmento",
                    "score": concentration_score,
                    "level": _classify_signal_level(concentration_score),
                    "summary": str(segment_pressure.get("summary") or "Un segmento concentra la mayor presión observable."),
                }
            )

    driver_relations = diagnostic_context.get("driver_relations") if isinstance(diagnostic_context.get("driver_relations"), list) else []
    if driver_relations:
        top_relation = driver_relations[0]
        relation_score = round(max(0.0, min(1.0, abs(float(top_relation.get("correlation") or 0.0)))), 2)
        if relation_score > 0:
            relation_label = str(top_relation.get("label") or top_relation.get("column") or "driver relacionado")
            signals.append(
                {
                    "code": "driver_linkage",
                    "label": "Vinculo con driver",
                    "score": relation_score,
                    "level": _classify_signal_level(relation_score),
                    "summary": f"La métrica principal muestra una relación {top_relation.get('direction') or 'observable'} con {relation_label}.",
                }
            )

    return sorted(signals, key=lambda item: float(item.get("score") or 0), reverse=True)[:4]


def _build_finding_priority(
    *,
    intent: Any,
    hard_facts: dict[str, Any],
    confidence: dict[str, Any],
    evidence: list[str],
    compliance_result: dict[str, Any],
    metric_polarity: str,
    diagnostic_signals: list[dict[str, Any]],
) -> dict[str, Any]:
    confidence_score = float(confidence.get("score") or 0)
    evidence_density = round(min(1.0, len(evidence) / 3), 2)
    primary_signal = diagnostic_signals[0] if diagnostic_signals else {}
    signal_score = float(primary_signal.get("score") or 0)
    compliance_boost = 0.08 if compliance_result.get("matched") else 0.0

    growth = hard_facts.get("overall_growth_pct")
    impact_bias = 0.45
    stance = "monitor"
    if isinstance(growth, (int, float)):
        growth_value = float(growth)
        if metric_polarity == "unfavorable":
            if growth_value > 0:
                impact_bias = 0.95
                stance = "risk"
            elif growth_value < 0:
                impact_bias = 0.7
                stance = "opportunity"
        else:
            if growth_value < 0:
                impact_bias = 0.95
                stance = "risk"
            elif growth_value > 0:
                impact_bias = 0.72
                stance = "opportunity"

    if compliance_result.get("matched"):
        stance = "risk"

    score = (
        confidence_score * 0.35
        + signal_score * 0.35
        + evidence_density * 0.1
        + impact_bias * 0.2
        + compliance_boost
    )
    score = max(0.0, min(0.99, round(score, 2)))

    if score >= 0.82:
        level = "critical"
    elif score >= 0.66:
        level = "high"
    elif score >= 0.48:
        level = "medium"
    else:
        level = "low"

    primary_label = str(primary_signal.get("label") or "Señal principal")
    if stance == "risk":
        summary = f"{primary_label} emerge como foco prioritario y requiere seguimiento ejecutivo antes de asumir que la variacion es transitoria."
    elif stance == "opportunity":
        summary = f"{primary_label} concentra la oportunidad mas visible y conviene validarla para escalarla con bajo riesgo."
    else:
        summary = f"{primary_label} resume la señal dominante, pero todavia conviene tratarla como monitoreo asistido."

    return {
        "score": score,
        "level": level,
        "stance": stance,
        "primary_signal": primary_signal.get("code"),
        "summary": summary,
    }


def _to_finite_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        numeric = float(value)
        return numeric if math.isfinite(numeric) else None
    try:
        numeric = float(str(value).strip())
        return numeric if math.isfinite(numeric) else None
    except (TypeError, ValueError):
        return None


def _resolve_breakdown_axis_kind(intent: Any) -> str:
    intent_type = str(getattr(intent, "type", "") or "").strip()
    if intent_type in {"trend", "predictive"}:
        return "period"
    if intent_type == "diagnostic":
        return "observation"
    return "category"


def _extract_ranked_points(rows: list[Any]) -> list[dict[str, Any]]:
    ranked_points: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = row.get("name")
        value = _to_finite_float(row.get("value"))
        if name is None or value is None:
            continue
        ranked_points.append(
            {
                "name": str(name),
                "value": value,
            }
        )
    return ranked_points


def _build_driver_breakdown(*, intent: Any, rows: list[Any], hard_facts: dict[str, Any]) -> dict[str, Any]:
    axis_kind = _resolve_breakdown_axis_kind(intent)
    ranked_points = sorted(_extract_ranked_points(rows), key=lambda item: item["value"], reverse=True)

    if not ranked_points:
        return {
            "axis_kind": axis_kind,
            "top_contributors": [],
            "segment_divergence": None,
            "variance_profile": None,
            "summary": "No hay suficiente granularidad observable para aislar contributors o segmentos dominantes.",
        }

    total_reference = _to_finite_float(hard_facts.get("total_analyzed"))
    if total_reference is None or total_reference <= 0:
        positive_sum = sum(item["value"] for item in ranked_points if item["value"] >= 0)
        total_reference = positive_sum if positive_sum > 0 else sum(abs(item["value"]) for item in ranked_points)

    top_contributors: list[dict[str, Any]] = []
    for index, item in enumerate(ranked_points[:3], start=1):
        share = (item["value"] / total_reference * 100) if total_reference and total_reference > 0 else None
        top_contributors.append(
            {
                "rank": index,
                "name": item["name"],
                "value": round(item["value"], 4),
                "share_pct": round(share, 2) if share is not None else None,
            }
        )

    top_value = ranked_points[0]["value"]
    median_value = ranked_points[len(ranked_points) // 2]["value"]
    mean_value = sum(item["value"] for item in ranked_points) / len(ranked_points)
    spread_ratio = (top_value / median_value) if median_value not in {0, None} else None
    variance_ratio = (
        max(item["value"] for item in ranked_points) / min(item["value"] for item in ranked_points if item["value"] > 0)
        if any(item["value"] > 0 for item in ranked_points)
        else None
    )

    segment_divergence = None
    if spread_ratio is not None and math.isfinite(spread_ratio):
        divergence_score = round(max(0.0, min(1.0, (spread_ratio - 1.0) / 2.5)), 2)
        segment_divergence = {
            "score": divergence_score,
            "level": _classify_signal_level(divergence_score),
            "summary": f"El elemento líder supera a la mediana observada en {round(spread_ratio, 2)}x.",
        }

    variance_profile = {
        "mean_value": round(mean_value, 4),
        "top_value": round(top_value, 4),
        "spread_ratio": round(spread_ratio, 2) if spread_ratio is not None and math.isfinite(spread_ratio) else None,
        "range_ratio": round(variance_ratio, 2) if variance_ratio is not None and math.isfinite(variance_ratio) else None,
    }

    if axis_kind == "period":
        summary = f"Los periodos con mayor peso explican la mayor parte de la señal reciente; el principal punto observado es {top_contributors[0]['name']}."
    else:
        summary = f"Los contributors líderes concentran la mayor señal observable; el principal es {top_contributors[0]['name']}."

    return {
        "axis_kind": axis_kind,
        "top_contributors": top_contributors,
        "segment_divergence": segment_divergence,
        "variance_profile": variance_profile,
        "summary": summary,
    }


def _build_variance_decomposition(
    *,
    driver_breakdown: dict[str, Any],
    diagnostic_context: dict[str, Any],
) -> dict[str, Any]:
    top_contributors = driver_breakdown.get("top_contributors") if isinstance(driver_breakdown.get("top_contributors"), list) else []
    axis_kind = str(driver_breakdown.get("axis_kind") or "unknown")
    if not top_contributors:
        return {
            "axis_kind": axis_kind,
            "dominant_factor": None,
            "explained_share_pct": None,
            "residual_share_pct": None,
            "top_components": [],
            "driver_alignment": None,
            "summary": "No hay suficiente granularidad para cuantificar qué factor explica la mayor parte del resultado.",
        }

    explained_share_values = [
        float(item.get("share_pct"))
        for item in top_contributors
        if isinstance(item.get("share_pct"), (int, float))
    ]
    explained_share_pct = round(sum(explained_share_values), 2) if explained_share_values else None
    residual_share_pct = round(max(0.0, 100.0 - explained_share_pct), 2) if explained_share_pct is not None else None
    dominant_factor = top_contributors[0]

    driver_alignment = None
    driver_relations = diagnostic_context.get("driver_relations") if isinstance(diagnostic_context.get("driver_relations"), list) else []
    if driver_relations:
        top_relation = driver_relations[0]
        driver_alignment = {
            "label": top_relation.get("label") or top_relation.get("column"),
            "correlation": top_relation.get("correlation"),
            "direction": top_relation.get("direction"),
            "support_size": top_relation.get("support_size"),
        }

    if axis_kind == "period":
        summary = (
            f"Los periodos líderes explican {explained_share_pct}% de la señal visible; "
            f"el principal es {dominant_factor.get('name')}."
            if explained_share_pct is not None
            else f"El periodo dominante es {dominant_factor.get('name')}."
        )
    else:
        summary = (
            f"Los componentes líderes explican {explained_share_pct}% de la señal visible; "
            f"el principal es {dominant_factor.get('name')}."
            if explained_share_pct is not None
            else f"El componente dominante es {dominant_factor.get('name')}."
        )

    return {
        "axis_kind": axis_kind,
        "dominant_factor": dominant_factor,
        "explained_share_pct": explained_share_pct,
        "residual_share_pct": residual_share_pct,
        "top_components": top_contributors,
        "driver_alignment": driver_alignment,
        "summary": summary,
    }


def _build_forecast_explainability(
    *,
    intent: Any,
    hard_facts: dict[str, Any],
    confidence: dict[str, Any],
    analysis_guardrails: dict[str, Any],
    rows: list[Any],
) -> dict[str, Any]:
    intent_type = str(getattr(intent, "type", "") or "").strip()
    if intent_type != "predictive":
        return {
            "status": "not_applicable",
            "support_level": None,
            "requested_horizon": getattr(intent, "horizon", None),
            "grain": getattr(getattr(intent, "grain", None), "value", None) or getattr(intent, "grain", None),
            "summary": "No aplica una explicación de forecast para esta lectura.",
        }

    forecast_guard = analysis_guardrails.get("forecast_viability") if isinstance(analysis_guardrails.get("forecast_viability"), dict) else {}
    status = str(forecast_guard.get("status") or "guarded").strip()
    support_level = str(confidence.get("level") or "low").strip()
    temporal_coverage = float((confidence.get("factors") or {}).get("temporal_coverage") or 0.0)
    total_periods = forecast_guard.get("total_periods")
    if not isinstance(total_periods, int):
        total_periods = _resolve_sample_size(rows, hard_facts)
    forecast_points = forecast_guard.get("forecast_points")
    if not isinstance(forecast_points, int):
        raw_forecast_points = hard_facts.get("forecast_points")
        forecast_points = int(raw_forecast_points) if isinstance(raw_forecast_points, (int, float)) and float(raw_forecast_points) > 0 else None
    requested_horizon = getattr(intent, "horizon", None)
    grain = getattr(getattr(intent, "grain", None), "value", None) or getattr(intent, "grain", None)
    reasons = list(forecast_guard.get("reasons") or [])

    if status == "blocked":
        summary = (
            f"El forecast queda rechazado porque la serie solo aporta {total_periods} periodos útiles "
            f"y la cobertura temporal efectiva es {round(temporal_coverage, 2)}."
        )
    elif status == "guarded":
        summary = (
            f"El forecast puede leerse solo como señal tentativa: historial útil {total_periods} periodos, "
            f"cobertura temporal {round(temporal_coverage, 2)}."
        )
    else:
        summary = (
            f"El forecast es utilizable: historial útil {total_periods} periodos, "
            f"cobertura temporal {round(temporal_coverage, 2)} y soporte {support_level}."
        )

    return {
        "status": status,
        "support_level": support_level,
        "requested_horizon": requested_horizon,
        "grain": grain,
        "total_periods": total_periods,
        "forecast_points": forecast_points,
        "temporal_coverage": round(temporal_coverage, 2),
        "reasons": reasons[:4],
        "summary": summary,
    }


def build_analysis_explainability(
    *,
    plan: Any,
    ibis_output: dict[str, Any],
    actual_prompt: str,
    compliance_result: dict[str, Any] | None = None,
    diagnostic_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    compliance_result = compliance_result or {}
    diagnostic_context = diagnostic_context or {}
    aliases = getattr(plan, "column_aliases", {}) or {}
    intent = getattr(plan, "main_intent", None)
    filters = _serialize_filters(list(getattr(intent, "filters", None) or []), aliases)

    raw_metrics = []
    for attr in ("metric", "value_column"):
        value = getattr(intent, attr, None)
        if value:
            raw_metrics.append(value)
    for value in list(getattr(intent, "metrics", None) or []):
        if value and value not in raw_metrics:
            raw_metrics.append(value)

    raw_dimensions = []
    for attr in ("dimension", "date_column"):
        value = getattr(intent, attr, None)
        if value:
            raw_dimensions.append(value)
    for value in list(getattr(intent, "group_by", None) or []):
        if value and value not in raw_dimensions:
            raw_dimensions.append(value)

    metrics = [_humanize_column(metric, aliases) or str(metric) for metric in raw_metrics]
    dimensions = [_humanize_column(dimension, aliases) or str(dimension) for dimension in raw_dimensions]
    evidence = _extract_evidence(ibis_output, aliases)
    limitations = _build_limitations(intent=intent, ibis_output=ibis_output, filters=filters, evidence=evidence)
    confidence = _build_confidence(
        intent=intent,
        evidence=evidence,
        filters=filters,
        ibis_output=ibis_output,
        compliance_result=compliance_result,
    )
    hard_facts = ibis_output.get("hard_facts") if isinstance(ibis_output.get("hard_facts"), dict) else {}
    metric_polarity = str(getattr(plan, "metric_polarity", "neutral") or "neutral")
    rows = ibis_output.get("data") if isinstance(ibis_output.get("data"), list) else []
    assumptions = _build_assumptions(intent=intent, filters=filters, rows=rows, hard_facts=hard_facts)
    analysis_guardrails = _build_analysis_guardrails(
        intent=intent,
        hard_facts=hard_facts,
        confidence=confidence,
        rows=rows,
        diagnostic_context=diagnostic_context,
    )
    diagnostic_signals = _build_diagnostic_signals(
        intent=intent,
        hard_facts=hard_facts,
        confidence=confidence,
        metric_polarity=metric_polarity,
        rows=rows,
        diagnostic_context=diagnostic_context,
    )
    conclusion_gate = _build_conclusion_gate(
        intent=intent,
        confidence=confidence,
        evidence=evidence,
        ibis_output=ibis_output,
        diagnostic_context=diagnostic_context,
        compliance_result=compliance_result,
        analysis_guardrails=analysis_guardrails,
    )
    probable_causes = _build_probable_causes(
        hard_facts=hard_facts,
        diagnostic_context=diagnostic_context,
        conclusion_gate=conclusion_gate,
    )
    driver_breakdown = _build_driver_breakdown(
        intent=intent,
        rows=rows,
        hard_facts=hard_facts,
    )
    variance_decomposition = _build_variance_decomposition(
        driver_breakdown=driver_breakdown,
        diagnostic_context=diagnostic_context,
    )
    forecast_explainability = _build_forecast_explainability(
        intent=intent,
        hard_facts=hard_facts,
        confidence=confidence,
        analysis_guardrails=analysis_guardrails,
        rows=rows,
    )
    finding_priority = _build_finding_priority(
        intent=intent,
        hard_facts=hard_facts,
        confidence=confidence,
        evidence=evidence,
        compliance_result=compliance_result,
        metric_polarity=metric_polarity,
        diagnostic_signals=diagnostic_signals,
    )
    suggested_action = _build_suggested_action(
        intent=intent,
        hard_facts=hard_facts,
        confidence=confidence,
        compliance_result=compliance_result,
        metric_polarity=metric_polarity,
        diagnostic_context=diagnostic_context,
        analysis_guardrails=analysis_guardrails,
    )
    driver_relations = diagnostic_context.get("driver_relations") if isinstance(diagnostic_context.get("driver_relations"), list) else []
    pressure_segments = diagnostic_context.get("segment_pressure") if isinstance(diagnostic_context.get("segment_pressure"), dict) else None
    segment_divergence = diagnostic_context.get("segment_divergence") if isinstance(diagnostic_context.get("segment_divergence"), dict) else None

    visual_protocol = getattr(getattr(intent, "visual_protocol", None), "value", None) or getattr(intent, "visual_protocol", None)
    visual_label = VISUAL_LABELS.get(str(visual_protocol), str(visual_protocol or "n/a"))
    intent_type = str(getattr(intent, "type", "") or "").strip()

    compliance_payload = {
        "matched": bool(compliance_result.get("matched")),
        "document_title": compliance_result.get("document_title"),
        "rule_sentence": _compact_text(compliance_result.get("rule_sentence"), max_sentences=2, max_chars=180),
        "action": _compact_text(compliance_result.get("action"), max_sentences=1, max_chars=120),
        "observed_value": compliance_result.get("observed_value"),
        "threshold": compliance_result.get("threshold"),
    }

    return {
        "title": getattr(plan, "title", "Analisis"),
        "question_interpreted": str(actual_prompt or "").strip(),
        "intent_type": INTENT_LABELS.get(intent_type, intent_type or "unknown"),
        "visual_protocol": visual_label,
        "rationale": _compact_text(getattr(intent, "rationale", "")),
        "methodology": _build_methodology(intent),
        "metrics": metrics,
        "dimensions": dimensions,
        "filters": filters,
        "evidence": evidence,
        "assumptions": assumptions,
        "limitations": limitations,
        "metric_polarity": metric_polarity,
        "confidence": confidence,
        "conclusion_gate": conclusion_gate,
        "analysis_guardrails": analysis_guardrails,
        "diagnostic_signals": diagnostic_signals,
        "driver_breakdown": driver_breakdown,
        "variance_decomposition": variance_decomposition,
        "forecast_explainability": forecast_explainability,
        "driver_relations": driver_relations,
        "pressure_segments": pressure_segments,
        "segment_divergence": segment_divergence,
        "probable_causes": probable_causes,
        "finding_priority": finding_priority,
        "suggested_action": suggested_action,
        "compliance": compliance_payload,
    }
