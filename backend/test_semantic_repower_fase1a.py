"""
test_semantic_repower_fase1a.py
═══════════════════════════════════════════════════════════════════
Fase 1a — Saneamiento semántico (solo reparación):

  F1.1 — La firma de deduplicación de finalize_plans debe distinguir planes
         legítimos (KPIs distintos, group_by distinto, filtros distintos) y
         colapsar solo duplicados reales, normalizando tipos (1 vs 1.0 vs "1").
  F1.3 — El prompt estático del unified translator debe usar valores válidos
         del enum VisualProtocol (kpi_card, no kpi) y heurísticas de selección.

Estos tests son deterministas y sin LLM.
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))

from app.core.semantic_grammar import (
    AnalysisPlan,
    DataFilter,
    DescriptiveIntent,
    DistributionIntent,
)
from app.services.semantic_translator.unified_translator import (
    get_unified_static_instruction,
)
from app.services.semantic_translator.validator import finalize_plans


def _kpi(metric: str, title: str) -> AnalysisPlan:
    return AnalysisPlan(
        main_intent=DescriptiveIntent(rationale="kpi", metrics=[metric], aggregation="sum"),
        title=title,
    )


def _dist(
    group_by: list[str] | None = None,
    filters: list[DataFilter] | None = None,
    title: str = "dist",
) -> AnalysisPlan:
    return AnalysisPlan(
        main_intent=DistributionIntent(
            rationale="dist",
            dimension="categoria",
            metric="ventas",
            group_by=group_by,
            filters=filters or [],
        ),
        title=title,
    )


# ── F1.1: dos KPIs con métricas distintas sobreviven ──────────────────
def test_distinct_kpis_survive() -> None:
    plans = [_kpi("ventas", "Ventas"), _kpi("costos", "Costos")]
    assert len(finalize_plans(plans, {})) == 2


# ── F1.1: dos distribuciones con group_by distinto sobreviven ─────────
def test_distinct_group_by_survives() -> None:
    plans = [_dist(group_by=["region"], title="A"), _dist(group_by=["canal"], title="B")]
    assert len(finalize_plans(plans, {})) == 2


# ── F1.1: dos planes que difieren solo en filtro sobreviven ───────────
def test_distinct_filter_survives() -> None:
    lima = _dist(filters=[DataFilter(column="ciudad", operator="==", value="Lima")], title="Lima")
    arequipa = _dist(
        filters=[DataFilter(column="ciudad", operator="==", value="Arequipa")], title="Arequipa"
    )
    assert len(finalize_plans([lima, arequipa], {})) == 2


# ── F1.1: duplicado exacto (misma data, distinto título) se colapsa ───
def test_exact_duplicate_collapses() -> None:
    assert len(finalize_plans([_kpi("ventas", "A"), _kpi("ventas", "B")], {})) == 1


# ── F1.1: normalización de tipos (1 vs 1.0 vs "1") colapsa duplicados ─
def test_filter_type_normalization_collapses() -> None:
    as_int = _dist(filters=[DataFilter(column="anio", operator="==", value=2024)], title="i")
    as_str = _dist(filters=[DataFilter(column="anio", operator="==", value="2024")], title="s")
    as_float = _dist(filters=[DataFilter(column="anio", operator="==", value=2024.0)], title="f")
    assert len(finalize_plans([as_int, as_str, as_float], {})) == 1


# ── F1.1: orden de lista en filtros IN no crea falsos no-duplicados ───
def test_in_list_order_normalized() -> None:
    a = _dist(filters=[DataFilter(column="region", operator="in", value=["Sur", "Norte"])], title="a")
    b = _dist(filters=[DataFilter(column="region", operator="in", value=["Norte", "Sur"])], title="b")
    assert len(finalize_plans([a, b], {})) == 1


# ── F1.3: prompt estático usa kpi_card y heurísticas válidas ──────────
def test_unified_prompt_uses_valid_visual_values() -> None:
    instruction = get_unified_static_instruction()
    assert "kpi_card" in instruction
    # 'kpi' solo (sin _card) ya no debe aparecer como valor sugerido
    assert re.search(r"\bkpi\b", instruction) is None
    for visual in ("scatter_plot", "boxplot", "heatmap", "waterfall", "dual_axis_chart"):
        assert visual in instruction, f"Falta {visual} en el prompt visual"
