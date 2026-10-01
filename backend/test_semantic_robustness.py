"""Tests de robustez de la capa semántica (Tier 0).

Blindan la clase estructural de fallos que causó el incidente de producción
"muestrame un analisis gerencial" (crash por aggregation="mean"):

1. Canonicalización única de agregación (alias ES/EN/PT → Literal).
2. El macro bundle con una métrica no aditiva (sin alternativa) NO debe lanzar.
3. El trend del macro describe una métrica que reconcilia con el KPI.
4. Propiedad: todo plan emitido por el builder tiene aggregation dentro del Literal.
5. El firebreak de translate() degrada ante un error de construcción, no mata.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from app.core.semantic_grammar import (
    AGGREGATION_LITERAL_VALUES,
    DescriptiveIntent,
    DistributionIntent,
    PreAggregationSpec,
    TimeGrain,
    TimeTrendIntent,
    canonicalize_aggregation,
)
from app.services.semantic_translator import planner


# ═══════════════════════════════════════════════════════════════
# 1. Canonicalización
# ═══════════════════════════════════════════════════════════════

class TestCanonicalizeAggregation:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("mean", "avg"), ("average", "avg"), ("Promedio", "avg"), ("MEDIA", "avg"),
            ("sum", "sum"), ("SUMA", "sum"), ("Total", "sum"), ("soma", "sum"),
            ("min", "min"), ("Mínimo", "min"), ("max", "max"), ("mayor", "max"),
            ("count", "count"), ("conteo", "count"), ("contar", "count"),
            (None, "sum"), ("", "sum"),
        ],
    )
    def test_aliases_map_to_literal(self, raw, expected):
        assert canonicalize_aggregation(raw) == expected

    def test_recognized_but_unsupported_values_are_coerced(self):
        # Deben producir SIEMPRE un valor válido (nunca crashear).
        for raw in ("median", "std", "variance", "count_distinct", "nunique"):
            assert canonicalize_aggregation(raw) in AGGREGATION_LITERAL_VALUES

    def test_unknown_falls_back_to_sum(self):
        assert canonicalize_aggregation("banana") == "sum"

    def test_intents_canonicalize_on_construction(self):
        d = DescriptiveIntent(rationale="x", metrics=["a"], aggregation="mean")
        assert d.aggregation == "avg"
        t = TimeTrendIntent(rationale="x", date_column="f", value_column="v", aggregation="AVERAGE")
        assert t.aggregation == "avg"
        p = PreAggregationSpec(group_by=["g"], metrics=["m"], aggregation="nunique")
        assert p.aggregation in AGGREGATION_LITERAL_VALUES

    def test_no_producer_can_emit_out_of_literal(self):
        # El escenario exacto del crash de producción ya no es posible.
        d = DescriptiveIntent(rationale="x", metrics=["a"], aggregation="mean")
        assert d.aggregation in AGGREGATION_LITERAL_VALUES


# ═══════════════════════════════════════════════════════════════
# Fixtures de datasets sintéticos multi-dominio
# ═══════════════════════════════════════════════════════════════

def _schema(df: pd.DataFrame) -> dict[str, dict[str, Any]]:
    schema: dict[str, dict[str, Any]] = {}
    for col in df.columns:
        if str(col).lower() in {"fecha", "date"}:
            schema[col] = {"role": "date", "type": "temporal",
                           "cardinality": int(df[col].nunique()), "cardinality_ratio": 0.1}
        elif str(col).lower() in {"categoria", "producto", "planta", "sede", "cliente"}:
            schema[col] = {"role": "dimension", "type": "categorical",
                           "cardinality": int(df[col].nunique()), "cardinality_ratio": 0.1}
        else:
            schema[col] = {"role": "metric", "type": "numeric",
                           "cardinality": int(df[col].nunique()), "cardinality_ratio": 0.5}
    return schema


@pytest.fixture
def only_non_additive_df() -> pd.DataFrame:
    """Métrica a nivel documento/encabezado repetida en cada línea (SAP-like).

    No existe alternativa aditiva → antes crasheaba con aggregation="mean".
    """
    return pd.DataFrame({
        "monto_documento": [100.0, 100.0, 100.0, 100.0, 250.0, 250.0],
        "fecha": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-02-01",
                                 "2024-02-02", "2024-03-01", "2024-03-02"]),
        "categoria": ["A", "B", "A", "B", "A", "B"],
    })


@pytest.fixture
def additive_plus_non_additive_df() -> pd.DataFrame:
    """Documento repetido (no aditivo) + total de línea (aditivo) + fecha."""
    return pd.DataFrame({
        "monto_documento": [500.0, 500.0, 500.0, 900.0, 900.0, 900.0],
        "total_linea": [120.0, 130.0, 140.0, 200.0, 210.0, 220.0],
        "fecha": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-02-01",
                                 "2024-02-02", "2024-03-01", "2024-03-02"]),
        "categoria": ["A", "B", "A", "B", "A", "B"],
    })


@pytest.fixture
def healthy_multi_family_df() -> pd.DataFrame:
    return pd.DataFrame({
        "ventas": [1000.0, 2000.0, 1500.0, 1800.0, 2200.0],
        "margen_pct": [0.10, 0.15, 0.12, 0.11, 0.13],
        "fecha": pd.to_datetime(["2024-01-01", "2024-02-01", "2024-03-01",
                                 "2024-04-01", "2024-05-01"]),
        "categoria": ["A", "B", "A", "B", "A"],
    })


BROAD_PROMPT = "realiza un analisis general"


# ═══════════════════════════════════════════════════════════════
# 2 + 4. Macro bundle: no crashea y todo plan es válido
# ═══════════════════════════════════════════════════════════════

class TestMacroBundleRobustness:
    def test_only_non_additive_metric_does_not_crash(self, only_non_additive_df):
        df = only_non_additive_df
        plans = planner.build_macro_analysis_bundle(
            BROAD_PROMPT, list(df.columns),
            schema_profile=_schema(df), dataset_contract={}, candidate_df=df,
        )
        assert plans, "El bundle debe producir planes aunque la métrica sea no aditiva"
        kpi = plans[0].main_intent
        assert isinstance(kpi, DescriptiveIntent)
        assert kpi.aggregation == "avg"
        assert "(promedio por registro)" in plans[0].title

    def test_non_additive_second_metric_trend_uses_primary(self, additive_plus_non_additive_df):
        """La 2ª métrica (no aditiva) NO debe ser el trend: el trend usa la primaria
        para que su serie reconcilie con el KPI."""
        df = additive_plus_non_additive_df
        plans = planner.build_macro_analysis_bundle(
            BROAD_PROMPT, list(df.columns),
            schema_profile=_schema(df), dataset_contract={}, candidate_df=df,
        )
        kpi_intent = plans[0].main_intent
        assert kpi_intent.metrics == ["total_linea"], "El KPI debe ser la métrica aditiva"
        trend = next(
            (p.main_intent for p in plans if isinstance(p.main_intent, TimeTrendIntent)),
            None,
        )
        assert trend is not None
        assert trend.value_column == "total_linea"
        assert trend.aggregation == "sum"
        assert "monto_documento" not in trend.value_column

    def test_all_plans_have_literal_aggregation(self, only_non_additive_df,
                                                additive_plus_non_additive_df,
                                                healthy_multi_family_df):
        for df in (only_non_additive_df, additive_plus_non_additive_df,
                   healthy_multi_family_df):
            plans = planner.build_macro_analysis_bundle(
                BROAD_PROMPT, list(df.columns),
                schema_profile=_schema(df), dataset_contract={}, candidate_df=df,
            )
            for plan in plans or []:
                agg = getattr(plan.main_intent, "aggregation", None)
                assert agg in AGGREGATION_LITERAL_VALUES, (
                    f"Agregación inválida '{agg}' en {plan.title}"
                )

    def test_no_metrics_returns_empty(self):
        df = pd.DataFrame({"cliente": ["A", "B", "C"],
                           "fecha": pd.to_datetime(["2024-01-01"] * 3)})
        plans = planner.build_macro_analysis_bundle(
            BROAD_PROMPT, list(df.columns),
            schema_profile=_schema(df), dataset_contract={}, candidate_df=df,
        )
        assert plans == []

    def test_kpi_and_distribution_share_primary_metric(self, healthy_multi_family_df):
        """Garantía de reconciliación: KPI y distribución describen la misma métrica."""
        df = healthy_multi_family_df
        plans = planner.build_macro_analysis_bundle(
            BROAD_PROMPT, list(df.columns),
            schema_profile=_schema(df), dataset_contract={}, candidate_df=df,
        )
        kpi = plans[0].main_intent
        assert isinstance(kpi, DescriptiveIntent)
        dist = next(
            (p.main_intent for p in plans if isinstance(p.main_intent, DistributionIntent)),
            None,
        )
        assert dist is not None
        assert kpi.metrics[0] == dist.metric


# ═══════════════════════════════════════════════════════════════
# Cobertura de idioma (ES / EN / PT)
# ═══════════════════════════════════════════════════════════════

class TestLanguageCoverage:
    def test_fold_latin1(self):
        from app.services.semantic_translator.temporal_resolver import _fold_accents
        assert _fold_accents("ação") == "acao"
        assert _fold_accents("Año") == "ano"
        assert _fold_accents("mês") == "mes"

    @pytest.mark.parametrize(
        "token,expected",
        [
            ("enero", 1), ("December", 12), ("setiembre", 9),
            ("março", 3), ("janeiro", 1), ("Dezembro", 12), ("fev", 2),
        ],
    )
    def test_parse_month_token_multilingual(self, token, expected):
        from app.services.semantic_translator.temporal_resolver import _parse_month_token
        assert _parse_month_token(token) == expected

    @pytest.mark.parametrize(
        "prompt,expected",
        [
            ("análisis mensual", TimeGrain.MONTH),
            ("evolución diária", TimeGrain.DAY),
            ("resumo mensal", TimeGrain.MONTH),
            ("análisis semanal", TimeGrain.WEEK),
            ("análisis general", None),
        ],
    )
    def test_detect_explicit_grain_multilingual(self, prompt, expected):
        from app.core.time_grain import detect_explicit_grain
        assert detect_explicit_grain(prompt) == expected


# ═══════════════════════════════════════════════════════════════
# 5. Firebreak de translate(): degrada, no mata
# ═══════════════════════════════════════════════════════════════

class TestTranslateFirebreak:
    def test_macro_bundle_error_degrades(self, monkeypatch):
        from app.services.semantic_translator import unified_translator

        def _boom(*_a, **_k):
            raise RuntimeError("simulated_macro_failure")

        monkeypatch.setattr(planner, "build_macro_analysis_bundle", _boom)
        monkeypatch.setattr(
            unified_translator, "unified_translate",
            lambda *a, **k: None,
        )
        monkeypatch.setattr(
            planner, "route_prompt_with_semantic_router",
            lambda *a, **k: {
                "route": "COMPLEJO", "confidence": 0.0, "detected_intent": "unknown",
                "reason_codes": [], "original_route": "COMPLEJO", "semantic_contract": {},
            },
        )
        monkeypatch.setattr(
            planner, "generate_translator_plans_with_model", lambda *a, **k: [],
        )

        # No debe lanzar: el error del macro se captura y se degrada.
        result = planner.translate(
            BROAD_PROMPT, ["monto_documento", "fecha"],
            glossary_context="", topology_context="",
            schema_profile={}, dataset_contract={},
        )
        assert result is None

    def test_simple_contract_error_degrades(self, monkeypatch):
        from app.services.semantic_translator import unified_translator

        monkeypatch.setattr(
            unified_translator, "unified_translate", lambda *a, **k: None,
        )
        monkeypatch.setattr(
            planner, "looks_broad_analysis_request", lambda *a, **k: False,
        )
        monkeypatch.setattr(
            planner, "route_prompt_with_semantic_router",
            lambda *a, **k: {
                "route": "SIMPLE", "confidence": 0.99, "detected_intent": "descriptive",
                "reason_codes": [], "original_route": "SIMPLE",
                "semantic_contract": {"intent": "descriptive", "metric": "m"},
            },
        )

        def _boom(*_a, **_k):
            raise RuntimeError("simulated_simple_failure")

        monkeypatch.setattr(planner, "build_plan_from_router_contract", _boom)
        monkeypatch.setattr(
            planner, "generate_translator_plans_with_model", lambda *a, **k: [],
        )

        result = planner.translate(
            "total de monto", ["monto", "fecha"],
            glossary_context="", topology_context="",
            schema_profile={}, dataset_contract={},
        )
        assert result is None
