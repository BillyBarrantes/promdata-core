"""Tests de la capa semántica — Lote #5 (combo/dual-axis) + #4 (acumulados).

Blindan dos mejoras estructurales domain-agnostic:

#5 — Segunda métrica en un trend para gráfico combinado:
    `TimeTrendIntent.secondary_value_column` (opcional, fail-closed). Si la
    columna no existe o no es numérica, el trend queda de una sola serie. La
    ruta unified (que NO pasa por el sanitizer) se protege en `finalize_plans`
    (choque point) y en el engine.

#4 — Métricas acumuladas (running totals):
    Una serie ya acumulada NO se suma por período. Dos señales fail-closed
    (monotonía temporal AND léxico genérico de acumulación) corrigen la
    agregación a max/min (último valor). Además, la frontera de agregación
    ahora honra `max`/`min` (antes caían silenciosamente a `sum`).
"""

from __future__ import annotations

import inspect

import ibis
import pandas as pd
import pytest

from app.core.semantic_grammar import (
    AnalysisPlan,
    DescriptiveIntent,
    DistributionIntent,
    TimeGrain,
    TimeTrendIntent,
    VisualProtocol,
)
from app.services.canonical_tabular_canary_executor import _build_chart_option
from app.services.canonical_tabular_production_executor import (
    _apply_cumulative_metric_guard,
)
from app.services.ibis_engine import IbisEngine
from app.services.semantic_translator import unified_translator
from app.services.semantic_translator.metric_archetype import (
    build_metric_coverage_metadata,
    compute_metric_features,
    cumulative_aggregation,
    has_accumulation_lexicon,
    is_cumulative_metric,
    monotonic_direction,
)
from app.services.semantic_translator.validator import (
    _plan_covered_concepts,
    finalize_plans,
    sanitize_translator_payload_item,
)


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════

def _trend_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "fecha": pd.date_range("2021-01-01", periods=5, freq="MS"),
            "ventas": [3.0, 9.0, 2.0, 8.0, 4.0],
            "saldo_acumulado": [10.0, 20.0, 30.0, 40.0, 50.0],
        }
    )


def _trend_plan(**intent_kwargs) -> AnalysisPlan:
    base = dict(
        rationale="test",
        date_column="fecha",
        value_column="ventas",
        grain=TimeGrain.MONTH,
        visual_protocol=VisualProtocol.LINE,
    )
    base.update(intent_kwargs)
    return AnalysisPlan(
        main_intent=TimeTrendIntent(**base),
        title="Evolución",
        column_aliases={"ventas": "Ventas", "saldo_acumulado": "Saldo", "fecha": "Fecha"},
    )


# ═══════════════════════════════════════════════════════════════
# #5 — Combo / segunda métrica
# ═══════════════════════════════════════════════════════════════

class TestSecondaryValueColumnSchema:
    def test_field_accepts_value_and_defaults_none(self) -> None:
        with_secondary = TimeTrendIntent(
            rationale="x", date_column="fecha", value_column="ventas",
            secondary_value_column="saldo_acumulado",
        )
        without = TimeTrendIntent(rationale="x", date_column="fecha", value_column="ventas")
        assert with_secondary.secondary_value_column == "saldo_acumulado"
        assert without.secondary_value_column is None

    def test_unknown_field_is_ignored_not_error(self) -> None:
        # Pydantic v2 (extra=ignore): payloads cacheados viejos no rompen.
        intent = TimeTrendIntent(
            rationale="x", date_column="fecha", value_column="ventas",
            campo_inexistente="z",
        )
        assert intent.secondary_value_column is None


class TestSecondarySanitizer:
    def test_hallucinated_secondary_is_nulled(self) -> None:
        item = {
            "main_intent": {
                "type": "trend", "rationale": "x", "date_column": "fecha",
                "value_column": "ventas", "secondary_value_column": "no_existe",
            },
            "title": "t",
            "column_aliases": {},
        }
        sanitized = sanitize_translator_payload_item(item, ["fecha", "ventas"], "multi")
        assert sanitized["main_intent"]["secondary_value_column"] is None

    def test_valid_secondary_is_kept(self) -> None:
        item = {
            "main_intent": {
                "type": "trend", "rationale": "x", "date_column": "fecha",
                "value_column": "ventas", "secondary_value_column": "saldo_acumulado",
            },
            "title": "t",
            "column_aliases": {},
        }
        sanitized = sanitize_translator_payload_item(
            item, ["fecha", "ventas", "saldo_acumulado"], "multi"
        )
        assert sanitized["main_intent"]["secondary_value_column"] == "saldo_acumulado"


