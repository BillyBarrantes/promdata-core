from __future__ import annotations

import pandas as pd

from app.core.config import settings
from app.services.canonical_native_tabular_ingestion import build_native_frame_payload


def test_phase8_native_ingestion_keeps_full_analytics_rows_even_with_preview_cap(monkeypatch) -> None:
    monkeypatch.setattr(settings, "CANONICAL_NATIVE_TABULAR_MAX_ROWS", 2)
    monkeypatch.setattr(settings, "CANONICAL_NATIVE_TABULAR_MAX_COLUMNS", 2)
    monkeypatch.setattr(settings, "CANONICAL_NATIVE_TABULAR_ANALYTICS_MAX_ROWS", 1000)
    monkeypatch.setattr(settings, "CANONICAL_NATIVE_TABULAR_ANALYTICS_MAX_COLUMNS", 1000)

    frame = {
        "frame_id": "sheet::Hoja1",
        "label": "Hoja1",
        "sheet_name": "Hoja1",
        "dataframe": pd.DataFrame(
            [
                {"a": 1, "b": 10, "c": "x"},
                {"a": 2, "b": 20, "c": "y"},
                {"a": 3, "b": 30, "c": "z"},
                {"a": 4, "b": 40, "c": "w"},
                {"a": 5, "b": 50, "c": "v"},
            ]
        ),
        "metadata": {},
    }

    payload = build_native_frame_payload(frame)
    metadata = payload["metadata"]

    assert payload["row_count"] == 5
    assert payload["column_count"] == 3
    assert payload["column_names"] == ["a", "b", "c"]
    assert len(metadata["rows_payload"]) == 5
    assert metadata["preview_row_count"] == 2
    assert metadata["preview_column_count"] == 2
    assert metadata["preview_truncated_rows"] is True
    assert metadata["preview_truncated_columns"] is True
    assert metadata["truncated_rows"] is False
    assert metadata["truncated_columns"] is False


def test_phase8_native_ingestion_applies_analytics_cap_when_needed(monkeypatch) -> None:
    monkeypatch.setattr(settings, "CANONICAL_NATIVE_TABULAR_MAX_ROWS", 1000)
    monkeypatch.setattr(settings, "CANONICAL_NATIVE_TABULAR_MAX_COLUMNS", 1000)
    monkeypatch.setattr(settings, "CANONICAL_NATIVE_TABULAR_ANALYTICS_MAX_ROWS", 3)
    monkeypatch.setattr(settings, "CANONICAL_NATIVE_TABULAR_ANALYTICS_MAX_COLUMNS", 2)

    frame = {
        "frame_id": "sheet::Hoja1",
        "label": "Hoja1",
        "sheet_name": "Hoja1",
        "dataframe": pd.DataFrame(
            [
                {"a": 1, "b": 10, "c": "x"},
                {"a": 2, "b": 20, "c": "y"},
                {"a": 3, "b": 30, "c": "z"},
                {"a": 4, "b": 40, "c": "w"},
                {"a": 5, "b": 50, "c": "v"},
            ]
        ),
        "metadata": {},
    }

    payload = build_native_frame_payload(frame)
    metadata = payload["metadata"]

    assert payload["row_count"] == 3
    assert payload["column_count"] == 2
    assert payload["column_names"] == ["a", "b"]
    assert len(metadata["rows_payload"]) == 3
    assert all(set(row.keys()) == {"a", "b"} for row in metadata["rows_payload"])
    assert metadata["truncated_rows"] is True
    assert metadata["truncated_columns"] is True
