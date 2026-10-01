import pytest
import pandas as pd
import numpy as np
from unittest.mock import MagicMock

from app.core.rate_limit import check_rate_limits_batch
from app.services.semantic_translator.unified_translator import (
    ClassificationResult,
    UnifiedAnalysisOutput,
)
from app.core.semantic_grammar import AnalysisPlan, VisualProtocol


def test_unified_translator_schemas():
    """Verifica que el schema Pydantic de UnifiedAnalysisOutput sea compatible con AnalysisPlan."""
    from app.core.semantic_grammar import DescriptiveIntent, MetricUnit
    plan = AnalysisPlan(
        title="Ventas por Región",
        rationale="Muestra la suma de ventas por región",
        main_intent=DescriptiveIntent(
            metrics=["ventas"],
            group_by=["region"],
            aggregation="sum",
            metric_unit=MetricUnit.CURRENCY,
            rationale="Analiza las ventas agregadas por cada región",
        ),
        visual_protocol=VisualProtocol.BAR,
    )
    classification = ClassificationResult(
        route="SIMPLE",
        detected_intent="descriptive",
        confidence=0.98,
        reason_codes=["direct_query"],
    )
    output = UnifiedAnalysisOutput(
        classification=classification,
        plans=[plan],
    )
    dump = output.model_dump(mode="json")
    assert dump["classification"]["route"] == "SIMPLE"
    assert len(dump["plans"]) == 1

    # Round-trip validation
    restored = UnifiedAnalysisOutput.model_validate(dump)
    assert len(restored.plans) == 1
    assert restored.classification.confidence == 0.98


def test_clean_regional_vectorization():
    """Verifica que la vectorización produzca exactamente el mismo resultado que la función original clean_regional."""
    raw_values = [
        "1.234,56",       # EU format
        "1,234.56",       # US format
        "1234,56",        # Comma only
        "1234.56",        # Dot only
        "1.000",          # EU single dot
        "1,000",          # US single comma
        "",               # Empty string
        None,             # None
        "$1,250.50",      # Currency US
        "S/ 1.250,50",    # Currency EU
        "-1.234,56",      # Negative EU
        "1.234.567,89",   # Multi-thousand EU
    ]
    df = pd.DataFrame({"col": raw_values})

    # 1. Ejecución original con apply
    clean_col_orig = df["col"].astype(str).str.replace(r'\s+[-]\s+', ' ', regex=True)
    clean_col_orig = clean_col_orig.str.replace(r'[^\d.,-]', '', regex=True)

    def clean_regional(val):
        if not val: return val
        if ',' in val and '.' in val:
            if val.rfind(',') > val.rfind('.'): return val.replace('.', '').replace(',', '.')
            else: return val.replace(',', '')
        elif ',' in val: return val.replace(',', '.')
        return val

    orig_result = pd.to_numeric(clean_col_orig.apply(clean_regional), errors='coerce')

    # 2. Ejecución vectorizada
    clean_col = df["col"].astype(str).str.replace(r'\s+[-]\s+', ' ', regex=True)
    clean_col = clean_col.str.replace(r'[^\d.,-]', '', regex=True)

    has_comma = clean_col.str.contains(',', regex=False, na=False)
    has_dot = clean_col.str.contains('.', regex=False, na=False)
    both = has_comma & has_dot

    eu_format = both & (clean_col.str.rfind(',') > clean_col.str.rfind('.'))
    us_format = both & ~eu_format
    only_comma = has_comma & ~has_dot

    result = clean_col.copy()
    result[eu_format] = result[eu_format].str.replace('.', '', regex=False).str.replace(',', '.', regex=False)
    result[us_format] = result[us_format].str.replace(',', '', regex=False)
    result[only_comma] = result[only_comma].str.replace(',', '.', regex=False)

    vec_result = pd.to_numeric(result, errors='coerce')

    # Verificar coincidencia 1 a 1 entre original y vectorizado
    for actual, expected in zip(vec_result, orig_result):
        if np.isnan(expected):
            assert np.isnan(actual)
        else:
            assert actual == pytest.approx(expected, 0.001)


def test_check_rate_limits_batch_disabled(monkeypatch):
    """Verifica que si el rate limit está deshabilitado en config, retorna True inmediatamente."""
    from fastapi import Request
    from app.core.config import settings

    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", False)

    mock_request = MagicMock(spec=Request)
    mock_request.client = MagicMock()
    mock_request.client.host = "127.0.0.1"

    allowed, info = check_rate_limits_batch(
        request=mock_request,
        token="",
        scope="analyze",
        rate_limit=10,
        rate_window=60,
        burst_limit=5,
        burst_window=10,
        max_concurrent=2,
        concurrency_ttl=60,
    )
    assert allowed is True
    assert info.get("slot_acquired") is False
