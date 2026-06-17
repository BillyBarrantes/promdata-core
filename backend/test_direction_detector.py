"""
Tests for direction_detector.py — Enterprise-grade direction column detection.
"""

import pytest
import sys
import os

# Ensure backend is in path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.services.direction_detector import (
    detect_direction_columns,
    should_split_by_flow_direction,
    _is_antonym_pair,
    _normalize_value,
    _extract_unique_values,
)


# ═══════════════════════════════════════════════════════════════════
# UNIT 1: Private helpers
# ═══════════════════════════════════════════════════════════════════

class TestNormalizeValue:
    def test_string_lowercase(self):
        assert _normalize_value("Ingreso") == "ingreso"

    def test_none_returns_empty(self):
        assert _normalize_value(None) == ""

    def test_strips_whitespace(self):
        assert _normalize_value("  Egreso  ") == "egreso"


class TestExtractUniqueValues:
    def test_plain_list(self):
        assert _extract_unique_values(["Ingreso", "Egreso", "Ingreso"]) == {"ingreso", "egreso"}

    def test_with_none(self):
        assert _extract_unique_values(["Ingreso", None, "Egreso"]) == {"ingreso", "egreso"}


class TestIsAntonymPair:
    def test_exact_pair_ingreso_egreso(self):
        is_pair, conf = _is_antonym_pair({"ingreso", "egreso"})
        assert is_pair is True
        assert conf == 1.0

    def test_exact_pair_income_expense(self):
        is_pair, conf = _is_antonym_pair({"income", "expense"})
        assert is_pair is True
        assert conf == 1.0

    def test_not_a_pair(self):
        is_pair, conf = _is_antonym_pair({"foo", "bar"})
        assert is_pair is False
        assert conf == 0.0

    def test_pair_with_neutral_value(self):
        is_pair, conf = _is_antonym_pair({"ingreso", "egreso", "neutral"})
        assert is_pair is True
        assert conf == 0.95

    def test_pair_with_two_neutral_values(self):
        is_pair, conf = _is_antonym_pair({"ingreso", "egreso", "neutral", "otro"})
        assert is_pair is True
        assert conf == 0.90

    def test_too_many_values(self):
        is_pair, conf = _is_antonym_pair({"ingreso", "egreso", "a", "b", "c", "d"})
        assert is_pair is False
        assert conf == 0.0

    def test_single_value(self):
        is_pair, conf = _is_antonym_pair({"ingreso"})
        assert is_pair is False
        assert conf == 0.0


# ═══════════════════════════════════════════════════════════════════
# UNIT 2: detect_direction_columns
# ═══════════════════════════════════════════════════════════════════

class TestDetectDirectionColumns:
    def test_detects_ingreso_egreso(self):
        schema = {
            "tipo_movimiento": {"type": "categorical", "role": "dimension", "cardinality": 2},
        }
        sample = {"tipo_movimiento": ["Ingreso", "Egreso", "Ingreso", "Egreso"]}
        
        result = detect_direction_columns(schema, sample)
        
        assert len(result) == 1
        assert result[0]["column_name"] == "tipo_movimiento"
        assert result[0]["confidence"] == 1.0
        assert result[0]["detected_pair"] == {"ingreso", "egreso"}

    def test_detects_entrada_salida(self):
        schema = {
            "tipo": {"type": "categorical", "role": "dimension", "cardinality": 2},
        }
        sample = {"tipo": ["Entrada", "Salida", "Entrada"]}
        
        result = detect_direction_columns(schema, sample)
        
        assert len(result) == 1
        assert result[0]["detected_pair"] == {"entrada", "salida"}

    def test_detects_buy_sell(self):
        schema = {
            "direction": {"type": "categorical", "role": "identifier", "cardinality": 2},
        }
        sample = {"direction": ["Buy", "Sell", "Buy"]}
        
        result = detect_direction_columns(schema, sample)
        
        assert len(result) == 1
        assert result[0]["detected_pair"] == {"buy", "sell"}

    def test_detects_with_neutral_value(self):
        schema = {
            "tipo_movimiento": {"type": "categorical", "role": "dimension", "cardinality": 3},
        }
        sample = {"tipo_movimiento": ["Ingreso", "Egreso", "Neutro", "Ingreso"]}
        
        result = detect_direction_columns(schema, sample)
        
        assert len(result) == 1
        assert result[0]["confidence"] == 0.95

    def test_does_not_detect_non_direction(self):
        schema = {
            "centro_costo": {"type": "categorical", "role": "dimension", "cardinality": 5},
        }
        sample = {"centro_costo": ["CC-Marketing", "CC-IT", "CC-Admin", "CC-Ventas", "CC-Ops"]}
        
        result = detect_direction_columns(schema, sample)
        
        assert len(result) == 0

    def test_does_not_detect_high_cardinality(self):
        schema = {
            "tipo_movimiento": {"type": "categorical", "role": "dimension", "cardinality": 10},
        }
        sample = {"tipo_movimiento": ["Ingreso", "Egreso"] * 5}
        
        result = detect_direction_columns(schema, sample)
        
        assert len(result) == 0

    def test_respects_confidence_threshold(self):
        schema = {
            "tipo_movimiento": {"type": "categorical", "role": "dimension", "cardinality": 3},
        }
        sample = {"tipo_movimiento": ["Ingreso", "Egreso", "Neutro"]}
        
        # With threshold 0.95, should detect (confidence is exactly 0.95)
        result = detect_direction_columns(schema, sample, confidence_threshold=0.95)
        assert len(result) == 1
        
        # With threshold 0.96, should NOT detect
        result = detect_direction_columns(schema, sample, confidence_threshold=0.96)
        assert len(result) == 0

    def test_detects_from_schema_unique_values(self):
        schema = {
            "tipo_movimiento": {
                "type": "categorical",
                "role": "dimension",
                "cardinality": 2,
                "unique_values": ["Ingreso", "Egreso"],
            },
        }
        
        result = detect_direction_columns(schema)
        
        assert len(result) == 1
        assert result[0]["column_name"] == "tipo_movimiento"


