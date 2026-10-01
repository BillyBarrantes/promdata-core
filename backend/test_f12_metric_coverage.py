import math
import sys
from typing import Any

import numpy as np
import pandas as pd
import pytest

from app.services.semantic_translator.metric_archetype import (
    _DIVERSITY_THRESHOLD,
    _lexical_diversity,
    _score_prompt_relevance,
    build_metric_coverage_metadata,
    classify_metric_families,
    compute_metric_features,
    select_metrics_for_broad_analysis,
)
from app.services.semantic_translator.validator import infer_default_metric_column


# ═══════════════════════════════════════════════════════════════
# Unit Tests — Feature Extraction
# ═══════════════════════════════════════════════════════════════

def _df(cols: dict[str, list[float]]) -> pd.DataFrame:
    return pd.DataFrame(cols)


class TestComputeMetricFeatures:
    def test_ratio_0_1_detected(self):
        df = _df({"margen": [0.1, 0.5, 0.9, 0.3]})
        feat = compute_metric_features(df, ["margen"])
        assert feat["margen"]["is_ratio"] is True

    def test_ratio_0_100_detected(self):
        df = _df({"porcentaje": [10.0, 50.0, 90.0, 30.0]})
        feat = compute_metric_features(df, ["porcentaje"])
        assert feat["porcentaje"]["is_ratio"] is True

    def test_large_abs_not_ratio(self):
        df = _df({"ingresos": [1000.0, 5000.0, 20000.0, 8000.0]})
        feat = compute_metric_features(df, ["ingresos"])
        assert feat["ingresos"]["is_ratio"] is False

    def test_negative_detected(self):
        df = _df({"saldo": [-500.0, 0.0, 1000.0, -200.0]})
        feat = compute_metric_features(df, ["saldo"])
        assert feat["saldo"]["is_non_negative"] is False

    def test_non_negative_detected(self):
        df = _df({"ventas": [0.0, 100.0, 500.0, 200.0]})
        feat = compute_metric_features(df, ["ventas"])
        assert feat["ventas"]["is_non_negative"] is True

    def test_log_scale_large(self):
        df = _df({"big": [1e3, 1e6, 5e6, 2e5]})
        feat = compute_metric_features(df, ["big"])
        assert feat["big"]["log_scale"] == 6

    def test_log_scale_small(self):
        df = _df({"small": [1.0, 10.0, 50.0, 25.0]})
        feat = compute_metric_features(df, ["small"])
        assert feat["small"]["log_scale"] == 1

    def test_cv_low(self):
        df = _df({"stable": [100.0, 105.0, 95.0, 100.0]})
        feat = compute_metric_features(df, ["stable"])
        assert feat["stable"]["cv_tier"] == "LOW"

    def test_cv_medium(self):
        df = _df({"volatile": [100.0, 0.0, 300.0, 50.0]})
        feat = compute_metric_features(df, ["volatile"])
        assert feat["volatile"]["cv_tier"] == "MEDIUM"

    def test_cv_high(self):
        df = _df({"wild": [100.0, -200.0, 500.0, -50.0]})
        feat = compute_metric_features(df, ["wild"])
        assert feat["wild"]["cv_tier"] == "HIGH"

    def test_binary_columns(self):
        df = _df({"a": [0, 1, 0, 1], "b": [1, 0, 1, 0]})
        feat = compute_metric_features(df, ["a", "b"])
        for col in ("a", "b"):
            assert feat[col]["is_ratio"] is True
            assert feat[col]["log_scale"] == 0


# ═══════════════════════════════════════════════════════════════
# Unit Tests — Classification
# ═══════════════════════════════════════════════════════════════

