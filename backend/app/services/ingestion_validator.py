"""
ingestion_validator.py — Validador Pre-Ingesta, Detección de Formatos y Seguridad ZIP.
══════════════════════════════════════════════════════════════════════════════════════
Implementa las validaciones de Fase 7:
  1. Verificación de UploadFlowContractV1 (bucket dash-uploads, tamaño <= 50MB, paths seguros).
  2. Detección de Magic Bytes y rechazo de binarios maliciosos (.exe, scripts, elf, etc.).
  3. Descompresión ZIP segura con protección contra Zip Bombs y Path Traversal.
  4. Parsing tabular de CSV (sniffing de delimitador y codificación UTF-8/Latin-1) y Excel.
  5. Detección y rechazo de datasets vacíos o sin columnas tabulares.
  6. Descarga resiliente de Supabase Storage con reintentos y backoff exponencial.
"""
from __future__ import annotations

import csv
import io
import os
import time
import zipfile
from typing import Any

import pandas as pd

from app.core.analytical_contract import UploadFlowContractV1
from app.core.structured_logging import emit_structured_log

# Firmas binarias (Magic Bytes)
ZIP_MAGIC = b"PK\x03\x04"
XLS_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
EXE_MAGIC = b"MZ"
ELF_MAGIC = b"\x7fELF"
PDF_MAGIC = b"%PDF"

MAX_FILE_SIZE_BYTES = 52_428_800  # 50 MB
MAX_UNCOMPRESSED_ZIP_BYTES = 104_857_600  # 100 MB
MAX_ZIP_COMPRESSION_RATIO = 10.0  # 10:1
MAX_ZIP_MEMBER_COUNT = 100


def validate_upload_contract(
    flow_contract: UploadFlowContractV1,
    *,
    file_name: str,
    file_size_bytes: int,
    storage_path: str,
    user_id: str,
) -> tuple[bool, str | None]:
    """
    Verifica que el archivo subido cumpla las restricciones del contrato UploadFlowContractV1.
    """
    if file_size_bytes > flow_contract.max_size_bytes:
        return False, (
            f"El archivo '{file_name}' ({file_size_bytes} bytes) supera el límite "
            f"máximo permitido de {flow_contract.max_size_bytes} bytes (50 MB)."
        )

    # Path traversal check
    if ".." in storage_path or storage_path.startswith("/") or "\\" in storage_path:
        return False, f"Ruta de almacenamiento inválida o sospechosa de path traversal: '{storage_path}'."

    # RLS user prefix check (si storage_path incluye prefijo de usuario)
    if not storage_path.startswith(f"{user_id}/") and not storage_path.startswith(f"dash-uploads/{user_id}/"):
        return False, f"La ruta '{storage_path}' no corresponde al espacio aislado del usuario '{user_id}'."

    return True, None


def detect_file_format_and_validate_bytes(
    file_bytes: bytes,
    file_name: str,
) -> str:
    """
    Detecta el tipo de archivo mediante Magic Bytes y rechaza binarios maliciosos.
    Retorna: 'zip', 'xlsx', 'xls', o 'csv'.
    """
    if not file_bytes:
        raise ValueError("El archivo cargado tiene 0 bytes (archivo vacío).")

    if file_bytes.startswith(EXE_MAGIC):
        raise ValueError("Archivo rechazado: se detectó un ejecutable de Windows (MZ), no es un archivo tabular.")
    if file_bytes.startswith(ELF_MAGIC):
        raise ValueError("Archivo rechazado: se detectó un binario ejecutable ELF, no es un archivo tabular.")
    if file_bytes.startswith(PDF_MAGIC):
        raise ValueError("Archivo rechazado: se detectó un documento PDF, no es un archivo tabular soportado.")

    lower_name = file_name.lower()

    if file_bytes.startswith(ZIP_MAGIC):
        if lower_name.endswith(".xlsx"):
            return "xlsx"
        if lower_name.endswith(".zip"):
            return "zip"
        # Si tiene magic PK pero extensión ambigua, comprobar si contiene xl/workbook.xml
        try:
            with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
                namelist = zf.namelist()
                if "xl/workbook.xml" in namelist or any(n.startswith("xl/") for n in namelist):
                    return "xlsx"
                return "zip"
        except Exception:
            return "zip"

    if file_bytes.startswith(XLS_OLE2_MAGIC) or lower_name.endswith(".xls"):
        return "xls"

    # Si no es ZIP ni OLE2, verificar si es texto plano (CSV / TSV)
    # Comprobar presencia de caracteres nulos excesivos
    sample = file_bytes[:4096]
    if b"\x00" in sample:
        null_count = sample.count(b"\x00")
        if null_count > len(sample) * 0.1:
            raise ValueError("Archivo rechazado: contiene datos binarios desconocidos no tabulares.")

    return "csv"


