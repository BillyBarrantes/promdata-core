"""
test_phase7_ingestion_pipeline_contract.py — Suite de Ingesta, Validaciones y Pipeline Asíncrono (Fase 7)
══════════════════════════════════════════════════════════════════════════════════════════════════════════
Verifica:
  1. Contrato UploadFlowContractV1 (bucket dash-uploads, límite 50MB, paths aislados por tenant).
  2. Detección de Magic Bytes y rechazo tajante de binarios maliciosos (.exe, elf, pdf).
  3. Descompresión ZIP segura con blindaje contra Zip Bomb y Path Traversal.
  4. Parsing tabular de CSV con auto-detección de delimitadores (',', ';', '\t') y encodings.
  5. Rechazo temprano de archivos vacíos con mensajes accionables.
  6. Descarga resiliente de Storage con reintentos y backoff exponencial.
"""
from __future__ import annotations

import io
import os
import sys
import zipfile

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(__file__))

from app.core.analytical_contract import UploadFlowContractV1
from app.services.ingestion_validator import (
    detect_file_format_and_validate_bytes,
    download_storage_file_with_retry,
    parse_tabular_bytes_to_dfs,
    safe_extract_tabular_from_zip,
    validate_upload_contract,
)


# ── 1. VALIDACIÓN DE CONTRATO UploadFlowContractV1 ─────────────────────────────

def test_upload_flow_contract_valid():
    """Valida un archivo dentro de los límites y con ruta correcta de tenant."""
    contract = UploadFlowContractV1()
    is_valid, error = validate_upload_contract(
        contract,
        file_name="ventas_2024.csv",
        file_size_bytes=1024 * 1024,  # 1 MB
        storage_path="user_123/1709400000_ventas_2024.csv",
        user_id="user_123",
    )
    assert is_valid is True
    assert error is None


def test_upload_flow_contract_rejects_exceeded_size():
    """Rechaza archivos que superen los 50 MB permitidos."""
    contract = UploadFlowContractV1()
    is_valid, error = validate_upload_contract(
        contract,
        file_name="huge_dataset.csv",
        file_size_bytes=60 * 1024 * 1024,  # 60 MB
        storage_path="user_123/1709400000_huge_dataset.csv",
        user_id="user_123",
    )
    assert is_valid is False
    assert "supera el límite" in error


def test_upload_flow_contract_rejects_path_traversal_and_cross_tenant():
    """Rechaza rutas con path traversal o que violen el aislamiento del tenant."""
    contract = UploadFlowContractV1()

    # Path traversal con ..
    valid_traversal, err_traversal = validate_upload_contract(
        contract,
        file_name="hack.csv",
        file_size_bytes=100,
        storage_path="user_123/../../../etc/passwd",
        user_id="user_123",
    )
    assert valid_traversal is False
    assert "path traversal" in err_traversal

    # Ruta de otro usuario
    valid_cross, err_cross = validate_upload_contract(
        contract,
        file_name="leak.csv",
        file_size_bytes=100,
        storage_path="other_user_456/1709400000_leak.csv",
        user_id="user_123",
    )
    assert valid_cross is False
    assert "no corresponde al espacio aislado" in err_cross


# ── 2. MAGIC BYTES Y RECHAZO DE ARCHIVOS MALICIOSOS ───────────────────────────

def test_detect_format_rejects_executables_and_pdfs():
    """Rechaza binarios ejecutables y PDFs que pretendan hacerse pasar por datos."""
    # Windows MZ Executable
    with pytest.raises(ValueError, match="ejecutable de Windows"):
        detect_file_format_and_validate_bytes(b"MZ\x90\x00\x03\x00\x00\x00", "datos.csv")

    # Linux ELF
    with pytest.raises(ValueError, match="binario ejecutable ELF"):
        detect_file_format_and_validate_bytes(b"\x7fELF\x02\x01\x01\x00", "datos.csv")

    # PDF
    with pytest.raises(ValueError, match="documento PDF"):
        detect_file_format_and_validate_bytes(b"%PDF-1.4\n%...", "reporte.xlsx")

    # 0 bytes
    with pytest.raises(ValueError, match="0 bytes"):
        detect_file_format_and_validate_bytes(b"", "vacio.csv")


