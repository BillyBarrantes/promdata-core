"use client";

import React from 'react';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { format } from 'date-fns';
import { es } from 'date-fns/locale';
import { Maximize2, MessageSquare, MoreHorizontal, Sparkles, Trash2 } from 'lucide-react';
import { SavedReport, globalFiltersAtom } from '@/lib/state';
import { AnalysisReport } from '@/components/analysis-report';
import { SmartTable } from '@/components/smart-table';
import dynamic from 'next/dynamic';
import { useAtomValue } from 'jotai';
import * as duckdbEngine from '@/lib/duckdb-engine';
import { buildReactiveChartOption, inferQueryContractFromChartOption, WidgetQueryContract } from '@/lib/dashboard-crossfilter';
import { buildExecutiveWidgetSnapshot, ExecutiveNarrativeWidgetSnapshot } from '@/lib/dashboard-narrative';
import { getScopedLocalPerfAverage, startLocalPerf } from '@/lib/local-performance';

const ChartsReport = dynamic(() => import('@/components/charts-report').then(mod => mod.ChartsReport), {
  loading: () => <div className="h-full w-full flex items-center justify-center bg-muted/50">Cargando gráfico...</div>,
  ssr: false
});

interface GridWidgetProps {
  report: SavedReport;
  onDelete: (id: string) => void;
  onAnalyze: (report: SavedReport) => void;
  onChartClick?: (
    params: any,
    tableName?: string,
    sourceFileId?: string,
    widgetMeta?: {
      reportId?: string;
      chartOption?: any;
      contract?: WidgetQueryContract | null;
    }
  ) => void;
  onNarrativeSnapshotChange?: (reportId: string, snapshot: ExecutiveNarrativeWidgetSnapshot) => void;
  presentationMode?: boolean;
  onRequestFocus?: (report: SavedReport) => void;
  recomputePriority?: number;
}

const IMMEDIATE_RECOMPUTE_WIDGET_COUNT = 2;
const RECOMPUTE_STAGGER_MS = 18;
const RECOMPUTE_STAGGER_MAX_MS = 144;
const HEAVY_WIDGET_THRESHOLD_MS = 160;
const MEDIUM_WIDGET_THRESHOLD_MS = 90;
const HEAVY_WIDGET_EXTRA_DELAY_MS = 72;
const MEDIUM_WIDGET_EXTRA_DELAY_MS = 30;

const MONTH_NAMES_FALLBACK: Record<string, number> = {
  ene: 1, enero: 1, jan: 1, january: 1,
  feb: 2, febrero: 2, february: 2,
  mar: 3, marzo: 3, march: 3,
  abr: 4, abril: 4, apr: 4, april: 4,
  may: 5, mayo: 5,
  jun: 6, junio: 6, june: 6,
  jul: 7, julio: 7, july: 7,
  ago: 8, agosto: 8, aug: 8, august: 8,
  sep: 9, sept: 9, set: 9, septiembre: 9, setiembre: 9, september: 9,
  oct: 10, octubre: 10, october: 10,
  nov: 11, noviembre: 11, november: 11,
  dic: 12, diciembre: 12, dec: 12, december: 12,
};

function normalizeFilterToken(val: string): string {
  return val
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .trim();
}

