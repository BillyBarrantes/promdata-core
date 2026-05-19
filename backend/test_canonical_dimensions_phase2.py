import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '.'))

from app.services.data_engine import DataEngine


def test_case_variants_collapse_into_single_dimension_bucket():
    df = pd.DataFrame({
        'Fecha': ['2024-01-01', '2024-01-02', '2024-01-03', '2024-01-04', '2024-01-05', '2024-01-06'],
        'provincia de venta': ['Lima', 'lima', 'LIMA', 'Huancayo', 'HUANCAYO', 'arequipa'],
        'Cantidad vendida': [100, 200, 300, 80, 120, 75],
        'Total venta PEN': [10.0, 20.0, 30.0, 8.0, 12.0, 7.5],
    })

    main_df, _, _, _, _ = DataEngine.unify_and_clean({'principal': df}, {})
    aggregated = (
        main_df.groupby('provincia_de_venta', dropna=False)['cantidad_vendida']
        .sum()
        .to_dict()
    )
    contract = main_df.attrs.get('semantic_contract', {})

    assert aggregated == {
        'Lima': 600.0,
        'Huancayo': 200.0,
        'Arequipa': 75.0,
    }
    assert contract.get('canonical_dimensions', {}).get('provincia_de_venta', {}).get('collapsed_variant_groups') == 2


def test_code_like_dimensions_remain_stable():
    df = pd.DataFrame({
        'Fecha de stock': ['2021-07-31', '2021-07-31', '2021-07-31'],
        'Tipo almacén': ['130', '400', '810'],
        'Material': ['MAT-001', 'MAT-002', 'MAT-003'],
        'Stock disponible': [100, 80, 60],
    })

    main_df, _, _, _, _ = DataEngine.unify_and_clean({'principal': df}, {})

    assert sorted(main_df['tipo_almacen'].astype(str).tolist()) == ['130', '400', '810']
