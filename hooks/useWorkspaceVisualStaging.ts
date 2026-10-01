"use client"

import { useRef, useCallback, useEffect, startTransition } from "react"
import * as duckdbEngine from "@/lib/duckdb-engine"
import {
  prioritizeVisualComponents,
  collectArrowPreloadsFromVisuals,
} from "@/lib/chat-utils"
import type { AnalysisComponent } from "@/lib/state"

export interface UseWorkspaceVisualStagingDeps {
  setWorkspaceItems: (items: AnalysisComponent[]) => void
  setWorkspaceRenderState: (state: any) => void
  setIsDuckDBReady: (ready: boolean) => void
}

export function useWorkspaceVisualStaging({
  setWorkspaceItems,
  setWorkspaceRenderState,
  setIsDuckDBReady,
}: UseWorkspaceVisualStagingDeps) {
  const workspaceStageTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const workspaceVisualsRef = useRef<AnalysisComponent[]>([])
  const workspacePreloadPromiseRef = useRef<Promise<void> | null>(null)

  const clearWorkspaceStageTimer = useCallback(() => {
    if (workspaceStageTimerRef.current) {
      clearTimeout(workspaceStageTimerRef.current)
      workspaceStageTimerRef.current = null
    }
  }, [])

  const stageWorkspaceVisuals = useCallback(
    (visuals: AnalysisComponent[]) => {
      clearWorkspaceStageTimer()

      const prioritizedVisuals = prioritizeVisualComponents(visuals)
      if (prioritizedVisuals.length === 0) {
        startTransition(() => {
          setWorkspaceRenderState({
            status: "idle",
            message: null,
            pendingVisuals: 0,
            renderedVisuals: 0,
          })
        })
        return
      }

      workspaceVisualsRef.current = prioritizedVisuals

      const preloadEntries = collectArrowPreloadsFromVisuals(prioritizedVisuals)
      if (preloadEntries.length > 0) {
        const preloadPromise = duckdbEngine
          .preloadArrowTables(preloadEntries, 1)
          .then(() => {
            setIsDuckDBReady(true)
          })
          .catch((error) => {
            console.warn("⚠️ [DuckDB] Preload de visuales no completado:", error)
          })
        workspacePreloadPromiseRef.current = preloadPromise
        void preloadPromise.finally(() => {
          if (workspacePreloadPromiseRef.current === preloadPromise) {
            workspacePreloadPromiseRef.current = null
          }
        })
      } else {
        workspacePreloadPromiseRef.current = null
      }

      startTransition(() => {
        setWorkspaceItems(prioritizedVisuals)
        setWorkspaceRenderState({
          status: "idle",
          message: null,
          pendingVisuals: prioritizedVisuals.length,
          renderedVisuals: prioritizedVisuals.length,
        })
      })
    },
    [clearWorkspaceStageTimer, setIsDuckDBReady, setWorkspaceItems, setWorkspaceRenderState]
  )

  useEffect(() => {
    return () => {
      clearWorkspaceStageTimer()
    }
  }, [clearWorkspaceStageTimer])

  return {
    stageWorkspaceVisuals,
    clearWorkspaceStageTimer,
    workspaceVisualsRef,
    workspacePreloadPromiseRef,
  }
}
