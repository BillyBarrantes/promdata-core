"use client"

import React from 'react';
import dynamic from 'next/dynamic';
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { SaveIcon } from '@/components/icons/save-icon';
import { EChartsOption } from 'echarts';
import { EChartsChart } from '@/components/echarts-chart'; // Importamos nuestro nuevo componente
import { AlertTriangle, BarChart3, Check, Filter, PanelsTopLeft, RefreshCcw, Sparkles, Rows3, Table2 } from 'lucide-react';
import { useAtomValue, useSetAtom } from 'jotai';
import { globalFiltersAtom, activeFileIdAtom } from '@/lib/state';
import {
  buildVisualOptionFromPayload,
  getTransformSupportReason,
  isVisualTransformSupported,
  type VisualCatalogEntry,
  type VisualGovernancePayload,
  type VisualId,
  type VisualSourcePayload,
} from '@/lib/visual-engine';
import type { SmartTableColumn } from '@/components/smart-table';

const SmartTablePreview = dynamic(
  () => import('@/components/smart-table').then((mod) => mod.SmartTable),
  {
    ssr: false,
    loading: () => <div className="rounded-xl border border-border/60 bg-muted/20 p-6 text-sm text-muted-foreground">Cargando vista tabular...</div>,
  }
);

const EMPTY_ARRAY: any[] = [];

const clonePreservingFunctions = <T,>(value: T): T => {
  if (Array.isArray(value)) {
    return value.map((item) => clonePreservingFunctions(item)) as T;
  }

  if (typeof value === "function") {
    return value;
  }

  if (value && typeof value === "object") {
    const next: Record<string, unknown> = {};
    Object.entries(value as Record<string, unknown>).forEach(([key, entryValue]) => {
      next[key] = clonePreservingFunctions(entryValue);
    });
    return next as T;
  }

  return value;
};

interface ChartsReportProps {
  option: EChartsOption;
  title?: string;
  onSave: (optionOverride?: EChartsOption) => void;
  isThumbnail?: boolean;
  onChartClick?: (params: any) => void;
  /** Cuando true, renderiza sin Card/padding externo (usado dentro de GridWidget) */
  isWidget?: boolean;
  interactionMode?: 'explore' | 'filter';
  /** Cuando true, oculta el switch Tabla/Híbrida/Gráfico (usado en embeds que ya tienen controles propios). */
  hideModeSwitch?: boolean;
  /** Inserta controles externos dentro de la misma toolbar (ej: SmartTable mode switch). */
  toolbarPrefix?: React.ReactNode;
  /** Cuando true, oculta el botón de reemplazo visual. */
  hideVisualPicker?: boolean;
  /** Cuando true, suprime chrome secundario para modo presentación/exporte. */
  presentationMode?: boolean;
}

type VisualGovernance = VisualGovernancePayload;

const normalizeChartOptionForRender = (rawOption: EChartsOption, title?: string): EChartsOption => {
  if (!rawOption) return rawOption;

  try {
    const nextOption = clonePreservingFunctions(rawOption) as any;

    // Si el contenedor ya provee un título externo, ocultar siempre el título interno de ECharts.
    // title="" también significa "suprimir título interno".
    if (typeof title === 'string' && nextOption.title) {
      if (Array.isArray(nextOption.title)) {
        nextOption.title.forEach((entry: any) => {
          if (entry && typeof entry === 'object') entry.show = false;
        });
      } else if (typeof nextOption.title === 'object') {
        nextOption.title.show = false;
      }
    }

    if (nextOption.xAxis) {
      const axes = Array.isArray(nextOption.xAxis) ? nextOption.xAxis : [nextOption.xAxis];
      axes.forEach((axis: any) => {
        if (!axis.axisLabel) axis.axisLabel = {};
        axis.axisLabel.hideOverlap = true;
      });
    }

    return nextOption;
  } catch (error) {
    console.error("Error normalizing chart option:", error);
    return rawOption;
  }
}

const hasRenderableSeriesData = (option: EChartsOption | null | undefined): boolean => {
  if (!option?.series) return false;
  const series = Array.isArray(option.series) ? option.series : [option.series];

  return series.some((entry: any) => {
    if (!entry) return false;
    if (Array.isArray(entry.data) && entry.data.length > 0) return true;
    return false;
  });
};