class TestClassifyMetricFamilies:
    def test_two_abs_one_ratio(self):
        df = _df({"ingresos": [1000, 2000, 1500],
                  "gastos": [500, 800, 600],
                  "margen": [0.1, 0.2, 0.15]})
        feat = compute_metric_features(df, ["ingresos", "gastos", "margen"])
        families = classify_metric_families(feat)
        assert len(families) == 2, f"Expected 2 families, got {len(families)}: {families}"

    def test_all_same_family(self):
        df = _df({"a": [100, 200, 150],
                  "b": [300, 400, 350],
                  "c": [50, 100, 75]})
        feat = compute_metric_features(df, ["a", "b", "c"])
        families = classify_metric_families(feat)
        assert len(families) == 1

    def test_single_column(self):
        df = _df({"unico": [100, 200, 150]})
        feat = compute_metric_features(df, ["unico"])
        families = classify_metric_families(feat)
        assert len(families) == 1
        assert "unico" in list(families.values())[0]

    def test_negative_vs_non_negative(self):
        df = _df({"saldo": [-500, 0, 100],
                  "ventas": [0, 100, 200]})
        feat = compute_metric_features(df, ["saldo", "ventas"])
        families = classify_metric_families(feat)
        assert len(families) >= 2

    def test_scale_gap_separates(self):
        df = _df({"millones": [1e6, 2e6, 1.5e6],
                  "unidades": [50, 100, 75]})
        feat = compute_metric_features(df, ["millones", "unidades"])
        families = classify_metric_families(feat)
        assert len(families) >= 2

    def test_binary_all_same(self):
        df = _df({"a": [0, 1, 0], "b": [1, 0, 1],
                  "c": [0, 0, 1], "d": [1, 1, 0]})
        feat = compute_metric_features(df, ["a", "b", "c", "d"])
        families = classify_metric_families(feat)
        assert len(families) == 1


# ═══════════════════════════════════════════════════════════════
# Unit Tests — Selection
# ═══════════════════════════════════════════════════════════════

class TestSelectMetricsForBroad:
    def test_selects_from_each_family(self):
        df = _df({"ingresos": [1000, 2000, 1500],
                  "gastos": [500, 800, 600],
                  "margen": [0.1, 0.2, 0.15]})
        feat = compute_metric_features(df, ["ingresos", "gastos", "margen"])
        families = classify_metric_families(feat)
        selected = select_metrics_for_broad_analysis(
            ["ingresos", "gastos", "margen"], feat, families, "dame un analisis completo"
        )
        assert len(selected) >= 2, f"Expected at least 2, got {selected}"

    def test_single_family_returns_one(self):
        df = _df({"a": [100, 200], "b": [300, 400]})
        feat = compute_metric_features(df, ["a", "b"])
        families = classify_metric_families(feat)
        selected = select_metrics_for_broad_analysis(
            ["a", "b"], feat, families, "analisis general"
        )
        assert len(selected) == 2

    def test_empty_metric_list(self):
        selected = select_metrics_for_broad_analysis([], {}, {}, "")
        assert selected == []

    def test_max_metrics_respected(self):
        df = _df({
            "r": [0.1, 0.2, 0.3],
            "a1": [100, 200, 300],
            "a2": [1000, 2000, 3000],
            "n": [-50, 0, 50],
        })
        feat = compute_metric_features(df, ["r", "a1", "a2", "n"])
        families = classify_metric_families(feat)
        selected = select_metrics_for_broad_analysis(
            ["r", "a1", "a2", "n"], feat, families, "completo", max_metrics=3
        )
        assert len(selected) <= 3

    def test_prompt_relevance_scoring(self):
        score_a = _score_prompt_relevance("ingresos", "ingresos")
        score_b = _score_prompt_relevance("gastos", "ingresos")
        assert score_a > score_b, "Exact match should score higher"


# ═══════════════════════════════════════════════════════════════
# Unit Tests — Coverage Metadata
# ═══════════════════════════════════════════════════════════════

