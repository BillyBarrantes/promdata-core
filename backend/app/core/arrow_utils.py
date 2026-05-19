# En: backend/app/core/arrow_utils.py
"""
Arrow Transport Layer V1.0 — Serialización DataFrame → Arrow IPC → Base64.

Convierte DataFrames grandes en formato binario columnar Apache Arrow,
codificado en base64 para transporte seguro dentro del JSON existente
del pipeline Celery → Supabase → Frontend.

Principios:
- Schema-Agnostic: Convierte cualquier DataFrame sin conocer las columnas.
- Extension-Only: Nuevo módulo, no modifica nada existente.
- Conditional: Solo se activa si el dataset supera el umbral de filas.
- Type Hints: Todos los parámetros y retornos tipados.
"""

import base64
from typing import Any, Optional

import pandas as pd
try:
    import pyarrow as pa
    import pyarrow.ipc as ipc
except ModuleNotFoundError:  # pragma: no cover - entorno sin pyarrow
    pa = None
    ipc = None


# ---------------------------------------------------------------------------
# CONSTANTES
# ---------------------------------------------------------------------------
ARROW_THRESHOLD: int = 500  # Compatibilidad legacy
ARROW_MIN_ROWS: int = 300
ARROW_MIN_CELLS: int = 4_000
ARROW_MIN_BYTES: int = 64 * 1024
ARROW_MAX_SAMPLE_ROWS: int = 50


# ---------------------------------------------------------------------------
# SEMÁFORO DE ACTIVACIÓN
# ---------------------------------------------------------------------------

