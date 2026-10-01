"use client"

import { useMemo, useState, useEffect, useRef } from "react"
import { useAtomValue } from "jotai"
import {
  workspaceItemsAtom,
  activeCrossFilterAtom,
  globalFiltersAtom,
} from "@/lib/state"
import * as duckdbEngine from "@/lib/duckdb-engine"
import { CURRENCY_LOCALES, getActiveCurrency } from "@/components/user-preferences"

interface KpiReactivityOptions {
  label: string
  originalValue: string
  sourceTable?: string
  isWidget?: boolean
}

interface KpiReactivityResult {
  filteredValue: string | null
  isFiltered: boolean
  originalValue: string
}

function normalizeLabel(s: string): string {
  return s
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[_\s]+/g, "")
    .trim()
}

function formatNumber(value: unknown): string {
  if (value == null) return ""
  const num = typeof value === "number" ? value : Number(value)
  if (isNaN(num)) return String(value)
  const locale = CURRENCY_LOCALES[getActiveCurrency()] ?? "es-PE"
  return num.toLocaleString(locale, { maximumFractionDigits: 2 })
}

export function useKpiReactivity({
  label,
  originalValue,
  sourceTable,
  isWidget = false,
}: KpiReactivityOptions): KpiReactivityResult {
  const workspaceItems = useAtomValue(workspaceItemsAtom)
  const activeCrossFilter = useAtomValue(activeCrossFilterAtom)
  const globalFilters = useAtomValue(globalFiltersAtom)
  const [filteredValue, setFilteredValue] = useState<string | null>(null)
  const queryIdRef = useRef(0)

  const activeFilter = useMemo(() => {
    if (activeCrossFilter) return activeCrossFilter
    const entries = Object.entries(globalFilters).filter(
      ([k]) => !k.startsWith("__")
    )
    if (entries.length === 0) return null
    return { dimension: entries[0][0], value: String(entries[0][1]) }
  }, [activeCrossFilter, globalFilters])

  const matchingChart = useMemo(() => {
    if (!activeFilter) return null

    const normalizedLabel = normalizeLabel(label)

    const chartComponents = workspaceItems.filter(
      (item) =>
        item.type === "configuracion_echarts" ||
        item.type === "smart_table"
    )

    for (const chart of chartComponents) {
      const option = chart.original_chart_option || chart.option
      if (!option) continue

      const contract = option.query_contract
      if (!contract) continue

      const metricName = contract.metric || contract.yAxis?.name
      if (!metricName) continue

      if (normalizeLabel(metricName) === normalizedLabel) {
        return {
          tableName: chart.table_name || sourceTable,
          contract,
        }
      }
    }

    return null
  }, [activeFilter, label, workspaceItems, sourceTable])

  useEffect(() => {
    // En modo widget (Dashboard), el filtrado lo maneja grid-widget con resolvedMetrics.
    // No consultamos DuckDB aquí — solo devolvemos el valor original.
    if (isWidget) {
      setFilteredValue(null)
      return
    }

    if (!activeFilter || !matchingChart) {
      setFilteredValue(null)
      return
    }

    const { tableName, contract } = matchingChart
    if (!tableName || !duckdbEngine.isReady()) {
      setFilteredValue(null)
      return
    }

    const tableNames = duckdbEngine.getTableNames()
    if (!tableNames.includes(tableName)) {
      setFilteredValue(null)
      return
    }

    const dimension = contract.dimension || activeFilter.dimension
    const aggregation = contract.aggregation || "SUM"
    const metric = contract.metric || label

    const sql = `SELECT ${aggregation}("${metric}") AS kpi_value FROM "${tableName}" WHERE "${dimension}" = '${activeFilter.value.replace(/'/g, "''")}'`

    const currentQueryId = ++queryIdRef.current

    duckdbEngine.query(sql, true)
      .then((rows) => {
        if (currentQueryId !== queryIdRef.current) return
        if (rows.length > 0 && rows[0].kpi_value != null) {
          setFilteredValue(formatNumber(rows[0].kpi_value))
        } else {
          setFilteredValue(null)
        }
      })
      .catch(() => {
        if (currentQueryId !== queryIdRef.current) return
        setFilteredValue(null)
      })
  }, [activeFilter, matchingChart, label, isWidget])

  return {
    filteredValue,
    isFiltered: filteredValue !== null,
    originalValue,
  }
}
