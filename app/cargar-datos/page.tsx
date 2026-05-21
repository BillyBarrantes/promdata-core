'use client'

import React, { Suspense, useState, useEffect, useRef } from "react";
import { Sidebar } from "@/components/sidebar";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useRouter, useSearchParams } from "next/navigation";
import { useSupabase } from '@/lib/supabase-provider';
import { toast } from "sonner"; // Usaremos toast para notificaciones
import { AlertTriangle, RefreshCw, Search, ShieldAlert, ShieldCheck, Table2, Trash2, Unplug, X } from "lucide-react";
import { API_BASE_URL } from "@/lib/api-config";

// El tipo de archivo que esperamos de la base de datos
interface UploadedFile {
  id: string;
  file_name: string;
  storage_path: string; // Necesario para eliminar del bucket
  created_at?: string | null;
}

interface CloudConnectorProvider {
  id: string;
  name: string;
  category: string;
  status: string;
  configured: boolean;
  oauth_ready: boolean;
  auth_flow: string;
  auth_start_path: string;
  auth_callback_path: string;
  watchdog_mode: string;
  watchdog_enabled: boolean;
  capabilities: {
    can_import: boolean;
    can_watch: boolean;
    supports_webhook: boolean;
    supports_polling: boolean;
  };
  notes: string;
  connected: boolean;
  connection_id?: string | null;
  connection_status?: string | null;
  connected_account_email?: string | null;
  connected_account_name?: string | null;
  watch_target_count: number;
  last_refreshed_at?: string | null;
}

interface CloudRemoteFileItem {
  id: string;
  name: string;
  provider: string;
  item_type: string;
  extension?: string | null;
  mime_type?: string | null;
  size_bytes?: number | null;
  modified_at?: string | null;
  web_url?: string | null;
  download_url?: string | null;
  supports_analysis?: boolean;
  ingest_source_type?: string | null;
}

interface CloudRemoteFileListResponse {
  provider: string;
  connected_account_email?: string | null;
  files: CloudRemoteFileItem[];
  next_cursor?: string | null;
}

interface CloudRemoteImportResponse {
  provider: string;
  uploaded_file_id: string;
  file_name: string;
  storage_path: string;
  source_type: string;
}

