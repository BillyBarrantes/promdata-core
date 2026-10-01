// lib/chat-utils.ts
import * as duckdbEngine from "@/lib/duckdb-engine"
import { getScopedLocalPerfAverage } from "@/lib/local-performance"
import type { AnalysisComponent } from "@/lib/state"

export const POLLING_REQUEST_TIMEOUT_MS = 5000
export const POLLING_FAST_INTERVAL_MS = 1500
export const MAX_POLL_RETRIES = 90
export const ANALYSIS_COMPLETION_DEADLINE_MS = 180000

export interface ChatMessage {
  id: string
  type: "user" | "assistant"
  content: string
  timestamp: Date
  components?: AnalysisComponent[]
  taskId?: string
  _visuals?: AnalysisComponent[]
}

export const VISUAL_COMPONENT_TYPES = [
  "metricas_clave",
  "configuracion_echarts",
  "smart_table",
  "tabla_datos",
] as const

export const extractRawChartCategory = (params: any): string | null => {
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
  ]

  for (const candidate of candidates) {
    if (typeof candidate === "string" && candidate.trim()) {
      return candidate.replace(/\0/g, "").normalize("NFC").replace(/\s+/g, " ").trim()
    }
  }

  return null
}

export const normalizeSyntheticBucketToken = (value: string): string => {
  return value
    .replace(/\0/g, "")
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "")
}

export const isOthersBucketLabel = (value: string | null | undefined): boolean => {
  if (!value) return false
  const normalized = normalizeSyntheticBucketToken(String(value))
  return normalized === "otros" || normalized === "other" || normalized === "others"
}

export const getChartOptionFromVisualComponent = (component: any): any | null => {
  if (!component || typeof component !== "object") return null
  if (component.option && typeof component.option === "object") return component.option
  if (component.original_chart_option && typeof component.original_chart_option === "object") {
    return component.original_chart_option
  }
  return null
}

export const extractHierarchyDisplayItems = (option: any): string[] => {
  const seriesList = Array.isArray(option?.series)
    ? option.series
    : option?.series
      ? [option.series]
      : []
  const primarySeries = seriesList.find(Boolean)
  const primaryType = String(primarySeries?.type || "").toLowerCase()

  if (!["pie", "treemap", "funnel"].includes(primaryType)) {
    return []
  }

  const rawItems = Array.isArray(primarySeries?.data) ? primarySeries.data : []
  return rawItems
    .map((item: any) => {
      if (item && typeof item === "object" && !Array.isArray(item)) {
        const candidate =
          item.raw_name ??
          item.rawName ??
          item.full_name ??
          item.fullName ??
          item.name
        return typeof candidate === "string" ? candidate : null
      }

      return typeof item === "string" ? item : null
    })
    .filter((item: string | null): item is string => Boolean(item && item.trim()))
    .map((item: string) => item.replace(/\0/g, "").normalize("NFC").replace(/\s+/g, " ").trim())
}

export const enrichFiltersWithSyntheticBucket = (
  filters: Record<string, string>,
  matchedComponent: any
): Record<string, string> => {
  const chartOption = getChartOptionFromVisualComponent(matchedComponent)
  const dimension = typeof chartOption?.query_contract?.dimension === "string"
    ? chartOption.query_contract.dimension
    : null
  const selectedValue =
    (dimension ? filters[dimension] : null) ||
    filters.global_chart_filter ||
    filters.global_cross_filter ||
    null
  if (!isOthersBucketLabel(selectedValue)) {
    return filters
  }
  if (!dimension) {
    return filters
  }

  const excluded = extractHierarchyDisplayItems(chartOption).filter(
    (item) => !isOthersBucketLabel(item)
  )
  if (excluded.length === 0) {
    return filters
  }

  const payload = {
    type: "others_excluding_visible",
    label: String(selectedValue),
    dimension,
    excluded,
  }

  console.log("🧠 [CROSS-FILTER] synthetic_bucket_enriched", payload)

  return {
    ...filters,
    __synthetic_bucket__: JSON.stringify(payload),
  }
}

export const getVisualPriority = (component: AnalysisComponent): number => {
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

export const prioritizeVisualComponents = (
  components: AnalysisComponent[]
): AnalysisComponent[] => {
  return [...components].sort((left, right) => getVisualPriority(left) - getVisualPriority(right))
}

export const sanitizeTableToken = (value: string): string => {
  return value
    .replace(/[^a-zA-Z0-9_]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .toLowerCase()
}

export const sanitizeFilterValue = (value: string): string => {
  if (typeof value !== "string") return value
  return value.replace(/\0/g, "").normalize("NFC").replace(/\s+/g, " ").trim()
}

export const buildAnalysisTableName = (
  fileId: string | null,
  scope: string,
  rawKey: string | number
): string => {
  const fileToken = sanitizeTableToken(fileId || "global")
  const scopeToken = sanitizeTableToken(scope)
  const keyToken = sanitizeTableToken(String(rawKey || "default"))
  return `pd_${scopeToken}_${fileToken}_${keyToken}`.slice(0, 120)
}

export const collectArrowPreloadsFromVisuals = (
  visuals: AnalysisComponent[]
): duckdbEngine.ArrowPreloadEntry[] => {
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
    const leftHistoricalCost = getScopedLocalPerfAverage("duckdb_cross_filter", left.tableName) || 0
    const rightHistoricalCost = getScopedLocalPerfAverage("duckdb_cross_filter", right.tableName) || 0

    if (leftHistoricalCost !== rightHistoricalCost) {
      return rightHistoricalCost - leftHistoricalCost
    }

    return (left.priority ?? 99) - (right.priority ?? 99)
  })
}

export const normalizeTaskStatusPayload = (rawPayload: any): any => {
  if (!rawPayload || typeof rawPayload !== "object") {
    return { status: "failed", result: null }
  }
  const status = rawPayload.status || "processing"
  const result = rawPayload.results_json || rawPayload.result || null
  return {
    ...rawPayload,
    status,
    result,
  }
}
