"""
test_upload_hardening.py — Blindaje de frontera de ingesta (Fase D).

Cubre:
  1. UploadFlowContractV1: tamaño, path traversal y prefijo RLS de usuario.
  2. parse_tabular_bytes_to_dfs: rechazo de archivos que exceden el límite antes de parsear.
  3. get_dataframe_from_storage: aplica el contrato de ingesta tras descargar.
  4. _read_upload_bounded: lectura en chunks con tope de bytes (413 al exceder).
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from fastapi import HTTPException

from app.api.routes import _read_upload_bounded
from app.core.analytical_contract import UploadFlowContractV1
from app.services import ingestion_validator as iv
from app.services.ingestion_validator import validate_upload_contract
from app.tasks.analysis_pipeline.data_loader import get_dataframe_from_storage

USER_ID = "11111111-1111-1111-1111-111111111111"


# ── 1. validate_upload_contract ────────────────────────────────────────────────

def test_validate_upload_contract_accepts_valid_upload():
    ok, reason = validate_upload_contract(
        UploadFlowContractV1(),
        file_name="ventas.csv",
        file_size_bytes=1024,
        storage_path=f"{USER_ID}/1700000000_ventas.csv",
        user_id=USER_ID,
    )
    assert ok is True
    assert reason is None


def test_validate_upload_contract_rejects_oversize():
    contract = UploadFlowContractV1()
    ok, reason = validate_upload_contract(
        contract,
        file_name="grande.csv",
        file_size_bytes=contract.max_size_bytes + 1,
        storage_path=f"{USER_ID}/1700000000_grande.csv",
        user_id=USER_ID,
    )
    assert ok is False
    assert reason and "supera" in reason


def test_validate_upload_contract_rejects_path_traversal():
    ok, _ = validate_upload_contract(
        UploadFlowContractV1(),
        file_name="x.csv",
        file_size_bytes=10,
        storage_path=f"../{USER_ID}/x.csv",
        user_id=USER_ID,
    )
    assert ok is False


def test_validate_upload_contract_rejects_foreign_user_prefix():
    ok, reason = validate_upload_contract(
        UploadFlowContractV1(),
        file_name="x.csv",
        file_size_bytes=10,
        storage_path=f"{USER_ID}/1700000000_x.csv",
        user_id="otro-usuario",
    )
    assert ok is False
    assert reason and "espacio aislado" in reason


# ── 2. parse_tabular_bytes_to_dfs (tope antes de parsear) ──────────────────────

def test_parse_rejects_oversized_payload(monkeypatch):
    monkeypatch.setattr(iv, "MAX_FILE_SIZE_BYTES", 10)
    with pytest.raises(ValueError):
        iv.parse_tabular_bytes_to_dfs(b"a,b\n1,2\n3,4\n", "x.csv")


def test_parse_accepts_small_payload():
    dfs = iv.parse_tabular_bytes_to_dfs(b"a,b\n1,2\n3,4\n", "x.csv")
    assert dfs
    assert any(len(df) == 2 for df in dfs.values())


# ── 3. get_dataframe_from_storage aplica el contrato ───────────────────────────

class _FakeResp:
    def __init__(self, data):
        self.data = data


class _FakeSupabase:
    def __init__(self, row):
        self._row = row
        self.storage = object()

    def table(self, _name):
        return self

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def single(self):
        return self

    def execute(self):
        return _FakeResp(self._row)


def test_get_dataframe_rejects_foreign_owner(monkeypatch):
    monkeypatch.setattr(
        iv,
        "download_storage_file_with_retry",
        lambda *a, **k: b"a,b\n1,2\n3,4\n",
    )
    supabase = _FakeSupabase(
        {"storage_path": f"otro/{os.path.basename('x.csv')}", "user_id": USER_ID}
    )
    with pytest.raises(Exception):
        get_dataframe_from_storage(supabase, "file-1")


def test_get_dataframe_accepts_valid_owner(monkeypatch):
    monkeypatch.setattr(
        iv,
        "download_storage_file_with_retry",
        lambda *a, **k: b"a,b\n1,2\n3,4\n",
    )
    supabase = _FakeSupabase(
        {"storage_path": f"{USER_ID}/1700000000_x.csv", "user_id": USER_ID}
    )
    dfs, _audit = get_dataframe_from_storage(supabase, "file-1")
    assert dfs


# ── 4. _read_upload_bounded ────────────────────────────────────────────────────

class _FakeUpload:
    def __init__(self, data: bytes):
        self._data = data
        self._pos = 0

    async def read(self, size: int = -1) -> bytes:
        if self._pos >= len(self._data):
            return b""
        end = self._pos + (size if size and size > 0 else len(self._data))
        chunk = self._data[self._pos:end]
        self._pos = end
        return chunk


def test_read_upload_bounded_rejects_oversize():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(_read_upload_bounded(_FakeUpload(b"x" * 100), 10))
    assert exc.value.status_code == 413


def test_read_upload_bounded_returns_within_limit():
    result = asyncio.run(_read_upload_bounded(_FakeUpload(b"hello"), 1024))
    assert result == b"hello"
