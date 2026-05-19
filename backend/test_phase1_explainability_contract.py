import pandas as pd

from app.core.semantic_grammar import AnalysisPlan
from app.services.analysis_explainability import build_analysis_explainability
from app.services.analysis_diagnostic_context import build_enterprise_diagnostic_context


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _build_descriptive_plan() -> AnalysisPlan:
    return AnalysisPlan.model_validate(
        {
            "title": "Stock por almacen",
            "column_aliases": {
                "stock_disponible": "Stock disponible",
                "almacen": "Almacen",
                "tipo_almacen": "Tipo almacen",
            },
            "metric_polarity": "neutral",
            "main_intent": {
                "type": "descriptive",
                "rationale": "Se eligio una vista descriptiva para resumir el stock disponible y compararlo por almacen sin mezclar series temporales ni diagnosticos innecesarios.",
                "metrics": ["stock_disponible"],
                "group_by": ["almacen"],
                "aggregation": "sum",
                "metric_unit": "quantity",
                "visual_protocol": "bar_chart",
                "filters": [
                    {"column": "tipo_almacen", "operator": "==", "value": "Principal"},
                ],
            },
        }
    )


def _build_predictive_plan() -> AnalysisPlan:
    return AnalysisPlan.model_validate(
        {
            "title": "Proyeccion mensual de ventas",
            "column_aliases": {
                "fecha": "Fecha",
                "ventas": "Ventas",
            },
            "metric_polarity": "favorable",
            "main_intent": {
                "type": "predictive",
                "rationale": "Se eligio un forecast porque la consulta pide anticipar comportamiento futuro con base en la serie historica disponible.",
                "date_column": "fecha",
                "value_column": "ventas",
                "metric": "ventas",
                "analysis_subtype": "forecast",
                "grain": "month",
                "horizon": 6,
                "visual_protocol": "line_chart",
                "filters": [],
            },
        }
    )


def _build_diagnostic_plan() -> AnalysisPlan:
    return AnalysisPlan.model_validate(
        {
            "title": "Correlacion operativa",
            "column_aliases": {
                "merma": "Merma",
                "devoluciones": "Devoluciones",
            },
            "metric_polarity": "unfavorable",
            "main_intent": {
                "type": "diagnostic",
                "rationale": "Se eligio una lectura diagnostica para entender si existe relacion util entre las variables observadas.",
                "metrics": ["merma", "devoluciones"],
                "visual_protocol": "scatter_plot",
                "filters": [
                    {"column": "region", "operator": "==", "value": "Norte"},
                ],
            },
        }
    )