class TestBuildMetricCoverageMetadata:
    def test_basic_coverage(self):
        families = {"f1": ["ingresos", "gastos"], "f2": ["margen"]}
        meta = build_metric_coverage_metadata(
            ["ingresos", "margen"], ["ingresos", "gastos", "margen"], families
        )
        assert meta["families_found"] == 2
        assert meta["families_covered"] == 2
        assert meta["uncovered_columns"] == ["gastos"]

    def test_partial_coverage(self):
        families = {"f1": ["a", "b"], "f2": ["c"], "f3": ["d"]}
        meta = build_metric_coverage_metadata(
            ["a", "c"], ["a", "b", "c", "d"], families
        )
        assert meta["families_found"] == 3
        assert meta["families_covered"] == 2
        assert sorted(meta["uncovered_columns"]) == ["b", "d"]

    def test_no_metrics(self):
        meta = build_metric_coverage_metadata([], [], {})
        assert meta["families_found"] == 0
        assert meta["metrics_selected"] == []

    def test_single_family_full_coverage(self):
        families = {"f1": ["a", "b", "c"]}
        meta = build_metric_coverage_metadata(["a"], ["a", "b", "c"], families)
        assert meta["families_found"] == 1
        assert meta["families_covered"] == 1
        assert sorted(meta["uncovered_columns"]) == ["b", "c"]


# ═══════════════════════════════════════════════════════════════
# Contract Tests
# ═══════════════════════════════════════════════════════════════

class TestContract:
    def test_infer_default_metric_unchanged(self):
        df = _df({"ingresos": [100, 200], "gastos": [50, 80]})
        schema = {"ingresos": {"role": "metric"}, "gastos": {"role": "metric"}}
        result = infer_default_metric_column("ingresos", ["ingresos", "gastos"], schema)
        assert result == "ingresos"

    def test_infer_default_metric_with_keywords(self):
        schema = {"cantidad_vendida": {"role": "metric"}, "margen": {"role": "metric"}}
        result = infer_default_metric_column("ventas", ["cantidad_vendida", "margen"], schema)
        assert result == "cantidad_vendida"

    def test_coverage_metadata_shape(self):
        df = _df({"ingresos": [1000, 2000], "margen": [0.1, 0.2]})
        feat = compute_metric_features(df, ["ingresos", "margen"])
        families = classify_metric_families(feat)
        selected = select_metrics_for_broad_analysis(
            ["ingresos", "margen"], feat, families, "completo"
        )
        meta = build_metric_coverage_metadata(selected, ["ingresos", "margen"], families)
        assert "families_found" in meta
        assert "families_covered" in meta
        assert "metrics_selected" in meta
        assert "uncovered_columns" in meta
        assert isinstance(meta["families_found"], int)
        assert isinstance(meta["metrics_selected"], list)


# ═══════════════════════════════════════════════════════════════
# Adversarial Tests
# ═══════════════════════════════════════════════════════════════

class TestAdversarial:
    def test_ten_binary_columns_one_family(self):
        data = {f"col_{i}": [0, 1] * 5 for i in range(10)}
        df = _df(data)
        feat = compute_metric_features(df, list(data.keys()))
        families = classify_metric_families(feat)
        assert len(families) == 1

    def test_extreme_scale_separation(self):
        df = _df({"big": [1e6, 2e6], "small": [50, 100]})
        feat = compute_metric_features(df, ["big", "small"])
        families = classify_metric_families(feat)
        assert len(families) >= 2

    def test_all_negative_vs_positive(self):
        df = _df({"neg": [-500, -300], "pos": [100, 200]})
        feat = compute_metric_features(df, ["neg", "pos"])
        families = classify_metric_families(feat)
        assert len(families) >= 2

    def test_sparse_zeros_same_family(self):
        n = 200
        vals_a = [0.0] * 190 + [100.0] * 10
        vals_b = [0.0] * 10 + [100.0] * 190
        df = _df({"sparse_a": vals_a, "sparse_b": vals_b})
        feat = compute_metric_features(df, ["sparse_a", "sparse_b"])
        families = classify_metric_families(feat)
        assert len(families) == 1

    def test_mix_ratio_abs_negative(self):
        df = _df({
            "ratio": [0.1, 0.5, 0.9],
            "abs": [1000, 2000, 1500],
            "neg": [-100, 0, 50],
        })
        feat = compute_metric_features(df, ["ratio", "abs", "neg"])
        families = classify_metric_families(feat)
        assert len(families) >= 3


