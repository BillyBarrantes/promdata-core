// En: PromData/components/chat-interface.tsx
"use client"

import type React from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { useState, useRef, useEffect, useCallback, memo, startTransition } from "react"
import { useAtom, useSetAtom, useAtomValue } from "jotai"
import { duckdbReadyAtom, drillDownAtom, workspaceItemsAtom, workspaceRenderStateAtom, AnalysisComponent } from "@/lib/state"
import { DrillDownBridge } from "@/components/chat/drill-down-bridge"
import * as duckdbEngine from "@/lib/duckdb-engine"
import { API_BASE_URL } from "@/lib/api-config"
import { toast } from "sonner"
import { useSupabase } from '@/lib/supabase-provider'
import { SaveReportDialog } from "@/components/chat/save-report-dialog"
import { ChatInputBox } from "@/components/chat/chat-input-box"
import { ChatMessageList } from "@/components/chat/chat-message-list"
import { getAccessToken } from "@/components/auth-helpers"
import { useVoiceRecognition } from "@/hooks/useVoiceRecognition"
import { useChatFilterHandler } from "@/hooks/useChatFilterHandler"
import { useWorkspaceVisualStaging } from "@/hooks/useWorkspaceVisualStaging"
import { useChatTaskPipeline } from "@/hooks/useChatTaskPipeline"
import {
  type ChatMessage,
  VISUAL_COMPONENT_TYPES,
  extractRawChartCategory,
} from "@/lib/chat-utils"

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

  // Voice State (hook desacoplado)
  const { isListening, toggleListening } = useVoiceRecognition((transcript) => {
    setMessage((prev) => prev + (prev ? " " : "") + transcript);
  });

  // DrillDown State: useSetAtom = write-only, NO re-render al abrir/cerrar menu
  const setDrillDown = useSetAtom(drillDownAtom);

  // 🦆 [FASE 4] DuckDB-WASM State
  const [isDuckDBReady, setIsDuckDBReady] = useAtom(duckdbReadyAtom);

  // 🧠 [FASE 5] Global State para Workspace
  const setWorkspaceItems = useSetAtom(workspaceItemsAtom);
  const setWorkspaceRenderState = useSetAtom(workspaceRenderStateAtom);
  const workspaceItems = useAtomValue(workspaceItemsAtom);


  // Last completed task for context
  const [lastCompletedTaskId, setLastCompletedTaskId] = useState<string | null>(null);

  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Estados para el modal de guardar reporte
  const [isSaveDialogOpen, setIsSaveDialogOpen] = useState(false);
  const [reportToSave, setReportToSave] = useState<any>(null);
  const [defaultReportTitle, setDefaultReportTitle] = useState("");
  const [userName, setUserName] = useState<string>("");

  useEffect(() => {
    let isMounted = true;

    const resolveUserName = (user: any): string => {
      const candidates = [
        user?.user_metadata?.full_name,
        user?.user_metadata?.name,
        user?.user_metadata?.preferred_username,
        user?.email?.split("@")[0],
      ];
      for (const candidate of candidates) {
        if (typeof candidate === "string" && candidate.trim()) {
          return candidate.trim();
        }
      }
      return "";
    };

    const fetchUser = async () => {
      try {
        const { data: { session } } = await supabase.auth.getSession();
        if (isMounted && session?.user) {
          const resolved = resolveUserName(session.user);
          if (resolved) setUserName(resolved);
        }
      } catch (err) {
        console.error("Error retrieving user for chat greeting:", err);
      }
    };

    void fetchUser();

    const { data: { subscription } } = supabase.auth.onAuthStateChange((_event: string, session: any) => {
      if (!isMounted) return;
      const resolved = resolveUserName(session?.user);
      if (resolved) setUserName(resolved);
    });

    return () => {
      isMounted = false;
      subscription?.unsubscribe();
    };
  }, [supabase]);

  const getChatAccessToken = useCallback(
    () => getAccessToken(supabase, "chat"),
    [supabase]
  );

  // 🧠 [FASE 5] Staging progresivo de visuales y pre-carga Arrow en DuckDB
  const {
    stageWorkspaceVisuals,
    clearWorkspaceStageTimer,
    workspaceVisualsRef,
    workspacePreloadPromiseRef,
  } = useWorkspaceVisualStaging({
    setWorkspaceItems,
    setWorkspaceRenderState,
    setIsDuckDBReady,
  });

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

  // --- DELETE: Delete message from history ---
  const deleteMessage = useCallback(async (messageId: string) => {
    try {
      const accessToken = await getChatAccessToken();
      if (!accessToken) return;

      const res = await fetch(`${API_BASE_URL}/api/v1/chat/messages/${messageId}`, {
        method: 'DELETE',
        headers: { 'Authorization': `Bearer ${accessToken}` }
      });

      if (res.ok || res.status === 404) {
        const deletedMsg = messages.find(m => m.id === messageId);
        const nextMessages = messages.filter(msg => msg.id !== messageId);
        setMessages(nextMessages);
        if (deletedMsg?._visuals && deletedMsg._visuals.length > 0) {
          const lastWithVisuals = [...nextMessages].reverse().find(
            m => m.type === 'assistant' && m._visuals && m._visuals.length > 0
          );
          if (lastWithVisuals?._visuals) {
            stageWorkspaceVisuals(lastWithVisuals._visuals);
          } else {
            setWorkspaceItems([]);
            setWorkspaceRenderState({
              status: "idle",
              message: null,
              pendingVisuals: 0,
              renderedVisuals: 0,
            });
          }
        }
        if (res.ok) toast.success("Mensaje eliminado");
      } else {
        throw new Error(`Error al eliminar: ${res.status}`);
      }
    } catch (e) {
      console.error("Error eliminando mensaje:", e);
      toast.error("No se pudo eliminar el mensaje");
    }
  }, [getChatAccessToken, messages, stageWorkspaceVisuals, setWorkspaceItems, setWorkspaceRenderState]);

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

  const handleOpenSaveDialog = useCallback((data: any, type: string, defaultTitle?: string) => {
    setReportToSave({ type, content: data });
    setDefaultReportTitle(defaultTitle || "");
    setIsSaveDialogOpen(true);
  }, []);

  // --- EFFECT: Load initial file data and history ---
  useEffect(() => {
    const fileIdFromUrl = searchParams.get('fileId');
    if (fileIdFromUrl && fileIdFromUrl !== analysisFileId) {
      setAnalysisFileId(fileIdFromUrl);
      setWorkspaceItems([]);

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
                visualComponents = chatComponents
                  .filter((c: any) => VISUAL_COMPONENT_TYPES.includes(c.type))
                  .map((c: any) => ({
                    ...c,
                    task_id: c.task_id || msg.id || undefined,
                    file_id: c.file_id || analysisFileId || undefined,
                  }));
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
              setWorkspaceItems([]);
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
            setWorkspaceItems([]);
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
        setWorkspaceItems([]);
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

  // 🚀 [FASE A.5] Pipeline de Tareas Desacoplado (SSE + Polling + Trigger + Stop)
  const { triggerAnalysis, stopAnalysis } = useChatTaskPipeline({
    analysisFileId,
    message,
    setMessage,
    isAnalyzing,
    setIsAnalyzing,
    activeTaskId,
    setActiveTaskId,
    lastCompletedTaskId,
    setLastCompletedTaskId,
    setMessages,
    setWorkspaceItems,
    setWorkspaceRenderState,
    stageWorkspaceVisuals,
    clearWorkspaceStageTimer,
    saveMessageToBackend,
    getChatAccessToken,
    setIsDuckDBReady,
    processedTasksRef,
  });

  // 🔒 Ref estable para isAnalyzing — evita recrear handleChartDrillDown en cada cambio
  const isAnalyzingRef = useRef(isAnalyzing);
  isAnalyzingRef.current = isAnalyzing;

  const handleChartDrillDown = useCallback((params: any, tableName?: string, option?: any) => {
    // Leer via ref para no depender del closure
    if (isAnalyzingRef.current) return;

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
          option: option,
        }
      });
    }
  }, [setDrillDown]);

  const handleDrillDownSelect = useCallback((prompt: string) => {
    setDrillDown(prev => ({ ...prev, isVisible: false }));
    triggerAnalysis(prompt);
  }, [triggerAnalysis, setDrillDown]);

  // 🦆 [FASE A.4] Handler de Cross-Filter Desacoplado (DuckDB-WASM local)
  const { handleCrossFilter } = useChatFilterHandler({
    workspacePreloadPromiseRef,
    workspaceVisualsRef,
    workspaceItems,
    messages,
    analysisFileId,
    lastCompletedTaskId,
    activeTaskId,
    getChatAccessToken,
    setIsDuckDBReady,
    setWorkspaceItems,
    setMessages,
  });

  const handleSendMessage = async (e?: React.FormEvent) => {
    if (e?.preventDefault) e.preventDefault();
    if (isAnalyzing) {
      await stopAnalysis();
      return;
    }
    setWorkspaceItems([]);
    await triggerAnalysis();
  };

  return (
    <div className="flex flex-col h-full w-full min-w-0 overflow-hidden">

      <div ref={chatContainerRef} className="flex-1 overflow-y-auto overflow-x-hidden scrollbar-hide min-w-0">
        {/* Inner container — ajustado para panel lateral */}
        <div className="max-w-full w-full mx-auto px-4 pb-4 pt-4">
          {!hasInteraction && (
            <div className="text-center mb-8 mt-6 transition-all duration-500 ease-in-out">
              <h2 className="text-2xl text-foreground tracking-tight">
                {userName ? `Hola, ${userName}` : "Bienvenido"}
              </h2>
              {fileName && (
                <p className="text-xs text-muted-foreground/70 mt-1 font-mono truncate max-w-xs mx-auto" title={fileName}>
                  Analizando: {fileName}
                </p>
              )}
              <p className="text-muted-foreground mt-2 text-sm font-light">¿Qué puedo analizar por ti hoy?</p>
            </div>
          )}

          {/* Renderizado de Mensajes Desacoplado [FASE A.3] */}
          <ChatMessageList
            messages={messages}
            activeTaskId={activeTaskId}
            isAnalyzing={isAnalyzing}
            onDeleteMessage={deleteMessage}
            onOpenSaveDialog={handleOpenSaveDialog}
            onChartDrillDown={handleChartDrillDown}
            onRestoreDashboard={setWorkspaceItems}
            messagesEndRef={messagesEndRef}
          />
        </div>
      </div>

      {/* Barra de Entrada Desacoplada [FASE A.2] */}
      <ChatInputBox
        message={message}
        setMessage={setMessage}
        isAnalyzing={isAnalyzing}
        analysisFileId={analysisFileId}
        fileName={fileName}
        onClearFile={() => {
          setAnalysisFileId(null);
          setFileName(null);
          setWorkspaceItems([]);
        }}
        onSubmit={handleSendMessage}
        isListening={isListening}
        onToggleListening={toggleListening}
      />

      {/* Modal de Guardado Desacoplado [FASE A.1] */}
      <SaveReportDialog
        isOpen={isSaveDialogOpen}
        onClose={() => setIsSaveDialogOpen(false)}
        reportToSave={reportToSave}
        defaultTitle={defaultReportTitle}
        fileId={analysisFileId}
        getAccessToken={getChatAccessToken}
      />
      {/* DrillDown Menu Bridge: lee atom independientemente, sin re-render de ChatInterface */}
      <DrillDownBridge
        onSelect={handleDrillDownSelect}
        onCrossFilter={handleCrossFilter}
      />
    </div>
  );
}
