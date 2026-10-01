"""P3 — Harness de propiedades multi-dominio para la capa semántica.

Garantiza, con datasets **sintéticos** de dominios distintos (no datos de un
cliente), que los invariantes de la capa semántica se cumplen siempre. El punto
es blindar la CLASE de fallo, no una instancia:

1. **Gate del macro bypass por residuo de dominio** (P0.1): un prompt que nombra
   cualquier concepto de negocio NO puede ser secuestrado por el fast-path.
2. **No aditividad estructural** (P1.4): una métrica a nivel documento
   (cabecera repetida por línea) nunca se usa como KPI aditivo sin degradar.
3. **Granularidad adaptativa** (P1.5): un trend nunca colapsa a < 2 puntos.
4. **Eje temporal sin NaT** (P1.6): los periodos nulos no son categorías.
"""

from __future__ import annotations

import os
import sys
import tempfile

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "."))

from app.core.semantic_grammar import (
    AnalysisPlan,
    DescriptiveIntent,
    TimeGrain,
    TimeTrendIntent,
    VisualProtocol,
)
from app.core.time_grain import detect_explicit_grain, estimate_periods, resolve_time_grain
from app.services.data_engine import DataEngine
from app.services.ibis_engine import IbisEngine
from app.services.semantic_translator.core import (
    extract_domain_residue,
    looks_broad_analysis_request,
)
from app.services.semantic_translator.metric_archetype import (
    compute_metric_features,
    is_non_additive,
    key_repeat_ratio,
    repeated_sum_ratio,
)
from app.services.semantic_translator.planner import build_macro_analysis_bundle
from app.services.semantic_translator.validator import finalize_plans


# ═══════════════════════════════════════════════════════════════════════
# Fixtures sintéticas (multi-dominio)
# ═══════════════════════════════════════════════════════════════════════

def _write_parquet(df: pd.DataFrame) -> str:
    tmp = tempfile.NamedTemporaryFile(suffix=".parquet", delete=False)
    pq.write_table(pa.Table.from_pandas(df), tmp.name)
    tmp.close()
    return tmp.name


def _sap_like_header_detail_df() -> pd.DataFrame:
    """Cabecera (documento) repetida por cada línea de detalle + total de línea."""
    rows = []
    for doc in range(1, 6):
        doc_amount = float(doc * 1000)
        for line in range(1, 11):
            rows.append(
                {
                    "num_documento": f"DOC-{doc:03d}",
                    "cuenta": "CTA-1",
                    "fecha": pd.Timestamp("2024-01-01") + pd.Timedelta(days=doc),
                    "monto_documento": doc_amount,
                    "total_linea": float(line * 7 + doc),
                }
            )
    return pd.DataFrame(rows)


def _monthly_single_period_df() -> pd.DataFrame:
    """Reporte de un solo mes: MONTH colapsaría a 1 punto."""
    rows = []
    for day in range(1, 21):
        rows.append(
            {
                "fecha": pd.Timestamp("2026-08-01") + pd.Timedelta(days=day - 1),
                "cuenta": "1650694006",
                "monto": float(day * 100),
            }
        )
    return pd.DataFrame(rows)


def _sap_schema_profile() -> dict:
    return {
        "num_documento": {"role": "identifier", "cardinality": 5, "cardinality_ratio": 0.1},
        "cuenta": {"role": "dimension", "cardinality": 1},
        "fecha": {"role": "date", "cardinality": 5},
        "monto_documento": {"role": "metric", "cardinality": 5},
        "total_linea": {"role": "metric", "cardinality": 50},
    }


_FLOW_CONTRACT = {
    "dataset_mode": "flow",
    "snapshot_guard_allowed": False,
    "time_axis": "fecha",
    "date_columns": ["fecha"],
    "contract_state": "confirmed_file_contract",
}


# ═══════════════════════════════════════════════════════════════════════
# 1. Gate del macro por residuo de dominio (P0.1)
# ═══════════════════════════════════════════════════════════════════════

def test_generic_prompts_stay_broad() -> None:
    for prompt in (
        "realiza un analisis general",
        "dame un resumen general",
        "analisis completo",
        "dashboard general",
        "quiero un overview global",
        # Adjetivos de audiencia/alcance: no nombran un concepto de negocio.
        "realiza un analisis gerencial",
        "resumen ejecutivo general",
    ):
        assert looks_broad_analysis_request(prompt) is True, prompt


def test_domain_prompts_are_never_hijacked_by_macro() -> None:
    for prompt in (
        "realiza un analisis sobre la forma de pago",
        "analiza las ventas de combustible",
        "gastos por proveedor",
        "analiza la morosidad por sucursal",
    ):
        assert looks_broad_analysis_request(prompt) is False, prompt


def test_domain_residue_is_empty_only_for_generic_prompts() -> None:
    assert extract_domain_residue("realiza un analisis general") == []
    assert extract_domain_residue("resumen general") == []
    residue = extract_domain_residue("realiza un analisis sobre la forma de pago")
    assert "forma" in residue and "pago" in residue


# ═══════════════════════════════════════════════════════════════════════
# 2. No aditividad estructural (P1.4)
# ═══════════════════════════════════════════════════════════════════════

def test_header_metric_is_detected_as_non_additive() -> None:
    df = _sap_like_header_detail_df()

    assert repeated_sum_ratio(df["monto_documento"]) >= 0.5
    assert repeated_sum_ratio(df["total_linea"]) <= 0.5
    assert key_repeat_ratio(df, "monto_documento", "num_documento") == 1.0


