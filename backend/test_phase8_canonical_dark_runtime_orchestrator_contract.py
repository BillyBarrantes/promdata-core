from __future__ import annotations

import io
import sys
import types
import zipfile

import pandas as pd

from app.core.config import settings
from app.services.canonical_dark_runtime_orchestrator import (
    is_canonical_dark_runtime_orchestrator_enabled,
    run_canonical_dark_pipeline_for_uploaded_file,
    summarize_canonical_dark_pipeline_result,
)
from app.services.cloud_imports import DASH_UPLOADS_BUCKET
from app.services.data_engine import DataEngine


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
            self.paragraphs = [_FakeParagraph("Resumen operativo")]
            self.tables = [
                _FakeTable(
                    [
                        ["fecha", "region", "amount"],
                        ["2026-01-01", "North", "100"],
                        ["2026-01-02", "South", "120"],
                    ]
                )
            ]

    fake_docx = types.ModuleType("docx")
    fake_docx.Document = _FakeDocument
    monkeypatch.setitem(sys.modules, "docx", fake_docx)


def test_phase8_dark_runtime_orchestrator_flag_defaults_to_off() -> None:
    assert is_canonical_dark_runtime_orchestrator_enabled() is False


def test_phase8_dark_runtime_orchestrator_runs_end_to_end_without_active_runtime(monkeypatch) -> None:
    _install_fake_docx_module(monkeypatch)
    monkeypatch.setattr(DataEngine, "load_cached_dataset", staticmethod(lambda _file_id: None))

    client = _FakeServiceClient()
    client.tables["uploaded_files"].append(
        {
            "id": "file-1",
            "user_id": "00000000-0000-4000-8000-000000000001",
            "team_id": "00000000-0000-4000-8000-000000000002",
            "file_name": "memo.docx",
            "storage_path": "user-1/memo.docx",
            "created_at": "2026-05-05T00:00:00+00:00",
        }
    )
    client.storage.from_(DASH_UPLOADS_BUCKET).upload("user-1/memo.docx", _build_minimal_docx_bytes(), {})

    result = run_canonical_dark_pipeline_for_uploaded_file(
        file_id="file-1",
        service_client=client,
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    summary = summarize_canonical_dark_pipeline_result(result)

    assert summary["pipeline_status"] == "ready_for_shadow_compare"
    assert summary["selected_candidate_id"] is not None
    assert summary["candidate_count"] >= 1
    assert summary["comparison_grade"] == "no_active_runtime"
    assert summary["active_runtime_available"] is False


def test_phase8_dark_runtime_orchestrator_materializes_native_csv_when_parallel_flag_enabled(monkeypatch) -> None:
    monkeypatch.setattr(settings, "CANONICAL_NATIVE_TABULAR_EXTRACTION_ENABLED", True)
    monkeypatch.setattr(DataEngine, "load_cached_dataset", staticmethod(lambda _file_id: None))

    client = _FakeServiceClient()
    client.tables["uploaded_files"].append(
        {
            "id": "file-native-csv",
            "user_id": "00000000-0000-4000-8000-000000000001",
            "team_id": "00000000-0000-4000-8000-000000000002",
            "file_name": "dataset.csv",
            "storage_path": "user-1/dataset.csv",
            "created_at": "2026-05-05T00:00:00+00:00",
        }
    )
    client.storage.from_(DASH_UPLOADS_BUCKET).upload(
        "user-1/dataset.csv",
        b"fecha,region,amount\n2026-01-01,North,100\n2026-01-02,South,120\n",
        {},
    )

    result = run_canonical_dark_pipeline_for_uploaded_file(
        file_id="file-native-csv",
        service_client=client,
        mime_type="text/csv",
    )
    summary = summarize_canonical_dark_pipeline_result(result)

    assert summary["pipeline_status"] == "ready_for_shadow_compare"
    assert result.canonical_bundle_summary["parser_name"] == "native_tabular_parallel"
    assert result.materialized_bundle_summary["preview_ready_tables"] >= 1
    assert result.preview_runtime_summary["tables"][0]["row_count"] == 2
    assert result.preview_runtime_summary["tables"][0]["column_count"] == 3
    assert result.analytical_adapter_summary["candidates"][0]["metric_count"] >= 1


def test_phase8_dark_runtime_orchestrator_compares_against_active_runtime(monkeypatch) -> None:
    _install_fake_docx_module(monkeypatch)

    active_df = pd.DataFrame(
        [
            {"fecha": "2026-01-01", "region": "North", "amount": 100},
            {"fecha": "2026-01-02", "region": "South", "amount": 120},
        ]
    )
    active_df.attrs["schema_profile"] = {
        "fecha": {"type": "temporal", "role": "date"},
        "region": {"type": "categorical", "role": "dimension"},
        "amount": {"type": "numeric", "role": "metric"},
    }
    active_df.attrs["semantic_contract"] = {
        "dataset_mode": "flow",
        "time_axis": "fecha",
        "metric_columns": ["amount"],
        "dimension_columns": ["region"],
        "identifier_columns": [],
        "entity_key": "region",
    }
    monkeypatch.setattr(
        DataEngine,
        "load_cached_dataset",
        staticmethod(lambda _file_id: (active_df, "/tmp/fake.parquet", {"_schema_profile": active_df.attrs["schema_profile"], **active_df.attrs["semantic_contract"]})),
    )

    client = _FakeServiceClient()
    client.tables["uploaded_files"].append(
        {
            "id": "file-2",
            "user_id": "00000000-0000-4000-8000-000000000001",
            "team_id": "00000000-0000-4000-8000-000000000002",
            "file_name": "memo.docx",
            "storage_path": "user-1/memo2.docx",
            "created_at": "2026-05-05T00:00:00+00:00",
        }
    )
    client.storage.from_(DASH_UPLOADS_BUCKET).upload("user-1/memo2.docx", _build_minimal_docx_bytes(), {})

    result = run_canonical_dark_pipeline_for_uploaded_file(
        file_id="file-2",
        service_client=client,
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    assert result.runtime_comparison_summary["active_runtime_available"] is True
    assert result.runtime_comparison_summary["candidate_available"] is True
    assert result.runtime_comparison_summary["comparison_grade"] in {"high_alignment", "partial_alignment", "low_alignment"}


def test_phase8_dark_runtime_orchestrator_runs_image_shadow_pipeline_with_ocr_table(monkeypatch) -> None:
    monkeypatch.setattr(settings, "FILE_INTELLIGENCE_ENABLE_IMAGE_OCR", True)
    monkeypatch.setattr(settings, "CANONICAL_DOCUMENT_TABLE_QUALITY_GATE_ENABLED", True)
    monkeypatch.setattr(DataEngine, "load_cached_dataset", staticmethod(lambda _file_id: None))

    client = _FakeServiceClient()
    client.tables["uploaded_files"].append(
        {
            "id": "file-image-1",
            "user_id": "00000000-0000-4000-8000-000000000001",
            "team_id": "00000000-0000-4000-8000-000000000002",
            "file_name": "scan.png",
            "storage_path": "user-1/scan.png",
            "created_at": "2026-05-05T00:00:00+00:00",
        }
    )
    client.storage.from_(DASH_UPLOADS_BUCKET).upload("user-1/scan.png", b"\x89PNG\r\n\x1a\n", {})

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

    result = run_canonical_dark_pipeline_for_uploaded_file(
        file_id="file-image-1",
        service_client=client,
        mime_type="image/png",
    )
    summary = summarize_canonical_dark_pipeline_result(result)

    assert summary["pipeline_status"] == "ready_for_shadow_compare"
    assert summary["selected_candidate_id"] == "primary__ocr-table-1"
    assert result.canonical_bundle_summary["analytics_ready"] is True
    assert result.canonical_bundle_summary["tabular_frame_count"] == 1
