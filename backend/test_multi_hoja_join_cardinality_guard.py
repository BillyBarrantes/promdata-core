from __future__ import annotations

import os
import pandas as pd
import pytest

from app.core.canonical_artifacts import (
    ArtifactAvailabilityStatus,
    ArtifactOperationalMode,
    ArtifactSupportLevel,
    ArtifactSourceKind,
    CanonicalArtifactBundle,
    CanonicalSourceManifest,
    CanonicalTabularFrame,
)
from app.core.config import settings
from app.core.semantic_grammar import AnalysisPlan, DescriptiveIntent
from app.services.canonical_bundle_orchestrator import infer_frame_relations
from app.services.canonical_shadow_metric_validity_gate import (
    _downgraded_role,
    apply_canonical_shadow_metric_validity_gate,
)
from app.services.ibis_engine import IbisEngine


def _manifest() -> CanonicalSourceManifest:
    return CanonicalSourceManifest(
        file_name="test-multi-hoja.xlsx",
        extension="xlsx",
        source_kind=ArtifactSourceKind.SPREADSHEET,
        support_level=ArtifactSupportLevel.FULL_ANALYTICS,
        availability_status=ArtifactAvailabilityStatus.ACTIVE,
        preferred_mode=ArtifactOperationalMode.ANALYTICAL,
        analytics_ready=True,
    )


def test_orchestrator_rejects_low_cardinality_join_key():
    """Fix A: _build_join_relation() rechaza columnas con <3 valores unicos
    en el sample combinado, prefiriendo el join key de alta cardinalidad."""
    bundle = CanonicalArtifactBundle(
        source_manifest=_manifest(),
        tabular_frames=[
            CanonicalTabularFrame(
                frame_id="ingresos",
                label="Ingresos",
                row_count=100,
                column_count=3,
                column_names=["factura_id", "tipo_movimiento", "monto"],
                extraction_confidence=0.95,
                metadata={"sample_rows": [
                    ["FAC-001", "Ingreso", "1500"],
                    ["FAC-002", "Ingreso", "2200"],
                    ["FAC-003", "Egreso", "800"],
                    ["FAC-004", "Ingreso", "1900"],
                    ["FAC-005", "Egreso", "450"],
                ]},
            ),
            CanonicalTabularFrame(
                frame_id="detalles",
                label="Detalles",
                row_count=100,
                column_count=3,
                column_names=["factura_id", "tipo_movimiento", "saldo"],
                extraction_confidence=0.95,
                metadata={"sample_rows": [
                    ["FAC-001", "Ingreso", "500"],
                    ["FAC-002", "Ingreso", "800"],
                    ["FAC-003", "Egreso", "200"],
                    ["FAC-004", "Ingreso", "700"],
                    ["FAC-005", "Egreso", "150"],
                ]},
            ),
        ],
    )
    relations = infer_frame_relations(bundle)
    assert len(relations) == 1
    assert relations[0].join_keys == ["factura_id"]
    assert relations[0].relation_type == "likely_join"
    assert relations[0].confidence > 0.7


def test_orchestrator_returns_none_when_only_low_cardinality_keys():
    """Fix A: Si la unica columna compartida tiene <3 valores unicos en
    el sample, _build_join_relation() retorna None (sin join candidato)."""
    bundle = CanonicalArtifactBundle(
        source_manifest=_manifest(),
        tabular_frames=[
            CanonicalTabularFrame(
                frame_id="ingresos",
                label="Ingresos",
                row_count=100,
                column_count=2,
                column_names=["tipo_movimiento", "monto"],
                extraction_confidence=0.95,
                metadata={"sample_rows": [
                    ["Ingreso", "1500"],
                    ["Egreso", "800"],
                    ["Ingreso", "2200"],
                    ["Egreso", "450"],
                    ["Ingreso", "1900"],
                ]},
            ),
            CanonicalTabularFrame(
                frame_id="detalles",
                label="Detalles",
                row_count=100,
                column_count=2,
                column_names=["tipo_movimiento", "saldo"],
                extraction_confidence=0.95,
                metadata={"sample_rows": [
                    ["Ingreso", "500"],
                    ["Egreso", "200"],
                    ["Ingreso", "800"],
                    ["Egreso", "150"],
                    ["Ingreso", "700"],
                ]},
            ),
        ],
    )
    relations = infer_frame_relations(bundle)
    assert len(relations) == 0


