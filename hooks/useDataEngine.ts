"use client"

import { useState, useEffect, useCallback, useRef } from "react"
import * as duckdbEngine from "@/lib/duckdb-engine"
import { useSupabase } from "@/lib/supabase-provider"
import { API_BASE_URL } from "@/lib/api-config"
import { toast } from "sonner"

export interface CrossFilterAggResult {
  category: string
  value: number
}

export const CROSS_FILTER_DEEP_SQL_ENABLED =
  typeof process !== "undefined" &&
  process.env.NEXT_PUBLIC_CROSS_FILTER_DEEP_SQL === "true"

export interface UseDataEngineReturn {
  isReady: boolean
  loadedTables: string[]
  warmup: () => Promise<void>
  loadArrowData: (arrowBase64: string, tableName: string) => Promise<void>
  preloadArrowTables: (
    entries: duckdbEngine.ArrowPreloadEntry[],
    concurrency?: number
  ) => Promise<void>
  rehydrateFromTask: (taskId: string, preferredTableName?: string) => Promise<string | null>
  executeCrossFilter: (
    filters: Record<string, string>,
    tableName?: string,
    taskId?: string,
    fallbackArrow?: string | null
  ) => Promise<Record<string, unknown>[] | null>
  queryCrossFilterAggregation: (
    tableName: string,
    filterDim: string,
    filterVal: string,
    targetDim: string,
    metricCol?: string,
    aggFunc?: "SUM" | "AVG" | "COUNT"
  ) => Promise<CrossFilterAggResult[] | null>
  query: (sql: string) => Promise<Record<string, unknown>[]>
}

