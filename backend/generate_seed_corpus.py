from __future__ import annotations

import io
import json
import os
from pathlib import Path
import sys
import types
import zipfile
import zlib

import pandas as pd

from app.core.config import settings
from app.services.canonical_dark_runtime_orchestrator import (
    run_canonical_dark_pipeline_for_uploaded_file,
    summarize_canonical_dark_pipeline_result,
)
from app.services.canonical_shadow_format_comparator import (
    build_shadow_format_readiness_summary,
    summarize_shadow_corpus_readiness,
)
from app.services.cloud_imports import DASH_UPLOADS_BUCKET


class _FakeResponse:
    def __init__(self, data):
        self.data = data
        self.error = None


class _FakeBucket:
    def __init__(self, storage: dict[str, dict[str, bytes]], bucket_name: str):
        self.storage = storage
        self.bucket_name = bucket_name

    def upload(self, path: str, content: bytes, _options=None):
        self.storage.setdefault(self.bucket_name, {})[path] = content

    def download(self, path: str) -> bytes:
        return self.storage.get(self.bucket_name, {}).get(path, b"")


class _FakeStorage:
    def __init__(self):
        self.buckets: dict[str, dict[str, bytes]] = {}

    def from_(self, bucket_name: str):
        return _FakeBucket(self.buckets, bucket_name)


class _FakeTable:
    def __init__(self, client, name: str):
        self.client = client
        self.name = name
        self.filters: list[tuple[str, object]] = []
        self._limit: int | None = None

    def select(self, _fields: str):
        return self

    def eq(self, key: str, value):
        self.filters.append((key, value))
        return self

    def limit(self, value: int):
        self._limit = value
        return self

    def execute(self):
        rows = self.client.tables.setdefault(self.name, [])

        def matches(row: dict) -> bool:
            return all(row.get(key) == value for key, value in self.filters)

        result = [dict(row) for row in rows if matches(row)]
        if self._limit is not None:
            result = result[: self._limit]
        return _FakeResponse(result)


class _FakeServiceClient:
    def __init__(self):
        self.tables = {"uploaded_files": []}
        self.storage = _FakeStorage()

    def table(self, name: str):
        return _FakeTable(self, name)


