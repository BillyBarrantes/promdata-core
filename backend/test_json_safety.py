"""Frontera de serialización — blindaje contra floats no finitos (TDD).

Escenario real que motiva este fix (incident 2026-09):

1. El worker persiste ``results_json`` con ``json.dumps(..., cls=CustomEncoder)``.
   El default de ``json.dumps`` es ``allow_nan=True``, así que un ``NaN``/
   ``Infinity`` en el payload se escribe como literal inválido.
2. ``GET /api/v1/tasks/{id}`` devuelve ese payload. Starlette serializa con
   ``allow_nan=False`` → ``ValueError: Out of range float values are not JSON
   compliant`` → HTTP 500 sin headers CORS → el navegador lo ve como
   ``TypeError: Failed to fetch`` y el frontend agota el deadline de 180s
   mostrando "El análisis superó el tiempo de espera" (falso: el análisis
   terminó en 11s).

Estos tests fijan el contrato:
  * ``json_safe`` convierte no finitos anidados a ``None`` (y normaliza tipos
    numpy/pandas), sin tocar el resto del árbol.
  * ``dumps_safe`` nunca emite ``NaN``/``Infinity``.
  * El payload saneado atraviesa ``JSONResponse`` (el mismo que usa la API)
    sin excepción.
  * ``CustomEncoder``/``convert_keys_to_str`` permanecen intactos.
"""

import json
import math
import types

import numpy as np
import pandas as pd
import pytest
from starlette.responses import JSONResponse

from app.api import routes as routes_module
from app.core.serializers import (
    CustomEncoder,
    convert_keys_to_str,
    dumps_safe,
    has_non_finite,
    json_safe,
)


# ═══════════════════════════════════════════════════════════════════════
# 1. json_safe: no finitos → None (anidados)
# ═══════════════════════════════════════════════════════════════════════
def test_nested_non_finite_floats_become_none():
    payload = {
        "chart_options": [
            {"data": [{"name": "Ago-2026", "value": float("nan")},
                      {"name": "Sep-2026", "value": 12.5}]},
            {"data": [{"name": "x", "value": float("inf")},
                      {"name": "y", "value": float("-inf")}]},
        ],
        "metrics": {"growth": float("nan"), "total": 3},
        "traceability": {"list": [1, float("nan"), {"deep": float("inf")}]},
    }

    safe = json_safe(payload)

    assert safe["chart_options"][0]["data"][0]["value"] is None
    assert safe["chart_options"][0]["data"][1]["value"] == 12.5
    assert safe["chart_options"][1]["data"][0]["value"] is None
    assert safe["chart_options"][1]["data"][1]["value"] is None
    assert safe["metrics"]["growth"] is None
    assert safe["metrics"]["total"] == 3
    assert safe["traceability"]["list"][1] is None
    assert safe["traceability"]["list"][2]["deep"] is None


def test_json_safe_does_not_mutate_input():
    payload = {"value": float("nan"), "nested": {"v": [float("inf")]}}

    json_safe(payload)

    assert math.isnan(payload["value"])
    assert math.isinf(payload["nested"]["v"][0])


# ═══════════════════════════════════════════════════════════════════════
# 2. json_safe: tipos numpy/pandas
# ═══════════════════════════════════════════════════════════════════════
def test_numpy_scalars_are_normalized():
    safe = json_safe(
        {
            "np_nan": np.float64("nan"),
            "np_inf": np.float32("inf"),
            "np_int": np.int64(7),
            "np_bool": np.bool_(True),
            "np_finite": np.float64(1.25),
        }
    )

    assert safe["np_nan"] is None
    assert safe["np_inf"] is None
    assert safe["np_int"] == 7 and isinstance(safe["np_int"], int)
    assert safe["np_bool"] is True
    assert safe["np_finite"] == 1.25
    assert all(not isinstance(v, np.generic) for v in safe.values())


def test_pandas_missing_and_timestamps():
    ts = pd.Timestamp("2024-06-01 00:00:00")
    safe = json_safe({"nat": pd.NaT, "na": pd.NA, "ts": ts,
                      "series": pd.Series([1.0, float("nan")])})

    assert safe["nat"] is None
    assert safe["na"] is None
    assert safe["ts"] == "2024-06-01T00:00:00"
    assert safe["series"] == [1.0, None]