def test_is_non_additive_classification() -> None:
    df = _sap_like_header_detail_df()
    features = compute_metric_features(
        df, ["monto_documento", "total_linea"], identifier_cols=["num_documento"]
    )

    assert is_non_additive(features["monto_documento"]) is True
    assert is_non_additive(features["total_linea"]) is False


def test_macro_bundle_never_sums_a_header_metric_as_kpi() -> None:
    df = _sap_like_header_detail_df()
    plans = build_macro_analysis_bundle(
        prompt="realiza un analisis general",
        columns=list(_sap_schema_profile().keys()),
        schema_profile=_sap_schema_profile(),
        dataset_contract=dict(_FLOW_CONTRACT),
        candidate_df=df,
    )

    assert plans, "el bundle macro debe producir al menos un plan"
    kpi_plan = next(
        p for p in plans if getattr(p.main_intent, "type", None) == "descriptive"
    )
    kpi_metric = kpi_plan.main_intent.metrics[0]
    kpi_aggregation = str(getattr(kpi_plan.main_intent, "aggregation", "sum")).lower()

    # Invariante: el atributo de documento jamás se presenta como suma.
    assert not (kpi_metric == "monto_documento" and kpi_aggregation == "sum")

    coverage = getattr(kpi_plan, "coverage_metadata", None) or {}
    assert "monto_documento" in (coverage.get("non_additive_metrics") or {})


# ═══════════════════════════════════════════════════════════════════════
# 3. Granularidad adaptativa (P1.5)
# ═══════════════════════════════════════════════════════════════════════

def test_resolve_time_grain_refines_when_too_coarse() -> None:
    # Rango amplio: el grano pedido ya es viable → se respeta.
    assert resolve_time_grain(TimeGrain.MONTH, 400.0, 24) == TimeGrain.MONTH
    # Un solo mes → MONTH colapsa; se refina a SEMANA.
    assert resolve_time_grain(TimeGrain.MONTH, 20.0, 20) == TimeGrain.WEEK
    # Rango muy corto → se refina a DÍA.
    assert resolve_time_grain(TimeGrain.MONTH, 3.0, 3) == TimeGrain.DAY


def test_explicit_user_grain_is_respected() -> None:
    assert resolve_time_grain(
        TimeGrain.MONTH, 20.0, 20, prompt_explicit=TimeGrain.MONTH
    ) == TimeGrain.MONTH
    assert detect_explicit_grain("evolucion mensual del monto") == TimeGrain.MONTH
    assert detect_explicit_grain("analisis gerencial") is None


def test_estimate_periods_is_monotonic_in_grain() -> None:
    assert estimate_periods(TimeGrain.DAY, 20.0, 20) > estimate_periods(TimeGrain.WEEK, 20.0, 20)
    assert estimate_periods(TimeGrain.WEEK, 20.0, 20) > estimate_periods(TimeGrain.MONTH, 20.0, 20)


def test_finalize_plans_adapts_grain_from_contract_span() -> None:
    plan = AnalysisPlan(
        title="Evolución de monto",
        main_intent=TimeTrendIntent(
            type="trend",
            date_column="fecha",
            value_column="monto",
            grain=TimeGrain.MONTH,
            rationale="Evolución.",
        ),
    )
    schema_profile = {"fecha": {"role": "date", "cardinality": 20}}
    contract = {
        "time_axis": "fecha",
        "evidence": {"min_date": "2026-08-01", "max_date": "2026-08-21"},
    }

    finalized = finalize_plans(
        [plan], schema_profile, dataset_contract=contract, prompt="analisis general"
    )

    assert len(finalized) == 1
    assert finalized[0].main_intent.grain == TimeGrain.WEEK


def test_finalize_plans_drops_single_period_trend() -> None:
    plan = AnalysisPlan(
        title="Evolución de monto",
        main_intent=TimeTrendIntent(
            type="trend",
            date_column="fecha",
            value_column="monto",
            grain=TimeGrain.MONTH,
            rationale="Evolución.",
        ),
    )
    schema_profile = {"fecha": {"role": "date", "cardinality": 1}}

    finalized = finalize_plans([plan], schema_profile)

    assert finalized == []


# ═══════════════════════════════════════════════════════════════════════
# 4. Eje temporal sin NaT + doble cerrojo en ejecución (P1.5/P1.6)
# ═══════════════════════════════════════════════════════════════════════

def test_format_period_label_never_returns_nat() -> None:
    assert IbisEngine._format_period_label(pd.NaT, TimeGrain.MONTH) == "Sin fecha"
    assert IbisEngine._format_period_label(None, TimeGrain.DAY) == "Sin fecha"


def test_trend_execution_adapts_grain_and_excludes_nat(monkeypatch) -> None:
    df = _monthly_single_period_df()
    df = pd.concat(
        [df, pd.DataFrame([{"fecha": pd.NaT, "cuenta": "1650694006", "monto": 5.0}])],
        ignore_index=True,
    )
    parquet_path = _write_parquet(df)
    monkeypatch.setattr(DataEngine, "load_semantic_contract", lambda _p: dict(_FLOW_CONTRACT))

    plan = AnalysisPlan(
        title="Evolución de monto",
        main_intent=TimeTrendIntent(
            type="trend",
            date_column="fecha",
            value_column="monto",
            grain=TimeGrain.MONTH,
            rationale="Evolución.",
        ),
    )
    result = IbisEngine.execute_plan(parquet_path, plan)

    assert "error" not in result
    names = [point["name"] for point in result["data"]]
    assert "NaT" not in names and "Sin fecha" not in names
    # Doble cerrojo: MONTH (1 punto) → adaptado a un grano con >= 2 puntos.
    assert result["hard_facts"]["total_periods"] >= 2