def test_ibis_engine_rejects_low_cardinality_join(capsys):
    """Fixes B+C+D: cardinality guard bloquea JOIN con llave de baja
    cardinalidad (tipo_movimiento con 2 valores en 100 filas). El sistema
    se degrada controladamente y ejecuta solo la tabla primaria."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        primary_df = pd.DataFrame({
            "tipo_movimiento": ["Ingreso"] * 50 + ["Egreso"] * 50,
            "monto": [float(1000 + i) for i in range(100)],
        })
        related_df = pd.DataFrame({
            "tipo_movimiento": ["Ingreso"] * 50 + ["Egreso"] * 50,
            "saldo": [float(500 + i) for i in range(100)],
        })

        primary_path = os.path.join(tmpdir, "primary.parquet")
        related_path = os.path.join(tmpdir, "related.parquet")
        primary_df.to_parquet(primary_path)
        related_df.to_parquet(related_path)

        plan = AnalysisPlan(
            main_intent=DescriptiveIntent(
                rationale="test multi-hoja con baja cardinalidad",
                metrics=["monto"],
                group_by=["tipo_movimiento"],
                aggregation="sum",
            ),
            title="Test Multi-Hoja",
            join_keys=["tipo_movimiento"],
            primary_frame_id="primary",
            related_frame_ids=["related"],
        )

        result = IbisEngine.execute_plan(
            parquet_path=primary_path,
            plan=plan,
            recipe_mode=True,
            related_parquets={"related": related_path},
        )

        captured = capsys.readouterr()
        output = captured.out

        # Fix D: debe mostrar mensaje diagnostico de degradacion controlada
        assert "Degradacion controlada" in output or "Degradaci" in output, (
            f"Falta mensaje de degradacion. Output:\n{output}"
        )
        assert "JOIN abortado" in output

        # Fix B+C: el JOIN se aborto pero el analisis se completo con tabla primaria
        assert "error" not in result, (
            f"execute_plan retorno error inesperado: {result}"
        )
        assert result.get("type") == "echarts"


def test_ibis_engine_allows_high_cardinality_join(capsys):
    """Fixes B+C: cardinality guard PERMITE JOIN con llave de alta
    cardinalidad (factura_id con 100 valores unicos en 100 filas)."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        primary_df = pd.DataFrame({
            "factura_id": [f"FAC-{i:03d}" for i in range(100)],
            "tipo_movimiento": ["Ingreso"] * 50 + ["Egreso"] * 50,
            "monto": [float(1000 + i) for i in range(100)],
        })
        related_df = pd.DataFrame({
            "factura_id": [f"FAC-{i:03d}" for i in range(100)],
            "tipo_movimiento": ["Ingreso"] * 50 + ["Egreso"] * 50,
            "saldo": [float(500 + i) for i in range(100)],
        })

        primary_path = os.path.join(tmpdir, "primary.parquet")
        related_path = os.path.join(tmpdir, "related.parquet")
        primary_df.to_parquet(primary_path)
        related_df.to_parquet(related_path)

        plan = AnalysisPlan(
            main_intent=DescriptiveIntent(
                rationale="test multi-hoja con alta cardinalidad",
                metrics=["monto"],
                group_by=["tipo_movimiento"],
                aggregation="sum",
            ),
            title="Test Multi-Hoja",
            join_keys=["factura_id"],
            primary_frame_id="primary",
            related_frame_ids=["related"],
        )

        result = IbisEngine.execute_plan(
            parquet_path=primary_path,
            plan=plan,
            recipe_mode=True,
            related_parquets={"related": related_path},
        )

        captured = capsys.readouterr()
        output = captured.out

        assert "JOIN gobernado por contrato" in output, (
            f"Falta mensaje de JOIN exitoso. Output:\n{output}"
        )
        assert "factura_id" in output

        assert "error" not in result, (
            f"execute_plan retorno error inesperado: {result}"
        )
        assert result.get("type") == "echarts"


