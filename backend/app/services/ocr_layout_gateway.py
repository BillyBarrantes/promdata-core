from __future__ import annotations

import io
import statistics
from typing import Any

from app.core.config import settings


def is_image_ocr_enabled() -> bool:
    return settings.FILE_INTELLIGENCE_ENABLE_IMAGE_OCR


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _group_ocr_blocks_into_lines(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not blocks:
        return []

    sorted_blocks = sorted(
        blocks,
        key=lambda block: (
            _safe_float((list(block.get("bbox") or [0, 0, 0, 0])[1] + list(block.get("bbox") or [0, 0, 0, 0])[3]) / 2.0),
            _safe_float(list(block.get("bbox") or [0, 0, 0, 0])[0]),
        ),
    )
    heights = [
        max(1.0, _safe_float(list(block.get("bbox") or [0, 0, 0, 0])[3]) - _safe_float(list(block.get("bbox") or [0, 0, 0, 0])[1]))
        for block in sorted_blocks
    ]
    median_height = statistics.median(heights) if heights else 14.0
    y_tolerance = max(8.0, median_height * 0.8)

    lines: list[dict[str, Any]] = []
    current_line: dict[str, Any] | None = None
    for block in sorted_blocks:
        bbox = list(block.get("bbox") or [0, 0, 0, 0])
        center_y = (_safe_float(bbox[1]) + _safe_float(bbox[3])) / 2.0
        if current_line is None:
            current_line = {"center_y": center_y, "blocks": [block]}
            continue
        if abs(center_y - _safe_float(current_line.get("center_y"))) <= y_tolerance:
            current_blocks = list(current_line.get("blocks") or [])
            current_blocks.append(block)
            centers = []
            for item in current_blocks:
                item_bbox = list(item.get("bbox") or [0, 0, 0, 0])
                centers.append((_safe_float(item_bbox[1]) + _safe_float(item_bbox[3])) / 2.0)
            current_line["blocks"] = current_blocks
            current_line["center_y"] = sum(centers) / len(centers)
            continue
        lines.append(current_line)
        current_line = {"center_y": center_y, "blocks": [block]}

    if current_line is not None:
        lines.append(current_line)

    normalized_lines: list[dict[str, Any]] = []
    for index, line in enumerate(lines, start=1):
        line_blocks = sorted(
            list(line.get("blocks") or []),
            key=lambda item: _safe_float(list(item.get("bbox") or [0, 0, 0, 0])[0]),
        )
        tokens: list[dict[str, Any]] = []
        texts: list[str] = []
        for block in line_blocks:
            bbox = list(block.get("bbox") or [0, 0, 0, 0])
            text = str(block.get("text") or "").strip()
            if not text:
                continue
            tokens.append(
                {
                    "text": text,
                    "bbox": bbox,
                    "center_x": (_safe_float(bbox[0]) + _safe_float(bbox[2])) / 2.0,
                    "confidence": _safe_float(block.get("confidence")),
                }
            )
            texts.append(text)
        if tokens:
            normalized_lines.append(
                {
                    "line_id": f"ocr-line-{index}",
                    "text": " ".join(texts),
                    "tokens": tokens,
                    "token_count": len(tokens),
                    "center_y": _safe_float(line.get("center_y")),
                }
            )
    return normalized_lines


def _assign_tokens_to_header_columns(tokens: list[dict[str, Any]], header_centers: list[float]) -> list[str]:
    cells = [""] * len(header_centers)
    for token in tokens:
        center_x = _safe_float(token.get("center_x"))
        best_index = min(
            range(len(header_centers)),
            key=lambda idx: abs(center_x - header_centers[idx]),
        )
        existing = cells[best_index]
        token_text = str(token.get("text") or "").strip()
        cells[best_index] = f"{existing} {token_text}".strip() if existing else token_text
    return cells


def _reconstruct_ocr_tables(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    lines = _group_ocr_blocks_into_lines(blocks)
    if len(lines) < 2:
        return []

    candidate_lines = [line for line in lines if int(line.get("token_count") or 0) >= 2]
    if len(candidate_lines) < 2:
        return []

    tables: list[dict[str, Any]] = []
    current_group: list[dict[str, Any]] = []

    def _flush_group(group: list[dict[str, Any]]) -> None:
        if len(group) < 2:
            return
        header_tokens = list(group[0].get("tokens") or [])
        header_count = len(header_tokens)
        if header_count < 2:
            return
        header_centers = [_safe_float(token.get("center_x")) for token in header_tokens]
        rows: list[list[str]] = []
        confidences: list[float] = []
        for line in group:
            tokens = list(line.get("tokens") or [])
            if len(tokens) < max(2, header_count - 1):
                return
            row = _assign_tokens_to_header_columns(tokens, header_centers)
            if sum(1 for cell in row if str(cell or "").strip()) < 2:
                return
            rows.append(row)
            confidences.extend([_safe_float(token.get("confidence")) for token in tokens])
        if len(rows) < 2:
            return
        header = rows[0]
        body = rows[1:]
        tables.append(
            {
                "table_id": f"ocr-table-{len(tables) + 1}",
                "rows": rows,
                "column_names": [value if value else f"column_{index}" for index, value in enumerate(header, start=1)],
                "row_count": len(body),
                "column_count": len(header),
                "sample_rows": body[:3],
                "confidence": round(sum(confidences) / len(confidences), 4) if confidences else 0.0,
                "metadata": {
                    "source_kind": "ocr_table",
                    "header_detected": True,
                    "line_count": len(group),
                },
            }
        )

    for line in candidate_lines:
        if not current_group:
            current_group = [line]
            continue
        current_header_count = len(list(current_group[0].get("tokens") or []))
        line_token_count = len(list(line.get("tokens") or []))
        if abs(line_token_count - current_header_count) <= 1:
            current_group.append(line)
            continue
        _flush_group(current_group)
        current_group = [line]

    _flush_group(current_group)
    return tables


def extract_image_layout_payload(file_bytes: bytes) -> dict[str, Any]:
    """
    Gateway OCR/layout desacoplado.
    Si faltan dependencias o la flag está apagada, retorna degradación segura.
    """
    payload: dict[str, Any] = {
        "enabled": is_image_ocr_enabled(),
        "backend": None,
        "text": "",
        "blocks": [],
        "lines": [],
        "tables": [],
        "confidence": 0.0,
        "warnings": [],
        "metadata": {},
    }

    if not is_image_ocr_enabled():
        payload["warnings"].append("OCR de imágenes deshabilitado por feature flag.")
        return payload

    try:
        from PIL import Image
        import pytesseract
    except ImportError:
        payload["warnings"].append("Faltan dependencias OCR ('Pillow' y/o 'pytesseract').")
        return payload

    try:
        image = Image.open(io.BytesIO(file_bytes))
    except Exception as exc:
        payload["warnings"].append(f"No se pudo abrir la imagen para OCR: {exc}")
        return payload

    payload["backend"] = "pytesseract"
    payload["metadata"]["image_size"] = {
        "width": int(getattr(image, "width", 0) or 0),
        "height": int(getattr(image, "height", 0) or 0),
    }

    try:
        raw_text = str(pytesseract.image_to_string(image) or "").strip()
    except Exception as exc:
        payload["warnings"].append(f"Falló image_to_string: {exc}")
        return payload

    payload["text"] = raw_text

    try:
        ocr_data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
    except Exception as exc:
        payload["warnings"].append(f"No se pudo obtener layout OCR estructurado: {exc}")
        ocr_data = {}

    blocks: list[dict[str, Any]] = []
    confidences: list[float] = []
    if isinstance(ocr_data, dict):
        texts = list(ocr_data.get("text") or [])
        lefts = list(ocr_data.get("left") or [])
        tops = list(ocr_data.get("top") or [])
        widths = list(ocr_data.get("width") or [])
        heights = list(ocr_data.get("height") or [])
        confs = list(ocr_data.get("conf") or [])

        total_items = min(len(texts), len(lefts), len(tops), len(widths), len(heights), len(confs))
        for index in range(total_items):
            text = str(texts[index] or "").strip()
            if not text:
                continue

            try:
                confidence = max(0.0, min(100.0, float(confs[index])))
            except (TypeError, ValueError):
                confidence = 0.0

            bbox = [
                float(lefts[index] or 0),
                float(tops[index] or 0),
                float((lefts[index] or 0) + (widths[index] or 0)),
                float((tops[index] or 0) + (heights[index] or 0)),
            ]
            confidences.append(confidence / 100.0)
            blocks.append(
                {
                    "block_id": f"ocr-word-{index + 1}",
                    "block_type": "ocr_word",
                    "text": text,
                    "bbox": bbox,
                    "confidence": confidence / 100.0,
                }
            )

    payload["blocks"] = blocks
    payload["lines"] = _group_ocr_blocks_into_lines(blocks)
    payload["tables"] = _reconstruct_ocr_tables(blocks)
    payload["metadata"]["table_count"] = len(payload["tables"])
    payload["confidence"] = (
        sum(confidences) / len(confidences)
        if confidences
        else (0.75 if raw_text else 0.0)
    )
    if not raw_text:
        payload["warnings"].append("La imagen no produjo texto OCR usable.")
    return payload
