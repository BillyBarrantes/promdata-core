from typing import Any

from pydantic import BaseModel, Field
import uuid

class AnalysisRequest(BaseModel):
    """Modelo para la solicitud de un nuevo análisis."""
    file_id: str  # Mantenemos este como string, lo cual es correcto
    prompt: str

class AnalysisTaskResponse(BaseModel):
    """Modelo para la respuesta al iniciar un análisis."""
    # --- CAMBIO AQUÍ ---
    # Cambiamos el tipo de uuid.UUID a str para evitar problemas de serialización.
    task_id: str = Field(..., description="El ID único de la tarea de análisis creada.")


class AnalysisHistoryItemResponse(BaseModel):
    task_id: str
    file_id: str | None = None
    status: str
    created_at: str | None = None
    prompt_preview: str
    plan_count: int = 0
    intent_types: list[str] = Field(default_factory=list)
    filter_scope: list[str] = Field(default_factory=list)
    source_count: int = 0
    chart_count: int = 0
    metric_count: int = 0
    recommendation_count: int = 0
    format_override: str | None = None
    traceability_available: bool = False


class AnalysisHistoryResponse(BaseModel):
    items: list[AnalysisHistoryItemResponse] = Field(default_factory=list)

class PresentationCreate(BaseModel):
    """Modelo para crear una nueva presentación."""
    name: str
    file_id: str | None = None

class PresentationResponse(BaseModel):
    """Modelo de respuesta de una presentación."""
    id: str
    name: str
    file_id: str | None = None
    created_at: str

class PresentationUpdate(BaseModel):
    """Modelo para renombrar una presentación."""
    name: str


class DashboardExecutiveWidgetPayload(BaseModel):
    report_id: str
    title: str
    widget_type: str
    visual_type: str | None = None
    file_id: str | None = None
    metric: str | None = None
    dimension: str | None = None
    aggregation: str | None = None
    facts: list[str] = Field(default_factory=list)


class DashboardExecutiveSummaryRequest(BaseModel):
    presentation_id: str | None = None
    presentation_name: str | None = None
    global_filters: dict[str, str] = Field(default_factory=dict)
    widgets: list[DashboardExecutiveWidgetPayload] = Field(default_factory=list)


class DashboardExecutiveSummaryResponse(BaseModel):
    headline: str
    overview: str
    key_findings: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    widget_count: int = 0
    mixed_sources: bool = False
    filter_scope: list[str] = Field(default_factory=list)

class ReportSaveRequest(BaseModel):
    """Modelo para guardar un reporte."""
    title: str
    content: dict
    file_id: str | None = None
    presentation_id: str | None = None

class ReportLayoutUpdateItem(BaseModel):
    """Coordenadas persistidas de un widget del dashboard."""
    report_id: str
    x: int
    y: int
    w: int
    h: int

class ReportLayoutBulkUpdateRequest(BaseModel):
    """Payload batch para persistir layouts de una presentación."""
    items: list[ReportLayoutUpdateItem]

class ChatMessage(BaseModel):
    """Modelo para guardar/recuperar mensajes del chat."""
    role: str # 'user' | 'assistant'
    content: list | dict | str # Flexibilidad para contenido rico o texto simple
    file_id: str | None = None
    created_at: str | None = None

class ChartRecipe(BaseModel):
    """Modelo de Contrato Reactivo (Receta) para Frontend DuckDB-Wasm"""
    title: str
    visual_protocol: str # 'bar_chart', 'line_chart', etc.
    x_axis: str | list[str] | None = None
    y_axis: str | list[str] | None = None
    sql_query: str
    options_overrides: dict = {} # Configuraciones adicionales de ECharts (colores, ejes, etc.)
    metric_polarity: str = "neutral"


class CloudConnectorCapabilities(BaseModel):
    can_import: bool
    can_watch: bool
    supports_webhook: bool
    supports_polling: bool


class CloudConnectorProviderResponse(BaseModel):
    id: str
    name: str
    category: str
    status: str
    configured: bool
    oauth_ready: bool
    auth_flow: str
    auth_start_path: str
    auth_callback_path: str
    watchdog_mode: str
    watchdog_enabled: bool
    capabilities: CloudConnectorCapabilities
    notes: str
    connected: bool = False
    connection_id: str | None = None
    connection_status: str | None = None
    connected_account_email: str | None = None
    connected_account_name: str | None = None
    watch_target_count: int = 0
    last_refreshed_at: str | None = None


class WatchdogStatusResponse(BaseModel):
    enabled: bool
    poll_interval_seconds: int
    configured_provider_count: int
    watchdog_provider_count: int
    configured_providers: list[str]
    watchdog_providers: list[str]
    connected_provider_count: int = 0
    active_target_count: int = 0
    pending_target_count: int = 0
    synced_target_count: int = 0
    fallback_provider_count: int = 0
    operational_state: str = "idle"
    summary: str = ""
    last_activity_at: str | None = None
    provider_states: list[dict[str, Any]] = Field(default_factory=list)


class OAuthAuthorizationResponse(BaseModel):
    provider: str
    auth_url: str
    state_expires_at: str
    return_to: str


class CloudRemoteFileItem(BaseModel):
    id: str
    name: str
    provider: str
    item_type: str
    extension: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None
    modified_at: str | None = None
    web_url: str | None = None
    download_url: str | None = None
    supports_analysis: bool = False
    ingest_source_type: str | None = None