# ═══════════════════════════════════════════════════════════════
# E2E: build_macro_analysis_bundle produces multi-metric plans
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def multi_family_df() -> pd.DataFrame:
    return pd.DataFrame({
        "ingresos": [1000, 2000, 1500, 1800, 2200],
        "gastos": [500, 800, 600, 700, 900],
        "margen_pct": [0.10, 0.15, 0.12, 0.11, 0.13],
        "fecha": pd.to_datetime(["2024-01-01", "2024-02-01", "2024-03-01",
                                 "2024-04-01", "2024-05-01"]),
        "categoria": ["A", "B", "A", "B", "A"],
    })


@pytest.fixture
def mono_family_df() -> pd.DataFrame:
    return pd.DataFrame({
        "precio": [100, 200, 150, 180, 220],
        "cantidad": [10, 20, 15, 18, 22],
        "fecha": pd.to_datetime(["2024-01-01", "2024-02-01", "2024-03-01",
                                 "2024-04-01", "2024-05-01"]),
        "producto": ["X", "Y", "X", "Y", "X"],
    })


@pytest.fixture
def no_metrics_df() -> pd.DataFrame:
    return pd.DataFrame({
        "cliente": ["A", "B", "C", "D", "E"],
        "ciudad": ["MX", "BO", "MX", "AR", "BO"],
        "fecha": pd.to_datetime(["2024-01-01"] * 5),
    })


def _schema_from_df(df: pd.DataFrame) -> dict[str, dict[str, Any]]:
    schema: dict[str, dict[str, Any]] = {}
    for col in df.columns:
        if col == "fecha":
            schema[col] = {"role": "date", "type": "temporal", "cardinality": df[col].nunique(), "cardinality_ratio": 0.1}
        elif col == "categoria" or col == "producto" or col == "ciudad":
            schema[col] = {"role": "dimension", "type": "categorical", "cardinality": df[col].nunique(), "cardinality_ratio": 0.1}
        elif col == "cliente":
            schema[col] = {"role": "identifier", "type": "id", "cardinality": df[col].nunique(), "cardinality_ratio": 0.1}
        else:
            schema[col] = {"role": "metric", "type": "numeric", "cardinality": df[col].nunique(), "cardinality_ratio": 0.1}
    return schema


class TestE2EMacroBundle:
    def test_multi_family_selects_multiple_metrics(self, multi_family_df):
        from app.services.semantic_translator.planner import build_macro_analysis_bundle
        df = multi_family_df
        cols = list(df.columns)
        schema = _schema_from_df(df)
        plans = build_macro_analysis_bundle(
            "dame un analisis completo",
            cols,
            schema_profile=schema,
            dataset_contract={},
            candidate_df=df,
        )
        assert plans is not None
        assert len(plans) >= 2
        coverage = plans[0].coverage_metadata
        assert coverage is not None
        assert coverage["families_found"] >= 2
        assert coverage["families_covered"] >= 2

    def test_mono_family_single_metric(self, mono_family_df):
        from app.services.semantic_translator.planner import build_macro_analysis_bundle
        df = mono_family_df
        cols = list(df.columns)
        schema = _schema_from_df(df)
        plans = build_macro_analysis_bundle(
            "analisis general",
            cols,
            schema_profile=schema,
            dataset_contract={},
            candidate_df=df,
        )
        assert plans is not None
        assert len(plans) >= 1
        coverage = plans[0].coverage_metadata
        assert coverage is not None
        assert coverage["families_found"] == 1
        assert coverage["families_covered"] == 1

    def test_no_metrics_returns_empty_list(self, no_metrics_df):
        from app.services.semantic_translator.planner import build_macro_analysis_bundle
        df = no_metrics_df
        cols = list(df.columns)
        schema = _schema_from_df(df)
        plans = build_macro_analysis_bundle(
            "dame un analisis completo",
            cols,
            schema_profile=schema,
            dataset_contract={},
            candidate_df=df,
        )
        assert plans is not None
        assert len(plans) == 0

    def test_multi_family_coverage_on_bundle(self, multi_family_df):
        from app.services.semantic_translator.planner import build_macro_analysis_bundle
        df = multi_family_df
        cols = list(df.columns)
        schema = _schema_from_df(df)
        plans = build_macro_analysis_bundle(
            "haz un analisis completo de todo",
            cols,
            schema_profile=schema,
            dataset_contract={},
            candidate_df=df,
        )
        assert plans is not None
        coverage = plans[0].coverage_metadata
        assert coverage is not None
        metrics_used = set()
        for plan in plans:
            intent = plan.main_intent
            for attr in ('metrics', 'metric', 'value_column'):
                vals = getattr(intent, attr, None) or []
                if isinstance(vals, str):
                    metrics_used.add(vals)
                elif isinstance(vals, list):
                    metrics_used.update(vals)
        assert len(metrics_used) >= 2, (
            f"Expected at least 2 distinct metrics, got {metrics_used}"
        )


