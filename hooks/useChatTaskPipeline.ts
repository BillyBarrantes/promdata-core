"use client"

import { useState, useRef, useEffect, useCallback } from "react"
import { toast } from "sonner"
import { fetchEventSource } from "@microsoft/fetch-event-source"
import { API_BASE_URL } from "@/lib/api-config"
import * as duckdbEngine from "@/lib/duckdb-engine"
import { tryParseArrow } from "@/lib/arrow-parser"
import {
  VISUAL_COMPONENT_TYPES,
  buildAnalysisTableName,
  type ChatMessage,
} from "@/lib/chat-utils"
import type { AnalysisComponent } from "@/lib/state"

// Polling and deadline constants
const POLLING_IMMEDIATE_DELAY_MS = 0
const POLLING_INITIAL_INTERVAL_MS = 1500
const POLLING_MAX_INTERVAL_MS = 4000
const POLLING_BACKOFF_STEP_MS = 500
const POLLING_ERROR_RETRY_MS = 1500
const POLLING_REQUEST_TIMEOUT_MS = 5000
const ANALYSIS_COMPLETION_DEADLINE_MS = 180_000
const MAX_POLL_RETRIES = 90
const MAX_CONSECUTIVE_SERVER_ERRORS = 5

const getAdaptivePollDelay = (startedAt: number | null): number => {
  const elapsed = Date.now() - (startedAt || Date.now())
  const ageSec = elapsed / 1000
  if (ageSec < 5) {
    return POLLING_INITIAL_INTERVAL_MS
  } else if (ageSec < 15) {
    return POLLING_INITIAL_INTERVAL_MS + POLLING_BACKOFF_STEP_MS
  } else if (ageSec < 30) {
    return POLLING_INITIAL_INTERVAL_MS + POLLING_BACKOFF_STEP_MS * 2
  } else {
    return POLLING_MAX_INTERVAL_MS
  }
}

const normalizeAnalysisPromptKey = (value: string): string => {
  return value.trim().replace(/\s+/g, " ").toLowerCase()
}

const buildAnalysisRequestKey = (
  fileId: string,
  prompt: string,
  parentTaskId: string | null
): string => {
  return [fileId, normalizeAnalysisPromptKey(prompt), parentTaskId || "root"].join("::")
}

const parseTaskResultPayload = (value: any): any => {
  if (typeof value !== "string") return value
  try {
    return JSON.parse(value)
  } catch {
    return value
  }
}

const normalizeTaskStatusPayload = (payload: any): { status: string; result: any } => {
  const source = payload && typeof payload === "object" ? payload : {}
  const nestedData = source.data && typeof source.data === "object" ? source.data : {}
  const rawStatus =
    source.status ?? nestedData.status ?? source.task_status ?? nestedData.task_status ?? ""
  const result =
    source.result ??
    source.results_json ??
    source.final_struct ??
    nestedData.result ??
    nestedData.results_json ??
    nestedData.final_struct ??
    null

  const parsedResult = parseTaskResultPayload(result)
  const hasMaterializedResult =
    parsedResult !== null &&
    parsedResult !== undefined &&
    !(
      typeof parsedResult === "object" &&
      !Array.isArray(parsedResult) &&
      Object.keys(parsedResult).length === 0
    )

  return {
    ...source,
    status: String(rawStatus || (hasMaterializedResult ? "completed" : "")).toLowerCase(),
    result: parsedResult,
  }
}