def _estimate_records_size_bytes(records: list[dict[str, Any]], sample_rows: int = ARROW_MAX_SAMPLE_ROWS) -> int:
    """
    Estima el peso JSON del payload sin serializar el dataset completo.
    """
    if not records:
        return 0

    sampled = records[: min(len(records), sample_rows)]
    sample_json = str(sampled).encode('utf-8')
    avg_row_bytes = max(1, len(sample_json) // max(1, len(sampled)))
    return avg_row_bytes * len(records)


def _estimate_dataframe_size_bytes(df: pd.DataFrame) -> int:
    """
    Estima memoria del DataFrame para decidir transporte.
    """
    if df.empty:
        return 0
    return int(df.memory_usage(index=False, deep=True).sum())


def evaluate_arrow_transport(
    row_count: int,
    column_count: int = 0,
    estimated_bytes: Optional[int] = None,
    threshold: int = ARROW_THRESHOLD,
) -> dict[str, Any]:
    """
    Decide el transporte óptimo según volumen real.

    Mantiene compatibilidad con el threshold legacy, pero prioriza tamaño
    efectivo del payload para evitar activar Arrow demasiado pronto.
    """
    safe_rows = max(0, int(row_count or 0))
    safe_cols = max(0, int(column_count or 0))
    safe_bytes = max(0, int(estimated_bytes or 0))
    total_cells = safe_rows * safe_cols

    reasons: list[str] = []
    if safe_rows >= max(threshold, ARROW_MIN_ROWS):
        reasons.append(f"rows={safe_rows}")
    if total_cells >= ARROW_MIN_CELLS:
        reasons.append(f"cells={total_cells}")
    if safe_bytes >= ARROW_MIN_BYTES:
        reasons.append(f"bytes≈{safe_bytes}")

    use_arrow = len(reasons) > 0
    mode = "arrow" if use_arrow else "json"
    reason = ", ".join(reasons) if reasons else f"payload ligero (rows={safe_rows}, cells={total_cells}, bytes≈{safe_bytes})"

    return {
        "mode": mode,
        "use_arrow": use_arrow,
        "row_count": safe_rows,
        "column_count": safe_cols,
        "estimated_bytes": safe_bytes,
        "estimated_cells": total_cells,
        "reason": reason,
    }


def should_use_arrow(
    row_count: int,
    threshold: int = ARROW_THRESHOLD,
    column_count: int = 0,
    estimated_bytes: Optional[int] = None,
) -> bool:
    """
    Decide si el dataset justifica Arrow transport.

    Datasets pequeños (<threshold filas) no necesitan optimización binaria;
    el overhead del encoding/decoding supera el ahorro.

    Args:
        row_count: Número de filas del dataset.
        threshold: Umbral configurable (default: 500).

    Returns:
        True si Arrow es beneficioso, False si JSON es suficiente.
    """
    return evaluate_arrow_transport(
        row_count=row_count,
        column_count=column_count,
        estimated_bytes=estimated_bytes,
        threshold=threshold,
    )["use_arrow"]


# ---------------------------------------------------------------------------
# SERIALIZACIÓN: DataFrame → Arrow IPC → Base64 string
# ---------------------------------------------------------------------------

def dataframe_to_arrow_base64(df: pd.DataFrame) -> str:
    """
    Convierte un DataFrame de Pandas a un string base64 de Arrow IPC Stream.

    Pipeline:
      1. DataFrame → PyArrow Table (preservando tipos)
      2. PyArrow Table → IPC Stream bytes (formato binario columnar)
      3. Bytes → Base64 string (seguro para transporte JSON)

    Args:
        df: DataFrame de Pandas con cualquier esquema.

    Returns:
        String base64 del Arrow IPC stream, listo para inyectar en JSON.

    Raises:
        ValueError: Si el DataFrame está vacío.
    """
    if df.empty:
        raise ValueError("Cannot serialize empty DataFrame to Arrow")
    if pa is None or ipc is None:
        raise ModuleNotFoundError("pyarrow is required to serialize DataFrames to Arrow")

    # Paso 1: DataFrame → PyArrow Table
    # preserve_index=False evita inyectar columna '__index_level_0__'
    table = pa.Table.from_pandas(df, preserve_index=False)

    # Paso 2: PyArrow Table → IPC Stream bytes
    sink = pa.BufferOutputStream()
    writer = ipc.new_stream(sink, table.schema)
    writer.write_table(table)
    writer.close()
    arrow_bytes = sink.getvalue().to_pybytes()

    # Paso 3: Bytes → Base64 string
    return base64.b64encode(arrow_bytes).decode('utf-8')


# ---------------------------------------------------------------------------
# DESERIALIZACIÓN: Base64 → Arrow → DataFrame (para testing/verificación)
# ---------------------------------------------------------------------------

def arrow_base64_to_dataframe(b64_string: str) -> pd.DataFrame:
    """
    Deserializa un string base64 de Arrow IPC Stream de vuelta a DataFrame.

    Útil para:
    - Tests unitarios (round-trip verification)
    - Debugging en el backend

    Args:
        b64_string: String base64 de un Arrow IPC stream.

    Returns:
        DataFrame de Pandas reconstruido.
    """
    if ipc is None:
        raise ModuleNotFoundError("pyarrow is required to deserialize Arrow payloads")

    arrow_bytes = base64.b64decode(b64_string)
    reader = ipc.open_stream(arrow_bytes)
    return reader.read_pandas()


# ---------------------------------------------------------------------------
# HELPER: Convierte lista de dicts → Arrow base64 (para payloads existentes)
# ---------------------------------------------------------------------------

def records_to_arrow_base64(records: list[dict[str, Any]]) -> str:
    """
    Convierte una lista de diccionarios (formato actual del pipeline) a Arrow base64.

    Este es el helper principal para la inyección en analysis_tasks.py,
    donde los datos ya vienen como list[dict] en vez de DataFrame.

    Args:
        records: Lista de diccionarios (cada dict = una fila).

    Returns:
        String base64 del Arrow IPC stream.

    Raises:
        ValueError: Si la lista está vacía.
    """
    if not records:
        raise ValueError("Cannot serialize empty records list to Arrow")

    df = pd.DataFrame(records)
    return dataframe_to_arrow_base64(df)


def evaluate_records_arrow_transport(
    records: list[dict[str, Any]],
    threshold: int = ARROW_THRESHOLD,
) -> dict[str, Any]:
    """
    Evalúa transporte para listas de registros.
    """
    row_count = len(records)
    column_count = len(records[0]) if records and isinstance(records[0], dict) else 0
    estimated_bytes = _estimate_records_size_bytes(records)
    return evaluate_arrow_transport(
        row_count=row_count,
        column_count=column_count,
        estimated_bytes=estimated_bytes,
        threshold=threshold,
    )


def evaluate_dataframe_arrow_transport(
    df: pd.DataFrame,
    threshold: int = ARROW_THRESHOLD,
) -> dict[str, Any]:
    """
    Evalúa transporte para DataFrames ya materializados.
    """
    return evaluate_arrow_transport(
        row_count=len(df),
        column_count=len(df.columns),
        estimated_bytes=_estimate_dataframe_size_bytes(df),
        threshold=threshold,
    )
