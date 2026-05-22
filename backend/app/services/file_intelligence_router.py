import io
import zipfile
from typing import Any

from app.core.canonical_artifacts import (
    ArtifactAvailabilityStatus,
    ArtifactDetectionEvidence,
    ArtifactOperationalMode,
    ArtifactParserFamily,
    ArtifactSourceKind,
    ArtifactSupportLevel,
    CanonicalArtifactBundle,
    CanonicalSourceManifest,
)
from app.core.config import settings


_OLE_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_PDF_SIGNATURE = b"%PDF-"
_ZIP_SIGNATURES = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_JPEG_SIGNATURE = b"\xff\xd8\xff"
_GIF_SIGNATURES = (b"GIF87a", b"GIF89a")
_TIFF_SIGNATURES = (b"II*\x00", b"MM\x00*")

_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "tif", "tiff", "gif", "bmp", "webp"}
_TABULAR_EXTENSIONS = {"xlsx", "xls", "csv"}
_PLAIN_TEXT_EXTENSIONS = {"txt", "md"}


def _normalize_extension(file_name: str | None) -> str | None:
    raw_name = str(file_name or "").strip().lower()
    if "." not in raw_name:
        return None
    return raw_name.rsplit(".", 1)[-1]


def _detect_signature(file_bytes: bytes | None) -> str | None:
    if not file_bytes:
        return None

    head = file_bytes[:16]
    if head.startswith(_PDF_SIGNATURE):
        return "pdf"
    if head.startswith(_OLE_SIGNATURE):
        return "ole_compound"
    if any(head.startswith(signature) for signature in _ZIP_SIGNATURES):
        return "zip_container"
    if head.startswith(_PNG_SIGNATURE):
        return "png"
    if head.startswith(_JPEG_SIGNATURE):
        return "jpeg"
    if any(head.startswith(signature) for signature in _GIF_SIGNATURES):
        return "gif"
    if any(head.startswith(signature) for signature in _TIFF_SIGNATURES):
        return "tiff"
    return None


def _resolve_openxml_family(file_bytes: bytes | None) -> str | None:
    if not file_bytes:
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as zipped:
            names = set(zipped.namelist())
    except Exception:
        return None

    if "xl/workbook.xml" in names:
        return "xlsx"
    if "word/document.xml" in names:
        return "docx"
    return None


def _is_probably_text_mime(mime_type: str | None) -> bool:
    normalized = str(mime_type or "").strip().lower()
    return normalized.startswith("text/")