class TestFinalizePlansNeutralization:
    def test_hallucinated_secondary_is_neutralized(self) -> None:
        plan = _trend_plan(secondary_value_column="col_alucinada")
        out = finalize_plans([plan], {"fecha": {}, "ventas": {}, "saldo_acumulado": {}})
        assert out[0].main_intent.secondary_value_column is None

    def test_valid_secondary_survives(self) -> None:
        plan = _trend_plan(secondary_value_column="saldo_acumulado")
        out = finalize_plans([plan], {"fecha": {}, "ventas": {}, "saldo_acumulado": {}})
        assert out[0].main_intent.secondary_value_column == "saldo_acumulado"

    def test_empty_schema_profile_does_not_neutralize(self) -> None:
        # Sin columnas conocidas no se puede validar: no se toca (el engine
        # vuelve a aplicar su guard).
        plan = _trend_plan(secondary_value_column="cualquiera")
        out = finalize_plans([plan], {})
        assert out[0].main_intent.secondary_value_column == "cualquiera"

    def test_covered_concepts_include_secondary(self) -> None:
        plan = _trend_plan(secondary_value_column="saldo_acumulado")
        assert "saldo_acumulado" in _plan_covered_concepts(plan)


class TestTrendSecondaryExecution:
    def _table(self):
        return ibis.memtable(_trend_df())

    def test_secondary_value_emitted_per_period(self) -> None:
        intent = TimeTrendIntent(
            rationale="x", date_column="fecha", value_column="ventas",
            secondary_value_column="saldo_acumulado", grain=TimeGrain.MONTH,
            visual_protocol=VisualProtocol.LINE,
        )
        out = IbisEngine._analyze_trend(self._table(), intent)
        values = [row["extra_info"].get("secondary_value") for row in out["data"]]
        assert values == [10.0, 20.0, 30.0, 40.0, 50.0]

    def test_single_metric_trend_unchanged(self) -> None:
        intent = TimeTrendIntent(
            rationale="x", date_column="fecha", value_column="ventas",
            grain=TimeGrain.MONTH, visual_protocol=VisualProtocol.LINE,
        )
        out = IbisEngine._analyze_trend(self._table(), intent)
        assert all("secondary_value" not in row["extra_info"] for row in out["data"])

    def test_hallucinated_secondary_is_ignored_fail_closed(self) -> None:
        intent = TimeTrendIntent(
            rationale="x", date_column="fecha", value_column="ventas",
            secondary_value_column="no_existe", grain=TimeGrain.MONTH,
            visual_protocol=VisualProtocol.LINE,
        )
        out = IbisEngine._analyze_trend(self._table(), intent)
        assert "error" not in out
        assert all("secondary_value" not in row["extra_info"] for row in out["data"])

    def test_secondary_aggregation_does_not_inherit_primary(self) -> None:
        # Primaria con aggregation='max'; la 2ª métrica (aditiva) debe seguir sum.
        intent_max = TimeTrendIntent(
            rationale="x", date_column="fecha", value_column="saldo_acumulado",
            aggregation="max",
        )
        assert IbisEngine._metric_aggregation_method(intent_max, "saldo_acumulado") == "max"
        assert (
            IbisEngine._metric_aggregation_method(
                intent_max, "ventas", ignore_intent_aggregation=True
            )
            == "sum"
        )


