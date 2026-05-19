'use client'

import React, { useEffect, useMemo, useRef, useState } from "react";
import { Sidebar } from "@/components/sidebar";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useSupabase } from "@/lib/supabase-provider";
import { cn } from "@/lib/utils";
import { toast } from "sonner";
import {
  AlertCircle,
  AlertTriangle,
  BookOpen,
  CheckCircle2,
  Clock3,
  DatabaseZap,
  FileCode2,
  FileText,
  LoaderCircle,
  Search,
  RefreshCw,
  Upload,
  X,
} from "lucide-react";

interface KnowledgeDocument {
  id: string;
  title: string;
  file_name: string;
  bucket_name: string;
  storage_path: string;
  mime_type: string;
  file_size_bytes: number;
  source_kind: string;
  status: string;
  chunk_count: number;
  word_count: number;
  last_error?: string | null;
  created_at?: string | null;
  processed_at?: string | null;
  metadata: Record<string, unknown>;
}

interface KnowledgeDocumentListResponse {
  documents: KnowledgeDocument[];
}

interface KnowledgeDocumentUploadResponse {
  document: KnowledgeDocument;
  task_status: string;
}

interface KnowledgeSnippet {
  document_id: string;
  document_title: string;
  document_file_name: string;
  chunk_index: number;
  content: string;
  similarity?: number | null;
  source_kind: string;
  metadata: Record<string, unknown>;
}

interface KnowledgeQueryResponse {
  query: string;
  count: number;
  snippets: KnowledgeSnippet[];
  context_block: string;
}

interface KnowledgeAskCitation {
  source_id: string;
  document_id: string;
  document_title: string;
  document_file_name: string;
  chunk_index: number;
  snippet: string;
  similarity?: number | null;
  source_kind: string;
  metadata: Record<string, unknown>;
}

interface KnowledgeAskResponse {
  question: string;
  answer: string;
  citations: KnowledgeAskCitation[];
  snippets_used: number;
  retrieved_count: number;
  grounded: boolean;
  insufficient_evidence: boolean;
}

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";
const KNOWLEDGE_STATUS_POLL_MS = 5000;
const ACCEPTED_DOCUMENT_EXTENSIONS = [".pdf", ".txt", ".md"];
const ACCEPTED_DOCUMENT_MIME_TYPES = [
  "application/pdf",
  "text/plain",
  "text/markdown",
  "text/x-markdown",
];