# ═══════════════════════════════════════════════════════════════
# Regression Tests — Single-Family Metric Coverage
# ═══════════════════════════════════════════════════════════════


class TestSingleFamilyCoverage:
    """Cuando todas las métricas caen en la misma familia estadística,
    select_metrics_for_broad_analysis debe retornar múltiples métricas
    ordenadas por relevancia al prompt, no solo 1."""

    def test_currency_prompt_selects_both_dollar_metrics(self):
        """Prompt 'dólares' con gastos_en_dolares y ahorro_acumulado_en_dolares
        debe seleccionar ambas métricas."""
        metric_cols = [
            "gastos_en_pesos_chilenos",
            "gastos_en_dolares",
            "ahorro_acumulado_en_pesos",
            "ahorro_acumulado_en_dolares",
        ]
        features = {
            col: {
                "is_ratio": False,
                "is_non_negative": True,
                "log_scale": 5,
                "cv_tier": "MEDIUM",
                "zero_ratio": 0.0,
                "mean": 100000.0,
                "std": 50000.0,
                "min": 10000.0,
                "max": 500000.0,
            }
            for col in metric_cols
        }

        families = classify_metric_families(features)
        assert len(families) == 1, "Todas las métricas deben estar en la misma familia"

        surface_prompt = "dame un análisis sobre los datos que tenemos en dólares"
        selected = select_metrics_for_broad_analysis(
            metric_cols, features, families, surface_prompt, max_metrics=3
        )

        assert "gastos_en_dolares" in selected, (
            f"gastos_en_dolares debe ser seleccionada, got {selected}"
        )
        assert "ahorro_acumulado_en_dolares" in selected, (
            f"ahorro_acumulado_en_dolares debe ser seleccionada, got {selected}"
        )
        assert len(selected) <= 3, f"No debe exceder max_metrics=3, got {len(selected)}"

    def test_currency_prompt_single_family_respects_max_metrics(self):
        """Single-family con 4 métricas y max_metrics=2 retorna exactamente 2."""
        metric_cols = [
            "gastos_en_pesos_chilenos",
            "gastos_en_dolares",
            "ahorro_acumulado_en_pesos",
            "ahorro_acumulado_en_dolares",
        ]
        features = {
            col: {
                "is_ratio": False,
                "is_non_negative": True,
                "log_scale": 5,
                "cv_tier": "MEDIUM",
                "zero_ratio": 0.0,
                "mean": 100000.0,
                "std": 50000.0,
                "min": 10000.0,
                "max": 500000.0,
            }
            for col in metric_cols
        }

        families = classify_metric_families(features)
        surface_prompt = "dólares"
        selected = select_metrics_for_broad_analysis(
            metric_cols, features, families, surface_prompt, max_metrics=2
        )

        assert len(selected) == 2, f"Expected 2 metrics, got {len(selected)}: {selected}"