def run() -> None:
    descriptive_context = build_enterprise_diagnostic_context(
        plan=_build_descriptive_plan(),
        granular_df=pd.DataFrame(
            {
                "almacen": ["Lima", "Lima", "Lima", "Arequipa", "Arequipa", "Piura", "Piura", "Piura"],
                "stock_disponible": [700, 610, 540, 930, 850, 260, 210, 160],
                "rotacion": [9, 8, 7, 6, 6, 3, 2, 2],
                "quiebres": [1, 1, 2, 2, 2, 5, 4, 5],
            }
        ),
        schema_profile={
            "almacen": {"role": "dimension"},
            "stock_disponible": {"role": "metric"},
            "rotacion": {"role": "metric"},
            "quiebres": {"role": "metric"},
        },
    )
    descriptive = build_analysis_explainability(
        plan=_build_descriptive_plan(),
        actual_prompt="Analiza el stock por almacen principal",
        ibis_output={
            "data": [
                {"name": "Lima", "value": 1250},
                {"name": "Arequipa", "value": 930},
                {"name": "Piura", "value": 610},
            ],
            "hard_facts": {
                "top_1_name": "Lima",
                "top_1_val": 1250,
                "top_1_share": 44.8,
                "total_analyzed": 2790,
            },
        },
        diagnostic_context=descriptive_context,
    )
    _assert(descriptive["intent_type"] == "descriptive", "Debe preservar el intent_type")
    _assert(len(descriptive["filters"]) == 1, "Debe serializar filtros")
    _assert("confidence" in descriptive, "Debe incluir confidence")
    _assert("conclusion_gate" in descriptive, "Debe incluir compuerta de suficiencia")
    _assert("analysis_guardrails" in descriptive, "Debe incluir guardrails especializados por tipo de análisis")
    _assert("finding_priority" in descriptive, "Debe incluir prioridad del hallazgo")
    _assert("diagnostic_signals" in descriptive, "Debe incluir señales diagnósticas")
    _assert("driver_breakdown" in descriptive, "Debe incluir breakdown de contributors")
    _assert("variance_decomposition" in descriptive, "Debe incluir descomposición cuantificada del impacto")
    _assert("forecast_explainability" in descriptive, "Debe incluir explicabilidad explícita de forecast aunque no aplique")
    _assert("probable_causes" in descriptive, "Debe incluir causas probables")
    _assert("assumptions" in descriptive, "Debe incluir supuestos auditables")
    _assert("suggested_action" in descriptive, "Debe incluir acción sugerida")
    _assert("factors" in descriptive["confidence"], "Debe incluir factores auditables")
    _assert(
        set(descriptive["confidence"]["factors"].keys()) == {
            "temporal_coverage",
            "valid_point_ratio",
            "universe_density",
            "filter_consistency",
            "statistical_strength",
        },
        "Debe exponer exactamente los factores de confianza esperados",
    )
    _assert(
        descriptive["finding_priority"]["primary_signal"] in {"concentration", "driver_linkage", "segment_pressure"},
        "La prioridad del hallazgo debe poder apoyarse en concentración o en señales diagnósticas enriquecidas",
    )
    _assert(len(descriptive["diagnostic_signals"]) >= 1, "Debe exponer al menos una señal diagnóstica")
    _assert(descriptive["driver_breakdown"]["axis_kind"] == "category", "El breakdown descriptivo debe clasificar categorías")
    _assert(descriptive["driver_breakdown"]["top_contributors"][0]["name"] == "Lima", "El contributor principal debe respetar el ranking observado")
    _assert(descriptive["driver_breakdown"]["segment_divergence"]["score"] > 0, "Debe calcular divergencia entre líder y mediana")
    _assert(len(descriptive["driver_relations"]) >= 1, "Debe exponer relaciones con drivers cuando el granular lo permita")
    _assert(descriptive["pressure_segments"]["dimension"] == "almacen", "Debe identificar la dimensión con presión principal")
    _assert(descriptive["segment_divergence"]["dimension"] == "almacen", "Debe identificar divergencia segmentaria sobre la dimensión principal")
    _assert(len(descriptive["probable_causes"]) >= 1, "Debe proponer al menos una causa probable cuando hay contexto suficiente")
    _assert(descriptive["conclusion_gate"]["decision"] in {"allow_strong_conclusion", "cautionary_conclusion"}, "La lectura descriptiva suficiente no debe degradarse a evidencia insuficiente")
    _assert(descriptive["analysis_guardrails"]["overall_status"] in {"clear", "guarded"}, "La lectura descriptiva no debe quedar bloqueada si tiene soporte suficiente")
    _assert(descriptive["variance_decomposition"]["dominant_factor"]["name"] == "Lima", "La descomposición debe respetar el contributor dominante")
    _assert(
        descriptive["variance_decomposition"]["explained_share_pct"] is not None and descriptive["variance_decomposition"]["explained_share_pct"] > 0,
        "La descomposición debe cuantificar la parte explicada del resultado",
    )
    _assert(
        descriptive["forecast_explainability"]["status"] == "not_applicable",
        "La explicabilidad de forecast no debe activarse en lecturas descriptivas",
    )
    _assert(len(descriptive["assumptions"]) >= 2, "Debe explicitar supuestos básicos del análisis")
    _assert(len(descriptive["rationale"]) <= 220, "La rationale debe venir compactada")

    predictive_context = build_enterprise_diagnostic_context(
        plan=_build_predictive_plan(),
        granular_df=pd.DataFrame(
            {
                "fecha": pd.date_range("2025-01-01", periods=12, freq="MS"),
                "ventas": [110, 118, 121, 126, 133, 140, 146, 151, 156, 163, 168, 174],
                "inversion_marketing": [45, 48, 49, 52, 55, 58, 60, 62, 64, 67, 69, 72],
                "tickets": [900, 930, 945, 980, 1005, 1030, 1050, 1065, 1090, 1125, 1140, 1175],
            }
        ),
        schema_profile={
            "fecha": {"role": "date"},
            "ventas": {"role": "metric"},
            "inversion_marketing": {"role": "metric"},
            "tickets": {"role": "metric"},
        },
    )
    predictive = build_analysis_explainability(
        plan=_build_predictive_plan(),
        actual_prompt="Proyecta las ventas mensuales del siguiente semestre",
        ibis_output={
            "data": [
                {"name": "2025-01", "value": 110},
                {"name": "2025-02", "value": 118},
                {"name": "2025-03", "value": 121},
                {"name": "2025-04", "value": 126},
                {"name": "2025-05", "value": 133},
                {"name": "2025-06", "value": 140},
                {"name": "2025-07", "value": 146},
                {"name": "2025-08", "value": 151},
                {"name": "2025-09", "value": 156},
                {"name": "2025-10", "value": 163},
                {"name": "2025-11", "value": 168},
                {"name": "2025-12", "value": 174},
            ],
            "hard_facts": {
                "start_val": 110,
                "end_val": 174,
                "overall_growth_pct": 58.18,
                "trend": "Creciente",
                "peak_period": "2025-12",
                "peak_value": 174,
                "trough_period": "2025-01",
                "trough_value": 110,
                "total_periods": 12,
                "yoy_avg_pct": 11.2,
                "yoy_available": True,
            },
        },
        compliance_result={
            "matched": True,
            "document_title": "Politica Comercial",
            "rule_sentence": "Si la proyeccion de ventas supera el umbral aprobado, escalar a la gerencia comercial para ampliar capacidad de atencion y cobertura.",
            "action": "Escalar a gerencia comercial para ampliar capacidad.",
            "observed_value": 174,
            "threshold": 160,
        },
        diagnostic_context=predictive_context,
    )
    _assert(predictive["confidence"]["level"] == "high", "Un forecast con 12 periodos y hard facts ricos debe quedar en high")
    _assert(
        predictive["confidence"]["factors"]["temporal_coverage"] >= 0.85,
        "La cobertura temporal del forecast debe ser alta",
    )
    _assert(
        predictive["confidence"]["factors"]["statistical_strength"] >= 0.8,
        "La fortaleza estadistica del forecast debe reflejar soporte suficiente",
    )
    _assert(
        predictive["finding_priority"]["level"] in {"high", "critical"},
        "Un forecast con señal fuerte y compliance debe quedar priorizado",
    )
    _assert(
        predictive["conclusion_gate"]["decision"] == "allow_strong_conclusion",
        "Un forecast bien soportado debe permitir una conclusión fuerte",
    )
    _assert(
        predictive["analysis_guardrails"]["forecast_viability"]["status"] == "clear",
        "Un forecast robusto debe superar el guardrail especializado de proyección",
    )
    _assert(
        predictive["forecast_explainability"]["status"] == "clear",
        "Un forecast robusto debe exponer su explicabilidad como habilitada",
    )
    _assert(
        predictive["forecast_explainability"]["total_periods"] == 12,
        "La explicabilidad de forecast debe exponer los periodos históricos observados",
    )
    _assert(
        predictive["forecast_explainability"]["requested_horizon"] == 6,
        "La explicabilidad de forecast debe preservar el horizonte solicitado",
    )
    _assert(
        predictive["driver_breakdown"]["axis_kind"] == "period",
        "El breakdown predictivo debe reconocer periodos como eje principal",
    )
    _assert(
        predictive["variance_decomposition"]["axis_kind"] == "period",
        "La descomposición predictiva debe cuantificar el impacto por periodo",
    )
    _assert(
        predictive["driver_breakdown"]["top_contributors"][0]["name"] == "2025-12",
        "El periodo pico debe aparecer como contributor principal en la proyección",
    )
    _assert(
        predictive["diagnostic_signals"][0]["code"] in {"trend_shift", "driver_linkage", "segment_pressure"},
        "La priorización debe poder usar señales enriquecidas por el contexto granular",
    )
    _assert(
        len(predictive["probable_causes"]) >= 1,
        "Un forecast fuerte debe poder exponer causas probables prudentes",
    )
    _assert(
        predictive["suggested_action"] == "Escalar a gerencia comercial para ampliar capacidad.",
        "La acción sugerida debe respetar el mandato institucional cuando aplica",
    )
    _assert(predictive["compliance"]["matched"] is True, "Debe preservar el match de compliance")
    _assert(len(predictive["compliance"]["rule_sentence"]) <= 180, "La regla institucional debe salir compactada")

    weak_predictive = build_analysis_explainability(
        plan=_build_predictive_plan(),
        actual_prompt="Proyecta ventas con el historial corto disponible",
        ibis_output={
            "data": [
                {"name": "2025-01", "value": 110},
                {"name": "2025-02", "value": 112},
                {"name": "2025-03", "value": 111},
                {"name": "2025-04", "value": 114},
            ],
            "hard_facts": {
                "total_periods": 4,
                "forecast_points": 1,
                "overall_growth_pct": 3.64,
                "peak_period": "2025-04",
                "peak_value": 114,
                "trough_period": "2025-01",
                "trough_value": 110,
            },
        },
    )
    _assert(
        weak_predictive["analysis_guardrails"]["forecast_viability"]["status"] == "blocked",
        "Un forecast con historial corto debe bloquearse por guardrail especializado",
    )
    _assert(
        weak_predictive["forecast_explainability"]["status"] == "blocked",
        "Un forecast débil debe explicar explícitamente su rechazo",
    )
    _assert(
        weak_predictive["conclusion_gate"]["decision"] == "insufficient_evidence",
        "Un forecast débil no debe permitir una conclusión firme",
    )
    _assert(
        weak_predictive["probable_causes"] == [],
        "Un forecast bloqueado no debe inventar causas probables",
    )
    _assert(
        "Ampliar la serie histórica" in weak_predictive["suggested_action"],
        "La acción sugerida debe pedir más historia cuando la proyección es débil",
    )

    weak_diagnostic = build_analysis_explainability(
        plan=_build_diagnostic_plan(),
        actual_prompt="Evalúa si merma y devoluciones guardan una relación útil",
        ibis_output={
            "data": [
                {"name": "Obs-1", "value": 10},
                {"name": "Obs-2", "value": 11},
                {"name": "Obs-3", "value": 9},
            ],
            "hard_facts": {
                "correlation": 0.21,
                "strength": "Débil",
                "sample_size": 9,
            },
        },
    )
    _assert(
        weak_diagnostic["analysis_guardrails"]["correlation_relevance"]["status"] == "blocked",
        "Una correlación débil no debe narrarse como hallazgo relevante",
    )
    _assert(
        weak_diagnostic["forecast_explainability"]["status"] == "not_applicable",
        "La explicabilidad de forecast no debe contaminar lecturas diagnósticas",
    )
    _assert(
        weak_diagnostic["conclusion_gate"]["decision"] == "insufficient_evidence",
        "Una relación débil debe degradarse a evidencia insuficiente",
    )
    _assert(
        weak_diagnostic["probable_causes"] == [],
        "Sin relación relevante no debe proponer causas probables",
    )
    _assert(
        "No usar esta relación como driver operativo" in weak_diagnostic["suggested_action"],
        "La acción sugerida debe evitar convertir una relación débil en driver",
    )

    sparse = build_analysis_explainability(
        plan=_build_diagnostic_plan(),
        actual_prompt="Encuentra una relacion entre merma y devoluciones en la region norte",
        ibis_output={
            "data": [],
            "hard_facts": {},
            "error": "Sin datos suficientes para correlacion.",
        },
    )
    _assert(sparse["confidence"]["level"] == "low", "Sin datos la confianza debe ser low")
    _assert(len(sparse["limitations"]) >= 1, "Debe exponer limitaciones cuando no hay base suficiente")
    _assert(
        sparse["confidence"]["factors"]["filter_consistency"] <= 0.3,
        "Si hay filtros pero no hay datos, la consistencia debe caer",
    )
    _assert(
        sparse["finding_priority"]["level"] == "low",
        "Sin datos suficientes la prioridad del hallazgo debe degradarse",
    )
    _assert(
        sparse["conclusion_gate"]["decision"] == "insufficient_evidence",
        "Sin base suficiente la compuerta debe bloquear conclusiones fuertes",
    )
    _assert(
        any(signal.get("code") == "support_gap" for signal in sparse["diagnostic_signals"]),
        "Sin base suficiente debe aparecer la señal de soporte analítico bajo",
    )
    _assert(
        sparse["driver_breakdown"]["top_contributors"] == [],
        "Sin datos el breakdown de drivers debe permanecer vacío",
    )
    _assert(
        sparse["probable_causes"] == [],
        "Sin evidencia suficiente no debe inventar causas probables",
    )

    print("OK: phase1 explainability contract")


if __name__ == "__main__":
    run()