const knowledgeDateFormatter = new Intl.DateTimeFormat("es-PE", {
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

const KNOWLEDGE_ACTION_BUTTON_CLASSNAME =
  "border-slate-200 bg-white text-slate-900 shadow-xs hover:bg-slate-50 hover:text-slate-900 disabled:border-slate-200 disabled:bg-slate-100 disabled:text-slate-400 disabled:opacity-100";

function getFileExtension(fileName: string): string {
  const extension = fileName.split(".").pop();
  return extension ? `.${extension.toLowerCase()}` : "";
}

function formatFileSize(sizeBytes: number): string {
  if (!sizeBytes || sizeBytes <= 0) return "Sin tamaño";
  if (sizeBytes < 1024) return `${sizeBytes} B`;
  if (sizeBytes < 1024 * 1024) return `${(sizeBytes / 1024).toFixed(1)} KB`;
  return `${(sizeBytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatKnowledgeDate(value?: string | null): string {
  if (!value) return "Sin fecha";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "Sin fecha";
  return knowledgeDateFormatter.format(parsed);
}

function getDocumentTypeLabel(document: KnowledgeDocument): string {
  const extension = getFileExtension(document.file_name);
  if (extension === ".pdf") return "PDF";
  if (extension === ".md") return "MD";
  if (extension === ".txt") return "TXT";
  if (document.source_kind === "pdf") return "PDF";
  return "TXT";
}

function getDocumentTypeIcon(document: KnowledgeDocument) {
  const extension = getFileExtension(document.file_name);
  if (extension === ".md") {
    return <FileCode2 className="h-4 w-4 text-sky-600" aria-hidden="true" />;
  }
  return <FileText className="h-4 w-4 text-slate-500" aria-hidden="true" />;
}

function isPendingKnowledgeStatus(status: string): boolean {
  const normalized = String(status || "").trim().toLowerCase();
  return normalized === "queued" || normalized === "processing";
}

function isRetryableKnowledgeStatus(status: string): boolean {
  const normalized = String(status || "").trim().toLowerCase();
  return normalized === "queued" || normalized === "error" || normalized === "failed";
}

function isStaleKnowledgeDocument(document: KnowledgeDocument): boolean {
  if (!isRetryableKnowledgeStatus(document.status)) return false;
  const referenceValue = document.processed_at || document.created_at;
  if (!referenceValue) return false;
  const parsed = new Date(referenceValue);
  if (Number.isNaN(parsed.getTime())) return false;
  return Date.now() - parsed.getTime() >= 90 * 1000;
}

function isAcceptedKnowledgeFile(file: File): boolean {
  const extension = getFileExtension(file.name);
  if (ACCEPTED_DOCUMENT_EXTENSIONS.includes(extension)) return true;
  return ACCEPTED_DOCUMENT_MIME_TYPES.includes(file.type);
}

function KnowledgeStatusBadge({ status }: { status: string }) {
  const normalized = String(status || "").trim().toLowerCase();

  if (normalized === "indexed") {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-200 bg-emerald-50 px-2.5 py-1 text-xs font-medium text-emerald-700">
        <CheckCircle2 className="h-3.5 w-3.5" />
        Indexado
      </span>
    );
  }

  if (normalized === "error" || normalized === "failed") {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full border border-rose-200 bg-rose-50 px-2.5 py-1 text-xs font-medium text-rose-700">
        <AlertCircle className="h-3.5 w-3.5" />
        Error
      </span>
    );
  }

  if (normalized === "processing") {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full border border-amber-200 bg-amber-50 px-2.5 py-1 text-xs font-medium text-amber-700">
        <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
        Procesando
      </span>
    );
  }

  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-amber-200 bg-amber-50 px-2.5 py-1 text-xs font-medium text-amber-700">
      <Clock3 className="h-3.5 w-3.5" />
      En cola
    </span>
  );
}

export default function ConocimientoPage() {
  const supabase = useSupabase();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isUploadSheetOpen, setIsUploadSheetOpen] = useState(false);
  const [uploadTitle, setUploadTitle] = useState("");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [isDraggingOver, setIsDraggingOver] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [windowFocused, setWindowFocused] = useState(true);
  const [pageVisible, setPageVisible] = useState(true);
  const [retryingDocumentId, setRetryingDocumentId] = useState<string | null>(null);
  const [knowledgeQuery, setKnowledgeQuery] = useState("");
  const [submittedKnowledgeQuery, setSubmittedKnowledgeQuery] = useState("");
  const [knowledgeAnswer, setKnowledgeAnswer] = useState("");
  const [knowledgeCitations, setKnowledgeCitations] = useState<KnowledgeAskCitation[]>([]);
  const [knowledgeRetrievedCount, setKnowledgeRetrievedCount] = useState(0);
  const [knowledgeSnippetsUsed, setKnowledgeSnippetsUsed] = useState(0);
  const [knowledgeGrounded, setKnowledgeGrounded] = useState(false);
  const [knowledgeInsufficientEvidence, setKnowledgeInsufficientEvidence] = useState(false);
  const [isQueryingKnowledge, setIsQueryingKnowledge] = useState(false);
  const [knowledgeQueryError, setKnowledgeQueryError] = useState<string | null>(null);
  const [expandedCitationIds, setExpandedCitationIds] = useState<Record<string, boolean>>({});

  const hasPendingDocuments = useMemo(
    () => documents.some((document) => isPendingKnowledgeStatus(document.status)),
    [documents]
  );
  const hasStalePendingDocuments = useMemo(
    () => documents.some((document) => isStaleKnowledgeDocument(document)),
    [documents]
  );
  const indexedDocumentsCount = useMemo(
    () => documents.filter((document) => String(document.status || "").trim().toLowerCase() === "indexed").length,
    [documents]
  );

  const fetchKnowledgeDocuments = async (options?: { silent?: boolean }) => {
    const isSilent = options?.silent ?? false;
    if (!isSilent) {
      setIsLoading(true);
    } else {
      setIsRefreshing(true);
    }

    try {
      const { data: { session } } = await supabase.auth.getSession();
      if (!session?.access_token) {
        setDocuments([]);
        return;
      }

      const response = await fetch(`${API_BASE_URL}/api/v1/knowledge/documents`, {
        headers: {
          Authorization: `Bearer ${session.access_token}`,
        },
        cache: "no-store",
      });

      const payload: KnowledgeDocumentListResponse | { detail?: string } = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error((payload as { detail?: string }).detail || "No se pudo cargar la base de conocimiento.");
      }

      const nextDocuments = Array.isArray((payload as KnowledgeDocumentListResponse).documents)
        ? (payload as KnowledgeDocumentListResponse).documents
        : [];
      setDocuments(nextDocuments);
    } catch (error: any) {
      if (!isSilent) {
        toast.error(error.message || "Error cargando documentos.");
      }
      setDocuments([]);
    } finally {
      setIsLoading(false);
      setIsRefreshing(false);
    }
  };

  useEffect(() => {
    void fetchKnowledgeDocuments();
  }, []);

  useEffect(() => {
    const handleFocus = () => setWindowFocused(true);
    const handleBlur = () => setWindowFocused(false);
    const handleVisibilityChange = () => setPageVisible(document.visibilityState === "visible");

    window.addEventListener("focus", handleFocus);
    window.addEventListener("blur", handleBlur);
    document.addEventListener("visibilitychange", handleVisibilityChange);

    return () => {
      window.removeEventListener("focus", handleFocus);
      window.removeEventListener("blur", handleBlur);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, []);

  useEffect(() => {
    if (!hasPendingDocuments || !windowFocused || !pageVisible) return;

    const interval = window.setInterval(() => {
      void fetchKnowledgeDocuments({ silent: true });
    }, KNOWLEDGE_STATUS_POLL_MS);

    return () => window.clearInterval(interval);
  }, [hasPendingDocuments, pageVisible, windowFocused]);

  const resetUploadState = () => {
    setUploadTitle("");
    setSelectedFile(null);
    setUploadError(null);
    setIsDraggingOver(false);
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  };

  const handleCloseUploadSheet = () => {
    if (isUploading) return;
    setIsUploadSheetOpen(false);
    resetUploadState();
  };

  const handleSelectFile = (file: File | null) => {
    if (!file) return;
    if (!isAcceptedKnowledgeFile(file)) {
      setUploadError("Solo se permiten archivos PDF, TXT o MD.");
      return;
    }

    setSelectedFile(file);
    setUploadError(null);
    if (!uploadTitle.trim()) {
      const fileNameWithoutExtension = file.name.replace(/\.[^.]+$/, "");
      setUploadTitle(fileNameWithoutExtension);
    }
  };

  const handleDrop = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.stopPropagation();
    setIsDraggingOver(false);
    const file = event.dataTransfer.files?.[0];
    handleSelectFile(file || null);
  };

  const handleUploadDocument = async () => {
    if (!selectedFile) {
      setUploadError("Selecciona un documento antes de subir.");
      return;
    }

    setIsUploading(true);
    setUploadError(null);

    try {
      const { data: { session } } = await supabase.auth.getSession();
      if (!session?.access_token) {
        throw new Error("Debes iniciar sesión para subir documentos.");
      }

      const formData = new FormData();
      formData.append("file", selectedFile);
      if (uploadTitle.trim()) {
        formData.append("title", uploadTitle.trim());
      }

      const response = await fetch(`${API_BASE_URL}/api/v1/knowledge/documents/upload`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${session.access_token}`,
        },
        body: formData,
      });

      const payload: KnowledgeDocumentUploadResponse | { detail?: string } = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error((payload as { detail?: string }).detail || "No se pudo subir el documento.");
      }

      const createdDocument = (payload as KnowledgeDocumentUploadResponse).document;
      setDocuments((current) => [
        createdDocument,
        ...current.filter((document) => document.id !== createdDocument.id),
      ]);
      toast.success(`Documento registrado: ${createdDocument.file_name}`);
      setIsUploadSheetOpen(false);
      resetUploadState();
      void fetchKnowledgeDocuments({ silent: true });
    } catch (error: any) {
      const message = error.message || "Error subiendo documento.";
      setUploadError(message);
      toast.error(message);
    } finally {
      setIsUploading(false);
    }
  };

  const handleRetryDocument = async (document: KnowledgeDocument) => {
    setRetryingDocumentId(document.id);

    try {
      const { data: { session } } = await supabase.auth.getSession();
      if (!session?.access_token) {
        throw new Error("Debes iniciar sesión para reencolar documentos.");
      }

      const response = await fetch(`${API_BASE_URL}/api/v1/knowledge/documents/${document.id}/retry`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${session.access_token}`,
        },
      });

      const payload: KnowledgeDocumentUploadResponse | { detail?: string } = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error((payload as { detail?: string }).detail || "No se pudo reencolar el documento.");
      }

      const queuedDocument = (payload as KnowledgeDocumentUploadResponse).document;
      setDocuments((current) => current.map((item) => (
        item.id === queuedDocument.id ? queuedDocument : item
      )));
      toast.success(`Documento reencolado: ${queuedDocument.file_name}`);
      void fetchKnowledgeDocuments({ silent: true });
    } catch (error: any) {
      toast.error(error.message || "Error reintentando indexación.");
    } finally {
      setRetryingDocumentId(null);
    }
  };

  const handleKnowledgeQuery = async (event?: React.FormEvent<HTMLFormElement>) => {
    event?.preventDefault();

    const trimmedQuery = knowledgeQuery.trim();
    if (!trimmedQuery) {
      setKnowledgeQueryError("Escribe una pregunta para consultar la base de conocimiento.");
      setKnowledgeAnswer("");
      setKnowledgeCitations([]);
      setKnowledgeRetrievedCount(0);
      setKnowledgeSnippetsUsed(0);
      setKnowledgeGrounded(false);
      setKnowledgeInsufficientEvidence(false);
      return;
    }

    setIsQueryingKnowledge(true);
    setKnowledgeQueryError(null);
    setSubmittedKnowledgeQuery(trimmedQuery);
    setExpandedCitationIds({});

    try {
      const { data: { session } } = await supabase.auth.getSession();
      if (!session?.access_token) {
        throw new Error("Debes iniciar sesión para consultar la base de conocimiento.");
      }

      const response = await fetch(`${API_BASE_URL}/api/v1/knowledge/ask`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${session.access_token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          question: trimmedQuery,
          limit: 4,
        }),
      });

      const payload: KnowledgeAskResponse | { detail?: string } = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error((payload as { detail?: string }).detail || "No se pudo consultar la base de conocimiento.");
      }

      const nextCitations = Array.isArray((payload as KnowledgeAskResponse).citations)
        ? (payload as KnowledgeAskResponse).citations
        : [];

      setKnowledgeAnswer((payload as KnowledgeAskResponse).answer || "");
      setKnowledgeCitations(nextCitations);
      setKnowledgeRetrievedCount((payload as KnowledgeAskResponse).retrieved_count || 0);
      setKnowledgeSnippetsUsed((payload as KnowledgeAskResponse).snippets_used || 0);
      setKnowledgeGrounded(Boolean((payload as KnowledgeAskResponse).grounded));
      setKnowledgeInsufficientEvidence(Boolean((payload as KnowledgeAskResponse).insufficient_evidence));
    } catch (error: any) {
      const message = error.message || "Error consultando la base de conocimiento.";
      setKnowledgeQueryError(message);
      setKnowledgeAnswer("");
      setKnowledgeCitations([]);
      setKnowledgeRetrievedCount(0);
      setKnowledgeSnippetsUsed(0);
      setKnowledgeGrounded(false);
      setKnowledgeInsufficientEvidence(false);
    } finally {
      setIsQueryingKnowledge(false);
    }
  };

  const toggleCitationExpansion = (citationKey: string) => {
    setExpandedCitationIds((current) => ({
      ...current,
      [citationKey]: !current[citationKey],
    }));
  };

  return (
    <div className="flex h-screen bg-background overflow-hidden">
      <Sidebar />
      <main className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <header className="border-b border-border px-6 py-4 shrink-0">
          <div className="flex items-center justify-between">
            <h1 className="text-lg font-semibold text-foreground"></h1>
            <div className="flex items-center gap-2">
              <div className="flex h-8 w-8 items-center justify-center rounded-full bg-blue-600 text-sm font-medium text-white">
                LB
              </div>
            </div>
          </div>
        </header>

        <div className="flex-1 overflow-y-auto bg-slate-50/60">
          <div className="mx-auto flex w-full max-w-7xl flex-col gap-6 px-6 py-8">
            <section className="flex flex-col gap-4 rounded-[28px] border border-slate-200/80 bg-white/90 p-8 shadow-sm shadow-slate-200/60">
              <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                <div className="space-y-3">
                  <div className="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs font-medium text-slate-600">
                    <BookOpen className="h-3.5 w-3.5" />
                    Contexto documental para análisis RAG
                  </div>
                  <div className="space-y-2">
                    <h2 className="text-3xl font-semibold tracking-tight text-slate-900">Base de Conocimiento</h2>
                    <p className="max-w-2xl text-sm leading-6 text-slate-500">
                      Sube normativas, planes corporativos y documentación institucional para que la capa analítica
                      pueda cruzar datos operativos con contexto de negocio.
                    </p>
                    {hasStalePendingDocuments ? (
                      <div className="inline-flex items-center gap-2 rounded-full border border-amber-200 bg-amber-50 px-3 py-1 text-xs font-medium text-amber-700">
                        <AlertCircle className="h-3.5 w-3.5" />
                        Hay documentos en cola que requieren reintento.
                      </div>
                    ) : null}
                  </div>
                </div>

                <div className="flex items-center gap-3">
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => void fetchKnowledgeDocuments({ silent: true })}
                    disabled={isLoading}
                    className={KNOWLEDGE_ACTION_BUTTON_CLASSNAME}
                  >
                    <RefreshCw className={cn("h-4 w-4", (isRefreshing || isLoading) && "animate-spin")} />
                    Actualizar
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => setIsUploadSheetOpen(true)}
                    className={cn("shrink-0", KNOWLEDGE_ACTION_BUTTON_CLASSNAME)}
                  >
                    <Upload className="h-4 w-4" />
                    Subir Documento
                  </Button>
                </div>
              </div>
            </section>

            <Card className="gap-0 overflow-hidden border-slate-200/80 bg-white shadow-sm shadow-slate-200/50">
              <CardHeader className="border-b border-slate-100 bg-white">
                <CardTitle className="text-lg text-slate-900">Consulta semántica directa</CardTitle>
                <CardDescription>
                  Busca políticas, definiciones y lineamientos institucionales ya indexados en la base vectorial.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-5 p-6">
                <form onSubmit={handleKnowledgeQuery} className="space-y-4">
                  <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
                    <div className="relative flex-1">
                      <Search className="pointer-events-none absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
                      <Input
                        value={knowledgeQuery}
                        onChange={(event) => setKnowledgeQuery(event.target.value)}
                        placeholder="Pregúntale a la base de conocimiento..."
                        className="h-12 rounded-2xl border-slate-200 pl-11 pr-4 text-sm shadow-sm"
                        disabled={isQueryingKnowledge}
                      />
                    </div>
                    <Button
                      type="submit"
                      variant="outline"
                      disabled={isQueryingKnowledge || indexedDocumentsCount === 0}
                      className={cn("h-12 min-w-[170px]", KNOWLEDGE_ACTION_BUTTON_CLASSNAME)}
                    >
                      {isQueryingKnowledge ? (
                        <>
                          <LoaderCircle className="h-4 w-4 animate-spin" />
                          Consultando...
                        </>
                      ) : (
                        <>
                          <DatabaseZap className="h-4 w-4" />
                          Consultar
                        </>
                      )}
                    </Button>
                  </div>

                  <div className="flex flex-wrap items-center gap-3 text-xs text-slate-500">
                    <span>{indexedDocumentsCount} documentos indexados disponibles para consulta</span>
                    {submittedKnowledgeQuery ? (
                      <span className="rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-slate-600">
                        Ultima consulta: {submittedKnowledgeQuery}
                      </span>
                    ) : null}
                  </div>
                </form>

                {knowledgeQueryError ? (
                  <div className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
                    {knowledgeQueryError}
                  </div>
                ) : null}

                {isQueryingKnowledge ? (
                  <div className="flex min-h-[180px] items-center justify-center rounded-[24px] border border-slate-200 bg-slate-50/70 px-6 py-10 text-sm text-slate-500">
                    <LoaderCircle className="mr-2 h-4 w-4 animate-spin" />
                    Consultando y sintetizando respuesta con respaldo documental...
                  </div>
                ) : submittedKnowledgeQuery && knowledgeAnswer ? (
                  <div className="space-y-4">
                    <div className={cn(
                      "rounded-[24px] border p-5 shadow-sm",
                      knowledgeInsufficientEvidence
                        ? "border-amber-200 bg-amber-50/80 shadow-amber-100/50"
                        : "border-slate-200 bg-white shadow-slate-200/40"
                    )}>
                      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                        <div className="space-y-2">
                          <div className="flex items-center gap-2">
                            {knowledgeInsufficientEvidence ? (
                              <AlertTriangle className="h-4 w-4 text-amber-600" />
                            ) : (
                              <CheckCircle2 className="h-4 w-4 text-emerald-600" />
                            )}
                            <p className={cn(
                              "text-sm font-semibold",
                              knowledgeInsufficientEvidence ? "text-amber-800" : "text-slate-900"
                            )}>
                              {knowledgeInsufficientEvidence ? "Respuesta sin respaldo suficiente" : "Respuesta generada con respaldo documental"}
                            </p>
                          </div>
                          <p className={cn(
                            "text-base leading-7",
                            knowledgeInsufficientEvidence ? "text-amber-900" : "text-slate-800"
                          )}>
                            {knowledgeAnswer}
                          </p>
                        </div>
                        <div className="flex shrink-0 flex-wrap items-center gap-2 text-xs">
                          <span className="rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-slate-600">
                            Recuperados {knowledgeRetrievedCount}
                          </span>
                          <span className="rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-slate-600">
                            Citados {knowledgeSnippetsUsed}
                          </span>
                          <span className={cn(
                            "rounded-full border px-2.5 py-1",
                            knowledgeGrounded
                              ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                              : "border-amber-200 bg-amber-50 text-amber-700"
                          )}>
                            {knowledgeGrounded ? "Grounded" : "Sin evidencia"}
                          </span>
                        </div>
                      </div>
                    </div>
                    {knowledgeCitations.length > 0 ? (
                      <div className="space-y-4">
                        <div className="flex items-center justify-between">
                          <p className="text-sm font-medium text-slate-900">Fuentes utilizadas</p>
                          <p className="text-xs text-slate-400">Citas estructuradas del motor RAG</p>
                        </div>
                        <div className="grid gap-4">
                          {knowledgeCitations.map((citation) => (
                            <article
                              key={`${citation.document_id}-${citation.chunk_index}-${citation.source_id}`}
                              className="rounded-[24px] border border-slate-200 bg-white p-5 shadow-sm shadow-slate-200/40"
                            >
                              {(() => {
                                const citationKey = `${citation.document_id}-${citation.chunk_index}-${citation.source_id}`;
                                const isExpanded = Boolean(expandedCitationIds[citationKey]);
                                return (
                                  <>
                              <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                                <div className="flex min-w-0 items-start gap-3">
                                  <div className="mt-0.5 rounded-xl border border-slate-200 bg-slate-50 p-2">
                                    {getDocumentTypeIcon({
                                      file_name: citation.document_file_name,
                                      source_kind: citation.source_kind,
                                    } as KnowledgeDocument)}
                                  </div>
                                  <div className="min-w-0 space-y-1">
                                    <p className="truncate text-sm font-semibold text-slate-900">
                                      {citation.document_title || citation.document_file_name}
                                    </p>
                                    <p className="text-xs text-slate-500">
                                      {citation.source_id} · {citation.document_file_name} · Fragmento {citation.chunk_index + 1}
                                    </p>
                                  </div>
                                </div>
                                {typeof citation.similarity === "number" ? (
                                  <span className="inline-flex shrink-0 rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-xs font-medium text-slate-600">
                                    Similitud {(citation.similarity * 100).toFixed(1)}%
                                  </span>
                                ) : null}
                              </div>
                              <div className="mt-4 rounded-2xl border border-slate-100 bg-slate-50/80 p-4 transition-all duration-200">
                                <p
                                  className={cn(
                                    "whitespace-pre-wrap text-sm leading-6 text-slate-700 transition-all duration-200",
                                    isExpanded ? "line-clamp-none" : "line-clamp-3"
                                  )}
                                >
                                  {citation.snippet}
                                </p>
                                <button
                                  type="button"
                                  onClick={() => toggleCitationExpansion(citationKey)}
                                  className="mt-3 text-xs font-medium text-slate-500 transition-colors hover:text-slate-700"
                                >
                                  {isExpanded ? "Ver menos" : "Ver más"}
                                </button>
                              </div>
                                  </>
                                );
                              })()}
                            </article>
                          ))}
                        </div>
                      </div>
                    ) : null}
                  </div>
                ) : submittedKnowledgeQuery && !knowledgeQueryError ? (
                  <div className="flex min-h-[180px] flex-col items-center justify-center gap-3 rounded-[24px] border border-slate-200 bg-slate-50/70 px-6 py-10 text-center">
                    <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
                      <Search className="h-5 w-5 text-slate-500" />
                    </div>
                    <div className="space-y-1">
                      <p className="text-sm font-medium text-slate-900">No hubo respuesta disponible.</p>
                      <p className="text-sm text-slate-500">
                        Intenta reformular la pregunta o ampliar la base documental indexada.
                      </p>
                    </div>
                  </div>
                ) : (
                  <div className="flex min-h-[180px] flex-col items-center justify-center gap-3 rounded-[24px] border border-slate-200 bg-slate-50/70 px-6 py-10 text-center">
                    <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
                      <DatabaseZap className="h-5 w-5 text-slate-500" />
                    </div>
                    <div className="space-y-1">
                      <p className="text-sm font-medium text-slate-900">La consulta semántica está lista.</p>
                      <p className="text-sm text-slate-500">
                        Escribe una pregunta para obtener una respuesta natural respaldada por documentos.
                      </p>
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>

            <Card className="gap-0 overflow-hidden border-slate-200/80 bg-white shadow-sm shadow-slate-200/50">
              <CardHeader className="border-b border-slate-100 bg-white">
                <CardTitle className="text-lg text-slate-900">Documentos indexables</CardTitle>
                <CardDescription>
                  Seguimiento del pipeline documental: carga, indexación vectorial y disponibilidad para consultas semánticas.
                </CardDescription>
              </CardHeader>
              <CardContent className="px-0">
                {isLoading ? (
                  <div className="flex items-center justify-center px-6 py-20 text-sm text-slate-500">
                    <LoaderCircle className="mr-2 h-4 w-4 animate-spin" />
                    Cargando base de conocimiento...
                  </div>
                ) : documents.length === 0 ? (
                  <div className="flex flex-col items-center justify-center gap-3 px-6 py-20 text-center">
                    <div className="rounded-2xl border border-slate-200 bg-slate-50 p-4">
                      <BookOpen className="h-6 w-6 text-slate-500" />
                    </div>
                    <div className="space-y-1">
                      <p className="text-sm font-medium text-slate-900">Aún no hay documentos cargados.</p>
                      <p className="text-sm text-slate-500">
                        Sube tu primer PDF, TXT o MD para comenzar a construir contexto institucional.
                      </p>
                    </div>
                  </div>
                ) : (
                  <Table>
                    <TableHeader>
                      <TableRow className="border-slate-100 hover:bg-white">
                        <TableHead className="px-6 py-4 text-xs uppercase tracking-[0.18em] text-slate-400">Nombre del archivo</TableHead>
                        <TableHead className="px-4 py-4 text-xs uppercase tracking-[0.18em] text-slate-400">Tipo</TableHead>
                        <TableHead className="px-4 py-4 text-xs uppercase tracking-[0.18em] text-slate-400">Estado</TableHead>
                        <TableHead className="px-4 py-4 text-xs uppercase tracking-[0.18em] text-slate-400">Fecha de carga</TableHead>
                        <TableHead className="px-4 py-4 text-right text-xs uppercase tracking-[0.18em] text-slate-400">Acciones</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {documents.map((document) => (
                        <TableRow key={document.id} className="border-slate-100 hover:bg-slate-50/70">
                          <TableCell className="px-6 py-4 align-top">
                            <div className="flex items-start gap-3">
                              <div className="mt-0.5 rounded-xl border border-slate-200 bg-slate-50 p-2">
                                {getDocumentTypeIcon(document)}
                              </div>
                              <div className="min-w-0 space-y-1">
                                <p className="truncate text-sm font-medium text-slate-900">{document.file_name}</p>
                                <p className="truncate text-xs text-slate-500">{document.title}</p>
                                <div className="flex flex-wrap items-center gap-3 text-xs text-slate-400">
                                  <span>{formatFileSize(document.file_size_bytes)}</span>
                                  <span>{document.chunk_count} chunks</span>
                                  <span>{document.word_count} palabras</span>
                                </div>
                                {document.last_error ? (
                                  <p className="max-w-xl truncate text-xs text-rose-500">{document.last_error}</p>
                                ) : null}
                              </div>
                            </div>
                          </TableCell>
                          <TableCell className="px-4 py-4 align-top">
                            <span className="inline-flex rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-xs font-medium text-slate-600">
                              {getDocumentTypeLabel(document)}
                            </span>
                          </TableCell>
                          <TableCell className="px-4 py-4 align-top">
                            <KnowledgeStatusBadge status={document.status} />
                          </TableCell>
                          <TableCell className="px-4 py-4 align-top text-sm text-slate-500">
                            {formatKnowledgeDate(document.created_at)}
                          </TableCell>
                          <TableCell className="px-4 py-4 align-top">
                            <div className="flex justify-end">
                              {isRetryableKnowledgeStatus(document.status) ? (
                                <Button
                                  type="button"
                                  variant="outline"
                                  size="sm"
                                  disabled={retryingDocumentId === document.id}
                                  onClick={() => void handleRetryDocument(document)}
                                  className="border-slate-200 text-slate-700 hover:bg-slate-50"
                                >
                                  {retryingDocumentId === document.id ? (
                                    <>
                                      <LoaderCircle className="h-4 w-4 animate-spin" />
                                      Reintentando...
                                    </>
                                  ) : (
                                    <>
                                      <RefreshCw className="h-4 w-4" />
                                      Reintentar
                                    </>
                                  )}
                                </Button>
                              ) : (
                                <span className="text-xs text-slate-300">-</span>
                              )}
                            </div>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                )}
              </CardContent>
            </Card>
          </div>
        </div>
      </main>

      {isUploadSheetOpen ? (
        <>
          <div
            className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm transition-all duration-200"
            onClick={handleCloseUploadSheet}
          />
          <aside className="fixed right-0 top-0 z-50 flex h-full w-full flex-col border-l border-border bg-background shadow-2xl sm:max-w-[640px]">
            <div className="shrink-0 border-b border-border/50 bg-background/95 p-5 backdrop-blur">
              <div className="flex items-start justify-between gap-4">
                <div className="space-y-1">
                  <h3 className="text-xl font-semibold text-slate-900">Subir Documento</h3>
                  <p className="text-sm text-slate-500">
                    Carga archivos PDF, TXT o MD para indexarlos en la base vectorial institucional.
                  </p>
                </div>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  onClick={handleCloseUploadSheet}
                  disabled={isUploading}
                  className="text-slate-500 hover:bg-slate-100 hover:text-slate-700"
                >
                  <X className="h-4 w-4" />
                </Button>
              </div>
            </div>

            <div className="flex min-h-0 flex-1 flex-col gap-5 overflow-y-auto p-5">
              <div className="space-y-2">
                <label htmlFor="knowledge-document-title" className="text-sm font-medium text-slate-700">
                  Título documental
                </label>
                <Input
                  id="knowledge-document-title"
                  value={uploadTitle}
                  onChange={(event) => setUploadTitle(event.target.value)}
                  placeholder="Ej. Plan de contingencia Q4"
                  disabled={isUploading}
                  className="border-slate-200"
                />
              </div>

              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                onDragEnter={(event) => {
                  event.preventDefault();
                  event.stopPropagation();
                  setIsDraggingOver(true);
                }}
                onDragOver={(event) => {
                  event.preventDefault();
                  event.stopPropagation();
                  setIsDraggingOver(true);
                }}
                onDragLeave={(event) => {
                  event.preventDefault();
                  event.stopPropagation();
                  setIsDraggingOver(false);
                }}
                onDrop={handleDrop}
                disabled={isUploading}
                className={cn(
                  "flex min-h-[280px] w-full flex-col items-center justify-center rounded-[28px] border border-dashed px-8 py-10 text-center transition-colors",
                  isDraggingOver
                    ? "border-slate-900 bg-slate-100"
                    : "border-slate-300 bg-slate-50 hover:border-slate-400 hover:bg-slate-100/70",
                  isUploading && "cursor-not-allowed opacity-70"
                )}
              >
                <div className="mb-4 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
                  <Upload className="h-6 w-6 text-slate-700" />
                </div>
                <div className="space-y-2">
                  <p className="text-base font-medium text-slate-900">
                    Arrastra tu documento aquí o selecciona un archivo
                  </p>
                  <p className="text-sm text-slate-500">
                    Formatos soportados: PDF, TXT y MD.
                  </p>
                </div>
                <span className="mt-5 inline-flex rounded-full border border-slate-200 bg-white px-3 py-1 text-xs font-medium text-slate-600 shadow-sm">
                  Abrir selector
                </span>
              </button>

              <input
                ref={fileInputRef}
                type="file"
                accept=".pdf,.txt,.md,text/plain,text/markdown,application/pdf"
                className="hidden"
                onChange={(event) => handleSelectFile(event.target.files?.[0] || null)}
              />

              <div className="rounded-2xl border border-slate-200 bg-white p-4">
                <div className="flex items-start justify-between gap-4">
                  <div className="space-y-1">
                    <p className="text-sm font-medium text-slate-900">Documento seleccionado</p>
                    {selectedFile ? (
                      <>
                        <p className="text-sm text-slate-600">{selectedFile.name}</p>
                        <p className="text-xs text-slate-400">{formatFileSize(selectedFile.size)}</p>
                      </>
                    ) : (
                      <p className="text-sm text-slate-500">Ningún archivo seleccionado todavía.</p>
                    )}
                  </div>
                  {selectedFile ? (
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      onClick={() => {
                        setSelectedFile(null);
                        setUploadError(null);
                        if (fileInputRef.current) {
                          fileInputRef.current.value = "";
                        }
                      }}
                      disabled={isUploading}
                      className="text-slate-500 hover:bg-slate-100 hover:text-slate-700"
                    >
                      <X className="h-4 w-4" />
                    </Button>
                  ) : null}
                </div>
              </div>

              <p className="text-xs italic text-slate-400">
                El documento se registrará como <span className="font-medium">queued</span> y su indexación vectorial continuará en segundo plano.
              </p>

              {uploadError ? (
                <div className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
                  {uploadError}
                </div>
              ) : null}
            </div>

            <div className="shrink-0 border-t border-border/50 bg-background/95 p-5 backdrop-blur">
              <div className="flex items-center justify-end gap-3">
                <Button
                  type="button"
                  variant="outline"
                  onClick={handleCloseUploadSheet}
                  disabled={isUploading}
                  className={KNOWLEDGE_ACTION_BUTTON_CLASSNAME}
                >
                  Cancelar
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => void handleUploadDocument()}
                  disabled={!selectedFile || isUploading}
                  className={KNOWLEDGE_ACTION_BUTTON_CLASSNAME}
                >
                  {isUploading ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
                  {isUploading ? "Subiendo..." : "Subir Documento"}
                </Button>
              </div>
            </div>
          </aside>
        </>
      ) : null}
    </div>
  );
}