class CloudRemoteFileListResponse(BaseModel):
    provider: str
    connected_account_email: str | None = None
    files: list[CloudRemoteFileItem]
    next_cursor: str | None = None
    current_folder_id: str | None = None


class CloudRemoteImportRequest(BaseModel):
    item_id: str


class CloudRemoteImportResponse(BaseModel):
    provider: str
    uploaded_file_id: str
    file_name: str
    storage_path: str
    source_type: str


class CloudWatchTargetRequest(BaseModel):
    item_id: str


class CloudWatchTargetResponse(BaseModel):
    id: str
    provider: str
    target_type: str
    target_id: str
    target_name: str | None = None
    linked_file_id: str | None = None
    is_active: bool
    watchdog_mode: str
    contract_status: str | None = None
    pending_change: bool = False
    pending_change_summary: str | None = None
    sync_state: str | None = None
    last_known_modified_at: str | None = None
    last_known_size_bytes: int | None = None
    last_polled_at: str | None = None
    last_change_detected_at: str | None = None
    auto_sync_status: str | None = None
    last_auto_sync_at: str | None = None
    last_auto_sync_error: str | None = None
    last_auto_sync_job_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class CloudWatchTargetListResponse(BaseModel):
    provider: str
    targets: list[CloudWatchTargetResponse]


class CloudWatchdogPollRequest(BaseModel):
    provider: str | None = None


class CloudWatchdogPollChange(BaseModel):
    watch_target_id: str
    provider: str
    target_id: str
    target_name: str | None = None
    linked_file_id: str | None = None
    change_summary: str | None = None
    changed_at: str | None = None
    requires_reimport: bool = True


class CloudWatchdogPollResponse(BaseModel):
    checked_count: int
    new_change_count: int
    skipped_contract_count: int = 0
    error_count: int = 0
    auto_sync_enqueued_count: int = 0
    auto_sync_skipped_count: int = 0
    auto_sync_dispatch_failed_count: int = 0
    changes: list[CloudWatchdogPollChange]


class FilePreviewColumn(BaseModel):
    name: str
    inferred_type: str


class FilePreviewQualityAlert(BaseModel):
    code: str
    severity: str
    title: str
    message: str
    affected_columns: list[str] = Field(default_factory=list)


class FilePreviewColumnQualityIssue(BaseModel):
    name: str
    inferred_type: str
    non_null_count: int
    null_count: int
    null_ratio: float
    distinct_count: int
    invalid_count: int = 0
    outlier_count: int = 0
    issue_flags: list[str] = Field(default_factory=list)


class FilePreviewQualityProfile(BaseModel):
    health_score: int
    health_status: str
    null_cell_count: int
    null_cell_ratio: float
    duplicate_row_count: int
    duplicate_row_ratio: float
    ambiguous_column_count: int
    invalid_date_column_count: int
    outlier_column_count: int
    alert_count: int
    alerts: list[FilePreviewQualityAlert] = Field(default_factory=list)
    column_issues: list[FilePreviewColumnQualityIssue] = Field(default_factory=list)


class FilePreviewResponse(BaseModel):
    file_id: str
    file_name: str
    selected_sheet: str | None = None
    row_count: int
    column_count: int
    preview_limit: int
    file_size_bytes: int
    created_at: str | None = None
    columns: list[FilePreviewColumn]
    rows: list[dict[str, Any]]
    quality_profile: FilePreviewQualityProfile | None = None


class EnterpriseTelemetrySummaryResponse(BaseModel):
    telemetry_ready: bool = True
    window_days: int
    generated_at: str
    event_count: int = 0
    usage: dict[str, Any] = Field(default_factory=dict)
    confidence: dict[str, Any] = Field(default_factory=dict)
    product: dict[str, Any] = Field(default_factory=dict)
    latency: dict[str, Any] = Field(default_factory=dict)


class KnowledgeDocumentResponse(BaseModel):
    id: str
    title: str
    file_name: str
    bucket_name: str
    storage_path: str
    mime_type: str
    file_size_bytes: int
    source_kind: str
    status: str
    chunk_count: int = 0
    word_count: int = 0
    last_error: str | None = None
    created_at: str | None = None
    processed_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeDocumentListResponse(BaseModel):
    documents: list[KnowledgeDocumentResponse]


class KnowledgeDocumentUploadResponse(BaseModel):
    document: KnowledgeDocumentResponse
    task_status: str


class KnowledgeQueryRequest(BaseModel):
    query: str
    limit: int = 4
    document_ids: list[str] | None = None


class KnowledgeSnippetResponse(BaseModel):
    document_id: str
    document_title: str
    document_file_name: str
    chunk_index: int
    content: str
    similarity: float | None = None
    source_kind: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeQueryResponse(BaseModel):
    query: str
    count: int
    snippets: list[KnowledgeSnippetResponse]
    context_block: str = ""


class KnowledgeAskRequest(BaseModel):
    question: str
    limit: int = 4
    document_ids: list[str] | None = None


class KnowledgeCitationResponse(BaseModel):
    source_id: str
    document_id: str
    document_title: str
    document_file_name: str
    chunk_index: int
    snippet: str
    similarity: float | None = None
    source_kind: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeAskResponse(BaseModel):
    question: str
    answer: str
    citations: list[KnowledgeCitationResponse]
    snippets_used: int
    retrieved_count: int
    grounded: bool
    insufficient_evidence: bool
