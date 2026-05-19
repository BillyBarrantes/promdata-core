"use client"

import React, { useState, useMemo, useCallback, useDeferredValue } from 'react'
import { Card } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { SaveIcon } from '@/components/icons/save-icon'
import { ChartsReport } from '@/components/charts-report'
import { EChartsChart } from '@/components/echarts-chart'
import { Search, ArrowUpDown, ArrowUp, ArrowDown, BarChart3, Table2, Rows3, X, SlidersHorizontal, LoaderCircle } from 'lucide-react'
import { useAtomValue } from 'jotai'
import { activeFileIdAtom } from '@/lib/state'

// ---------------------------------------------------------------------------
// TIPOS
// ---------------------------------------------------------------------------

export interface SmartTableColumn {
  key: string
  label: string
  type: "text" | "number" | "percentage" | "sparkline"
  bar?: boolean
  heatmap?: boolean
}

export interface SmartTableProps {
  title?: string
  columns: SmartTableColumn[]
  data: Record<string, any>[]
  sortBy?: string
  sortOrder?: "asc" | "desc"
  originalChartOption?: any
  onSave: () => void
  onChartClick?: (params: any) => void
  fileId?: string | null
  /** Cuando true, renderiza sin Card/padding externo (usado dentro de GridWidget) */
  isWidget?: boolean
  /** Vista inicial preferida. Si no se envía, usa gráfico cuando exista originalChartOption. */
  defaultViewMode?: 'table' | 'chart' | 'hybrid'
  /** Cuando true, oculta chrome secundario para modo presentación/exporte. */
  presentationMode?: boolean
}

// ---------------------------------------------------------------------------
// CONSTANTES
// ---------------------------------------------------------------------------

const DEFAULT_PAGE_SIZE = 25
const PAGE_SIZE_OPTIONS = [25, 50, 100, 250]
const VIRTUALIZATION_THRESHOLD = 40
const VIRTUAL_ROW_HEIGHT = 44

