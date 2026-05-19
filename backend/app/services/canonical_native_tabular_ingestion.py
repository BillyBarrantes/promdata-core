from __future__ import annotations

import csv
import io
import math
from typing import Any

import pandas as pd

from app.core.config import settings


_CSV_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")
_CSV_DELIMITERS = (",", ";", "\t", "|")


def _is_empty_scalar(value: Any) -> bool:
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except Exception:
        pass
    return str(value).strip() == ""


def _pythonize_scalar(value: Any) -> Any:
    if _is_empty_scalar(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "isoformat") and not isinstance(value, str):
        try:
            return value.isoformat()
        except Exception:
            pass
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        try:
            return value.item()
        except Exception:
            pass
    return value


def _drop_empty_edges(df: pd.DataFrame) -> pd.DataFrame:
    working_df = df.copy()
    if working_df.empty and len(working_df.columns) == 0:
        return working_df

    row_mask = working_df.apply(
        lambda row: any(not _is_empty_scalar(value) for value in row.tolist()),
        axis=1,
    )
    working_df = working_df.loc[row_mask]
    if working_df.empty and len(working_df.columns) == 0:
        return working_df.reset_index(drop=True)

    keep_columns = [
        column_name
        for column_name in working_df.columns
        if any(not _is_empty_scalar(value) for value in working_df[column_name].tolist())
    ]
    working_df = working_df.loc[:, keep_columns]
    return working_df.reset_index(drop=True)


def _is_generic_column_name(value: Any) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return True
    if text.startswith("unnamed:"):
        return True
    if text.startswith("column_"):
        return True
    return text.isdigit()


def _looks_generic_header(columns: list[Any]) -> bool:
    if not columns:
        return True
    generic_count = sum(1 for value in columns if _is_generic_column_name(value))
    return (generic_count / max(len(columns), 1)) >= 0.5


def _looks_numeric_text(value: str) -> bool:
    normalized = str(value or "").strip().replace(",", "").replace(".", "").replace("-", "")
    return bool(normalized) and normalized.isdigit()


def _should_promote_first_row(df: pd.DataFrame) -> bool:
    if df.empty or not _looks_generic_header(list(df.columns)):
        return False

    first_row = [str(value or "").strip() for value in df.iloc[0].tolist()]
    non_empty = [value for value in first_row if value]
    if len(non_empty) < max(2, math.ceil(len(first_row) * 0.5)):
        return False

    unique_ratio = len({value.lower() for value in non_empty}) / max(len(non_empty), 1)
    if unique_ratio < 0.7:
        return False

    label_like_count = sum(1 for value in non_empty if not _looks_numeric_text(value))
    return (label_like_count / max(len(non_empty), 1)) >= 0.5


def _normalize_column_names(raw_columns: list[Any]) -> list[str]:
    normalized_columns: list[str] = []
    seen: dict[str, int] = {}
    for index, raw_name in enumerate(raw_columns, start=1):
        candidate = str(raw_name or "").strip() or f"column_{index}"
        counter = seen.get(candidate, 0)
        seen[candidate] = counter + 1
        if counter:
            candidate = f"{candidate}_{counter + 1}"
        normalized_columns.append(candidate)
    return normalized_columns


