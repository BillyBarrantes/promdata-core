import os
import sys
import uuid

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '.'))

from app.services.data_engine import DataEngine


def test_flow_dataset_contract_disables_snapshot_guard():
    df = pd.DataFrame({
        'ID': ['P001', 'P002', 'P003', 'P004', 'P005', 'P006'],
        'Fecha': ['2024-01-01', '2024-01-02', '2024-01-03', '2024-01-04', '2024-01-05', '2024-01-06'],
        'Cantidad vendida': [120, 240, 180, 320, 150, 215],
        'Total venta PEN': [12.5, 24.0, 18.3, 32.0, 15.2, 21.5],
        'provincia de venta': ['Lima', 'Lima', 'Huancayo', 'Arequipa', 'Pisco', 'Lima'],
    })

    main_df, _, _, _, _ = DataEngine.unify_and_clean({'principal': df}, {})
    contract = main_df.attrs.get('semantic_contract', {})

    assert contract.get('dataset_mode') == 'flow'
    assert contract.get('snapshot_guard_allowed') is False
    assert 'is_latest_snapshot' not in main_df.columns


def test_snapshot_dataset_contract_enables_latest_snapshot():
    df = pd.DataFrame({
        'Fecha de stock': [
            '2021-05-31', '2021-05-31',
            '2021-06-30', '2021-06-30',
            '2021-07-31', '2021-07-31',
        ],
        'Material': ['MAT-001', 'MAT-002', 'MAT-001', 'MAT-002', 'MAT-001', 'MAT-002'],
        'Tipo almacén': ['130', '400', '130', '400', '130', '400'],
        'Stock disponible': [100, 80, 110, 90, 130, 100],
    })

    main_df, _, _, _, _ = DataEngine.unify_and_clean({'principal': df}, {})
    contract = main_df.attrs.get('semantic_contract', {})

    assert contract.get('dataset_mode') == 'snapshot'
    assert contract.get('snapshot_guard_allowed') is True
    assert 'is_latest_snapshot' in main_df.columns
    assert int(main_df['is_latest_snapshot'].sum()) == 2


def test_semantic_contract_sidecar_roundtrip():
    df = pd.DataFrame({
        'Fecha de stock': [
            '2021-05-31', '2021-05-31',
            '2021-06-30', '2021-06-30',
            '2021-07-31', '2021-07-31',
        ],
        'Material': ['MAT-001', 'MAT-002', 'MAT-001', 'MAT-002', 'MAT-001', 'MAT-002'],
        'Tipo almacén': ['130', '400', '130', '400', '130', '400'],
        'Stock disponible': [100, 80, 110, 90, 130, 100],
    })

    main_df, _, _, _, _ = DataEngine.unify_and_clean({'principal': df}, {})
    file_id = f"semantic_contract_{uuid.uuid4().hex}"
    parquet_path = DataEngine.commit_to_parquet(main_df, file_id)
    if not parquet_path:
        pytest.skip("Parquet dependencies are not available in this environment.")
    contract = DataEngine.load_semantic_contract(parquet_path)

    assert parquet_path
    assert contract.get('dataset_mode') == 'snapshot'
    assert contract.get('snapshot_guard_allowed') is True
