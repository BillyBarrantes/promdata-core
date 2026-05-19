from __future__ import annotations

import io
import sys
import types
import zipfile

from app.core.config import settings
from app.services.artifact_extraction_service import (
    build_canonical_bundle_for_uploaded_file,
    is_canonical_extraction_pipeline_enabled,
    summarize_canonical_bundle,
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
        self.tables = {
            "uploaded_files": [],
        }
        self.storage = _FakeStorage()

    def table(self, name: str):
        return _FakeTable(self, name)


def _build_minimal_docx_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zipped:
        zipped.writestr("[Content_Types].xml", "<Types></Types>")
        zipped.writestr("word/document.xml", "<w:document></w:document>")
    return buffer.getvalue()


def _install_fake_docx_module(monkeypatch) -> None:
    class _FakeParagraph:
        def __init__(self, text: str):
            self.text = text

    class _FakeCell:
        def __init__(self, text: str):
            self.text = text

    class _FakeRow:
        def __init__(self, cells: list[str]):
            self.cells = [_FakeCell(value) for value in cells]

    class _FakeTable:
        def __init__(self, rows: list[list[str]]):
            self.rows = [_FakeRow(row) for row in rows]

    class _FakeDocument:
        def __init__(self, _stream):
            self.paragraphs = [_FakeParagraph("Resumen ejecutivo"), _FakeParagraph("Detalle operativo")]
            self.tables = [
                _FakeTable(
                    [
                        ["Departamento", "Headcount"],
                        ["Ventas", "12"],
                        ["Finanzas", "4"],
                    ]
                )
            ]

    fake_docx = types.ModuleType("docx")
    fake_docx.Document = _FakeDocument
    monkeypatch.setitem(sys.modules, "docx", fake_docx)


def test_phase8_canonical_extraction_pipeline_flag_defaults_to_off() -> None:
    assert is_canonical_extraction_pipeline_enabled() is False


def test_phase8_extraction_service_builds_bundle_for_uploaded_csv() -> None:
    client = _FakeServiceClient()
    client.tables["uploaded_files"].append(
        {
            "id": "file-1",
            "user_id": "user-1",
            "team_id": "team-1",
            "file_name": "dataset.csv",
            "storage_path": "user-1/dataset.csv",
            "created_at": "2026-05-05T00:00:00+00:00",
        }
    )
    client.storage.from_(DASH_UPLOADS_BUCKET).upload("user-1/dataset.csv", b"col1,col2\n1,2\n", {})

    bundle = build_canonical_bundle_for_uploaded_file(file_id="file-1", service_client=client, mime_type="text/csv")
    summary = summarize_canonical_bundle(bundle)

    assert summary["support_level"] == "full_analytics"
    assert summary["preferred_mode"] == "analytical"
    assert summary["tabular_frame_count"] == 1
    assert bundle.metadata["file_id"] == "file-1"


def test_phase8_extraction_service_builds_bundle_for_uploaded_docx(monkeypatch) -> None:
    _install_fake_docx_module(monkeypatch)
    client = _FakeServiceClient()
    client.tables["uploaded_files"].append(
        {
            "id": "file-2",
            "user_id": "user-1",
            "team_id": "team-1",
            "file_name": "memo.docx",
            "storage_path": "user-1/memo.docx",
            "created_at": "2026-05-05T00:00:00+00:00",
        }
    )
    client.storage.from_(DASH_UPLOADS_BUCKET).upload("user-1/memo.docx", _build_minimal_docx_bytes(), {})

    bundle = build_canonical_bundle_for_uploaded_file(
        file_id="file-2",
        service_client=client,
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    summary = summarize_canonical_bundle(bundle)

    assert summary["support_level"] == "document_qa"
    assert summary["preferred_mode"] in {"hybrid", "document_intelligence"}
    assert summary["parser_name"] == "docx_openxml"
    assert summary["tabular_frame_count"] == 1
    assert summary["layout_block_count"] >= 1
    assert bundle.metadata["primary_frame_id"] == "docx-table-1"


def test_phase8_extraction_service_promotes_document_table_when_quality_gate_passes(monkeypatch) -> None:
    _install_fake_docx_module(monkeypatch)
    monkeypatch.setattr(settings, "CANONICAL_DOCUMENT_TABLE_QUALITY_GATE_ENABLED", True)
    client = _FakeServiceClient()
    client.tables["uploaded_files"].append(
        {
            "id": "file-2b",
            "user_id": "user-1",
            "team_id": "team-1",
            "file_name": "memo.docx",
            "storage_path": "user-1/memo-analytics.docx",
            "created_at": "2026-05-05T00:00:00+00:00",
        }
    )
    client.storage.from_(DASH_UPLOADS_BUCKET).upload("user-1/memo-analytics.docx", _build_minimal_docx_bytes(), {})

    bundle = build_canonical_bundle_for_uploaded_file(
        file_id="file-2b",
        service_client=client,
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    summary = summarize_canonical_bundle(bundle)

    assert summary["analytics_ready"] is True
    assert summary["preferred_mode"] == "hybrid"
    assert summary["quality_gate_applied"] is True
    assert summary["analytics_ready_frame_count"] == 1
    assert bundle.tabular_frames[0].metadata["quality_gate"]["passed"] is True


def test_phase8_extraction_service_keeps_weak_document_table_in_document_mode(monkeypatch) -> None:
    class _WeakParagraph:
        def __init__(self, text: str):
            self.text = text

    class _WeakCell:
        def __init__(self, text: str):
            self.text = text

    class _WeakRow:
        def __init__(self, cells: list[str]):
            self.cells = [_WeakCell(value) for value in cells]

    class _WeakTableDoc:
        def __init__(self, _stream):
            self.paragraphs = [_WeakParagraph("Checklist")]
            self.tables = [type("Table", (), {"rows": [_WeakRow(["Observacion"]), _WeakRow(["OK"])]})()]

    fake_docx = types.ModuleType("docx")
    fake_docx.Document = _WeakTableDoc
    monkeypatch.setitem(sys.modules, "docx", fake_docx)
    monkeypatch.setattr(settings, "CANONICAL_DOCUMENT_TABLE_QUALITY_GATE_ENABLED", True)
    client = _FakeServiceClient()
    client.tables["uploaded_files"].append(
        {
            "id": "file-2c",
            "user_id": "user-1",
            "team_id": "team-1",
            "file_name": "weak.docx",
            "storage_path": "user-1/weak.docx",
            "created_at": "2026-05-05T00:00:00+00:00",
        }
    )
    client.storage.from_(DASH_UPLOADS_BUCKET).upload("user-1/weak.docx", _build_minimal_docx_bytes(), {})

    bundle = build_canonical_bundle_for_uploaded_file(
        file_id="file-2c",
        service_client=client,
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    summary = summarize_canonical_bundle(bundle)

    assert summary["analytics_ready"] is False
    assert summary["preferred_mode"] in {"hybrid", "document_intelligence"}
    assert summary["analytics_ready_frame_count"] == 0
    assert bundle.tabular_frames[0].metadata["quality_gate"]["passed"] is False
    assert any("no alcanzaron calidad analítica" in warning for warning in bundle.source_manifest.warnings)


def test_phase8_pdf_parser_can_emit_table_frames_when_optional_dependency_and_flag_exist(monkeypatch) -> None:
    client = _FakeServiceClient()
    client.tables["uploaded_files"].append(
        {
            "id": "file-3",
            "user_id": "user-1",
            "team_id": "team-1",
            "file_name": "report.pdf",
            "storage_path": "user-1/report.pdf",
            "created_at": "2026-05-05T00:00:00+00:00",
        }
    )
    client.storage.from_(DASH_UPLOADS_BUCKET).upload("user-1/report.pdf", b"%PDF-1.7\nfake", {})

    class _FakePdfPage:
        def __init__(self, text: str):
            self._text = text

        def extract_text(self) -> str:
            return self._text

    class _FakePdfReader:
        def __init__(self, _stream):
            self.pages = [_FakePdfPage("Resumen financiero")]

    class _FakePlumberPage:
        def extract_tables(self):
            return [
                [
                    ["Cuenta", "Monto"],
                    ["Ventas", "100"],
                    ["Costo", "50"],
                ]
            ]

    class _FakePdfPlumberContext:
        def __init__(self, _stream):
            self.pages = [_FakePlumberPage()]

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    fake_pypdf = types.ModuleType("pypdf")
    fake_pypdf.PdfReader = _FakePdfReader
    fake_pdfplumber = types.ModuleType("pdfplumber")
    fake_pdfplumber.open = lambda stream: _FakePdfPlumberContext(stream)

    monkeypatch.setitem(sys.modules, "pypdf", fake_pypdf)
    monkeypatch.setitem(sys.modules, "pdfplumber", fake_pdfplumber)
    monkeypatch.setattr(settings, "FILE_INTELLIGENCE_ENABLE_PDF_TABLE_EXTRACTION", True)

    bundle = build_canonical_bundle_for_uploaded_file(
        file_id="file-3",
        service_client=client,
        mime_type="application/pdf",
    )
    summary = summarize_canonical_bundle(bundle)

    assert summary["support_level"] == "document_qa"
    assert summary["tabular_frame_count"] == 1
    assert bundle.tabular_frames[0].column_names == ["Cuenta", "Monto"]
    assert bundle.metadata["primary_frame_id"] == "pdf-table-1-1"


def test_phase8_pdf_parser_consolidates_fragmented_multipage_tables(monkeypatch) -> None:
    client = _FakeServiceClient()
    client.tables["uploaded_files"].append(
        {
            "id": "file-3b",
            "user_id": "user-1",
            "team_id": "team-1",
            "file_name": "report-multipage.pdf",
            "storage_path": "user-1/report-multipage.pdf",
            "created_at": "2026-05-05T00:00:00+00:00",
        }
    )
    client.storage.from_(DASH_UPLOADS_BUCKET).upload("user-1/report-multipage.pdf", b"%PDF-1.7\nfake", {})

    class _FakePdfPage:
        def __init__(self, text: str):
            self._text = text

        def extract_text(self) -> str:
            return self._text

    class _FakePdfReader:
        def __init__(self, _stream):
            self.pages = [_FakePdfPage("Pagina 1"), _FakePdfPage("Pagina 2")]

    class _FakePlumberPage:
        def __init__(self, rows):
            self._rows = rows

        def extract_tables(self):
            return [self._rows]

    class _FakePdfPlumberContext:
        def __init__(self, _stream):
            self.pages = [
                _FakePlumberPage([["Cuenta", "Monto"], ["Ventas", "100"]]),
                _FakePlumberPage([["Cuenta", "Monto"], ["Costo", "50"]]),
            ]

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    fake_pypdf = types.ModuleType("pypdf")
    fake_pypdf.PdfReader = _FakePdfReader
    fake_pdfplumber = types.ModuleType("pdfplumber")
    fake_pdfplumber.open = lambda stream: _FakePdfPlumberContext(stream)

    monkeypatch.setitem(sys.modules, "pypdf", fake_pypdf)
    monkeypatch.setitem(sys.modules, "pdfplumber", fake_pdfplumber)
    monkeypatch.setattr(settings, "FILE_INTELLIGENCE_ENABLE_PDF_TABLE_EXTRACTION", True)

    bundle = build_canonical_bundle_for_uploaded_file(
        file_id="file-3b",
        service_client=client,
        mime_type="application/pdf",
    )

    assert len(bundle.tabular_frames) == 1
    assert bundle.tabular_frames[0].row_count == 2
    assert bundle.tabular_frames[0].metadata["fragment_consolidated"] is True


def test_phase8_image_parser_uses_ocr_gateway_when_enabled(monkeypatch) -> None:
    client = _FakeServiceClient()
    client.tables["uploaded_files"].append(
        {
            "id": "file-4",
            "user_id": "user-1",
            "team_id": "team-1",
            "file_name": "scan.png",
            "storage_path": "user-1/scan.png",
            "created_at": "2026-05-05T00:00:00+00:00",
        }
    )
    client.storage.from_(DASH_UPLOADS_BUCKET).upload("user-1/scan.png", b"\x89PNG\r\n\x1a\n", {})

    class _FakeImage:
        width = 1000
        height = 500

    fake_image_module = types.ModuleType("PIL.Image")
    fake_image_module.open = lambda _stream: _FakeImage()
    fake_pil_module = types.ModuleType("PIL")
    fake_pil_module.Image = fake_image_module

    class _FakeTesseractOutput:
        DICT = "DICT"

    fake_tesseract = types.ModuleType("pytesseract")
    fake_tesseract.Output = _FakeTesseractOutput
    fake_tesseract.image_to_string = lambda _image: "Texto OCR"
    fake_tesseract.image_to_data = lambda _image, output_type=None: {
        "text": ["Texto", "OCR"],
        "left": [10, 80],
        "top": [15, 15],
        "width": [50, 40],
        "height": [20, 20],
        "conf": ["92", "88"],
    }

    monkeypatch.setitem(sys.modules, "PIL", fake_pil_module)
    monkeypatch.setitem(sys.modules, "PIL.Image", fake_image_module)
    monkeypatch.setitem(sys.modules, "pytesseract", fake_tesseract)
    monkeypatch.setattr(settings, "FILE_INTELLIGENCE_ENABLE_IMAGE_OCR", True)

    bundle = build_canonical_bundle_for_uploaded_file(
        file_id="file-4",
        service_client=client,
        mime_type="image/png",
    )
    summary = summarize_canonical_bundle(bundle)

    assert summary["support_level"] == "ocr_only"
    assert summary["text_block_count"] == 1
    assert summary["layout_block_count"] == 2
    assert summary["parser_name"] == "ocr_layout_gate"


def test_phase8_image_parser_promotes_ocr_table_when_quality_gate_passes(monkeypatch) -> None:
    client = _FakeServiceClient()
    client.tables["uploaded_files"].append(
        {
            "id": "file-4b",
            "user_id": "user-1",
            "team_id": "team-1",
            "file_name": "scan-table.png",
            "storage_path": "user-1/scan-table.png",
            "created_at": "2026-05-05T00:00:00+00:00",
        }
    )
    client.storage.from_(DASH_UPLOADS_BUCKET).upload("user-1/scan-table.png", b"\x89PNG\r\n\x1a\n", {})

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
    monkeypatch.setattr(settings, "CANONICAL_DOCUMENT_TABLE_QUALITY_GATE_ENABLED", True)

    bundle = build_canonical_bundle_for_uploaded_file(
        file_id="file-4b",
        service_client=client,
        mime_type="image/png",
    )
    summary = summarize_canonical_bundle(bundle)

    assert summary["support_level"] == "ocr_only"
    assert summary["preferred_mode"] == "hybrid"
    assert summary["analytics_ready"] is True
    assert summary["analytics_ready_frame_count"] == 1
    assert summary["tabular_frame_count"] == 1
    assert bundle.tabular_frames[0].metadata["quality_gate"]["passed"] is True