class TestComboGovernance:
    def test_canary_promotes_secondary_with_real_name(self) -> None:
        intent = TimeTrendIntent(
            rationale="x", date_column="fecha", value_column="ventas",
            secondary_value_column="saldo_acumulado", grain=TimeGrain.MONTH,
            visual_protocol=VisualProtocol.DUAL_AXIS,
        )
        out = IbisEngine._analyze_trend(ibis.memtable(_trend_df()), intent)
        plan = AnalysisPlan(
            main_intent=intent, title="Ventas vs Saldo",
            column_aliases={"ventas": "Ventas", "saldo_acumulado": "Saldo"},
        )
        option = _build_chart_option(
            plan=plan, title="Ventas vs Saldo", result_payload=out,
            currency_meta={},
            schema_profile={
                "fecha": {"role": "date"},
                "ventas": {"role": "metric"},
                "saldo_acumulado": {"role": "metric"},
            },
        )
        assert option is not None
        series = [(s.get("name"), s.get("type")) for s in option.get("series", [])]
        assert series == [("Ventas", "bar"), ("Saldo Acumulado", "line")]

    def test_line_trend_with_secondary_is_promoted_to_combo(self) -> None:
        intent = TimeTrendIntent(
            rationale="x", date_column="fecha", value_column="ventas",
            secondary_value_column="saldo_acumulado", grain=TimeGrain.MONTH,
            visual_protocol=VisualProtocol.LINE,
        )
        out = IbisEngine._analyze_trend(ibis.memtable(_trend_df()), intent)
        plan = AnalysisPlan(
            main_intent=intent, title="Ventas y Saldo",
            column_aliases={"ventas": "Ventas", "saldo_acumulado": "Saldo"},
        )
        option = _build_chart_option(
            plan=plan, title="Ventas y Saldo", result_payload=out,
            currency_meta={},
            schema_profile={
                "fecha": {"role": "date"},
                "ventas": {"role": "metric"},
                "saldo_acumulado": {"role": "metric"},
            },
        )
        assert option is not None
        assert len(option.get("series", [])) == 2


class TestUnifiedPromptContract:
    def test_instruction_teaches_secondary_value_column(self) -> None:
        instruction = unified_translator.get_unified_static_instruction()
        assert "secondary_value_column" in instruction

    def test_cache_version_bumped(self) -> None:
        source = inspect.getsource(unified_translator)
        assert '"unified_contract_version": "v4"' in source


# ═══════════════════════════════════════════════════════════════
# #4 — Métricas acumuladas
# ═══════════════════════════════════════════════════════════════

class TestMonotonicDirection:
    def test_increasing_and_decreasing(self) -> None:
        assert monotonic_direction(pd.Series([10.0, 20.0, 30.0, 40.0, 50.0])) == "inc"
        assert monotonic_direction(pd.Series([50.0, 40.0, 30.0, 20.0, 10.0])) == "dec"

    def test_noisy_is_not_monotonic(self) -> None:
        assert monotonic_direction(pd.Series([10.0, 25.0, 20.0, 40.0, 35.0])) is None

    def test_constant_is_ambiguous(self) -> None:
        assert monotonic_direction(pd.Series([5.0, 5.0, 5.0, 5.0])) is None

    def test_too_few_points(self) -> None:
        assert monotonic_direction(pd.Series([1.0, 2.0])) is None


class TestAccumulationLexicon:
    @pytest.mark.parametrize(
        "text",
        ["saldo_acumulado", "ACUMULADA", "running_total", "ytd", "year to date",
         "total corrido", "cumulativo", "cumulative"],
    )
    def test_positive_terms(self, text: str) -> None:
        assert has_accumulation_lexicon(text) is True

    @pytest.mark.parametrize("text", ["ventas", "precio", "cantidad", "ingresos"])
    def test_negative_terms(self, text: str) -> None:
        assert has_accumulation_lexicon(text) is False

    def test_prompt_also_counts(self) -> None:
        assert has_accumulation_lexicon("ventas", "evolución del total acumulado") is True


class TestIsCumulativeMetric:
    def _features(self) -> dict:
        return compute_metric_features(
            _trend_df(), ["saldo_acumulado", "ventas"], time_col="fecha"
        )

    def test_both_signals_required(self) -> None:
        features = self._features()
        # Ambas señales (monotonía + léxico) → True.
        assert is_cumulative_metric(features["saldo_acumulado"], "saldo_acumulado") is True
        # Monotonía sin léxico → False (fail-closed).
        assert is_cumulative_metric(features["saldo_acumulado"], "x_ac") is False
        # Léxico sin monotonía → False.
        assert is_cumulative_metric(
            {"cumulative_monotonic_direction": None}, "saldo_acumulado"
        ) is False

    def test_structural_direction_stored(self) -> None:
        features = self._features()
        assert features["saldo_acumulado"]["cumulative_monotonic_direction"] == "inc"
        assert features["ventas"]["cumulative_monotonic_direction"] is None

    def test_cumulative_aggregation_mapping(self) -> None:
        assert cumulative_aggregation("inc") == "max"
        assert cumulative_aggregation("dec") == "min"
        assert cumulative_aggregation(None) is None

    def test_coverage_metadata_reports_cumulative(self) -> None:
        meta = build_metric_coverage_metadata(
            ["saldo_acumulado"], ["saldo_acumulado", "ventas"], {},
            cumulative_metrics={"saldo_acumulado": {"cumulative_monotonic_direction": "inc"}},
        )
        assert "saldo_acumulado" in meta["cumulative_metrics"]