type ChartTablePayload = {
  columns: SmartTableColumn[];
  data: Record<string, unknown>[];
  sortBy: string;
};

const toFiniteChartNumber = (value: unknown): number | null => {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const parsed = Number(value.replace(/%/g, "").trim());
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
};

const inferTableColumnType = (key: string, values: unknown[]): SmartTableColumn["type"] => {
  const numericValues = values
    .map((value) => toFiniteChartNumber(value))
    .filter((value): value is number => value !== null);

  if (numericValues.length === values.filter((value) => value !== undefined && value !== null).length && numericValues.length > 0) {
    const keyLabel = key.toLowerCase();
    if (keyLabel.includes("%") || keyLabel.includes("variaci") || keyLabel.includes("growth") || keyLabel.includes("ratio")) {
      return "percentage";
    }
    return "number";
  }

  return "text";
};

const buildChartTablePayload = (payload: VisualSourcePayload | null | undefined): ChartTablePayload | null => {
  const rows = Array.isArray(payload?.rows) ? payload.rows : [];
  if (rows.length === 0) return null;

  const normalizedRows = rows
    .map((row) => {
      if (!row || typeof row !== "object" || Array.isArray(row)) return null;
      const record = row as Record<string, unknown>;
      const plainRow: Record<string, unknown> = {};

      Object.entries(record).forEach(([key, value]) => {
        if (key === "extra_info") return;
        plainRow[key] = value;
      });

      return Object.keys(plainRow).length > 0 ? plainRow : null;
    })
    .filter((row): row is Record<string, unknown> => Boolean(row));

  if (normalizedRows.length === 0) return null;

  const keySet = new Set<string>();
  normalizedRows.forEach((row) => {
    Object.keys(row).forEach((key) => keySet.add(key));
  });

  const orderedKeys = Array.from(keySet);
  const columns: SmartTableColumn[] = orderedKeys.map((key, index) => {
    const values = normalizedRows.map((row) => row[key]);
    const type = inferTableColumnType(key, values);
    const label = key
      .replace(/_/g, ' ')
      .replace(/\b\w/g, (char) => char.toUpperCase());

    return {
      key,
      label,
      type,
      bar: type === "number",
      heatmap: type === "percentage",
    };
  });

  const firstNumeric = columns.find((column) => column.type === "number" || column.type === "percentage");

  return {
    columns,
    data: normalizedRows,
    sortBy: firstNumeric?.key || columns[0]?.key || "name",
  };
};

const VisualStatePanel = ({
  icon,
  title,
  message,
  tone = "muted",
}: {
  icon: React.ReactNode;
  title: string;
  message: string;
  tone?: "muted" | "warning" | "error";
}) => {
  const toneClasses = {
    muted: {
      wrapper: "border-muted-foreground/20 bg-muted/20",
      icon: "bg-muted-foreground/10 text-muted-foreground/60",
      title: "text-foreground",
      message: "text-muted-foreground",
    },
    warning: {
      wrapper: "border-amber-200 bg-amber-50/80",
      icon: "bg-amber-100 text-amber-700",
      title: "text-amber-900",
      message: "text-amber-700",
    },
    error: {
      wrapper: "border-rose-200 bg-rose-50/80",
      icon: "bg-rose-100 text-rose-700",
      title: "text-rose-900",
      message: "text-rose-700",
    },
  }[tone];

  return (
    <div className={`flex flex-col items-center justify-center rounded-xl border-2 border-dashed p-6 text-center ${toneClasses.wrapper}`}>
      <div className={`mb-4 rounded-full p-4 ${toneClasses.icon}`}>
        {icon}
      </div>
      <p className={`text-sm font-semibold ${toneClasses.title}`}>{title}</p>
      <p className={`mt-1 max-w-xl text-sm leading-6 ${toneClasses.message}`}>{message}</p>
    </div>
  );
};