# ═══════════════════════════════════════════════════════════════════
# UNIT 3: should_split_by_flow_direction
# ═══════════════════════════════════════════════════════════════════

class TestShouldSplitByFlowDirection:
    def test_should_split_when_detected(self):
        schema = {
            "tipo_movimiento": {"type": "categorical", "role": "dimension", "cardinality": 2},
        }
        sample = {"tipo_movimiento": ["Ingreso", "Egreso"]}
        
        decision = should_split_by_flow_direction(schema, sample)
        
        assert decision["should_split"] is True
        assert decision["column_name"] == "tipo_movimiento"
        assert decision["confidence"] == 1.0

    def test_should_not_split_when_no_direction(self):
        schema = {
            "centro_costo": {"type": "categorical", "role": "dimension", "cardinality": 5},
        }
        sample = {"centro_costo": ["CC-1", "CC-2", "CC-3", "CC-4", "CC-5"]}
        
        decision = should_split_by_flow_direction(schema, sample)
        
        assert decision["should_split"] is False
        assert decision["column_name"] is None
        assert decision["confidence"] == 0.0

    def test_empty_schema_returns_false(self):
        decision = should_split_by_flow_direction({}, {})
        
        assert decision["should_split"] is False
        assert decision["column_name"] is None

    def test_multiple_directions_picks_best(self):
        schema = {
            "tipo_movimiento": {"type": "categorical", "role": "dimension", "cardinality": 2},
            "tipo": {"type": "categorical", "role": "dimension", "cardinality": 2},
        }
        sample = {
            "tipo_movimiento": ["Ingreso", "Egreso"],
            "tipo": ["Entrada", "Salida"],
        }
        
        decision = should_split_by_flow_direction(schema, sample)
        
        assert decision["should_split"] is True
        assert decision["column_name"] in {"tipo_movimiento", "tipo"}
        assert decision["confidence"] == 1.0


# ═══════════════════════════════════════════════════════════════════
# UNIT 4: Edge cases
# ═══════════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_numeric_column_skipped(self):
        schema = {
            "monto": {"type": "numeric", "role": "metric", "cardinality": 1000},
        }
        sample = {"monto": [100.0, 200.0, 300.0]}
        
        result = detect_direction_columns(schema, sample)
        assert len(result) == 0

    def test_case_insensitive_detection(self):
        schema = {
            "tipo": {"type": "categorical", "role": "dimension", "cardinality": 2},
        }
        sample = {"tipo": ["INGRESO", "EGRESO", "ingreso"]}
        
        result = detect_direction_columns(schema, sample)
        assert len(result) == 1
        assert result[0]["detected_pair"] == {"ingreso", "egreso"}

    def test_whitespace_handling(self):
        schema = {
            "tipo": {"type": "categorical", "role": "dimension", "cardinality": 2},
        }
        sample = {"tipo": [" Ingreso ", "  Egreso  "]}
        
        result = detect_direction_columns(schema, sample)
        assert len(result) == 1

    def test_none_values_ignored(self):
        schema = {
            "tipo": {"type": "categorical", "role": "dimension", "cardinality": 2},
        }
        sample = {"tipo": ["Ingreso", None, "Egreso", None]}
        
        result = detect_direction_columns(schema, sample)
        assert len(result) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
