"use client"

import React from 'react';
import { createPortal } from 'react-dom';
import dynamic from 'next/dynamic';
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { SaveIcon } from '@/components/icons/save-icon';
import { EChartsOption } from 'echarts';
import { EChartsChart } from '@/components/echarts-chart'; // Importamos nuestro nuevo componente
import { AlertTriangle, BarChart3, Check, Filter, MoreHorizontal, RefreshCcw, Sparkles, Rows3, Table2, ShieldCheck, Trash2, X } from 'lucide-react';
import { EvidenceDrawer } from '@/components/dashboard/evidence-drawer';
import { useCrossFilter } from '@/hooks/useCrossFilter';
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
    loading: () => <div className="rounded-xl border border-border/40 bg-muted/20 p-6 text-sm text-muted-foreground">Cargando vista tabular...</div>,
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
  /** Callback para eliminar el widget desde el menú de 3 puntos */
  onDelete?: () => void;
  /** Contenedor DOM para portalar los controles de acción al header del widget */
  headerPortalContainer?: HTMLElement | null;
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

    // [FASE 5.2] Paleta corporativa SaaS accesible para consistencia visual
    const ECHARTS_SAAS_PALETTE = [
      "#2563eb", // Royal Blue
      "#06b6d4", // Cyan
      "#10b981", // Emerald
      "#f59e0b", // Amber
      "#8b5cf6", // Violet
      "#ec4899", // Pink
      "#6366f1", // Indigo
      "#14b8a6", // Teal
    ];
    if (!nextOption.color || !Array.isArray(nextOption.color) || nextOption.color.length === 0) {
      nextOption.color = ECHARTS_SAAS_PALETTE;
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

/**
 * ECharts accepts singleton and array forms for component options. Keep that
 * boundary normalized for diagnostics as well as rendering, so telemetry never
 * assumes a narrower shape than the public EChartsOption contract.
 */
const getChartDiagnosticSnapshot = (option: EChartsOption | null | undefined) => {
  const titleOption = Array.isArray(option?.title) ? option.title[0] : option?.title;
  const seriesOptions = option?.series
    ? (Array.isArray(option.series) ? option.series : [option.series])
    : [];
  const primarySeries = seriesOptions[0] as { data?: unknown } | undefined;

  return {
    title: typeof titleOption?.text === "string" ? titleOption.text : undefined,
    hasSeries: seriesOptions.length > 0,
    seriesDataLength: Array.isArray(primarySeries?.data) ? primarySeries.data.length : 0,
  };
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
    const pctIndicators = ["%", "variaci", "growth", "ratio"];
    const pctMatchCount = pctIndicators.filter((ind) => keyLabel.includes(ind)).length;
    if (pctMatchCount >= 2) {
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
  const hasAdjustment = Boolean(governance?.override_applied && governance.blocked_reason);
  const hasBannerContent = Boolean(hasGlobalFilter || hasAdjustment);

  if (!hasBannerContent) return null;

  const advisoryText = governance?.blocked_reason || governance?.advisory_reason || governance?.recommendation_reason;

  return (
    <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        {hasAdjustment && (
          <span className="inline-flex items-center gap-1 rounded-full border border-amber-200 bg-amber-50 dark:bg-amber-950/40 dark:border-amber-900 px-2.5 py-0.5 text-xs font-medium text-amber-700 dark:text-amber-400">
            <AlertTriangle className="h-3 w-3" />
            Ajustado por validez visual
          </span>
        )}
        {hasGlobalFilter && (
          <span className="inline-flex items-center gap-1.5 rounded-full border border-border/40 bg-muted/60 px-2.5 py-0.5 text-xs font-medium text-foreground">
            <Filter className="h-3 w-3 text-muted-foreground" />
            <span className="max-w-[220px] truncate">Filtro activo: {filterLabel}</span>
            <button
              type="button"
              onClick={onClearGlobalFilter}
              className="ml-0.5 rounded-full p-0.5 text-muted-foreground hover:bg-background hover:text-foreground transition-all duration-150 ease-[cubic-bezier(0.2,0,0,1)]"
              title="Limpiar filtro global"
            >
              <X className="h-3 w-3" />
            </button>
          </span>
        )}
      </div>

      {advisoryText && (
        <p className="w-full text-[11px] leading-4 text-muted-foreground/80">
          {advisoryText}
        </p>
      )}
    </div>
  );
};

const CompactMenuItem = ({
  active = false,
  icon,
  label,
  onSelect,
}: {
  active?: boolean;
  icon: React.ReactNode;
  label: string;
  onSelect: () => void;
}) => (
  <button
    type="button"
    onClick={onSelect}
    className={[
      "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs transition-all duration-150 ease-[cubic-bezier(0.2,0,0,1)]",
      active
        ? "bg-muted/70 font-medium text-foreground"
        : "text-muted-foreground hover:bg-muted/50 hover:text-foreground",
    ].join(" ")}
  >
    <span className="shrink-0">{icon}</span>
    <span className="flex-1 truncate">{label}</span>
    {active && <Check className="h-3.5 w-3.5 shrink-0 text-foreground" />}
  </button>
);

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
  onDelete,
  headerPortalContainer,
}: ChartsReportProps) => {
  const [localOption, setLocalOption] = React.useState<EChartsOption>(() => normalizeChartOptionForRender(option, title));
  const [visualPickerOpen, setVisualPickerOpen] = React.useState(false);
  const [visualOverride, setVisualOverride] = React.useState<VisualId | null>(null);
  const [visualError, setVisualError] = React.useState<string | null>(null);
  const [viewMode, setViewMode] = React.useState<'table' | 'chart' | 'hybrid'>('chart');
  const [viewMenuOpen, setViewMenuOpen] = React.useState(false);

  // Memoizado: evita re-serializar localOption en cada corrida del effect de sync
  const localOptionSerialized = React.useMemo(() => {
    try {
      return JSON.stringify(localOption);
    } catch {
      return null;
    }
  }, [localOption]);

  // Jotai State Integrations
  const activeFileId = useAtomValue(activeFileIdAtom);
  const globalFilters = useAtomValue(globalFiltersAtom);
  const setGlobalFilters = useSetAtom(globalFiltersAtom);
  const [selectedCategory, setSelectedCategory] = React.useState<string | null>(null);
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
  const hasGovernanceAdjustment = Boolean(visualGovernance?.override_applied && visualGovernance?.blocked_reason);

  React.useEffect(() => {
    if (!activeGlobalFilter) {
      setSelectedCategory(null);
    }
  }, [activeGlobalFilter]);

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


  const { activeSelection, isSelfChart } = useCrossFilter();
  const myTableName = (option as any)?.table_name || (option as any)?.original_chart_option?.table_name || (localOption as any)?.table_name;
  const prevActiveSelectionRef = React.useRef(activeSelection);

  // [FASE 6.3] Reactividad cruzada automática entre gráficos con Anti-Self-Filter
  React.useEffect(() => {
    const wasFiltered = prevActiveSelectionRef.current !== null && prevActiveSelectionRef.current !== undefined;
    prevActiveSelectionRef.current = activeSelection;

    if (!activeSelection) {
      // Solo restaurar si PREVIAMENTE existía un filtro activo y se acaba de limpiar
      if (wasFiltered) {
        const baseOpt = visualOverride && sourcePayload
          ? buildVisualOptionFromPayload(sourcePayload, visualOverride, option)
          : option;

        setLocalOption(attachVisualMetadata(
          normalizeChartOptionForRender(baseOpt || option, title),
          visualGovernance,
          sourcePayload
        ));
      }
      return;
    }

    // Anti-Self-Filter: El gráfico origen resalta pero NO colapsa sus barras
    if (isSelfChart(myTableName)) {
      return;
    }

    const targetDim = activeSelection.dimension.toLowerCase().trim();
    const targetVal = activeSelection.value.toLowerCase().trim();
    const rows = sourcePayload?.rows;

    if (Array.isArray(rows) && rows.length > 0) {
      const filteredRows = rows.filter((row: any) => {
        if (!row || typeof row !== 'object') return false;

        // 🎯 [FASE B.1] Dimension-Aware Filtering
        // 1. Identificar columna objetivo en el row correspondiente a targetDim
        const rowKeys = Object.keys(row);
        const exactDimKey = rowKeys.find(k => k.toLowerCase() === targetDim);
        const partialDimKey = exactDimKey || rowKeys.find(k => k.toLowerCase().includes(targetDim));

        if (partialDimKey) {
          const v = row[partialDimKey];
          return String(v ?? "").toLowerCase().trim() === targetVal;
        }

        // 2. Si no coincide el nombre exacto de la dimensión, verificar campos estándar
        const standardDimKey = rowKeys.find(k => 
          ['category', 'name', 'label', 'dim', 'x', 'dimension'].includes(k.toLowerCase())
        );
        if (standardDimKey) {
          const v = row[standardDimKey];
          return String(v ?? "").toLowerCase().trim() === targetVal;
        }

        // 3. Fallback seguro: solo comparar contra valores de tipo string (evitando colisiones numéricas con métricas)
        for (const [k, v] of Object.entries(row)) {
          if (typeof v === 'string' && v.toLowerCase().trim() === targetVal) return true;
        }
        return false;
      });

      if (filteredRows.length > 0) {
        const filteredPayload = { ...sourcePayload, rows: filteredRows };
        const nextOpt = buildVisualOptionFromPayload(filteredPayload, activeVisualId || "bar_chart", option);
        if (nextOpt) {
          setLocalOption(attachVisualMetadata(
            normalizeChartOptionForRender(nextOpt, title),
            visualGovernance,
            filteredPayload
          ));
        }
      }
    }
  }, [activeSelection, isSelfChart, myTableName, option, title, visualGovernance, sourcePayload, activeVisualId, visualOverride]);

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
        ? buildVisualOptionFromPayload(sourcePayload, visualOverride, option)
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

      setVisualError(null);

      if (localOptionSerialized !== null && serializedOption === localOptionSerialized) return;
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
  }, [option, title, baseGovernance, sourcePayload, visualOverride]);

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

    if (visualId === "smart_table") {
      setVisualOverride(visualId);
      setVisualError(null);
      setViewMode('table');
      setVisualPickerOpen(false);
      return;
    }

    const nextOption = buildVisualOptionFromPayload(sourcePayload, visualId, option);
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
      <Card className="p-6 mt-6 bg-card border border-border/40 shadow-[var(--cursor-shadow-sm)]">
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
        className="h-7 shrink-0 gap-1.5 whitespace-nowrap text-[11px] border-border/40 hover:bg-secondary/60 hover:text-[var(--cursor-danger)] transition-all duration-200 ease-[cubic-bezier(0.2,0,0,1)]"
        onClick={() => setViewMode('table')}
      >
        <Table2 className="h-3 w-3" />
        Tabla
      </Button>
      <Button
        variant={viewMode === 'hybrid' ? "default" : "outline"}
        size="sm"
        className="h-7 shrink-0 gap-1.5 whitespace-nowrap text-[11px] border-border/40 hover:bg-secondary/60 hover:text-[var(--cursor-danger)] transition-all duration-200 ease-[cubic-bezier(0.2,0,0,1)]"
        onClick={() => setViewMode('hybrid')}
      >
        <Rows3 className="h-3 w-3" />
        Híbrida
      </Button>
      <Button
        variant={viewMode === 'chart' ? "default" : "outline"}
        size="sm"
        className="h-7 shrink-0 gap-1.5 whitespace-nowrap text-[11px] border-border/40 hover:bg-secondary/60 hover:text-[var(--cursor-danger)] transition-all duration-200 ease-[cubic-bezier(0.2,0,0,1)]"
        onClick={() => setViewMode('chart')}
      >
        <BarChart3 className="h-3 w-3" />
        Gráfico
      </Button>
    </>
  ) : null;

  const visualButton = (
    <Popover open={visualPickerOpen} onOpenChange={setVisualPickerOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7 shrink-0 text-muted-foreground hover:text-foreground hover:bg-secondary/60 transition-all duration-200 ease-[cubic-bezier(0.2,0,0,1)]"
          disabled={!canOpenVisualPicker}
          title={canOpenVisualPicker ? "Cambiar tipo de gráfico" : "No hay otros tipos de gráfico disponibles"}
          aria-label="Cambiar tipo de gráfico"
        >
          <BarChart3 className="h-4 w-4" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-[320px] p-3">
        <div className="space-y-3">
          <div>
            <div className="text-sm font-semibold text-foreground">Cambiar tipo de gráfico</div>
            <p className="text-xs text-muted-foreground">
              Cambia la lectura visual sin recalcular el análisis.
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
                            "w-full rounded-lg border px-3 py-2 text-left transition-all duration-200 ease-[cubic-bezier(0.2,0,0,1)]",
                              activeVisualId === entry.id
                                ? "border-foreground/20 bg-foreground/5 shadow-xs"
                                : "border-border/40 bg-background hover:bg-muted/40 hover:border-border/60",
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
  const [isEvidenceOpen, setIsEvidenceOpen] = React.useState(false);
  const evidenceData = React.useMemo(() => {
    const opt = (localOption || option) as any;
    return opt?.evidence_bundle || {
      plan_hash: opt?.plan_hash || opt?.plan_hash_sha256,
      computed_facts: opt?.computed_facts || opt?.hard_facts || {},
      sql_canonical_query: opt?.sql_canonical_query || opt?.sql || opt?.query,
      row_count: opt?.row_count || opt?.total_rows,
      execution_timestamp: opt?.execution_timestamp,
      filters_applied: opt?.filters_applied || opt?.chart_base_filters,
    };
  }, [localOption, option]);

  const evidenceButton = (
    <Button
      variant="outline"
      size="sm"
      className="h-8 shrink-0 gap-1.5 whitespace-nowrap text-xs border-border/40 hover:bg-secondary/60 hover:text-[var(--cursor-danger)] transition-all duration-200 ease-[cubic-bezier(0.2,0,0,1)]"
      onClick={() => setIsEvidenceOpen(true)}
      title="Ver evidencia matemática y auditoría SQL"
    >
      <ShieldCheck className="h-3.5 w-3.5 text-emerald-500" />
      <span>Evidencia</span>
    </Button>
  );

  // Menú compacto de tres puntos para el modo widget: agrupa cambio de vista y evidencia
  const viewMenu = !hideModeSwitch && !presentationMode ? (
    <Popover open={viewMenuOpen} onOpenChange={setViewMenuOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7 shrink-0 text-muted-foreground hover:text-foreground hover:bg-secondary/60 transition-all duration-200 ease-[cubic-bezier(0.2,0,0,1)]"
          title="Más opciones"
          aria-label="Más opciones"
        >
          <MoreHorizontal className="h-4 w-4" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-52 p-1.5">
        <div className="px-2 pb-1 pt-0.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
          Vista
        </div>
        {supportsTabularMode ? (
          <>
            <CompactMenuItem
              active={viewMode === 'chart'}
              icon={<BarChart3 className="h-3.5 w-3.5" />}
              label="Gráfico"
              onSelect={() => { setViewMode('chart'); setViewMenuOpen(false); }}
            />
            <CompactMenuItem
              active={viewMode === 'hybrid'}
              icon={<Rows3 className="h-3.5 w-3.5" />}
              label="Híbrida"
              onSelect={() => { setViewMode('hybrid'); setViewMenuOpen(false); }}
            />
            <CompactMenuItem
              active={viewMode === 'table'}
              icon={<Table2 className="h-3.5 w-3.5" />}
              label="Tabla"
              onSelect={() => { setViewMode('table'); setViewMenuOpen(false); }}
            />
          </>
        ) : (
          <div className="px-2 py-1.5 text-xs text-muted-foreground">Solo vista de gráfico</div>
        )}
        <div className="my-1 h-px bg-border/60" />
        <button
          type="button"
          onClick={() => { setIsEvidenceOpen(true); setViewMenuOpen(false); }}
          className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs text-muted-foreground transition-colors hover:bg-muted/50 hover:text-foreground"
        >
          <ShieldCheck className="h-3.5 w-3.5 shrink-0 text-emerald-500" />
          <span className="flex-1 truncate">Ver evidencia</span>
        </button>
        {onDelete && (
          <>
            <div className="my-1 h-px bg-border/60" />
            <button
              type="button"
              onClick={() => { onDelete(); setViewMenuOpen(false); }}
              className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs text-destructive transition-all duration-150 ease-[cubic-bezier(0.2,0,0,1)] hover:bg-destructive/10"
            >
              <Trash2 className="h-3.5 w-3.5 shrink-0" />
              <span className="flex-1 truncate">Eliminar</span>
            </button>
          </>
        )}
      </PopoverContent>
    </Popover>
  ) : null;

  const showVisualButton = !hideVisualPicker && !presentationMode;
  const headerActions = (
    <>
      {showVisualButton ? visualButton : null}
      {viewMenu}
    </>
  );
  const hasToolbarControls = Boolean(modeSwitch || toolbarPrefix || showVisualButton || !presentationMode);

  const displayOption = React.useMemo(() => {
    if (!selectedCategory || !localOption) return localOption;
    try {
      const next = clonePreservingFunctions(localOption) as any;
      if (!Array.isArray(next.series)) return localOption;

      const normSelected = selectedCategory.toLowerCase().trim();
      const categories: string[] = Array.isArray(next.xAxis?.data)
        ? next.xAxis.data.map((c: any) => String(c ?? '').toLowerCase().trim())
        : [];

      next.series = next.series.map((serie: any) => {
        if (!serie || !Array.isArray(serie.data)) return serie;
        const dimData = serie.data.map((item: any, idx: number) => {
          let itemName = '';
          if (typeof item === 'object' && item !== null) {
            itemName = String(item.name ?? item.raw_name ?? categories[idx] ?? '').toLowerCase().trim();
          } else {
            itemName = categories[idx] || '';
          }

          const isMatch = itemName === normSelected || (itemName.length >= 3 && normSelected.includes(itemName)) || (normSelected.length >= 3 && itemName.includes(normSelected));
          if (typeof item === 'object' && item !== null) {
            return {
              ...item,
              itemStyle: {
                ...(item.itemStyle || {}),
                opacity: isMatch ? 1 : 0.35,
              },
            };
          } else {
            return {
              value: item,
              itemStyle: {
                opacity: isMatch ? 1 : 0.35,
              },
            };
          }
        });
        return { ...serie, data: dimData };
      });
      return next;
    } catch {
      return localOption;
    }
  }, [localOption, selectedCategory]);

  const computedChartHeight = React.useMemo(() => {
    if (isWidget) return undefined;

    // Detectar barras horizontales (yAxis tipo category o data en yAxis)
    const yAxisObj = Array.isArray(displayOption?.yAxis) ? displayOption.yAxis[0] : displayOption?.yAxis;
    const xAxisObj = Array.isArray(displayOption?.xAxis) ? displayOption.xAxis[0] : displayOption?.xAxis;

    const isHorizontalBar = yAxisObj?.type === 'category' || (Array.isArray(yAxisObj?.data) && yAxisObj.data.length > 0);
    const categoryCount = isHorizontalBar
      ? (Array.isArray(yAxisObj?.data) ? yAxisObj.data.length : 0)
      : (Array.isArray(xAxisObj?.data) ? xAxisObj.data.length : 0);

    if (isHorizontalBar && categoryCount > 0) {
      // 44px por categoría + 100px para márgenes/legend/eje, con un mínimo ejecutivo de 480px
      return Math.max(480, Math.min(760, categoryCount * 44 + 100));
    }

    // Para cualquier otro gráfico en el canvas (líneas, barras verticales, pie, treemap):
    // Altura ejecutiva estándar de proporción dorada (480px)
    return 480;
  }, [isWidget, displayOption]);

  const renderChartCanvas = () => {
    const hasData = hasRenderableSeriesData(displayOption);

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
      <div 
        className={`relative w-full ${isWidget ? 'h-full flex-1 min-h-0' : 'min-h-[440px]'}`}
        style={isWidget ? undefined : { height: `${computedChartHeight}px` }}
      >
        <EChartsChart
          option={displayOption}
          onChartClick={handleInternalChartClick}
          onEvents={legendEvents}
          interactionMode={interactionMode}
          style={isWidget ? { width: '100%', height: '100%' } : { width: '100%', height: `${computedChartHeight}px` }}
        />
      </div>
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
    return (
      <div className="w-full h-full min-h-0 min-w-0 flex flex-col">
        {headerPortalContainer && typeof window !== 'undefined'
          ? createPortal(headerActions, headerPortalContainer)
          : null}
        {!presentationMode && (activeGlobalFilter || hasGovernanceAdjustment) && (
          <div className="flex shrink-0 items-center gap-2 pb-0.5">
            <div className="flex min-w-0 flex-1 items-center gap-1.5 overflow-hidden">
              {activeGlobalFilter && (
                <span className="inline-flex min-w-0 items-center gap-1 rounded-full bg-secondary px-2 py-0.5 text-[11px] text-muted-foreground">
                  <Filter className="h-3 w-3 shrink-0 text-muted-foreground/60" />
                  <span className="max-w-[180px] truncate">{activeGlobalFilter}</span>
                  <button
                    type="button"
                    onClick={handleClearGlobalFilter}
                    className="ml-0.5 rounded-full p-0.5 text-muted-foreground/60 transition-colors hover:text-foreground"
                    title="Limpiar filtro global"
                    aria-label="Limpiar filtro global"
                  >
                    <X className="h-3 w-3" />
                  </button>
                </span>
              )}
              {hasGovernanceAdjustment && (
                <span
                  className="inline-flex shrink-0 items-center gap-1 rounded-full bg-secondary px-2 py-0.5 text-[11px] text-muted-foreground"
                  title={visualGovernance?.blocked_reason || "Ajustado por validez visual"}
                >
                  <AlertTriangle className="h-3 w-3" />
                  Ajustado
                </span>
              )}
            </div>
          </div>
        )}
        {!presentationMode && !headerPortalContainer && (
          <div className="flex shrink-0 items-center justify-end gap-0.5 pb-0.5">
            {toolbarPrefix}
            {showVisualButton ? visualButton : null}
            {viewMenu}
          </div>
        )}
        {visualError && (
          <div className="mb-2 shrink-0">
            <VisualStatePanel
              icon={<AlertTriangle className="h-7 w-7" />}
              title="Reemplazo visual no disponible"
              message={visualError}
              tone="warning"
            />
          </div>
        )}
        {viewMode === 'table' && supportsTabularMode ? (
          <div className="flex-1 min-h-0 overflow-hidden">{renderTablePanel()}</div>
        ) : viewMode === 'hybrid' && supportsTabularMode ? (
          <div className="flex flex-1 min-h-0 flex-col gap-3">
            <div className="flex min-h-[200px] min-h-0 flex-1 overflow-hidden">{renderChartCanvas()}</div>
            <div className="min-h-[180px] min-h-0 overflow-hidden">{renderTablePanel()}</div>
          </div>
        ) : (() => {
          const hasData = hasRenderableSeriesData(localOption);
          if (!hasData) {
            return (
              <div className="flex-1 min-h-0">
                <VisualStatePanel
                  icon={<BarChart3 className="h-8 w-8" />}
                  title="Sin datos visualizables"
                  message={`El análisis para "${title || 'este visual'}" no devolvió una estructura suficiente para pintar el gráfico. El resultado sigue protegido y puede leerse desde la tabla o cambiando de visual.`}
                  tone="muted"
                />
              </div>
            )
          }
          return (
            <div className="flex-1 min-h-0 w-full overflow-hidden">{renderChartCanvas()}</div>
          );
        })()}

        <EvidenceDrawer
          isOpen={isEvidenceOpen}
          onClose={() => setIsEvidenceOpen(false)}
          evidence={evidenceData}
          title={title ? `Evidencia: ${title}` : "Evidencia Analítica"}
        />
      </div>
    );
  }

  return (
    <div className="mt-6 w-full min-w-0 overflow-hidden animate-fade-slide-in"> 
      <Card variant="interactive" className="w-full px-5 py-3 min-w-0 overflow-hidden flex flex-col rounded-xl group shadow-[var(--cursor-shadow-md)] hover:shadow-[var(--cursor-shadow-lg)] transition-all duration-300 ease-[cubic-bezier(0.2,0,0,1)]">
        {/* Header limpio estilo Cursor/Apple: 1 sola línea con título y controles a la derecha */}
        <div className="flex items-center justify-between gap-4 pb-2 mb-2 border-b border-border/20 shrink-0">
          {title ? (
            <div className="min-w-0 flex-1">
              <h3 className="text-base font-medium text-foreground leading-snug tracking-tight truncate" title={title}>
                {title}
              </h3>
            </div>
          ) : <div className="flex-1" />}

          <div className="flex items-center gap-1 shrink-0 opacity-0 group-hover:opacity-100 has-[[data-state=open]]:opacity-100 transition-opacity duration-150">
            {!presentationMode && visualGovernance?.recommended_label && visualGovernance.recommended_label !== visualGovernance.applied_label && (
              <span
                className="inline-flex items-center gap-1 rounded-full bg-secondary px-1.5 py-px text-[10px] font-medium text-muted-foreground"
                title={`Recomendado: ${visualGovernance.recommended_label}`}
              >
                <Sparkles className="h-2.5 w-2.5 shrink-0 text-accent" />
                <span className="max-w-[96px] truncate">{visualGovernance.recommended_label}</span>
              </span>
            )}
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7 text-muted-foreground hover:text-foreground hover:bg-secondary/60 transition-colors duration-150"
              onClick={() => onSave(localOption)}
              title="Guardar visual"
              data-testid="chart-save-button"
            >
              <SaveIcon className="h-4 w-4" />
            </Button>
            {showVisualButton ? visualButton : null}
            {viewMenu}
          </div>
        </div>

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
          <div className="flex-1 min-h-0 overflow-hidden">{renderTablePanel()}</div>
        ) : viewMode === 'hybrid' && supportsTabularMode ? (
          <div className="flex flex-1 min-h-0 flex-col gap-4">
            <div className="flex min-h-[360px] min-h-0 flex-1 overflow-hidden">
              {renderChartCanvas()}
            </div>
            <div className="min-h-[260px] min-h-0 overflow-hidden">
              {renderTablePanel()}
            </div>
          </div>
        ) : (
          <div 
            className={`w-full overflow-hidden ${isWidget ? 'flex-1 min-h-0' : 'min-h-[440px]'}`}
            style={isWidget ? undefined : { height: `${computedChartHeight}px` }}
          >
            {renderChartCanvas()}
          </div>
        )}

        <EvidenceDrawer
          isOpen={isEvidenceOpen}
          onClose={() => setIsEvidenceOpen(false)}
          evidence={evidenceData}
          title={title ? `Evidencia: ${title}` : "Evidencia Analítica"}
        />
      </Card>
    </div>
  );
}

export const ChartsReport = React.memo(ChartsReportComponent);
