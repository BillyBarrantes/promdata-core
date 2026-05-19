import os
import sys

import pandas as pd
from pandas.api.types import is_numeric_dtype

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '.'))

from app.services.data_engine import DataEngine


def test_sales_and_quantity_columns_do_not_fall_into_identifier_shields():
    df = pd.DataFrame({
        'ID': ['P001', 'P002', 'P003', 'P004'],
        'Fecha': ['2024-01-01', '2024-01-02', '2024-01-03', '2024-01-04'],
        'Cantidad vendida': ['278', '79', '254', '298'],
        'Total venta PEN': ['44.21', '241.85', '257.77', '64.99'],
        'provincia de venta': ['Lima', 'LIMA', 'lima', 'Huancayo'],
    })

    main_df, _, _, currency_meta, schema_profile = DataEngine.unify_and_clean({'principal': df}, {})

    assert is_numeric_dtype(main_df['cantidad_vendida'])
    assert is_numeric_dtype(main_df['total_venta_pen'])
    assert schema_profile['cantidad_vendida']['role'] == 'metric'
    assert schema_profile['total_venta_pen']['role'] == 'metric'
    assert currency_meta.get('code') == 'PEN'


def test_identifier_shield_still_protects_real_codes():
    df = pd.DataFrame({
        'codigo_documento': ['0001', '0002', '0003', '0004'],
        'monto_total': ['10.5', '20.5', '30.5', '40.5'],
    })

    main_df, _, _, _, schema_profile = DataEngine.unify_and_clean({'principal': df}, {})

    assert main_df['codigo_documento'].astype(str).tolist() == ['0001', '0002', '0003', '0004']
    assert schema_profile['codigo_documento']['role'] == 'identifier'
    assert is_numeric_dtype(main_df['monto_total'])
    assert schema_profile['monto_total']['role'] == 'metric'