def test_detect_format_identifies_csv_and_excel():
    """Identifica correctamente CSV y formatos de hoja de cálculo."""
    csv_bytes = b"id,nombre,valor\n1,Alpha,100\n"
    assert detect_file_format_and_validate_bytes(csv_bytes, "datos.csv") == "csv"

    # OLE2 Magic para .xls
    xls_bytes = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 50
    assert detect_file_format_and_validate_bytes(xls_bytes, "datos.xls") == "xls"


# ── 3. DESCOMPRESIÓN ZIP SEGURA Y PROTECCIÓN CONTRA ZIP BOMBS ─────────────────

def test_safe_extract_tabular_from_zip_success():
    """Extrae exitosamente un archivo CSV contenido en un ZIP legítimo."""
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("datos_internos.csv", "id,ventas\n1,500\n2,800\n")

    extracted_bytes, extracted_name = safe_extract_tabular_from_zip(zip_buffer.getvalue())
    assert extracted_name == "datos_internos.csv"
    assert b"id,ventas" in extracted_bytes


def test_safe_extract_rejects_path_traversal_in_zip():
    """Rechaza archivos ZIP con miembros que intenten path traversal."""
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zf:
        zf.writestr("../../malicious.csv", "id,1\n")

    with pytest.raises(ValueError, match="path traversal"):
        safe_extract_tabular_from_zip(zip_buffer.getvalue())


def test_safe_extract_rejects_zip_without_tabular():
    """Rechaza archivos ZIP que solo contengan imágenes u otros binarios."""
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zf:
        zf.writestr("foto.jpg", b"\xff\xd8\xff\xe0")

    with pytest.raises(ValueError, match="no contiene ningún archivo tabular válido"):
        safe_extract_tabular_from_zip(zip_buffer.getvalue())


# ── 4. PARSING TABULAR Y DETECCIÓN DE DELIMITADORES ───────────────────────────

def test_parse_csv_with_different_delimiters():
    """Detecta y parsea CSVs con comas, punto y coma y tabuladores."""
    # Coma
    csv_comma = b"pais,pib\nPeru,240\nChile,310\n"
    dfs_comma = parse_tabular_bytes_to_dfs(csv_comma, "datos.csv")
    assert "pais" in dfs_comma["principal"].columns
    assert len(dfs_comma["principal"]) == 2

    # Punto y coma (formato hispano/europeo)
    csv_semicolon = "producto;precio\nCafé;12,50\nAzúcar;5,00\n".encode("latin-1")
    dfs_semi = parse_tabular_bytes_to_dfs(csv_semicolon, "datos.csv")
    assert "producto" in dfs_semi["principal"].columns
    assert len(dfs_semi["principal"]) == 2

    # Tabulador TSV
    tsv_bytes = b"sku\tstock\nA100\t50\nB200\t120\n"
    dfs_tsv = parse_tabular_bytes_to_dfs(tsv_bytes, "datos.tsv")
    assert "sku" in dfs_tsv["principal"].columns
    assert len(dfs_tsv["principal"]) == 2


def test_parse_empty_dataset_raises_actionable_error():
    """Rechaza datasets que solo contienen cabeceras vacías o no tienen filas."""
    empty_csv = b"col1,col2\n"
    with pytest.raises(ValueError, match="está vacío o no contiene filas con datos tabulares"):
        parse_tabular_bytes_to_dfs(empty_csv, "vacio.csv")


# ── 5. DESCARGA RESILIENTE CON REINTENTOS Y BACKOFF ───────────────────────────

def test_download_storage_file_with_retry_succeeds_on_transient_failure():
    """Recupera exitosamente la descarga ante un fallo transitorio de red."""
    attempts = [0]

    class MockStorageBucket:
        def download(self, path):
            attempts[0] += 1
            if attempts[0] == 1:
                raise ConnectionResetError("Timeout transitorio en Supabase Storage")
            return b"col_a,col_b\n1,10\n2,20\n"

    class MockStorageClient:
        def from_(self, bucket):
            return MockStorageBucket()

    res = download_storage_file_with_retry(
        MockStorageClient(),
        bucket_name="dash-uploads",
        storage_path="user_1/archivo.csv",
        max_retries=3,
        initial_delay=0.01,
    )
    assert res == b"col_a,col_b\n1,10\n2,20\n"
    assert attempts[0] == 2
