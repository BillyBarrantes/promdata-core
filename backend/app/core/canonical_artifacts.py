from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ArtifactSupportLevel(str, Enum):
    FULL_ANALYTICS = "full_analytics"
    DOCUMENT_QA = "document_qa"
    OCR_ONLY = "ocr_only"
    CONVERSION_REQUIRED = "conversion_required"
    UNSUPPORTED = "unsupported"


class ArtifactOperationalMode(str, Enum):
    ANALYTICAL = "analytical"
    DOCUMENT_INTELLIGENCE = "document_intelligence"
    HYBRID = "hybrid"
    UNSUPPORTED = "unsupported"


class ArtifactAvailabilityStatus(str, Enum):
    ACTIVE = "active"
    PLANNED = "planned"
    DISABLED = "disabled"
    UNSUPPORTED = "unsupported"


class ArtifactSourceKind(str, Enum):
    SPREADSHEET = "spreadsheet"
    DELIMITED_TEXT = "delimited_text"
    PDF = "pdf"
    WORD_PROCESSOR = "word_processor"
    IMAGE = "image"
    PLAIN_TEXT = "plain_text"
    UNKNOWN = "unknown"


class ArtifactParserFamily(str, Enum):
    PANDAS_TABULAR = "pandas_tabular"
    PDF_TEXT = "pdf_text"
    PDF_LAYOUT = "pdf_layout"
    WORD_OPENXML = "word_openxml"
    OLE_CONVERSION = "ole_conversion"
    OCR_LAYOUT = "ocr_layout"
    TEXT_PLAIN = "text_plain"
    UNKNOWN = "unknown"


class CanonicalMaterializationStatus(str, Enum):
    READY = "ready"
    PREVIEW_ONLY = "preview_only"
    DEFERRED = "deferred"
    EMPTY = "empty"


class ArtifactDetectionEvidence(BaseModel):
    extension: str | None = None
    mime_type: str | None = None
    signature_label: str | None = None
    signature_matched: bool = False
    detection_confidence: float = 0.0


class ArtifactLineageRef(BaseModel):
    source_id: str | None = None
    file_id: str | None = None
    sheet_name: str | None = None
    page_number: int | None = None
    table_id: str | None = None
    row_start: int | None = None
    row_end: int | None = None
    column_names: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CanonicalSourceManifest(BaseModel):
    file_name: str
    mime_type: str | None = None
    size_bytes: int = 0
    extension: str | None = None
    source_kind: ArtifactSourceKind = ArtifactSourceKind.UNKNOWN
    parser_family: ArtifactParserFamily = ArtifactParserFamily.UNKNOWN
    support_level: ArtifactSupportLevel = ArtifactSupportLevel.UNSUPPORTED
    availability_status: ArtifactAvailabilityStatus = ArtifactAvailabilityStatus.UNSUPPORTED
    preferred_mode: ArtifactOperationalMode = ArtifactOperationalMode.UNSUPPORTED
    candidate_modes: list[ArtifactOperationalMode] = Field(default_factory=list)
    requires_ocr: bool = False
    requires_conversion: bool = False
    analytics_ready: bool = False
    detection: ArtifactDetectionEvidence = Field(default_factory=ArtifactDetectionEvidence)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CanonicalTabularFrame(BaseModel):
    frame_id: str
    label: str
    row_count: int = 0
    column_count: int = 0
    column_names: list[str] = Field(default_factory=list)
    extraction_confidence: float = 1.0
    lineage: list[ArtifactLineageRef] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CanonicalTextBlock(BaseModel):
    block_id: str
    text: str
    block_type: str = "paragraph"
    page_number: int | None = None
    bbox: list[float] = Field(default_factory=list)
    extraction_confidence: float = 1.0
    lineage: list[ArtifactLineageRef] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CanonicalLayoutBlock(BaseModel):
    block_id: str
    block_type: str
    page_number: int | None = None
    bbox: list[float] = Field(default_factory=list)
    text_excerpt: str | None = None
    extraction_confidence: float = 1.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class CanonicalFrameRelation(BaseModel):
    relation_id: str
    relation_type: str
    left_frame_id: str
    right_frame_id: str
    confidence: float = 0.0
    join_keys: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CanonicalMaterializedFrame(BaseModel):
    frame_id: str
    label: str
    status: CanonicalMaterializationStatus = CanonicalMaterializationStatus.EMPTY
    relation_type: str | None = None
    join_keys: list[str] = Field(default_factory=list)
    row_count: int = 0
    column_names: list[str] = Field(default_factory=list)
    records: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CanonicalMaterializedView(BaseModel):
    view_id: str
    view_type: str
    status: CanonicalMaterializationStatus = CanonicalMaterializationStatus.EMPTY
    source_frame_ids: list[str] = Field(default_factory=list)
    row_count: int = 0
    column_names: list[str] = Field(default_factory=list)
    records: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CanonicalMaterializedBundle(BaseModel):
    primary_frame_id: str | None = None
    status: CanonicalMaterializationStatus = CanonicalMaterializationStatus.EMPTY
    primary_frame: CanonicalMaterializedFrame | None = None
    related_frames: list[CanonicalMaterializedFrame] = Field(default_factory=list)
    derived_views: list[CanonicalMaterializedView] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CanonicalAnalyticalCandidate(BaseModel):
    candidate_id: str
    table_name: str
    source_ids: list[str] = Field(default_factory=list)
    status: CanonicalMaterializationStatus = CanonicalMaterializationStatus.EMPTY
    row_count: int = 0
    column_count: int = 0
    schema_profile: dict[str, Any] = Field(default_factory=dict)
    topology_rules: dict[str, Any] = Field(default_factory=dict)
    dataset_contract: dict[str, Any] = Field(default_factory=dict)
    currency_meta: dict[str, Any] = Field(default_factory=dict)
    literal_filter_catalog: dict[str, Any] = Field(default_factory=dict)
    translator_context_summary: str = ""
    reference_date: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CanonicalAnalyticalContractBundle(BaseModel):
    selected_candidate_id: str | None = None
    candidates: list[CanonicalAnalyticalCandidate] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CanonicalArtifactBundle(BaseModel):
    version: str = "enterprise_canonical_v1"
    source_manifest: CanonicalSourceManifest
    tabular_frames: list[CanonicalTabularFrame] = Field(default_factory=list)
    text_blocks: list[CanonicalTextBlock] = Field(default_factory=list)
    layout_blocks: list[CanonicalLayoutBlock] = Field(default_factory=list)
    frame_relations: list[CanonicalFrameRelation] = Field(default_factory=list)
    extraction_confidence: float = 0.0
    lineage: list[ArtifactLineageRef] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def has_tabular_payload(self) -> bool:
        return bool(self.tabular_frames)

    def has_document_payload(self) -> bool:
        return bool(self.text_blocks or self.layout_blocks)