interface CloudWatchTarget {
  id: string;
  provider: string;
  target_type: string;
  target_id: string;
  target_name?: string | null;
  linked_file_id?: string | null;
  is_active: boolean;
  watchdog_mode: string;
  contract_status?: string | null;
  pending_change: boolean;
  pending_change_summary?: string | null;
  sync_state?: string | null;
  last_known_modified_at?: string | null;
  last_known_size_bytes?: number | null;
  last_polled_at?: string | null;
  last_change_detected_at?: string | null;
  auto_sync_status?: string | null;
  last_auto_sync_at?: string | null;
  last_auto_sync_error?: string | null;
  last_auto_sync_job_id?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

interface CloudWatchTargetListResponse {
  provider: string;
  targets: CloudWatchTarget[];
}

interface CloudWatchdogPollResponse {
  checked_count: number;
  new_change_count: number;
  skipped_contract_count: number;
  error_count: number;
  auto_sync_enqueued_count: number;
  auto_sync_skipped_count: number;
  auto_sync_dispatch_failed_count: number;
  changes: Array<{
    watch_target_id: string;
    provider: string;
    target_id: string;
    target_name?: string | null;
    linked_file_id?: string | null;
    change_summary?: string | null;
    changed_at?: string | null;
    requires_reimport: boolean;
  }>;
}

interface WatchdogProviderRuntimeState {
  provider_id: string;
  provider_name?: string | null;
  connected: boolean;
  runtime_mode: string;
  watch_target_count: number;
  pending_target_count: number;
  synced_target_count: number;
  stale_target_count: number;
  fallback_target_count: number;
  error_target_count: number;
  contract_state: string;
  operational_state: string;
  contract_statuses?: string[];
  sync_summary?: string;
  recommended_action?: string;
  last_activity_at?: string | null;
  last_polled_at?: string | null;
  last_change_detected_at?: string | null;
  next_check_due_at?: string | null;
}

interface WatchdogRuntimeStatus {
  enabled: boolean;
  poll_interval_seconds: number;
  configured_provider_count: number;
  watchdog_provider_count: number;
  configured_providers: string[];
  watchdog_providers: string[];
  connected_provider_count: number;
  active_target_count: number;
  pending_target_count: number;
  synced_target_count: number;
  fallback_provider_count: number;
  operational_state: string;
  summary: string;
  last_activity_at?: string | null;
  provider_states: WatchdogProviderRuntimeState[];
}

interface FilePreviewColumn {
  name: string;
  inferred_type: string;
}

interface FilePreviewResponse {
  file_id: string;
  file_name: string;
  selected_sheet?: string | null;
  row_count: number;
  column_count: number;
  preview_limit: number;
  file_size_bytes: number;
  created_at?: string | null;
  columns: FilePreviewColumn[];
  rows: Array<Record<string, unknown>>;
  quality_profile?: {
    health_score: number;
    health_status: string;
    null_cell_count: number;
    null_cell_ratio: number;
    duplicate_row_count: number;
    duplicate_row_ratio: number;
    ambiguous_column_count: number;
    invalid_date_column_count: number;
    outlier_column_count: number;
    alert_count: number;
    alerts: Array<{
      code: string;
      severity: string;
      title: string;
      message: string;
      affected_columns: string[];
    }>;
    column_issues: Array<{
      name: string;
      inferred_type: string;
      non_null_count: number;
      null_count: number;
      null_ratio: number;
      distinct_count: number;
      invalid_count: number;
      outlier_count: number;
      issue_flags: string[];
    }>;
  } | null;
}

function ExcelMiniIcon() {
  return (
    <svg viewBox="0 0 20 20" className="h-5 w-5" aria-hidden="true">
      <rect x="3" y="2.5" width="12.5" height="15" rx="3" fill="#16A34A" />
      <path d="M7 6.2h4.8v1.1H8.3V9h3.2v1.05H8.3v2h3.7v1.1H7V6.2Z" fill="white" />
      <rect x="1.5" y="5" width="6" height="10" rx="1.8" fill="#15803D" />
      <path d="m3.3 8 1.15 1.8L5.6 8h1.15l-1.7 2.48 1.78 2.52H5.67L4.45 11.1 3.2 13H2.05l1.8-2.58L2.17 8H3.3Z" fill="white" />
    </svg>
  );
}

function CsvMiniIcon() {
  return (
    <svg viewBox="0 0 20 20" className="h-5 w-5" aria-hidden="true">
      <path d="M5 2.5h6.4L15.5 6v10.4A1.6 1.6 0 0 1 13.9 18H5A1.5 1.5 0 0 1 3.5 16.5V4A1.5 1.5 0 0 1 5 2.5Z" fill="#F59E0B" />
      <path d="M11.4 2.5V5a1 1 0 0 0 1 1h3.1" fill="#FCD34D" />
      <path d="M6.1 8.1h7.3v1H6.1v-1Zm0 2h7.3v1H6.1v-1Zm0 2h5.2v1H6.1v-1Z" fill="white" />
      <rect x="5.8" y="13.5" width="8.1" height="2.2" rx="1.1" fill="#B45309" />
      <text x="9.85" y="15.1" textAnchor="middle" fontSize="3.1" fill="white" fontWeight="700">CSV</text>
    </svg>
  );
}

function GoogleSheetMiniIcon() {
  return (
    <svg viewBox="0 0 20 20" className="h-5 w-5" aria-hidden="true">
      <path d="M5 2.5h6.3L15.5 6v10.4A1.6 1.6 0 0 1 13.9 18H5A1.5 1.5 0 0 1 3.5 16.5V4A1.5 1.5 0 0 1 5 2.5Z" fill="#2563EB" />
      <path d="M11.3 2.5V5a1 1 0 0 0 1 1h3.2" fill="#8AB4F8" />
      <rect x="5.8" y="8" width="7.9" height="5.8" rx="1" fill="white" />
      <path d="M8.43 8v5.8M11.07 8v5.8M5.8 9.93h7.9M5.8 11.87h7.9" stroke="#2563EB" strokeWidth="0.75" />
    </svg>
  );
}

function ProviderIdentityIcon({ providerId }: { providerId: string }) {
  if (providerId === 'google_drive') {
    return (
      <div className="flex h-9 w-9 items-center justify-center rounded-xl border border-slate-200 bg-white shadow-sm" aria-hidden="true">
        <svg viewBox="0 0 20 20" className="h-5 w-5">
          <path d="M3.7 13.6 7.8 6.5h3.1l-4.1 7.1H3.7Z" fill="#34A853" />
          <path d="M12 13.6H6.8l-1.6 2.7H13c.6 0 1.1-.3 1.4-.8l.2-.3-2.6-1.6Z" fill="#188038" />
          <path d="m8 6.5 1.6-2.8c.3-.5.8-.8 1.4-.8h.4l2.5 4.3-1.6 2.8H8Z" fill="#4285F4" />
          <path d="M16.2 13.5 14 9.8l1.6-2.8 2.2 3.8c.3.5.3 1.1 0 1.6l-.2.4-1.4-.3Z" fill="#FBBC04" />
          <path d="M14 9.8H7.8L6.2 7h9.4c.6 0 1.1.3 1.4.8l.2.4L14 9.8Z" fill="#EA4335" />
        </svg>
      </div>
    );
  }

  return (
    <div className="flex h-9 w-9 items-center justify-center rounded-xl border border-slate-200 bg-white shadow-sm" aria-hidden="true">
      <svg viewBox="0 0 20 20" className="h-5 w-5">
        <path d="M7.1 14.6h7.7a3.05 3.05 0 0 0 .4-6.1A4.8 4.8 0 0 0 6 7.5a3.35 3.35 0 0 0 1.1 7.1Z" fill="#2563EB" />
        <path d="M7.1 14.6h7.7a3.05 3.05 0 0 0 .4-6.1A4.15 4.15 0 0 0 7.5 10c0 1.7 1.3 3.1 2.9 3.1h4.4" fill="#60A5FA" opacity=".9" />
      </svg>
    </div>
  );
}

function CargarDatosPageContent() {
  const WATCHDOG_CLIENT_POLL_INTERVAL_SECONDS = 30;
  const FAST_GOOGLE_WATCHDOG_POLL_INTERVAL_SECONDS = 10;
  const FAST_GOOGLE_WATCHDOG_ACTIVITY_WINDOW_MS = 3 * 60 * 1000;
  const WATCHDOG_POLL_REQUEST_TIMEOUT_MS = 12000;
  const WATCHDOG_POLL_ERROR_BACKOFF_MS = 60000;
  const WATCHDOG_AUTO_SYNC_SETTLE_MAX_ATTEMPTS = 8;
  const WATCHDOG_AUTO_SYNC_SETTLE_INTERVAL_MS = 1500;
  const [uploadedFiles, setUploadedFiles] = useState<UploadedFile[]>([]);
  const [cloudProviders, setCloudProviders] = useState<CloudConnectorProvider[]>([]);
  const [connectingProviderId, setConnectingProviderId] = useState<string | null>(null);
  const [isExplorerOpen, setIsExplorerOpen] = useState(false);
  const [activeExplorerProvider, setActiveExplorerProvider] = useState<CloudConnectorProvider | null>(null);
  const [explorerItems, setExplorerItems] = useState<CloudRemoteFileItem[]>([]);
  const [explorerCache, setExplorerCache] = useState<Record<string, CloudRemoteFileItem[]>>({});
  const [explorerLoading, setExplorerLoading] = useState(false);
  const [explorerError, setExplorerError] = useState<string | null>(null);
  const [importingRemoteFileId, setImportingRemoteFileId] = useState<string | null>(null);
  const [watchTargetMutationItemId, setWatchTargetMutationItemId] = useState<string | null>(null);
  const [disconnectingProviderId, setDisconnectingProviderId] = useState<string | null>(null);
  const [explorerSearch, setExplorerSearch] = useState("");
  const [watchTargetsByProvider, setWatchTargetsByProvider] = useState<Record<string, CloudWatchTarget[]>>({});
  const [watchdogPollIntervalSeconds, setWatchdogPollIntervalSeconds] = useState(WATCHDOG_CLIENT_POLL_INTERVAL_SECONDS);
  const [watchdogRuntimeStatus, setWatchdogRuntimeStatus] = useState<WatchdogRuntimeStatus | null>(null);
  const [manualProviderPollingId, setManualProviderPollingId] = useState<string | null>(null);
  const [autoSyncMonitoringProviderIds, setAutoSyncMonitoringProviderIds] = useState<Record<string, boolean>>({});
  const [isPreviewOpen, setIsPreviewOpen] = useState(false);
  const [previewSourceFile, setPreviewSourceFile] = useState<UploadedFile | null>(null);
  const [previewData, setPreviewData] = useState<FilePreviewResponse | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [windowFocused, setWindowFocused] = useState(true);
  const [pageVisible, setPageVisible] = useState(true);
  const [fastGooglePollingUntil, setFastGooglePollingUntil] = useState<number>(() => Date.now());
  const router = useRouter();
  const searchParams = useSearchParams();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const isMountedRef = useRef(true);
  const watchdogPollingRef = useRef(false);
  const watchdogLastPollAtRef = useRef(0);
  const watchdogBackoffUntilRef = useRef(0);
  const queuedManualProviderPollIdRef = useRef<string | null>(null);
  const autoSyncMonitorProvidersRef = useRef<Set<string>>(new Set());
  const supabase = useSupabase(); // Creamos una instancia del cliente

  const markCloudActivity = () => {
    setFastGooglePollingUntil(Date.now() + FAST_GOOGLE_WATCHDOG_ACTIVITY_WINDOW_MS);
  };

  const hasGoogleWatchTargets = cloudProviders.some(
    (provider) => provider.id === 'google_drive' && provider.connected && provider.watch_target_count > 0
  );
  const hasRecentCloudActivity = fastGooglePollingUntil > Date.now();
  const shouldUseFastGooglePolling =
    hasGoogleWatchTargets &&
    pageVisible &&
    windowFocused &&
    (isExplorerOpen || isPreviewOpen || hasRecentCloudActivity);
  const effectiveWatchdogPollIntervalSeconds = shouldUseFastGooglePolling
    ? Math.min(watchdogPollIntervalSeconds, FAST_GOOGLE_WATCHDOG_POLL_INTERVAL_SECONDS)
    : watchdogPollIntervalSeconds;

  // Función para obtener los archivos desde la base de datos de Supabase
  const fetchFiles = async () => {
    setIsLoading(true);
    const { data: { user } } = await supabase.auth.getUser();
    if (!user) {
      toast.error("Debes iniciar sesión para ver tus archivos.");
      setIsLoading(false);
      return;
    }

    const { data, error } = await supabase
      .from('uploaded_files')
      .select('id, file_name, storage_path, created_at')
      .eq('user_id', user.id)
      .order('created_at', { ascending: false });

    if (error) {
      toast.error("Error al cargar archivos: " + error.message);
      setUploadedFiles([]);
    } else {
      setUploadedFiles(data || []);
    }
    setIsLoading(false);
  };

  const fetchCloudConnectorStatus = async () => {
    const { data: { session } } = await supabase.auth.getSession();
    if (!session?.access_token) {
      setCloudProviders([]);
      return;
    }

    try {
      const providersResponse = await fetch(`${API_BASE_URL}/api/v1/connectors/providers`, {
        headers: { 'Authorization': `Bearer ${session.access_token}` }
      });

      const providersData = providersResponse.ok ? await providersResponse.json() : [];
      setCloudProviders(Array.isArray(providersData) ? providersData : []);
    } catch (error) {
      console.error("Error cargando estado cloud connectors:", error);
      setCloudProviders([]);
    }
  };

  const fetchWatchdogRuntimeStatus = async () => {
    const { data: { session } } = await supabase.auth.getSession();
    if (!session?.access_token) {
      setWatchdogRuntimeStatus(null);
      return;
    }

    try {
      const response = await fetch(`${API_BASE_URL}/api/v1/connectors/watchdog/status`, {
        headers: { 'Authorization': `Bearer ${session.access_token}` }
      });
      if (!response.ok) return;
      const payload = await response.json();
      setWatchdogRuntimeStatus(payload);
      const intervalSeconds = Number(payload?.poll_interval_seconds);
      if (Number.isFinite(intervalSeconds) && intervalSeconds > 0) {
        setWatchdogPollIntervalSeconds(
          Math.max(15, Math.min(intervalSeconds, WATCHDOG_CLIENT_POLL_INTERVAL_SECONDS))
        );
      }
    } catch (error) {
      console.error("Error cargando watchdog runtime status:", error);
    }
  };

  useEffect(() => {
    fetchFiles();
    fetchCloudConnectorStatus();
    fetchWatchdogRuntimeStatus();
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    const oauthStatus = searchParams.get('oauth_status');
    const oauthProvider = searchParams.get('oauth_provider');
    const oauthMessage = searchParams.get('oauth_message');

    if (!oauthStatus || !oauthProvider) return;

    if (oauthStatus === 'connected') {
      toast.success(oauthMessage || `Conector ${oauthProvider} conectado correctamente.`);
      fetchCloudConnectorStatus();
      fetchWatchdogRuntimeStatus();
    } else if (oauthStatus === 'error') {
      toast.error(oauthMessage || `No se pudo completar la conexión ${oauthProvider}.`);
    }

    router.replace('/cargar-datos');
  }, [router, searchParams]);

  // Nueva función para subir archivos directamente a Supabase
  const handleFileUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;

    setIsLoading(true);
    toast.info(`Subiendo ${file.name}...`);

    const { data: { user } } = await supabase.auth.getUser();
    if (!user) {
      toast.error("No estás autenticado.");
      setIsLoading(false);
      return;
    }

    // --- INICIO DE LA CORRECCIÓN ---
    // 1. Obtenemos el team_id del usuario desde la tabla team_members.
    const { data: teamData, error: teamError } = await supabase
      .from('team_members')
      .select('team_id')
      .eq('user_id', user.id)
      .single(); // .single() espera solo un resultado, que es lo correcto.

    if (teamError || !teamData) {
      toast.error("Error: No se pudo encontrar el equipo del usuario.");
      setIsLoading(false);
      return;
    }
    const { team_id } = teamData;
    // --- FIN DE LA CORRECCIÓN ---

    // 2. Subir el archivo a Supabase Storage
    const filePath = `${user.id}/${Date.now()}_${file.name}`;
    const { error: uploadError } = await supabase.storage
      .from('dash-uploads') // Asegúrate que este sea el nombre de tu bucket
      .upload(filePath, file);

    if (uploadError) {
      toast.error(`Error al subir: ${uploadError.message}`);
      setIsLoading(false);
      return;
    }

    // 3. Insertar el registro en la base de datos (AHORA CON team_id)
    const { error: dbError } = await supabase
      .from('uploaded_files')
      .insert({
        user_id: user.id,
        team_id: team_id, // <-- ¡Aquí está la magia!
        file_name: file.name,
        storage_path: filePath,
      });

    if (dbError) {
      toast.error(`Error al registrar en DB: ${dbError.message}`);
    } else {
      toast.success("¡Archivo subido con éxito!");
      await fetchFiles(); // Refrescar la lista de archivos
    }

    setIsLoading(false);
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  };

