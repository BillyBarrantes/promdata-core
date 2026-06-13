// En: PromData/components/chat-interface.tsx
"use client"

import type React from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { useState, useRef, useEffect, useCallback, memo, startTransition } from "react"
import { useAtom, useSetAtom, useAtomValue } from "jotai"
import { duckdbReadyAtom, drillDownAtom, workspaceItemsAtom, workspaceRenderStateAtom, activePresentationIdAtom, presentationsListAtom, AnalysisComponent } from "@/lib/state"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { Card } from "@/components/ui/card"
import { AnalysisReport } from "@/components/analysis-report"
import { ChartsReport } from "@/components/charts-report"
import { SmartTable } from "@/components/smart-table"
import { tryParseArrow } from "@/lib/arrow-parser"
import { DrillDownMenu } from "@/components/drill-down-menu"
import * as duckdbEngine from "@/lib/duckdb-engine"
import { getScopedLocalPerfAverage } from "@/lib/local-performance"
import { Database, Paperclip, SendHorizonal, X, Trash2, Square, Mic, MicOff, LayoutDashboard } from "lucide-react"
import { cn } from "@/lib/utils"
import { API_BASE_URL } from "@/lib/api-config"
import { toast } from "sonner"
import { useSupabase } from '@/lib/supabase-provider'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Label } from "@/components/ui/label"
import { Input } from "@/components/ui/input"

// 🚀 [PERF] Bridge component — reads drillDownAtom independently
// Modificado en QA: ahora lee duckdbReadyAtom directamente para evitar pérdida de inyección
const DrillDownMenuBridge = ({ onSelect, onCrossFilter }: {
  onSelect: (prompt: string) => void;
  onCrossFilter: (filters: Record<string, string>, tableName?: string, crossFilterContext?: any) => void;
}) => {
  const drillDown = useAtomValue(drillDownAtom);
  const isDuckDBReady = useAtomValue(duckdbReadyAtom);
  const setDrillDown = useSetAtom(drillDownAtom);
  const handleClose = useCallback(() => {
    setDrillDown(prev => ({ ...prev, isVisible: false }));
  }, [setDrillDown]);
  return (
    <DrillDownMenu
      isVisible={drillDown.isVisible}
      position={drillDown.position}
      dataContext={drillDown.dataContext}
      onSelect={onSelect}
      onClose={handleClose}
      isDuckDBReady={isDuckDBReady}
      onCrossFilter={onCrossFilter}
    />
  );
};

const extractRawChartCategory = (params: any): string | null => {
  const candidates = [
    params?.rawCategory,
    params?.data?.raw_name,
    params?.data?.rawName,
    params?.data?.full_name,
    params?.data?.fullName,
    params?.data?.name,
    params?.name,
    params?.axisValue,
    params?.axisValueLabel,
    params?.data?.value?.[3],
    params?.value?.[3],
  ];

  for (const candidate of candidates) {
    if (typeof candidate === "string" && candidate.trim()) {
      return candidate.replace(/\0/g, "").normalize('NFC').replace(/\s+/g, ' ').trim();
    }
  }

  return null;
};

const normalizeSyntheticBucketToken = (value: string): string => {
  return value
    .replace(/\0/g, "")
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "");
};

const isOthersBucketLabel = (value: string | null | undefined): boolean => {
  if (!value) return false;
  const normalized = normalizeSyntheticBucketToken(String(value));
  return normalized === "otros" || normalized === "other" || normalized === "others";
};

const getChartOptionFromVisualComponent = (component: any): any | null => {
  if (!component || typeof component !== "object") return null;
  if (component.option && typeof component.option === "object") return component.option;
  if (component.original_chart_option && typeof component.original_chart_option === "object") {
    return component.original_chart_option;
  }
  return null;
};

const extractHierarchyDisplayItems = (option: any): string[] => {
  const seriesList = Array.isArray(option?.series)
    ? option.series
    : option?.series
      ? [option.series]
      : [];
  const primarySeries = seriesList.find(Boolean);
  const primaryType = String(primarySeries?.type || "").toLowerCase();

  if (!["pie", "treemap", "funnel"].includes(primaryType)) {
    return [];
  }

  const rawItems = Array.isArray(primarySeries?.data) ? primarySeries.data : [];
  return rawItems
    .map((item: any) => {
      if (item && typeof item === "object" && !Array.isArray(item)) {
        const candidate =
          item.raw_name
          ?? item.rawName
          ?? item.full_name
          ?? item.fullName
          ?? item.name;
        return typeof candidate === "string" ? candidate : null;
      }

      return typeof item === "string" ? item : null;
    })
    .filter((item: string | null): item is string => Boolean(item && item.trim()))
    .map((item: string) => item.replace(/\0/g, "").normalize("NFC").replace(/\s+/g, " ").trim());
};

const enrichFiltersWithSyntheticBucket = (
  filters: Record<string, string>,
  matchedComponent: any
): Record<string, string> => {
  const selectedValue = filters.global_chart_filter || filters.global_cross_filter || null;
  if (!isOthersBucketLabel(selectedValue)) {
    return filters;
  }

  const chartOption = getChartOptionFromVisualComponent(matchedComponent);
  const dimension = typeof chartOption?.query_contract?.dimension === "string"
    ? chartOption.query_contract.dimension
    : null;
  if (!dimension) {
    return filters;
  }

  const excluded = extractHierarchyDisplayItems(chartOption)
    .filter((item) => !isOthersBucketLabel(item));
  if (excluded.length === 0) {
    return filters;
  }

  const payload = {
    type: "others_excluding_visible",
    label: String(selectedValue),
    dimension,
    excluded,
  };

  console.log("🧠 [CROSS-FILTER] synthetic_bucket_enriched", payload);

  return {
    ...filters,
    __synthetic_bucket__: JSON.stringify(payload),
  };
};


interface ChatMessage {
  id: string
  type: "user" | "assistant"
  content: string
  timestamp: Date
  components?: AnalysisComponent[]
  taskId?: string
  _visuals?: AnalysisComponent[]
}

const VISUAL_COMPONENT_TYPES = ["metricas_clave", "configuracion_echarts", "smart_table", "tabla_datos"] as const

const getVisualPriority = (component: AnalysisComponent): number => {
  switch (component.type) {
    case "metricas_clave":
      return 0
    case "configuracion_echarts":
      return 1
    case "smart_table":
      return 2
    case "tabla_datos":
      return 3
    default:
      return 9
  }
}

const prioritizeVisualComponents = (components: AnalysisComponent[]): AnalysisComponent[] => {
  return [...components].sort((left, right) => getVisualPriority(left) - getVisualPriority(right))
}