def safe_extract_tabular_from_zip(
    file_bytes: bytes,
) -> tuple[bytes, str]:
    """
    Descomprime de forma segura un archivo ZIP, protegiendo contra Zip Bombs y Path Traversal.
    Retorna (bytes_extraidos, nombre_archivo_extraido).
    """
    with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
        infolist = zf.infolist()
        if len(infolist) > MAX_ZIP_MEMBER_COUNT:
            raise ValueError(f"Archivo ZIP rechazado: contiene {len(infolist)} archivos (máximo permitido: {MAX_ZIP_MEMBER_COUNT}).")

        total_uncompressed = 0
        tabular_member = None

        for info in infolist:
            # 1. Path traversal check
            if ".." in info.filename or info.filename.startswith("/") or "\\" in info.filename:
                raise ValueError(f"Archivo ZIP rechazado: ruta sospechosa o path traversal '{info.filename}'.")

            total_uncompressed += info.file_size

            # 2. Zip Bomb check (tamaño total)
            if total_uncompressed > MAX_UNCOMPRESSED_ZIP_BYTES:
                raise ValueError(
                    f"Archivo ZIP rechazado: tamaño descomprimido supera {MAX_UNCOMPRESSED_ZIP_BYTES} bytes (100 MB)."
                )

            # 3. Compression ratio check
            compressed_size = max(info.compress_size, 1)
            ratio = info.file_size / compressed_size
            if ratio > MAX_ZIP_COMPRESSION_RATIO and info.file_size > 1_000_000:
                raise ValueError(f"Archivo ZIP rechazado: ratio de compresión anómalo ({ratio:.1f}:1). Posible Zip Bomb.")

            # Buscar primer archivo tabular candidato
            lower_name = info.filename.lower()
            if not info.is_dir() and not info.filename.startswith("__MACOSX"):
                if lower_name.endswith((".csv", ".xlsx", ".xls", ".tsv", ".txt")):
                    if tabular_member is None:
                        tabular_member = info

        if not tabular_member:
            raise ValueError("El archivo ZIP no contiene ningún archivo tabular válido (.csv, .xlsx, .xls, .tsv).")

        extracted_bytes = zf.read(tabular_member)
        return extracted_bytes, os.path.basename(tabular_member.filename)


def parse_tabular_bytes_to_dfs(
    file_bytes: bytes,
    file_name: str,
) -> dict[str, pd.DataFrame]:
    """
    Convierte bytes crudos a un diccionario de DataFrames de pandas ({nombre_hoja: df}).
    Valida codificación, delimitadores y descarta archivos sin filas.
    """
    fmt = detect_file_format_and_validate_bytes(file_bytes, file_name)

    if fmt == "zip":
        file_bytes, file_name = safe_extract_tabular_from_zip(file_bytes)
        fmt = detect_file_format_and_validate_bytes(file_bytes, file_name)

    dfs: dict[str, pd.DataFrame] = {}

    if fmt in ("xlsx", "xls"):
        f_io = io.BytesIO(file_bytes)
        try:
            xls = pd.ExcelFile(f_io)
            for sheet in xls.sheet_names:
                df_sheet = pd.read_excel(xls, sheet_name=sheet)
                if not df_sheet.empty and len(df_sheet.columns) > 0:
                    dfs[sheet] = df_sheet
        except Exception as e:
            raise ValueError(f"Error procesando archivo Excel: {str(e)}")

    if not dfs:
        # Intentar como CSV / TSV
        # 1. Detectar codificación (UTF-8 vs Latin-1)
        decoded_text = None
        for enc in ("utf-8", "utf-8-sig", "latin-1", "cp1252"):
            try:
                decoded_text = file_bytes.decode(enc)
                break
            except (UnicodeDecodeError, UnicodeError):
                continue

        if decoded_text is None:
            decoded_text = file_bytes.decode("latin-1", errors="replace")

        # 2. Sniff delimiter
        sample_lines = decoded_text.splitlines()[:20]
        sample_text = "\n".join(sample_lines)
        sep = ","
        try:
            dialect = csv.Sniffer().sniff(sample_text, delimiters=";,|\t,")
            sep = dialect.delimiter
        except Exception:
            # Fallback heurístico si sniffer falla
            counts = {
                ",": sample_text.count(","),
                ";": sample_text.count(";"),
                "\t": sample_text.count("\t"),
                "|": sample_text.count("|"),
            }
            best_sep = max(counts, key=counts.get)
            if counts[best_sep] > 0:
                sep = best_sep

        f_io = io.StringIO(decoded_text)
        try:
            df = pd.read_csv(f_io, sep=sep, on_bad_lines="skip")
            if not df.empty and len(df.columns) > 0:
                dfs["principal"] = df
        except Exception as e:
            raise ValueError(f"Error procesando archivo CSV: {str(e)}")

    if not dfs or all(d.empty for d in dfs.values()):
        raise ValueError("El archivo cargado está vacío o no contiene filas con datos tabulares.")

    return dfs


def download_storage_file_with_retry(
    storage_client: Any,
    bucket_name: str,
    storage_path: str,
    max_retries: int = 3,
    initial_delay: float = 1.0,
) -> bytes:
    """
    Descarga un archivo de Supabase Storage con reintentos y backoff exponencial ante fallos de red.
    """
    last_error: Exception | None = None
    delay = initial_delay

    for attempt in range(1, max_retries + 1):
        try:
            file_bytes = storage_client.from_(bucket_name).download(storage_path)
            if file_bytes is not None and len(file_bytes) > 0:
                return file_bytes
            raise ValueError(f"La descarga desde storage retornó datos vacíos para '{storage_path}'.")
        except Exception as e:
            last_error = e
            emit_structured_log(
                "storage_download_retry",
                level="warning",
                attempt=attempt,
                max_retries=max_retries,
                storage_path=storage_path,
                error=str(e)[:200],
            )
            if attempt < max_retries:
                time.sleep(delay)
                delay *= 2.0

    raise RuntimeError(
        f"Fallo al descargar '{storage_path}' de '{bucket_name}' tras {max_retries} intentos: {last_error}"
    )