export function useDataEngine(): UseDataEngineReturn {
  const [isReady, setIsReady] = useState<boolean>(duckdbEngine.isReady())
  const [loadedTables, setLoadedTables] = useState<string[]>(() => duckdbEngine.getTableNames())
  const supabase = useSupabase()
  const rehydratingTasksRef = useRef<Map<string, Promise<string | null>>>(new Map())

  const getAccessToken = useCallback(async (): Promise<string | null> => {
    const { data: { session } } = await supabase.auth.getSession()
    if (session?.access_token) return session.access_token

    if (typeof window !== "undefined" && process.env.NODE_ENV !== "production") {
      const params = new URLSearchParams(window.location.search)
      if (params.get("__qa_chat") === "1") {
        return params.get("__qa_chat_token") || "qa-chat-token"
      }
    }
    return null
  }, [supabase])

  const syncTables = useCallback(() => {
    const tables = duckdbEngine.getTableNames()
    setLoadedTables(tables)
    setIsReady(duckdbEngine.isReady())
  }, [])

  const warmup = useCallback(async () => {
    try {
      await duckdbEngine.warmup()
      syncTables()
    } catch (err) {
      console.warn("⚠️ [useDataEngine] Warmup falló o fue cancelado:", err)
    }
  }, [syncTables])

  useEffect(() => {
    let cancelled = false
    const scheduleWarmup = () => {
      duckdbEngine.warmup().then(() => {
        if (!cancelled) syncTables()
      }).catch(() => {})
    }

    if (typeof window !== "undefined" && "requestIdleCallback" in window) {
      const idleId = window.requestIdleCallback(() => scheduleWarmup(), { timeout: 1500 })
      return () => {
        cancelled = true
        window.cancelIdleCallback(idleId)
      }
    } else {
      const timeoutId = setTimeout(scheduleWarmup, 400)
      return () => {
        cancelled = true
        clearTimeout(timeoutId)
      }
    }
  }, [syncTables])

  const loadArrowData = useCallback(async (arrowBase64: string, tableName: string) => {
    await duckdbEngine.loadArrowData(arrowBase64, tableName)
    syncTables()
  }, [syncTables])

  const preloadArrowTables = useCallback(async (
    entries: duckdbEngine.ArrowPreloadEntry[],
    concurrency = 1
  ) => {
    await duckdbEngine.preloadArrowTables(entries, concurrency)
    syncTables()
  }, [syncTables])

  /**
   * [FASE 6.1 & 6.2] Re-hidratación Automática On-Demand desde GET /api/v1/tasks/{task_id}.
   * Si DuckDB-WASM se reinició vacío al navegar entre rutas, recupera el snapshot_arrow
   * o granular_arrow de la tarea original sin requerir endpoints nuevos.
   */
  const rehydrateFromTask = useCallback(async (
    taskId: string,
    preferredTableName?: string
  ): Promise<string | null> => {
    if (!taskId) return null

    // Evitar peticiones concurrentes duplicadas para el mismo task_id
    if (rehydratingTasksRef.current.has(taskId)) {
      return rehydratingTasksRef.current.get(taskId)!
    }

    const rehydrationPromise = (async (): Promise<string | null> => {
      try {
        const token = await getAccessToken()
        if (!token) return null

        console.log(`🦆 [useDataEngine] Re-hidratando tablas para task ${taskId}...`)
        const res = await fetch(`${API_BASE_URL}/api/v1/tasks/${taskId}`, {
          headers: { Authorization: `Bearer ${token}` }
        })

        if (!res.ok) {
          console.warn(`⚠️ [useDataEngine] No se pudo obtener resultado de tarea ${taskId} (${res.status})`)
          return null
        }

        const taskData = await res.json()
        const result = taskData?.results_json || taskData?.result || {}

        const snapshotArrow = result?.snapshot_arrow
        const topLevelArrow = result?.arrow_data
        const chartOptions = Array.isArray(result?.chart_options)
          ? result.chart_options
          : (result?.chart_options ? [result.chart_options] : [])

        const preloadEntries: duckdbEngine.ArrowPreloadEntry[] = []
        let resolvedTable = preferredTableName || null

        // 1. Cargar snapshot completo si existe
        if (snapshotArrow) {
          const snapshotTable = `pd_snapshot_${taskId.replace(/-/g, "_")}`
          preloadEntries.push({ tableName: snapshotTable, base64Data: snapshotArrow, priority: 0 })
          if (!resolvedTable) resolvedTable = snapshotTable
        }

        // 2. Cargar arrow top-level
        if (topLevelArrow) {
          const detailTable = `pd_analysis_${taskId.replace(/-/g, "_")}_detail`
          preloadEntries.push({ tableName: detailTable, base64Data: topLevelArrow, priority: 1 })
          if (!resolvedTable) resolvedTable = detailTable
        }

        // 3. Cargar granular_arrow por chart
        chartOptions.forEach((opt: any, idx: number) => {
          const arrow = opt?.granular_arrow || opt?.arrow_data
          const cTable = opt?.table_name || `pd_chart_${taskId.replace(/-/g, "_")}_${idx}`
          if (arrow) {
            preloadEntries.push({ tableName: cTable, base64Data: arrow, priority: 2 })
            if (!resolvedTable) resolvedTable = cTable
          }
        })

        if (preloadEntries.length > 0) {
          await duckdbEngine.preloadArrowTables(preloadEntries, 1)
          syncTables()
          console.log(`🦆 [useDataEngine] Re-hidratación completada con éxito. Tablas:`, duckdbEngine.getTableNames())
          return resolvedTable
        }

        return null
      } catch (err) {
        console.error("⚠️ [useDataEngine] Error durante re-hidratación:", err)
        return null
      } finally {
        rehydratingTasksRef.current.delete(taskId)
      }
    })()

    rehydratingTasksRef.current.set(taskId, rehydrationPromise)
    return rehydrationPromise
  }, [getAccessToken, syncTables])

  /**
   * [FASE 6.2] Ejecución de Cross-Filter con auto-recuperación transparente.
   */
  const executeCrossFilter = useCallback(async (
    filters: Record<string, string>,
    tableName?: string,
    taskId?: string,
    fallbackArrow?: string | null
  ): Promise<Record<string, unknown>[] | null> => {
    let currentTables = duckdbEngine.getTableNames()
    let targetTable = tableName

    // 1. Si targetTable no está cargada o no se especificó, intentar resolver
    if (!targetTable || !currentTables.includes(targetTable)) {
      if (fallbackArrow && targetTable) {
        // Cargar inmediatamente desde fallback granular disponible
        await duckdbEngine.loadArrowData(fallbackArrow, targetTable)
        syncTables()
      } else if (taskId) {
        // Re-hidratar on-demand desde backend task
        const rehydrated = await rehydrateFromTask(taskId, targetTable)
        if (rehydrated) {
          targetTable = rehydrated
        }
      }
    }

    currentTables = duckdbEngine.getTableNames()

    // 2. Si todavía no está disponible, usar la primera tabla viva
    if (!targetTable || !currentTables.includes(targetTable)) {
      if (currentTables.length > 0) {
        targetTable = currentTables[0]
      } else {
        console.warn("⚠️ [useDataEngine] Sin tablas disponibles para filtrado local.")
        toast.info("Filtrado local no disponible para este análisis.")
        return null
      }
    }

    try {
      const results = await duckdbEngine.crossFilter(filters, targetTable)
      return results
    } catch (err) {
      console.error("⚠️ [useDataEngine] Error ejecutando crossFilter en DuckDB:", err)
      return null
    }
  }, [rehydrateFromTask, syncTables])

  /**
   * [FASE B.2] Deep SQL Path: agregación analítica directa en DuckDB-WASM
   * con sanitización rigurosa de identificadores y valores.
   */
  const queryCrossFilterAggregation = useCallback(async (
    tableName: string,
    filterDim: string,
    filterVal: string,
    targetDim: string,
    metricCol?: string,
    aggFunc: "SUM" | "AVG" | "COUNT" = "SUM"
  ): Promise<CrossFilterAggResult[] | null> => {
    try {
      const currentTables = duckdbEngine.getTableNames()
      if (!currentTables.includes(tableName)) {
        return null
      }

      // 🛡️ Sanitización de identificadores (comillas dobles)
      const safeTable = tableName.replace(/"/g, '""')
      const safeFilterDim = filterDim.replace(/"/g, '""')
      const safeTargetDim = targetDim.replace(/"/g, '""')
      // 🛡️ Sanitización de valores literales SQL (comillas simples)
      const safeFilterVal = String(filterVal).replace(/'/g, "''").toLowerCase().trim()

      const safeMetricExpr = metricCol && aggFunc !== "COUNT"
        ? `${aggFunc}("${metricCol.replace(/"/g, '""')}")`
        : `COUNT(*)`

      const sql = `
        SELECT CAST("${safeTargetDim}" AS VARCHAR) AS category,
               ${safeMetricExpr} AS value
        FROM "${safeTable}"
        WHERE LOWER(CAST("${safeFilterDim}" AS VARCHAR)) = '${safeFilterVal}'
        GROUP BY "${safeTargetDim}"
        ORDER BY value DESC
        LIMIT 20
      `

      const rows = await duckdbEngine.query(sql)
      return rows.map((r: any) => ({
        category: String(r.category ?? ""),
        value: Number(r.value ?? 0),
      }))
    } catch (err) {
      console.warn("⚠️ [useDataEngine] Deep SQL crossFilter falló, fallback a Fast Path:", err)
      return null
    }
  }, [])

  const query = useCallback(async (sql: string): Promise<Record<string, unknown>[]> => {
    return duckdbEngine.query(sql)
  }, [])

  return {
    isReady,
    loadedTables,
    warmup,
    loadArrowData,
    preloadArrowTables,
    rehydrateFromTask,
    executeCrossFilter,
    queryCrossFilterAggregation,
    query,
  }
}
