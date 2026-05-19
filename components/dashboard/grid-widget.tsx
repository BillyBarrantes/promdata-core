"use client";

import React from 'react';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { format } from 'date-fns';
import { es } from 'date-fns/locale';
import { GripHorizontal, Maximize2, MessageSquare, Trash2 } from 'lucide-react';
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
  onChartClick?: (params: any, tableName?: string, sourceFileId?: string) => void;
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
  const [resolvedContract, setResolvedContract] = React.useState<WidgetQueryContract | null>(explicitContract);

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

      const granularArrow = type === 'chart'
        ? innerContent?.granular_arrow
        : innerContent?.granular_arrow || innerContent?.original_chart_option?.granular_arrow;

      if (!granularArrow) {
        console.warn("⚠️ [DASHBOARD] Widget sin granular_arrow; se omite recomputación reactiva", {
          reportId: report.id,
          type,
        });
        finishPerf({
          hasGlobalFilters: true,
          skipped: true,
          reason: 'missing_granular_arrow',
        });
        return;
      }

      const tableName = `dashboard_widget_${report.id.replace(/-/g, '_')}`;

      try {
        await duckdbEngine.loadArrowData(granularArrow, tableName);

        let filteredRows: Record<string, unknown>[] = [];
        try {
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
          console.error("Error aplicando duckdbEngine.crossFilter:", {
            error: crossFilterError,
            tableName,
            globalFilters: scopedFilters,
            reportId: report.id,
            widgetType: type,
          });
          throw crossFilterError;
        }

        if (cancelled) return;

        if (type === 'chart') {
          const contract = resolveContractForRows(innerContent, filteredRows);
          if (contract) {
            const nextChartOption = buildReactiveChartOption(innerContent, filteredRows, contract);
            setResolvedChartOption(nextChartOption);
            finishPerf({
              hasGlobalFilters: true,
              filteredRows: filteredRows.length,
              chartReactive: true,
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
            });
          }
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
        return <AnalysisReport data={{ metrics: innerContent, tableData: [] }} onSave={() => {}} />;
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
               onChartClick={(params) => onChartClick && onChartClick(params, innerContent.table_name, report.file_id)}
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
            onChartClick={(params) => onChartClick && onChartClick(params, innerContent.table_name, report.file_id)} 
            isWidget={true}
            interactionMode="filter"
            hideModeSwitch={presentationMode}
            hideVisualPicker={presentationMode}
            presentationMode={presentationMode}
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
      className="flex flex-col w-full h-full bg-card border shadow-sm rounded-xl overflow-hidden group"
      data-testid={`dashboard-widget-${report.id}`}
      data-widget-type={type}
      data-report-title={report.title}
      onDoubleClick={presentationMode && onRequestFocus ? () => onRequestFocus(report) : undefined}
    >
      {/* Header Interactivo (Drag Handle) */}
      <CardHeader className={[
        "flex flex-row items-center justify-between border-b border-border/30 bg-background/50",
        presentationMode ? "p-2.5" : "p-3",
        presentationMode ? "" : "cursor-grab active:cursor-grabbing widget-drag-handle",
      ].join(" ")}>
        <div className="flex flex-col min-w-0">
          <CardTitle className="text-sm font-medium leading-tight truncate text-foreground/90">
            {report.title}
          </CardTitle>
          <p className="text-[10px] text-muted-foreground/80 font-medium">
            {format(new Date(report.created_at), "d 'de' MMM, yy", { locale: es })}
          </p>
        </div>
        
        {/* Controles Ocultos por defecto, visibles en Hover */}
        {presentationMode ? (
          onRequestFocus ? (
            <div className="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7 text-muted-foreground hover:bg-secondary/80"
                onClick={() => onRequestFocus(report)}
                title="Enfocar visual"
              >
                <Maximize2 className="h-3.5 w-3.5" />
              </Button>
            </div>
          ) : null
        ) : (
          <div className="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
            <Button variant="ghost" size="icon" className="h-7 w-7 text-muted-foreground hover:bg-secondary/80" onClick={() => onAnalyze(report)} title="Continuar en Chat">
              <MessageSquare className="h-3.5 w-3.5" />
            </Button>
            <Button variant="ghost" size="icon" className="h-7 w-7 text-muted-foreground hover:text-destructive hover:bg-destructive/10" onClick={() => onDelete(report.id)} title="Eliminar">
              <Trash2 className="h-3.5 w-3.5" />
            </Button>
            <div className="flex items-center text-muted-foreground/50 ml-1">
              <GripHorizontal className="h-4 w-4" />
            </div>
          </div>
        )}
      </CardHeader>
      
      {/* Contenido (Canvas Completo) */}
      <CardContent className={[
        "flex-1 min-h-0 relative overflow-hidden flex flex-col",
        presentationMode ? "p-1.5" : "p-2",
      ].join(" ")}>
        {renderContent()}
      </CardContent>
    </Card>
  );
});