class TestLexicalDiversity:
    """_lexical_diversity mide distancia Jaccard entre tokens normalizados.
    1.0 = completamente diferente, 0.0 = idéntico."""

    def test_identical_columns_zero_diversity(self):
        assert _lexical_diversity("gastos_en_dolares", ["gastos_en_dolares"]) == 0.0

    def test_identical_with_underscores_zero(self):
        assert _lexical_diversity("gastos", ["gastos"]) == 0.0

    def test_completely_different_high_diversity(self):
        div = _lexical_diversity("ventas", ["cantidad"])
        assert div >= 0.8, f"Expected high diversity, got {div}"

    def test_empty_selected_returns_one(self):
        assert _lexical_diversity("cualquier_columna", []) == 1.0

    def test_empty_col_name_returns_one(self):
        assert _lexical_diversity("", ["uno"]) == 1.0

    def test_partial_overlap_medium_diversity(self):
        div = _lexical_diversity("gastos_en_dolares", ["gastos_en_dolares_mensual"])
        assert 0.2 <= div <= 0.4, f"Expected medium diversity ~0.25, got {div}"

    def test_different_tokens_high_diversity(self):
        div = _lexical_diversity("margen_bruto", ["cantidad_vendida"])
        assert div >= 0.7, f"Expected high diversity, got {div}"

    def test_multi_word_shared_root_moderate(self):
        div = _lexical_diversity("ventas_brutas", ["ventas_netas"])
        assert 0.4 <= div <= 0.8, f"Expected moderate diversity, got {div}"


class TestSingleFamilyDiversityFilter:
    """Cuando todas las métricas caen en una sola familia, el greedy filter
    debe evitar near-duplicados léxicos (ej: gastos_en_dolares vs
    gastos_en_dolares_mensual) pero permitir métricas genuinamente distintas."""

    def test_blocks_near_duplicates(self):
        metric_cols = [
            "gastos_en_dolares",
            "gastos_en_dolares_mensual",
            "gastos_en_dolares_acumulado",
        ]
        features = {
            col: {"is_ratio": False, "is_non_negative": True, "log_scale": 5,
                  "cv_tier": "MEDIUM", "zero_ratio": 0.0, "mean": 1000.0,
                  "std": 500.0, "min": 100.0, "max": 10000.0}
            for col in metric_cols
        }
        families = classify_metric_families(features)
        assert len(families) == 1

        selected = select_metrics_for_broad_analysis(
            metric_cols, features, families, "dólares", max_metrics=3
        )
        # gastos_en_dolares entra (primera, mejor relevancia).
        # Las otras dos son near-duplicadas léxicas (comparten ~75% tokens)
        # pero debe caer en fallback porque solo 1 diversa de 3 → fallback
        # completa con las mejores restantes.
        assert "gastos_en_dolares" in selected
        assert len(selected) == 3, f"Should fallback to 3, got {len(selected)}: {selected}"

    def test_diverse_metrics_all_allowed(self):
        metric_cols = [
            "ventas_totales",
            "margen_bruto",
            "cantidad_vendida",
        ]
        features = {
            col: {"is_ratio": False, "is_non_negative": True, "log_scale": 5,
                  "cv_tier": "MEDIUM", "zero_ratio": 0.0, "mean": 1000.0,
                  "std": 500.0, "min": 100.0, "max": 10000.0}
            for col in metric_cols
        }
        families = classify_metric_families(features)
        assert len(families) == 1

        selected = select_metrics_for_broad_analysis(
            metric_cols, features, families, "analisis completo", max_metrics=3
        )
        assert len(selected) == 3, f"Expected 3 diverse metrics, got {selected}"

    def test_picks_best_relevance_then_diverse(self):
        """Single-family con prompt que coincide con gastos_en_dolares
        debe seleccionar primero esa métrica, luego las demás diversas."""
        metric_cols = [
            "gastos_en_dolares",
            "cantidad_vendida",
            "margen_bruto",
        ]
        features = {
            col: {"is_ratio": False,
                  "is_non_negative": True, "log_scale": 5,
                  "cv_tier": "MEDIUM", "zero_ratio": 0.0, "mean": 1000.0,
                  "std": 500.0, "min": 100.0, "max": 10000.0}
            for col in metric_cols
        }
        families = classify_metric_families(features)
        assert len(families) == 1

        selected = select_metrics_for_broad_analysis(
            metric_cols, features, families, "gastos en dolares"
        )
        assert selected[0] == "gastos_en_dolares", (
            f"First pick should be 'gastos_en_dolares', got {selected}"
        )
        assert len(selected) >= 2

    def test_existing_tests_unchanged(self):
        """Reproduce test_single_family_returns_one exactamente."""
        df = pd.DataFrame({"a": [100, 200], "b": [300, 400]})
        feat = compute_metric_features(df, ["a", "b"])
        families = classify_metric_families(feat)
        selected = select_metrics_for_broad_analysis(
            ["a", "b"], feat, families, "analisis general"
        )
        assert len(selected) == 2