const getVisualGovernance = (option: EChartsOption): VisualGovernance | null => {
  const candidate = (option as EChartsOption & { visual_governance?: VisualGovernance }).visual_governance;
  if (!candidate || typeof candidate !== 'object') return null;
  return candidate;
};

const getVisualSourcePayload = (option: EChartsOption): VisualSourcePayload | null => {
  const candidate = (option as EChartsOption & { visual_source_payload?: VisualSourcePayload }).visual_source_payload;
  if (!candidate || typeof candidate !== 'object') return null;
  return candidate;
};

const cloneVisualGovernanceWithAppliedVisual = (
  governance: VisualGovernance | null,
  selectedVisual: VisualId | null,
): VisualGovernance | null => {
  if (!governance || !selectedVisual) return governance;
  const catalog = Array.isArray(governance.catalog) ? governance.catalog : [];
  const selectedEntry = catalog.find((entry) => entry.id === selectedVisual);

  return {
    ...governance,
    applied_visual: selectedVisual,
    applied_label: selectedEntry?.label || governance.applied_label,
    catalog: catalog.map((entry) => ({
      ...entry,
      applied: entry.id === selectedVisual,
    })),
  };
};

const attachVisualMetadata = (
  option: EChartsOption,
  governance: VisualGovernance | null,
  sourcePayload: VisualSourcePayload | null,
): EChartsOption => {
  return {
    ...(option as Record<string, unknown>),
    visual_governance: governance || undefined,
    visual_source_payload: sourcePayload || undefined,
  } as EChartsOption;
};

const VisualGovernanceBanner = ({
  governance,
  activeGlobalFilter,
  onClearGlobalFilter,
}: {
  governance: VisualGovernance | null;
  activeGlobalFilter: string | null;
  onClearGlobalFilter: () => void;
}) => {
  const filterLabel = activeGlobalFilter ?? "";
  const hasGlobalFilter = filterLabel.length > 0;
  const hasBannerContent = Boolean(
    hasGlobalFilter ||
    governance?.applied_label ||
    governance?.recommended_label ||
    governance?.blocked_reason ||
    governance?.advisory_reason ||
    governance?.recommendation_reason
  );

  if (!hasBannerContent || !governance && !hasGlobalFilter) return null;

  const hasDescriptorText = Boolean(
    governance?.applied_label ||
    governance?.recommended_label ||
    governance?.blocked_reason ||
    governance?.advisory_reason ||
    governance?.recommendation_reason
  );

  return (
    <div className="mb-4 rounded-xl border border-border/70 bg-muted/30 px-4 py-3">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="flex flex-wrap items-center gap-2 text-xs sm:text-sm">
          {governance?.applied_label && (
          <span className="inline-flex items-center rounded-full border border-border bg-background px-2.5 py-1 font-medium text-foreground">
            Aplicado: {governance.applied_label}
          </span>
        )}
          {governance?.recommended_label && governance.recommended_label !== governance.applied_label && (
            <span className="inline-flex items-center gap-1 rounded-full border border-emerald-200 bg-emerald-50 px-2.5 py-1 font-medium text-emerald-700">
              <Sparkles className="h-3.5 w-3.5" />
              Recomendado: {governance.recommended_label}
            </span>
          )}
          {governance?.override_applied && governance.blocked_reason && (
            <span className="inline-flex items-center gap-1 rounded-full border border-amber-200 bg-amber-50 px-2.5 py-1 font-medium text-amber-700">
              <AlertTriangle className="h-3.5 w-3.5" />
              Ajustado por validez visual
            </span>
          )}
          {hasGlobalFilter && (
            <span className="inline-flex items-center gap-1 rounded-full border border-emerald-200 bg-emerald-50 px-2.5 py-1 font-medium text-emerald-700">
              <Filter className="h-3.5 w-3.5" />
              Filtro global activo: {filterLabel}
            </span>
          )}
        </div>

        {hasGlobalFilter && (
          <div className="flex shrink-0 items-center gap-2">
            <Button variant="outline" size="sm" className="h-8 gap-1.5 text-xs" onClick={onClearGlobalFilter}>
              <RefreshCcw className="h-3.5 w-3.5" />
              Limpiar filtro global
            </Button>
          </div>
        )}
      </div>

      {hasDescriptorText && (
        <p className="mt-2 text-xs leading-5 text-muted-foreground">
          {governance?.blocked_reason || governance?.advisory_reason || governance?.recommendation_reason}
        </p>
      )}
    </div>
  );
};

