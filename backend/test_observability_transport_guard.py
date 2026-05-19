import io
import json
import logging

import pandas as pd

from app.core.arrow_utils import (
    evaluate_dataframe_arrow_transport,
    evaluate_records_arrow_transport,
)
from app.core.structured_logging import emit_structured_log


def assert_structured_log_contract() -> None:
    logger = logging.getLogger("promdata.structured")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    try:
        emit_structured_log(
            "phase8_guard_event",
            task_id="abc-123",
            nested={"mode": "json", "count": 2},
            values=[1, 2, 3],
        )
    finally:
        logger.removeHandler(handler)

    payload = json.loads(stream.getvalue().strip())
    assert payload["event"] == "phase8_guard_event", "El evento estructurado debe conservar el nombre estable"
    assert payload["task_id"] == "abc-123", "Los campos simples deben preservarse"
    assert payload["nested"]["mode"] == "json", "Los campos anidados deben serializarse de forma segura"
    assert payload["values"] == [1, 2, 3], "Las listas deben permanecer serializables"
    assert "ts" in payload and isinstance(payload["ts"], str), "Todo log estructurado debe incluir timestamp"


def assert_records_transport_contract() -> None:
    tiny_records = [
        {"almacen": "Norte", "stock": 10},
        {"almacen": "Sur", "stock": 12},
    ]
    tiny_decision = evaluate_records_arrow_transport(tiny_records)
    assert tiny_decision["mode"] == "json", "Payloads pequeños deben ir por JSON"
    assert "payload ligero" in tiny_decision["reason"], "La decisión liviana debe explicitarse"

    large_records = [
        {
            "almacen": f"Almacen {index}",
            "material": f"Material {index}",
            "stock": index,
            "ubicacion": f"UB-{index:05d}",
            "fecha": "2021-07-31",
        }
        for index in range(2500)
    ]
    large_decision = evaluate_records_arrow_transport(large_records)
    assert large_decision["mode"] == "arrow", "Payloads medianos/grandes deben activar Arrow"
    assert any(token in large_decision["reason"] for token in ("rows=", "cells=", "bytes≈")), (
        "La decisión Arrow debe explicar el motivo cuantitativo"
    )


def assert_dataframe_transport_contract() -> None:
    df = pd.DataFrame(
        {
            "almacen": [f"Almacen {index}" for index in range(1500)],
            "material": [f"Material {index}" for index in range(1500)],
            "stock": list(range(1500)),
            "fecha": ["2021-07-31"] * 1500,
        }
    )
    decision = evaluate_dataframe_arrow_transport(df)
    assert decision["mode"] == "arrow", "DataFrames densos deben activar Arrow"
    assert decision["estimated_cells"] == len(df) * len(df.columns), "El cálculo de cells debe ser consistente"


def run_assertions() -> None:
    assert_structured_log_contract()
    assert_records_transport_contract()
    assert_dataframe_transport_contract()


if __name__ == "__main__":
    run_assertions()
    print("OK: observability + transport guard")