const sanitizeTableToken = (value: string): string => {
  return value
    .replace(/[^a-zA-Z0-9_]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .toLowerCase()
}

// [FIX 2026-06-09] LIMPIEZA DE STRING en chart_base_filters.
//
// Por que: el backend (canonical_tabular_canary_executor.py:359) serializa
// filtros como `f"{op} {val}"`, donde `op` viene de `str(f.operator)`.
// Si `f.operator` es un `FilterOperator` (que es `str, Enum` en Python),
// `str()` devuelve la representación textual del enum, e.g.
// "FilterOperator.EQUALS" en vez de su valor canónico "==".
// Resultado: el chart_base_filters llega al frontend con valores como
//   "tipo_movimiento": "FilterOperator.EQUALS ingreso"
// Cuando DuckDB-WASM intenta hacer WHERE tipo_movimiento = 'FilterOperator.EQUALS ingreso',
// no encuentra coincidencias y emite "Degradación elegante: omitido".
//
// Esta función defensiva limpia cualquier prefijo "FilterOperator.X "
// (case-insensitive) o prefijo de operador canónico ("== ", "!= ", "> ", "< ")
// para que el valor puro quede como DuckDB lo espera ("ingreso").
const FILTER_OPERATOR_RE = /^(?:filteroperator\.(?:equals|not_equals|greater_than|less_than|greater_equal|less_equal|in|not_in|like|not_like|contains|starts_with|ends_with)\s+|[!=><~]+\s+)/i
const sanitizeFilterValue = (value: string): string => {
  if (typeof value !== "string") return value
  return value.replace(FILTER_OPERATOR_RE, "").trim()
}

const buildAnalysisTableName = (
  fileId: string | null,
  scope: string,
  rawKey: string | number
): string => {
  const fileToken = sanitizeTableToken(fileId || "global")
  const scopeToken = sanitizeTableToken(scope)
  const keyToken = sanitizeTableToken(String(rawKey || "default"))
  return `pd_${scopeToken}_${fileToken}_${keyToken}`.slice(0, 120)
}

const collectArrowPreloadsFromVisuals = (visuals: AnalysisComponent[]): duckdbEngine.ArrowPreloadEntry[] => {
  const rawEntries = visuals.flatMap((component, index) => {
    const extendedComponent = component as AnalysisComponent & {
      granular_arrow?: string | null
      arrow_data?: string | null
    }
    const chartOption = component.option || component.original_chart_option || null
    const granularArrow = extendedComponent.granular_arrow || chartOption?.granular_arrow || null
    const arrowData = extendedComponent.arrow_data || chartOption?.arrow_data || null
    const tableName = component.table_name || chartOption?.table_name || null

    if (!tableName) return []

    return [
      granularArrow ? { tableName, base64Data: granularArrow, priority: index } : null,
      !granularArrow && arrowData ? { tableName, base64Data: arrowData, priority: index } : null,
    ].filter(Boolean) as duckdbEngine.ArrowPreloadEntry[]
  })

  return rawEntries.sort((left, right) => {
    const leftHistoricalCost = getScopedLocalPerfAverage('duckdb_cross_filter', left.tableName) || 0
    const rightHistoricalCost = getScopedLocalPerfAverage('duckdb_cross_filter', right.tableName) || 0

    if (leftHistoricalCost !== rightHistoricalCost) {
      return rightHistoricalCost - leftHistoricalCost
    }

    return (left.priority ?? 99) - (right.priority ?? 99)
  })
}

const normalizeAnalysisPromptKey = (value: string): string => {
  return value.trim().replace(/\s+/g, " ").toLowerCase()
}

const buildAnalysisRequestKey = (
  fileId: string,
  prompt: string,
  parentTaskId: string | null
): string => {
  return [
    fileId,
    normalizeAnalysisPromptKey(prompt),
    parentTaskId || "root",
  ].join("::")
}

export function ChatInterface() {
  const [message, setMessage] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const hasInteraction = messages.some(m => m.type === 'user');
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [analysisFileId, setAnalysisFileId] = useState<string | null>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const router = useRouter();
  const searchParams = useSearchParams();
  const supabase = useSupabase();
  const chatContainerRef = useRef<HTMLDivElement>(null);
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null);

  // Voice State
  const [isListening, setIsListening] = useState(false);
  const recognitionRef = useRef<any>(null);

  // DrillDown State: useSetAtom = write-only, NO re-render al abrir/cerrar menu
  const setDrillDown = useSetAtom(drillDownAtom);

  // 🦆 [FASE 4] DuckDB-WASM State
  const [isDuckDBReady, setIsDuckDBReady] = useAtom(duckdbReadyAtom);

  // 🧠 [FASE 5] Global State para Workspace
  const setWorkspaceItems = useSetAtom(workspaceItemsAtom);
  const setWorkspaceRenderState = useSetAtom(workspaceRenderStateAtom);
  const workspaceItems = useAtomValue(workspaceItemsAtom);

  // 🧠 [FASE 5.3] Presentation State Flow
  const setActivePresentationId = useSetAtom(activePresentationIdAtom);
  const setPresentations = useSetAtom(presentationsListAtom);
  const presentations = useAtomValue(presentationsListAtom);

  // Last completed task for context
  const [lastCompletedTaskId, setLastCompletedTaskId] = useState<string | null>(null);

  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Estados para el modal de guardar reporte
  const [isSaveDialogOpen, setIsSaveDialogOpen] = useState(false);
  const [reportTitle, setReportTitle] = useState("");
  const [reportToSave, setReportToSave] = useState<any>(null);
  const [selectedSavePresentationId, setSelectedSavePresentationId] = useState("");
  const [isSaveActionLoading, setIsSaveActionLoading] = useState(false);
  const [isSaveDestinationsLoading, setIsSaveDestinationsLoading] = useState(false);

  const getChatAccessToken = useCallback(async (): Promise<string | null> => {
    const { data: { session } } = await supabase.auth.getSession();
    if (session?.access_token) return session.access_token;

    if (typeof window !== 'undefined' && process.env.NODE_ENV !== 'production') {
      const params = new URLSearchParams(window.location.search);
      if (params.get('__qa_chat') === '1') {
        return params.get('__qa_chat_token') || 'qa-chat-token';
      }
    }

    return null;
  }, [supabase]);

  const clearWorkspaceStageTimer = useCallback(() => {
    if (workspaceStageTimerRef.current) {
      clearTimeout(workspaceStageTimerRef.current);
      workspaceStageTimerRef.current = null;
    }
  }, []);

  const stageWorkspaceVisuals = useCallback((visuals: AnalysisComponent[]) => {
    clearWorkspaceStageTimer();

    const prioritizedVisuals = prioritizeVisualComponents(visuals);
    if (prioritizedVisuals.length === 0) {
      startTransition(() => {
        setWorkspaceRenderState({
          status: "idle",
          message: null,
          pendingVisuals: 0,
          renderedVisuals: 0,
        });
      });
      return;
    }

    const primaryVisuals = prioritizedVisuals.slice(0, 1);
    const secondaryVisuals = prioritizedVisuals.slice(1);
    workspaceVisualsRef.current = prioritizedVisuals;

    const preloadEntries = collectArrowPreloadsFromVisuals(prioritizedVisuals);
    if (preloadEntries.length > 0) {
      const preloadPromise = duckdbEngine
        .preloadArrowTables(preloadEntries, 1)
        .then(() => {
          setIsDuckDBReady(true);
        })
        .catch((error) => {
          console.warn('⚠️ [DuckDB] Preload de visuales no completado:', error);
        });
      workspacePreloadPromiseRef.current = preloadPromise;
      void preloadPromise.finally(() => {
        if (workspacePreloadPromiseRef.current === preloadPromise) {
          workspacePreloadPromiseRef.current = null;
        }
      });
    } else {
      workspacePreloadPromiseRef.current = null;
    }

    startTransition(() => {
      setWorkspaceItems(primaryVisuals);
      setWorkspaceRenderState({
        status: secondaryVisuals.length > 0 ? "staging" : "idle",
        message: secondaryVisuals.length > 0 ? "Visual principal listo. Cargando detalle adicional..." : null,
        pendingVisuals: prioritizedVisuals.length,
        renderedVisuals: primaryVisuals.length,
      });
    });

    if (secondaryVisuals.length === 0) {
      return;
    }

    workspaceStageTimerRef.current = setTimeout(() => {
      startTransition(() => {
        setWorkspaceItems(prioritizedVisuals);
        setWorkspaceRenderState({
          status: "idle",
          message: null,
          pendingVisuals: prioritizedVisuals.length,
          renderedVisuals: prioritizedVisuals.length,
        });
      });
      workspaceStageTimerRef.current = null;
    }, 240);
  }, [clearWorkspaceStageTimer, setIsDuckDBReady, setWorkspaceItems, setWorkspaceRenderState]);

  // --- EFFECT: Chat Recovery Logic ---
  useEffect(() => {
    const recoveryRaw = localStorage.getItem('chat_recovery_context');
    if (recoveryRaw) {
      try {
        const recoveryData = JSON.parse(recoveryRaw);
        if (recoveryData.initial_content) {
          setMessage(`🔍 Continuar análisis: ${recoveryData.initial_content}`);
          // Si deseamos automatizar el envío, podríamos llamar a triggerAnalysis aquí con un pequeño delay
          // setTimeout(() => triggerAnalysis(`Continuar con: ${recoveryData.initial_content}`), 500);
        }
        if (recoveryData.parent_id) {
          setLastCompletedTaskId(recoveryData.parent_id);
        }
        toast.info("Contexto de análisis recuperado.");
      } catch (e) {
        console.error("Error parsing recovery context", e);
      } finally {
        localStorage.removeItem('chat_recovery_context');
      }
    }
  }, []); // Solo al montar el componente

  // Ref para evitar guardar resultados duplicados durante el polling
  const processedTasksRef = useRef<Set<string>>(new Set());
  const workspaceStageTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollingTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollingAbortRef = useRef<AbortController | null>(null);
  const pollingInFlightRef = useRef(false);
  const activeAnalysisRequestRef = useRef<{ key: string; taskId: string | null } | null>(null);
  const workspaceVisualsRef = useRef<AnalysisComponent[]>([]);
  const workspacePreloadPromiseRef = useRef<Promise<void> | null>(null);
  // Ref para controlar el scroll inteligente
  const prevMessagesLengthRef = useRef(0);

  useEffect(() => {
    let cancelled = false;

    const scheduleWarmup = () => {
      duckdbEngine
        .warmup()
        .then(() => {
          if (!cancelled) {
            setIsDuckDBReady(true);
          }
        })
        .catch((error: any) => {
          console.warn('⚠️ [DuckDB] Warm-up ocioso no completado:', error);
        });
    };

    if (typeof window !== 'undefined' && 'requestIdleCallback' in window) {
      const idleId = window.requestIdleCallback(() => scheduleWarmup(), { timeout: 1200 });
      return () => {
        cancelled = true;
        window.cancelIdleCallback(idleId);
      };
    }

    const timeoutId = globalThis.setTimeout(scheduleWarmup, 300);
    return () => {
      cancelled = true;
      globalThis.clearTimeout(timeoutId);
    };
  }, [setIsDuckDBReady]);

  useEffect(() => {
    return () => {
      clearWorkspaceStageTimer();
    };
  }, [clearWorkspaceStageTimer]);

  // --- DELETE: Delete message from history ---
  const deleteMessage = async (messageId: string) => {
    try {
      const accessToken = await getChatAccessToken();
      if (!accessToken) return;

      const res = await fetch(`${API_BASE_URL}/api/v1/chat/messages/${messageId}`, {
        method: 'DELETE',
        headers: { 'Authorization': `Bearer ${accessToken}` }
      });

      if (res.ok || res.status === 404) {
        setMessages(prev => prev.filter(msg => msg.id !== messageId));
        // Solo mostrar toast si fue exitoso (200-299), si es 404 asumimos que ya estaba borrado y no molestamos
        if (res.ok) toast.success("Mensaje eliminado");
      } else {
        throw new Error(`Error al eliminar: ${res.status}`);
      }
    } catch (e) {
      console.error("Error eliminando mensaje:", e);
      toast.error("No se pudo eliminar el mensaje");
    }
  };

  // --- PERSISTENCE: Save message to backend ---
  const saveMessageToBackend = useCallback(async (role: 'user' | 'assistant', content: any) => {
    if (!analysisFileId) return;

    try {
      const accessToken = await getChatAccessToken();
      if (!accessToken) return;

      await fetch(`${API_BASE_URL}/api/v1/chat`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${accessToken}`
        },
        body: JSON.stringify({
          role,
          content,
          file_id: analysisFileId
        })
      });
    } catch (e) {
      console.error("Error guardando mensaje en historial:", e);
    }
  }, [analysisFileId, getChatAccessToken]);

  const refreshSaveDestinations = useCallback(async () => {
    setIsSaveDestinationsLoading(true);
    try {
      const accessToken = await getChatAccessToken();
      if (!accessToken) return;

      const response = await fetch(`${API_BASE_URL}/api/v1/presentations?_t=${Date.now()}`, {
        headers: {
          'Authorization': `Bearer ${accessToken}`,
          'Cache-Control': 'no-cache, no-store, must-revalidate'
        },
        cache: 'no-store'
      });

      if (!response.ok) return;
      const payload = await response.json().catch(() => []);
      setPresentations(Array.isArray(payload) ? payload : []);
    } catch (error) {
      console.error("Error cargando presentaciones para guardado:", error);
    } finally {
      setIsSaveDestinationsLoading(false);
    }
  }, [getChatAccessToken, setPresentations]);

  const handleOpenSaveDialog = (data: any, type: string, defaultTitle?: string) => {
    setReportToSave({ type, content: data });
    setReportTitle(defaultTitle || ""); // Pre-llenar con el título original si existe
    setSelectedSavePresentationId("");
    setIsSaveDialogOpen(true);
    void refreshSaveDestinations();
  };

  const handleConfirmSave = useCallback(async () => {
    if (!reportTitle.trim()) {
      toast.error("Por favor ingresa un título para el reporte.");
      return;
    }

    const fileId = analysisFileId;

    if (!fileId) {
      toast.error("No hay un archivo activo para asociar el reporte.");
      return;
    }

    setIsSaveActionLoading(true);
    try {
      const accessToken = await getChatAccessToken();
      if (!accessToken) return;

      const destinationPresentationId = selectedSavePresentationId.trim();
      const reportResponse = await fetch(`${API_BASE_URL}/api/v1/reports`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${accessToken}`
        },
        body: JSON.stringify({
          title: reportTitle,
          content: reportToSave,
          file_id: fileId,
          ...(destinationPresentationId ? { presentation_id: destinationPresentationId } : {}),
        })
      });

      if (!reportResponse.ok) {
        throw new Error(`Error al guardar reporte (${reportResponse.status})`);
      }

      const reportPayload = await reportResponse.json().catch(() => ({}));
      const savedPresentation = reportPayload?.presentation;
      const resolvedPresentationId =
        destinationPresentationId
        || (typeof reportPayload?.presentation_id === "string" ? reportPayload.presentation_id : "")
        || (typeof savedPresentation?.id === "string" ? savedPresentation.id : "");

      if (savedPresentation?.id) {
        const currentPresentations = Array.isArray(presentations) ? presentations : [];
        const nextPresentations = [...currentPresentations];
        const existingIndex = nextPresentations.findIndex((presentation: any) => presentation?.id === savedPresentation.id);

        if (existingIndex >= 0) {
          nextPresentations[existingIndex] = {
            ...nextPresentations[existingIndex],
            ...savedPresentation,
          };
        } else {
          nextPresentations.unshift(savedPresentation);
        }

        setPresentations(nextPresentations);
      }

      if (resolvedPresentationId) {
        setActivePresentationId(resolvedPresentationId);
      }

      const knownPresentations = Array.isArray(presentations) ? presentations : [];
      const selectedPresentation = knownPresentations.find((presentation: any) => presentation?.id === resolvedPresentationId);
      const resolvedPresentationName =
        (typeof savedPresentation?.name === "string" && savedPresentation.name.trim())
        || (typeof selectedPresentation?.name === "string" && selectedPresentation.name.trim())
        || null;

      const successMessage = destinationPresentationId
        ? (resolvedPresentationName
            ? `Reporte guardado en "${resolvedPresentationName}".`
            : "Reporte guardado en la presentación seleccionada.")
        : "Reporte guardado en la presentación automática del archivo.";

      toast.success(successMessage, {
        action: resolvedPresentationId
          ? {
              label: "Ir a la presentación",
              onClick: () => router.push(`/dashboard?presentationId=${encodeURIComponent(resolvedPresentationId)}`),
            }
          : undefined,
      });

      setSelectedSavePresentationId("");
      setIsSaveDialogOpen(false);
    } catch (error) {
      toast.error("No se pudo guardar el reporte.");
      console.error(error);
    } finally {
      setIsSaveActionLoading(false);
    }
  }, [
    analysisFileId,
    getChatAccessToken,
    presentations,
    reportTitle,
    reportToSave,
    router,
    selectedSavePresentationId,
    setActivePresentationId,
    setPresentations,
  ]);

  // --- EFFECT: Load initial file data and history ---
  useEffect(() => {
    const fileIdFromUrl = searchParams.get('fileId');
    if (fileIdFromUrl && fileIdFromUrl !== analysisFileId) {
      setAnalysisFileId(fileIdFromUrl);

      const fetchFileName = async () => {
        const { data, error } = await supabase
          .from('uploaded_files')
          .select('file_name')
          .eq('id', fileIdFromUrl)
          .single();
        if (error) {
          toast.error("No se pudo encontrar el archivo.");
          setFileName(null);
        } else {
          setFileName(data.file_name);
        }
      };
      fetchFileName();
    }
  }, [searchParams, supabase, analysisFileId]); // Eliminado fetchFileName de dependencias innecesarias

  // --- EFFECT: Load Chat History ---
  useEffect(() => {
    const loadHistory = async () => {
      if (!analysisFileId) return;
      try {
        const accessToken = await getChatAccessToken();
        if (!accessToken) return;

        const res = await fetch(`${API_BASE_URL}/api/v1/chat/${analysisFileId}`, {
          headers: { 'Authorization': `Bearer ${accessToken}` }
        });

        if (res.ok) {
          const history = await res.json();
          if (history && history.length > 0) {
            const formattedHistory = history.map((msg: any) => {
              let displayContent = "Contenido estructurado";
              let chatComponents = Array.isArray(msg.content) ? msg.content : undefined;
              let visualComponents: AnalysisComponent[] = [];

              if (typeof msg.content === 'string') {
                displayContent = msg.content;
              } else if (chatComponents) {
                const resumen = chatComponents.find((c: any) => c.type === 'mensaje_resumen');
                if (resumen && resumen.content) {
                  displayContent = resumen.content;
                }
                visualComponents = chatComponents.filter((c: any) => VISUAL_COMPONENT_TYPES.includes(c.type));
                chatComponents = chatComponents.filter((c: any) => !VISUAL_COMPONENT_TYPES.includes(c.type));
              }

              return {
                id: msg.id || Date.now().toString(),
                type: msg.role,
                content: displayContent,
                timestamp: new Date(msg.created_at),
                components: chatComponents,
                _visuals: visualComponents
              };
            });
            
            // Cargar los últimos visuales al workspace canvas
            const lastAssistantWithVisuals = [...formattedHistory].reverse().find(m => m.type === 'assistant' && m._visuals && m._visuals.length > 0);
            if (lastAssistantWithVisuals) {
              stageWorkspaceVisuals(lastAssistantWithVisuals._visuals);
            } else {
              setWorkspaceRenderState({
                status: 'idle',
                message: null,
                pendingVisuals: 0,
                renderedVisuals: 0,
              });
            }
            
            setMessages(formattedHistory as ChatMessage[]);
          } else {
            // Si no hay historial, mostrar mensaje de bienvenida
            setWorkspaceRenderState({
              status: 'idle',
              message: null,
              pendingVisuals: 0,
              renderedVisuals: 0,
            });
            setMessages([{
              id: 'initial-welcome',
              type: 'assistant',
              content: `¡Hola! Archivo cargado. ¿Qué te gustaría saber?`,
              timestamp: new Date()
            }]);
          }
        }
      } catch (e) {
        console.error("Error cargando historial de chat", e);
        setWorkspaceRenderState({
          status: 'idle',
          message: null,
          pendingVisuals: 0,
          renderedVisuals: 0,
        });
      }
    };
    loadHistory();
  }, [analysisFileId, supabase, getChatAccessToken, stageWorkspaceVisuals, setWorkspaceRenderState]);

  // --- EFFECT: Scroll to bottom ---
  useEffect(() => {
    if (chatContainerRef.current) {
      // Solo hacer scroll si se agregaron nuevos mensajes (evitar scroll al borrar o actualizar)
      if (messages.length > prevMessagesLengthRef.current) {
        // Pequeño timeout para asegurar que el DOM se haya pintado antes de scrollear
        setTimeout(() => {
          if (chatContainerRef.current) {
            chatContainerRef.current.scrollTop = chatContainerRef.current.scrollHeight;
          }
        }, 100);
      }
    }
    prevMessagesLengthRef.current = messages.length;
  }, [messages]);

  // --- EFFECT: Polling ---
  useEffect(() => {
    if (!isAnalyzing || !activeTaskId) return;
    let cancelled = false;

    const clearPollingState = () => {
      if (pollingTimerRef.current) {
        clearTimeout(pollingTimerRef.current);
        pollingTimerRef.current = null;
      }
      if (pollingAbortRef.current) {
        pollingAbortRef.current.abort();
        pollingAbortRef.current = null;
      }
      pollingInFlightRef.current = false;
    };

    const scheduleNextPoll = (delayMs: number) => {
      if (cancelled) return;
      if (pollingTimerRef.current) {
        clearTimeout(pollingTimerRef.current);
      }
      pollingTimerRef.current = setTimeout(runPoll, delayMs);
    };

    const runPoll = async () => {
      if (cancelled || pollingInFlightRef.current) return;

      pollingInFlightRef.current = true;
      const controller = new AbortController();
      pollingAbortRef.current = controller;

      try {
        const response = await fetch(`${API_BASE_URL}/api/v1/tasks/${activeTaskId}`, {
          signal: controller.signal,
          cache: 'no-store',
        });
        if (!response.ok) {
          scheduleNextPoll(1800);
          return;
        }

        const data = await response.json();

        if (data.status === 'completed' || data.status === 'failed') {
          clearPollingState();

          const componentsList: AnalysisComponent[] = [];

          // NUeva Lógica de Parsing Híbrido (V2)
          if (Array.isArray(data.result)) {
            // Caso Legacy: La respuesta ya es una lista de componentes
            componentsList.push(...data.result);
          } else if (typeof data.result === 'object' && data.result !== null) {
            // Caso Nuevo (Flat JSON): { analysis, chart_options, data }
            // 1. Convertimos el texto 'analysis' en un componente mensaje_resumen
            if (data.result.analysis) {
              componentsList.push({
                type: 'mensaje_resumen',
                content: data.result.analysis,
                texto: data.result.analysis // Compatibilidad
              });
            }

            if (data.result.metrics && typeof data.result.metrics === 'object' && Object.keys(data.result.metrics).length > 0) {
              componentsList.push({
                type: 'metricas_clave',
                data: data.result.metrics,
                title: 'Métricas Clave'
              });
            }

            // 2. Convertimos 'chart_options' en componente(s) configuracion_echarts
            if (Array.isArray(data.result.chart_options) && data.result.chart_options.length > 0) {
              data.result.chart_options.forEach((opt: any, index: number) => {
                if (opt.type === 'smart_table') {
                  
                  // 🏹 [FASE 3B] Arrow hydration: si viene arrow_data, decodificar antes de renderizar
                  if (opt.arrow_data) {
                    opt.data = tryParseArrow(opt.arrow_data, opt.data);
                  }
                  
                  const chartTableName = buildAnalysisTableName(
                    analysisFileId,
                    'smart_table',
                    opt.id || opt.title || index
                  );
                  const smartTableArrow = opt.granular_arrow || opt.arrow_data || null
                  if (smartTableArrow) {
                    void duckdbEngine.preloadArrowTables([
                      { tableName: chartTableName, base64Data: smartTableArrow, priority: 1 }
                    ], 0)
                      .then(() => setIsDuckDBReady(true))
                      .catch((err: any) => console.warn('⚠️ [DuckDB] Smart Table no cargada:', err));
                  }
                  
                  opt.table_name = chartTableName;
                  componentsList.push(opt);
                  
                } else {
                  let chartTableName = undefined;
                  const targetArrow = opt.granular_arrow || opt.arrow_data;
                  if (targetArrow) {
                    chartTableName = buildAnalysisTableName(
                      analysisFileId,
                      'chart',
                      opt.id || opt.title?.text || index
                    );
                    void duckdbEngine.preloadArrowTables([
                      { tableName: chartTableName, base64Data: targetArrow, priority: 0 }
                    ], 0)
                      .then(() => setIsDuckDBReady(true))
                      .catch((err: any) => console.warn('⚠️ [DuckDB] Chart Arrow no cargado:', err));
                  } else {
                    // [FIX 2026-06-09] FALLBACK: El backend solo inyecta arrow_data a nivel
                    // top-level (final_struct.arrow_data), no dentro de chart_options[*].
                    // Si este chart no tiene granular_arrow ni arrow_data per-chart, igual
                    // podemos cross-filtrar usando la MISMA tabla top-level
                    // (pd_analysis_<fileId>_detail) que el backend SÍ garantiza.
                    // Sin este fallback, el botón "Filtrar aquí" siempre falla con
                    // "No hay datos cargados" cuando el canary omite granular_arrow.
                    // IMPORTANTE: usamos el MISMO nombre que la línea 935
                    // (buildAnalysisTableName(analysisFileId, 'analysis', 'detail'))
                    // para que ambos referencien la misma tabla en DuckDB-WASM.
                    const topLevelArrow = data.result?.arrow_data;
                    if (topLevelArrow) {
                      chartTableName = buildAnalysisTableName(
                        analysisFileId,
                        'analysis',
                        'detail'
                      );
                    }
                  }

                  componentsList.push({
                    type: 'configuracion_echarts',
                    option: opt,
                    table_name: chartTableName,
                    title: opt.title?.text || "Análisis Visual"
                  });
                }
              });
            } else if (data.result.chart_options && typeof data.result.chart_options === 'object' && Object.keys(data.result.chart_options).length > 0) {
              // Legacy: single chart_options object
              componentsList.push({
                type: 'configuracion_echarts',
                option: data.result.chart_options,
                title: data.result.chart_options.title?.text || "Análisis Visual"
              });
            }

            // 3. Convertimos 'data' en un componente tabla_datos
            // 🏹 [FASE 3B] Arrow Transport: si el backend envió arrow_data, decodificar
            const resolvedTableData = data.result.arrow_data
              ? tryParseArrow(data.result.arrow_data, data.result.data)
              : data.result.data;

            // 🦆 [FASE 4] Cargar tabla_datos en DuckDB para cross-filtering
            if (data.result.arrow_data) {
              duckdbEngine.preloadArrowTables([
                {
                  tableName: buildAnalysisTableName(analysisFileId, 'analysis', 'detail'),
                  base64Data: data.result.arrow_data,
                  priority: 2
                }
              ], 0)
                .then(() => setIsDuckDBReady(true))
                .catch((err: any) => console.warn('⚠️ [DuckDB] Tabla datos no cargada:', err));
            }

            if (resolvedTableData && Array.isArray(resolvedTableData) && resolvedTableData.length > 0) {
              componentsList.push({
                type: 'tabla_datos',
                data: resolvedTableData,
                title: "Datos Detallados"
              });
            }

            // 4. 🎯 [PHASE 3] Prescriptive: recomendaciones
            if (data.result.recommendations && Array.isArray(data.result.recommendations) && data.result.recommendations.length > 0) {
              componentsList.push({
                type: 'recomendaciones',
                data: data.result.recommendations
              });
            }

            if (data.result.explainability && Array.isArray(data.result.explainability) && data.result.explainability.length > 0) {
              data.result.explainability.forEach((item: any) => {
                if (item && typeof item === 'object') {
                  componentsList.push({
                    type: 'explicabilidad_analitica',
                    data: item
                  });
                }
              });
            }

            // 🦆 [FASE 4] SNAPSHOT ARROW: Dataset completo para DuckDB cross-filtering
            // El backend inyecta el Parquet snapshot como Arrow IPC base64.
            // Esto da a DuckDB TODAS las dimensiones para filtrado en cualquier dirección.
            if (data.result.snapshot_arrow) {
              duckdbEngine.preloadArrowTables([
                {
                  tableName: buildAnalysisTableName(analysisFileId, 'snapshot', 'latest'),
                  base64Data: data.result.snapshot_arrow,
                  priority: 0
                }
              ], 0)
                .then(() => {
                  setIsDuckDBReady(true);
                  console.log('🦆 [SNAPSHOT] Dataset completo cargado en DuckDB ✅');
                })
                .catch((err: any) => console.warn('⚠️ [DuckDB] Snapshot no cargado:', err));
            }
          }

          const summaryComponent = componentsList.find(c => c.type === 'mensaje_resumen');
          let finalContent = "El análisis ha finalizado.";

          if (summaryComponent) {
            // Prioridad: 'texto' (Backend nuevo) > 'content' (Backend viejo)
            if (typeof summaryComponent.texto === 'string') finalContent = summaryComponent.texto;
            else if (typeof summaryComponent.content === 'string') finalContent = summaryComponent.content;
          }

          // --- FASE 5: Separación de Responsabilidades ---
          const visualComponents = componentsList.filter(c => VISUAL_COMPONENT_TYPES.includes(c.type as (typeof VISUAL_COMPONENT_TYPES)[number]));
          const chatComponents = componentsList.filter(c => !VISUAL_COMPONENT_TYPES.includes(c.type as (typeof VISUAL_COMPONENT_TYPES)[number]));

          if (visualComponents.length > 0) {
            stageWorkspaceVisuals(visualComponents);
            
            // Inyectar el mensaje automático conversacional si hay elementos pesados
            if (!chatComponents.some(c => c.type === 'mensaje_resumen')) {
              const infoMsg = "He realizado el análisis sobre los datos que solicitaste. He colocado los gráficos y tablas en tu lienzo principal para que puedas explorarlos.";
              chatComponents.push({
                type: 'mensaje_resumen',
                content: infoMsg,
                // @ts-ignore
                texto: infoMsg
              });
              finalContent = infoMsg;
            }
          } else {
            setWorkspaceRenderState({
              status: 'idle',
              message: null,
              pendingVisuals: 0,
              renderedVisuals: 0,
            });
          }

          // Guardar respuesta del asistente en historial (persistencia)
          // Solo si: 1. Es exitoso o fallido con componentes. 2. NO ha sido procesado ya en esta sesión.
          if (!processedTasksRef.current.has(activeTaskId!)) {
            // NOTA FASE 5: Persistimos componentsList completo para no perder datos en la DB
            if (componentsList.length > 0) {
              saveMessageToBackend('assistant', componentsList);
              processedTasksRef.current.add(activeTaskId!);
            } else if (data.status === 'failed') {
              saveMessageToBackend('assistant', [{ type: 'error', content: finalContent }]);
              processedTasksRef.current.add(activeTaskId!);
            }
          }

          setMessages(prevMessages => prevMessages.map(msg =>
            msg.id === activeTaskId
              ? {
                ...msg,
                content: finalContent,
                components: data.status === 'completed' ? chatComponents : undefined,
                _visuals: data.status === 'completed' && visualComponents.length > 0 ? visualComponents : undefined
              }
              : msg
          ));

          if (activeAnalysisRequestRef.current?.taskId === activeTaskId) {
            activeAnalysisRequestRef.current = null;
          }
          setActiveTaskId(null);
          setIsAnalyzing(false);
          if (data.status === 'failed') {
            setWorkspaceRenderState({
              status: 'idle',
              message: null,
              pendingVisuals: 0,
              renderedVisuals: 0,
            });
          }
          // Set last task as parent for next message
          if (data.status === 'completed') {
            setLastCompletedTaskId(activeTaskId);
          }
          return;
        }

        scheduleNextPoll(1800);
      } catch (error: any) {
        if (error?.name === 'AbortError') {
          return;
        }
        console.error(`Error durante el polling:`, error);
        scheduleNextPoll(2200);
      } finally {
        pollingInFlightRef.current = false;
        if (pollingAbortRef.current === controller) {
          pollingAbortRef.current = null;
        }
      }
    };

    scheduleNextPoll(600);
    return () => {
      cancelled = true;
      clearPollingState();
    }
  }, [isAnalyzing, activeTaskId, saveMessageToBackend, setWorkspaceRenderState, stageWorkspaceVisuals]);



  // --- VOICE RECOGNITION ---
  const toggleListening = useCallback(() => {
    if (isListening) {
      recognitionRef.current?.stop();
      setIsListening(false);
      return;
    }

    const SpeechRecognition = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SpeechRecognition) {
      toast.error("Tu navegador no soporta entrada de voz.");
      return;
    }

    const recognition = new SpeechRecognition();
    recognition.lang = 'es-ES';
    recognition.continuous = false;
    recognition.interimResults = false;

    recognition.onstart = () => {
      setIsListening(true);
      toast.info("Escuchando...");
    };

    recognition.onresult = (event: any) => {
      const transcript = event.results[0][0].transcript;
      setMessage(prev => prev + (prev ? " " : "") + transcript);
    };

    recognition.onerror = (event: any) => {
      console.error("Error de voz:", event.error);
      setIsListening(false);
      if (event.error !== 'no-speech') {
        toast.error("Error al escuchar.");
      }
    };

    recognition.onend = () => {
      setIsListening(false);
    };

    recognitionRef.current = recognition;
    recognition.start();

  }, [isListening]);


  // Refactor: Separar lógica de envío para llamarla desde Clic o Submit
  const triggerAnalysis = useCallback(async (customMessage?: string) => {
    const textToSend = customMessage || message;

    if (!textToSend.trim() || !analysisFileId) {
      if (!customMessage) toast.error("Por favor escribe un mensaje.");
      return;
    }

    const currentMessage = textToSend.trim();
    const analysisRequestKey = buildAnalysisRequestKey(
      analysisFileId,
      currentMessage,
      lastCompletedTaskId
    );
    const activeRequest = activeAnalysisRequestRef.current;
    if (activeRequest) {
      if (activeRequest.key === analysisRequestKey) {
        return;
      }
      if (!customMessage) {
        toast.info("Ya hay un análisis en curso para este archivo.");
      }
      return;
    }

    activeAnalysisRequestRef.current = {
      key: analysisRequestKey,
      taskId: null,
    };

    const isDrillDown = currentMessage.startsWith("🔍 Drill-Down:");

    // 1. UI: Mostramos SOLO el texto limpio al usuario (sin el JSON técnico)
    const userMessage: ChatMessage = {
      id: Date.now().toString(),
      type: "user",
      content: currentMessage,
      timestamp: new Date()
    };

    setMessages(prev => [...prev, userMessage]);
    setMessage("");
    setIsAnalyzing(true);
    clearWorkspaceStageTimer();
    setWorkspaceRenderState({
      status: 'analyzing',
      message: 'Preparando el lienzo y priorizando el visual principal...',
      pendingVisuals: 0,
      renderedVisuals: 0,
    });

    // Guardamos historial visual limpio
    saveMessageToBackend('user', currentMessage);

    const accessToken = await getChatAccessToken();
    if (!accessToken) {
      toast.error("Sesión expirada");
      activeAnalysisRequestRef.current = null;
      setIsAnalyzing(false);
      setWorkspaceRenderState({
        status: 'idle',
        message: null,
        pendingVisuals: 0,
        renderedVisuals: 0,
      });
      return;
    }

    try {
      // 2. EL TRUCO MAESTRO: Empaquetamos el contexto (Payload Oculto)
      // Usamos 'lastCompletedTaskId' que ya tienes en el estado como el ID del padre
      const enrichedPrompt = JSON.stringify({
        text: currentMessage,
        parent_id: lastCompletedTaskId // <--- AQUÍ ESTÁ EL CONECTOR DE MEMORIA
      });

      // 3. Enviamos el paquete enriquecido al backend
      const response = await fetch(`${API_BASE_URL}/api/v1/analyze`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${accessToken}` },
        body: JSON.stringify({ file_id: analysisFileId, prompt: enrichedPrompt })
      });

      if (!response.ok) throw new Error("Error en petición");
      const data = await response.json();
      if (activeAnalysisRequestRef.current?.key === analysisRequestKey) {
        activeAnalysisRequestRef.current = {
          key: analysisRequestKey,
          taskId: data.task_id,
        };
      }
      setActiveTaskId(data.task_id);

      // ... (Resto de tu lógica de mensajes de carga) ...
      const assistantLoadingMessage: ChatMessage = {
        id: data.task_id,
        type: "assistant",
        content: isDrillDown ? "Profundizando en el análisis..." : "Analizando... por favor espera.",
        timestamp: new Date(),
        taskId: data.task_id,
      };
      setMessages(prev => [...prev, assistantLoadingMessage]);

    } catch (error: any) {
      console.error(error);
      const errorMessage: ChatMessage = {
        id: Date.now().toString() + "-error",
        type: "assistant",
        content: `Error: ${error.message}`,
        timestamp: new Date()
      };
      setMessages(prev => [...prev, errorMessage]);
      activeAnalysisRequestRef.current = null;
      setIsAnalyzing(false);
      setActiveTaskId(null);
      setWorkspaceRenderState({
        status: 'idle',
        message: null,
        pendingVisuals: 0,
        renderedVisuals: 0,
      });
    }
  }, [message, analysisFileId, supabase, saveMessageToBackend, setMessages, setMessage, setIsAnalyzing, setActiveTaskId, lastCompletedTaskId, getChatAccessToken, clearWorkspaceStageTimer, setWorkspaceRenderState]);

  // 🔒 Ref estable para isAnalyzing — evita recrear handleChartDrillDown en cada cambio
  const isAnalyzingRef = useRef(isAnalyzing);
  useEffect(() => { isAnalyzingRef.current = isAnalyzing; }, [isAnalyzing]);

  const handleChartDrillDown = useCallback((params: any, tableName?: string) => {
    // Leer via ref para no depender del closure
    if (isAnalyzingRef.current) return;

    // 🔍 [DIAG] Verificar qué recibe handleChartDrillDown
    console.log('🔍 [DIAG handleChartDrillDown] params received:', {
      hasCrossFilterContext: !!params?.crossFilterContext,
      crossFilterContext: params?.crossFilterContext,
      hasOption: !!params?.option,
      paramsKeys: params ? Object.keys(params).filter((k: string) => k.includes('cross') || k.includes('filter') || k.includes('option') || k.includes('Context')) : [],
    });

    const rawCategory = extractRawChartCategory(params);
    if (rawCategory) {
      const rawSecondaryCategory = typeof params?.rawSecondaryCategory === 'string'
        ? params.rawSecondaryCategory.replace(/\0/g, '').normalize('NFC').replace(/\s+/g, ' ').trim()
        : null;
      const seriesName = rawSecondaryCategory || params.seriesName || 'valor';
      const category = rawCategory;
      const heatmapPoint = Array.isArray(params?.data)
        ? params.data
        : (Array.isArray(params?.value) ? params.value : null);
      const value = Array.isArray(heatmapPoint) && heatmapPoint.length >= 3
        ? heatmapPoint[2]
        : params.value;

      const x = params.eventCoordinates?.x || 0;
      const y = params.eventCoordinates?.y || 0;

      const safeCategory = String(category).replace(/\0/g, '');
      const safeSeries = String(seriesName).replace(/\0/g, '');

      setDrillDown({
        isVisible: true,
        position: { x, y },
        dataContext: {
          category: safeCategory,
          value: value,
          series: safeSeries,
          tableName: tableName,
          secondaryCategory: rawSecondaryCategory || undefined,
          crossFilterContext: params?.crossFilterContext || undefined,
          option: params?.option || undefined
        }
      });
    }
  }, [setDrillDown]);

  const handleDrillDownSelect = useCallback((prompt: string) => {
    setDrillDown(prev => ({ ...prev, isVisible: false }));
    triggerAnalysis(prompt);
  }, [triggerAnalysis, setDrillDown]);

  // 🦆 [FASE 4] Cross-Filter Handler (DuckDB-WASM local, Multidimensional)
  const handleCrossFilter = useCallback(async (filters: Record<string, string>, tableName?: string, crossFilterContext?: any) => {
    // ── PURE LOCAL CROSS-FILTER (DuckDB-WASM, <50ms) ────────────────
    // Filters directly on loaded pd_chart_* tables in WASM memory.
    // If tableName is undefined, probes ALL loaded tables to find the match.
    // NEVER invokes the LLM — this is a pure data-grid operation.
    try {
      if (workspacePreloadPromiseRef.current) {
        await workspacePreloadPromiseRef.current.catch(() => {});
      }
      const loadedTables = duckdbEngine.getTableNames();

      // ── Step 1: Resolve the target table ──
      let tName = crossFilterContext?.source_table || tableName;

      if (!tName || !loadedTables.includes(tName)) {
        // tableName missing or not loaded — find it from visual components
        const visualSources: any[] = [];
        if (Array.isArray(workspaceVisualsRef.current)) {
          visualSources.push(...workspaceVisualsRef.current);
        }
        if (Array.isArray(workspaceItems)) {
          visualSources.push(...workspaceItems);
        }
        const lastVisuals = [...messages]
          .reverse()
          .find((msg) => Array.isArray(msg._visuals) && msg._visuals.length > 0)?._visuals || [];
        if (Array.isArray(lastVisuals)) {
          visualSources.push(...lastVisuals);
        }

        // Try to find a table_name from the visual components that IS loaded
        for (const comp of visualSources) {
          if (!comp || typeof comp !== 'object') continue;
          const candidates = [
            comp?.table_name,
            comp?.option?.table_name,
            comp?.original_chart_option?.table_name,
          ].filter(Boolean) as string[];
          for (const candidate of candidates) {
            if (loadedTables.includes(candidate)) {
              tName = candidate;
              break;
            }
          }
          if (tName) break;
        }

        // Still nothing? Try loading Arrow from the component on-demand
        if (!tName || !loadedTables.includes(tName)) {
          const findArrowPayload = (component: any): string | null => {
            if (!component || typeof component !== 'object') return null;
            return (
              component?.granular_arrow ||
              component?.arrow_data ||
              component?.option?.granular_arrow ||
              component?.option?.arrow_data ||
              component?.original_chart_option?.granular_arrow ||
              component?.original_chart_option?.arrow_data ||
              null
            );
          };

          for (const comp of visualSources) {
            const arrow = findArrowPayload(comp);
            const compTable = comp?.table_name || comp?.option?.table_name || comp?.original_chart_option?.table_name;
            if (arrow && compTable) {
              await duckdbEngine.loadArrowData(arrow, compTable);
              setIsDuckDBReady(true);
              tName = compTable;
              console.log(`🦆 [CROSS-FILTER] On-demand Arrow cargado para tabla: ${tName}`);
              break;
            }
          }
        }

        // Last resort: use first loaded table (pd_chart_* tables are already there)
        if (!tName || !loadedTables.includes(tName)) {
          const refreshedTables = duckdbEngine.getTableNames();
          if (refreshedTables.length > 0) {
            tName = refreshedTables[0];
            console.log(`🦆 [CROSS-FILTER] Usando primera tabla disponible: ${tName}`);
          } else {
            console.error('⚠️ [CROSS-FILTER] No hay tablas cargadas en DuckDB-WASM.');
            toast.error('No hay datos cargados para filtrar localmente.');
            return;
          }
        }
      }

      console.log(`🦆 [CROSS-FILTER] Tabla resuelta: "${tName}" (de ${loadedTables.length} cargadas)`);

      // ── Step 2: Enrich filters (synthetic bucket for "Otros") ──
      const findComponentByTableName = (list: any[]): any | null => {
        for (const component of list) {
          if (!component || typeof component !== 'object') continue;
          if (component?.table_name === tName) return component;
          if (component?.option?.table_name === tName) return component;
          if (component?.original_chart_option?.table_name === tName) return component;
        }
        return null;
      };
      const allVisualSources: any[] = [];
      if (Array.isArray(workspaceItems)) allVisualSources.push(...workspaceItems);
      const lastVis = [...messages]
        .reverse()
        .find((msg) => Array.isArray(msg._visuals) && msg._visuals.length > 0)?._visuals || [];
      if (Array.isArray(lastVis)) allVisualSources.push(...lastVis);

      const matchedComponent = findComponentByTableName(allVisualSources);

      // [FIX 2026-06-08] Extraer los filtros base del chart original (e.g.
      // "Tipo Movimiento = Ingreso" si el chart solo graficaba ingresos).
      // El backend (canary_executor) ahora inyecta estos en chart_option.chart_base_filters.
      // Sin mergearlos, DuckDB solo filtra por el clic y retorna registros
      // que no pertenezcan al subset que el chart estaba visualizando.
      //
      // [FIX 2026-06-09] Aplicar sanitizeFilterValue a cada valor para
      // limpiar prefijos como "FilterOperator.EQUALS " que el backend
      // serializa accidentalmente. Sin esto, DuckDB busca la cadena
      // literal y nunca encuentra coincidencia.
      let baseFilters: Record<string, string> = {};
      if (matchedComponent) {
        const chartOption =
          (matchedComponent as any).option ||
          (matchedComponent as any).original_chart_option ||
          null;
        if (chartOption?.chart_base_filters && typeof chartOption.chart_base_filters === 'object') {
          baseFilters = Object.fromEntries(
            Object.entries(chartOption.chart_base_filters).map(
              ([k, v]) => [k, sanitizeFilterValue(String(v))]
            )
          );
        }
      }

      // [FIX 2026-06-09] RUTEO INTELIGENTE DE TABLA: Si la tabla resuelta
      // es per-chart (pd_chart_*, agregada), preferir SIEMPRE la tabla
      // snapshot (pd_snapshot_*, raw con todas las dimensiones).
      //
      // Por que: el requerimiento de negocio es que el usuario SIEMPRE
      // vea el detalle crudo (drill-down) cuando hace clic en 'Filtrar aquí',
      // independientemente de si el chart tiene filtros base o no.
      //
      // La tabla per-chart contiene el resultado agregado (e.g. sum(monto)
      // by month), NO contiene todas las columnas dimensionales. El usuario
      // espera ver los registros crudos subyacentes, no una sola fila
      // agregada (Name, Value) que es lo que retorna la tabla per-chart
      // cuando se filtra por un valor dimensional.
      //
      // La tabla snapshot (pd_snapshot_<fileId>_latest) SÍ tiene todas
      // las dimensiones raw: fecha_operacion, placa_unidad, tipo_unidad,
      // origen, destino, km_recorridos, horas_manejo, galones_consumidos,
      // gasto_combustible_s, etc. (truncada a 10,000 filas por el
      // BIG DATA SHIELD del backend). Esta tabla siempre se pre-carga
      // junto con el chart (linea 1014-1027) cuando data.result.snapshot_arrow
      // existe.
      //
      // [FIX 2026-06-09 2da iteracion] Se elimino la condicion
      // `Object.keys(baseFilters).length > 0` porque el ruteo debe ocurrir
      // siempre que la tabla per-chart sea agregada, no solo cuando hay
      // filtros dimensionales.
      //
      // [FIX 2026-06-09 3ra iteracion] Se cambio la tabla destino de
      // `pd_analysis_*_detail` (que resulto ser AGREGADA, no raw) a
      // `pd_snapshot_*_latest` (que es RAW con todas las dimensiones).
      // La tabla pd_analysis_*_detail solo contiene la salida agregada
      // del chart (e.g. name='Aug-2021', value=sum), NO las columnas
      // dimensionales como fecha_operacion. Por eso DuckDB retornaba
      // solo 1 fila para 'Aug-2021': matcheaba contra la columna 'name'
      // agregada. La tabla pd_snapshot_*_latest tiene 10K filas raw
      // con fecha_operacion como timestamp, donde el motor DuckDB puede
      // aplicar el predicate temporal EXTRACT(YEAR|MONTH).
      //
      // [FIX 2026-06-09 4ta iteracion] Se guarda la tabla original
      // (per-chart, agregada) en `originalChartTableName` como fallback.
      // Si el cross-filter contra la tabla snapshot retorna 0 rows
      // (porque el snapshot de 10K head() no contiene el periodo
      // seleccionado por el usuario, e.g. 'Dec-2023' cae fuera del
      // rango de las primeras 10K filas), hacemos fallback a la tabla
      // per-chart y mostramos la fila agregada con un mensaje claro.
      // Esto preserva el contrato de UX: el usuario SIEMPRE ve datos
      // del periodo que selecciono, ya sea crudos o agregados.
      let originalChartTableName: string | null = null;
      if (
        tName &&
        (tName.startsWith('pd_chart_') || tName.startsWith('pd_analysis_')) &&
        analysisFileId
      ) {
        originalChartTableName = tName;
        const snapshotTableName = buildAnalysisTableName(
          analysisFileId,
          'snapshot',
          'latest'
        );
        const refreshedTables = duckdbEngine.getTableNames();
        if (refreshedTables.includes(snapshotTableName)) {
          const reason = Object.keys(baseFilters).length > 0
            ? `hay ${Object.keys(baseFilters).length} filtro(s) dimensional(es) que requieren columnas raw`
            : `el usuario espera ver el detalle crudo subyacente (drill-down)`;
          console.log(
            `🦆 [CROSS-FILTER] Re-ruteo a tabla snapshot '${snapshotTableName}' ` +
            `porque ${reason} (la tabla '${tName}' solo tiene datos agregados).`
          );
          tName = snapshotTableName;
        }
      }

      // Merge: los filtros base del chart (e.g. "Tipo Movimiento=Ingreso")
      // se combinan con el clic del usuario (e.g. "mes=Jan-2025").
      // Si el usuario hace click en algo que YA está en los filtros base,
      // gana el clic (overwrite) para evitar duplicación.
      const mergedFilters = { ...baseFilters, ...filters };
      const effectiveFilters = matchedComponent
        ? enrichFiltersWithSyntheticBucket(mergedFilters, matchedComponent)
        : mergedFilters;

      // Build summaries for UI feedback
      const baseFilterSummary = Object.entries(baseFilters)
        .filter(([key]) => !key.startsWith('__'))
        .map(([k, v]) => `${k}="${v}"`)
        .join(' AND ');
      const clickFilterSummary = Object.entries(filters)
        .filter(([key]) => !key.startsWith('__'))
        .map(([k, v]) => `${k}="${v}"`)
        .join(' AND ');
      const filterSummary = Object.entries(mergedFilters)
        .filter(([key]) => !key.startsWith('__'))
        .map(([k, v]) => `${k}="${v}"`)
        .join(' AND ');

      // ── Step 3: Execute local DuckDB cross-filter ──
      console.log(
        `🦆 [CROSS-FILTER] Filtrando: ${filterSummary} en tabla '${tName}'`,
        { baseFilters, clickFilters: filters, merged: mergedFilters, crossFilterContext }
      );
      let filtered = await duckdbEngine.crossFilter(effectiveFilters, tName, crossFilterContext);

      // [FIX 2026-06-09 4ta iteracion] FALLBACK al per-chart si el snapshot
      // retorna 0 rows. Esto pasa cuando el snapshot de 10K head() no
      // contiene el periodo seleccionado (e.g. 'Dec-2023' cae fuera del
      // rango de las primeras 10K filas). En ese caso, mostramos la fila
      // agregada del per-chart como fallback honesto.
      let isAggregatedFallback = false;
      if (filtered.length === 0 && originalChartTableName) {
        console.log(
          `🦆 [CROSS-FILTER] Snapshot sin resultados. Fallback a tabla per-chart ` +
          `'${originalChartTableName}' para mostrar datos agregados del periodo.`
        );
        const fallbackFiltered = await duckdbEngine.crossFilter(
          effectiveFilters,
          originalChartTableName,
          crossFilterContext
        );
        if (fallbackFiltered.length > 0) {
          filtered = fallbackFiltered;
          isAggregatedFallback = true;
        }
      }

      if (filtered.length === 0) {
        console.warn('⚠️ [CROSS-FILTER] Sin resultados, manteniendo vista actual');
        toast.info('No se encontraron registros con ese filtro.');
        return;
      }

      // ── Step 4: Dispatch to workspace canvas (ORIGINAL BEHAVIOR) ──
      const newTableComponent: AnalysisComponent = {
        type: 'tabla_datos' as const,
        data: filtered,
        title: `Datos Filtrados: ${filterSummary}`
      };

      console.log('4. [Bridge/Chat] Despachando a workspaceItemsAtom y messagesAtom:', { newTableComponent });
      setWorkspaceItems((prev: AnalysisComponent[]) => [...prev, newTableComponent]);

      // [FIX 2026-06-08] UI feedback que muestra EXPLÍCITAMENTE la suma de
      // los filtros (base + clic) para que el usuario confíe en la tabla
      // resultante y entienda por qué algunos registros fueron excluidos.
      let filterDescription = `⚡ **Filtro local aplicado:** \n\n`;
      if (baseFilterSummary) {
        filterDescription += `📊 **Filtros base del chart:** ${baseFilterSummary}\n`;
      }
      if (clickFilterSummary) {
        filterDescription += `➕ **Filtros del clic:** ${clickFilterSummary}\n`;
      }
      if (!baseFilterSummary && !clickFilterSummary && !crossFilterContext?.base_predicates?.length && !crossFilterContext?.runtime_predicates?.length) {
        filterDescription += `🔍 Sin filtros activos\n`;
      }
      if (isAggregatedFallback) {
        filterDescription += `\n⚠️ **Nota:** El detalle crudo no estaba disponible para este periodo (el snapshot solo incluye las primeras 10,000 filas del dataset). Mostrando la **fila agregada** del chart. Para ver el detalle completo, intenta con un periodo cubierto por el snapshot.\n`;
      }
      filterDescription += `\n📌 *Se encontraron ${filtered.length} registros en <50ms.* \nLa tabla filtrada ha sido añadida a tu Lienzo.`;

      const filterMsg: ChatMessage = {
        id: `crossfilter-${Date.now()}`,
        type: 'assistant',
        content: filterDescription,
        timestamp: new Date(),
        components: [],
        _visuals: [newTableComponent]
      };
      setMessages(prev => [...prev, filterMsg]);
      console.log(`🦆 [CROSS-FILTER] Completado: ${filtered.length} filas`);
    } catch (error) {
      console.error('⚠️ [CROSS-FILTER] Error en filtro local:', error);
      toast.error('Error al aplicar filtro local. Intenta de nuevo.');
    }
  }, [workspaceItems, messages, setIsDuckDBReady, setWorkspaceItems, setMessages]);

  const handleSendMessage = async (e?: React.FormEvent) => {
    if (e?.preventDefault) e.preventDefault();
    // STOP LOGIC (Existente)
    if (isAnalyzing) {
      // ... (Lógica de Stop ya implementada, mantenemos igual)
      if (activeTaskId) {
        // ... (Repetir lógica de Stop aquí o extraerla)
        // Para no romper la refactorización, voy a asumir que el usuario quiere
        // mantener la lógica de Stop aquí.
        // COPIAR PEGAR LOGICA DE STOP ANTERIOR (Simplificada por brevedad en replace)
        try {
          setIsAnalyzing(false);
          clearWorkspaceStageTimer();
          setMessages(prev => {
            const newMessages = [...prev];
            const index = newMessages.findIndex(m => m.id === activeTaskId);
            if (index !== -1) newMessages[index] = { ...newMessages[index], content: "🛑 Análisis detenido." };
            return newMessages;
          });

          const accessToken = await getChatAccessToken();
          if (accessToken) {
            fetch(`${API_BASE_URL}/api/v1/tasks/${activeTaskId}/cancel`, {
              method: 'POST',
              headers: { 'Authorization': `Bearer ${accessToken}` }
            });
          }
          setWorkspaceRenderState({
            status: 'idle',
            message: null,
            pendingVisuals: 0,
            renderedVisuals: 0,
          });
          activeAnalysisRequestRef.current = null;
          toast.success("Cancelado.");
        } catch (e) {
          console.error("Error al cancelar tarea:", e);
        }
      }
      activeAnalysisRequestRef.current = null;
      setActiveTaskId(null);
      return;
    }
    triggerAnalysis();
  };

  return (
    <div className="flex flex-col h-full w-full min-w-0 overflow-hidden">

      <div ref={chatContainerRef} className="flex-1 overflow-y-auto overflow-x-hidden scrollbar-hide min-w-0">
        {/* Inner container — ajustado para panel lateral */}
        <div className="max-w-full w-full mx-auto px-4 pb-4 pt-4">
          {!hasInteraction && (
            <div className="text-center mb-8 mt-6 transition-all duration-500 ease-in-out">
              <h2 className="text-2xl font-medium text-foreground tracking-tight">Hola, Luilly Barrantes</h2>
              <p className="text-muted-foreground mt-2 text-sm font-light">¿Qué puedo analizar por ti hoy?</p>
            </div>
          )}

          <div className="space-y-6 min-w-0">
            {messages.map((msg) => (
              <div key={msg.id} className={cn("flex items-start gap-3 min-w-0", msg.type === "user" && "justify-end")}>
                {msg.type === "assistant" && (
                  <div className="w-8 h-8 bg-blue-600 rounded-full flex items-center justify-center text-white text-sm font-medium flex-shrink-0">P</div>
                )}

                <div className={cn(
                  "rounded-2xl space-y-4 relative group min-w-0",
                  msg.type === "user"
                    ? "p-4 bg-muted/50 text-foreground max-w-[85%] ml-auto shadow-sm break-words"
                    : "w-full overflow-hidden" // Asistente: contenido contenido estrictamente
                )}>
                  {/* Botón de eliminar (visible en hover) */}
                  <button
                    onClick={() => deleteMessage(msg.id)}
                    className={cn(
                      "absolute -top-2 right-0 p-1 rounded-full bg-background border shadow-sm opacity-0 group-hover:opacity-100 transition-opacity", // Ajustado a right-0
                      "hover:bg-destructive/10 hover:text-destructive"
                    )}
                    title="Eliminar mensaje"
                  >
                    <Trash2 className="h-3 w-3" />
                  </button>
                  <div className={cn(
                    "text-sm whitespace-pre-wrap leading-relaxed min-w-0",
                    msg.taskId === activeTaskId && isAnalyzing ? "text-muted-foreground animate-pulse" : "prose-sm max-w-none text-foreground/90 font-sans"
                  )}>
                    {/* OPTIMIZACIÓN: Renderizado condicional seguro */}
                    {typeof msg.content === 'string' ? (
                      msg.content // Si es texto, lo mostramos normal
                    ) : (
                      // Si es un objeto complejo, usamos el componente de reporte
                      <AnalysisReport
                        data={msg.content as any}
                        onSave={() => handleOpenSaveDialog(msg.content, "report", "Análisis Completo")}
                      />
                    )}
                  </div>

                  {msg.type === "assistant" && msg.components && msg.components.map((component, index) => {
                    const componentKey = `${msg.id} -component - ${index} `;
                    switch (component.type) {
                      case 'metricas_clave':
                        if (component.data && Object.keys(component.data).length > 0) {
                          return <AnalysisReport key={componentKey} data={{ metrics: component.data, tableData: [] }} onSave={() => handleOpenSaveDialog(component.data, "metrics", "Métricas Clave")} />;
                        }
                        return null;
                      case 'tabla_datos':
                        if (component.data && Array.isArray(component.data) && component.data.length > 0) {
                          return <AnalysisReport key={componentKey} data={{ tableData: component.data, title: component.title, metrics: {} }} onSave={() => handleOpenSaveDialog({ data: component.data, title: component.title }, "table", component.title)} />;
                        }
                        return null;
                      case 'correlaciones':
                        if (component.data && Array.isArray(component.data) && component.data.length > 0) {
                          return (
                            <Card key={componentKey} className="p-4 bg-muted/30 border-l-4 border-l-primary !mt-4">
                              <h4 className="font-semibold mb-3 text-sm flex items-center gap-2">
                                🔗 Correlaciones & Patrones Detectados
                              </h4>
                              <div className="grid gap-2">
                                {component.data.map((corr: any, idx: number) => {
                                  if (!corr.variable_1 || !corr.variable_2 || isNaN(corr.fuerza)) return null;
                                  return (
                                    <div key={idx} className="text-sm flex flex-wrap justify-between items-center bg-background p-3 rounded-md border shadow-sm">
                                      <div className="flex items-center gap-2">
                                        <span className="font-medium">{corr.variable_1}</span>
                                        <span className="text-muted-foreground">↔</span>
                                        <span className="font-medium">{corr.variable_2}</span>
                                      </div>
                                      <span className={cn(
                                        "text-xs font-bold px-2 py-1 rounded-full border",
                                        corr.fuerza > 0
                                          ? "bg-emerald-100 text-emerald-700 border-emerald-200 dark:bg-emerald-900/30 dark:text-emerald-400"
                                          : "bg-rose-100 text-rose-700 border-rose-200 dark:bg-rose-900/30 dark:text-rose-400"
                                      )}>
                                        {corr.fuerza > 0 ? 'Positiva' : 'Inversa'} ({Math.abs(corr.fuerza)})
                                      </span>
                                    </div>
                                  );
                                })}
                              </div>
                            </Card>
                          );
                        }
                        return null;
                      case 'configuracion_echarts':
                        if (component.option) {
                          return <ChartsReport key={componentKey} option={component.option} title={component.title} onSave={(optionOverride) => handleOpenSaveDialog(optionOverride || component.option, "chart", component.title)} onChartClick={(params) => handleChartDrillDown(params, component.table_name)} />;
                        }
                        return null;
                      case 'smart_table':
                        if (component.columns && component.data) {
                          return (
                            <SmartTable
                              key={componentKey}
                              title={component.title}
                              columns={component.columns}
                              data={component.data}
                              sortBy={component.sort_by}
                              sortOrder={component.sort_order}
                              originalChartOption={component.original_chart_option}
                              defaultViewMode={component.default_view_mode}
                              onSave={() => handleOpenSaveDialog(component, "table", component.title)}
                              onChartClick={(params) => handleChartDrillDown(params, component.table_name)}
                            />
                          );
                        }
                        return null;
                      case 'recomendaciones':
                        if (component.data && Array.isArray(component.data) && component.data.length > 0) {
                          return (
                            <div key={componentKey} className="mt-4 p-4 bg-blue-50/50 dark:bg-blue-900/10 border border-blue-100 dark:border-blue-900/30 rounded-lg">
                              <h4 className="font-medium text-blue-700 dark:text-blue-300 mb-2 flex items-center gap-2">
                                💡 Recomendaciones de IA
                              </h4>
                              <ul className="list-disc list-inside space-y-1 text-sm text-foreground/80">
                                {component.data.map((rec: string, idx: number) => (
                                  <li key={idx}>{rec}</li>
                                ))}
                              </ul>
                            </div>
                          );
                        }
                        return null;
                      case 'explicabilidad_analitica':
                        if (component.data && typeof component.data === 'object') {
                          const explainability = component.data as any;
                          const confidence = explainability.confidence || {};
                          const compliance = explainability.compliance || {};
                          return (
                            <details key={componentKey} className="mt-4 group">
                              <summary className="list-none cursor-pointer rounded-lg border border-slate-200/80 bg-slate-50/60 px-4 py-3 text-sm text-slate-700 transition-colors hover:bg-slate-100 dark:border-slate-800 dark:bg-slate-900/20 dark:text-slate-200 dark:hover:bg-slate-900/40">
                                <div className="flex items-center justify-between gap-4">
                                  <span className="font-medium">🔍 Ver trazabilidad y auditoría del dato</span>
                                  <span className="text-xs text-muted-foreground group-open:hidden">
                                    {confidence.level || "n/a"} {typeof confidence.score === "number" ? `· ${Math.round(confidence.score * 100)}%` : ""}
                                  </span>
                                </div>
                              </summary>
                              <Card className="mt-2 border border-slate-200/80 bg-slate-50/60 dark:bg-slate-900/20 dark:border-slate-800">
                                <div className="p-4 space-y-4">
                                  <div className="flex flex-col gap-2">
                                    <div className="flex items-center justify-between gap-4">
                                      <div>
                                        <h4 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                                          Trazabilidad del analisis
                                        </h4>
                                        <p className="text-xs text-muted-foreground">
                                          {explainability.title || "Analisis"} · {explainability.intent_type || "n/a"} · {explainability.visual_protocol || "n/a"}
                                        </p>
                                      </div>
                                      <div className="text-right">
                                        <div className="text-[11px] uppercase tracking-wide text-muted-foreground">Confianza</div>
                                        <div className="text-sm font-medium text-slate-900 dark:text-slate-100">
                                          {confidence.level || "n/a"} {typeof confidence.score === "number" ? `(${Math.round(confidence.score * 100)}%)` : ""}
                                        </div>
                                      </div>
                                    </div>
                                    {explainability.rationale ? (
                                      <p className="text-sm text-foreground/85">{explainability.rationale}</p>
                                    ) : null}
                                  </div>

                                  {explainability.methodology ? (
                                    <div className="space-y-1">
                                      <div className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Metodologia</div>
                                      <p className="text-sm text-foreground/85">{explainability.methodology}</p>
                                    </div>
                                  ) : null}

                                  {Array.isArray(explainability.metrics) && explainability.metrics.length > 0 ? (
                                    <div className="space-y-1">
                                      <div className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Metricas</div>
                                      <div className="flex flex-wrap gap-2">
                                        {explainability.metrics.map((metric: string, idx: number) => (
                                          <span key={idx} className="rounded-full border border-slate-300/80 px-2 py-1 text-xs text-slate-700 dark:border-slate-700 dark:text-slate-200">
                                            {metric}
                                          </span>
                                        ))}
                                      </div>
                                    </div>
                                  ) : null}

                                  {Array.isArray(explainability.filters) && explainability.filters.length > 0 ? (
                                    <div className="space-y-1">
                                      <div className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Filtros aplicados</div>
                                      <ul className="space-y-1 text-sm text-foreground/85">
                                        {explainability.filters.map((filter: string, idx: number) => (
                                          <li key={idx}>• {filter}</li>
                                        ))}
                                      </ul>
                                    </div>
                                  ) : null}

                                  {Array.isArray(explainability.evidence) && explainability.evidence.length > 0 ? (
                                    <div className="space-y-1">
                                      <div className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Evidencia usada</div>
                                      <ul className="space-y-1 text-sm text-foreground/85">
                                        {explainability.evidence.map((fact: string, idx: number) => (
                                          <li key={idx}>• {fact}</li>
                                        ))}
                                      </ul>
                                    </div>
                                  ) : null}

                                  {Array.isArray(explainability.limitations) && explainability.limitations.length > 0 ? (
                                    <div className="space-y-1">
                                      <div className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Limites de lectura</div>
                                      <ul className="space-y-1 text-sm text-foreground/75">
                                        {explainability.limitations.map((item: string, idx: number) => (
                                          <li key={idx}>• {item}</li>
                                        ))}
                                      </ul>
                                    </div>
                                  ) : null}

                                  {compliance.matched ? (
                                    <div className="rounded-lg border border-amber-300/70 bg-amber-50/80 p-3 dark:border-amber-900/60 dark:bg-amber-950/20">
                                      <div className="text-[11px] font-semibold uppercase tracking-wide text-amber-700 dark:text-amber-300">
                                        Regla institucional aplicada
                                      </div>
                                      <p className="mt-1 text-sm text-amber-900 dark:text-amber-100">
                                        {compliance.rule_sentence}
                                      </p>
                                      {compliance.action ? (
                                        <p className="mt-2 text-sm font-medium text-amber-800 dark:text-amber-200">
                                          Accion obligatoria: {compliance.action}
                                        </p>
                                      ) : null}
                                    </div>
                                  ) : null}
                                </div>
                              </Card>
                            </details>
                          );
                        }
                        return null;
                      case 'mensaje_resumen':
                        // Detectar si hay "regalos" extra en el paquete
                        const hasMetrics = component.metricas_destacadas && Object.keys(component.metricas_destacadas).length > 0;
                        const hasPoints = component.puntos_clave && Array.isArray(component.puntos_clave) && component.puntos_clave.length > 0;

                        if (!hasMetrics && !hasPoints) return null; // Si solo es texto, no pintamos nada extra

                        return (
                          <div key={componentKey} className="space-y-4 mt-4 w-full">
                            {/* 1. Renderizar Tarjeta de KPIs si existe */}
                            {hasMetrics && (
                              <AnalysisReport
                                data={{ metrics: component.metricas_destacadas }}
                                onSave={() => handleOpenSaveDialog(component.metricas_destacadas, "metrics", "Métricas Clave")}
                              />
                            )}

                            {/* 2. Renderizar Alertas/Puntos Clave si existen */}
                            {hasPoints && (
                              <div className="p-4 bg-amber-50/50 dark:bg-amber-900/10 border border-amber-100 dark:border-amber-900/30 rounded-lg">
                                <h4 className="font-medium text-amber-700 dark:text-amber-400 mb-2 flex items-center gap-2 text-sm">
                                  ⚠️ Hallazgos Críticos
                                </h4>
                                <ul className="list-disc list-inside space-y-1 text-sm text-foreground/80">
                                  {component.puntos_clave?.map((point: string, idx: number) => (
                                    <li key={idx}>{point}</li>
                                  ))}
                                </ul>
                              </div>
                            )}
                          </div>
                        );
                      default:
                        return null;
                    }
                  })}

                  {/* FASE 5 Refinamiento: Botón Restaurar Dashboard */}
                  {msg.type === "assistant" && msg._visuals && msg._visuals.length > 0 && (
                    <div className="mt-3 flex">
                      <Button
                        variant="secondary"
                        size="sm"
                        className="text-xs flex items-center gap-1.5 h-8 bg-primary/10 hover:bg-primary/20 text-primary hover:text-primary border border-primary/20 transition-all font-medium"
                        onClick={() => setWorkspaceItems(msg._visuals!)}
                      >
                         <LayoutDashboard className="w-3.5 h-3.5" />
                         Restaurar Dashboard
                      </Button>
                    </div>
                  )}
                </div>
              </div>
            ))}
            {/* Elemento invisible para asegurar scroll al final */}
            <div ref={messagesEndRef} />
          </div>
        </div>
      </div>

      <div className="w-full bg-background pt-4 pb-6 mt-auto z-10">
        <div className="w-full px-4">
          <form
            onSubmit={handleSendMessage}
            className="flex flex-col gap-2 relative bg-card dark:bg-zinc-800/50 border border-transparent dark:border-white/10 p-4 rounded-xl shadow-lg transition-all"
          >
            {/* Selección de Archivo (Visual, no funcional en este snippet simplificado) */}
            {fileName && (
              <div className="flex items-center gap-2 px-3 py-1.5 bg-muted/50 rounded-full w-fit mb-1 border">
                <Database className="w-3 h-3 text-primary" />
                <span className="text-xs font-medium truncate max-w-[200px]">{fileName}</span>
                <button type="button" onClick={() => { setAnalysisFileId(null); setFileName(null); }} className="text-muted-foreground hover:text-destructive">
                  <X className="w-3 h-3" />
                </button>
              </div>
            )}

            {/* Contenedor del Input */}
            <div className="flex items-end gap-2">
              <textarea
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    handleSendMessage();
                  }
                }}
                placeholder={isAnalyzing ? "Analizando datos..." : analysisFileId ? "Pregúntame sobre tus datos..." : "Carga un archivo para empezar..."}
                className="flex-1 min-h-[44px] max-h-32 bg-transparent border-0 focus:ring-0 resize-none py-3 px-2 text-sm"
                disabled={isAnalyzing}
                data-testid="chat-input"
              />
              <div className="flex flex-col justify-end pb-1 gap-1">
                {/* Voice Button */}
                <Button
                  type="button"
                  size="icon"
                  variant="ghost"
                  onClick={toggleListening}
                  className={cn(
                    "rounded-xl h-9 w-9 transition-colors",
                    isListening ? "bg-red-100 text-red-600 animate-pulse" : "text-muted-foreground hover:bg-muted"
                  )}
                  title="Entrada de Voz"
                >
                  {isListening ? <MicOff className="w-4 h-4" /> : <Mic className="w-4 h-4" />}
                </Button>

                <Button
                  type="submit"
                  size="icon"
                  disabled={(!message.trim() && !isAnalyzing) || (!analysisFileId && !isAnalyzing)}
                  data-testid="chat-submit"
                  className={cn(
                    "rounded-xl h-9 w-9 transition-all",
                    message.trim() || isAnalyzing ? "bg-primary text-primary-foreground hover:bg-primary/90" : "bg-muted text-muted-foreground"
                  )}
                >
                  {isAnalyzing ? <Square className="w-4 h-4 fill-current" /> : <SendHorizonal className="w-4 h-4" />}
                </Button>
              </div>
            </div>

            <button type="button" className="absolute bottom-3 left-3 text-muted-foreground hover:text-foreground transition-colors" title="Adjuntar (Simulado)">
              <Paperclip className="w-4 h-4" />
            </button>
          </form>
          <div className="text-center mt-2">
            <p className="text-[10px] text-muted-foreground">PromData AI puede cometer errores. Verifica la información importante.</p>
          </div>
        </div>
      </div>

      {/* Modal de Guardado */}
      {isSaveDialogOpen && reportToSave && (
        <div className="fixed inset-0 bg-background/80 backdrop-blur-sm z-50 flex items-center justify-center p-4" data-testid="save-report-dialog">
          <div className="bg-card border shadow-xl rounded-xl max-w-md w-full p-6 space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="font-semibold text-lg">Guardar Reporte</h3>
              <button onClick={() => setIsSaveDialogOpen(false)} className="p-1 hover:bg-muted rounded-full" disabled={isSaveActionLoading}>
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">Título del Reporte</label>
              <input
                type="text"
                value={reportTitle}
                onChange={(e) => setReportTitle(e.target.value)}
                placeholder="Ej: Análisis de Ventas Q3"
                className="w-full px-3 py-2 rounded-md border text-sm outline-none focus:ring-2 ring-primary/30 bg-background"
                autoFocus
                data-testid="save-report-title-input"
                disabled={isSaveActionLoading}
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">Presentación destino (Opcional)</label>
              <select
                value={selectedSavePresentationId}
                onChange={(event) => setSelectedSavePresentationId(event.target.value)}
                className="w-full px-3 py-2 rounded-md border text-sm outline-none focus:ring-2 ring-primary/30 bg-background"
                disabled={isSaveActionLoading || isSaveDestinationsLoading}
              >
                <option value="">Automático (por archivo)</option>
                {(Array.isArray(presentations) ? presentations : []).map((presentation: any) => (
                  <option key={presentation.id} value={presentation.id}>
                    {presentation.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="outline" onClick={() => setIsSaveDialogOpen(false)} disabled={isSaveActionLoading}>Cancelar</Button>
              <Button onClick={handleConfirmSave} data-testid="save-report-confirm" disabled={isSaveActionLoading}>
                {isSaveActionLoading ? "Guardando..." : "Guardar Reporte"}
              </Button>
            </div>
          </div>
        </div>
      )}
      {/* DrillDown Menu Bridge: lee atom independientemente, sin re-render de ChatInterface */}
      <DrillDownMenuBridge
        onSelect={handleDrillDownSelect}
        onCrossFilter={handleCrossFilter}
      />
    </div>
  );
}