def _build_manifest(
    *,
    file_name: str,
    mime_type: str | None,
    size_bytes: int,
    extension: str | None,
    source_kind: ArtifactSourceKind,
    parser_family: ArtifactParserFamily,
    support_level: ArtifactSupportLevel,
    availability_status: ArtifactAvailabilityStatus,
    preferred_mode: ArtifactOperationalMode,
    candidate_modes: list[ArtifactOperationalMode],
    signature_label: str | None,
    signature_matched: bool,
    detection_confidence: float,
    requires_ocr: bool = False,
    requires_conversion: bool = False,
    analytics_ready: bool = False,
    warnings: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> CanonicalSourceManifest:
    return CanonicalSourceManifest(
        file_name=file_name,
        mime_type=mime_type,
        size_bytes=size_bytes,
        extension=extension,
        source_kind=source_kind,
        parser_family=parser_family,
        support_level=support_level,
        availability_status=availability_status,
        preferred_mode=preferred_mode,
        candidate_modes=candidate_modes,
        requires_ocr=requires_ocr,
        requires_conversion=requires_conversion,
        analytics_ready=analytics_ready,
        detection=ArtifactDetectionEvidence(
            extension=extension,
            mime_type=mime_type,
            signature_label=signature_label,
            signature_matched=signature_matched,
            detection_confidence=detection_confidence,
        ),
        warnings=warnings or [],
        metadata=metadata or {},
    )


class FileIntelligenceRouter:
    """
    Capa de clasificación de archivos enterprise.
    No reemplaza el runtime vigente; solo describe cómo debe rutearse cada archivo.
    """

    @staticmethod
    def inspect(
        *,
        file_name: str,
        file_bytes: bytes | None = None,
        mime_type: str | None = None,
        size_bytes: int | None = None,
    ) -> CanonicalSourceManifest:
        extension = _normalize_extension(file_name)
        detected_size = int(size_bytes if size_bytes is not None else len(file_bytes or b""))
        signature_label = _detect_signature(file_bytes)
        openxml_family = _resolve_openxml_family(file_bytes) if signature_label == "zip_container" else None
        strict_signatures = settings.FILE_INTELLIGENCE_STRICT_SIGNATURES

        if openxml_family == "xlsx" or extension in _TABULAR_EXTENSIONS:
            signature_matched = signature_label in {None, "zip_container", "ole_compound"} or openxml_family == "xlsx"
            confidence = 0.99 if signature_matched else 0.8
            warnings: list[str] = []
            if strict_signatures and extension == "xlsx" and openxml_family != "xlsx":
                warnings.append("La extensión indica XLSX pero la firma OpenXML no fue confirmada.")
            return _build_manifest(
                file_name=file_name,
                mime_type=mime_type,
                size_bytes=detected_size,
                extension=extension,
                source_kind=ArtifactSourceKind.SPREADSHEET if extension in {"xlsx", "xls"} or openxml_family == "xlsx" else ArtifactSourceKind.DELIMITED_TEXT,
                parser_family=ArtifactParserFamily.PANDAS_TABULAR,
                support_level=ArtifactSupportLevel.FULL_ANALYTICS,
                availability_status=ArtifactAvailabilityStatus.ACTIVE,
                preferred_mode=ArtifactOperationalMode.ANALYTICAL,
                candidate_modes=[ArtifactOperationalMode.ANALYTICAL],
                signature_label=openxml_family or signature_label,
                signature_matched=signature_matched,
                detection_confidence=confidence,
                analytics_ready=True,
                warnings=warnings,
            )

        if signature_label == "pdf" or extension == "pdf":
            return _build_manifest(
                file_name=file_name,
                mime_type=mime_type,
                size_bytes=detected_size,
                extension=extension,
                source_kind=ArtifactSourceKind.PDF,
                parser_family=ArtifactParserFamily.PDF_TEXT,
                support_level=ArtifactSupportLevel.DOCUMENT_QA,
                availability_status=ArtifactAvailabilityStatus.ACTIVE,
                preferred_mode=ArtifactOperationalMode.DOCUMENT_INTELLIGENCE,
                candidate_modes=[ArtifactOperationalMode.DOCUMENT_INTELLIGENCE, ArtifactOperationalMode.HYBRID],
                signature_label=signature_label,
                signature_matched=signature_label == "pdf",
                detection_confidence=0.98 if signature_label == "pdf" else 0.85,
                warnings=[
                    "El runtime actual trata PDF como documento; la extracción tabular enterprise aún no está conectada."
                ],
            )

        if openxml_family == "docx" or extension == "docx":
            signature_matched = openxml_family == "docx"
            return _build_manifest(
                file_name=file_name,
                mime_type=mime_type,
                size_bytes=detected_size,
                extension=extension,
                source_kind=ArtifactSourceKind.WORD_PROCESSOR,
                parser_family=ArtifactParserFamily.WORD_OPENXML,
                support_level=ArtifactSupportLevel.DOCUMENT_QA,
                availability_status=ArtifactAvailabilityStatus.PLANNED,
                preferred_mode=ArtifactOperationalMode.HYBRID,
                candidate_modes=[ArtifactOperationalMode.DOCUMENT_INTELLIGENCE, ArtifactOperationalMode.HYBRID],
                signature_label=openxml_family or signature_label,
                signature_matched=signature_matched,
                detection_confidence=0.95 if signature_matched else 0.8,
                warnings=[
                    "DOCX está clasificado para la nueva capa universal, pero su parser enterprise aún no está activado en runtime."
                ],
            )

        if signature_label == "ole_compound" or extension == "doc":
            return _build_manifest(
                file_name=file_name,
                mime_type=mime_type,
                size_bytes=detected_size,
                extension=extension,
                source_kind=ArtifactSourceKind.WORD_PROCESSOR,
                parser_family=ArtifactParserFamily.OLE_CONVERSION,
                support_level=ArtifactSupportLevel.CONVERSION_REQUIRED,
                availability_status=ArtifactAvailabilityStatus.PLANNED,
                preferred_mode=ArtifactOperationalMode.DOCUMENT_INTELLIGENCE,
                candidate_modes=[ArtifactOperationalMode.DOCUMENT_INTELLIGENCE],
                signature_label=signature_label,
                signature_matched=signature_label == "ole_compound",
                detection_confidence=0.92 if signature_label == "ole_compound" else 0.75,
                requires_conversion=True,
                warnings=[
                    "DOC binario requiere conversión controlada antes de extracción enterprise."
                ],
            )

        if extension in _IMAGE_EXTENSIONS or signature_label in {"png", "jpeg", "gif", "tiff"}:
            return _build_manifest(
                file_name=file_name,
                mime_type=mime_type,
                size_bytes=detected_size,
                extension=extension,
                source_kind=ArtifactSourceKind.IMAGE,
                parser_family=ArtifactParserFamily.OCR_LAYOUT,
                support_level=ArtifactSupportLevel.OCR_ONLY,
                availability_status=ArtifactAvailabilityStatus.PLANNED,
                preferred_mode=ArtifactOperationalMode.DOCUMENT_INTELLIGENCE,
                candidate_modes=[ArtifactOperationalMode.DOCUMENT_INTELLIGENCE, ArtifactOperationalMode.HYBRID],
                signature_label=signature_label,
                signature_matched=signature_label in {"png", "jpeg", "gif", "tiff"},
                detection_confidence=0.95 if signature_label else 0.78,
                requires_ocr=True,
                warnings=[
                    "Las imágenes necesitan OCR/layout antes de entrar al cerebro analítico."
                ],
            )

        if extension in _PLAIN_TEXT_EXTENSIONS or _is_probably_text_mime(mime_type):
            return _build_manifest(
                file_name=file_name,
                mime_type=mime_type,
                size_bytes=detected_size,
                extension=extension,
                source_kind=ArtifactSourceKind.PLAIN_TEXT,
                parser_family=ArtifactParserFamily.TEXT_PLAIN,
                support_level=ArtifactSupportLevel.DOCUMENT_QA,
                availability_status=ArtifactAvailabilityStatus.ACTIVE,
                preferred_mode=ArtifactOperationalMode.DOCUMENT_INTELLIGENCE,
                candidate_modes=[ArtifactOperationalMode.DOCUMENT_INTELLIGENCE],
                signature_label=signature_label,
                signature_matched=signature_label is None,
                detection_confidence=0.75 if extension in _PLAIN_TEXT_EXTENSIONS else 0.6,
            )

        return _build_manifest(
            file_name=file_name,
            mime_type=mime_type,
            size_bytes=detected_size,
            extension=extension,
            source_kind=ArtifactSourceKind.UNKNOWN,
            parser_family=ArtifactParserFamily.UNKNOWN,
            support_level=ArtifactSupportLevel.UNSUPPORTED,
            availability_status=ArtifactAvailabilityStatus.UNSUPPORTED,
            preferred_mode=ArtifactOperationalMode.UNSUPPORTED,
            candidate_modes=[ArtifactOperationalMode.UNSUPPORTED],
            signature_label=signature_label,
            signature_matched=bool(signature_label),
            detection_confidence=0.4 if signature_label or extension else 0.0,
            warnings=[
                "El formato no entra todavía en la matriz enterprise activa."
            ],
        )

    @staticmethod
    def build_empty_bundle(
        *,
        file_name: str,
        file_bytes: bytes | None = None,
        mime_type: str | None = None,
        size_bytes: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> CanonicalArtifactBundle:
        manifest = FileIntelligenceRouter.inspect(
            file_name=file_name,
            file_bytes=file_bytes,
            mime_type=mime_type,
            size_bytes=size_bytes,
        )
        return CanonicalArtifactBundle(
            source_manifest=manifest,
            extraction_confidence=manifest.detection.detection_confidence,
            metadata=metadata or {},
        )

    @staticmethod
    def is_runtime_enabled() -> bool:
        return settings.FILE_INTELLIGENCE_ROUTER_ENABLED

    @staticmethod
    def should_route_to_analytical_mode(manifest: CanonicalSourceManifest) -> bool:
        return manifest.analytics_ready and manifest.preferred_mode == ArtifactOperationalMode.ANALYTICAL

    @staticmethod
    def should_route_to_document_mode(manifest: CanonicalSourceManifest) -> bool:
        return manifest.preferred_mode in {
            ArtifactOperationalMode.DOCUMENT_INTELLIGENCE,
            ArtifactOperationalMode.HYBRID,
        }