def _pdf_escape(text: str) -> str:
    return str(text or "").replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _build_seed_pdf_bytes() -> bytes:
    lines = [
        "Reporte de ventas semilla",
        "Resumen ejecutivo de ingresos por canal",
        "Canal | Ingreso | Margen",
        "Online | 1200 | 18",
        "Retail | 900 | 12",
        "Wholesale | 1500 | 22",
    ]
    content_lines = ["BT", "/F1 12 Tf", "50 780 Td"]
    first = True
    for line in lines:
        if not first:
            content_lines.append("0 -18 Td")
        content_lines.append(f"({_pdf_escape(line)}) Tj")
        first = False
    content_lines.append("ET")
    content = "\n".join(content_lines).encode("utf-8")

    objects = [
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj",
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj",
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj",
        b"4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj",
        b"5 0 obj << /Length %d >> stream\n%s\nendstream endobj" % (len(content), content),
    ]

    buffer = io.BytesIO()
    buffer.write(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(buffer.tell())
        buffer.write(obj)
        buffer.write(b"\n")

    xref_start = buffer.tell()
    buffer.write(f"xref\n0 {len(offsets)}\n".encode("ascii"))
    buffer.write(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        buffer.write(f"{offset:010d} 00000 n \n".encode("ascii"))
    buffer.write(
        (
            f"trailer << /Size {len(offsets)} /Root 1 0 R >>\n"
            f"startxref\n{xref_start}\n%%EOF\n"
        ).encode("ascii")
    )
    return buffer.getvalue()


def _build_seed_docx_bytes() -> bytes:
    document_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>Reporte operativo semilla</w:t></w:r></w:p>
    <w:p><w:r><w:t>El documento incluye texto narrativo y una tabla de indicadores.</w:t></w:r></w:p>
    <w:tbl>
      <w:tr>
        <w:tc><w:p><w:r><w:t>Área</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Costo</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Variación</w:t></w:r></w:p></w:tc>
      </w:tr>
      <w:tr>
        <w:tc><w:p><w:r><w:t>Finanzas</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>500</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>4</w:t></w:r></w:p></w:tc>
      </w:tr>
      <w:tr>
        <w:tc><w:p><w:r><w:t>RRHH</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>320</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>2</w:t></w:r></w:p></w:tc>
      </w:tr>
    </w:tbl>
  </w:body>
</w:document>
"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zipped:
        zipped.writestr("[Content_Types].xml", "<Types></Types>")
        zipped.writestr("_rels/.rels", "<Relationships></Relationships>")
        zipped.writestr("word/document.xml", document_xml)
    return buffer.getvalue()


def _build_seed_csv_bytes() -> bytes:
    return (
        "fecha,canal,ingreso,margen\n"
        "2026-01-01,Online,1200,18\n"
        "2026-01-02,Retail,900,12\n"
        "2026-01-03,Wholesale,1500,22\n"
    ).encode("utf-8")


def _build_seed_xlsx_bytes() -> bytes:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame(
            [
                {"fecha": "2026-01-01", "canal": "Online", "ingreso": 1200, "margen": 18},
                {"fecha": "2026-01-02", "canal": "Retail", "ingreso": 900, "margen": 12},
                {"fecha": "2026-01-03", "canal": "Wholesale", "ingreso": 1500, "margen": 22},
            ]
        ).to_excel(writer, sheet_name="Ventas", index=False)
    return buffer.getvalue()


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    return (
        len(data).to_bytes(4, "big")
        + chunk_type
        + data
        + zlib.crc32(chunk_type + data).to_bytes(4, "big")
    )


def _build_seed_png_bytes(width: int = 64, height: int = 32) -> bytes:
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"\x08\x00\x00\x00\x00"
    raw_rows = b"".join([b"\x00" + (b"\xff" * width) for _ in range(height)])
    idat = zlib.compress(raw_rows)
    return signature + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", idat) + _png_chunk(b"IEND", b"")


def _install_fake_seed_ocr_modules() -> None:
    class _FakeImage:
        width = 1200
        height = 500

    fake_image_module = types.ModuleType("PIL.Image")
    fake_image_module.open = lambda _stream: _FakeImage()
    fake_pil_module = types.ModuleType("PIL")
    fake_pil_module.Image = fake_image_module
    fake_pil_module.__version__ = "0.0-seed"

    class _FakeTesseractOutput:
        DICT = "DICT"

    fake_tesseract = types.ModuleType("pytesseract")
    fake_tesseract.Output = _FakeTesseractOutput
    fake_tesseract.image_to_string = lambda _image: "Canal Ingreso Margen\nOnline 1200 18\nRetail 900 12"
    fake_tesseract.image_to_data = lambda _image, output_type=None: {
        "text": [
            "Canal", "Ingreso", "Margen",
            "Online", "1200", "18",
            "Retail", "900", "12",
        ],
        "left": [80, 320, 560, 80, 320, 560, 80, 320, 560],
        "top": [40, 40, 40, 95, 95, 95, 150, 150, 150],
        "width": [120, 120, 120, 120, 120, 60, 120, 120, 60],
        "height": [24, 24, 24, 24, 24, 24, 24, 24, 24],
        "conf": ["95", "95", "95", "92", "92", "92", "90", "90", "90"],
    }

    sys.modules["PIL"] = fake_pil_module
    sys.modules["PIL.Image"] = fake_image_module
    sys.modules["pytesseract"] = fake_tesseract


def _write_seed_file(output_dir: Path, file_name: str, file_bytes: bytes) -> Path:
    path = output_dir / file_name
    path.write_bytes(file_bytes)
    return path


def _seed_uploaded_file(client: _FakeServiceClient, *, file_id: str, file_name: str, storage_path: str, file_bytes: bytes) -> dict:
    row = {
        "id": file_id,
        "user_id": "seed-user",
        "team_id": "seed-team",
        "file_name": file_name,
        "storage_path": storage_path,
        "created_at": "2026-05-05T00:00:00+00:00",
    }
    client.tables["uploaded_files"].append(row)
    client.storage.from_(DASH_UPLOADS_BUCKET).upload(storage_path, file_bytes, {})
    return row


def main() -> None:
    output_dir = Path("/tmp/promdata_seed_corpus")
    output_dir.mkdir(parents=True, exist_ok=True)
    seed_enable_native_tabular = os.getenv("SEED_ENABLE_NATIVE_TABULAR", "1").strip().lower() in {"1", "true", "yes", "on"}
    seed_enable_quality_gate = os.getenv("SEED_ENABLE_QUALITY_GATE", "0").strip().lower() in {"1", "true", "yes", "on"}
    seed_enable_image_ocr = os.getenv("SEED_ENABLE_IMAGE_OCR", "0").strip().lower() in {"1", "true", "yes", "on"}
    seed_fake_ocr = os.getenv("SEED_FAKE_OCR", "1").strip().lower() in {"1", "true", "yes", "on"}

    settings.CANONICAL_NATIVE_TABULAR_EXTRACTION_ENABLED = seed_enable_native_tabular
    settings.FILE_INTELLIGENCE_ENABLE_PDF_TABLE_EXTRACTION = False
    settings.FILE_INTELLIGENCE_ENABLE_IMAGE_OCR = seed_enable_image_ocr
    settings.CANONICAL_DOCUMENT_TABLE_QUALITY_GATE_ENABLED = seed_enable_quality_gate
    settings.CANONICAL_IBIS_PREVIEW_RUNTIME_ENABLED = False
    settings.CANONICAL_ANALYTICAL_CONTRACT_ADAPTER_ENABLED = False
    settings.CANONICAL_DARK_RUNTIME_ORCHESTRATOR_ENABLED = False

    csv_bytes = _build_seed_csv_bytes()
    xlsx_bytes = _build_seed_xlsx_bytes()
    pdf_bytes = _build_seed_pdf_bytes()
    docx_bytes = _build_seed_docx_bytes()
    png_bytes = _build_seed_png_bytes()

    if seed_enable_image_ocr and seed_fake_ocr:
        _install_fake_seed_ocr_modules()

    csv_path = _write_seed_file(output_dir, "seed_report.csv", csv_bytes)
    xlsx_path = _write_seed_file(output_dir, "seed_report.xlsx", xlsx_bytes)
    pdf_path = _write_seed_file(output_dir, "seed_report.pdf", pdf_bytes)
    docx_path = _write_seed_file(output_dir, "seed_report.docx", docx_bytes)
    png_path = _write_seed_file(output_dir, "seed_report.png", png_bytes)

    client = _FakeServiceClient()
    files = [
        _seed_uploaded_file(
            client,
            file_id="seed-csv-1",
            file_name="seed_report.csv",
            storage_path="seed-user/seed_report.csv",
            file_bytes=csv_bytes,
        ),
        _seed_uploaded_file(
            client,
            file_id="seed-xlsx-1",
            file_name="seed_report.xlsx",
            storage_path="seed-user/seed_report.xlsx",
            file_bytes=xlsx_bytes,
        ),
        _seed_uploaded_file(
            client,
            file_id="seed-pdf-1",
            file_name="seed_report.pdf",
            storage_path="seed-user/seed_report.pdf",
            file_bytes=pdf_bytes,
        ),
        _seed_uploaded_file(
            client,
            file_id="seed-docx-1",
            file_name="seed_report.docx",
            storage_path="seed-user/seed_report.docx",
            file_bytes=docx_bytes,
        ),
        _seed_uploaded_file(
            client,
            file_id="seed-png-1",
            file_name="seed_report.png",
            storage_path="seed-user/seed_report.png",
            file_bytes=png_bytes,
        ),
    ]

    report = {
        "flags": {
            "CANONICAL_NATIVE_TABULAR_EXTRACTION_ENABLED": settings.CANONICAL_NATIVE_TABULAR_EXTRACTION_ENABLED,
            "FILE_INTELLIGENCE_ENABLE_PDF_TABLE_EXTRACTION": settings.FILE_INTELLIGENCE_ENABLE_PDF_TABLE_EXTRACTION,
            "FILE_INTELLIGENCE_ENABLE_IMAGE_OCR": settings.FILE_INTELLIGENCE_ENABLE_IMAGE_OCR,
            "SEED_FAKE_OCR": seed_fake_ocr,
            "CANONICAL_DOCUMENT_TABLE_QUALITY_GATE_ENABLED": settings.CANONICAL_DOCUMENT_TABLE_QUALITY_GATE_ENABLED,
            "CANONICAL_IBIS_PREVIEW_RUNTIME_ENABLED": settings.CANONICAL_IBIS_PREVIEW_RUNTIME_ENABLED,
            "CANONICAL_ANALYTICAL_CONTRACT_ADAPTER_ENABLED": settings.CANONICAL_ANALYTICAL_CONTRACT_ADAPTER_ENABLED,
            "CANONICAL_DARK_RUNTIME_ORCHESTRATOR_ENABLED": settings.CANONICAL_DARK_RUNTIME_ORCHESTRATOR_ENABLED,
        },
        "generated_files": {
            "csv": str(csv_path),
            "xlsx": str(xlsx_path),
            "pdf": str(pdf_path),
            "docx": str(docx_path),
            "png": str(png_path),
        },
        "results": [],
    }

    mime_map = {
        "seed_report.csv": "text/csv",
        "seed_report.xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "seed_report.pdf": "application/pdf",
        "seed_report.docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "seed_report.png": "image/png",
    }

    readiness_rows: list[dict[str, object]] = []
    for row in files:
        if row["file_name"] == "seed_report.png" and seed_enable_image_ocr and seed_fake_ocr:
            _install_fake_seed_ocr_modules()
        result = run_canonical_dark_pipeline_for_uploaded_file(
            file_id=row["id"],
            service_client=client,
            uploaded_file_row=row,
            mime_type=mime_map[row["file_name"]],
        )
        format_readiness = build_shadow_format_readiness_summary(
            file_name=row["file_name"],
            pipeline_summary=summarize_canonical_dark_pipeline_result(result),
            bundle_summary=result.canonical_bundle_summary,
            materialized_summary=result.materialized_bundle_summary,
            preview_summary=result.preview_runtime_summary,
            analytical_summary=result.analytical_adapter_summary,
            runtime_comparison_summary=result.runtime_comparison_summary,
        )
        readiness_rows.append(format_readiness)
        report["results"].append(
            {
                "file_id": row["id"],
                "file_name": row["file_name"],
                "pipeline_summary": summarize_canonical_dark_pipeline_result(result),
                "format_readiness": format_readiness,
                "bundle_summary": result.canonical_bundle_summary,
                "materialized_summary": result.materialized_bundle_summary,
                "preview_summary": result.preview_runtime_summary,
                "analytical_summary": result.analytical_adapter_summary,
                "warnings": result.canonical_bundle.source_manifest.warnings,
            }
        )
    report["corpus_readiness"] = summarize_shadow_corpus_readiness(readiness_rows)

    report_path = output_dir / "seed_corpus_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