class TestMultiFamilyCoverage:
    """Cuando las métricas están en familias diferentes,
    select_metrics_for_broad_analysis debe retornar una métrica por familia."""

    def test_selects_one_per_family(self):
        """Métricas en familias diferentes deben ser seleccionadas todas
        (una por familia)."""
        metric_cols = ["gastos_en_dolares", "ahorro_acumulado_en_dolares"]
        features = {
            "gastos_en_dolares": {
                "is_ratio": False,
                "is_non_negative": True,
                "log_scale": 3,
                "cv_tier": "HIGH",
                "zero_ratio": 0.0,
                "mean": 1000.0,
                "std": 800.0,
                "min": 100.0,
                "max": 5000.0,
            },
            "ahorro_acumulado_en_dolares": {
                "is_ratio": False,
                "is_non_negative": True,
                "log_scale": 6,
                "cv_tier": "LOW",
                "zero_ratio": 0.0,
                "mean": 1000000.0,
                "std": 200000.0,
                "min": 500000.0,
                "max": 2000000.0,
            },
        }

        families = classify_metric_families(features)
        assert len(families) == 2, "Las métricas deben estar en familias diferentes"

        surface_prompt = "dame un análisis sobre los datos que tenemos en dólares"
        selected = select_metrics_for_broad_analysis(
            metric_cols, features, families, surface_prompt, max_metrics=3
        )

        assert len(selected) == 2, (
            f"Expected 2 metrics (one per family), got {len(selected)}: {selected}"
        )
        assert "gastos_en_dolares" in selected
        assert "ahorro_acumulado_en_dolares" in selected

    def test_multi_family_respects_max_metrics(self):
        """Multi-family con 3 familias y max_metrics=2 retorna exactamente 2."""
        metric_cols = ["metrica_a", "metrica_b", "metrica_c"]
        features = {
            "metrica_a": {
                "is_ratio": False, "is_non_negative": True,
                "log_scale": 3, "cv_tier": "LOW",
                "zero_ratio": 0.0, "mean": 100.0, "std": 10.0, "min": 80.0, "max": 120.0,
            },
            "metrica_b": {
                "is_ratio": False, "is_non_negative": True,
                "log_scale": 5, "cv_tier": "MEDIUM",
                "zero_ratio": 0.0, "mean": 50000.0, "std": 20000.0, "min": 10000.0, "max": 100000.0,
            },
            "metrica_c": {
                "is_ratio": False, "is_non_negative": True,
                "log_scale": 7, "cv_tier": "HIGH",
                "zero_ratio": 0.0, "mean": 5000000.0, "std": 3000000.0, "min": 1000000.0, "max": 10000000.0,
            },
        }

        families = classify_metric_families(features)
        assert len(families) >= 2

        surface_prompt = "analisis general"
        selected = select_metrics_for_broad_analysis(
            metric_cols, features, families, surface_prompt, max_metrics=2
        )

        assert len(selected) == 2, (
            f"Expected 2 metrics, got {len(selected)}: {selected}"
        )