def test_dataframe_is_serialized_with_finiteness():
    df = pd.DataFrame({"a": [1.0, float("nan")], "b": ["x", "y"]})

    safe = json_safe(df)

    assert safe[0]["a"] == 1.0
    assert safe[1]["a"] is None


# ═══════════════════════════════════════════════════════════════════════
# 3. dumps_safe: estricto, sin NaN/Infinity
# ═══════════════════════════════════════════════════════════════════════
def test_dumps_safe_emits_valid_json_without_non_finite_tokens():
    payload = {"a": float("nan"), "b": [float("inf")], "c": np.float64("-inf")}

    out = dumps_safe(payload)

    assert "NaN" not in out
    assert "Infinity" not in out
    assert json.loads(out) == {"a": None, "b": [None], "c": None}


def test_raw_dumps_with_allow_nan_false_documents_the_bug():
    payload = {"value": float("nan")}

    with pytest.raises(ValueError):
        json.dumps(payload, allow_nan=False)

    assert dumps_safe(payload) == '{"value": null}'


# ═══════════════════════════════════════════════════════════════════════
# 4. Contrato API: JSONResponse (el que rompía) acepta el payload saneado
# ═══════════════════════════════════════════════════════════════════════
def test_json_response_renders_sanitized_payload():
    payload = {"status": "completed", "result": {"value": float("nan")}}

    with pytest.raises(ValueError):
        JSONResponse({"status": "completed", "result": payload["result"]})

    response = JSONResponse(
        {"status": "completed", "result": json_safe(payload["result"])}
    )

    assert response.status_code == 200
    assert json.loads(response.body) == {"status": "completed", "result": {"value": None}}


# ═══════════════════════════════════════════════════════════════════════
# 5. No regresión: CustomEncoder / convert_keys_to_str intactos
# ═══════════════════════════════════════════════════════════════════════
def test_existing_serializers_behaviour_is_preserved():
    assert convert_keys_to_str({1: {2: "x"}}) == {"1": {"2": "x"}}
    assert json.dumps({"p": pd.Period("2024-01")}, cls=CustomEncoder) == '{"p": "2024-01"}'
    assert json.dumps({"ts": pd.Timestamp("2024-01-01")}, cls=CustomEncoder) == (
        '{"ts": "2024-01-01 00:00:00"}'
    )


def test_has_non_finite_detects_only_real_violations():
    assert has_non_finite({"a": float("nan")}) is True
    assert has_non_finite({"a": [1, {"b": float("inf")}]}) is True
    assert has_non_finite(np.float32("nan")) is True
    assert has_non_finite({"a": 1.5, "b": [1, 2], "c": "texto", "d": None}) is False
    assert has_non_finite(pd.Series([1.0, 2.0])) is False


# ═══════════════════════════════════════════════════════════════════════
# 6. Endpoint de estado: fila legacy con NaN se responde 200 (no 500)
# ═══════════════════════════════════════════════════════════════════════
class _FakeQuery:
    def __init__(self, data):
        self._data = data

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def single(self):
        return self

    def execute(self):
        return types.SimpleNamespace(data=self._data)


class _FakeSupabase:
    def __init__(self, data):
        self._data = data

    def table(self, _name):
        return _FakeQuery(self._data)


def test_get_task_status_sanitizes_legacy_non_finite_payload(monkeypatch):
    legacy_payload = {
        "chart_options": [
            {"data": [{"name": "Ago-2026", "value": float("nan")}, {"name": "Sep", "value": 2.0}]}
        ],
        "metrics": {"growth": float("inf")},
    }
    fake_row = {"status": "completed", "results_json": legacy_payload}
    monkeypatch.setattr(
        routes_module,
        "_get_authenticated_user",
        lambda _token: (_FakeSupabase(fake_row), types.SimpleNamespace(id="user-1")),
    )

    response = routes_module.get_task_status(task_id="task-1", token="token-1")

    assert response["status"] == "completed"
    assert response["result"]["chart_options"][0]["data"][0]["value"] is None
    assert response["result"]["metrics"]["growth"] is None

    body = JSONResponse(response).body
    assert b"NaN" not in body and b"Infinity" not in body