const normalizeFilterLabel = (value: unknown): string | null => {
  if (typeof value !== "string") return null;
  const normalized = value.replace(/\0/g, "").normalize("NFC").replace(/\s+/g, " ").trim();
  return normalized || null;
};

const ChartsReportComponent = ({
  option,
  title,
  onSave,
  isThumbnail = false,
  onChartClick,
  isWidget = false,
  interactionMode = 'explore',
  hideModeSwitch = false,
  toolbarPrefix = null,
  hideVisualPicker = false,
  presentationMode = false,
}: ChartsReportProps) => {
  const [localOption, setLocalOption] = React.useState<EChartsOption>(() => normalizeChartOptionForRender(option, title));
  const [visualPickerOpen, setVisualPickerOpen] = React.useState(false);
  const [visualOverride, setVisualOverride] = React.useState<VisualId | null>(null);
  const [visualError, setVisualError] = React.useState<string | null>(null);
  const [viewMode, setViewMode] = React.useState<'table' | 'chart' | 'hybrid'>('chart');

  // Jotai State Integrations
  const activeFileId = useAtomValue(activeFileIdAtom);
  const globalFilters = useAtomValue(globalFiltersAtom);
  const setGlobalFilters = useSetAtom(globalFiltersAtom);
  const [, setSelectedCategory] = React.useState<string | null>(null);
  const baseGovernance = React.useMemo(() => getVisualGovernance(option), [option]);
  const sourcePayload = React.useMemo(() => getVisualSourcePayload(option), [option]);
  const visualGovernance = React.useMemo(
    () => cloneVisualGovernanceWithAppliedVisual(baseGovernance, visualOverride),
    [baseGovernance, visualOverride]
  );
  const visualCatalog = React.useMemo<VisualCatalogEntry[]>(() => {
    return Array.isArray(visualGovernance?.catalog) ? visualGovernance.catalog : [];
  }, [visualGovernance]);
  const activeVisualId = (visualOverride || visualGovernance?.applied_visual || visualGovernance?.requested_visual || null) as VisualId | null;
  const tablePayload = React.useMemo(() => buildChartTablePayload(sourcePayload), [sourcePayload]);
  const supportsTabularMode = Boolean(tablePayload && tablePayload.data.length > 0);
  const activeGlobalFilter = React.useMemo(() => {
    return normalizeFilterLabel(globalFilters?.global_cross_filter ?? globalFilters?.global_chart_filter ?? null);
  }, [globalFilters]);

  const handleClearGlobalFilter = React.useCallback(() => {
    const nextFilters = { ...globalFilters };
    delete nextFilters.global_cross_filter;
    delete nextFilters.global_chart_filter;
    setGlobalFilters(nextFilters);
  }, [globalFilters, setGlobalFilters]);

  const handleInternalChartClick = (params: any) => {
    const clickedCategory = typeof params?.rawCategory === 'string' && params.rawCategory.trim()
      ? params.rawCategory
      : params?.name;

    if (typeof clickedCategory === 'string' && clickedCategory.trim()) {
      const normalized = clickedCategory.replace(/\0/g, '').normalize('NFC').replace(/\s+/g, ' ').trim();
      setSelectedCategory(prev => prev === normalized ? null : normalized);
    }
    if (onChartClick) onChartClick(params);
  };


  // Sync prop changes to state
  React.useEffect(() => {
    if (!option) return;
    try {
      const normalizedOption = attachVisualMetadata(
        normalizeChartOptionForRender(option, title),
        baseGovernance,
        sourcePayload,
      );
      const overriddenOption = visualOverride && sourcePayload
        ? buildVisualOptionFromPayload(sourcePayload, visualOverride)
        : null;

      const nextOption = overriddenOption
        ? attachVisualMetadata(
            normalizeChartOptionForRender(overriddenOption, title),
            cloneVisualGovernanceWithAppliedVisual(baseGovernance, visualOverride),
            sourcePayload,
          )
        : normalizedOption;

      if (visualOverride && !overriddenOption) {
        setVisualOverride(null);
      }

      const serializedOption = JSON.stringify(nextOption);
      const currentSerialized = JSON.stringify(localOption);

      setVisualError(null);

      if (serializedOption === currentSerialized) return;
      setLocalOption(nextOption);
    } catch (e) {
      console.error("Error updating chart options:", e);
      setLocalOption(
        attachVisualMetadata(
          normalizeChartOptionForRender(option, title),
          baseGovernance,
          sourcePayload,
        )
      );
    }
  }, [option, title, baseGovernance, sourcePayload]);

  const handleSelectVisual = React.useCallback((visualId: VisualId) => {
    if (!sourcePayload) {
      setVisualError("Este grafico no expone datos fuente suficientes para reemplazo visual.");
      return;
    }

    const transformReason = getTransformSupportReason(visualId, sourcePayload);
    if (transformReason) {
      setVisualError(transformReason);
      return;
    }

    if (visualId === visualGovernance?.applied_visual) {
      setVisualOverride(null);
      setVisualError(null);
      setLocalOption(
        attachVisualMetadata(
          normalizeChartOptionForRender(option, title),
          baseGovernance,
          sourcePayload,
        )
      );
      setVisualPickerOpen(false);
      return;
    }

    const nextOption = buildVisualOptionFromPayload(sourcePayload, visualId);
    if (!nextOption) {
      setVisualError("No se pudo reconstruir ese visual con el payload disponible.");
      return;
    }

    setVisualOverride(visualId);
    setVisualError(null);
    setViewMode('chart');
    setLocalOption(
      attachVisualMetadata(
        normalizeChartOptionForRender(nextOption, title),
        cloneVisualGovernanceWithAppliedVisual(baseGovernance, visualId),
        sourcePayload,
      )
    );
    setVisualPickerOpen(false);
  }, [sourcePayload, visualGovernance, option, title, baseGovernance]);

  const handleRestoreOriginalVisual = React.useCallback(() => {
    setVisualOverride(null);
    setVisualError(null);
    setLocalOption(
      attachVisualMetadata(
        normalizeChartOptionForRender(option, title),
        baseGovernance,
        sourcePayload,
      )
    );
    setVisualPickerOpen(false);
  }, [option, title, baseGovernance, sourcePayload]);

  const handleLegendChange = React.useCallback((params: any, instance: any) => {
    const { selected } = params;
    const currentOption = instance.getOption();

    if (!currentOption.series || !Array.isArray(currentOption.series)) return;

    // 1. Identify Bars and Target Lines
    const barSeries = currentOption.series.filter((s: any) => s.type === 'bar');
    // Identify lines that might need recalculation
    const lineSeries = currentOption.series.filter((s: any) => s.type === 'line' && s.data.length > 0);

    if (barSeries.length === 0 || lineSeries.length === 0) return;

    // Fix 2: Guard against Interaction/Drill-Down Filter
    // If the chart is currently filtered (Drill-Down), data will be subset.
    // We MUST NOT recalculate totals based on a filtered view.
    const xAxis = Array.isArray(currentOption.xAxis) ? currentOption.xAxis[0] : currentOption.xAxis;
    const totalCategories = xAxis?.data?.length || 0;
    const currentDataLength = barSeries[0].data.length;

    if (totalCategories > 0 && currentDataLength < totalCategories) {
      // Filter Active: Skip recalculation to protect data integrity
      return;
    }

    // 2. Calculate Totals per Category (Column) based on VISIBLE series
    const visibleBars = barSeries.filter((s: any) => selected[s.name] !== false);

    // Assuming all series have aligned data length (standard ECharts)
    // If no visible bars, totals are 0
    const dataLength = barSeries[0].data.length;
    const newTotals: number[] = new Array(dataLength).fill(0);

    // Sum vertical stack
    if (visibleBars.length > 0) {
      visibleBars.forEach((s: any) => {
        s.data.forEach((val: any, idx: number) => {
          // Handle raw numbers or object value format { value: N }
          const num = (typeof val === 'object' && val !== null) ? val.value : val;
          newTotals[idx] += (parseFloat(num) || 0);
        });
      });
    }

    // 3. Update Lines
    let modified = false;
    const newSeries = currentOption.series.map((s: any) => {
      // Logic for "Total" lines (Sum)
      // Heuristic: Name contains 'Total' or 'Stock' AND is a line
      if (s.type === 'line' && (s.name.toLowerCase().includes('total') || s.name.toLowerCase().includes('stock'))) {
        // Only update if it looks like a summary line (match data length)
        if (s.data.length === dataLength) {
          s.data = newTotals;
          modified = true;
        }
      }

      // Logic for "Variation" lines (%)
      // Heuristic: Name contains 'Variación' or '%'
      if (s.type === 'line' && (s.name.toLowerCase().includes('variaci') || s.name.includes('%'))) {
        // Calculate variation based on newTotals
        const newVariations = newTotals.map((curr, idx) => {
          if (idx === 0) return 0; // First point usually 0 or null
          const prev = newTotals[idx - 1];
          if (prev === 0) return 0;
          return parseFloat(((curr - prev) / prev * 100).toFixed(2));
        });
        s.data = newVariations;
        modified = true;
      }
      return s;
    });

    if (modified) {
      // We use standard echarts setOption merge to update data
      instance.setOption({ series: newSeries });
    }

  }, []);

  if (!localOption) {
    return (
      <Card className="p-6 mt-6">
        <VisualStatePanel
          icon={<AlertTriangle className="h-8 w-8" />}
          title="No se pudo renderizar el visual"
          message="La configuración del gráfico llegó incompleta o inválida. Conservamos el análisis, pero este visual necesita una revisión."
          tone="error"
        />
        {/* Usamos EMPTY_ARRAY para asegurar que si algo falla, no halla undefined */}
        <div style={{ display: 'none' }}>{EMPTY_ARRAY.length}</div>
      </Card>
    );
  }

  // Si es Thumbnail, renderizado ultra-simplificado
  if (isThumbnail) {
    return (
      <div className="w-full h-full">
        <EChartsChart option={localOption} isThumbnail={true} style={{ width: '100%', height: '100%' }} />
      </div>
    );
  }

  // 🚀 onEvents memoizado — referencia estable entre renders
  const legendEvents = React.useMemo(() => ({
    'legendselectchanged': handleLegendChange
  }), [handleLegendChange]);

  const canOpenVisualPicker = Boolean(sourcePayload && visualCatalog.length > 0);
  const modeSwitch = !hideModeSwitch && !presentationMode && supportsTabularMode ? (
    <>
      <Button
        variant={viewMode === 'table' ? "default" : "outline"}
        size="sm"
        className="h-8 shrink-0 gap-1.5 whitespace-nowrap text-xs"
        onClick={() => setViewMode('table')}
      >
        <Table2 className="h-3.5 w-3.5" />
        Tabla
      </Button>
      <Button
        variant={viewMode === 'hybrid' ? "default" : "outline"}
        size="sm"
        className="h-8 shrink-0 gap-1.5 whitespace-nowrap text-xs"
        onClick={() => setViewMode('hybrid')}
      >
        <Rows3 className="h-3.5 w-3.5" />
        Híbrida
      </Button>
      <Button
        variant={viewMode === 'chart' ? "default" : "outline"}
        size="sm"
        className="h-8 shrink-0 gap-1.5 whitespace-nowrap text-xs"
        onClick={() => setViewMode('chart')}
      >
        <BarChart3 className="h-3.5 w-3.5" />
        Gráfico
      </Button>
    </>
  ) : null;

  const visualButton = (
    <Popover open={visualPickerOpen} onOpenChange={setVisualPickerOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          className="h-8 shrink-0 whitespace-nowrap"
          disabled={!canOpenVisualPicker}
          title={canOpenVisualPicker ? "Reemplazar visual" : "No hay reemplazo visual disponible"}
        >
          <PanelsTopLeft className="h-4 w-4" />
          Visual
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-[320px] p-3">
        <div className="space-y-3">
          <div>
            <div className="text-sm font-semibold text-foreground">Reemplazar visual</div>
            <p className="text-xs text-muted-foreground">
              Cambia la lectura sin recalcular el analisis.
            </p>
          </div>
          <TooltipProvider delayDuration={120}>
            <div className="max-h-[320px] overflow-y-auto space-y-1 pr-1">
              {visualCatalog.map((entry) => {
                const transformReason = getTransformSupportReason(entry.id, sourcePayload);
                const canTransform = isVisualTransformSupported(entry.id, sourcePayload);
                const isSelectable = entry.enabled && canTransform;
                const disabledReason = entry.reason || transformReason || "No disponible para este dataset.";
                return (
                  <Tooltip key={entry.id}>
                    <TooltipTrigger asChild>
                      <div>
                        <button
                          type="button"
                          disabled={!isSelectable}
                          onClick={() => handleSelectVisual(entry.id)}
                          className={[
                            "w-full rounded-lg border px-3 py-2 text-left transition-colors",
                            activeVisualId === entry.id
                              ? "border-foreground/20 bg-foreground/5"
                              : "border-border bg-background hover:bg-muted/40",
                            !isSelectable ? "cursor-not-allowed opacity-55" : "",
                          ].join(" ")}
                        >
                          <div className="flex items-center justify-between gap-3">
                            <div>
                              <div className="text-sm font-medium text-foreground">{entry.label}</div>
                              <div className="text-[11px] text-muted-foreground">
                                {entry.applied ? "Visual activo" : entry.recommended ? "Recomendado por el motor" : "Disponible"}
                              </div>
                            </div>
                            <div className="flex items-center gap-1">
                              {entry.recommended && (
                                <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[10px] font-medium text-emerald-700">
                                  IA
                                </span>
                              )}
                              {activeVisualId === entry.id && (
                                <Check className="h-4 w-4 text-foreground" />
                              )}
                            </div>
                          </div>
                        </button>
                      </div>
                    </TooltipTrigger>
                    {!isSelectable && (
                      <TooltipContent side="left">
                        {disabledReason}
                      </TooltipContent>
                    )}
                  </Tooltip>
                );
              })}
            </div>
          </TooltipProvider>
          {visualOverride && (
            <Button
              variant="ghost"
              size="sm"
              className="w-full justify-center"
              onClick={handleRestoreOriginalVisual}
            >
              <RefreshCcw className="h-4 w-4" />
              Restaurar visual original
            </Button>
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
  const showVisualButton = !hideVisualPicker && !presentationMode;
  const hasToolbarControls = Boolean(modeSwitch || toolbarPrefix || showVisualButton);

  const renderChartCanvas = () => {
    const hasData = hasRenderableSeriesData(localOption);

    if (!hasData) {
      return (
        <VisualStatePanel
          icon={<BarChart3 className="h-8 w-8" />}
          title="Sin datos visualizables"
          message={`El visual "${title || 'actual'}" no tiene puntos suficientes para renderizarse con claridad.`}
          tone="muted"
        />
      );
    }

    return (
      <EChartsChart
        option={localOption}
        onChartClick={handleInternalChartClick}
        onEvents={legendEvents}
        interactionMode={interactionMode}
      />
    );
  };

  const renderTablePanel = () => {
    if (!tablePayload) return null;

    return (
      <SmartTablePreview
        title={title || sourcePayload?.title || "Vista tabular"}
        columns={tablePayload.columns}
        data={tablePayload.data}
        sortBy={tablePayload.sortBy}
        sortOrder="desc"
        onSave={() => onSave(localOption)}
        isWidget={true}
        fileId={activeFileId}
      />
    );
  };

  // isWidget: renderizar contenido neto sin Card/mt-6 (GridWidget ya es el contenedor visual)
  if (isWidget) {
    const showWidgetInnerTitle = !presentationMode;

    return (
      <div className="w-full h-full min-h-0 min-w-0 flex flex-col">
        {((showWidgetInnerTitle && title) || hasToolbarControls) && (
          <div className="mb-2 flex items-start justify-between gap-3 shrink-0">
            {showWidgetInnerTitle && title ? (
              <h3 className="min-w-0 flex-1 pr-2 text-lg font-semibold text-foreground">
                {title}
              </h3>
            ) : <div className="flex-1" />}
            {hasToolbarControls && (
              <div className="chart-toolbar-row ml-auto inline-flex w-max max-w-full shrink-0 flex-nowrap items-center gap-2 overflow-x-auto pl-2 whitespace-nowrap scrollbar-hide">
                {modeSwitch}
                {toolbarPrefix}
                {showVisualButton ? visualButton : null}
              </div>
            )}
          </div>
        )}
        {!presentationMode && (
          <VisualGovernanceBanner
            governance={visualGovernance}
            activeGlobalFilter={activeGlobalFilter}
            onClearGlobalFilter={handleClearGlobalFilter}
          />
        )}
        {visualError && (
          <div className="mb-3">
            <VisualStatePanel
              icon={<AlertTriangle className="h-7 w-7" />}
              title="Reemplazo visual no disponible"
              message={visualError}
              tone="warning"
            />
          </div>
        )}
        {viewMode === 'table' && supportsTabularMode ? (
          <div className="flex-1 min-h-0">{renderTablePanel()}</div>
        ) : viewMode === 'hybrid' && supportsTabularMode ? (
          <div className="flex flex-1 min-h-0 flex-col gap-4">
            <div className="min-h-[280px] flex-1">{renderChartCanvas()}</div>
            <div className="min-h-[240px]">{renderTablePanel()}</div>
          </div>
        ) : (() => {
          const hasData = hasRenderableSeriesData(localOption);
          if (!hasData) {
            return (
              <div className="flex-1">
                <VisualStatePanel
                  icon={<BarChart3 className="h-8 w-8" />}
                  title="Sin datos visualizables"
                  message={`El análisis para "${title || 'este visual'}" no devolvió una estructura suficiente para pintar el gráfico. El resultado sigue protegido y puede leerse desde la tabla o cambiando de visual.`}
                  tone="muted"
                />
              </div>
            )
          }
          return renderChartCanvas();
        })()}
      </div>
    );
  }

  return (
    <div className="mt-6 w-full min-w-0 overflow-hidden h-full"> 
      <Card className="w-full p-4 relative min-w-0 overflow-hidden h-full flex flex-col">
        <div className="chart-toolbar-row absolute top-4 right-4 z-10 inline-flex w-max max-w-[calc(100%-2rem)] flex-nowrap items-center gap-2 overflow-x-auto whitespace-nowrap scrollbar-hide">
          {modeSwitch}
          {toolbarPrefix}
          {showVisualButton ? visualButton : null}
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8"
            onClick={() => onSave(localOption)}
            title="Guardar"
            data-testid="chart-save-button"
          >
            <SaveIcon className="h-4 w-4" />
          </Button>
        </div>

        {title && (
          <h3 className="text-lg font-semibold text-foreground mb-4 pr-20 shrink-0">
            {title}
          </h3>
        )}

        {!presentationMode && (
          <VisualGovernanceBanner
            governance={visualGovernance}
            activeGlobalFilter={activeGlobalFilter}
            onClearGlobalFilter={handleClearGlobalFilter}
          />
        )}
        {visualError && (
          <div className="mb-4">
            <VisualStatePanel
              icon={<AlertTriangle className="h-7 w-7" />}
              title="Reemplazo visual no disponible"
              message={visualError}
              tone="warning"
            />
          </div>
        )}
        {/* Dentro del render, antes de <ReactECharts ... /> */}
        {viewMode === 'table' && supportsTabularMode ? (
          renderTablePanel()
        ) : viewMode === 'hybrid' && supportsTabularMode ? (
          <div className="flex flex-1 min-h-0 flex-col gap-4">
            <div className="min-h-[320px] flex-1">
              {renderChartCanvas()}
            </div>
            <div className="min-h-[260px]">
              {renderTablePanel()}
            </div>
          </div>
        ) : (
          renderChartCanvas()
        )}

      </Card>
    </div>
  );
}

export const ChartsReport = React.memo(ChartsReportComponent);