def _prepare_dataframe(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    working_df = _drop_empty_edges(df)
    metadata = {
        "header_promoted": False,
        "source_row_count": int(len(working_df.index)),
        "source_column_count": int(len(working_df.columns)),
    }

    if working_df.empty and len(working_df.columns) == 0:
        return working_df, metadata

    if _should_promote_first_row(working_df):
        promoted_columns = _normalize_column_names(working_df.iloc[0].tolist())
        working_df = working_df.iloc[1:].reset_index(drop=True)
        working_df.columns = promoted_columns
        metadata["header_promoted"] = True
    else:
        working_df.columns = _normalize_column_names(list(working_df.columns))

    working_df = _drop_empty_edges(working_df)
    metadata["normalized_row_count"] = int(len(working_df.index))
    metadata["normalized_column_count"] = int(len(working_df.columns))
    return working_df, metadata


def _truncate_preview_dataframe(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    max_rows = max(int(settings.CANONICAL_NATIVE_TABULAR_MAX_ROWS or 0), 1)
    max_columns = max(int(settings.CANONICAL_NATIVE_TABULAR_MAX_COLUMNS or 0), 1)
    preview_df = df.iloc[:max_rows, :max_columns].copy()
    return preview_df, {
        "preview_truncated_rows": len(df.index) > max_rows,
        "preview_truncated_columns": len(df.columns) > max_columns,
        "preview_row_count": int(len(preview_df.index)),
        "preview_column_count": int(len(preview_df.columns)),
        "preview_max_rows": max_rows,
        "preview_max_columns": max_columns,
    }


def _truncate_analytics_dataframe(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    max_rows = max(int(settings.CANONICAL_NATIVE_TABULAR_ANALYTICS_MAX_ROWS or 0), 1)
    max_columns = max(int(settings.CANONICAL_NATIVE_TABULAR_ANALYTICS_MAX_COLUMNS or 0), 1)
    analytics_df = df.iloc[:max_rows, :max_columns].copy()
    return analytics_df, {
        "analytics_truncated_rows": len(df.index) > max_rows,
        "analytics_truncated_columns": len(df.columns) > max_columns,
        "analytics_row_count": int(len(analytics_df.index)),
        "analytics_column_count": int(len(analytics_df.columns)),
        "analytics_max_rows": max_rows,
        "analytics_max_columns": max_columns,
    }


def _dataframe_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row in df.to_dict(orient="records"):
        records.append({str(key): _pythonize_scalar(value) for key, value in row.items()})
    return records


def _score_dataframe(df: pd.DataFrame) -> tuple[int, int]:
    return int(len(df.columns)), int(len(df.index))


def _detect_csv_delimiter(sample_text: str) -> str:
    candidate_text = str(sample_text or "").strip()
    if not candidate_text:
        return ","
    try:
        dialect = csv.Sniffer().sniff(candidate_text, delimiters="".join(_CSV_DELIMITERS))
        return str(dialect.delimiter or ",")
    except Exception:
        return ","


def _read_csv_dataframe(file_bytes: bytes) -> tuple[pd.DataFrame, dict[str, Any]]:
    best_df: pd.DataFrame | None = None
    best_metadata: dict[str, Any] = {}
    best_score = (-1, -1)
    sample_bytes = file_bytes[:8192]

    for encoding in _CSV_ENCODINGS:
        try:
            sample_text = sample_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue

        detected_delimiter = _detect_csv_delimiter(sample_text)
        delimiters = [detected_delimiter, *_CSV_DELIMITERS]
        seen_delimiters: set[str] = set()

        for delimiter in delimiters:
            if delimiter in seen_delimiters:
                continue
            seen_delimiters.add(delimiter)
            try:
                candidate_df = pd.read_csv(
                    io.BytesIO(file_bytes),
                    encoding=encoding,
                    sep=delimiter,
                    dtype=object,
                    engine="python",
                )
            except Exception:
                continue

            prepared_df, prepared_metadata = _prepare_dataframe(candidate_df)
            score = _score_dataframe(prepared_df)
            if score > best_score:
                best_df = prepared_df
                best_metadata = {
                    **prepared_metadata,
                    "encoding": encoding,
                    "delimiter": delimiter,
                }
                best_score = score

    if best_df is None:
        raise ValueError("No se pudo interpretar el CSV con una combinación segura de encoding/separador.")
    return best_df, best_metadata


def _normalized_column_set(df: pd.DataFrame) -> frozenset[str]:
    """Return normalized lowercase column names for schema comparison."""
    return frozenset(str(c).strip().lower() for c in df.columns)


def _header_overlap_ratio_df(left: pd.DataFrame, right: pd.DataFrame) -> float:
    """Column overlap between two DataFrames (0.0 – 1.0)."""
    left_cols = _normalized_column_set(left)
    right_cols = _normalized_column_set(right)
    if not left_cols or not right_cols:
        return 0.0
    return len(left_cols & right_cols) / max(len(left_cols), len(right_cols))


_UNION_HEADER_OVERLAP_THRESHOLD = 0.90


def _read_excel_frames(file_bytes: bytes, extension: str) -> list[dict[str, Any]]:
    engine = "xlrd" if extension == "xls" else "openpyxl"
    workbook = pd.ExcelFile(io.BytesIO(file_bytes), engine=engine)

    max_frames = max(int(settings.CANONICAL_NATIVE_TABULAR_MAX_FRAMES or 0), 1)

    # Fase 1: Leer y preparar cada hoja individualmente
    raw_frames: list[dict[str, Any]] = []
    for sheet_name in workbook.sheet_names[:max_frames]:
        try:
            raw_df = workbook.parse(sheet_name=sheet_name, dtype=object)
        except Exception:
            continue
        prepared_df, prepared_metadata = _prepare_dataframe(raw_df)
        if prepared_df.empty and len(prepared_df.columns) == 0:
            continue
        raw_frames.append(
            {
                "sheet_name": str(sheet_name or ""),
                "dataframe": prepared_df,
                "metadata": prepared_metadata,
            }
        )

    if not raw_frames:
        return []

    # Fase 2: Concatenación Inteligente — agrupar hojas con schema idéntico
    # Si múltiples hojas comparten >= 90% de columnas, se unifican en un solo
    # DataFrame (UNION) en vez de producir frames separados que el materializer
    # convertiría en un JOIN incorrecto con columnas duplicadas.
    union_groups: list[list[int]] = []
    assigned: set[int] = set()

    for i in range(len(raw_frames)):
        if i in assigned:
            continue
        group = [i]
        assigned.add(i)
        ref_df = raw_frames[i]["dataframe"]
        for j in range(i + 1, len(raw_frames)):
            if j in assigned:
                continue
            if _header_overlap_ratio_df(ref_df, raw_frames[j]["dataframe"]) >= _UNION_HEADER_OVERLAP_THRESHOLD:
                group.append(j)
                assigned.add(j)
        union_groups.append(group)

    result_frames: list[dict[str, Any]] = []

    for group in union_groups:
        if len(group) == 1:
            # Hoja solitaria o con schema único → frame individual
            idx = group[0]
            entry = raw_frames[idx]
            result_frames.append(
                {
                    "frame_id": f"sheet::{entry['sheet_name']}",
                    "label": entry["sheet_name"] or "Sheet",
                    "sheet_name": entry["sheet_name"],
                    "dataframe": entry["dataframe"],
                    "metadata": entry["metadata"],
                }
            )
        else:
            # Múltiples hojas con schema compatible → UNION concatenado
            sheet_names = [raw_frames[idx]["sheet_name"] for idx in group]
            dfs_to_concat = [raw_frames[idx]["dataframe"] for idx in group]

            # Usar las columnas de la primera hoja como referencia
            ref_columns = list(dfs_to_concat[0].columns)

            # Alinear columnas: cada DF se reindexará a las columnas de referencia
            aligned_dfs: list[pd.DataFrame] = []
            for df in dfs_to_concat:
                aligned = df.reindex(columns=ref_columns)
                aligned_dfs.append(aligned)

            unified_df = pd.concat(aligned_dfs, ignore_index=True)

            # Liberar memoria de los DataFrames individuales
            del aligned_dfs
            del dfs_to_concat
            for idx in group:
                raw_frames[idx]["dataframe"] = None  # type: ignore[assignment]

            total_rows = len(unified_df)
            unified_label = f"{'_'.join(sheet_names[:3])}{'_...' if len(sheet_names) > 3 else ''}"

            print(
                f"📋 [MULTI-SHEET UNION] {len(sheet_names)} hojas unificadas "
                f"({', '.join(sheet_names)}) → {total_rows:,} filas, "
                f"{len(ref_columns)} columnas"
            )

            result_frames.append(
                {
                    "frame_id": f"union::{'__'.join(sheet_names[:5])}",
                    "label": unified_label,
                    "sheet_name": None,
                    "dataframe": unified_df,
                    "metadata": {
                        "source_sheets": sheet_names,
                        "source_sheet_count": len(sheet_names),
                        "union_strategy": "schema_compatible_concat",
                        "source_row_count": total_rows,
                        "source_column_count": len(ref_columns),
                        "normalized_row_count": total_rows,
                        "normalized_column_count": len(ref_columns),
                    },
                }
            )

    return result_frames


def extract_native_tabular_frames(
    *,
    file_name: str,
    file_bytes: bytes,
    extension: str,
) -> list[dict[str, Any]]:
    normalized_extension = str(extension or "").lower().strip(".")
    if normalized_extension == "csv":
        dataframe, metadata = _read_csv_dataframe(file_bytes)
        return [
            {
                "frame_id": "csv::main",
                "label": file_name,
                "sheet_name": None,
                "dataframe": dataframe,
                "metadata": metadata,
            }
        ]

    if normalized_extension in {"xlsx", "xls"}:
        return _read_excel_frames(file_bytes, normalized_extension)

    raise ValueError(f"Formato tabular nativo no soportado por el extractor paralelo: {normalized_extension}")


def build_native_frame_payload(frame: dict[str, Any]) -> dict[str, Any]:
    dataframe = frame["dataframe"]
    preview_df, preview_metadata = _truncate_preview_dataframe(dataframe)
    analytics_df, analytics_metadata = _truncate_analytics_dataframe(dataframe)
    records = _dataframe_records(analytics_df)
    preview_records = _dataframe_records(preview_df)
    metadata = {
        **dict(frame.get("metadata") or {}),
        **preview_metadata,
        **analytics_metadata,
        # Backward-compatible flags used by parser warnings.
        "truncated_rows": bool(analytics_metadata.get("analytics_truncated_rows")),
        "truncated_columns": bool(analytics_metadata.get("analytics_truncated_columns")),
        "rows_payload": records,
        "sample_rows": preview_records[:3],
        "parser_backend": "pandas_native_parallel",
        "sheet_name": frame.get("sheet_name"),
    }
    return {
        "frame_id": str(frame["frame_id"]),
        "label": str(frame["label"]),
        "row_count": int(len(analytics_df.index)),
        "column_count": int(len(analytics_df.columns)),
        "column_names": list(map(str, analytics_df.columns)),
        "metadata": metadata,
    }