export interface UseChatTaskPipelineDeps {
  analysisFileId: string | null
  message: string
  setMessage: React.Dispatch<React.SetStateAction<string>>
  isAnalyzing: boolean
  setIsAnalyzing: React.Dispatch<React.SetStateAction<boolean>>
  activeTaskId: string | null
  setActiveTaskId: React.Dispatch<React.SetStateAction<string | null>>
  lastCompletedTaskId: string | null
  setLastCompletedTaskId: React.Dispatch<React.SetStateAction<string | null>>
  setMessages: React.Dispatch<React.SetStateAction<ChatMessage[]>>
  setWorkspaceItems: (visuals: AnalysisComponent[]) => void
  setWorkspaceRenderState: (state: any) => void
  stageWorkspaceVisuals: (visuals: AnalysisComponent[]) => void
  clearWorkspaceStageTimer: () => void
  saveMessageToBackend: (role: "user" | "assistant", content: any) => Promise<void>
  getChatAccessToken: () => Promise<string | null>
  setIsDuckDBReady: (ready: boolean) => void
  processedTasksRef: React.MutableRefObject<Set<string>>
}

export function useChatTaskPipeline({
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
}: UseChatTaskPipelineDeps) {
  const [pollingFallbackEnabled, setPollingFallbackEnabled] = useState(false)
  const pendingTaskResultRef = useRef<{ taskId: string; data: any } | null>(null)
  const pollingTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const pollingAbortRef = useRef<AbortController | null>(null)
  const pollRetryCountRef = useRef(0)
  const pollingInFlightRef = useRef(false)
  const analysisStartedAtRef = useRef(0)
  const activeAnalysisRequestRef = useRef<{ key: string; taskId: string | null } | null>(null)

  // --- EFFECT: SSE primary path + polling fallback ---
  useEffect(() => {
    if (!isAnalyzing || !activeTaskId) return

    pendingTaskResultRef.current = null
    setPollingFallbackEnabled(false)

    const sseCtrl = new AbortController()
    let sseOpened = false
    let sseFallbackTriggered = false

    const fallbackToPolling = (): void => {
      if (sseFallbackTriggered) return
      sseFallbackTriggered = true
      sseCtrl.abort()
      setPollingFallbackEnabled(true)
    }

    const elapsedMs = Math.max(Date.now() - analysisStartedAtRef.current, 0)
    const deadlineDelayMs = Math.max(ANALYSIS_COMPLETION_DEADLINE_MS - elapsedMs, 0)
    const deadlineGuard = window.setTimeout(() => {
      console.warn("⚠️ [SSE] Deadline de análisis alcanzado; verificando estado final por polling.")
      fallbackToPolling()
    }, deadlineDelayMs)

    const connectGuard = window.setTimeout(() => {
      if (!sseOpened) {
        console.warn("⚠️ [SSE] No conectó a tiempo; fallback a polling.")
        fallbackToPolling()
      }
    }, 6000)

    ;(async () => {
      const token = await getChatAccessToken()
      if (!token || sseFallbackTriggered) {
        if (!token) fallbackToPolling()
        return
      }

      try {
        await fetchEventSource(`${API_BASE_URL}/api/v1/tasks/${activeTaskId}/stream`, {
          method: "GET",
          headers: { Authorization: `Bearer ${token}` },
          signal: sseCtrl.signal,
          async onopen(response) {
            if (response.ok) {
              sseOpened = true
              window.clearTimeout(connectGuard)
            } else if (response.status === 401 || response.status === 404) {
              throw new Error(`SSE rejected: ${response.status}`)
            } else {
              throw new Error(`SSE failed: ${response.status}`)
            }
          },
          onmessage(event) {
            if (sseFallbackTriggered) return
            let payload: any = null
            try {
              payload = JSON.parse(event.data)
            } catch {
              return
            }

            const status = String(payload?.status || "").toLowerCase()
            if (!["completed", "success", "failed", "timeout", "rate_limited"].includes(status)) {
              return
            }

            window.clearTimeout(connectGuard)
            sseFallbackTriggered = true
            sseCtrl.abort()

            ;(async () => {
              try {
                const pollToken = await getChatAccessToken()
                const response = await fetch(`${API_BASE_URL}/api/v1/tasks/${activeTaskId}`, {
                  cache: "no-store",
                  headers: pollToken ? { Authorization: `Bearer ${pollToken}` } : undefined,
                })
                if (response.ok) {
                  const fetchData = await response.json()
                  pendingTaskResultRef.current = {
                    taskId: activeTaskId,
                    data: fetchData,
                  }
                }
              } catch (error) {
                console.warn(
                  "⚠️ [SSE] No se pudo resolver resultado final; fallback a polling.",
                  error
                )
              } finally {
                setPollingFallbackEnabled(true)
              }
            })()
          },
          onclose() {
            if (!sseFallbackTriggered) {
              console.warn("⚠️ [SSE] Conexión cerrada por el servidor; fallback a polling.")
              fallbackToPolling()
            }
          },
          onerror(err) {
            console.warn("⚠️ [SSE] Error; fallback a polling.", err)
            fallbackToPolling()
            throw err
          },
        })
      } catch {
        // fetchEventSource threw — already handled in onerror/fallback
      }
    })()

    return () => {
      window.clearTimeout(connectGuard)
      window.clearTimeout(deadlineGuard)
      sseFallbackTriggered = true
      sseCtrl.abort()
    }
  }, [isAnalyzing, activeTaskId, getChatAccessToken])

  // --- EFFECT: Polling fallback ---
  useEffect(() => {
    if (!isAnalyzing || !activeTaskId || !pollingFallbackEnabled) return
    let cancelled = false
    let consecutiveServerErrors = 0

    const clearPollingState = () => {
      if (pollingTimerRef.current) {
        clearTimeout(pollingTimerRef.current)
        pollingTimerRef.current = null
      }
      if (pollingAbortRef.current) {
        pollingAbortRef.current.abort()
        pollingAbortRef.current = null
      }
      pollingInFlightRef.current = false
    }

    const hasAnalysisDeadlineElapsed = (): boolean =>
      Date.now() - analysisStartedAtRef.current >= ANALYSIS_COMPLETION_DEADLINE_MS

    const resolveClientTimeout = (): void => {
      clearPollingState()
      pollRetryCountRef.current = 0
      analysisStartedAtRef.current = 0
      pendingTaskResultRef.current = null
      activeAnalysisRequestRef.current = null
      setMessages((prevMessages) =>
        prevMessages.map((msg) =>
          msg.id === activeTaskId
            ? {
                ...msg,
                content:
                  "⏱️ El análisis superó el tiempo de espera. Intenta de nuevo o simplifica la consulta.",
              }
            : msg
        )
      )
      setWorkspaceRenderState({
        status: "idle",
        message: null,
        pendingVisuals: 0,
        renderedVisuals: 0,
      })
      setPollingFallbackEnabled(false)
      setActiveTaskId(null)
      setIsAnalyzing(false)
      toast.error("El análisis superó el tiempo de espera.")
    }

    const resolveServerDeliveryError = (): void => {
      clearPollingState()
      pollRetryCountRef.current = 0
      analysisStartedAtRef.current = 0
      pendingTaskResultRef.current = null
      activeAnalysisRequestRef.current = null
      setMessages((prevMessages) =>
        prevMessages.map((msg) =>
          msg.id === activeTaskId
            ? {
                ...msg,
                content:
                  "⚠️ El servidor no pudo entregar el resultado del análisis. Vuelve a intentarlo en unos minutos.",
              }
            : msg
        )
      )
      setWorkspaceRenderState({
        status: "idle",
        message: null,
        pendingVisuals: 0,
        renderedVisuals: 0,
      })
      setPollingFallbackEnabled(false)
      setActiveTaskId(null)
      setIsAnalyzing(false)
      toast.error("El servidor no pudo entregar el resultado del análisis.")
    }

    const scheduleNextPoll = (delayMs: number) => {
      if (cancelled) return
      if (pollingTimerRef.current) {
        clearTimeout(pollingTimerRef.current)
      }
      pollingTimerRef.current = setTimeout(runPoll, delayMs)
    }

    const runPoll = async () => {
      if (cancelled) return
      pollRetryCountRef.current++
      if (pollingInFlightRef.current) {
        scheduleNextPoll(getAdaptivePollDelay(analysisStartedAtRef.current))
        return
      }

      pollingInFlightRef.current = true
      const controller = new AbortController()
      const requestTimeout = setTimeout(() => controller.abort(), POLLING_REQUEST_TIMEOUT_MS)
      pollingAbortRef.current = controller

      try {
        let data: any = null
        if (pendingTaskResultRef.current?.taskId === activeTaskId) {
          data = pendingTaskResultRef.current.data
          pendingTaskResultRef.current = null
        } else {
          const pollToken = await getChatAccessToken()
          const response = await fetch(`${API_BASE_URL}/api/v1/tasks/${activeTaskId}`, {
            signal: controller.signal,
            cache: "no-store",
            headers: pollToken ? { Authorization: `Bearer ${pollToken}` } : undefined,
          })
          if (!response.ok) {
            if (response.status >= 500) {
              consecutiveServerErrors++
            } else {
              consecutiveServerErrors = 0
            }
            // Un 5xx persistente significa que el backend terminó el análisis
            // pero no puede entregar el resultado (p.ej. serialización). Se
            // reporta como error de servidor en segundos, en vez de agotar el
            // deadline de 180s con un falso "tiempo de espera".
            if (consecutiveServerErrors >= MAX_CONSECUTIVE_SERVER_ERRORS) {
              console.error(
                "⛔ [Polling] El servidor devolvió errores 5xx consecutivos; abortando polling."
              )
              resolveServerDeliveryError()
              return
            }
            if (hasAnalysisDeadlineElapsed() || pollRetryCountRef.current >= MAX_POLL_RETRIES) {
              console.error("⛔ [Polling] No fue posible confirmar el estado antes del deadline.")
              resolveClientTimeout()
              return
            }
            scheduleNextPoll(getAdaptivePollDelay(analysisStartedAtRef.current))
            return
          }

          consecutiveServerErrors = 0
          data = await response.json()
          if (cancelled) return
        }

        data = normalizeTaskStatusPayload(data)
        const resolvedStatus = data.status
        if (
          resolvedStatus === "completed" ||
          resolvedStatus === "success" ||
          resolvedStatus === "failed" ||
          resolvedStatus === "timeout" ||
          resolvedStatus === "rate_limited"
        ) {
          clearPollingState()
          pollRetryCountRef.current = 0
          analysisStartedAtRef.current = 0
          data.status = resolvedStatus === "success" ? "completed" : resolvedStatus

          const componentsList: AnalysisComponent[] = []

          // Nueva Lógica de Parsing Híbrido (V2)
          if (Array.isArray(data.result)) {
            componentsList.push(...data.result)
          } else if (typeof data.result === "object" && data.result !== null) {
            // [F1] Procedencia temporal: metadatos aditivos. No alteran
            // chart_base_filters, Arrow ni Grid; solo describen el linaje.
            const temporalProvenance = (() => {
              const traceability = data.result?.traceability;
              const traces = Array.isArray(traceability?.temporal_filters)
                ? traceability.temporal_filters
                : [];
              if (traces.length === 0) return null;
              const first = traces[0] || {};
              const itemCount = traces.reduce(
                (total: number, trace: any) =>
                  total + (Array.isArray(trace?.items) ? trace.items.length : 0),
                0
              );
              return {
                cut_applied: Boolean(
                  traceability?.temporal_cut_applied ?? first.cut_applied
                ),
                authority: String(first.authority || "unknown"),
                item_count: itemCount,
              };
            })();

            if (data.result.analysis) {
              componentsList.push({
                type: "mensaje_resumen",
                content: data.result.analysis,
                texto: data.result.analysis,
              })
            }

            if (
              data.result.metrics &&
              typeof data.result.metrics === "object" &&
              Object.keys(data.result.metrics).length > 0
            ) {
              componentsList.push({
                type: "metricas_clave",
                data: data.result.metrics,
                title: "Métricas Clave",
              })
            }

            if (Array.isArray(data.result.chart_options) && data.result.chart_options.length > 0) {
              data.result.chart_options.forEach((opt: any, index: number) => {
                // [F1] Adjuntar procedencia temporal al chart option (aditivo).
                if (temporalProvenance) {
                  opt.temporal_provenance = temporalProvenance;
                }
                if (opt.type === "smart_table") {
                  if (opt.arrow_data) {
                    opt.data = tryParseArrow(opt.arrow_data, opt.data)
                  }

                  const chartTableName = buildAnalysisTableName(
                    analysisFileId,
                    "smart_table",
                    opt.id || opt.title || index
                  )
                  const smartTableArrow = opt.granular_arrow || opt.arrow_data || null
                  if (smartTableArrow) {
                    void duckdbEngine
                      .preloadArrowTables(
                        [{ tableName: chartTableName, base64Data: smartTableArrow, priority: 1 }],
                        0
                      )
                      .then(() => setIsDuckDBReady(true))
                      .catch((err: any) => console.warn("⚠️ [DuckDB] Smart Table no cargada:", err))
                  }

                  opt.table_name = chartTableName
                  componentsList.push(opt)
                } else {
                  let chartTableName = undefined
                  const targetArrow = opt.granular_arrow || opt.arrow_data
                  if (targetArrow) {
                    chartTableName = buildAnalysisTableName(
                      analysisFileId,
                      "chart",
                      opt.id || opt.title?.text || index
                    )
                    void duckdbEngine
                      .preloadArrowTables(
                        [{ tableName: chartTableName, base64Data: targetArrow, priority: 0 }],
                        0
                      )
                      .then(() => setIsDuckDBReady(true))
                      .catch((err: any) => console.warn("⚠️ [DuckDB] Chart Arrow no cargado:", err))
                  } else {
                    const topLevelArrow = data.result?.arrow_data
                    if (topLevelArrow) {
                      chartTableName = buildAnalysisTableName(
                        analysisFileId,
                        "analysis",
                        "detail"
                      )
                    }
                  }

                  componentsList.push({
                    type: "configuracion_echarts",
                    option: opt,
                    table_name: chartTableName,
                    title: opt.title?.text || "Análisis Visual",
                  })
                }
              })
            } else if (
              data.result.chart_options &&
              typeof data.result.chart_options === "object" &&
              Object.keys(data.result.chart_options).length > 0
            ) {
              componentsList.push({
                type: "configuracion_echarts",
                option: temporalProvenance
                  ? { ...data.result.chart_options, temporal_provenance: temporalProvenance }
                  : data.result.chart_options,
                title: data.result.chart_options.title?.text || "Análisis Visual",
              })
            }

            const resolvedTableData = data.result.arrow_data
              ? tryParseArrow(data.result.arrow_data, data.result.data)
              : data.result.data

            if (data.result.arrow_data) {
              duckdbEngine
                .preloadArrowTables(
                  [
                    {
                      tableName: buildAnalysisTableName(analysisFileId, "analysis", "detail"),
                      base64Data: data.result.arrow_data,
                      priority: 2,
                    },
                  ],
                  0
                )
                .then(() => setIsDuckDBReady(true))
                .catch((err: any) => console.warn("⚠️ [DuckDB] Tabla datos no cargada:", err))
            }

            if (resolvedTableData && Array.isArray(resolvedTableData) && resolvedTableData.length > 0) {
              componentsList.push({
                type: "tabla_datos",
                data: resolvedTableData,
                title: "Datos Detallados",
              })
            }

            if (
              data.result.recommendations &&
              Array.isArray(data.result.recommendations) &&
              data.result.recommendations.length > 0
            ) {
              componentsList.push({
                type: "recomendaciones",
                data: data.result.recommendations,
              })
            }

            if (
              data.result.explainability &&
              Array.isArray(data.result.explainability) &&
              data.result.explainability.length > 0
            ) {
              data.result.explainability.forEach((item: any) => {
                if (item && typeof item === "object") {
                  componentsList.push({
                    type: "explicabilidad_analitica",
                    data: item,
                  })
                }
              })
            }

            if (data.result.snapshot_arrow) {
              duckdbEngine
                .preloadArrowTables(
                  [
                    {
                      tableName: buildAnalysisTableName(analysisFileId, "snapshot", "latest"),
                      base64Data: data.result.snapshot_arrow,
                      priority: 0,
                    },
                  ],
                  0
                )
                .then(() => {
                  setIsDuckDBReady(true)
                })
                .catch((err: any) => console.warn("⚠️ [DuckDB] Snapshot no cargado:", err))
            }
          }

          const summaryComponent = componentsList.find((c) => c.type === "mensaje_resumen")
          let finalContent = "El análisis ha finalizado."

          if (summaryComponent) {
            if (typeof summaryComponent.texto === "string") finalContent = summaryComponent.texto
            else if (typeof summaryComponent.content === "string")
              finalContent = summaryComponent.content
          }

          const visualComponents = componentsList.filter((c) =>
            VISUAL_COMPONENT_TYPES.includes(c.type as (typeof VISUAL_COMPONENT_TYPES)[number])
          )
          const chatComponents = componentsList.filter(
            (c) => !VISUAL_COMPONENT_TYPES.includes(c.type as (typeof VISUAL_COMPONENT_TYPES)[number])
          )

          if (visualComponents.length > 0) {
            stageWorkspaceVisuals(visualComponents)

            if (!chatComponents.some((c) => c.type === "mensaje_resumen")) {
              const infoMsg =
                "He realizado el análisis sobre los datos que solicitaste. He colocado los gráficos y tablas en tu lienzo principal para que puedas explorarlos."
              chatComponents.push({
                type: "mensaje_resumen",
                content: infoMsg,
                // @ts-ignore
                texto: infoMsg,
              })
              finalContent = infoMsg
            }
          } else {
            setWorkspaceRenderState({
              status: "idle",
              message: null,
              pendingVisuals: 0,
              renderedVisuals: 0,
            })
          }

          if (!processedTasksRef.current.has(activeTaskId!)) {
            if (componentsList.length > 0) {
              componentsList.forEach((comp: any) => {
                if (comp && typeof comp === "object") {
                  comp.task_id = activeTaskId
                  comp.file_id = analysisFileId || undefined
                }
              });
              saveMessageToBackend("assistant", componentsList)
              processedTasksRef.current.add(activeTaskId!)
            } else if (
              data.status === "failed" ||
              data.status === "timeout" ||
              data.status === "rate_limited"
            ) {
              saveMessageToBackend("assistant", [{ type: "error", content: finalContent }])
              processedTasksRef.current.add(activeTaskId!)
            }
          }

          setMessages((prevMessages) =>
            prevMessages.map((msg) =>
              msg.id === activeTaskId
                ? {
                    ...msg,
                    content: finalContent,
                    components: data.status === "completed" ? chatComponents : undefined,
                    _visuals:
                      data.status === "completed" && visualComponents.length > 0
                        ? visualComponents
                        : undefined,
                  }
                : msg
            )
          )

          if (activeAnalysisRequestRef.current?.taskId === activeTaskId) {
            activeAnalysisRequestRef.current = null
          }
          setActiveTaskId(null)
          setIsAnalyzing(false)
          if (
            data.status === "completed" ||
            data.status === "failed" ||
            data.status === "timeout" ||
            data.status === "rate_limited"
          ) {
            setWorkspaceRenderState({
              status: "idle",
              message: null,
              pendingVisuals: 0,
              renderedVisuals: 0,
            })
          }
          if (data.status === "completed") {
            setLastCompletedTaskId(activeTaskId)
          }
          return
        }

        if (hasAnalysisDeadlineElapsed() || pollRetryCountRef.current >= MAX_POLL_RETRIES) {
          console.error("⛔ [Polling] Deadline de análisis excedido sin estado terminal.")
          resolveClientTimeout()
          return
        }

        scheduleNextPoll(getAdaptivePollDelay(analysisStartedAtRef.current))
      } catch (error: any) {
        if (hasAnalysisDeadlineElapsed() || pollRetryCountRef.current >= MAX_POLL_RETRIES) {
          console.error("[Polling] No se pudo confirmar el estado final antes del deadline.", error)
          resolveClientTimeout()
          return
        }
        if (error?.name === "AbortError") {
          if (!cancelled) {
            scheduleNextPoll(POLLING_ERROR_RETRY_MS)
          }
          return
        }
        console.error(
          "[SPY] Polling error catch — name:",
          error?.name,
          "message:",
          error?.message,
          "cancelled:",
          cancelled
        )
        scheduleNextPoll(POLLING_ERROR_RETRY_MS)
      } finally {
        clearTimeout(requestTimeout)
        pollingInFlightRef.current = false
        if (pollingAbortRef.current === controller) {
          pollingAbortRef.current = null
        }
      }
    }

    scheduleNextPoll(POLLING_IMMEDIATE_DELAY_MS)
    return () => {
      cancelled = true
      clearPollingState()
    }
  }, [
    isAnalyzing,
    activeTaskId,
    pollingFallbackEnabled,
    saveMessageToBackend,
    setWorkspaceRenderState,
    stageWorkspaceVisuals,
    analysisFileId,
    getChatAccessToken,
    processedTasksRef,
    setIsAnalyzing,
    setIsDuckDBReady,
    setLastCompletedTaskId,
    setMessages,
    setActiveTaskId,
  ])

  // --- TRIGGER ANALYSIS ---
  const triggerAnalysis = useCallback(
    async (customMessage?: string) => {
      const textToSend = customMessage || message

      if (!textToSend.trim() || !analysisFileId) {
        if (!customMessage) toast.error("Por favor escribe un mensaje.")
        return
      }

      const currentMessage = textToSend.trim()
      console.time("🕵️‍♂️ [ESPÍA TOTAL] triggerAnalysis")
      const analysisRequestKey = buildAnalysisRequestKey(
        analysisFileId,
        currentMessage,
        lastCompletedTaskId
      )
      const activeRequest = activeAnalysisRequestRef.current
      if (activeRequest) {
        if (activeRequest.key === analysisRequestKey) {
          return
        }
        if (!customMessage) {
          toast.info("Ya hay un análisis en curso para este archivo.")
        }
        return
      }

      activeAnalysisRequestRef.current = {
        key: analysisRequestKey,
        taskId: null,
      }

      const isDrillDown = currentMessage.startsWith("🔍 Drill-Down:")

      const userMessage: ChatMessage = {
        id: Date.now().toString(),
        type: "user",
        content: currentMessage,
        timestamp: new Date(),
      }

      setMessages((prev) => [...prev, userMessage])
      setMessage("")
      setIsAnalyzing(true)
      clearWorkspaceStageTimer()
      setWorkspaceRenderState({
        status: "analyzing",
        message: "Preparando el lienzo y priorizando el visual principal...",
        pendingVisuals: 0,
        renderedVisuals: 0,
      })

      saveMessageToBackend("user", currentMessage)

      console.time("🕵️‍♂️ [ESPÍA AUTH] getChatAccessToken")
      const accessToken = await getChatAccessToken()
      console.timeEnd("🕵️‍♂️ [ESPÍA AUTH] getChatAccessToken")
      if (!accessToken) {
        toast.error("Sesión expirada")
        activeAnalysisRequestRef.current = null
        setIsAnalyzing(false)
        setWorkspaceRenderState({
          status: "idle",
          message: null,
          pendingVisuals: 0,
          renderedVisuals: 0,
        })
        return
      }

      try {
        const enrichedPrompt = JSON.stringify({
          text: currentMessage,
          parent_id: lastCompletedTaskId,
        })

        console.time("🕵️‍♂️ [ESPÍA POST] Tiempo Total Petición")
        const response = await fetch(`${API_BASE_URL}/api/v1/analyze`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${accessToken}`,
          },
          body: JSON.stringify({ file_id: analysisFileId, prompt: enrichedPrompt }),
        })

        if (!response.ok) throw new Error("Error en petición")
        const data = await response.json()
        console.timeEnd("🕵️‍♂️ [ESPÍA POST] Tiempo Total Petición")
        if (activeAnalysisRequestRef.current?.key === analysisRequestKey) {
          activeAnalysisRequestRef.current = {
            key: analysisRequestKey,
            taskId: data.task_id,
          }
        }
        pendingTaskResultRef.current = null
        setPollingFallbackEnabled(false)
        pollRetryCountRef.current = 0
        analysisStartedAtRef.current = Date.now()
        setActiveTaskId(data.task_id)

        const assistantLoadingMessage: ChatMessage = {
          id: data.task_id,
          type: "assistant",
          content: isDrillDown
            ? "Profundizando en el análisis..."
            : "Analizando... por favor espera.",
          timestamp: new Date(),
          taskId: data.task_id,
        }
        setMessages((prev) => [...prev, assistantLoadingMessage])
        console.timeEnd("🕵️‍♂️ [ESPÍA TOTAL] triggerAnalysis")
      } catch (error: any) {
        console.timeEnd("🕵️‍♂️ [ESPÍA TOTAL] triggerAnalysis")
        console.error(error)
        const errorMessage: ChatMessage = {
          id: Date.now().toString() + "-error",
          type: "assistant",
          content: `Error: ${error.message}`,
          timestamp: new Date(),
        }
        setMessages((prev) => [...prev, errorMessage])
        activeAnalysisRequestRef.current = null
        pendingTaskResultRef.current = null
        setPollingFallbackEnabled(false)
        setIsAnalyzing(false)
        setActiveTaskId(null)
        setWorkspaceRenderState({
          status: "idle",
          message: null,
          pendingVisuals: 0,
          renderedVisuals: 0,
        })
      }
    },
    [
      message,
      analysisFileId,
      saveMessageToBackend,
      setMessages,
      setMessage,
      setIsAnalyzing,
      setActiveTaskId,
      lastCompletedTaskId,
      getChatAccessToken,
      clearWorkspaceStageTimer,
      setWorkspaceRenderState,
    ]
  )

  // --- STOP ANALYSIS ---
  const stopAnalysis = useCallback(async () => {
    if (!isAnalyzing || !activeTaskId) return

    try {
      setIsAnalyzing(false)
      clearWorkspaceStageTimer()
      setMessages((prev) => {
        const newMessages = [...prev]
        const index = newMessages.findIndex((m) => m.id === activeTaskId)
        if (index !== -1) {
          newMessages[index] = { ...newMessages[index], content: "🛑 Análisis detenido." }
        }
        return newMessages
      })

      const accessToken = await getChatAccessToken()
      if (accessToken) {
        fetch(`${API_BASE_URL}/api/v1/tasks/${activeTaskId}/cancel`, {
          method: "POST",
          headers: { Authorization: `Bearer ${accessToken}` },
        }).catch((err) => console.warn("Error al cancelar tarea en backend:", err))
      }

      setWorkspaceRenderState({
        status: "idle",
        message: null,
        pendingVisuals: 0,
        renderedVisuals: 0,
      })
      activeAnalysisRequestRef.current = null
      pendingTaskResultRef.current = null
      pollRetryCountRef.current = 0
      analysisStartedAtRef.current = 0
      setPollingFallbackEnabled(false)
      setActiveTaskId(null)
      toast.success("Cancelado.")
    } catch (e) {
      console.error("Error al cancelar tarea:", e)
    }
  }, [
    isAnalyzing,
    activeTaskId,
    clearWorkspaceStageTimer,
    getChatAccessToken,
    setIsAnalyzing,
    setMessages,
    setActiveTaskId,
    setWorkspaceRenderState,
  ])

  return {
    triggerAnalysis,
    stopAnalysis,
  }
}
