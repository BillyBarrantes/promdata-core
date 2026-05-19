from __future__ import annotations

import io
import sys
import types
import zipfile

import pandas as pd

from app.core.config import settings
from app.services.artifact_parser_registry import ArtifactParserRegistry


def _build_minimal_docx_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zipped:
        zipped.writestr("[Content_Types].xml", "<Types></Types>")
        zipped.writestr("word/document.xml", "<w:document></w:document>")
    return buffer.getvalue()


def _build_docx_with_text_and_table_bytes() -> bytes:
    document_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>Reporte de ventas semilla</w:t></w:r></w:p>
    <w:tbl>
      <w:tr>
        <w:tc><w:p><w:r><w:t>Canal</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Ingreso</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Margen</w:t></w:r></w:p></w:tc>
      </w:tr>
      <w:tr>
        <w:tc><w:p><w:r><w:t>Online</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>1200</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>18</w:t></w:r></w:p></w:tc>
      </w:tr>
      <w:tr>
        <w:tc><w:p><w:r><w:t>Retail</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>900</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>12</w:t></w:r></w:p></w:tc>
      </w:tr>
    </w:tbl>
  </w:body>
</w:document>
"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zipped:
        zipped.writestr("[Content_Types].xml", "<Types></Types>")
        zipped.writestr("word/document.xml", document_xml)
    return buffer.getvalue()


def _build_xlsx_bytes() -> bytes:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame(
            [
                {"fecha": "2026-01-01", "region": "North", "amount": 100},
                {"fecha": "2026-01-02", "region": "South", "amount": 120},
            ]
        ).to_excel(writer, sheet_name="Ventas", index=False)
        pd.DataFrame(
            [
                {"region": "North", "manager": "Ana"},
                {"region": "South", "manager": "Luis"},
            ]
        ).to_excel(writer, sheet_name="Managers", index=False)
    return buffer.getvalue()


def test_phase8_registry_routes_tabular_as_delegated_legacy_runtime(monkeypatch) -> None:
    monkeypatch.setattr(settings, "CANONICAL_NATIVE_TABULAR_EXTRACTION_ENABLED", False)

    bundle = ArtifactParserRegistry.parse_to_bundle(
        file_name="dataset.csv",
        file_bytes=b"col1,col2\n1,2\n",
        mime_type="text/csv",
    )

    assert bundle.source_manifest.analytics_ready is True
    assert bundle.source_manifest.preferred_mode.value == "analytical"
    assert bundle.tabular_frames
    assert bundle.tabular_frames[0].metadata["delegated"] is True


def test_phase8_registry_extracts_csv_natively_when_parallel_flag_enabled(monkeypatch) -> None:
    monkeypatch.setattr(settings, "CANONICAL_NATIVE_TABULAR_EXTRACTION_ENABLED", True)

    bundle = ArtifactParserRegistry.parse_to_bundle(
        file_name="dataset.csv",
        file_bytes=b"fecha,region,amount\n2026-01-01,North,100\n2026-01-02,South,120\n",
        mime_type="text/csv",
    )

    assert bundle.metadata["parser_name"] == "native_tabular_parallel"
    assert bundle.tabular_frames[0].metadata["delegated"] is False
    assert bundle.tabular_frames[0].row_count == 2
    assert bundle.tabular_frames[0].column_names == ["fecha", "region", "amount"]
    assert len(bundle.tabular_frames[0].metadata["rows_payload"]) == 2