function rowMatchesFilterValue(val: unknown, targetStr: string): boolean {
  if (val === null || val === undefined) return false;
  if (typeof val === 'object' && !(val instanceof Date)) return false;

  const targetNormalized = normalizeFilterToken(targetStr);
  if (!targetNormalized) return true;

  const targetMonthNumber = MONTH_NAMES_FALLBACK[targetNormalized];

  // 1. Direct equality (raw or string)
  const cellStr = String(val).trim();
  const cellNormalized = normalizeFilterToken(cellStr);
  if (cellNormalized === targetNormalized) return true;

  // 2. Contains match if string length >= 3
  if (targetNormalized.length >= 3 && cellNormalized.includes(targetNormalized)) {
    return true;
  }

  // 2b. Singular/Plural matching (e.g. "ingresos" vs "ingreso")
  if (
    (targetNormalized.endsWith('s') && targetNormalized.slice(0, -1) === cellNormalized) ||
    (cellNormalized.endsWith('s') && cellNormalized.slice(0, -1) === targetNormalized)
  ) {
    return true;
  }

  // 3. Temporal matching if target is a month
  if (targetMonthNumber !== undefined) {
    if (val instanceof Date) {
      const mUtc = val.getUTCMonth() + 1;
      const mLoc = val.getMonth() + 1;
      return mUtc === targetMonthNumber || mLoc === targetMonthNumber;
    }
    // Check if cell is a month token itself
    if (MONTH_NAMES_FALLBACK[cellNormalized] === targetMonthNumber) {
      return true;
    }
    // Check if cell is a month-year token like "Abr-2026", "2026-Abr"
    const cellMonthYearMatch = cellNormalized.match(/^([a-z]+)[-_](\d{4})$/) || cellNormalized.match(/^(\d{4})[-_]([a-z]+)$/);
    if (cellMonthYearMatch) {
      const token = cellMonthYearMatch[1]?.match(/^[a-z]+$/) ? cellMonthYearMatch[1] : cellMonthYearMatch[2];
      if (token && MONTH_NAMES_FALLBACK[token] === targetMonthNumber) {
        return true;
      }
      return false;
    }
    // Check if cell is date-like "2025-01-15", "01/2025", etc.
    const isoMonthMatch = cellStr.match(/^\d{4}[-/](\d{1,2})([-/]\d{1,2})?$/);
    if (isoMonthMatch && Number(isoMonthMatch[1]) === targetMonthNumber) {
      return true;
    }
    const ddmmyyyyMatch = cellStr.match(/^(\d{1,2})[-/](\d{1,2})[-/]\d{4}$/);
    if (ddmmyyyyMatch && Number(ddmmyyyyMatch[2]) === targetMonthNumber) {
      return true;
    }
    const parsed = Date.parse(cellStr);
    if (!Number.isNaN(parsed)) {
      const d = new Date(parsed);
      if (d.getUTCMonth() + 1 === targetMonthNumber || d.getMonth() + 1 === targetMonthNumber) {
        return true;
      }
    }
  }

  // 3b. Temporal matching if target is YYYY-MM or YYYY
  const ymMatch = targetStr.match(/^(\d{4})[-/](\d{1,2})$/);
  if (ymMatch) {
    const tYear = Number(ymMatch[1]);
    const tMonth = Number(ymMatch[2]);
    if (val instanceof Date) {
      const vYearUtc = val.getUTCFullYear();
      const vMonthUtc = val.getUTCMonth() + 1;
      const vYearLoc = val.getFullYear();
      const vMonthLoc = val.getMonth() + 1;
      if ((vYearUtc === tYear && vMonthUtc === tMonth) || (vYearLoc === tYear && vMonthLoc === tMonth)) {
        return true;
      }
    }
    const cellYm = cellStr.match(/^(\d{4})[-/](\d{1,2})/);
    if (cellYm && Number(cellYm[1]) === tYear && Number(cellYm[2]) === tMonth) {
      return true;
    }
  }

  return false;
}

function filterRowsInMemory(
  rows: Record<string, unknown>[],
  scopedFilters: Record<string, string>
): Record<string, unknown>[] {
  const filterEntries = Object.entries(scopedFilters).filter(
    ([k, v]) => !k.startsWith('__') && v !== null && v !== undefined && String(v).trim() !== ''
  );
  if (filterEntries.length === 0 || rows.length === 0) return rows;

  return rows.filter((row) => {
    return filterEntries.every(([key, filterVal]) => {
      const targetStr = String(filterVal).trim();
      if (!targetStr) return true;

      if (key === 'global_cross_filter' || key === 'global_chart_filter') {
        // Search across any column in the row
        return Object.values(row).some((cellVal) => rowMatchesFilterValue(cellVal, targetStr));
      }

      // Column-specific filter
      const cellVal = row[key];
      return rowMatchesFilterValue(cellVal, targetStr);
    });
  });
}