  // Función para eliminar archivo
  const handleDeleteFile = async (fileId: string, storagePath: string) => {
    // Confirmación visual simple (opcional, se puede mejorar con un Dialog)
    if (!confirm("¿Estás seguro de eliminar este archivo? Se borrarán también los chats asociados.")) return;

    setIsLoading(true);

    try {
      // 1. Eliminar del Storage (si existe path)
      if (storagePath) {
        const { error: storageError } = await supabase.storage
          .from('dash-uploads')
          .remove([storagePath]);

        if (storageError) {
          console.error("Error eliminando del bucket:", storageError);
          // No detenemos el flujo, intentamos borrar de DB igual
        }
      }

      // 2. Eliminar de la Base de Datos
      const { error: dbError } = await supabase
        .from('uploaded_files')
        .delete()
        .eq('id', fileId);

      if (dbError) {
        throw new Error(dbError.message);
      }

      toast.success("Archivo eliminado correctamente");
      await fetchFiles(); // Recargar lista

    } catch (error: any) {
      toast.error("Error al eliminar: " + error.message);
    } finally {
      setIsLoading(false);
    }
  };

  const handleIniciarChat = (fileId: string) => {
    router.push(`/?fileId=${encodeURIComponent(fileId)}`);
  };

  const handleOpenPreview = async (file: UploadedFile) => {
    markCloudActivity();
    setPreviewSourceFile(file);
    setPreviewData(null);
    setPreviewError(null);
    setPreviewLoading(true);
    setIsPreviewOpen(true);
    setIsExplorerOpen(false);

    try {
      const { data: { session } } = await supabase.auth.getSession();
      if (!session?.access_token) {
        throw new Error("Debes iniciar sesión para ver la vista previa.");
      }

      const response = await fetch(`${API_BASE_URL}/api/v1/files/${file.id}/preview`, {
        headers: { 'Authorization': `Bearer ${session.access_token}` }
      });
      const payload: FilePreviewResponse | { detail?: string } = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error((payload as { detail?: string })?.detail || "No se pudo cargar la vista previa.");
      }

      setPreviewData(payload as FilePreviewResponse);
    } catch (error: any) {
      setPreviewError(error.message || "Error cargando la vista previa.");
    } finally {
      setPreviewLoading(false);
    }
  };

  const handleAddNewClick = () => {
    fileInputRef.current?.click();
  };

  const getExplorerCacheKey = (providerId: string, searchTerm: string) =>
    `${providerId}:${searchTerm.trim().toLowerCase() || 'recent'}`;

  const loadExplorerFiles = async (
    provider: CloudConnectorProvider,
    searchTerm: string
  ) => {
    const cacheKey = getExplorerCacheKey(provider.id, searchTerm);
    if (explorerCache[cacheKey]) {
      setExplorerItems(explorerCache[cacheKey]);
      setExplorerError(null);
      return;
    }

    setExplorerLoading(true);
    setExplorerError(null);
    try {
      const { data: { session } } = await supabase.auth.getSession();
      if (!session?.access_token) {
        throw new Error("Debes iniciar sesión para explorar archivos cloud.");
      }

      const query = new URLSearchParams({ limit: '50' });
      if (searchTerm.trim()) query.set('search', searchTerm.trim());
      const response = await fetch(`${API_BASE_URL}/api/v1/connectors/${provider.id}/files?${query.toString()}`, {
        headers: { 'Authorization': `Bearer ${session.access_token}` }
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => null);
        throw new Error(payload?.detail || "No se pudieron cargar los archivos remotos.");
      }
      const payload: CloudRemoteFileListResponse = await response.json();
      const files = Array.isArray(payload.files) ? payload.files : [];
      setExplorerItems(files);
      setExplorerCache((current) => ({ ...current, [cacheKey]: files }));
    } catch (error: any) {
      setExplorerItems([]);
      setExplorerError(error.message || "Error cargando archivos remotos.");
    } finally {
      setExplorerLoading(false);
    }
  };

  const loadWatchTargets = async (provider: CloudConnectorProvider): Promise<CloudWatchTarget[] | null> => {
    try {
      const { data: { session } } = await supabase.auth.getSession();
      if (!session?.access_token) return null;

      const response = await fetch(`${API_BASE_URL}/api/v1/connectors/${provider.id}/watch-targets`, {
        headers: { 'Authorization': `Bearer ${session.access_token}` }
      });
      if (!response.ok) {
        throw new Error("No se pudo cargar el estado de vigilancia.");
      }
      const payload: CloudWatchTargetListResponse = await response.json();
      const targets = Array.isArray(payload.targets) ? payload.targets : [];
      setWatchTargetsByProvider((current) => ({
        ...current,
        [provider.id]: targets,
      }));
      return targets;
    } catch (error) {
      console.error("Error cargando watch targets:", error);
      setWatchTargetsByProvider((current) => ({
        ...current,
        [provider.id]: [],
      }));
      return null;
    }
  };

  const getProviderById = (providerId: string) =>
    cloudProviders.find((provider) => provider.id === providerId)
    || (activeExplorerProvider?.id === providerId ? activeExplorerProvider : null);

  const getAutoSyncLifecycleState = (targets: CloudWatchTarget[]) => {
    const activeTargets = targets.filter((target) => target.is_active);
    const hasRunning = activeTargets.some((target) => {
      const status = String(target.auto_sync_status || '').trim().toLowerCase();
      return status === 'queued' || status === 'running';
    });
    const failedTarget = activeTargets.find((target) => {
      const status = String(target.auto_sync_status || '').trim().toLowerCase();
      return status === 'manual_attention';
    });
    const hasPending = activeTargets.some((target) => Boolean(target.pending_change));
    return {
      hasRunning,
      hasPending,
      failedTarget: failedTarget || null,
    };
  };

  const monitorProviderAutoSync = async (providerId: string) => {
    const provider = getProviderById(providerId);
    if (!provider || autoSyncMonitorProvidersRef.current.has(providerId)) return;

    autoSyncMonitorProvidersRef.current.add(providerId);
    if (isMountedRef.current) {
      setAutoSyncMonitoringProviderIds((current) => ({ ...current, [providerId]: true }));
    }

    const toastId = `watchdog-auto-sync-${providerId}`;

    try {
      for (let attempt = 0; attempt < WATCHDOG_AUTO_SYNC_SETTLE_MAX_ATTEMPTS; attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, attempt === 0 ? 900 : WATCHDOG_AUTO_SYNC_SETTLE_INTERVAL_MS));
        if (!isMountedRef.current) return;

        const targets = await loadWatchTargets(provider);
        await fetchCloudConnectorStatus();
        await fetchWatchdogRuntimeStatus();

        if (!targets) {
          continue;
        }

        const lifecycle = getAutoSyncLifecycleState(targets);
        if (lifecycle.failedTarget) {
          toast.error(
            lifecycle.failedTarget.last_auto_sync_error
              || lifecycle.failedTarget.pending_change_summary
              || 'La sincronización automática falló. Usa Reimportar para resolverlo.',
            { id: toastId }
          );
          return;
        }

        if (!lifecycle.hasPending && !lifecycle.hasRunning) {
          await fetchFiles();
          toast.success(`Sincronización completada: ${provider.name}`, { id: toastId });
          return;
        }
      }

      toast.info('La sincronización sigue procesándose en segundo plano.', { id: toastId });
    } finally {
      autoSyncMonitorProvidersRef.current.delete(providerId);
      if (isMountedRef.current) {
        setAutoSyncMonitoringProviderIds((current) => {
          const next = { ...current };
          delete next[providerId];
          return next;
        });
      }
    }
  };

  const pollWatchdogTargets = async (providerId?: string) => {
    const now = Date.now();
    if (watchdogPollingRef.current) {
      if (providerId && isMountedRef.current) {
        queuedManualProviderPollIdRef.current = providerId;
        setManualProviderPollingId(providerId);
      }
      return;
    }
    if (!providerId) {
      const hasActiveTargets = cloudProviders.some(
        (provider) => provider.connected && provider.watch_target_count > 0
      );
      if (!hasActiveTargets) return;
    }
    if (!providerId && now < watchdogBackoffUntilRef.current) return;
    if (providerId) {
      watchdogBackoffUntilRef.current = 0;
    }
    if (!providerId) {
      const minGapMs = Math.max(8000, effectiveWatchdogPollIntervalSeconds * 1000);
      if (now - watchdogLastPollAtRef.current < minGapMs) return;
    }

    const { data: { session } } = await supabase.auth.getSession();
    if (!session?.access_token) return;

    watchdogPollingRef.current = true;
    if (providerId) {
      setManualProviderPollingId(providerId);
    }
    watchdogLastPollAtRef.current = now;
    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), WATCHDOG_POLL_REQUEST_TIMEOUT_MS);
    try {
      const response = await fetch(`${API_BASE_URL}/api/v1/connectors/watchdog/poll`, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${session.access_token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ provider: providerId || null }),
        signal: controller.signal,
      });
      if (!response.ok) {
        if (response.status >= 500) {
          watchdogBackoffUntilRef.current = Date.now() + WATCHDOG_POLL_ERROR_BACKOFF_MS;
        }
        return;
      }
      watchdogBackoffUntilRef.current = 0;
      const payload: CloudWatchdogPollResponse = await response.json();
      const provider = providerId ? getProviderById(providerId) : null;

      if (payload.new_change_count > 0) {
        payload.changes.forEach((item) => {
          toast.info(`Cambio detectado en: ${item.target_name || 'Archivo remoto'}`, {
            id: `watchdog-change-${item.watch_target_id}-${item.changed_at || Date.now()}`,
          });
        });
      }

      if (isMountedRef.current) {
        await fetchCloudConnectorStatus();
        await fetchWatchdogRuntimeStatus();
      }

      let refreshedTargets: CloudWatchTarget[] | null = null;
      if (isMountedRef.current && provider) {
        refreshedTargets = await loadWatchTargets(provider);
      } else if (isMountedRef.current && activeExplorerProvider && (!providerId || activeExplorerProvider.id === providerId)) {
        refreshedTargets = await loadWatchTargets(activeExplorerProvider);
      }

      const lifecycle = Array.isArray(refreshedTargets)
        ? getAutoSyncLifecycleState(refreshedTargets)
        : { hasRunning: false, hasPending: false, failedTarget: null };

      if (providerId) {
        if (payload.auto_sync_dispatch_failed_count > 0) {
          toast.error('No se pudo despachar la sincronización automática. Usa Reimportar si el estado persiste.');
        } else if (lifecycle.failedTarget) {
          toast.error(
            lifecycle.failedTarget.last_auto_sync_error
              || lifecycle.failedTarget.pending_change_summary
              || 'La sincronización automática requiere atención manual.'
          );
        } else if (payload.auto_sync_enqueued_count > 0 || lifecycle.hasRunning) {
          toast.info('Sincronización automática iniciada. Actualizando en segundo plano.');
        } else if (!lifecycle.hasPending && payload.new_change_count === 0) {
          toast.success("Estado de vigilancia verificado. No se detectaron cambios nuevos.");
        }
      }

      if (
        providerId
        && provider
        && (payload.auto_sync_enqueued_count > 0 || lifecycle.hasRunning || lifecycle.hasPending)
      ) {
        void monitorProviderAutoSync(providerId);
      }
    } catch (error: any) {
      watchdogBackoffUntilRef.current = Date.now() + WATCHDOG_POLL_ERROR_BACKOFF_MS;
      if (error?.name !== 'AbortError') {
        console.error("Error ejecutando polling base del watchdog:", error);
      }
    } finally {
      window.clearTimeout(timeoutId);
      watchdogPollingRef.current = false;
      if (providerId && isMountedRef.current) {
        setManualProviderPollingId(null);
      }
      const queuedProviderId = queuedManualProviderPollIdRef.current;
      if (queuedProviderId) {
        queuedManualProviderPollIdRef.current = null;
        void pollWatchdogTargets(queuedProviderId);
      }
    }
  };

  const handleOpenExplorer = async (provider: CloudConnectorProvider) => {
    markCloudActivity();
    setActiveExplorerProvider(provider);
    setIsExplorerOpen(true);
    setExplorerSearch("");
    await Promise.all([
      loadExplorerFiles(provider, ""),
      loadWatchTargets(provider),
      pollWatchdogTargets(provider.id),
    ]);
  };

  const formatFileSize = (sizeBytes?: number | null) => {
    if (!sizeBytes || sizeBytes <= 0) return "Sin tamaño";
    if (sizeBytes < 1024) return `${sizeBytes} B`;
    if (sizeBytes < 1024 * 1024) return `${(sizeBytes / 1024).toFixed(1)} KB`;
    return `${(sizeBytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  const formatRuntimeTimestamp = (value?: string | null) => {
    if (!value) return "Sin actividad reciente";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return "Sin actividad reciente";
    return parsed.toLocaleString();
  };

  const formatRuntimeTimeShort = (value?: string | null) => {
    if (!value) return "—";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return "—";
    return parsed.toLocaleTimeString([], {
      hour: 'numeric',
      minute: '2-digit',
      second: '2-digit',
    });
  };

  const watchdogTone = (() => {
    const state = watchdogRuntimeStatus?.operational_state;
    if (state === 'healthy') {
      return {
        border: 'border-emerald-200',
        bg: 'bg-emerald-50/70',
        text: 'text-emerald-800',
        chip: 'bg-emerald-100 text-emerald-800 border-emerald-200',
        icon: <ShieldCheck className="h-4 w-4" />,
      };
    }
    if (state === 'attention') {
      return {
        border: 'border-amber-200',
        bg: 'bg-amber-50/80',
        text: 'text-amber-800',
        chip: 'bg-amber-100 text-amber-800 border-amber-200',
        icon: <AlertTriangle className="h-4 w-4" />,
      };
    }
    if (state === 'degraded') {
      return {
        border: 'border-red-200',
        bg: 'bg-red-50/80',
        text: 'text-red-800',
        chip: 'bg-red-100 text-red-800 border-red-200',
        icon: <ShieldAlert className="h-4 w-4" />,
      };
    }
    return {
      border: 'border-slate-200',
      bg: 'bg-slate-50/70',
      text: 'text-slate-700',
      chip: 'bg-white text-slate-700 border-slate-200',
      icon: <ShieldCheck className="h-4 w-4" />,
    };
  })();

  const getRuntimeStateForProvider = (providerId: string) =>
    watchdogRuntimeStatus?.provider_states.find((state) => state.provider_id === providerId) || null;

  const getProviderStateTone = (state?: string | null) => {
    if (state === 'healthy') {
      return {
        wrapper: 'border-emerald-200 bg-emerald-50 text-emerald-700',
        chip: 'border-emerald-200 bg-emerald-50 text-emerald-700',
      };
    }
    if (state === 'degraded') {
      return {
        wrapper: 'border-red-200 bg-red-50 text-red-700',
        chip: 'border-red-200 bg-red-50 text-red-700',
      };
    }
    if (state === 'attention') {
      return {
        wrapper: 'border-amber-200 bg-amber-50 text-amber-700',
        chip: 'border-amber-200 bg-amber-50 text-amber-700',
      };
    }
    return {
      wrapper: 'border-slate-200 bg-slate-50 text-slate-700',
      chip: 'border-slate-200 bg-slate-50 text-slate-700',
    };
  };

  const getHealthTone = (status?: string | null) => {
    if (status === 'healthy') {
      return {
        wrapper: 'border-emerald-200 bg-emerald-50 text-emerald-700',
        icon: <ShieldCheck className="h-4 w-4" />,
        label: 'Salud alta',
      };
    }
    if (status === 'warning') {
      return {
        wrapper: 'border-amber-200 bg-amber-50 text-amber-700',
        icon: <AlertTriangle className="h-4 w-4" />,
        label: 'Salud media',
      };
    }
    return {
      wrapper: 'border-red-200 bg-red-50 text-red-700',
      icon: <ShieldAlert className="h-4 w-4" />,
      label: 'Salud crítica',
    };
  };

  const formatPercent = (value?: number | null) => `${((value || 0) * 100).toFixed(1)}%`;

  const qualityFlagLabels: Record<string, string> = {
    high_nulls: 'Nulos',
    generic_name: 'Nombre genérico',
    ambiguous_content: 'Contenido mixto',
    duplicated_header: 'Cabecera duplicada',
    invalid_dates: 'Fechas inválidas',
    extreme_outliers: 'Outliers',
  };

  const getRemoteFileIcon = (item: CloudRemoteFileItem) => {
    if (item.ingest_source_type === 'google_sheet') return <GoogleSheetMiniIcon />;
    if (item.extension === 'csv') return <CsvMiniIcon />;
    return <ExcelMiniIcon />;
  };

  const handleConnectProvider = async (providerId: string) => {
    setConnectingProviderId(providerId);
    try {
      const { data: { session } } = await supabase.auth.getSession();
      if (!session?.access_token) {
        toast.error("Debes iniciar sesión para conectar una fuente cloud.");
        return;
      }

      const redirectTo = `${window.location.origin}/cargar-datos`;
      const provider = cloudProviders.find((item) => item.id === providerId);
      if (!provider?.auth_start_path) {
        throw new Error("El proveedor no expone una ruta OAuth válida.");
      }
      const response = await fetch(
        `${API_BASE_URL}${provider.auth_start_path}?redirect_to=${encodeURIComponent(redirectTo)}`,
        {
          headers: { 'Authorization': `Bearer ${session.access_token}` }
        }
      );

      const payload = await response.json();
      if (!response.ok || !payload?.auth_url) {
        throw new Error(payload?.detail || "No se pudo iniciar el flujo OAuth.");
      }

      window.location.assign(payload.auth_url);
    } catch (error: any) {
      toast.error(error.message || "Error iniciando conexión OAuth.");
    } finally {
      setConnectingProviderId(null);
    }
  };

  const handleDisconnectProvider = async (provider: CloudConnectorProvider) => {
    setDisconnectingProviderId(provider.id);
    try {
      const { data: { session } } = await supabase.auth.getSession();
      if (!session?.access_token) {
        throw new Error("Debes iniciar sesión para desconectar una cuenta cloud.");
      }
      const response = await fetch(`${API_BASE_URL}/api/v1/connectors/${provider.id}/connection`, {
        method: 'DELETE',
        headers: { 'Authorization': `Bearer ${session.access_token}` },
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload?.detail || "No se pudo desconectar la cuenta.");
      }
      toast.success(`Cuenta desconectada: ${provider.name}`);
      if (activeExplorerProvider?.id === provider.id) {
        setIsExplorerOpen(false);
        setActiveExplorerProvider(null);
        setExplorerItems([]);
        setExplorerSearch("");
        setExplorerError(null);
      }
      setWatchTargetsByProvider((current) => ({ ...current, [provider.id]: [] }));
      await fetchCloudConnectorStatus();
      await fetchWatchdogRuntimeStatus();
    } catch (error: any) {
      toast.error(error.message || "Error desconectando cuenta cloud.");
    } finally {
      setDisconnectingProviderId(null);
    }
  };

  const handleImportRemoteFile = async (item: CloudRemoteFileItem) => {
    if (!activeExplorerProvider) return;
    markCloudActivity();
    setImportingRemoteFileId(item.id);
    try {
      const { data: { session } } = await supabase.auth.getSession();
      if (!session?.access_token) {
        throw new Error("Debes iniciar sesión para importar archivos cloud.");
      }

      const response = await fetch(`${API_BASE_URL}/api/v1/connectors/${activeExplorerProvider.id}/import`, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${session.access_token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ item_id: item.id }),
      });
      const payload: CloudRemoteImportResponse | { detail?: string } = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error((payload as { detail?: string })?.detail || "No se pudo importar el archivo remoto.");
      }
      toast.success(`Archivo importado: ${(payload as CloudRemoteImportResponse).file_name}`);
      await fetchFiles();
      await fetchCloudConnectorStatus();
      await fetchWatchdogRuntimeStatus();
      setIsExplorerOpen(false);
    } catch (error: any) {
      toast.error(error.message || "Error importando archivo remoto.");
    } finally {
      setImportingRemoteFileId(null);
    }
  };

  const getWatchTargetForItem = (providerId: string, itemId: string) =>
    (watchTargetsByProvider[providerId] || []).find((target) => target.target_id === itemId && target.is_active);

  const activeExplorerWatchTargets = activeExplorerProvider
    ? (watchTargetsByProvider[activeExplorerProvider.id] || []).filter((target) => target.is_active)
    : [];

  const activeExplorerPendingCount = activeExplorerWatchTargets.filter((target) => target.pending_change).length;

  const handleToggleWatchTarget = async (item: CloudRemoteFileItem) => {
    if (!activeExplorerProvider) return;

    markCloudActivity();
    const existingTarget = getWatchTargetForItem(activeExplorerProvider.id, item.id);
    setWatchTargetMutationItemId(item.id);
    try {
      const { data: { session } } = await supabase.auth.getSession();
      if (!session?.access_token) {
        throw new Error("Debes iniciar sesión para vigilar archivos cloud.");
      }

      if (existingTarget) {
        const response = await fetch(
          `${API_BASE_URL}/api/v1/connectors/${activeExplorerProvider.id}/watch-targets/${existingTarget.id}`,
          {
            method: 'DELETE',
            headers: { 'Authorization': `Bearer ${session.access_token}` },
          }
        );
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(payload?.detail || "No se pudo desactivar la vigilancia.");
        }
        toast.success(`Vigilancia desactivada: ${item.name}`);
      } else {
        const response = await fetch(`${API_BASE_URL}/api/v1/connectors/${activeExplorerProvider.id}/watch-targets`, {
          method: 'POST',
          headers: {
            'Authorization': `Bearer ${session.access_token}`,
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ item_id: item.id }),
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(payload?.detail || "No se pudo activar la vigilancia.");
        }
        toast.success(`Archivo vigilado: ${item.name}`);
      }

      await loadWatchTargets(activeExplorerProvider);
      await fetchCloudConnectorStatus();
      await fetchWatchdogRuntimeStatus();
      void pollWatchdogTargets(activeExplorerProvider.id);
    } catch (error: any) {
      toast.error(error.message || "Error actualizando vigilancia cloud.");
    } finally {
      setWatchTargetMutationItemId(null);
    }
  };

  useEffect(() => {
    if (!isExplorerOpen || !activeExplorerProvider) return;
    const timeout = window.setTimeout(() => {
      loadExplorerFiles(activeExplorerProvider, explorerSearch);
    }, 250);
    return () => window.clearTimeout(timeout);
  }, [activeExplorerProvider, explorerSearch, isExplorerOpen]);

  useEffect(() => {
    if (!isExplorerOpen && !isPreviewOpen) return;

    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setIsExplorerOpen(false);
        setIsPreviewOpen(false);
      }
    };

    window.addEventListener('keydown', handleEscape);
    return () => window.removeEventListener('keydown', handleEscape);
  }, [isExplorerOpen, isPreviewOpen]);

  useEffect(() => {
    if (fastGooglePollingUntil <= Date.now()) return;
    const timeoutMs = Math.max(0, fastGooglePollingUntil - Date.now());
    const timeoutId = window.setTimeout(() => {
      setFastGooglePollingUntil(Date.now());
    }, timeoutMs);
    return () => window.clearTimeout(timeoutId);
  }, [fastGooglePollingUntil]);

  useEffect(() => {
    const handleWindowFocus = () => {
      setWindowFocused(true);
      markCloudActivity();
      void pollWatchdogTargets();
    };
    const handleWindowBlur = () => {
      setWindowFocused(false);
    };
    const handleVisibilityChange = () => {
      const visible = document.visibilityState === 'visible';
      setPageVisible(visible);
      if (visible) {
        markCloudActivity();
        void pollWatchdogTargets();
      }
    };

    setWindowFocused(document.hasFocus());
    setPageVisible(document.visibilityState === 'visible');
    window.addEventListener('focus', handleWindowFocus);
    window.addEventListener('blur', handleWindowBlur);
    document.addEventListener('visibilitychange', handleVisibilityChange);

    return () => {
      window.removeEventListener('focus', handleWindowFocus);
      window.removeEventListener('blur', handleWindowBlur);
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, [cloudProviders]);

  useEffect(() => {
    const hasActiveTargets = cloudProviders.some((provider) => provider.connected && provider.watch_target_count > 0);
    if (!hasActiveTargets) return;

    void pollWatchdogTargets();

    const intervalMs = effectiveWatchdogPollIntervalSeconds * 1000;
    const intervalId = window.setInterval(() => {
      void pollWatchdogTargets();
    }, intervalMs);

    return () => {
      window.clearInterval(intervalId);
    };
  }, [cloudProviders, effectiveWatchdogPollIntervalSeconds]);

  return (
    <div className="flex h-screen bg-background">
      <Sidebar />
      <main className="flex-1 flex flex-col">
        <header className="border-b border-border/40 px-6 py-4 bg-background/50 backdrop-blur-md sticky top-0 z-10">
          <h1 className="text-sm font-medium text-muted-foreground uppercase tracking-wider">Espacio de Trabajo</h1>
        </header>

        <div className="flex-1 p-6 overflow-auto">
          <div className="max-w-7xl mx-auto">
            <div className="flex justify-between items-center mb-6">
              <div>
                <h2 className="text-4xl font-normal tracking-tight text-foreground mb-2">Cargar Datos</h2>
                <p className="text-muted-foreground font-light text-lg">Gestiona tus archivos de datos para el análisis.</p>
              </div>
              <Button variant="outline" size="sm" onClick={handleAddNewClick}>
                Subir nuevo archivo
              </Button>
            </div>

            <div className="mb-8">
              <div className="mb-4">
                <div>
                  <h3 className="text-xl font-medium text-foreground">Fuentes Cloud</h3>
                  <p className="text-sm text-muted-foreground">
                    Conecta una cuenta y explora solo archivos listos para análisis.
                  </p>
                </div>
              </div>

              {watchdogRuntimeStatus && (
                <Card className={`mb-4 rounded-2xl border ${watchdogTone.border} ${watchdogTone.bg} px-4 py-3 shadow-[0_8px_24px_rgba(15,23,42,0.04)]`}>
                  <div className="flex flex-col gap-2 lg:flex-row lg:items-center lg:justify-between">
                    <div className={`flex items-center gap-2 text-sm font-medium ${watchdogTone.text}`}>
                      {watchdogTone.icon}
                      <span>Estado operativo del Watchdog Cloud</span>
                    </div>
                    <div className="flex flex-wrap items-center gap-2 text-xs">
                      <span className={`inline-flex items-center gap-2 rounded-full border px-3 py-1 ${watchdogTone.chip}`}>
                        <span className="h-2.5 w-2.5 rounded-full bg-emerald-500" />
                        {watchdogRuntimeStatus.connected_provider_count} Conectados
                      </span>
                      <span className={`inline-flex items-center gap-2 rounded-full border px-3 py-1 ${watchdogTone.chip}`}>
                        <span className="h-2.5 w-2.5 rounded-full bg-emerald-300" />
                        {watchdogRuntimeStatus.active_target_count} Vigilados
                      </span>
                      <span className={`inline-flex items-center gap-2 rounded-full border px-3 py-1 ${watchdogTone.chip}`}>
                        <span className="h-2.5 w-2.5 rounded-full bg-amber-400" />
                        {watchdogRuntimeStatus.pending_target_count} Pendiente{watchdogRuntimeStatus.pending_target_count === 1 ? '' : 's'}
                      </span>
                      <span className={`inline-flex items-center gap-2 rounded-full border px-3 py-1 ${watchdogTone.chip}`}>
                        <span className="h-2.5 w-2.5 rounded-full bg-yellow-300" />
                        {watchdogRuntimeStatus.fallback_provider_count} Fallback
                      </span>
                    </div>
                  </div>
                </Card>
              )}

              <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                {cloudProviders.map((provider) => {
                  const runtimeState = getRuntimeStateForProvider(provider.id);
                  const providerTone = getProviderStateTone(runtimeState?.operational_state);
                  return (
                  <Card key={provider.id} className="rounded-2xl border border-border/50 bg-card/70 p-3.5 shadow-[0_8px_24px_rgba(15,23,42,0.04)] backdrop-blur-sm">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0 flex items-start gap-3">
                        <ProviderIdentityIcon providerId={provider.id} />
                        <div className="min-w-0">
                          <div className="flex items-center gap-2">
                            <h4 className="truncate text-[15px] font-medium leading-none text-foreground">{provider.name}</h4>
                          </div>
                          <p className="mt-1 truncate text-[12px] leading-none text-muted-foreground">
                            {provider.connected ? (provider.connected_account_email || 'Cuenta conectada') : 'Sin conectar'}
                            {provider.connected && provider.watch_target_count > 0 && (
                              <span className="ml-1.5">
                                | {provider.watch_target_count} {provider.watch_target_count === 1 ? 'archivo vigilado' : 'archivos vigilados'}
                              </span>
                            )}
                          </p>
                        </div>
                      </div>
                      <span className={`shrink-0 rounded-full border px-2 py-0.5 text-[10px] ${
                        provider.connected
                          ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                          : provider.configured
                            ? 'bg-slate-50 text-slate-700 border-slate-200'
                            : 'bg-amber-50 text-amber-700 border-amber-200'
                      }`}>
                        {provider.connected ? 'Conectado' : provider.status === 'configured' ? 'Listo' : 'Pendiente'}
                      </span>
                    </div>

                    {runtimeState && (
                      <div className="mt-3 space-y-2">
                        <div className="flex flex-wrap gap-1.5">
                          <span className={`rounded-full border px-2 py-0.5 text-[10px] ${providerTone.wrapper}`}>
                            {runtimeState.operational_state === 'healthy'
                              ? 'Sincronizado'
                              : runtimeState.operational_state === 'degraded'
                                ? 'Degradado'
                                : runtimeState.operational_state === 'attention'
                                  ? 'Atención'
                                  : 'En espera'}
                          </span>
                          <span className="rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-[10px] text-slate-700">
                            {runtimeState.runtime_mode === 'webhook' ? 'Webhook' : 'Polling'}
                          </span>
                          {runtimeState.pending_target_count > 0 && (
                            <span className={`rounded-full border px-2 py-0.5 text-[10px] ${providerTone.chip}`}>
                              {runtimeState.pending_target_count} pend.
                            </span>
                          )}
                          {runtimeState.fallback_target_count > 0 && (
                            <span className="rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-[10px] text-amber-700">
                              Fallback
                            </span>
                          )}
                          {runtimeState.stale_target_count > 0 && (
                            <span className="rounded-full border border-red-200 bg-red-50 px-2 py-0.5 text-[10px] text-red-700">
                              Desact. x{runtimeState.stale_target_count}
                            </span>
                          )}
                        </div>
                        {runtimeState.sync_summary && (
                          <p className="text-[11px] leading-snug text-muted-foreground">
                            {runtimeState.sync_summary}
                          </p>
                        )}
                        <div className="flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-muted-foreground">
                          <span>Act.: {formatRuntimeTimeShort(runtimeState.last_activity_at)}</span>
                          <span>Re-sync: {formatRuntimeTimeShort(runtimeState.next_check_due_at)}</span>
                        </div>
                      </div>
                    )}

                    <div className="mt-3 flex flex-wrap gap-1.5">
                      {provider.connected ? (
                        <>
                          <Button
                            variant="default"
                            size="sm"
                            className="h-8 min-w-[9.5rem] flex-1 rounded-xl px-3 text-[11px] shadow-sm"
                            onClick={() => handleOpenExplorer(provider)}
                          >
                            Explorar archivos
                          </Button>
                          <Button
                            variant="outline"
                            size="sm"
                            className="h-8 rounded-xl px-3 text-[11px]"
                            disabled={manualProviderPollingId === provider.id || Boolean(autoSyncMonitoringProviderIds[provider.id])}
                            onClick={() => void pollWatchdogTargets(provider.id)}
                            title={runtimeState?.recommended_action || 'Verificar estado del proveedor ahora'}
                          >
                            <RefreshCw className={`h-3.5 w-3.5 mr-1.5 ${(manualProviderPollingId === provider.id || autoSyncMonitoringProviderIds[provider.id]) ? 'animate-spin' : ''}`} />
                            {manualProviderPollingId === provider.id
                              ? 'Verificando...'
                              : autoSyncMonitoringProviderIds[provider.id]
                                ? 'Sincronizando...'
                                : 'Verificar ahora'}
                          </Button>
                          <Button
                            variant="outline"
                            size="sm"
                            className="h-8 min-w-[9rem] flex-1 rounded-xl px-3 text-[11px]"
                            disabled={disconnectingProviderId === provider.id}
                            onClick={() => handleDisconnectProvider(provider)}
                          >
                            <Unplug className="h-3.5 w-3.5 mr-1.5" />
                            {disconnectingProviderId === provider.id ? 'Desconectando...' : 'Desconectar'}
                          </Button>
                        </>
                      ) : (
                        <Button
                          variant="outline"
                          size="sm"
                          className="h-8 w-full rounded-xl px-3 text-[11px]"
                          disabled={!provider.oauth_ready || connectingProviderId === provider.id}
                          onClick={() => handleConnectProvider(provider.id)}
                        >
                          {provider.oauth_ready
                            ? (connectingProviderId === provider.id ? 'Redirigiendo...' : 'Conectar')
                            : 'Configurar credenciales'}
                        </Button>
                      )}
                    </div>
                  </Card>
                )})}
              </div>
            </div>

            {isLoading && <p className="text-muted-foreground">Cargando archivos...</p>}

            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
              {!isLoading && uploadedFiles.map((file) => (
                <Card key={file.id} className="aspect-square p-6 flex flex-col justify-between relative group hover:shadow-xl hover:-translate-y-1 transition-all duration-300 border border-border/60 rounded-[2rem] bg-card/50 backdrop-blur-sm">

                  {/* Botón de eliminar (visible en hover) */}
                  <div className="absolute right-2 top-2 flex items-center gap-1 opacity-0 transition-all group-hover:opacity-100">
                    <Button
                      variant="outline"
                      size="sm"
                      className="h-7 rounded-xl border-border/70 px-2.5 text-[11px] shadow-sm"
                      title="Vista previa del archivo"
                      onClick={(e) => {
                        e.stopPropagation();
                        void handleOpenPreview(file);
                      }}
                    >
                      <Table2 className="mr-1.5 h-3.5 w-3.5" />
                      Vista previa
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7 text-muted-foreground transition-all hover:bg-destructive/10 hover:text-destructive"
                      title="Eliminar archivo"
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDeleteFile(file.id, file.storage_path);
                      }}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>

                  <div className="flex-1 flex flex-col items-center justify-center pt-2">
                    <div className="w-16 h-16 flex items-center justify-center mb-3">
                      <img src={file.file_name.endsWith('.csv') ? "/CSV.svg" : "/Excel.svg"} alt="Icono" className="h-14 w-14 object-contain" />
                    </div>
                    <h3 className="font-medium text-foreground text-sm text-center px-1 line-clamp-2" title={file.file_name}>
                      {file.file_name}
                    </h3>
                  </div>

                  <div className="w-full mt-2 opacity-0 group-hover:opacity-100 transition-all duration-300 translate-y-2 group-hover:translate-y-0">
                    <Button size="sm" className="w-full rounded-xl bg-primary/90 hover:bg-primary" onClick={() => handleIniciarChat(file.id)}>
                      Iniciar Chat
                    </Button>
                  </div>
                </Card>
              ))}
            </div>

            {!isLoading && uploadedFiles.length === 0 && (
              <p className="text-center text-muted-foreground mt-8">No se encontraron archivos cargados.</p>
            )}

            <input
              ref={fileInputRef}
              type="file"
              accept=".xlsx,.xls,.csv"
              onChange={handleFileUpload}
              className="hidden"
            />

            {isExplorerOpen && (
              <div className="fixed inset-0 z-50">
                <button
                  type="button"
                  aria-label="Cerrar explorador cloud"
                  className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm transition-all duration-200"
                  onClick={() => setIsExplorerOpen(false)}
                />

                <aside className="fixed top-0 right-0 z-50 flex h-full w-full flex-col bg-background shadow-2xl border-l border-border sm:max-w-[500px]">
                  <div className="flex shrink-0 items-start justify-between border-b border-border/50 bg-background/95 p-5 backdrop-blur">
                    <div className="min-w-0 pr-4">
                      <h3 className="truncate text-lg font-medium text-foreground">
                        {activeExplorerProvider ? `Explorar archivos de ${activeExplorerProvider.name}` : 'Explorar archivos'}
                      </h3>
                      <p className="mt-1 text-xs text-muted-foreground">
                        Archivos analizables listos para importar.
                      </p>
                    </div>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-8 w-8 shrink-0 rounded-xl text-muted-foreground"
                      onClick={() => setIsExplorerOpen(false)}
                    >
                      <X className="h-4 w-4" />
                    </Button>
                  </div>

                  <div className="shrink-0 border-b border-border/50 bg-background/95 p-5 backdrop-blur">
                    <div className="relative">
                      <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                      <Input
                        value={explorerSearch}
                        onChange={(event) => setExplorerSearch(event.target.value)}
                        placeholder="Buscar por nombre (.xlsx, .csv, Google Sheets)"
                        className="h-10 rounded-2xl border-border/60 bg-muted/30 pl-9 text-sm"
                      />
                    </div>
                    {activeExplorerProvider && (
                      <div className="mt-3 flex flex-wrap gap-2 text-[11px] text-slate-600">
                        <span className="rounded-full border border-slate-200 bg-white px-2.5 py-1">
                          {activeExplorerWatchTargets.length} vigilado{activeExplorerWatchTargets.length === 1 ? '' : 's'}
                        </span>
                        <span className={`rounded-full border px-2.5 py-1 ${
                          activeExplorerPendingCount > 0
                            ? 'border-amber-200 bg-amber-50 text-amber-700'
                            : 'border-emerald-200 bg-emerald-50 text-emerald-700'
                        }`}>
                          {activeExplorerPendingCount} pendiente{activeExplorerPendingCount === 1 ? '' : 's'} de re-sync
                        </span>
                      </div>
                    )}
                  </div>

                  <div className="flex-1 min-h-0 space-y-1 overflow-y-auto p-5 overscroll-contain">
                    {explorerError && (
                      <div className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                        {explorerError}
                      </div>
                    )}

                    {explorerLoading && (
                      <div className="py-6 text-sm text-muted-foreground">
                        {explorerSearch.trim() ? 'Buscando archivos...' : 'Cargando archivos recientes...'}
                      </div>
                    )}

                    {!explorerLoading && explorerItems.length === 0 && !explorerError && (
                      <div className="rounded-2xl border border-border/60 bg-card/40 px-4 py-6 text-sm text-muted-foreground">
                        {explorerSearch.trim()
                          ? 'No se encontraron archivos analizables con ese nombre.'
                          : 'No se encontraron archivos analizables recientes en esta cuenta.'}
                      </div>
                    )}

                    {!explorerLoading && explorerItems.length > 0 && (
                      <div className="overflow-hidden rounded-2xl border border-slate-200/80 bg-white shadow-[0_10px_30px_rgba(15,23,42,0.04)]">
                        {explorerItems.map((item, index) => (
                          <div
                            key={item.id}
                            className={`group flex cursor-default items-start justify-between border-slate-100 px-6 py-3 transition-colors hover:bg-slate-50/80 ${
                              index !== explorerItems.length - 1 ? 'border-b' : ''
                            }`}
                          >
                            <div className="flex min-w-0 flex-1 items-start gap-4 overflow-hidden">
                              <div className="shrink-0">
                                {getRemoteFileIcon(item)}
                              </div>
                              <div className="min-w-0 flex-1">
                                <div className="truncate text-sm font-medium text-slate-800">{item.name}</div>
                                <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px] leading-tight text-slate-400 tabular-nums">
                                  <span>{formatFileSize(item.size_bytes)}</span>
                                  <span>{item.modified_at ? new Date(item.modified_at).toLocaleDateString() : 'Sin fecha'}</span>
                                  {activeExplorerProvider && (() => {
                                    const watchTarget = getWatchTargetForItem(activeExplorerProvider.id, item.id);
                                    if (!watchTarget) return null;
                                    const isPending = Boolean(watchTarget.pending_change);
                                    return (
                                      <span className={`rounded-full border px-2 py-0.5 font-medium ${
                                        isPending
                                          ? 'border-amber-200 bg-amber-50 text-amber-700'
                                          : 'border-emerald-200 bg-emerald-50 text-emerald-700'
                                      }`}>
                                        {isPending ? 'Pendiente de reimportación' : 'Vigilado y sincronizado'}
                                      </span>
                                    );
                                  })()}
                                </div>
                                {activeExplorerProvider && (() => {
                                  const watchTarget = getWatchTargetForItem(activeExplorerProvider.id, item.id);
                                  if (!watchTarget) return null;
                                  const isPending = Boolean(watchTarget.pending_change);
                                  const helperText = isPending
                                    ? (watchTarget.pending_change_summary || 'Cambio remoto detectado. Requiere reimportación.')
                                    : (watchTarget.linked_file_id
                                        ? 'Archivo vigilado, enlazado y listo para re-sync cuando cambie.'
                                        : 'Archivo vigilado y sincronizado.');

                                  return (
                                    <div className="mt-1.5">
                                      <p className={`text-[11px] leading-snug ${
                                        isPending ? 'text-amber-700' : 'text-slate-500'
                                      }`}>
                                        {helperText}
                                      </p>
                                      <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-slate-400">
                                        {watchTarget.last_change_detected_at && (
                                          <span>Cambio: {formatRuntimeTimeShort(watchTarget.last_change_detected_at)}</span>
                                        )}
                                        {watchTarget.last_polled_at && (
                                          <span>Check: {formatRuntimeTimeShort(watchTarget.last_polled_at)}</span>
                                        )}
                                      </div>
                                    </div>
                                  );
                                })()}
                              </div>
                            </div>

                            <div className="ml-4 flex shrink-0 items-center gap-2 self-center">
                              {activeExplorerProvider && (() => {
                                const watchTarget = getWatchTargetForItem(activeExplorerProvider.id, item.id);
                                const isPending = Boolean(watchTarget?.pending_change);
                                return (
                                  <Button
                                    variant="outline"
                                    size="sm"
                                    className={`h-7 rounded-lg px-3 text-xs font-medium shadow-sm ${
                                      isPending
                                        ? 'border-amber-200 bg-amber-50 text-amber-700 hover:bg-amber-100'
                                        : watchTarget
                                          ? 'border-emerald-200 bg-emerald-50 text-emerald-700 hover:bg-emerald-100'
                                          : 'border border-slate-200 bg-white text-slate-700 hover:bg-slate-50'
                                    }`}
                                    disabled={watchTargetMutationItemId === item.id}
                                    onClick={() => handleToggleWatchTarget(item)}
                                    title={watchTarget?.pending_change_summary || undefined}
                                  >
                                    {watchTargetMutationItemId === item.id
                                      ? 'Guardando...'
                                      : isPending
                                        ? 'Pendiente'
                                        : watchTarget
                                          ? 'Vigilado'
                                          : 'Vigilar'}
                                  </Button>
                                );
                              })()}
                              <Button
                                variant="outline"
                                size="sm"
                                className="h-7 rounded-lg border border-slate-200 bg-white px-3 text-xs font-medium text-slate-700 shadow-sm hover:bg-slate-50"
                                disabled={!item.supports_analysis || importingRemoteFileId === item.id}
                                onClick={() => handleImportRemoteFile(item)}
                              >
                                {importingRemoteFileId === item.id
                                  ? 'Importando...'
                                  : activeExplorerProvider && getWatchTargetForItem(activeExplorerProvider.id, item.id)?.pending_change
                                    ? 'Reimportar'
                                    : 'Importar'}
                              </Button>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </aside>
              </div>
            )}

            {isPreviewOpen && (
              <div className="fixed inset-0 z-50">
                <button
                  type="button"
                  aria-label="Cerrar vista previa del archivo"
                  className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm transition-all duration-200"
                  onClick={() => setIsPreviewOpen(false)}
                />

                <aside className="fixed top-0 right-0 z-50 flex h-full w-full flex-col border-l border-border bg-background shadow-2xl sm:max-w-5xl">
                  <div className="shrink-0 border-b border-border/50 bg-background/95 p-6 backdrop-blur">
                    <div className="flex items-start justify-between gap-4">
                      <div className="min-w-0">
                        <h3 className="truncate text-2xl font-medium tracking-tight text-foreground">
                          {previewData?.file_name || previewSourceFile?.file_name || 'Vista previa del archivo'}
                        </h3>
                        <div className="mt-3 flex flex-wrap gap-x-6 gap-y-2 text-sm text-muted-foreground">
                          <span>
                            Filas: <span className="font-medium text-foreground">{previewData?.row_count ?? '...'}</span>
                          </span>
                          <span>
                            Columnas: <span className="font-medium text-foreground">{previewData?.column_count ?? '...'}</span>
                          </span>
                          <span>
                            Tamaño: <span className="font-medium text-foreground">{previewData ? formatFileSize(previewData.file_size_bytes) : '...'}</span>
                          </span>
                          <span>
                            Fecha: <span className="font-medium text-foreground">
                              {previewData?.created_at
                                ? new Date(previewData.created_at).toLocaleString()
                                : (previewSourceFile?.created_at ? new Date(previewSourceFile.created_at).toLocaleString() : 'Sin fecha')}
                            </span>
                          </span>
                          {previewData?.selected_sheet && (
                            <span>
                              Hoja: <span className="font-medium text-foreground">{previewData.selected_sheet}</span>
                            </span>
                          )}
                        </div>
                        <p className="mt-3 text-xs italic text-slate-400">
                          Mostrando una vista previa de las primeras 100 filas.
                        </p>
                      </div>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-8 w-8 shrink-0 rounded-xl text-muted-foreground"
                        onClick={() => setIsPreviewOpen(false)}
                      >
                        <X className="h-4 w-4" />
                      </Button>
                    </div>
                  </div>

                  <div className="flex-1 min-h-0 overflow-hidden bg-background">
                    {previewLoading && (
                      <div className="p-6 text-sm text-muted-foreground">Cargando vista previa...</div>
                    )}

                    {!previewLoading && previewError && (
                      <div className="p-6">
                        <div className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                          {previewError}
                        </div>
                      </div>
                    )}

                    {!previewLoading && previewData && (
                      <div className="flex h-full min-h-0 flex-col">
                        <div className="flex-1 min-h-0 overflow-auto px-6 pb-6">
                          {previewData.quality_profile && (
                            <div className="mb-6 space-y-4">
                              <div className="grid gap-4 xl:grid-cols-[280px_minmax(0,1fr)]">
                                <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-[0_8px_24px_rgba(15,23,42,0.04)]">
                                  <div className="flex items-start justify-between gap-3">
                                    <div>
                                      <div className="text-xs font-medium uppercase tracking-wide text-slate-400">
                                        Salud del dataset
                                      </div>
                                      <div className="mt-2 text-4xl font-semibold tracking-tight text-slate-900">
                                        {previewData.quality_profile.health_score}
                                      </div>
                                      <div className="mt-1 text-sm text-slate-500">
                                        Score previo al análisis
                                      </div>
                                    </div>
                                    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium ${getHealthTone(previewData.quality_profile.health_status).wrapper}`}>
                                      {getHealthTone(previewData.quality_profile.health_status).icon}
                                      {getHealthTone(previewData.quality_profile.health_status).label}
                                    </span>
                                  </div>
                                </div>

                                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
                                  <div className="rounded-2xl border border-slate-200/80 bg-white p-4 shadow-[0_8px_24px_rgba(15,23,42,0.04)]">
                                    <div className="text-xs uppercase tracking-wide text-slate-400">Celdas vacías</div>
                                    <div className="mt-2 text-2xl font-semibold text-slate-900">
                                      {previewData.quality_profile.null_cell_count.toLocaleString()}
                                    </div>
                                    <div className="mt-1 text-xs text-slate-500">
                                      {formatPercent(previewData.quality_profile.null_cell_ratio)} del dataset
                                    </div>
                                  </div>
                                  <div className="rounded-2xl border border-slate-200/80 bg-white p-4 shadow-[0_8px_24px_rgba(15,23,42,0.04)]">
                                    <div className="text-xs uppercase tracking-wide text-slate-400">Filas duplicadas</div>
                                    <div className="mt-2 text-2xl font-semibold text-slate-900">
                                      {previewData.quality_profile.duplicate_row_count.toLocaleString()}
                                    </div>
                                    <div className="mt-1 text-xs text-slate-500">
                                      {formatPercent(previewData.quality_profile.duplicate_row_ratio)} del total
                                    </div>
                                  </div>
                                  <div className="rounded-2xl border border-slate-200/80 bg-white p-4 shadow-[0_8px_24px_rgba(15,23,42,0.04)]">
                                    <div className="text-xs uppercase tracking-wide text-slate-400">Columnas ambiguas</div>
                                    <div className="mt-2 text-2xl font-semibold text-slate-900">
                                      {previewData.quality_profile.ambiguous_column_count}
                                    </div>
                                    <div className="mt-1 text-xs text-slate-500">
                                      Cabeceras o contenido dudoso
                                    </div>
                                  </div>
                                  <div className="rounded-2xl border border-slate-200/80 bg-white p-4 shadow-[0_8px_24px_rgba(15,23,42,0.04)]">
                                    <div className="text-xs uppercase tracking-wide text-slate-400">Fechas inválidas</div>
                                    <div className="mt-2 text-2xl font-semibold text-slate-900">
                                      {previewData.quality_profile.invalid_date_column_count}
                                    </div>
                                    <div className="mt-1 text-xs text-slate-500">
                                      Columnas temporales inconsistentes
                                    </div>
                                  </div>
                                  <div className="rounded-2xl border border-slate-200/80 bg-white p-4 shadow-[0_8px_24px_rgba(15,23,42,0.04)]">
                                    <div className="text-xs uppercase tracking-wide text-slate-400">Outliers</div>
                                    <div className="mt-2 text-2xl font-semibold text-slate-900">
                                      {previewData.quality_profile.outlier_column_count}
                                    </div>
                                    <div className="mt-1 text-xs text-slate-500">
                                      Columnas con extremos relevantes
                                    </div>
                                  </div>
                                </div>
                              </div>

                              {previewData.quality_profile.alerts.length > 0 && (
                                <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-[0_8px_24px_rgba(15,23,42,0.04)]">
                                  <div className="mb-3 flex items-center justify-between gap-3">
                                    <div>
                                      <h4 className="text-sm font-semibold text-slate-900">Alertas de calidad</h4>
                                      <p className="text-xs text-slate-500">
                                        Señales que pueden degradar el análisis o la recomendación visual.
                                      </p>
                                    </div>
                                    <span className="rounded-full border px-2.5 py-1 text-xs text-slate-600">
                                      {previewData.quality_profile.alert_count} alertas
                                    </span>
                                  </div>
                                  <div className="space-y-3">
                                    {previewData.quality_profile.alerts.map((alert) => {
                                      const toneClass = alert.severity === 'critical'
                                        ? 'border-red-200 bg-red-50'
                                        : 'border-amber-200 bg-amber-50';
                                      const textClass = alert.severity === 'critical'
                                        ? 'text-red-700'
                                        : 'text-amber-700';
                                      return (
                                        <div key={alert.code} className={`rounded-xl border px-4 py-3 ${toneClass}`}>
                                          <div className={`text-sm font-medium ${textClass}`}>{alert.title}</div>
                                          <div className={`mt-1 text-sm ${textClass}`}>{alert.message}</div>
                                          {alert.affected_columns.length > 0 && (
                                            <div className="mt-2 flex flex-wrap gap-2">
                                              {alert.affected_columns.map((columnName) => (
                                                <span key={`${alert.code}-${columnName}`} className={`rounded-full border bg-white/80 px-2 py-1 text-[11px] ${textClass}`}>
                                                  {columnName}
                                                </span>
                                              ))}
                                            </div>
                                          )}
                                        </div>
                                      );
                                    })}
                                  </div>
                                </div>
                              )}

                              {previewData.quality_profile.column_issues.length > 0 && (
                                <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-[0_8px_24px_rgba(15,23,42,0.04)]">
                                  <div className="mb-3">
                                    <h4 className="text-sm font-semibold text-slate-900">Columnas a revisar</h4>
                                    <p className="text-xs text-slate-500">
                                      Las más expuestas a nulos, ambigüedad, fechas inválidas u outliers.
                                    </p>
                                  </div>
                                  <div className="grid gap-3 xl:grid-cols-2">
                                    {previewData.quality_profile.column_issues.map((issue) => (
                                      <div key={issue.name} className="rounded-xl border border-slate-200 bg-slate-50/70 px-4 py-3">
                                        <div className="flex items-start justify-between gap-3">
                                          <div className="min-w-0">
                                            <div className="truncate text-sm font-medium text-slate-900">{issue.name}</div>
                                            <div className="mt-1 text-[11px] uppercase tracking-wide text-slate-400">
                                              {issue.inferred_type}
                                            </div>
                                          </div>
                                          <div className="text-right text-[11px] text-slate-500">
                                            <div>Nulos: {issue.null_count}</div>
                                            <div>Únicos: {issue.distinct_count}</div>
                                          </div>
                                        </div>
                                        <div className="mt-3 flex flex-wrap gap-2">
                                          {issue.issue_flags.map((flag) => (
                                            <span key={`${issue.name}-${flag}`} className="rounded-full border border-slate-200 bg-white px-2 py-1 text-[11px] text-slate-600">
                                              {qualityFlagLabels[flag] || flag}
                                            </span>
                                          ))}
                                        </div>
                                        {(issue.invalid_count > 0 || issue.outlier_count > 0) && (
                                          <div className="mt-3 text-[11px] text-slate-500">
                                            {issue.invalid_count > 0 && <span className="mr-3">Valores inválidos: {issue.invalid_count}</span>}
                                            {issue.outlier_count > 0 && <span>Outliers: {issue.outlier_count}</span>}
                                          </div>
                                        )}
                                      </div>
                                    ))}
                                  </div>
                                </div>
                              )}
                            </div>
                          )}

                          <div className="overflow-x-auto rounded-2xl border border-slate-100 bg-white shadow-[0_8px_24px_rgba(15,23,42,0.04)]">
                            <table className="min-w-full border-collapse">
                              <thead className="sticky top-0 z-10 bg-white">
                                <tr>
                                  {previewData.columns.map((column) => (
                                    <th
                                      key={column.name}
                                      className="border-b border-slate-100 px-4 py-3 text-left align-bottom"
                                    >
                                      <div className="text-sm font-medium text-slate-800">{column.name}</div>
                                      <div className="mt-1 text-[11px] font-normal uppercase tracking-wide text-slate-400">
                                        {column.inferred_type}
                                      </div>
                                    </th>
                                  ))}
                                </tr>
                              </thead>
                              <tbody>
                                {previewData.rows.map((row, rowIndex) => (
                                  <tr key={`${previewData.file_id}-row-${rowIndex}`} className="border-b border-slate-100/80 last:border-b-0">
                                    {previewData.columns.map((column) => (
                                      <td key={`${column.name}-${rowIndex}`} className="px-4 py-3 text-sm text-slate-700">
                                        {row[column.name] === null || row[column.name] === undefined || row[column.name] === ''
                                          ? <span className="text-slate-300">-</span>
                                          : String(row[column.name])}
                                      </td>
                                    ))}
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        </div>
                      </div>
                    )}
                  </div>
                </aside>
              </div>
            )}
          </div>
        </div>
      </main>
    </div>
  );
}

function CargarDatosPageFallback() {
  return (
    <div className="flex h-screen bg-background overflow-hidden">
      <Sidebar />
      <main className="flex-1 flex flex-col min-w-0 overflow-hidden">
        <header className="border-b border-border px-6 py-4 shrink-0">
          <div className="flex items-center justify-between">
            <h1 className="text-lg font-semibold text-foreground"></h1>
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 bg-blue-600 rounded-full flex items-center justify-center text-white text-sm font-medium">
                LB
              </div>
            </div>
          </div>
        </header>
        <div className="flex-1 overflow-y-auto">
          <div className="mx-auto max-w-7xl px-8 py-10 text-sm text-muted-foreground">
            Cargando gestión de archivos...
          </div>
        </div>
      </main>
    </div>
  );
}

export default function CargarDatosPage() {
  return (
    <Suspense fallback={<CargarDatosPageFallback />}>
      <CargarDatosPageContent />
    </Suspense>
  );
}