def test_phase8_registry_extracts_xlsx_frames_natively_when_parallel_flag_enabled(monkeypatch) -> None:
    monkeypatch.setattr(settings, "CANONICAL_NATIVE_TABULAR_EXTRACTION_ENABLED", True)

    bundle = ArtifactParserRegistry.parse_to_bundle(
        file_name="dataset.xlsx",
        file_bytes=_build_xlsx_bytes(),
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    assert bundle.metadata["parser_name"] == "native_tabular_parallel"
    assert len(bundle.tabular_frames) == 2
    assert bundle.tabular_frames[0].metadata["sheet_name"] == "Ventas"
    assert bundle.tabular_frames[0].row_count == 2
    assert bundle.tabular_frames[0].column_names == ["fecha", "region", "amount"]
    assert bundle.tabular_frames[1].metadata["sheet_name"] == "Managers"


def test_phase8_registry_degrades_pdf_without_crashing() -> None:
    bundle = ArtifactParserRegistry.parse_to_bundle(
        file_name="broken.pdf",
        file_bytes=b"%PDF-1.7\nbroken",
        mime_type="application/pdf",
    )

    assert bundle.source_manifest.support_level.value == "document_qa"
    assert bundle.source_manifest.preferred_mode.value == "document_intelligence"
    assert bundle.metadata["parser_name"] == "pdf_text"
    assert isinstance(bundle.source_manifest.warnings, list)


def test_phase8_registry_extracts_pdf_text_table_without_optional_pdf_table_flag(monkeypatch) -> None:
    class _FakePdfPage:
        def extract_text(self) -> str:
            return "Reporte semilla\nCanal | Ingreso | Margen\nOnline | 1200 | 18\nRetail | 900 | 12\n"

    class _FakePdfReader:
        def __init__(self, _stream):
            self.pages = [_FakePdfPage()]

    fake_pypdf = types.ModuleType("pypdf")
    fake_pypdf.PdfReader = _FakePdfReader
    monkeypatch.setitem(sys.modules, "pypdf", fake_pypdf)

    bundle = ArtifactParserRegistry.parse_to_bundle(
        file_name="seed.pdf",
        file_bytes=b"%PDF-1.7\nfake",
        mime_type="application/pdf",
    )

    assert bundle.metadata["parser_name"] == "pdf_text"
    assert len(bundle.text_blocks) == 1
    assert len(bundle.tabular_frames) == 1
    assert bundle.tabular_frames[0].column_names == ["Canal", "Ingreso", "Margen"]
    assert bundle.tabular_frames[0].row_count == 2


def test_phase8_registry_classifies_docx_without_touching_runtime() -> None:
    bundle = ArtifactParserRegistry.parse_to_bundle(
        file_name="memo.docx",
        file_bytes=_build_minimal_docx_bytes(),
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    assert bundle.source_manifest.support_level.value == "document_qa"
    assert bundle.source_manifest.preferred_mode.value in {"hybrid", "document_intelligence"}
    assert bundle.metadata["parser_name"] == "docx_openxml"


def test_phase8_registry_extracts_docx_via_openxml_fallback_without_python_docx() -> None:
    bundle = ArtifactParserRegistry.parse_to_bundle(
        file_name="memo.docx",
        file_bytes=_build_docx_with_text_and_table_bytes(),
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    assert bundle.metadata["parser_name"] == "docx_openxml"
    assert len(bundle.text_blocks) >= 2
    assert len(bundle.tabular_frames) == 1
    assert bundle.tabular_frames[0].column_names == ["Canal", "Ingreso", "Margen"]
    assert bundle.tabular_frames[0].row_count == 2


def test_phase8_registry_marks_images_for_ocr_path() -> None:
    bundle = ArtifactParserRegistry.parse_to_bundle(
        file_name="scan.png",
        file_bytes=b"\x89PNG\r\n\x1a\n",
        mime_type="image/png",
    )

    assert bundle.source_manifest.support_level.value == "ocr_only"
    assert bundle.source_manifest.requires_ocr is True
    assert bundle.metadata["parser_name"] == "ocr_layout_gate"


def test_phase8_registry_extracts_ocr_table_when_image_ocr_enabled(monkeypatch) -> None:
    class _FakeImage:
        width = 1200
        height = 500

    fake_image_module = types.ModuleType("PIL.Image")
    fake_image_module.open = lambda _stream: _FakeImage()
    fake_pil_module = types.ModuleType("PIL")
    fake_pil_module.Image = fake_image_module

    class _FakeTesseractOutput:
        DICT = "DICT"

    fake_tesseract = types.ModuleType("pytesseract")
    fake_tesseract.Output = _FakeTesseractOutput
    fake_tesseract.image_to_string = lambda _image: "Canal Ingreso Margen\nOnline 1200 18\nRetail 900 12"
    fake_tesseract.image_to_data = lambda _image, output_type=None: {
        "text": ["Canal", "Ingreso", "Margen", "Online", "1200", "18", "Retail", "900", "12"],
        "left": [80, 320, 560, 80, 320, 560, 80, 320, 560],
        "top": [40, 40, 40, 95, 95, 95, 150, 150, 150],
        "width": [120, 120, 120, 120, 120, 60, 120, 120, 60],
        "height": [24, 24, 24, 24, 24, 24, 24, 24, 24],
        "conf": ["95", "95", "95", "92", "92", "92", "90", "90", "90"],
    }

    monkeypatch.setitem(sys.modules, "PIL", fake_pil_module)
    monkeypatch.setitem(sys.modules, "PIL.Image", fake_image_module)
    monkeypatch.setitem(sys.modules, "pytesseract", fake_tesseract)
    monkeypatch.setattr(settings, "FILE_INTELLIGENCE_ENABLE_IMAGE_OCR", True)

    bundle = ArtifactParserRegistry.parse_to_bundle(
        file_name="scan.png",
        file_bytes=b"\x89PNG\r\n\x1a\n",
        mime_type="image/png",
    )

    assert bundle.metadata["parser_name"] == "ocr_layout_gate"
    assert len(bundle.text_blocks) == 1
    assert len(bundle.tabular_frames) == 1
    assert bundle.tabular_frames[0].column_names == ["Canal", "Ingreso", "Margen"]
    assert bundle.tabular_frames[0].row_count == 2