def test_ibis_engine_llm_keys_rejected_fallback_to_heuristic(capsys):
    """Fix C: cuando Layer 1-2 (LLM keys) son todas baja cardinalidad, el
    sistema cae a Layer 3 (heuristico). Si ahi tampoco hay keys validas,
    se degrada controladamente."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        primary_df = pd.DataFrame({
            "tipo_movimiento": ["Ingreso"] * 50 + ["Egreso"] * 50,
            "categoria": ["A"] * 25 + ["B"] * 25 + ["C"] * 25 + ["D"] * 25,
            "monto": [float(1000 + i) for i in range(100)],
        })
        related_df = pd.DataFrame({
            "tipo_movimiento": ["Ingreso"] * 50 + ["Egreso"] * 50,
            "categoria": ["A"] * 25 + ["B"] * 25 + ["C"] * 25 + ["D"] * 25,
            "saldo": [float(500 + i) for i in range(100)],
        })

        primary_path = os.path.join(tmpdir, "primary.parquet")
        related_path = os.path.join(tmpdir, "related.parquet")
        primary_df.to_parquet(primary_path)
        related_df.to_parquet(related_path)

        plan = AnalysisPlan(
            main_intent=DescriptiveIntent(
                rationale="test LLM keys con baja cardinalidad",
                metrics=["monto"],
                group_by=["tipo_movimiento"],
                aggregation="sum",
            ),
            title="Test Multi-Hoja",
            join_keys=["tipo_movimiento"],
            primary_frame_id="primary",
            related_frame_ids=["related"],
        )

        result = IbisEngine.execute_plan(
            parquet_path=primary_path,
            plan=plan,
            recipe_mode=True,
            related_parquets={"related": related_path},
        )

        captured = capsys.readouterr()
        output = captured.out

        assert "LLM join key" in output, (
            f"Falta mensaje de LLM key rechazada. Output:\n{output}"
        )
        assert "Contrato RECHAZADO" in output
        assert "heur" in output  # heurístico
        assert "error" not in result, (
            f"execute_plan retorno error inesperado: {result}"
        )


def test_metric_gate_protects_trusted_numeric_from_downgrade():
    """Fix E: _downgraded_role() mantiene columnas numericas con nombre
    confiable como 'metric', no las degrada a dimension/identifier."""
    info = {
        "cardinality_ratio": 0.003,
        "cardinality": 3,
        "type": "numeric",
    }
    series = pd.Series([100.0, 200.0, 300.0, 400.0, 500.0], dtype="float64")

    role, role_type = _downgraded_role("multas_s", info, series)
    assert role == "metric", f"Expected 'metric', got '{role}'"
    assert role_type == "numeric", f"Expected 'numeric', got '{role_type}'"

    role, role_type = _downgraded_role("total_ingresos", info, series)
    assert role == "metric", f"Expected 'metric', got '{role}'"
    assert role_type == "numeric", f"Expected 'numeric', got '{role_type}'"

    role, role_type = _downgraded_role("importe_promedio", info, series)
    assert role == "metric", f"Expected 'metric', got '{role}'"
    assert role_type == "numeric", f"Expected 'numeric', got '{role_type}'"

    name = _downgraded_role("name", info, series)
    assert name == ("identifier", "id"), f"Expected identifier, got {name}"

    high_card_info = {"cardinality_ratio": 0.98, "cardinality": 98}
    unknown = _downgraded_role("desconocido", high_card_info, series)
    assert unknown == ("identifier", "id"), f"Expected identifier, got {unknown}"


def test_metric_gate_downgrade_backward_compat():
    """Fix E: _downgraded_role() funciona sin series (callers legacy)."""
    info = {"cardinality_ratio": 0.003, "cardinality": 3}

    trusted_no_series = _downgraded_role("multas_s", info)
    assert trusted_no_series == ("dimension", "categorical")

    suspicious = _downgraded_role("name", info)
    assert suspicious == ("identifier", "id")

    high_card = _downgraded_role("multas_s", {"cardinality_ratio": 0.98})
    assert high_card == ("identifier", "id")


def test_metric_gate_integration_with_dataframe(monkeypatch):
    """Fix E: apply_canonical_shadow_metric_validity_gate() preserva
    columnas numericas confiables como metric."""
    monkeypatch.setattr(
        settings, "CANONICAL_SHADOW_METRIC_VALIDITY_GATE_ENABLED", True
    )
    monkeypatch.setattr(
        settings, "CANONICAL_SHADOW_METRIC_VALIDITY_MIN_PARSEABLE_RATIO", 0.5
    )
    monkeypatch.setattr(
        settings, "CANONICAL_SHADOW_METRIC_PROMOTION_MIN_PARSEABLE_RATIO", 0.5
    )

    df = pd.DataFrame({
        "multas_s": [100.0, 200.0, 300.0, 100.0, 200.0],
        "tipo": ["A", "B", "A", "B", "A"],
        "fecha": pd.to_datetime([
            "2024-01-01", "2024-01-02", "2024-01-03",
            "2024-01-04", "2024-01-05",
        ]),
    })
    schema_profile = {
        "multas_s": {
            "role": "metric", "type": "numeric",
            "cardinality": 3, "cardinality_ratio": 0.6,
        },
        "tipo": {
            "role": "dimension", "type": "categorical",
            "cardinality": 2, "cardinality_ratio": 0.4,
        },
        "fecha": {
            "role": "date", "type": "temporal",
            "cardinality": 5, "cardinality_ratio": 1.0,
        },
    }

    result_df, result_profile, report = apply_canonical_shadow_metric_validity_gate(
        df, schema_profile
    )

    assert result_profile["multas_s"]["role"] == "metric"
    assert result_profile["multas_s"]["type"] == "numeric"
    assert "multas_s" in report["safe_metric_columns"]
    assert "multas_s" not in report["blocked_metric_columns"]

    assert result_profile["tipo"]["role"] == "dimension"
    assert "tipo" not in report["safe_metric_columns"]

    assert result_profile["fecha"]["role"] == "date"