const toNumericValue = (rawValue: any): number | null => {
  const value = rawValue && typeof rawValue === 'object' && !Array.isArray(rawValue)
    ? rawValue.value
    : rawValue

  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  if (typeof value === 'string') {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

const buildSparklineFromSeries = (seriesList: any[], rowIndex: number, windowSize: number = 12): number[] | null => {
  if (!Array.isArray(seriesList) || seriesList.length === 0) return null

  if (seriesList.length > 1) {
    const values: number[] = []
    seriesList.forEach((series) => {
      const data = Array.isArray(series?.data) ? series.data : []
      if (rowIndex >= data.length) return
      const numeric = toNumericValue(data[rowIndex])
      if (numeric !== null) values.push(numeric)
    })
    return values.length >= 2 ? values : null
  }

  const onlySeries = Array.isArray(seriesList[0]?.data) ? seriesList[0].data : []
  const numericSeries = onlySeries
    .map((point: any) => toNumericValue(point))
    .filter((point: number | null): point is number => point !== null)

  if (numericSeries.length < 2) return null

  const start = Math.max(0, rowIndex - windowSize + 1)
  const end = Math.min(numericSeries.length, rowIndex + 1)
  let segment = numericSeries.slice(start, end)

  if (segment.length < 2) {
    const half = Math.floor(windowSize / 2)
    const centeredStart = Math.max(0, rowIndex - half)
    const centeredEnd = Math.min(numericSeries.length, rowIndex + half + 1)
    segment = numericSeries.slice(centeredStart, centeredEnd)
  }

  return segment.length >= 2 ? segment : null
}

const buildSparklineOption = (values: number[]): any => ({
  animation: false,
  tooltip: { show: false },
  grid: { left: 0, right: 0, top: 1, bottom: 1, containLabel: false },
  xAxis: { type: 'category', show: false, data: values.map((_, index) => index) },
  yAxis: { type: 'value', show: false, scale: true },
  series: [
    {
      type: 'line',
      data: values,
      smooth: true,
      symbol: 'none',
      lineStyle: { width: 1.5, color: '#2563eb' },
      areaStyle: { color: 'rgba(37, 99, 235, 0.12)' },
      animation: false,
      emphasis: { disabled: true }
    }
  ]
})

const SparklineCell = React.memo(({ values }: { values: number[] }) => {
  const safeValues = Array.isArray(values) ? values.filter((value) => Number.isFinite(Number(value))).map((value) => Number(value)) : []

  if (safeValues.length < 2) {
    return <span className="text-muted-foreground">—</span>
  }

  return (
    <div className="w-[84px] h-[24px] min-w-[84px]">
      <EChartsChart
        option={buildSparklineOption(safeValues)}
        isThumbnail={true}
        style={{ width: '84px', height: '24px' }}
        interactionMode="filter"
      />
    </div>
  )
})
SparklineCell.displayName = 'SparklineCell'

type SmartTablePreferenceState = {
  viewMode: 'table' | 'chart' | 'hybrid'
  sortKey: string
  sortDirection: 'asc' | 'desc'
  pageSize: number
}

const isBrowser = typeof window !== 'undefined'

// ---------------------------------------------------------------------------
// COMPONENTE
// ---------------------------------------------------------------------------

const SmartTableComponent = ({
  title,
  columns,
  data,
  sortBy,
  sortOrder = 'desc',
  originalChartOption,
  onSave,
  onChartClick,
  fileId,
  isWidget = false,
  defaultViewMode,
  presentationMode = false,
}: SmartTableProps) => {
  const activeFileId = useAtomValue(activeFileIdAtom)
  const resolvedFileId = fileId || activeFileId || 'global'
  const preferenceKey = useMemo(() => {
    const columnSignature = (Array.isArray(columns) ? columns : [])
      .map((column) => `${column.key}:${column.type}`)
      .join('|')
    const titleSignature = (title || 'smart-table').trim().toLowerCase().replace(/\s+/g, '-')
    return `promdata:smart-table:v2:${resolvedFileId}:${titleSignature}:${columnSignature}`
  }, [columns, resolvedFileId, title])

  // --- ESTADO ---
  const [search, setSearch] = useState('')
  const [pageSize, setPageSize] = useState<number>(DEFAULT_PAGE_SIZE)
  const [sortConfig, setSortConfig] = useState<{ key: string; direction: 'asc' | 'desc' }>({
    key: sortBy || columns[1]?.key || columns[0]?.key || '',
    direction: sortOrder
  })
  const [page, setPage] = useState(0)
  const [viewMode, setViewMode] = useState<'table' | 'chart' | 'hybrid'>(() => {
    if (defaultViewMode) return defaultViewMode
    return originalChartOption ? 'chart' : 'table'
  })
  const [prefsHydrated, setPrefsHydrated] = useState(false)
  const [scrollTop, setScrollTop] = useState(0)
  const [viewportHeight, setViewportHeight] = useState(420)
  const tableViewportRef = React.useRef<HTMLDivElement | null>(null)
  const deferredSearch = useDeferredValue(search)

  React.useEffect(() => {
    const nextViewMode: 'table' | 'chart' | 'hybrid' = defaultViewMode || (originalChartOption ? 'chart' : 'table')
    setViewMode(nextViewMode)
  }, [defaultViewMode, originalChartOption, title])

  React.useEffect(() => {
    if (!isBrowser) return

    try {
      const raw = window.localStorage.getItem(preferenceKey)
      if (!raw) {
        setPrefsHydrated(true)
        return
      }

      const parsed = JSON.parse(raw) as Partial<SmartTablePreferenceState>
      if (parsed.sortKey && typeof parsed.sortKey === 'string') {
        setSortConfig({
          key: parsed.sortKey,
          direction: parsed.sortDirection === 'asc' ? 'asc' : 'desc',
        })
      }
      if (parsed.viewMode && ['table', 'chart', 'hybrid'].includes(parsed.viewMode)) {
        if (parsed.viewMode !== 'chart' || originalChartOption) {
          setViewMode(parsed.viewMode)
        }
      }
      if (typeof parsed.pageSize === 'number' && PAGE_SIZE_OPTIONS.includes(parsed.pageSize)) {
        setPageSize(parsed.pageSize)
      }
    } catch (error) {
      console.error('Error cargando preferencias Smart Table', error)
    } finally {
      setPrefsHydrated(true)
    }
  }, [preferenceKey, originalChartOption])

  React.useEffect(() => {
    if (!isBrowser || !prefsHydrated) return

    const payload: SmartTablePreferenceState = {
      viewMode,
      sortKey: sortConfig.key,
      sortDirection: sortConfig.direction,
      pageSize,
    }

    try {
      window.localStorage.setItem(preferenceKey, JSON.stringify(payload))
    } catch (error) {
      console.error('Error persistiendo preferencias Smart Table', error)
    }
  }, [pageSize, preferenceKey, prefsHydrated, sortConfig.direction, sortConfig.key, viewMode])

  const { normalizedColumns, normalizedData } = useMemo(() => {
    const baseColumns = Array.isArray(columns) ? columns : []
    const baseData = Array.isArray(data) ? data : []
    const hasSparklineColumn = baseColumns.some((col) => col?.type === 'sparkline')

    if (hasSparklineColumn || !originalChartOption) {
      return { normalizedColumns: baseColumns, normalizedData: baseData }
    }

    const seriesList = Array.isArray(originalChartOption?.series) ? originalChartOption.series : []
    if (seriesList.length === 0) {
      return { normalizedColumns: baseColumns, normalizedData: baseData }
    }

    let hasInjectedSparkline = false
    const nextData = baseData.map((row, rowIndex) => {
      const sparkline = buildSparklineFromSeries(seriesList, rowIndex)
      if (!sparkline) return row
      hasInjectedSparkline = true
      return { ...row, sparkline_data: sparkline }
    })

    if (!hasInjectedSparkline) {
      return { normalizedColumns: baseColumns, normalizedData: baseData }
    }

    return {
      normalizedColumns: [
        ...baseColumns,
        { key: 'sparkline_data', label: 'Tendencia', type: 'sparkline' as const }
      ],
      normalizedData: nextData
    }
  }, [columns, data, originalChartOption])

  const columnByKey = useMemo(() => {
    const map: Record<string, SmartTableColumn> = {}
    normalizedColumns.forEach((column) => {
      map[column.key] = column
    })
    return map
  }, [normalizedColumns])

  // --- COLUMNAS MAX (para data bars) ---
  const columnMaxValues = useMemo(() => {
    const maxes: Record<string, number> = {}
    normalizedColumns.forEach(col => {
      if (col.type === 'number' && col.bar) {
        let max = 0
        normalizedData.forEach(row => {
          const val = typeof row[col.key] === 'number' ? Math.abs(row[col.key]) : 0
          if (val > max) max = val
        })
        maxes[col.key] = max
      }
      if (col.type === 'percentage' && col.heatmap) {
        let maxAbs = 0
        normalizedData.forEach(row => {
          const val = typeof row[col.key] === 'number' ? Math.abs(row[col.key]) : 0
          if (val > maxAbs) maxAbs = val
        })
        maxes[col.key] = maxAbs
      }
    })
    return maxes
  }, [normalizedData, normalizedColumns])

  // --- FILTRADO ---
  const filteredData = useMemo(() => {
    if (!deferredSearch.trim()) return normalizedData
    const term = deferredSearch.toLowerCase()
    return normalizedData.filter(row =>
      Object.values(row).some(v =>
        String(v ?? '').toLowerCase().includes(term)
      )
    )
  }, [normalizedData, deferredSearch])

  // --- SORTING ---
  const sortedData = useMemo(() => {
    const sorted = [...filteredData]
    const { key, direction } = sortConfig
    if (!key) return sorted

    sorted.sort((a, b) => {
      const valA = a[key]
      const valB = b[key]
      const columnType = columnByKey[key]?.type

      if (columnType === 'sparkline') {
        const lastA = Array.isArray(valA) && valA.length > 0 ? Number(valA[valA.length - 1]) : Number.NEGATIVE_INFINITY
        const lastB = Array.isArray(valB) && valB.length > 0 ? Number(valB[valB.length - 1]) : Number.NEGATIVE_INFINITY
        if (Number.isFinite(lastA) && Number.isFinite(lastB)) {
          return direction === 'asc' ? lastA - lastB : lastB - lastA
        }
      }

      // Nulls al final
      if (valA == null && valB == null) return 0
      if (valA == null) return 1
      if (valB == null) return -1

      // Numérico
      if (typeof valA === 'number' && typeof valB === 'number') {
        return direction === 'asc' ? valA - valB : valB - valA
      }

      // Texto
      const strA = String(valA).toLowerCase()
      const strB = String(valB).toLowerCase()
      const cmp = strA.localeCompare(strB, 'es')
      return direction === 'asc' ? cmp : -cmp
    })

    return sorted
  }, [filteredData, sortConfig, columnByKey])

  // --- PAGINACIÓN ---
  const totalPages = Math.max(1, Math.ceil(sortedData.length / pageSize))
  const paginatedData = useMemo(() => {
    const start = page * pageSize
    return sortedData.slice(start, start + pageSize)
  }, [sortedData, page, pageSize])

  // Reset page on filter change
  React.useEffect(() => { setPage(0) }, [search, sortConfig, pageSize])

  React.useEffect(() => {
    setPage((current) => Math.min(current, Math.max(0, totalPages - 1)))
  }, [totalPages])

  React.useEffect(() => {
    const viewport = tableViewportRef.current
    if (!viewport) return

    const updateViewportHeight = () => {
      const nextHeight = viewport.clientHeight || 420
      setViewportHeight(nextHeight)
    }

    updateViewportHeight()

    const observer = typeof ResizeObserver !== 'undefined'
      ? new ResizeObserver(() => updateViewportHeight())
      : null

    observer?.observe(viewport)

    return () => observer?.disconnect()
  }, [viewMode, isWidget, pageSize])

  const virtualizationEnabled = paginatedData.length > VIRTUALIZATION_THRESHOLD
  const visibleCount = Math.max(12, Math.ceil(viewportHeight / VIRTUAL_ROW_HEIGHT) + 8)
  const virtualStart = virtualizationEnabled
    ? Math.max(0, Math.floor(scrollTop / VIRTUAL_ROW_HEIGHT) - 4)
    : 0
  const virtualEnd = virtualizationEnabled
    ? Math.min(paginatedData.length, virtualStart + visibleCount)
    : paginatedData.length
  const visibleRows = virtualizationEnabled
    ? paginatedData.slice(virtualStart, virtualEnd)
    : paginatedData
  const topSpacerHeight = virtualizationEnabled ? virtualStart * VIRTUAL_ROW_HEIGHT : 0
  const bottomSpacerHeight = virtualizationEnabled
    ? Math.max(0, (paginatedData.length - virtualEnd) * VIRTUAL_ROW_HEIGHT)
    : 0

  // --- HANDLERS ---
  const handleSort = useCallback((colKey: string) => {
    setSortConfig(prev => ({
      key: colKey,
      direction: prev.key === colKey && prev.direction === 'desc' ? 'asc' : 'desc'
    }))
  }, [])

  const getSortIcon = (colKey: string) => {
    if (sortConfig.key !== colKey) return <ArrowUpDown className="h-3 w-3 opacity-30" />
    return sortConfig.direction === 'asc'
      ? <ArrowUp className="h-3 w-3 text-primary" />
      : <ArrowDown className="h-3 w-3 text-primary" />
  }

  // --- FORMATO ---
  // Heurística de timestamp: keys con indicadores de fecha o valores numéricos > 1e11 (milisegundos epoch)
  const DATE_KEY_HINTS = /fecha|fec|date|created|updated|timestamp/i

  const formatCell = (value: any, col: SmartTableColumn): string => {
    if (value == null) return '—'

    // Detección de timestamps crudos (Bug 1: Amnesia de formateo de fechas)
    if (typeof value === 'number' && (DATE_KEY_HINTS.test(col.key) || value > 1e11)) {
      try {
        const d = new Date(value)
        if (!isNaN(d.getTime())) {
          return d.toLocaleDateString('es-PE', { day: '2-digit', month: '2-digit', year: 'numeric' })
        }
      } catch { /* fallthrough a formato numérico normal */ }
    }

    if (col.type === 'percentage') {
      return typeof value === 'number' ? `${value.toFixed(1)}%` : String(value)
    }
    if (col.type === 'number') {
      return typeof value === 'number'
        ? value.toLocaleString('es-PE', { minimumFractionDigits: 0, maximumFractionDigits: 2 })
        : String(value)
    }
    return String(value)
  }

  // --- DATA BAR STYLE ---
  const getDataBarStyle = (value: any, col: SmartTableColumn): React.CSSProperties => {
    if (col.type !== 'number' || !col.bar) return {}
    const max = columnMaxValues[col.key]
    if (!max || typeof value !== 'number') return {}
    const pct = Math.min((Math.abs(value) / max) * 100, 100)
    return {
      background: `linear-gradient(to right, hsl(var(--primary) / 0.12) ${pct}%, transparent ${pct}%)`
    }
  }

  // --- HEATMAP STYLE ---
  const getHeatmapStyle = (value: any, col: SmartTableColumn): React.CSSProperties => {
    if (col.type !== 'percentage' || !col.heatmap) return {}
    if (typeof value !== 'number') return {}

    const maxAbs = columnMaxValues[col.key] || 1
    const normalized = Math.min(Math.abs(value) / maxAbs, 1)

    // Verde para positivo, Rojo para negativo, intensidad proporcional
    if (value >= 0) {
      return {
        backgroundColor: `hsla(142, 70%, 45%, ${normalized * 0.25})`,
        color: normalized > 0.5 ? 'hsl(142, 70%, 30%)' : undefined
      }
    } else {
      return {
        backgroundColor: `hsla(0, 70%, 45%, ${normalized * 0.25})`,
        color: normalized > 0.5 ? 'hsl(0, 70%, 30%)' : undefined
      }
    }
  }

  const clearSearch = useCallback(() => {
    setSearch('')
    setPage(0)
  }, [])

  const handlePageSizeChange = useCallback((nextPageSize: number) => {
    setPageSize(nextPageSize)
    setPage(0)
    setScrollTop(0)
    if (tableViewportRef.current) {
      tableViewportRef.current.scrollTop = 0
    }
  }, [])

  const renderTable = () => (
    <>
      <div className="relative mb-3 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div className="relative flex-1 max-w-xl">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <input
            type="text"
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Buscar en la tabla..."
            className="w-full h-9 pl-9 pr-10 bg-muted/40 border border-border/50 rounded-lg text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary/50 transition-all"
          />
          {search.trim() && (
            <button
              type="button"
              onClick={clearSearch}
              className="absolute right-2 top-1/2 -translate-y-1/2 rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
              title="Limpiar búsqueda"
            >
              <X className="h-4 w-4" />
            </button>
          )}
        </div>

        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          {search !== deferredSearch && (
            <span className="inline-flex items-center gap-1 rounded-lg border border-border/60 bg-muted/20 px-3 py-2">
              <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
              Refinando tabla...
            </span>
          )}
          {sortedData.length > PAGE_SIZE_OPTIONS[0] && (
            <div className="inline-flex items-center gap-2 rounded-lg border border-border/60 bg-muted/20 px-3 py-2">
              <SlidersHorizontal className="h-3.5 w-3.5" />
              <span>Filas por página</span>
              <select
                value={pageSize}
                onChange={(event) => handlePageSizeChange(Number(event.target.value))}
                className="bg-transparent text-foreground outline-none"
              >
                {PAGE_SIZE_OPTIONS.map((size) => (
                  <option key={size} value={size}>
                    {size} filas
                  </option>
                ))}
              </select>
            </div>
          )}
          <span className="hidden sm:inline">
            {sortedData.length.toLocaleString('es-PE')} registros visibles
          </span>
        </div>
      </div>

      <div className="overflow-x-auto border border-border/50 rounded-lg shadow-sm flex-1 min-h-0">
        <div
          ref={tableViewportRef}
          className={isWidget ? "h-full overflow-y-auto" : "max-h-[500px] overflow-y-auto"}
          onScroll={(event) => setScrollTop(event.currentTarget.scrollTop)}
        >
          <table className="w-full text-sm text-left">
            <thead className="bg-card text-muted-foreground sticky top-0 z-10">
              <tr className="border-b border-border">
                {normalizedColumns.map(col => (
                  <th
                    key={col.key}
                    className="py-3 px-4 font-medium text-xs uppercase tracking-wider whitespace-nowrap cursor-pointer select-none hover:bg-muted/70 transition-colors"
                    onClick={() => handleSort(col.key)}
                  >
                    <div className="flex items-center gap-1.5">
                      <span>{col.label}</span>
                      {getSortIcon(col.key)}
                    </div>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-border/50">
              {topSpacerHeight > 0 && (
                <tr aria-hidden="true">
                  <td colSpan={normalizedColumns.length} style={{ height: `${topSpacerHeight}px`, padding: 0 }} />
                </tr>
              )}
              {visibleRows.map((row, rowIdx) => (
                <tr
                  key={`${page}-${virtualStart + rowIdx}`}
                  className="hover:bg-muted/30 transition-colors"
                >
                  {normalizedColumns.map(col => {
                    if (col.type === 'sparkline') {
                      return (
                        <td
                          key={col.key}
                          className="py-2.5 px-4 whitespace-nowrap tabular-nums"
                        >
                          <SparklineCell values={Array.isArray(row[col.key]) ? row[col.key] : []} />
                        </td>
                      )
                    }

                    return (
                      <td
                        key={col.key}
                        className="py-2.5 px-4 whitespace-nowrap tabular-nums"
                        style={{
                          ...getDataBarStyle(row[col.key], col),
                          ...getHeatmapStyle(row[col.key], col)
                        }}
                      >
                        <span className={col.type === 'number' ? 'font-medium' : ''}>
                          {formatCell(row[col.key], col)}
                        </span>
                      </td>
                    )
                  })}
                </tr>
              ))}
              {bottomSpacerHeight > 0 && (
                <tr aria-hidden="true">
                  <td colSpan={normalizedColumns.length} style={{ height: `${bottomSpacerHeight}px`, padding: 0 }} />
                </tr>
              )}
              {paginatedData.length === 0 && (
                <tr>
                  <td
                    colSpan={normalizedColumns.length}
                    className="py-10 text-center"
                  >
                    <div className="flex flex-col items-center gap-2 text-muted-foreground">
                      <Table2 className="h-8 w-8 opacity-50" />
                      <p className="font-medium">No se encontraron resultados.</p>
                      <p className="text-xs">Prueba otro término o limpia la búsqueda.</p>
                    </div>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {totalPages > 1 && (
        <div className="flex flex-col gap-2 mt-3 text-sm text-muted-foreground sm:flex-row sm:items-center sm:justify-between">
          <span>
            Mostrando {sortedData.length === 0 ? 0 : page * pageSize + 1}–{Math.min((page + 1) * pageSize, sortedData.length)} de {sortedData.length}
          </span>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              className="h-8 text-xs"
              disabled={page === 0}
              onClick={() => setPage(p => Math.max(0, p - 1))}
            >
              ← Anterior
            </Button>
            <span className="text-xs font-medium px-2">
              {page + 1} / {totalPages}
            </span>
            <Button
              variant="outline"
              size="sm"
              className="h-8 text-xs"
              disabled={page >= totalPages - 1}
              onClick={() => setPage(p => Math.min(totalPages - 1, p + 1))}
            >
              Siguiente →
            </Button>
          </div>
        </div>
      )}

      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground/60">
        <span>{normalizedData.length} registros base</span>
        <span>•</span>
        <span>Orden persistido por archivo</span>
        <span>•</span>
        <span>{virtualizationEnabled ? 'Virtualización activa' : 'Render completo del bloque actual'}</span>
      </div>
    </>
  )

  const modeSwitch = originalChartOption && !presentationMode ? (
    <div className="flex items-center gap-2">
      <Button
        variant={viewMode === 'table' ? "default" : "outline"}
        size="sm"
        className="h-8 text-xs gap-1.5"
        onClick={() => setViewMode('table')}
      >
        <Table2 className="h-3.5 w-3.5" />
        Tabla
      </Button>
      <Button
        variant={viewMode === 'hybrid' ? "default" : "outline"}
        size="sm"
        className="h-8 text-xs gap-1.5"
        onClick={() => setViewMode('hybrid')}
      >
        <Rows3 className="h-3.5 w-3.5" />
        Híbrida
      </Button>
      <Button
        variant={viewMode === 'chart' ? "default" : "outline"}
        size="sm"
        className="h-8 text-xs gap-1.5"
        onClick={() => setViewMode('chart')}
      >
        <BarChart3 className="h-3.5 w-3.5" />
        Gráfico
      </Button>
    </div>
  ) : null

  const chartPanel = originalChartOption ? (
    <div className="flex-1 min-h-[260px]">
      <ChartsReport
        option={originalChartOption}
        title=""
        onSave={onSave}
        onChartClick={onChartClick}
        isWidget={true}
        interactionMode="filter"
        hideModeSwitch={true}
        toolbarPrefix={modeSwitch}
        presentationMode={presentationMode}
      />
    </div>
  ) : null

  // --- TOGGLE: MODO GRÁFICO ---
  if (viewMode === 'chart' && originalChartOption) {
    // isWidget: sin Card envolvente (GridWidget ya provee el contenedor visual)
    const chartContent = (
      <>
        {!isWidget && (
          <div className="mb-4 flex items-center justify-between">
            <h3 className="text-lg font-semibold text-foreground pr-12 truncate">
              {title}
            </h3>
            <div className="flex items-center gap-2">
              <Button variant="ghost" size="icon" className="h-8 w-8" onClick={onSave} title="Guardar">
                <SaveIcon className="h-4 w-4" />
              </Button>
            </div>
          </div>
        )}
        {chartPanel}
      </>
    );

    if (isWidget) {
      return <div className="h-full flex flex-col">{chartContent}</div>;
    }
    return (
      <div className="mt-6 min-w-0 overflow-hidden h-full">
        <Card className="p-6 relative h-full flex flex-col">
          {chartContent}
        </Card>
      </div>
    )
  }

  if (!prefsHydrated && isBrowser) {
    const skeletonInner = (
      <div className="space-y-3 animate-pulse">
        <div className="flex items-center justify-between gap-4">
          <div className="h-7 w-56 rounded-md bg-muted/60" />
          <div className="h-8 w-48 rounded-md bg-muted/60" />
        </div>
        <div className="h-9 w-full rounded-lg bg-muted/50" />
        <div className="rounded-lg border border-border/50">
          <div className="h-11 border-b border-border/50 bg-muted/30" />
          <div className="space-y-2 p-3">
            {Array.from({ length: 6 }).map((_, index) => (
              <div key={index} className="h-10 rounded-md bg-muted/35" />
            ))}
          </div>
        </div>
      </div>
    )

    if (isWidget) {
      return <div className="h-full flex flex-col">{skeletonInner}</div>
    }

    return (
      <div className="mt-6 min-w-0 overflow-hidden h-full">
        <Card className="p-6 relative flex flex-col h-full bg-card/50 backdrop-blur-sm">
          {skeletonInner}
        </Card>
      </div>
    )
  }

  // --- RENDER: MODO TABLA/HÍBRIDA ---
  const showHybrid = viewMode === 'hybrid' && Boolean(originalChartOption)
  const tableInner = (
    <>
      {/* Header */}
      <div className={`flex items-center justify-between gap-4 ${isWidget ? "mb-2" : "mb-4"}`}>
        {!isWidget ? (
          <h3 className="text-lg font-semibold text-foreground truncate">
            📋 {title || 'Smart Table'}
          </h3>
        ) : (
          <div />
        )}
        <div className="flex items-center gap-2 flex-shrink-0">
          {modeSwitch}
          {!isWidget && !presentationMode && (
            <Button variant="ghost" size="icon" className="h-8 w-8" onClick={onSave} title="Guardar">
              <SaveIcon className="h-4 w-4" />
            </Button>
          )}
        </div>
      </div>
      {showHybrid && chartPanel && (
        <div className="mb-4 rounded-xl border border-border/60 bg-muted/10 p-3">
          <div className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Vista híbrida
          </div>
          {chartPanel}
        </div>
      )}

      {renderTable()}
    </>
  );

  // isWidget: renderizar contenido neto sin Card/mt-6 (GridWidget ya es el contenedor visual)
  if (isWidget) {
    return <div className="h-full flex flex-col">{tableInner}</div>;
  }

  return (
    <div className="mt-6 min-w-0 overflow-hidden h-full">
      <Card className="p-6 relative flex flex-col h-full bg-card/50 backdrop-blur-sm">
        {tableInner}
      </Card>
    </div>
  )
}

export const SmartTable = React.memo(SmartTableComponent)
