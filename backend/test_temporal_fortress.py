"""
test_temporal_fortress.py
═══════════════════════════════════════════════════════════════════
CI/CD Gate — Inviolabilidad del Motor Temporal y Snapshot Guard
═══════════════════════════════════════════════════════════════════

9 tests puros que actúan como puerta de enlace anti-regresión:
  T1 — Corrección de año ISO vía _dataset_year
  T2 — No-corrección cuando año del filtro coincide con dataset
  T3 — Snapshot guard activado para trend sobre columna no-time_axis
  T4 — Snapshot guard omitido para trend sobre time_axis
  T5 — Snapshot guard por contrato sin keyword match en métricas
  T6 — Snapshot guard omitido cuando existe filtro explícito en time_axis
  T7 — _infer_dataset_year prefiere columna sobre _dataset_year
  T8 — Validator normalize_router_filters corrige ISO vía type="temporal"
  T9 — normalize_intent_temporal_filters cubre ruta COMPLEJO

Si cualquier modificación futura rompe esta lógica, este archivo
se pone en rojo de inmediato — es un CI/CD Gate, no un test opcional.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from app.core.semantic_grammar import AnalysisPlan
from app.services.snapshot_guard import should_apply_latest_snapshot_filter
from app.services.semantic_translator.temporal_resolver import (
    _infer_dataset_year,
    normalize_intent_temporal_filters,
    resolve_temporal_filter_value,
)


# ── T1: Corrección ISO con _dataset_year ──────────────────────────────
def test_iso_year_correction_with_dataset_year() -> None:
    """schema_profile con _dataset_year=2021 corrige 2023-06-01 → 2021-06-01."""
    result = resolve_temporal_filter_value(
        column="fecha_de_stock",
        operator=">=",
        value="2023-06-01",
        schema_profile={"_dataset_year": 2021},
    )
    assert result is not None, "Se esperaba corrección de año ISO"
    assert len(result) == 1, f"Esperado 1 filtro corregido, recibidos {len(result)}"
    assert result[0]["value"] == "2021-06-01", (
        f"Esperado 2021-06-01, recibido {result[0]['value']}"
    )
    assert result[0]["column"] == "fecha_de_stock"
    assert result[0]["operator"] == ">="


# ── T2: No-corrección cuando filtro ya está en año correcto ────────────
def test_iso_year_no_correction_same_year() -> None:
    """Si el año del filtro ya coincide con _dataset_year, no corrige."""
    result = resolve_temporal_filter_value(
        column="fecha_de_stock",
        operator=">=",
        value="2021-06-01",
        schema_profile={"_dataset_year": 2021},
    )
    assert result is None, "No debe corregir cuando el año ya coincide"


# ── T3: Trend sobre columna no-time_axis → guard aplicado ──────────────
def test_snapshot_guard_activates_for_trend_on_non_time_axis() -> None:
    """Trend con date_column ≠ time_axis del contrato → guard True."""
    plan = AnalysisPlan.model_validate(
        {
            "title": "Cronograma de vencimientos",
            "column_aliases": {},
            "main_intent": {
                "type": "trend",
                "rationale": "Stock que vence en próximos 30 días",
                "date_column": "fecaduc_feprefercons",
                "value_column": "stock_disponible",
                "grain": "day",
            },
        }
    )
    guard = should_apply_latest_snapshot_filter(
        plan.main_intent,
        [
            "fecha_de_stock",
            "fecaduc_feprefercons",
            "stock_disponible",
            "is_latest_snapshot",
        ],
        {
            "snapshot_guard_allowed": True,
            "dataset_mode": "snapshot",
            "time_axis": "fecha_de_stock",
        },
    )
    assert guard is True, (
        "El snapshot guard DEBE aplicarse cuando un trend usa una columna "
        "temporal distinta al time_axis del contrato"
    )


# ── T4: Trend sobre time_axis → guard omitido ──────────────────────────
def test_snapshot_guard_skips_for_trend_on_time_axis() -> None:
    """Trend con date_column == time_axis del contrato → guard False."""
    plan = AnalysisPlan.model_validate(
        {
            "title": "Evolución de stock en junio y julio",
            "column_aliases": {},
            "main_intent": {
                "type": "trend",
                "rationale": "Tendencia histórica de stock",
                "date_column": "fecha_de_stock",
                "value_column": "stock_disponible",
                "grain": "month",
            },
        }
    )
    guard = should_apply_latest_snapshot_filter(
        plan.main_intent,
        ["fecha_de_stock", "material", "stock_disponible", "is_latest_snapshot"],
        {
            "snapshot_guard_allowed": True,
            "dataset_mode": "snapshot",
            "time_axis": "fecha_de_stock",
        },
    )
    assert guard is False, (
        "El snapshot guard NO debe aplicarse cuando un trend usa "
        "el time_axis del contrato como su dimensión temporal"
    )


# ── T5: Contrato snapshot activa guard sin keyword match ────────────────
def test_snapshot_guard_activates_contract_based_no_keywords() -> None:
    """Dataset mode=snapshot activa guard incluso sin keyword en métrica."""
    plan = AnalysisPlan.model_validate(
        {
            "title": "Diagnóstico de cantidades por ubicación",
            "column_aliases": {},
            "main_intent": {
                "type": "diagnostic",
                "rationale": "Distribución de cantidades",
                "metric": "unidades",
                "metrics": ["unidades"],
                "dimension": "ubicacion",
            },
        }
    )
    guard = should_apply_latest_snapshot_filter(
        plan.main_intent,
        ["fecha_de_stock", "ubicacion", "unidades", "is_latest_snapshot"],
        {
            "snapshot_guard_allowed": True,
            "dataset_mode": "snapshot",
            "time_axis": "fecha_de_stock",
        },
    )
    assert guard is True, (
        "El snapshot guard DEBE activarse para dataset snapshot sin depender "
        "de keyword matching en métricas. La decisión por contrato es la "
        "autoridad, no los nombres de columna."
    )


# ── T6: Filtro explícito en time_axis → guard omitido ──────────────────
def test_snapshot_guard_skips_when_filter_on_time_axis() -> None:
    """Intent con filtro explícito en time_axis → guard False."""
    plan = AnalysisPlan.model_validate(
        {
            "title": "Stock de junio 2021",
            "column_aliases": {},
            "main_intent": {
                "type": "diagnostic",
                "rationale": "Análisis de stock en fecha específica",
                "metric": "stock_disponible",
                "dimension": "ubicacion",
                "filters": [
                    {
                        "column": "fecha_de_stock",
                        "operator": ">=",
                        "value": "2021-06-01",
                    },
                ],
            },
        }
    )
    guard = should_apply_latest_snapshot_filter(
        plan.main_intent,
        ["fecha_de_stock", "ubicacion", "stock_disponible", "is_latest_snapshot"],
        {
            "snapshot_guard_allowed": True,
            "dataset_mode": "snapshot",
            "time_axis": "fecha_de_stock",
        },
    )
    assert guard is False, (
        "El snapshot guard DEBE omitirse cuando el intent ya filtra "
        "explícitamente por el time_axis del contrato"
    )


# ── T7: _infer_dataset_year prefiere columna sobre _dataset_year ───────
def test_infer_dataset_year_prefers_column_over_dataset_year() -> None:
    """La cascada de _infer_dataset_year prefiere el max de una columna
    sobre _dataset_year (step 1 > step 4)."""
    schema_profile: dict = {
        "fecha_de_stock": {
            "max": "2019-07-31",
            "role": "date",
            "type": "temporal",
        },
        "_dataset_year": 2021,
    }
    year = _infer_dataset_year("fecha_de_stock", schema_profile)
    assert year == 2019, (
        f"Esperado 2019 (del max de fecha_de_stock), recibido {year}. "
        "La columna debe tener prioridad sobre _dataset_year en la cascada. "
        "Si retorna 2021, significa que el step 4 se ejecuto antes que el step 1."
    )


# ── T8: Validator path completo con type="temporal" ───────────────────
def test_validator_normalize_corrects_iso_with_type_temporal() -> None:
    """normalize_router_filters corrige el año ISO cuando schema_profile
    tiene type='temporal' + role='date' para la columna del filtro.

    Este test protege la linea col_meta_type == 'temporal' en validator.py.
    Si se elimina ese check, is_temporal_col no detecta columnas con
    role='date' y el temporal resolver nunca corrige el año."""
    from app.services.semantic_translator.validator import normalize_router_filters

    raw_filters = [
        {"column": "fecha_de_stock", "operator": ">=", "value": "2023-06-01"},
    ]
    columns = ["fecha_de_stock", "material", "stock_disponible"]
    schema_profile: dict = {
        "fecha_de_stock": {
            "type": "temporal",
            "role": "date",
            "cardinality": 42,
        },
        "_dataset_year": 2021,
    }

    result = normalize_router_filters(raw_filters, columns, schema_profile)

    assert len(result) == 1, (
        f"Esperado 1 filtro normalizado, recibidos {len(result)}"
    )
    assert result[0]["column"] == "fecha_de_stock"
    assert result[0]["operator"] == ">="
    assert result[0]["value"] == "2021-06-01", (
        f"Esperado 2021-06-01 (ano corregido), recibido {result[0]['value']}. "
        "El temporal resolver no corrigio el ano — is_temporal_col no detecto "
        "la columna type='temporal' con role='date'."
    )


# ── T9: normalize_intent_temporal_filters cubre ruta COMPLEJO ─────────
def test_normalize_intent_temporal_corrects_filter_year() -> None:
    """normalize_intent_temporal_filters corrige años en filtros del intent
    para planes de ruta COMPLEJO (que no pasan por normalize_router_filters).

    Cierra el gap donde el temporal resolver solo se ejecutaba en la ruta
    SIMPLE. Ahora el ibis_engine lo aplica a TODOS los planes."""
    plan = AnalysisPlan.model_validate(
        {
            "title": "Test COMPLEJO route temporal fix",
            "column_aliases": {},
            "main_intent": {
                "type": "diagnostic",
                "rationale": "Diagnóstico con filtro de año incorrecto",
                "metric": "stock_disponible",
                "filters": [
                    {
                        "column": "fecha_de_stock",
                        "operator": ">=",
                        "value": "2023-06-01",
                    },
                ],
            },
        }
    )

    schema_profile: dict = {
        "fecha_de_stock": {"type": "temporal", "role": "date"},
        "_dataset_year": 2021,
    }

    corrected = normalize_intent_temporal_filters(
        plan.main_intent, schema_profile
    )

    assert corrected is not plan.main_intent, (
        "Debe retornar una copia cuando los filtros fueron modificados"
    )
    assert len(corrected.filters) == 1, (
        f"Esperado 1 filtro tras corrección, recibidos {len(corrected.filters)}"
    )
    assert corrected.filters[0].value == "2021-06-01", (
        f"Esperado 2021-06-01 (ano corregido), recibido {corrected.filters[0].value}"
    )