class TestAggregationFrontier:
    def test_max_min_are_honored(self) -> None:
        class _Max:
            aggregation = "max"
            metric_unit = None

        class _Min:
            aggregation = "min"
            metric_unit = None

        assert IbisEngine._metric_aggregation_method(_Max(), "ventas") == "max"
        assert IbisEngine._metric_aggregation_method(_Min(), "ventas") == "min"

    def test_max_reaches_engine(self) -> None:
        intent = TimeTrendIntent(
            rationale="x", date_column="fecha", value_column="saldo_acumulado",
            aggregation="max", grain=TimeGrain.MONTH, visual_protocol=VisualProtocol.LINE,
        )
        out = IbisEngine._analyze_trend(ibis.memtable(_trend_df()), intent)
        assert [row["value"] for row in out["data"]] == [10.0, 20.0, 30.0, 40.0, 50.0]


class TestCumulativeGuard:
    def _df(self) -> pd.DataFrame:
        df = _trend_df()
        df.attrs["dataset_contract"] = {"time_axis": "fecha"}
        df.attrs["schema_profile"] = {
            "fecha": {"role": "date"},
            "saldo_acumulado": {"role": "metric"},
            "ventas": {"role": "metric"},
        }
        return df

    def _plan(self, metric: str, aggregation: str = "sum") -> AnalysisPlan:
        return AnalysisPlan(
            main_intent=DescriptiveIntent(
                rationale="x", metrics=[metric], aggregation=aggregation,
                visual_protocol=VisualProtocol.KPI,
            ),
            title=f"{metric} Total",
            column_aliases={},
        )

    def test_accumulated_flips_sum_to_max(self) -> None:
        plan = self._plan("saldo_acumulado")
        _apply_cumulative_metric_guard([plan], self._df(), "evolución del saldo acumulado")
        assert plan.main_intent.aggregation == "max"
        assert "serie acumulada" in plan.title

    def test_additive_metric_unchanged(self) -> None:
        plan = self._plan("ventas")
        _apply_cumulative_metric_guard([plan], self._df(), "evolución de ventas")
        assert plan.main_intent.aggregation == "sum"

    def test_structural_only_is_not_changed(self) -> None:
        # Monótona pero sin léxico de acumulación (ni en nombre ni en prompt)
        # → no se toca (fail-closed).
        df = _trend_df()
        df["monto_creciente"] = [1.0, 2.0, 3.0, 4.0, 5.0]
        df.attrs["dataset_contract"] = {"time_axis": "fecha"}
        df.attrs["schema_profile"] = {"fecha": {"role": "date"}}
        plan = self._plan("monto_creciente")
        _apply_cumulative_metric_guard([plan], df, "evolución del monto")
        assert plan.main_intent.aggregation == "sum"

    def test_explicit_avg_is_respected(self) -> None:
        plan = self._plan("saldo_acumulado", aggregation="avg")
        _apply_cumulative_metric_guard([plan], self._df(), "saldo acumulado")
        assert plan.main_intent.aggregation == "avg"

    def test_non_lexicon_metric_not_changed(self) -> None:
        plan = self._plan("ventas")
        _apply_cumulative_metric_guard([plan], self._df(), "acumulado de ventas")
        assert plan.main_intent.aggregation == "sum"

    def test_distribution_intent_metric_is_guarded(self) -> None:
        plan = AnalysisPlan(
            main_intent=DistributionIntent(
                rationale="x", dimension="fecha", metric="saldo_acumulado",
                aggregation="sum", visual_protocol=VisualProtocol.BAR,
            ),
            title="Saldo por mes",
            column_aliases={},
        )
        _apply_cumulative_metric_guard([plan], self._df(), "saldo acumulado por mes")
        assert plan.main_intent.aggregation == "max"

    def test_empty_dataframe_is_noop(self) -> None:
        plan = self._plan("saldo_acumulado")
        _apply_cumulative_metric_guard([plan], pd.DataFrame(), "saldo acumulado")
        assert plan.main_intent.aggregation == "sum"
