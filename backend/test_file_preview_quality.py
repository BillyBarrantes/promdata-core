import os
import sys

import pandas as pd
import numpy as np


sys.path.append(os.path.join(os.getcwd(), "backend"))

from app.services.file_preview import _build_quality_profile, infer_series_type


def test_quality_profile_preserves_temporal_grain_for_duplicates():
    df = pd.DataFrame(
        {
            "fecha_corte": ["2021-03-31", "2021-04-30", "2021-04-30"],
            "material": ["1001001", "1001001", "1001001"],
            "lote": ["000321", "000321", "000321"],
            "stock_disponible": ["15", "15", "15"],
        }
    )

    profile = _build_quality_profile(df, list(df.columns))

    assert profile["duplicate_row_count"] == 1
    assert profile["duplicate_row_ratio"] == round(1 / 3, 4)


def test_quality_profile_separates_identifiers_from_metrics():
    df = pd.DataFrame(
        {
            "fecha_corte": ["2021-03-31 00:00:00"] * 12,
            "material": [
                "1037762",
                "1047908",
                "1024442",
                "1046962",
                "1043958",
                "1038130",
                "1047648",
                "1047648",
                "1054890",
                "1054891",
                "1014201",
                "1036960",
            ],
            "lote": [
                "003021",
                "004520",
                "003220",
                "003420",
                "003720",
                "090920",
                "201020",
                "201020",
                "003121",
                "003121",
                "001821",
                "002021",
            ],
            "ubicacion": [
                "SALDOS",
                "050116",
                "021103",
                "021123",
                "PAMPAS",
                "050031",
                "050177",
                "050215",
                "ENT",
                "ENT",
                "PASILLO",
                "PASILLO",
            ],
            "stock_disponible": ["3", "199", "3754", "385", "66", "307", "1600", "891", "210", "210", "1280", "9000"],
        }
    )

    profile = _build_quality_profile(df, list(df.columns))

    invalid_date_columns = {
        column
        for alert in profile["alerts"]
        if alert["code"] == "invalid_dates"
        for column in alert["affected_columns"]
    }
    outlier_columns = {
        column
        for alert in profile["alerts"]
        if alert["code"] == "extreme_outliers"
        for column in alert["affected_columns"]
    }

    assert infer_series_type(df["fecha_corte"]) == "datetime"
    assert infer_series_type(df["material"]) == "identifier"
    assert infer_series_type(df["lote"]) == "identifier"
    assert infer_series_type(df["stock_disponible"]) == "number"

    assert "lote" not in invalid_date_columns
    assert "ubicacion" not in invalid_date_columns
    assert "material" not in outlier_columns
    assert "lote" not in outlier_columns
    assert "stock_disponible" in outlier_columns


def test_integer_code_morphology_outweighs_low_distinct_ratio():
    lote_values = [f"{2000 + code:04d}" for code in range(180)]
    lote_series = pd.Series(np.repeat(lote_values, 8))

    stock_values = [str(value) for value in range(120, 920)]
    stock_series = pd.Series(np.repeat(stock_values, 2))

    assert infer_series_type(lote_series) == "identifier"
    assert infer_series_type(stock_series) == "number"


def test_integer_codes_allow_small_non_numeric_noise_without_becoming_metric():
    lote_values = [f"{2000 + code:04d}" for code in range(120)]
    lote_series = pd.Series((lote_values * 6) + ["SALDOS", "PAMPAS", "ENT", "X", "A1"] * 8)

    assert infer_series_type(lote_series) == "identifier"