export const GridWidget = React.memo(function GridWidget({
  report,
  onDelete,
  onAnalyze,
  onChartClick,
  onNarrativeSnapshotChange,
  presentationMode = false,
  onRequestFocus,
  recomputePriority = 0,
}: GridWidgetProps) {
  const { content } = report;
  const type = content.type;
  const innerContent = content.content;
  const visualGovernance = type === 'chart'
    ? (innerContent as any)?.visual_governance
    : (innerContent as any)?.original_chart_option?.visual_governance || (innerContent as any)?.visual_governance;
  const recommendedLabel = (() => {
    const recommended = visualGovernance?.recommended_label;
    const applied = visualGovernance?.applied_label;
    if (!recommended || recommended === applied) return null;
    return String(recommended);
  })();
  const globalFilters = useAtomValue(globalFiltersAtom);
  const widgetPerfScopeKey = React.useMemo(() => `report:${report.id}`, [report.id]);
  const explicitContract = React.useMemo<WidgetQueryContract | null>(() => {
    if (type === 'chart') {
      return (innerContent?.query_contract as WidgetQueryContract | undefined) || null;
    }

    if (type === 'table') {
      return (
        (innerContent?.original_chart_option?.query_contract as WidgetQueryContract | undefined)
        || (innerContent?.query_contract as WidgetQueryContract | undefined)
        || null
      );
    }

    return null;
  }, [type, innerContent]);
  const [resolvedChartOption, setResolvedChartOption] = React.useState<any>(type === 'chart' ? innerContent : innerContent?.original_chart_option);
  const [resolvedTableData, setResolvedTableData] = React.useState<any[]>(type === 'table' ? innerContent?.data || [] : []);
  const [resolvedMetrics, setResolvedMetrics] = React.useState<Record<string, number> | null>(null);
  const [resolvedContract, setResolvedContract] = React.useState<WidgetQueryContract | null>(explicitContract);
  const [headerActionsEl, setHeaderActionsEl] = React.useState<HTMLDivElement | null>(null);

  React.useEffect(() => {
    setResolvedContract(explicitContract);
  }, [explicitContract, report.id]);

  React.useEffect(() => {
    let cancelled = false;
    let idleCallbackId: number | null = null;
    let deferredTimerId: ReturnType<typeof setTimeout> | null = null;
    const baseChartOption = type === 'chart' ? innerContent : innerContent?.original_chart_option;
    const summarizeContract = (contract: WidgetQueryContract | null | undefined) => {
      if (!contract) return null;
      return {
        metric: contract.metric || contract.value_column || contract.metrics?.[0] || null,
        dimension: contract.dimension || null,
        groupBy: contract.group_by || [],
        aggregation: contract.aggregation || 'sum',
      };
    };

    const resolveContractForRows = (
      baseOption: any,
      rows: Record<string, unknown>[]
    ): WidgetQueryContract | null => {
      if (explicitContract) {
        console.log("🕵️ [DASHBOARD CONTRACT] source=explicit", {
          reportId: report.id,
          reportTitle: report.title,
          widgetType: type,
          rows: rows.length,
          contract: summarizeContract(explicitContract),
        });
        return explicitContract;
      }

      if (resolvedContract) {
        console.log("🕵️ [DASHBOARD CONTRACT] source=cached", {
          reportId: report.id,
          reportTitle: report.title,
          widgetType: type,
          rows: rows.length,
          contract: summarizeContract(resolvedContract),
        });
        return resolvedContract;
      }

      const inferred = inferQueryContractFromChartOption(baseOption, rows);
      if (inferred && !cancelled) {
        setResolvedContract(inferred);
        console.log("🕵️ [DASHBOARD CONTRACT] source=inferred", {
          reportId: report.id,
          reportTitle: report.title,
          type,
          rows: rows.length,
          contract: summarizeContract(inferred),
        });
      }
      return inferred;
    };

    const recomputeWidget = async () => {
      const finishPerf = startLocalPerf('dashboard_widget_recompute', {
        reportId: report.id,
        widgetType: type,
        recomputePriority,
        presentationMode,
      }, widgetPerfScopeKey);
      const filterEntries = Object.entries(globalFilters || {}).filter(([key, value]) => {
        if (key.startsWith('__')) return false;
        if (value === null || value === undefined) return false;
        return String(value).trim() !== '';
      });
      const scopedFilters = Object.fromEntries(filterEntries);
      const hasGlobalFilters = filterEntries.length > 0;
      const scopedFileId = typeof globalFilters?.__scope_file_id === 'string'
        ? globalFilters.__scope_file_id.trim()
        : '';
      const widgetFileId = typeof report.file_id === 'string' ? report.file_id.trim() : '';
      const outOfScopeByFile = Boolean(scopedFileId) && (!widgetFileId || scopedFileId !== widgetFileId);

      if (!hasGlobalFilters) {
        setResolvedChartOption(baseChartOption);
        setResolvedMetrics(null);
        if (type === 'table') {
          setResolvedTableData(innerContent?.data || []);
        }
        finishPerf({
          hasGlobalFilters: false,
          resolvedRows: type === 'table' ? (innerContent?.data || []).length : 0,
          resetToBase: true,
        });
        return;
      }

      if (outOfScopeByFile) {
        setResolvedChartOption(baseChartOption);
        setResolvedMetrics(null);
        if (type === 'table') {
          setResolvedTableData(innerContent?.data || []);
        }
        finishPerf({
          hasGlobalFilters: true,
          outOfScopeByFile: true,
          resolvedRows: type === 'table' ? (innerContent?.data || []).length : 0,
          resetToBase: true,
        });
        return;
      }

      const sourceReportId = typeof globalFilters?.__source_report_id === 'string'
        ? globalFilters.__source_report_id.trim()
        : '';
      const isSourceWidget = Boolean(sourceReportId) && sourceReportId === report.id;

      if (isSourceWidget) {
        // [Anti-Self-Filter Guard]: El widget emisor mantiene su baseChartOption
        // para que ECharts mantenga la visibilidad de todas las barras y resalte la seleccionada.
        setResolvedChartOption(baseChartOption);
        setResolvedMetrics(null);
        if (type === 'table') {
          setResolvedTableData(innerContent?.data || []);
        }
        finishPerf({
          hasGlobalFilters: true,
          isSourceWidget: true,
          resolvedRows: type === 'table' ? (innerContent?.data || []).length : 0,
          resetToBase: true,
        });
        return;
      }

      const granularArrow = type === 'chart'
        ? innerContent?.granular_arrow
        : innerContent?.granular_arrow || innerContent?.original_chart_option?.granular_arrow;

      const rawRows: Record<string, unknown>[] = Array.isArray(innerContent?.visual_source_payload?.rows)
        ? innerContent.visual_source_payload.rows
        : Array.isArray(innerContent?.data)
          ? innerContent.data
          : Array.isArray(innerContent?.original_chart_option?.visual_source_payload?.rows)
            ? innerContent.original_chart_option.visual_source_payload.rows
            : Array.isArray(innerContent?.original_chart_option?.data)
              ? innerContent.original_chart_option.data
              : [];

      let filteredRows: Record<string, unknown>[] = [];
      let executionMode: 'duckdb' | 'memory_fallback' = 'duckdb';

      if (granularArrow) {
        const tableName = `dashboard_widget_${report.id.replace(/-/g, '_')}`;
        try {
          await duckdbEngine.loadArrowData(granularArrow, tableName);
          filteredRows = await duckdbEngine.crossFilter(scopedFilters, tableName);
          console.log("🕵️ [DASHBOARD FILTER RESULT]", {
            reportId: report.id,
            reportTitle: report.title,
            widgetType: type,
            tableName,
            globalFilters: scopedFilters,
            filteredRows: filteredRows.length,
          });
        } catch (crossFilterError) {
          console.warn("⚠️ [DASHBOARD] Error aplicando duckdbEngine.crossFilter, activando fallback en memoria", {
            error: crossFilterError,
            tableName,
            globalFilters: scopedFilters,
            reportId: report.id,
            widgetType: type,
          });
          executionMode = 'memory_fallback';
        }
      } else {
        executionMode = 'memory_fallback';
      }

      // Capa 2: Fallback en memoria si no hay granular_arrow, si DuckDB falló o si DuckDB devolvió 0 filas pero hay datos en memoria
      if (executionMode === 'memory_fallback' || (filteredRows.length === 0 && rawRows.length > 0)) {
        if (rawRows.length > 0) {
          const memoryFiltered = filterRowsInMemory(rawRows, scopedFilters as Record<string, string>);
          if (memoryFiltered.length > 0 || filteredRows.length === 0) {
            filteredRows = memoryFiltered;
            executionMode = 'memory_fallback';
            console.log("🧠 [DASHBOARD MEMORY FALLBACK RESULT]", {
              reportId: report.id,
              reportTitle: report.title,
              widgetType: type,
              globalFilters: scopedFilters,
              totalRawRows: rawRows.length,
              filteredRows: filteredRows.length,
            });
          }
        } else if (!granularArrow) {
          console.warn("⚠️ [DASHBOARD] Widget sin granular_arrow ni filas en memoria; se omite recomputación reactiva", {
            reportId: report.id,
            type,
          });
          finishPerf({
            hasGlobalFilters: true,
            skipped: true,
            reason: 'missing_granular_arrow_and_raw_rows',
          });
          return;
        }
      }

      if (cancelled) return;

      try {
        if (type === 'chart') {
          const contract = resolveContractForRows(innerContent, filteredRows);
          if (contract) {
            const nextChartOption = buildReactiveChartOption(innerContent, filteredRows, contract);
            setResolvedChartOption(nextChartOption);
            finishPerf({
              hasGlobalFilters: true,
              filteredRows: filteredRows.length,
              chartReactive: true,
              executionMode,
            });
          } else {
            console.warn("⚠️ [DASHBOARD] No se pudo resolver query_contract para widget chart", {
              reportId: report.id,
              filteredRows: filteredRows.length,
            });
            finishPerf({
              hasGlobalFilters: true,
              filteredRows: filteredRows.length,
              chartReactive: false,
              reason: 'missing_query_contract',
            });
          }
          return;
        }

        if (type === 'table') {
          setResolvedTableData(filteredRows);

          const originalChartOption = innerContent?.original_chart_option;
          // Para SmartTable en modo "Ver Gráfico", el contrato autoritativo es el del gráfico original.
          const contract = originalChartOption
            ? resolveContractForRows(originalChartOption, filteredRows)
            : null;
          if (originalChartOption && contract) {
            const nextChartOption = buildReactiveChartOption(originalChartOption, filteredRows, contract);
            setResolvedChartOption(nextChartOption);
            finishPerf({
              hasGlobalFilters: true,
              filteredRows: filteredRows.length,
              tableReactive: true,
              executionMode,
            });
          } else if (originalChartOption) {
            console.warn("⚠️ [DASHBOARD] No se pudo resolver query_contract para SmartTable híbrida", {
              reportId: report.id,
              filteredRows: filteredRows.length,
            });
            finishPerf({
              hasGlobalFilters: true,
              filteredRows: filteredRows.length,
              tableReactive: false,
              reason: 'missing_query_contract',
            });
          } else {
            finishPerf({
              hasGlobalFilters: true,
              filteredRows: filteredRows.length,
              tableReactive: false,
              reason: 'no_original_chart_option',
              executionMode,
            });
          }
        }

        if (type === 'metrics') {
          if (filteredRows.length > 0 && innerContent && typeof innerContent === 'object') {
            const filtered: Record<string, number> = {};
            const originalMetrics: Record<string, number> = {};
            for (const [key, val] of Object.entries(innerContent)) {
              if (typeof val === 'number') originalMetrics[key] = val;
            }

            // Buscar columnas en filteredRows que coincidan con las keys de métricas
            const sampleRow = filteredRows[0];
            const columns = Object.keys(sampleRow || {});
            for (const metricKey of Object.keys(originalMetrics)) {
              const normalizedKey = metricKey.toLowerCase().replace(/[_\s]+/g, '');
              const matchingCol = columns.find(
                (col) => col.toLowerCase().replace(/[_\s]+/g, '') === normalizedKey
              );
              if (matchingCol) {
                filtered[metricKey] = filteredRows.reduce(
                  (sum, row) => sum + (typeof row[matchingCol] === 'number' ? row[matchingCol] : 0), 0
                );
              } else {
                filtered[metricKey] = originalMetrics[metricKey];
              }
            }
            setResolvedMetrics(filtered);
          } else {
            setResolvedMetrics(null);
          }
          finishPerf({
            hasGlobalFilters: true,
            filteredRows: filteredRows.length,
            metricsReactive: true,
            executionMode,
          });
          return;
        }
      } catch (error) {
        finishPerf({
          hasGlobalFilters: true,
          failed: true,
          error: error instanceof Error ? error.message : String(error),
        });
        console.error('Error recomputando widget con cross-filter global', error);
      }
    };

    const hasMeaningfulGlobalFilters = Object.entries(globalFilters || {}).some(([key, value]) => {
      if (key.startsWith('__')) return false;
      if (value === null || value === undefined) return false;
      return String(value).trim() !== '';
    });

    const historicalWidgetCostMs = getScopedLocalPerfAverage(
      'dashboard_widget_recompute',
      widgetPerfScopeKey
    );
    const adaptiveExtraDelay = historicalWidgetCostMs === null
      ? 0
      : historicalWidgetCostMs >= HEAVY_WIDGET_THRESHOLD_MS
        ? HEAVY_WIDGET_EXTRA_DELAY_MS
        : historicalWidgetCostMs >= MEDIUM_WIDGET_THRESHOLD_MS
          ? MEDIUM_WIDGET_EXTRA_DELAY_MS
          : 0;

    const shouldDeferRecompute =
      hasMeaningfulGlobalFilters &&
      !presentationMode &&
      (
        recomputePriority >= IMMEDIATE_RECOMPUTE_WIDGET_COUNT ||
        adaptiveExtraDelay > 0
      );

    if (shouldDeferRecompute) {
      const baseDelay = Math.max(
        0,
        recomputePriority - (IMMEDIATE_RECOMPUTE_WIDGET_COUNT - 1)
      ) * RECOMPUTE_STAGGER_MS;
      const staggerDelay = Math.min(
        RECOMPUTE_STAGGER_MAX_MS,
        baseDelay + adaptiveExtraDelay
      );

      deferredTimerId = setTimeout(() => {
        if (cancelled) return;

        if (typeof window !== 'undefined' && 'requestIdleCallback' in window) {
          idleCallbackId = window.requestIdleCallback(() => {
            if (cancelled) return;
            void recomputeWidget();
          }, { timeout: 220 });
          return;
        }

        void recomputeWidget();
      }, staggerDelay);
    } else {
      void recomputeWidget();
    }

    return () => {
      cancelled = true;
      if (deferredTimerId) {
        clearTimeout(deferredTimerId);
      }
      if (idleCallbackId !== null && typeof window !== 'undefined' && 'cancelIdleCallback' in window) {
        window.cancelIdleCallback(idleCallbackId);
      }
    };
  }, [report.file_id, report.id, type, innerContent, globalFilters, explicitContract, resolvedContract, presentationMode, recomputePriority, widgetPerfScopeKey]);

  React.useEffect(() => {
    if (!onNarrativeSnapshotChange) return;

    const snapshot = buildExecutiveWidgetSnapshot({
      report,
      chartOption: resolvedChartOption,
      tableData: resolvedTableData,
    });
    onNarrativeSnapshotChange(report.id, snapshot);
  }, [onNarrativeSnapshotChange, report, resolvedChartOption, resolvedTableData]);

  const renderContent = () => {
    switch (type) {
      case 'metrics':
        return <AnalysisReport data={{ metrics: resolvedMetrics || innerContent, tableData: [] }} onSave={() => {}} isWidget={true} />;
      case 'table':
        return (
          <div className="h-full overflow-hidden flex flex-col pt-1">
            <SmartTable 
               title={innerContent.title}
               columns={innerContent.columns}
               data={resolvedTableData}
               onSave={() => {}} 
               originalChartOption={resolvedChartOption}
               defaultViewMode={innerContent?.default_view_mode}
               fileId={report.file_id}
               onChartClick={(params) => onChartClick && onChartClick(params, innerContent.table_name, report.file_id, {
                 reportId: report.id,
                 chartOption: resolvedChartOption || innerContent,
                 contract: resolvedContract || explicitContract,
               })}
               isWidget={true}
               presentationMode={presentationMode}
               // FUTURE PHASE: usar granular_arrow/query_contract para abrir modal "Ver datos crudos".
            />
          </div>
        );
      case 'chart':
        return (
          <ChartsReport 
            option={resolvedChartOption || innerContent} 
            onSave={() => {}} 
            onChartClick={(params) => onChartClick && onChartClick(params, innerContent.table_name, report.file_id, {
              reportId: report.id,
              chartOption: resolvedChartOption || innerContent,
              contract: resolvedContract || explicitContract,
            })} 
            isWidget={true}
            interactionMode="filter"
            hideModeSwitch={presentationMode}
            hideVisualPicker={presentationMode}
            presentationMode={presentationMode}
            onDelete={() => onDelete(report.id)}
            headerPortalContainer={headerActionsEl}
          />
        );
      default:
        return (
          <pre className="bg-muted p-4 rounded-md overflow-auto text-xs h-full">
            {JSON.stringify(content, null, 2)}
          </pre>
        );
    }
  };

  return (
    <Card
      className="flex flex-col w-full h-full bg-card border border-border/40 rounded-xl overflow-hidden group shadow-[var(--cursor-shadow-md)] hover:shadow-[var(--cursor-shadow-lg)] transition-all duration-300 ease-[cubic-bezier(0.2,0,0,1)] gap-0 py-0"
      data-testid={`dashboard-widget-${report.id}`}
      data-widget-type={type}
      data-report-title={report.title}
      onDoubleClick={presentationMode && onRequestFocus ? () => onRequestFocus(report) : undefined}
    >
      {/* Header Interactivo (Drag Handle) */}
      <CardHeader className={[
        "flex flex-row items-center justify-between border-b border-border/15",
        presentationMode ? "p-2" : "px-3 py-1.5",
        presentationMode ? "" : "cursor-grab active:cursor-grabbing widget-drag-handle",
      ].join(" ")}>
        <div className="flex flex-col min-w-0 pr-2">
          <CardTitle className="text-[13px] font-medium leading-tight truncate text-foreground tracking-tight">
            {report.title}
          </CardTitle>
          <div className="mt-0.5 flex min-w-0 items-center gap-1.5">
            <span className="shrink-0 font-mono text-[10px] text-muted-foreground/70">
              {format(new Date(report.created_at), "d 'de' MMM, yy", { locale: es })}
            </span>
          </div>
        </div>
        
        {/* Controles Ocultos por defecto, visibles en Hover */}
        {presentationMode ? (
          onRequestFocus ? (
            <div className="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity duration-150">
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7 text-muted-foreground hover:text-foreground hover:bg-secondary/60 transition-colors duration-150"
                onClick={() => onRequestFocus(report)}
                title="Enfocar visual"
              >
                <Maximize2 className="h-3.5 w-3.5" />
              </Button>
            </div>
          ) : null
        ) : (
          <div 
            className="flex items-center gap-1 opacity-0 group-hover:opacity-100 has-[[data-state=open]]:opacity-100 transition-opacity duration-150 shrink-0 cursor-default"
            onMouseDown={(e) => e.stopPropagation()}
          >
            {!presentationMode && recommendedLabel && (
              <span
                className="inline-flex min-w-0 items-center gap-1 rounded-full bg-secondary px-1.5 py-px text-[10px] font-medium text-muted-foreground"
                title={`Recomendado: ${recommendedLabel}`}
              >
                <Sparkles className="h-2.5 w-2.5 shrink-0 text-accent" />
                <span className="max-w-[96px] truncate">{recommendedLabel}</span>
              </span>
            )}
            <Button 
              variant="ghost" 
              size="icon" 
              className="h-7 w-7 text-muted-foreground hover:text-foreground hover:bg-secondary/60 transition-colors duration-150" 
              onClick={() => onAnalyze(report)} 
              title="Continuar en Chat"
            >
              <MessageSquare className="h-3.5 w-3.5" />
            </Button>
            
            <div ref={setHeaderActionsEl} className="flex items-center gap-1" />

            {type !== 'chart' && (
              <Popover>
                <PopoverTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7 text-muted-foreground hover:text-foreground hover:bg-secondary/60 transition-colors duration-150"
                    title="Más opciones"
                    aria-label="Más opciones"
                  >
                    <MoreHorizontal className="h-4 w-4" />
                  </Button>
                </PopoverTrigger>
                <PopoverContent align="end" className="w-44 p-1.5">
                  <button
                    type="button"
                    onClick={() => onDelete(report.id)}
                    className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs text-destructive transition-colors hover:bg-destructive/10"
                  >
                    <Trash2 className="h-3.5 w-3.5 shrink-0" />
                    <span className="flex-1 truncate">Eliminar</span>
                  </button>
                </PopoverContent>
              </Popover>
            )}
          </div>
        )}
      </CardHeader>
      
      {/* Contenido (Canvas Completo) */}
      <CardContent className={[
        "flex-1 min-h-0 relative overflow-hidden flex flex-col",
        presentationMode ? "p-1" : "p-1 sm:p-1.5",
      ].join(" ")}>
        {renderContent()}
      </CardContent>
    </Card>
  );
});
