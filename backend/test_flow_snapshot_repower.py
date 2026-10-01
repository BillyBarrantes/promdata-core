"""
test_flow_snapshot_repower.py
═══════════════════════════════════════════════════════════════════
Regression gate for the semantic/narrative repower (4 pilares):

  Pilar 1 — Flow datasets must NOT receive the implicit 'latest' snapshot
            filter. Snapshot/hybrid must keep it (no regression).
  Pilar 3 — The 'flujos opuestos' narrative note must be gated by the real
            DirectionGuard decision, so non-financial datasets (e.g. RRHH)
            never receive a false caveat.

These are deterministic, LLM-free tests. If any future change breaks the
flow/snapshot distinction or the direction-note gate, this file turns red.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(__file__))

from app.services.semantic_translator.core import (
    build_default_latest_snapshot_filters,
    should_default_to_latest_snapshot,
)

_PROMPT = "realiza un analisis gerencial"

_RRHH_COLUMNS = [
    "id_empleado",
    "nombre_completo",
    "departamento",
    "cargo",
    "fecha_contratacion",
    "salario_mensual",
    "nivel_desempeno",
    "estado",
]

_RRHH_SCHEMA = {
    "fecha_contratacion": {"type": "temporal", "role": "date", "cardinality": 8000},
    "departamento": {"type": "categorical", "role": "dimension", "cardinality": 6},
    "cargo": {"type": "categorical", "role": "dimension", "cardinality": 2},
    "estado": {"type": "categorical", "role": "dimension", "cardinality": 2},
    "salario_mensual": {"type": "numeric", "role": "metric", "cardinality": 9000},
}


# ── Pilar 1: flow datasets skip the implicit latest filter ────────────
def test_flow_datasets_skip_latest_snapshot() -> None:
    flow_contract = {
        "dataset_mode": "flow",
        "time_axis": "fecha_contratacion",
        "date_columns": ["fecha_contratacion"],
        "snapshot_guard_allowed": False,
    }
    assert should_default_to_latest_snapshot(
        _PROMPT, dataset_contract=flow_contract, schema_profile=_RRHH_SCHEMA
    ) is False
    assert (
        build_default_latest_snapshot_filters(
            _PROMPT,
            _RRHH_COLUMNS,
            dataset_contract=flow_contract,
            schema_profile=_RRHH_SCHEMA,
        )
        == []
    )


# ── Pilar 1 + A+: snapshot CONSERVA el filtro latest (no regression) ──
def test_snapshot_keeps_latest_filter() -> None:
    contract = {
        "dataset_mode": "snapshot",
        "time_axis": "fecha_contratacion",
        "date_columns": ["fecha_contratacion"],
        "snapshot_guard_allowed": True,  # forma real de data_engine
    }
    assert should_default_to_latest_snapshot(
        _PROMPT, dataset_contract=contract, schema_profile=_RRHH_SCHEMA
    ) is True, "snapshot debe conservar el filtro latest"
    filters = build_default_latest_snapshot_filters(
        _PROMPT,
        _RRHH_COLUMNS,
        dataset_contract=contract,
        schema_profile=_RRHH_SCHEMA,
    )
    assert len(filters) == 1, "snapshot debe generar exactamente 1 filtro"


# ── A+: hybrid NO inyecta latest (fix L1 — snapshot_guard_allowed=True real) ─
def test_hybrid_skips_latest_filter() -> None:
    """hybrid con snapshot_guard_allowed=True (forma real de data_engine.py:1142)
    NO debe inyectar el filtro latest. El gate en core.py intercepta
    dataset_mode=='hybrid' ANTES de leer snapshot_guard_allowed."""
    contract = {
        "dataset_mode": "hybrid",
        "time_axis": "fecha_contratacion",
        "date_columns": ["fecha_contratacion"],
        "snapshot_guard_allowed": True,  # ← forma real (data_engine:1142)
    }
    assert should_default_to_latest_snapshot(
        _PROMPT, dataset_contract=contract, schema_profile=_RRHH_SCHEMA
    ) is False, "hybrid no debe forzar latest"
    filters = build_default_latest_snapshot_filters(
        _PROMPT,
        _RRHH_COLUMNS,
        dataset_contract=contract,
        schema_profile=_RRHH_SCHEMA,
    )
    assert filters == [], "hybrid no debe generar filtros snapshot"


# ── Pilar 3: false 'flujos opuestos' note suppressed for RRHH ─────────
def test_direction_notes_suppressed_for_rrhh() -> None:
    from app.services.canonical_tabular_canary_executor import _extract_direction_notes

    plan = SimpleNamespace(
        main_intent=SimpleNamespace(group_by=["departamento"], split_dimension=None)
    )
    assert _extract_direction_notes([plan], schema_profile=_RRHH_SCHEMA) is None


# ── Pilar 3: genuine financial flow still receives the note ───────────
def test_direction_notes_emitted_for_finance() -> None:
    from app.services.canonical_tabular_canary_executor import _extract_direction_notes

    plan = SimpleNamespace(
        main_intent=SimpleNamespace(group_by=["tipo_movimiento"], split_dimension=None)
    )
    finance_schema = {
        "tipo_movimiento": {"type": "categorical", "role": "dimension", "cardinality": 2}
    }
    note = _extract_direction_notes([plan], schema_profile=finance_schema)
    assert note is not None, "Finanzas con columna dirección debe generar la nota"
    assert "flujos opuestos" in note


# ═══════════════════════════════════════════════════════════════════════
# A+ Tests — Macro bundle + Snapshot Guard (L1 + L2)
# ═══════════════════════════════════════════════════════════════════════

# ── A+ L1: macro fast path hybrid → 0 filtros latest ─────────────────
def test_macro_bundle_hybrid_no_latest_filter() -> None:
    """El macro fast path para broad analysis NO debe inyectar filtro
    latest cuando dataset_mode='hybrid'. Verifica L1 end-to-end."""
    from app.services.semantic_translator.planner import build_macro_analysis_bundle

    contract = {
        "dataset_mode": "hybrid",
        "time_axis": "fecha_operacion",
        "date_columns": ["fecha_operacion"],
        "snapshot_guard_allowed": True,  # ← forma real de data_engine
    }
    schema = {
        "fecha_operacion": {"type": "temporal", "role": "date", "cardinality": 1813},
        "ruta": {"type": "categorical", "role": "dimension", "cardinality": 25},
        "kilometraje": {"type": "numeric", "role": "metric", "cardinality": 5000},
    }
    columns = ["fecha_operacion", "ruta", "kilometraje"]

    plans = build_macro_analysis_bundle(
        "realiza un analisis gerencial",
        columns,
        schema_profile=schema,
        dataset_contract=contract,
    )
    assert plans is not None and len(plans) > 0, "Debe generar al menos 1 plan"

    for plan in plans:
        intent = plan.main_intent
        filters = getattr(intent, "filters", []) or []
        latest_filters = [f for f in filters if getattr(f, "value", "") == "latest"]
        assert latest_filters == [], (
            f"Plan '{plan.title}' no debe tener filtro latest en hybrid, "
            f"pero tiene: {latest_filters}"
        )


# ── A+ L1: macro fast path snapshot → SÍ inyecta latest (no regression) ──
def test_macro_bundle_snapshot_keeps_latest_filter() -> None:
    """Snapshot puro con macro fast path DEBE conservar el filtro latest."""
    from app.services.semantic_translator.planner import build_macro_analysis_bundle

    contract = {
        "dataset_mode": "snapshot",
        "time_axis": "fecha_operacion",
        "date_columns": ["fecha_operacion"],
        "snapshot_guard_allowed": True,
    }
    schema = {
        "fecha_operacion": {"type": "temporal", "role": "date", "cardinality": 12},
        "sucursal": {"type": "categorical", "role": "dimension", "cardinality": 8},
        "stock_disponible": {"type": "numeric", "role": "metric", "cardinality": 500},
    }
    columns = ["fecha_operacion", "sucursal", "stock_disponible"]

    plans = build_macro_analysis_bundle(
        "realiza un analisis gerencial",
        columns,
        schema_profile=schema,
        dataset_contract=contract,
    )
    assert plans is not None and len(plans) > 0
    # Al menos el KPI (DescriptiveIntent) debe tener filtro latest
    descriptive_plans = [
        p for p in plans if p.main_intent.type == "descriptive"
    ]
    assert len(descriptive_plans) > 0, "Debe haber al menos un KPI"
    for dp in descriptive_plans:
        filters = getattr(dp.main_intent, "filters", []) or []
        latest_filters = [f for f in filters if getattr(f, "value", "") == "latest"]
        assert len(latest_filters) == 1, (
            f"KPI snapshot debe tener filtro latest, tiene: {filters}"
        )


# ── A+ L2: hybrid + métrica de flujo → guard NO aplica ───────────────
def test_snapshot_guard_hybrid_flow_metric_skips() -> None:
    """hybrid con métrica de flujo (kilometraje) no debe aplicar
    is_latest_snapshot guard. L2 lo salta y el fallback por keywords
    no matchea 'kilometraje'."""
    from app.services.snapshot_guard import should_apply_latest_snapshot_filter

    intent = SimpleNamespace(
        type="distribution",
        metric="kilometraje",
        filters=[],
        date_column=None,
        dimension="ruta",
        group_by=None,
        split_dimension=None,
    )
    contract = {
        "dataset_mode": "hybrid",
        "time_axis": "fecha_operacion",
        "date_columns": ["fecha_operacion"],
        "snapshot_guard_allowed": True,
    }
    result = should_apply_latest_snapshot_filter(
        intent, ["fecha_operacion", "ruta", "kilometraje", "is_latest_snapshot"],
        dataset_contract=contract,
    )
    assert result is False, "hybrid + métrica flujo → guard no aplica"


# ── A+ L2: hybrid + métrica de stock → guard SÍ aplica (keywords) ────
def test_snapshot_guard_hybrid_stock_metric_applies() -> None:
    """hybrid con métrica de stock (stock_disponible) SÍ debe aplicar
    el guard — el fallback por keywords detecta 'stock'."""
    from app.services.snapshot_guard import should_apply_latest_snapshot_filter

    intent = SimpleNamespace(
        type="distribution",
        metric="stock_disponible",
        filters=[],
        date_column=None,
        dimension="sucursal",
        group_by=None,
        split_dimension=None,
    )
    contract = {
        "dataset_mode": "hybrid",
        "time_axis": "fecha_operacion",
        "date_columns": ["fecha_operacion"],
        "snapshot_guard_allowed": True,
    }
    result = should_apply_latest_snapshot_filter(
        intent, ["fecha_operacion", "sucursal", "stock_disponible", "is_latest_snapshot"],
        dataset_contract=contract,
    )
    assert result is True, "hybrid + métrica stock → guard aplica por keyword"


# ── A+ L2: snapshot + cualquier métrica → guard SÍ aplica (no regression) ─
def test_snapshot_guard_snapshot_always_applies() -> None:
    """snapshot puro siempre aplica guard independientemente de la métrica."""
    from app.services.snapshot_guard import should_apply_latest_snapshot_filter

    intent = SimpleNamespace(
        type="distribution",
        metric="kilometraje",
        filters=[],
        date_column=None,
        dimension="ruta",
        group_by=None,
        split_dimension=None,
    )
    contract = {
        "dataset_mode": "snapshot",
        "time_axis": "fecha_operacion",
        "date_columns": ["fecha_operacion"],
        "snapshot_guard_allowed": True,
    }
    result = should_apply_latest_snapshot_filter(
        intent, ["fecha_operacion", "ruta", "kilometraje", "is_latest_snapshot"],
        dataset_contract=contract,
    )
    assert result is True, "snapshot siempre aplica guard por contrato"


# ═══════════════════════════════════════════════════════════════════════
# Stage 1 — Invariante query↔arrow (reconciliación de A+)
# ═══════════════════════════════════════════════════════════════════════

def _plan_with_filters(*filters):
    return SimpleNamespace(main_intent=SimpleNamespace(filters=list(filters)))


def _filter(column, value):
    return SimpleNamespace(column=column, value=value)


# ── T1: guard del falso positivo categórico ('estado'=='actual') ──────
def test_plan_latest_date_columns_rejects_non_date() -> None:
    """[Stage 1] Solo columnas fecha cuentan. 'actual' en columna categórica
    NO debe detectarse (paridad con ibis_engine._build_filter_expression)."""
    from app.services.canonical_tabular_canary_executor import _plan_latest_date_columns

    date_cols = {"fecha_operacion"}
    # categórica con valor "actual" → NO detectado
    assert _plan_latest_date_columns(
        [_plan_with_filters(_filter("estado", "actual"))], date_cols
    ) == {}
    # columna fecha con token (casing distinto) → detectado con casing canónico
    assert _plan_latest_date_columns(
        [_plan_with_filters(_filter("FECHA_OPERACION", "latest"))], date_cols
    ) == {"fecha_operacion": "latest"}
    # token en español
    assert _plan_latest_date_columns(
        [_plan_with_filters(_filter("fecha_operacion", "último"))], date_cols
    ) == {"fecha_operacion": "último"}
    # entradas vacías
    assert _plan_latest_date_columns(None, date_cols) == {}
    assert _plan_latest_date_columns([_plan_with_filters()], date_cols) == {}


# ── T2: tabla de verdad del invariante ────────────────────────────────
def test_query_collapsed_truth_table() -> None:
    """[Stage 1] El query solo colapsa si snapshot, guard aplicado o latest explícito."""
    from app.services.canonical_tabular_canary_executor import (
        _query_collapsed_to_temporal_slice as collapse,
    )

    # hybrid flow macro (caso E2E) → NO colapsa (no-op)
    assert collapse(dataset_is_snapshot=False, guard_applied=False, plan_has_latest=False) is False
    assert collapse(dataset_is_snapshot=True, guard_applied=False, plan_has_latest=False) is True
    assert collapse(dataset_is_snapshot=False, guard_applied=True, plan_has_latest=False) is True
    assert collapse(dataset_is_snapshot=False, guard_applied=False, plan_has_latest=True) is True
    assert collapse(dataset_is_snapshot=False, guard_applied=True, plan_has_latest=True) is True


# ── T3: el camino macro hybrid es no-op (regresión del escenario E2E) ──
def test_macro_bundle_hybrid_has_no_latest_filter() -> None:
    """[Stage 1] El macro fast path de un dataset hybrid NO genera filtros
    'latest' sobre columnas fecha → `_query_collapsed` sería False (no-op)."""
    from app.services.semantic_translator.planner import build_macro_analysis_bundle
    from app.services.canonical_tabular_canary_executor import _plan_latest_date_columns

    contract = {
        "dataset_mode": "hybrid",
        "time_axis": "fecha_operacion",
        "date_columns": ["fecha_operacion"],
        "snapshot_guard_allowed": True,
    }
    schema = {
        "fecha_operacion": {"type": "temporal", "role": "date", "cardinality": 1813},
        "ruta": {"type": "categorical", "role": "dimension", "cardinality": 25},
        "kilometraje": {"type": "numeric", "role": "metric", "cardinality": 5000},
    }
    plans = build_macro_analysis_bundle(
        _PROMPT, ["fecha_operacion", "ruta", "kilometraje"],
        schema_profile=schema, dataset_contract=contract,
    )
    assert _plan_latest_date_columns(plans, {"fecha_operacion"}) == {}, (
        "El macro hybrid no debe activar el colapso query↔arrow"
    )


# ── T4: filtro 'latest' explícito sobre columna fecha → colapsa (hueco cerrado) ──
def test_hybrid_explicit_latest_resolves_on_filter_column() -> None:
    """[Stage 1] Un 'latest' explícito sobre una columna fecha debe detectarse
    y resolver sobre ESA columna (no sobre time_axis), cerrando el hueco."""
    from app.services.canonical_tabular_canary_executor import (
        _plan_latest_date_columns,
        _query_collapsed_to_temporal_slice as collapse,
    )

    plans = [_plan_with_filters(_filter("fecha_operacion", "latest"))]
    latest = _plan_latest_date_columns(plans, {"fecha_registro", "fecha_operacion"})
    assert latest == {"fecha_operacion": "latest"}
    assert collapse(dataset_is_snapshot=False, guard_applied=False, plan_has_latest=bool(latest)) is True


# ═══════════════════════════════════════════════════════════════════════
# C-A — El sufijo "(Corte Actual)" refleja el filtro real (no el modo)
# ═══════════════════════════════════════════════════════════════════════

def test_kpi_title_hybrid_has_no_corte_actual() -> None:
    """[C-A] hybrid tiene snapshot_guard_allowed=True pero NO recibe filtro
    latest (A+ L1) → su KPI es histórico y no debe titularse '(Corte Actual)'."""
    from app.services.semantic_translator.planner import build_macro_analysis_bundle

    contract = {
        "dataset_mode": "hybrid",
        "time_axis": "fecha_operacion",
        "date_columns": ["fecha_operacion"],
        "snapshot_guard_allowed": True,
    }
    schema = {
        "fecha_operacion": {"type": "temporal", "role": "date", "cardinality": 1813},
        "ruta": {"type": "categorical", "role": "dimension", "cardinality": 25},
        "kilometraje": {"type": "numeric", "role": "metric", "cardinality": 5000},
    }
    plans = build_macro_analysis_bundle(
        _PROMPT, ["fecha_operacion", "ruta", "kilometraje"],
        schema_profile=schema, dataset_contract=contract,
    )
    assert plans, "Debe generar planes"
    assert all("(Corte Actual)" not in p.title for p in plans), (
        f"hybrid no debe titular '(Corte Actual)': {[p.title for p in plans]}"
    )


def test_kpi_title_snapshot_keeps_corte_actual() -> None:
    """[C-A] snapshot SÍ recibe el filtro latest → conserva '(Corte Actual)'."""
    from app.services.semantic_translator.planner import build_macro_analysis_bundle

    contract = {
        "dataset_mode": "snapshot",
        "time_axis": "fecha_operacion",
        "date_columns": ["fecha_operacion"],
        "snapshot_guard_allowed": True,
    }
    schema = {
        "fecha_operacion": {"type": "temporal", "role": "date", "cardinality": 12},
        "sucursal": {"type": "categorical", "role": "dimension", "cardinality": 8},
        "stock_disponible": {"type": "numeric", "role": "metric", "cardinality": 500},
    }
    plans = build_macro_analysis_bundle(
        _PROMPT, ["fecha_operacion", "sucursal", "stock_disponible"],
        schema_profile=schema, dataset_contract=contract,
    )
    assert any("(Corte Actual)" in p.title for p in plans), (
        f"snapshot debe conservar '(Corte Actual)': {[p.title for p in plans]}"
    )


# ═══════════════════════════════════════════════════════════════════════
# B1 — Red determinista contra la repetición overview ↔ hallazgos
# ═══════════════════════════════════════════════════════════════════════

def test_drop_redundant_findings_removes_overview_echo() -> None:
    from app.services.dashboard_narrative import _drop_redundant_findings

    overview = "El analisis muestra una variacion total de menos 0.47 por ciento y tendencia decreciente."
    findings = [
        "una variacion total de menos 0.47 por ciento y tendencia decreciente",   # substring
        "Los galones consumidos presentan tendencia decreciente durante el periodo analizado completo",
        "El costo por kilometro alcanzo su maximo en la ruta Lima durante septiembre",
    ]
    kept = _drop_redundant_findings(findings, overview)
    assert findings[0] not in kept, "substring del overview debe descartarse"
    assert findings[2] in kept, "hallazgo con contenido nuevo debe conservarse"


def test_drop_redundant_findings_removes_high_similarity() -> None:
    from app.services.dashboard_narrative import _drop_redundant_findings

    overview = "Los galones consumidos presentan tendencia decreciente durante el periodo analizado completo"
    finding = "Los galones consumidos presentan tendencia decreciente durante el periodo analizado"
    assert _drop_redundant_findings([finding], overview) == []


def test_build_summary_result_dedups_findings() -> None:
    from app.services.dashboard_narrative import _build_summary_result

    overview = "El analisis muestra tendencia decreciente en galones consumidos."
    result = _build_summary_result(
        payload={
            "headline": "Consumo estable",
            "overview": overview,
            "key_findings": [overview, "El costo por kilometro subio en la ruta Lima."],
            "risks": [],
            "actions": [],
            "caveats": [],
        },
        presentation_name="Analisis Universal",
        widget_count=3,
        mixed_sources=False,
        filter_scope=[],
    )
    assert result["key_findings"] == ["El costo por kilometro subio en la ruta Lima."]


# ═══════════════════════════════════════════════════════════════════════
# C-KPI — Los KPI (data=dict) deben aportar facts a la narrativa
# ═══════════════════════════════════════════════════════════════════════

def test_widget_facts_kpi_dict_exposes_value() -> None:
    from app.services.canonical_tabular_canary_executor import _build_widget_facts

    facts = _build_widget_facts(
        "Gasto Total", {"type": "kpi", "data": {"gasto_combustible_s": 123.45}},
    )
    assert facts == ["Gasto Total: gasto_combustible_s = 123.45."]


def test_widget_facts_kpi_ignores_none_values() -> None:
    from app.services.canonical_tabular_canary_executor import _build_widget_facts

    assert _build_widget_facts("KPI", {"type": "kpi", "data": {"a": None}}) == []


def test_widget_facts_list_data_unchanged() -> None:
    """Regresión: un payload con `data` lista NO debe activar el branch dict."""
    from app.services.canonical_tabular_canary_executor import _build_widget_facts

    payload = {
        "type": "echarts",
        "data": [{"name": "A", "value": 1}],
        "hard_facts": {
            "top_1_name": "A",
            "top_1_val": 1.0,
            "top_1_share": 100.0,
            "total_analyzed": 1.0,
        },
    }
    facts = _build_widget_facts("Ranking", payload)
    assert any("líder A" in f for f in facts), f"facts inesperados: {facts}"
    assert all(not f.startswith("Ranking: name =") for f in facts)


def test_widget_facts_comparison_kpi_keeps_comparison_fact() -> None:
    """Regresión clave: el dict KPI NO debe impedir el fact de comparación."""
    from app.services.canonical_tabular_canary_executor import _build_widget_facts

    payload = {
        "type": "kpi",
        "data": {"Ventas (Base)": 100.0, "Ventas (2022)": 120.0, "Variación": 20.0, "%": 20.0},
        "hard_facts": {
            "comparison": {
                "year_from": "Base",
                "year_to": "2022",
                "metric": "ventas",
                "metric_humanized": "Ventas",
                "total_variation": 20.0,
                "base_total": 100.0,
                "compared_total": 120.0,
                "variation_pct": 20.0,
            }
        },
    }
    facts = _build_widget_facts("Variación Total de Ventas", payload)
    assert any("comparación" in f for f in facts), f"falta el fact de comparación: {facts}"


# ═══════════════════════════════════════════════════════════════════════
# E1 — Dual-Role Columns (métrica/dimensión)
# Columnas enteras de baja cardinalidad con semántica de medida deben
# clasificarse como MÉTRICA, no como dimensión (bug de desperfectos_mecanicos).
# ═══════════════════════════════════════════════════════════════════════

def test_dual_role_desperfectos_classified_as_metric() -> None:
    import pandas as pd
    from app.services.canonical_schema_profiler import build_canonical_schema_profile

    df = pd.DataFrame({
        "desperfectos_mecanicos": [0, 1, 2, 0, 1, 2, 0, 1, 0, 1] * 50,
        "costo_total": [10.0] * 500,
    })
    _, profile, _ = build_canonical_schema_profile(df)
    assert profile["desperfectos_mecanicos"]["role"] == "metric", profile[
        "desperfectos_mecanicos"
    ]


def test_dual_role_falla_singular_classified_as_metric() -> None:
    import pandas as pd
    from app.services.canonical_schema_profiler import build_canonical_schema_profile

    df = pd.DataFrame({"falla_mecanica": [0, 1] * 100})
    _, profile, _ = build_canonical_schema_profile(df)
    assert profile["falla_mecanica"]["role"] == "metric", profile["falla_mecanica"]


def test_dual_role_classify_columns_second_path() -> None:
    """El segundo path (_classify_columns) debe alinearse con el profiler."""
    import pandas as pd
    from app.services.data_engine import DataEngine

    df = pd.DataFrame({
        "desperfectos_mecanicos": [0, 1, 2] * 100,
        "tipo_almacen": [0, 1, 2] * 100,
    })
    schema = DataEngine._classify_columns(df)
    assert schema["desperfectos_mecanicos"]["role"] == "metric", schema[
        "desperfectos_mecanicos"
    ]
    assert schema["tipo_almacen"]["role"] == "dimension", schema["tipo_almacen"]


# ═══════════════════════════════════════════════════════════════════════
# E2 — Diccionario de sinónimos + resolución por stems
# ═══════════════════════════════════════════════════════════════════════

def test_synonym_resolve_ruta_to_origen_destino() -> None:
    from app.services.semantic_translator.dimension_synonyms import resolve_synonym

    cols = {"origen", "destino", "multas_s", "costo"}
    result = resolve_synonym("ruta", cols)
    assert result is not None
    assert "origen" in result and "destino" in result


def test_synonym_plural_returns_origen_destino() -> None:
    from app.services.semantic_translator.dimension_synonyms import resolve_synonym

    cols = {"origen", "destino", "placa_unidad"}
    assert resolve_synonym("rutas", cols) == ["origen", "destino"]


def test_synonym_no_match_returns_none() -> None:
    from app.services.semantic_translator.dimension_synonyms import resolve_synonym

    assert resolve_synonym("xyzabc", {"multas_s", "costo"}) is None


def test_synonym_multi_tenant_retail() -> None:
    """Stems genéricos funcionan con columnas de otro dominio (retail)."""
    from app.services.semantic_translator.dimension_synonyms import resolve_synonym

    result = resolve_synonym("fecha", {"fecha_venta", "tipo_cliente", "monto_total"})
    assert result is not None
    assert "fecha_venta" in result


def test_synonym_no_metric_collision() -> None:
    """'ruta' NO debe resolver a km_recorridos (métrica continua)."""
    from app.services.semantic_translator.dimension_synonyms import resolve_synonym

    cols = {"origen", "destino", "km_recorridos", "multas_s"}
    assert resolve_synonym("ruta", cols) == ["origen", "destino"]


def test_synonym_deterministic_order() -> None:
    """El orden no depende del set (origen siempre antes que destino)."""
    from app.services.semantic_translator.dimension_synonyms import resolve_synonym

    cols = {"destino", "origen"}
    for _ in range(5):
        assert resolve_synonym("ruta", cols) == ["origen", "destino"]


def test_synonym_ignores_internal_columns() -> None:
    """_dataset_year / is_latest_snapshot nunca son dimensión reparada."""
    from app.services.semantic_translator.dimension_synonyms import resolve_synonym

    cols = {"fecha_operacion", "_dataset_year", "is_latest_snapshot"}
    assert resolve_synonym("periodo", cols) == ["fecha_operacion"]


# ═══════════════════════════════════════════════════════════════════════
# E3 — Parquet Pipeline Optimization (skip writes muertos)
# ═══════════════════════════════════════════════════════════════════════

def test_should_skip_related_parquets_when_unified() -> None:
    from app.services.canonical_tabular_production_executor import (
        _should_skip_related_parquets,
    )

    assert _should_skip_related_parquets("derived__unified_all__sheets") is True
    assert _should_skip_related_parquets("unified_all__") is True


def test_should_not_skip_related_parquets_for_raw_sheet() -> None:
    from app.services.canonical_tabular_production_executor import (
        _should_skip_related_parquets,
    )

    assert _should_skip_related_parquets("sheet__Hoja1") is False
    assert _should_skip_related_parquets("") is False
    assert _should_skip_related_parquets(None) is False
