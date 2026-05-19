import type { SavedReport } from '@/lib/state';

type PrimitiveValue = string | number | boolean | null;

export interface ExecutiveNarrativeWidgetSnapshot {
  report_id: string;
  title: string;
  widget_type: string;
  visual_type: string | null;
  file_id: string | null;
  metric: string | null;
  dimension: string | null;
  aggregation: string | null;
  facts: string[];
}

const EMPTY_FACTS: string[] = [];

const normalizeText = (value: unknown): string =>
  String(value ?? '')
    .replace(/\0/g, '')
    .replace(/\s+/g, ' ')
    .trim();

const formatFactValue = (value: unknown): PrimitiveValue => {
  if (value === null || value === undefined) return null;
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'boolean') return value;
  const text = normalizeText(value);
  return text || null;
};

const normalizePointLabel = (value: unknown, fallback: string): string => {
  const normalized = normalizeText(value);
  return normalized || fallback;
};

const inferVisualType = (option: any, widgetType: string): string | null => {
  const series = Array.isArray(option?.series)
    ? option.series
    : option?.series
      ? [option.series]
      : [];
  const firstSeries = series[0];
  if (typeof firstSeries?.type === 'string' && firstSeries.type.trim()) {
    return firstSeries.type.trim();
  }
  if (typeof option?.visual_protocol === 'string' && option.visual_protocol.trim()) {
    return option.visual_protocol.trim();
  }
  return widgetType || null;
};

const extractContract = (content: any, option: any) => {
  const contract = option?.query_contract || content?.query_contract || null;
  if (!contract || typeof contract !== 'object') {
    return {
      metric: null,
      dimension: null,
      aggregation: null,
    };
  }

  const metric = normalizeText(
    contract.metric
      || contract.value_column
      || (Array.isArray(contract.metrics) ? contract.metrics[0] : '')
  ) || null;
  const dimension = normalizeText(
    contract.dimension
      || (Array.isArray(contract.group_by) ? contract.group_by[0] : contract.group_by)
  ) || null;
  const aggregation = normalizeText(contract.aggregation) || null;

  return { metric, dimension, aggregation };
};

const extractChartFacts = (option: any, visualType: string | null): string[] => {
  const series = Array.isArray(option?.series)
    ? option.series
    : option?.series
      ? [option.series]
      : [];
  if (series.length === 0) return EMPTY_FACTS;

  const xAxis = Array.isArray(option?.xAxis) ? option.xAxis[0] : option?.xAxis;
  const axisLabels = Array.isArray(xAxis?.data) ? xAxis.data : [];
  const facts: string[] = [];

  if (visualType) {
    facts.push(`Visual mostrado: ${visualType}.`);
  }

  series.slice(0, 2).forEach((entry: any, seriesIndex: number) => {
    const seriesName = normalizeText(entry?.name) || `Serie ${seriesIndex + 1}`;
    const points = Array.isArray(entry?.data) ? entry.data : [];
    const samples = points.slice(0, 6).map((point: unknown, pointIndex: number) => {
      if (Array.isArray(point)) {
        const label = normalizePointLabel(point[0], `Punto ${pointIndex + 1}`);
        const value = formatFactValue(point[1]);
        return value === null ? null : `${label}=${value}`;
      }

      if (point && typeof point === 'object') {
        const pointRecord = point as Record<string, unknown>;
        const label = normalizePointLabel(
          pointRecord.name ?? axisLabels[pointIndex],
          `Punto ${pointIndex + 1}`
        );
        const rawValue = Array.isArray(pointRecord.value)
          ? pointRecord.value[1] ?? pointRecord.value[0]
          : pointRecord.value;
        const value = formatFactValue(rawValue);
        return value === null ? null : `${label}=${value}`;
      }

      const label = normalizePointLabel(axisLabels[pointIndex], `Punto ${pointIndex + 1}`);
      const value = formatFactValue(point);
      return value === null ? null : `${label}=${value}`;
    }).filter((sample: string | null): sample is string => Boolean(sample));

    if (samples.length > 0) {
      facts.push(`${seriesName}: ${samples.join(', ')}.`);
    }
  });

  return facts;
};

const extractTableFacts = (
  rows: Record<string, unknown>[],
  metric: string | null,
  dimension: string | null
): string[] => {
  if (!Array.isArray(rows) || rows.length === 0) return EMPTY_FACTS;

  const facts: string[] = [`Tabla visible con ${rows.length} filas.`];
  const previewRows = rows.slice(0, 5);

  if (metric && dimension) {
    const samples = previewRows
      .map((row) => {
        const label = formatFactValue(row[dimension]);
        const value = formatFactValue(row[metric]);
        if (label === null || value === null) return null;
        return `${label}=${value}`;
      })
      .filter((sample): sample is string => Boolean(sample));

    if (samples.length > 0) {
      facts.push(`Muestra visible: ${samples.join(', ')}.`);
      return facts;
    }
  }

  const firstRow = previewRows[0];
  if (firstRow && typeof firstRow === 'object') {
    const columns = Object.keys(firstRow).slice(0, 4);
    if (columns.length > 0) {
      facts.push(`Columnas visibles: ${columns.join(', ')}.`);
    }
  }

  return facts;
};

export const normalizeExecutiveFilters = (rawFilters: Record<string, unknown>): Record<string, string> => {
  const entries = Object.entries(rawFilters || {}).filter(([key, value]) => {
    if (key.startsWith('__')) return false;
    const normalizedValue = normalizeText(value);
    return normalizedValue.length > 0;
  });

  return Object.fromEntries(entries.map(([key, value]) => [key, normalizeText(value)]));
};

export const buildExecutiveWidgetSnapshot = ({
  report,
  chartOption,
  tableData,
}: {
  report: SavedReport;
  chartOption?: any;
  tableData?: Record<string, unknown>[];
}): ExecutiveNarrativeWidgetSnapshot => {
  const content = report?.content || {};
  const widgetType = normalizeText(content?.type || report?.type || 'widget') || 'widget';
  const innerContent = content?.content || {};
  const narrativeSource = chartOption || (widgetType === 'chart' ? innerContent : innerContent?.original_chart_option) || {};
  const visualType = inferVisualType(narrativeSource, widgetType);
  const { metric, dimension, aggregation } = extractContract(innerContent, narrativeSource);
  const facts = widgetType === 'table'
    ? extractTableFacts(Array.isArray(tableData) ? tableData : (innerContent?.data || []), metric, dimension)
    : extractChartFacts(narrativeSource, visualType);

  return {
    report_id: report.id,
    title: normalizeText(report.title) || 'Widget sin titulo',
    widget_type: widgetType,
    visual_type: visualType,
    file_id: normalizeText(report.file_id) || null,
    metric,
    dimension,
    aggregation,
    facts,
  };
};
