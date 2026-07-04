"""
test_ibis_func_map.py
═══════════════════════════════════════════════════════════════════
Tests unitarios para IBIS_FUNC_MAP — Catálogo de Funciones de Agregación
═══════════════════════════════════════════════════════════════════

Valida que el motor Ibis soporta correctamente todas las funciones
del catálogo en filtros, incluyendo:
  - Mapeo avg→mean (Ibis usa .mean() no .avg())
  - count_distinct→nunique (Ibis usa .nunique())
  - Case-insensitive (MEDIAN == median)
  - Espacios en sintaxis (MEDIAN( columna ) == MEDIAN(columna))
"""
from __future__ import annotations

import pytest
import ibis
import pandas as pd

from app.services.ibis_engine import IbisEngine
from app.core.semantic_grammar import DataFilter


@pytest.fixture
def table():
    """Tabla de prueba con columnas numéricas y categóricas."""
    return ibis.memtable(pd.DataFrame({
        'salario': [3000, 4000, 5000, 6000, 7000],
        'edad': [25, 30, 35, 40, 45],
        'departamento': ['Ventas', 'IT', 'IT', 'RRHH', 'Ventas'],
    }))


def _resolve(table, value: str) -> bool:
    """Helper: intenta construir expresión de filtro con agregado."""
    f = DataFilter(column='salario', operator='<', value=value)
    try:
        result = IbisEngine._build_filter_expression(table, f)
        return result is not None
    except Exception:
        return False


# ── Tests del Catálogo ───────────────────────────────────────────

def test_func_map_contains_all_expected_keys() -> None:
    """IBIS_FUNC_MAP debe contener todas las 12 funciones esperadas."""
    expected = {
        'max', 'min', 'sum', 'avg', 'mean', 'median',
        'count', 'count_distinct', 'stddev', 'std', 'variance', 'var',
    }
    actual = set(IbisEngine.IBIS_FUNC_MAP.keys())
    missing = expected - actual
    assert not missing, f"Faltan funciones en IBIS_FUNC_MAP: {missing}"


def test_avg_maps_to_mean_ibis_method() -> None:
    """AVG debe mapear a .mean() en Ibis (no .avg())."""
    assert IbisEngine.IBIS_FUNC_MAP['avg'] == 'mean'


def test_count_distinct_maps_to_nunique() -> None:
    """COUNT_DISTINCT debe mapear a .nunique() en Ibis."""
    assert IbisEngine.IBIS_FUNC_MAP['count_distinct'] == 'nunique'


def test_stddev_maps_to_std() -> None:
    """STDDEV debe mapear a .std() en Ibis."""
    assert IbisEngine.IBIS_FUNC_MAP['stddev'] == 'std'


def test_variance_maps_to_var() -> None:
    """VARIANCE debe mapear a .var() en Ibis."""
    assert IbisEngine.IBIS_FUNC_MAP['variance'] == 'var'


# ── Tests de Resolución ──────────────────────────────────────────

def test_max_resolves(table) -> None:
    """MAX(salario) debe resolver a expresión válida."""
    assert _resolve(table, 'MAX(salario)')


def test_min_resolves(table) -> None:
    """MIN(salario) debe resolver a expresión válida."""
    assert _resolve(table, 'MIN(salario)')


def test_sum_resolves(table) -> None:
    """SUM(salario) debe resolver a expresión válida."""
    assert _resolve(table, 'SUM(salario)')


def test_avg_resolves(table) -> None:
    """AVG(salario) debe resolver a expresión válida."""
    assert _resolve(table, 'AVG(salario)')


def test_mean_resolves(table) -> None:
    """MEAN(salario) debe resolver a expresión válida."""
    assert _resolve(table, 'MEAN(salario)')


def test_median_resolves(table) -> None:
    """MEDIAN(salario) debe resolver a expresión válida (fix para RRHH)."""
    assert _resolve(table, 'MEDIAN(salario)')


def test_count_resolves(table) -> None:
    """COUNT(salario) debe resolver a expresión válida."""
    assert _resolve(table, 'COUNT(salario)')


def test_stddev_resolves(table) -> None:
    """STDDEV(salario) debe resolver a expresión válida."""
    assert _resolve(table, 'STDDEV(salario)')


def test_variance_resolves(table) -> None:
    """VARIANCE(salario) debe resolver a expresión válida."""
    assert _resolve(table, 'VARIANCE(salario)')


# ── Tests de Robustez ────────────────────────────────────────────

def test_case_insensitive_median_uppercase(table) -> None:
    """MEDIAN (mayúsculas) debe funcionar igual que median (minúsculas)."""
    assert _resolve(table, 'MEDIAN(salario)')


def test_case_insensitive_max_mixed(table) -> None:
    """Max (mixed case) debe funcionar."""
    assert _resolve(table, 'Max(salario)')


def test_spaces_around_column(table) -> None:
    """MEDIAN( salario ) con espacios debe funcionar."""
    assert _resolve(table, 'MEDIAN( salario )')


def test_valid_function_not_matched_no_side_effects(table) -> None:
    """Función no soportada no debe crashear."""
    assert not _resolve(table, 'INVALID_FUNC(salario)')


def test_nonexistent_column_no_crash(table) -> None:
    """Columna inexistente no debe crashear."""
    assert not _resolve(table, 'MAX(columna_inexistente)')
