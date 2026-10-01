"use client"

import { useState, useRef, useCallback } from "react"
import { toast } from "sonner"
import { API_BASE_URL } from "@/lib/api-config"
import {
  POLLING_FAST_INTERVAL_MS,
  POLLING_REQUEST_TIMEOUT_MS,
  MAX_POLL_RETRIES,
  ANALYSIS_COMPLETION_DEADLINE_MS,
  normalizeTaskStatusPayload,
} from "@/lib/chat-utils"

export interface UseAnalysisExecutionProps {
  getAccessToken: () => Promise<string | null>
  onTaskCompleted?: (taskId: string, result: any) => void
  onTaskFailed?: (taskId: string, errorMsg: string) => void
}

export function useAnalysisExecution({
  getAccessToken,
  onTaskCompleted,
  onTaskFailed,
}: UseAnalysisExecutionProps) {
  const [isAnalyzing, setIsAnalyzing] = useState(false)
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null)
  const [lastCompletedTaskId, setLastCompletedTaskId] = useState<string | null>(null)
  const [pollingFallbackEnabled, setPollingFallbackEnabled] = useState(false)

  const pollingTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const pollingAbortRef = useRef<AbortController | null>(null)
  const pollingInFlightRef = useRef(false)
  const pollRetryCountRef = useRef(0)
  const analysisStartedAtRef = useRef(0)
  const processedTasksRef = useRef<Set<string>>(new Set())

  const clearPollingState = useCallback(() => {
    if (pollingTimerRef.current) {
      clearTimeout(pollingTimerRef.current)
      pollingTimerRef.current = null
    }
    if (pollingAbortRef.current) {
      pollingAbortRef.current.abort()
      pollingAbortRef.current = null
    }
    pollingInFlightRef.current = false
  }, [])

  const stopAnalysis = useCallback(async () => {
    clearPollingState()
    if (activeTaskId) {
      try {
        const token = await getAccessToken()
        if (token) {
          await fetch(`${API_BASE_URL}/api/v1/tasks/${activeTaskId}/cancel`, {
            method: "POST",
            headers: { Authorization: `Bearer ${token}` },
          }).catch(() => {})
        }
      } catch (err) {
        console.warn("⚠️ [useAnalysisExecution] No se pudo cancelar tarea en backend:", err)
      }
    }
    setIsAnalyzing(false)
    setActiveTaskId(null)
    setPollingFallbackEnabled(false)
    toast.info("Análisis cancelado por el usuario.")
  }, [activeTaskId, clearPollingState, getAccessToken])

  return {
    isAnalyzing,
    setIsAnalyzing,
    activeTaskId,
    setActiveTaskId,
    lastCompletedTaskId,
    setLastCompletedTaskId,
    pollingFallbackEnabled,
    setPollingFallbackEnabled,
    clearPollingState,
    stopAnalysis,
    processedTasksRef,
    analysisStartedAtRef,
    pollRetryCountRef,
    pollingTimerRef,
    pollingAbortRef,
    pollingInFlightRef,
  }
}
