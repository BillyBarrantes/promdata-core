import { EChartsOption } from 'echarts';

type AggregationType = 'sum' | 'count' | 'avg' | 'min' | 'max';
const HIERARCHY_FALLBACK_COLORS = ['#2563eb', '#10b981', '#f59e0b', '#8b5cf6', '#f43f5e', '#0ea5e9'];

export interface WidgetQueryContract {
  intent_type?: string;
  visual_protocol?: string;
  aggregation?: AggregationType;
  metric?: string;
  metrics?: string[];
  value_column?: string;
  dimension?: string;
  group_by?: string[];
  limit?: number;
  barmode?: 'stacked' | 'group';
  title?: string;
}

const reactiveChartOptionCache = new WeakMap<
  object,
  WeakMap<Record<string, unknown>[], Map<string, EChartsOption>>
>();

function buildReactiveChartOptionCacheKey(contract: WidgetQueryContract): string {
  return JSON.stringify({
    aggregation: contract.aggregation || 'sum',
    metric: contract.metric || null,
    metrics: contract.metrics || [],
    value_column: contract.value_column || null,
    dimension: contract.dimension || null,
    group_by: contract.group_by || [],
    limit: contract.limit ?? null,
    barmode: contract.barmode || null,
    intent_type: contract.intent_type || null,
    visual_protocol: contract.visual_protocol || null,
  });
}

function getCachedReactiveChartOption(
  baseOption: EChartsOption,
  filteredRows: Record<string, unknown>[],
  cacheKey: string
): EChartsOption | null {
  const baseCache = reactiveChartOptionCache.get(baseOption as object);
  if (!baseCache) return null;

  const rowsCache = baseCache.get(filteredRows);
  if (!rowsCache) return null;

  return rowsCache.get(cacheKey) || null;
}

function rememberReactiveChartOption(
  baseOption: EChartsOption,
  filteredRows: Record<string, unknown>[],
  cacheKey: string,
  nextOption: EChartsOption
): EChartsOption {
  let baseCache = reactiveChartOptionCache.get(baseOption as object);
  if (!baseCache) {
    baseCache = new WeakMap<Record<string, unknown>[], Map<string, EChartsOption>>();
    reactiveChartOptionCache.set(baseOption as object, baseCache);
  }

  let rowsCache = baseCache.get(filteredRows);
  if (!rowsCache) {
    rowsCache = new Map<string, EChartsOption>();
    baseCache.set(filteredRows, rowsCache);
  }

  rowsCache.set(cacheKey, nextOption);
  return nextOption;
}

function getRepresentativeColumnSample(
  rows: Record<string, unknown>[],
  columnName: string,
  maxSample: number = 250
): unknown[] {
  if (rows.length === 0) return [];

  const targetSize = Math.min(maxSample, rows.length);
  const sampled: unknown[] = [];
  const visited = new Set<number>();

  for (let i = 0; i < targetSize; i += 1) {
    const index = Math.min(rows.length - 1, Math.floor((i * rows.length) / targetSize));
    if (visited.has(index)) continue;
    visited.add(index);

    const value = rows[index]?.[columnName];
    if (value !== null && value !== undefined) {
      sampled.push(value);
    }
  }

  return sampled;
}

function parseNumericLike(value: unknown): number | null {
  if (typeof value === 'number') {
    return Number.isFinite(value) ? value : null;
  }

  if (typeof value === 'bigint') {
    const asNumber = Number(value);
    return Number.isFinite(asNumber) ? asNumber : null;
  }

  if (typeof value !== 'string') return null;

  const text = value.trim();
  if (!text) return null;

  const compact = text.replace(/\s+/g, '');

  // 1,234,567.89
  if (/^[+-]?\d{1,3}(,\d{3})+(\.\d+)?$/.test(compact)) {
    const parsed = Number(compact.replace(/,/g, ''));
    return Number.isFinite(parsed) ? parsed : null;
  }

  // 1.234.567,89
  if (/^[+-]?\d{1,3}(\.\d{3})+(,\d+)?$/.test(compact)) {
    const normalized = compact.replace(/\./g, '').replace(',', '.');
    const parsed = Number(normalized);
    return Number.isFinite(parsed) ? parsed : null;
  }

  // 1234,56 o 1234.56 o 1234
  if (/^[+-]?\d+([.,]\d+)?$/.test(compact)) {
    const parsed = Number(compact.replace(',', '.'));
    return Number.isFinite(parsed) ? parsed : null;
  }

  return null;
}

function isNumericColumn(rows: Record<string, unknown>[], columnName: string): boolean {
  const sample = getRepresentativeColumnSample(rows, columnName, 250);
  if (sample.length === 0) return false;
  if (sample.some((value) => typeof value === 'boolean')) return false;

  const parseableCount = sample.filter((value) => parseNumericLike(value) !== null).length;
  return parseableCount / sample.length >= 0.95;
}

function looksLikeBooleanColumn(rows: Record<string, unknown>[], columnName: string): boolean {
  const sample = getRepresentativeColumnSample(rows, columnName, 150);

  if (sample.length === 0) return false;

  return sample.every((value) => {
    if (typeof value === 'boolean') return true;
    if (typeof value === 'number') return value === 0 || value === 1;
    if (typeof value === 'bigint') return value === BigInt(0) || value === BigInt(1);
    const normalized = String(value).trim().toLowerCase();
    return normalized === 'true' || normalized === 'false' || normalized === '0' || normalized === '1';
  });
}

function looksLikeDateColumn(columnName: string, rows: Record<string, unknown>[]): boolean {
  const normalizedName = columnName.toLowerCase();
  if (/(fecha|fec|date|time|periodo|period|mes|anio|year|month|day|caduc|expiry|expir)/.test(normalizedName)) {
    return true;
  }

  const sample = getRepresentativeColumnSample(rows, columnName, 40);

  if (sample.length === 0) return false;

  const dateLikeCount = sample.filter((value) => {
    if (value instanceof Date) return true;
    const asText = String(value).trim();
    if (!asText) return false;
    if (/^\d{4}-\d{2}-\d{2}/.test(asText) || /^\d{2}\/\d{2}\/\d{4}/.test(asText)) {
      return true;
    }
    return !Number.isNaN(Date.parse(asText));
  }).length;

  return dateLikeCount / sample.length >= 0.6;
}

function isPreferredMetricColumn(columnName: string): boolean {
  const normalizedName = columnName.toLowerCase();
  return /(stock|cantidad|qty|valor|total|monto|importe|venta|saldo|disponible|units|unidades)/.test(normalizedName);
}

function looksLikeIdentifierColumn(columnName: string, rows: Record<string, unknown>[]): boolean {
  const normalizedName = columnName.toLowerCase();
  const nameSignalsId = /(material|lote|sku|item|codigo|cod|id|identif)/.test(normalizedName);
  if (!nameSignalsId) return false;

  const sample = getRepresentativeColumnSample(rows, columnName, 300)
    .map((value) => String(value).trim())
    .filter(Boolean);

  if (sample.length === 0) return false;

  const integerLikeRatio = sample.filter((value) => /^\d{4,}$/.test(value)).length / sample.length;
  const uniqueRatio = new Set(sample).size / sample.length;
  return integerLikeRatio >= 0.7 && (uniqueRatio >= 0.6 || uniqueRatio <= 0.2);
}

function looksLikeEncodedDimensionColumn(columnName: string, rows: Record<string, unknown>[]): boolean {
  const sampleRaw = getRepresentativeColumnSample(rows, columnName, 300);
  if (sampleRaw.length === 0) return false;

  const textSample = sampleRaw
    .map((value) => String(value).trim())
    .filter(Boolean);
  if (textSample.length === 0) return false;

  const stringBackedRatio = sampleRaw.filter((value) => typeof value === 'string').length / sampleRaw.length;
  const numericLikeRatio = textSample.filter((value) => parseNumericLike(value) !== null).length / textSample.length;
  const integerLikeRatio = textSample.filter((value) => /^[+-]?\d+$/.test(value)).length / textSample.length;
  const leadingZeroRatio = textSample.filter((value) => /^0\d{2,}$/.test(value)).length / textSample.length;
  const uniqueRatio = new Set(textSample).size / textSample.length;

  const lengthCounts = new Map<number, number>();
  textSample.forEach((value) => {
    const current = lengthCounts.get(value.length) || 0;
    lengthCounts.set(value.length, current + 1);
  });
  const fixedWidthRatio = Math.max(...Array.from(lengthCounts.values())) / textSample.length;

  const normalizedName = columnName.toLowerCase();
  const dimensionNameSignal = /(ubic|location|warehouse|almacen|store|tienda|centro|branch|nodo|zona|depo|deposito|sede|planta)/.test(normalizedName);
  const behavesLikeDimension = uniqueRatio <= 0.35 || uniqueRatio >= 0.6;

  if (
    dimensionNameSignal &&
    numericLikeRatio >= 0.7 &&
    integerLikeRatio >= 0.7 &&
    (stringBackedRatio >= 0.5 || leadingZeroRatio >= 0.03 || fixedWidthRatio >= 0.55)
  ) {
    return true;
  }

  return (
    numericLikeRatio >= 0.85 &&
    integerLikeRatio >= 0.8 &&
    (stringBackedRatio >= 0.7 || leadingZeroRatio >= 0.05) &&
    (dimensionNameSignal || fixedWidthRatio >= 0.7) &&
    behavesLikeDimension
  );
}

export function inferQueryContractFromChartOption(
  baseOption: EChartsOption,
  rows: Record<string, unknown>[]
): WidgetQueryContract | null {
  if (!rows.length) return null;

  const xAxis = Array.isArray(baseOption.xAxis) ? baseOption.xAxis[0] : baseOption.xAxis;
  const yAxis = Array.isArray(baseOption.yAxis) ? baseOption.yAxis[0] : baseOption.yAxis;
  const categories = (xAxis?.type === 'category' ? xAxis?.data : yAxis?.type === 'category' ? yAxis?.data : []) || [];
  const hierarchyItems = getFrozenHierarchyItems(baseOption);
  const seriesNames = (Array.isArray(baseOption.series) ? baseOption.series : [])
    .map((series: any) => String(series?.name ?? ''))
    .filter((name) => name && !['Series', 'Valor', 'Total'].includes(name));

  const columns = Object.keys(rows[0] || {});
  let primaryDimension: string | null = null;
  let secondaryDimension: string | null = null;

  if (categories.length > 0) {
    primaryDimension = resolveDimensionFromDisplayItems(rows, categories) || null;
  }

  if (!primaryDimension && hierarchyItems.length > 0) {
    primaryDimension = resolveDimensionFromDisplayItems(rows, hierarchyItems) || null;
  }

  if (seriesNames.length > 1) {
    secondaryDimension = resolveDimensionFromDisplayItems(
      rows,
      seriesNames,
      new Set([primaryDimension].filter(Boolean) as string[])
    ) || null;
  }

  const nonNumericColumns = columns.filter((column) => !isNumericColumn(rows, column));
  const numericColumns = columns.filter(
    (column) =>
      isNumericColumn(rows, column) &&
      !looksLikeDateColumn(column, rows) &&
      !looksLikeEncodedDimensionColumn(column, rows)
  );
  const preferredMetricCandidates = numericColumns.filter((column) => isPreferredMetricColumn(column));
  const metricCandidates = preferredMetricCandidates.length > 0 ? preferredMetricCandidates : numericColumns;

  if (!primaryDimension) {
    primaryDimension = nonNumericColumns.find((column) => {
      const uniqueCount = new Set(rows.map((row) => String(row[column] ?? 'N/A'))).size;
      return uniqueCount > 1;
    }) || nonNumericColumns[0] || columns[0] || null;
  }

  if (!secondaryDimension && seriesNames.length > 1) {
    secondaryDimension = nonNumericColumns.find((column) => column !== primaryDimension) || null;
  }

  const rankedMetricCandidates = columns
    .filter((column) => column !== primaryDimension && column !== secondaryDimension)
    .filter((column) => metricCandidates.includes(column))
    .sort((left, right) => {
      const leftSum = rows.reduce((sum, row) => sum + toNumber(row[left]), 0);
      const rightSum = rows.reduce((sum, row) => sum + toNumber(row[right]), 0);
      return rightSum - leftSum;
    });

  const metricColumn = rankedMetricCandidates[0]
    || metricCandidates.find((column) => column !== primaryDimension && column !== secondaryDimension)
    || metricCandidates[0]
    || columns.find((column) => column !== primaryDimension && column !== secondaryDimension)
    || columns[0];

  if (!primaryDimension || !metricColumn) {
    return null;
  }

  return {
    intent_type: 'distribution',
    metric: metricColumn,
    dimension: primaryDimension,
    group_by: secondaryDimension ? [secondaryDimension] : undefined,
    aggregation: 'sum',
    limit: categories.length || 10,
    barmode: secondaryDimension ? 'stacked' : undefined,
  };
}

function getFrozenHierarchyItems(baseOption: EChartsOption): string[] {
  const series = Array.isArray(baseOption.series) ? baseOption.series : [baseOption.series];
  const primarySeries = series.find(Boolean) as any;
  const primaryType = String(primarySeries?.type ?? '').toLowerCase();

  if (!['pie', 'treemap', 'funnel'].includes(primaryType)) {
    return [];
  }

  const rawItems = Array.isArray(primarySeries?.data) ? primarySeries.data : [];
  return rawItems
    .map((item: any) => {
      if (item && typeof item === 'object' && !Array.isArray(item)) {
        const candidate =
          item.raw_name
          ?? item.rawName
          ?? item.full_name
          ?? item.fullName
          ?? item.name;
        return typeof candidate === 'string' ? candidate : null;
      }

      return typeof item === 'string' ? item : null;
    })
    .filter((item: string | null): item is string => Boolean(item && item.trim()));
}

function resolveDimensionFromDisplayItems(
  rows: Record<string, unknown>[],
  displayItems: unknown[],
  excludedColumns: Set<string> = new Set()
): string | null {
  if (!rows.length || displayItems.length === 0) return null;

  const normalizedItems = displayItems
    .map((item) => String(item ?? '').trim())
    .filter(Boolean);

  if (normalizedItems.length === 0) return null;

  const columns = Object.keys(rows[0] || {}).filter((column) => !excludedColumns.has(column));
  let bestColumn: string | null = null;
  let bestScore = -1;

  for (const column of columns) {
    if (!isUsableDimensionColumn(rows, column)) continue;

    const coverage = getDimensionCoverageScore(rows, column, normalizedItems);
    if (coverage <= 0) continue;

    let score = coverage;
    if (!isNumericColumn(rows, column)) score += 0.05;
    if (!looksLikeIdentifierColumn(column, rows)) score += 0.08;
    if (!looksLikeDateColumn(column, rows)) score += 0.03;

    const sampledValues = rows
      .slice(0, 1000)
      .map((row) => String(row[column] ?? '').trim())
      .filter(Boolean);
    const uniqueCount = new Set(sampledValues).size;
    if (normalizedItems.length > 0 && uniqueCount > normalizedItems.length * 8) {
      score -= 0.08;
    }

    if (score > bestScore) {
      bestScore = score;
      bestColumn = column;
    }
  }

  return bestColumn;
}

function toNumber(value: unknown): number {
  const parsed = parseNumericLike(value);
  return parsed === null ? 0 : parsed;
}

function aggregate(values: number[], aggregation: AggregationType = 'sum'): number {
  if (aggregation === 'count') return values.length;
  if (values.length === 0) return 0;
  if (aggregation === 'avg') return values.reduce((sum, value) => sum + value, 0) / values.length;
  if (aggregation === 'min') return Math.min(...values);
  if (aggregation === 'max') return Math.max(...values);
  return values.reduce((sum, value) => sum + value, 0);
}

function getMetricColumn(contract: WidgetQueryContract): string | null {
  return contract.metric || contract.value_column || contract.metrics?.[0] || null;
}

function hasColumn(rows: Record<string, unknown>[], columnName: string | null | undefined): columnName is string {
  if (!columnName || rows.length === 0) return false;
  return columnName in (rows[0] || {});
}

function isUsableMetricColumn(
  rows: Record<string, unknown>[],
  columnName: string | null | undefined
): columnName is string {
  if (!columnName || rows.length === 0) return false;
  if (!(columnName in rows[0])) return false;
  if (looksLikeBooleanColumn(rows, columnName)) return false;
  if (looksLikeDateColumn(columnName, rows)) return false;
  if (looksLikeIdentifierColumn(columnName, rows)) return false;
  if (looksLikeEncodedDimensionColumn(columnName, rows)) return false;
  if (!isNumericColumn(rows, columnName)) return false;

  const hasNonZero = rows
    .slice(0, 200)
    .some((row) => Math.abs(toNumber(row[columnName])) > 0);
  return hasNonZero;
}

function isUsableDimensionColumn(
  rows: Record<string, unknown>[],
  columnName: string | null | undefined
): columnName is string {
  if (!columnName || rows.length === 0) return false;
  if (!(columnName in rows[0])) return false;
  return rows.some((row) => row[columnName] !== null && row[columnName] !== undefined);
}

function categoriesLookTemporal(categories: string[]): boolean {
  if (categories.length === 0) return false;
  const temporalCount = categories.filter((value) => Boolean(toTemporalWeekKey(value) || toTemporalMonthKey(value))).length;
  return temporalCount / categories.length >= 0.6;
}

function getDimensionCoverageScore(
  rows: Record<string, unknown>[],
  columnName: string,
  categories: string[]
): number {
  if (categories.length === 0) return 0;

  const exactValues = new Set<string>();
  const normalizedValues = new Set<string>();
  const temporalMonthValues = new Set<string>();
  const temporalWeekValues = new Set<string>();

  const maxRows = Math.min(rows.length, 5000);
  for (let i = 0; i < maxRows; i += 1) {
    const raw = rows[i]?.[columnName];
    if (raw === null || raw === undefined) continue;
    const text = String(raw);
    exactValues.add(text);
    normalizedValues.add(normalizeVisualDomainValue(text));
    const monthKey = toTemporalMonthKey(text);
    if (monthKey) temporalMonthValues.add(monthKey);
    const weekKey = toTemporalWeekKey(text);
    if (weekKey) temporalWeekValues.add(weekKey);
  }

  let matches = 0;
  for (const category of categories) {
    const normalizedCategory = normalizeVisualDomainValue(category);
    const monthKey = toTemporalMonthKey(category);
    const weekKey = toTemporalWeekKey(category);

    if (
      exactValues.has(category) ||
      normalizedValues.has(normalizedCategory) ||
      (monthKey ? temporalMonthValues.has(monthKey) : false) ||
      (weekKey ? temporalWeekValues.has(weekKey) : false)
    ) {
      matches += 1;
    }
  }

  return matches / categories.length;
}

function getTemporalRowCoverageScore(
  rows: Record<string, unknown>[],
  columnName: string,
  categories: string[]
): number {
  if (rows.length === 0 || categories.length === 0) return 0;

  const monthCategoryKeys = new Set<string>();
  const weekCategoryKeys = new Set<string>();
  categories.forEach((category) => {
    const monthKey = toTemporalMonthKey(category);
    const weekKey = toTemporalWeekKey(category);
    if (monthKey) monthCategoryKeys.add(monthKey);
    if (weekKey) weekCategoryKeys.add(weekKey);
  });

  if (monthCategoryKeys.size === 0 && weekCategoryKeys.size === 0) return 0;

  const maxRows = Math.min(rows.length, 6000);
  let seen = 0;
  let matched = 0;
  for (let i = 0; i < maxRows; i += 1) {
    const raw = rows[i]?.[columnName];
    if (raw === null || raw === undefined) continue;
    seen += 1;

    const weekKey = toTemporalWeekKey(raw);
    if (weekKey && weekCategoryKeys.has(weekKey)) {
      matched += 1;
      continue;
    }

    const monthKey = toTemporalMonthKey(raw);
    if (monthKey && monthCategoryKeys.has(monthKey)) {
      matched += 1;
    }
  }

  if (seen === 0) return 0;
  return matched / seen;
}

function getTemporalBucketCardinality(
  rows: Record<string, unknown>[],
  columnName: string
): number {
  if (rows.length === 0) return 0;

  const buckets = new Set<string>();
  const maxRows = Math.min(rows.length, 6000);
  for (let i = 0; i < maxRows; i += 1) {
    const raw = rows[i]?.[columnName];
    if (raw === null || raw === undefined) continue;
    const weekKey = toTemporalWeekKey(raw);
    if (weekKey) {
      buckets.add(weekKey);
      continue;
    }
    const monthKey = toTemporalMonthKey(raw);
    if (monthKey) buckets.add(monthKey);
  }

  return buckets.size;
}

function resolveTemporalDimensionColumn(
  rows: Record<string, unknown>[],
  categories: string[],
  excludedColumns: Set<string>
): string | null {
  if (rows.length === 0 || categories.length === 0) return null;

  const columns = Object.keys(rows[0] || {}).filter((column) => !excludedColumns.has(column));
  let bestColumn: string | null = null;
  let bestScore = -1;

  for (const column of columns) {
    if (!isUsableDimensionColumn(rows, column)) continue;
    const coverage = getDimensionCoverageScore(rows, column, categories);
    if (coverage <= 0) continue;

    let score = coverage;
    if (looksLikeDateColumn(column, rows)) score += 0.25;

    const temporalRowCoverage = getTemporalRowCoverageScore(rows, column, categories);
    if (temporalRowCoverage > 0) score += temporalRowCoverage * 0.6;

    const bucketCount = getTemporalBucketCardinality(rows, column);
    if (bucketCount > 0 && categories.length > 0) {
      const closeness = 1 - Math.min(1, Math.abs(bucketCount - categories.length) / Math.max(bucketCount, categories.length));
      score += closeness * 0.2;
    }

    if (score > bestScore) {
      bestScore = score;
      bestColumn = column;
    }
  }

  return bestColumn;
}

function resolvePrimaryDimension(
  rows: Record<string, unknown>[],
  requestedDimension: string | null | undefined,
  baseOption: EChartsOption,
  metricColumn: string | null,
  secondaryDimension: string | null | undefined
): string | null {
  if (rows.length === 0) return requestedDimension || null;

  const frozenCategories = getFrozenCategories(baseOption);
  const hasFrozenCategories = frozenCategories.length > 0;

  if (isUsableDimensionColumn(rows, requestedDimension)) {
    if (!hasFrozenCategories) return requestedDimension;
    const requestedScore = getDimensionCoverageScore(rows, requestedDimension, frozenCategories);
    if (requestedScore >= 0.35) return requestedDimension;
  }

  const excluded = new Set<string>(
    [metricColumn, secondaryDimension].filter(Boolean) as string[]
  );
  const candidates = Object.keys(rows[0] || {}).filter((column) => !excluded.has(column));
  if (candidates.length === 0) return requestedDimension || null;

  if (!hasFrozenCategories) {
    const firstNonNumeric = candidates.find((column) => !isNumericColumn(rows, column));
    return firstNonNumeric || candidates[0];
  }

  const temporalAxis = categoriesLookTemporal(frozenCategories);

  let bestColumn: string | null = null;
  let bestScore = -1;

  for (const candidate of candidates) {
    if (!isUsableDimensionColumn(rows, candidate)) continue;

    const baseScore = getDimensionCoverageScore(rows, candidate, frozenCategories);
    if (baseScore <= 0) continue;

    let score = baseScore;
    if (!isNumericColumn(rows, candidate)) score += 0.05;
    if (temporalAxis && looksLikeDateColumn(candidate, rows)) score += 0.2;

    if (score > bestScore) {
      bestScore = score;
      bestColumn = candidate;
    }
  }

  if (bestColumn) return bestColumn;
  if (isUsableDimensionColumn(rows, requestedDimension)) return requestedDimension;
  return candidates[0] || requestedDimension || null;
}

function resolveSecondaryDimension(
  rows: Record<string, unknown>[],
  secondaryDimension: string | null | undefined,
  primaryDimension: string | null
): string | null {
  if (!secondaryDimension) return null;
  if (secondaryDimension === primaryDimension) return null;
  if (!isUsableDimensionColumn(rows, secondaryDimension)) return null;
  return secondaryDimension;
}

function resolveMetricColumn(
  rows: Record<string, unknown>[],
  contract: WidgetQueryContract,
  primaryDimension: string | null | undefined,
  secondaryDimension: string | null | undefined
): string | null {
  const requested = getMetricColumn(contract);
  if (rows.length === 0) return requested || null;

  // Contrato autoritativo: si la columna existe en el dataset filtrado,
  // no aplicar override heurístico.
  if (hasColumn(rows, requested)) {
    return requested;
  }

  const excluded = new Set([primaryDimension, secondaryDimension].filter(Boolean) as string[]);
  const columns = Object.keys(rows[0] || {}).filter((column) => !excluded.has(column));
  const numericCandidates = columns.filter((column) => isUsableMetricColumn(rows, column));
  if (numericCandidates.length === 0) return requested || null;

  // Prioridad absoluta: el contrato del backend es la fuente de verdad.
  // Si la columna contractual existe y es numéricamente válida, respetarla
  // sin override heurístico. Esto previene que columnas numéricas tipo ID/código
  // desplacen la métrica real después de un cross-filter.
  if (requested && numericCandidates.includes(requested)) {
    return requested;
  }

  const preferred = numericCandidates.filter((column) => isPreferredMetricColumn(column));
  const ranked = (preferred.length > 0 ? preferred : numericCandidates).sort((left, right) => {
    const leftSum = rows.reduce((sum, row) => sum + Math.abs(toNumber(row[left])), 0);
    const rightSum = rows.reduce((sum, row) => sum + Math.abs(toNumber(row[right])), 0);
    return rightSum - leftSum;
  });

  return ranked[0] || requested || null;
}

function isTemporalTrendChart(baseOption: EChartsOption): boolean {
  const primarySeries = getSeriesTemplate(baseOption, 0, 'bar');
  return primarySeries.type === 'line' && categoriesLookTemporal(getFrozenCategories(baseOption));
}

function resolveTemporalTrendMetricColumn(
  rows: Record<string, unknown>[],
  contract: WidgetQueryContract,
  primaryDimension: string | null | undefined,
  secondaryDimension: string | null | undefined,
  currentMetric: string | null | undefined
): string | null {
  if (rows.length === 0) return currentMetric || null;

  const requestedMetric = getMetricColumn(contract);
  if (hasColumn(rows, requestedMetric)) {
    return requestedMetric;
  }

  const excluded = new Set([primaryDimension, secondaryDimension].filter(Boolean) as string[]);
  const numericCandidates = Object.keys(rows[0] || {}).filter(
    (column) => !excluded.has(column) && isUsableMetricColumn(rows, column)
  );

  // Si la métrica actual (ya resuelta previamente) es válida, mantenerla.
  if (currentMetric && numericCandidates.includes(currentMetric)) {
    return currentMetric;
  }

  // Fallback heurístico: columnas con nombres semánticos de métrica.
  const preferredCandidates = numericCandidates.filter((column) => isPreferredMetricColumn(column));
  if (preferredCandidates.length === 0) return currentMetric || null;

  return preferredCandidates[0];
}

function getSeriesTemplate(baseOption: EChartsOption, index: number, fallbackType: 'bar' | 'line' | 'pie' = 'bar'): any {
  const series = Array.isArray(baseOption.series) ? baseOption.series : [];
  const template = series[index] || series[0] || {};
  return {
    ...template,
    type: template.type || fallbackType,
  };
}

function getFrozenCategories(baseOption: EChartsOption): string[] {
  const xAxis = Array.isArray(baseOption.xAxis) ? baseOption.xAxis[0] : baseOption.xAxis;
  const yAxis = Array.isArray(baseOption.yAxis) ? baseOption.yAxis[0] : baseOption.yAxis;
  const rawCategories = xAxis?.type === 'category'
    ? xAxis?.data
    : yAxis?.type === 'category'
      ? yAxis?.data
      : [];

  return Array.isArray(rawCategories) ? rawCategories.map((item) => String(item)) : [];
}

function getFrozenSeriesNames(baseOption: EChartsOption): string[] {
  return (Array.isArray(baseOption.series) ? baseOption.series : [])
    .map((series: any) => String(series?.name ?? ''))
    .filter(Boolean);
}

function normalizeVisualDomainValue(rawValue: unknown): string {
  const text = String(rawValue ?? '').trim();
  if (!text) return '';

  const separatorMatch = text.match(/^[^:=]+[:=]\s*(.+)$/);
  if (separatorMatch?.[1]) {
    return separatorMatch[1].trim();
  }

  const normalized = text.replace(/\s+/g, ' ').trim();
  const prefixedCodeMatch = normalized.match(/^(tipo\s+almacen|almacen|tipo\s+de\s+almacen|nodo)\s+(.+)$/i);
  if (prefixedCodeMatch?.[2]) {
    return prefixedCodeMatch[2].trim();
  }

  return normalized;
}

function toTemporalMonthKey(rawValue: unknown): string | null {
  if (rawValue === null || rawValue === undefined) return null;

  const toKey = (date: Date): string | null => {
    const time = date.getTime();
    if (!Number.isFinite(time)) return null;
    const year = date.getUTCFullYear();
    const month = date.getUTCMonth() + 1;
    if (year < 1900 || year > 2100) return null;
    return `${year}-${String(month).padStart(2, '0')}`;
  };

  const toKeyFromEpochLike = (numericValue: number): string | null => {
    if (!Number.isFinite(numericValue)) return null;

    const abs = Math.abs(numericValue);
    let millis: number | null = null;

    // DuckDB suele exponer timestamps como BIGINT en micro/nano segundos.
    if (abs >= 1e18 && abs < 1e21) {
      // nanoseconds
      millis = numericValue / 1e6;
    } else if (abs >= 1e14 && abs < 1e18) {
      // microseconds
      millis = numericValue / 1e3;
    } else if (abs >= 1e11 && abs < 1e14) {
      // milliseconds
      millis = numericValue;
    } else if (abs >= 1e9 && abs < 1e11) {
      // seconds
      millis = numericValue * 1000;
    } else if (abs >= 1e4 && abs < 1e7) {
      // days since 1970-01-01 (date32-like)
      millis = numericValue * 86_400_000;
    } else {
      return null;
    }

    return toKey(new Date(millis));
  };

  if (rawValue instanceof Date) {
    return toKey(rawValue);
  }

  if (typeof rawValue === 'number') {
    const epochKey = toKeyFromEpochLike(rawValue);
    if (epochKey) return epochKey;
  }

  if (typeof rawValue === 'bigint') {
    const asNumber = Number(rawValue);
    if (Number.isFinite(asNumber)) {
      const epochKey = toKeyFromEpochLike(asNumber);
      if (epochKey) return epochKey;
    }
  }

  const text = String(rawValue).trim();
  if (!text) return null;

  // Números puros: soportar fechas yyyymmdd y epochs de alta magnitud.
  if (/^\d+$/.test(text)) {
    if (/^(19|20)\d{6}$/.test(text)) {
      const year = Number(text.slice(0, 4));
      const month = Number(text.slice(4, 6));
      const day = Number(text.slice(6, 8));
      if (month >= 1 && month <= 12 && day >= 1 && day <= 31) {
        const utcDate = new Date(Date.UTC(year, month - 1, day));
        const key = toKey(utcDate);
        if (key) return key;
      }
    }

    if (text.length >= 5 && text.length <= 20) {
      const asNumber = Number(text);
      if (Number.isFinite(asNumber)) {
        const epochKey = toKeyFromEpochLike(asNumber);
        if (epochKey) return epochKey;
      }
    }

    return null;
  }

  // ISO / parseable estándar.
  const parsed = Date.parse(text);
  if (!Number.isNaN(parsed)) {
    const key = toKey(new Date(parsed));
    if (key) return key;
  }

  // Mes abreviado + año (es/en), ej: Mar-2021, Abr 2021, Sep_2021
  const normalized = text
    .normalize('NFKD')
    .replace(/[^\w\s-]/g, '')
    .toLowerCase()
    .replace(/_/g, '-')
    .replace(/\s+/g, '-')
    .trim();

  const monthByToken: Record<string, number> = {
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

  let match = normalized.match(/^([a-z]+)-(\d{4})$/);
  if (match) {
    const month = monthByToken[match[1]];
    const year = Number(match[2]);
    if (month && Number.isFinite(year)) {
      return `${year}-${String(month).padStart(2, '0')}`;
    }
  }

  match = normalized.match(/^(\d{4})-([a-z]+)$/);
  if (match) {
    const year = Number(match[1]);
    const month = monthByToken[match[2]];
    if (month && Number.isFinite(year)) {
      return `${year}-${String(month).padStart(2, '0')}`;
    }
  }

  match = normalized.match(/^(\d{4})-(\d{1,2})$/);
  if (match) {
    const year = Number(match[1]);
    const month = Number(match[2]);
    if (Number.isFinite(year) && month >= 1 && month <= 12) {
      return `${year}-${String(month).padStart(2, '0')}`;
    }
  }

  return null;
}

function toTemporalWeekKey(rawValue: unknown): string | null {
  if (rawValue === null || rawValue === undefined) return null;

  const toWeekKey = (date: Date): string | null => {
    const time = date.getTime();
    if (!Number.isFinite(time)) return null;

    const utcDate = new Date(Date.UTC(
      date.getUTCFullYear(),
      date.getUTCMonth(),
      date.getUTCDate()
    ));

    const dayNumber = utcDate.getUTCDay() || 7;
    utcDate.setUTCDate(utcDate.getUTCDate() + 4 - dayNumber);

    const weekYear = utcDate.getUTCFullYear();
    if (weekYear < 1900 || weekYear > 2100) return null;

    const yearStart = new Date(Date.UTC(weekYear, 0, 1));
    const weekNumber = Math.ceil((((utcDate.getTime() - yearStart.getTime()) / 86_400_000) + 1) / 7);
    if (!Number.isFinite(weekNumber) || weekNumber < 1 || weekNumber > 53) return null;

    return `${weekYear}-W${String(weekNumber).padStart(2, '0')}`;
  };

  const toWeekKeyFromEpochLike = (numericValue: number): string | null => {
    if (!Number.isFinite(numericValue)) return null;

    const abs = Math.abs(numericValue);
    let millis: number | null = null;

    if (abs >= 1e18 && abs < 1e21) {
      millis = numericValue / 1e6; // nanoseconds
    } else if (abs >= 1e14 && abs < 1e18) {
      millis = numericValue / 1e3; // microseconds
    } else if (abs >= 1e11 && abs < 1e14) {
      millis = numericValue; // milliseconds
    } else if (abs >= 1e9 && abs < 1e11) {
      millis = numericValue * 1000; // seconds
    } else if (abs >= 1e4 && abs < 1e7) {
      millis = numericValue * 86_400_000; // date32-like (days)
    } else {
      return null;
    }

    return toWeekKey(new Date(millis));
  };

  if (rawValue instanceof Date) {
    return toWeekKey(rawValue);
  }

  if (typeof rawValue === 'number') {
    const fromEpoch = toWeekKeyFromEpochLike(rawValue);
    if (fromEpoch) return fromEpoch;
  }

  if (typeof rawValue === 'bigint') {
    const asNumber = Number(rawValue);
    if (Number.isFinite(asNumber)) {
      const fromEpoch = toWeekKeyFromEpochLike(asNumber);
      if (fromEpoch) return fromEpoch;
    }
  }

  const text = String(rawValue).trim();
  if (!text) return null;

  const normalized = text
    .replace(/_/g, '-')
    .replace(/\s+/g, '-')
    .toUpperCase();

  const weekMatch = normalized.match(/^(\d{4})-?W(\d{1,2})$/);
  if (weekMatch) {
    const year = Number(weekMatch[1]);
    const week = Number(weekMatch[2]);
    if (Number.isFinite(year) && Number.isFinite(week) && week >= 1 && week <= 53) {
      return `${year}-W${String(week).padStart(2, '0')}`;
    }
  }

  const parsed = Date.parse(text);
  if (!Number.isNaN(parsed)) {
    const weekKey = toWeekKey(new Date(parsed));
    if (weekKey) return weekKey;
  }

  return null;
}

function domainValuesMatch(groupedKey: string, displayValue: string): boolean {
  if (groupedKey === displayValue) return true;

  const normalizedGrouped = normalizeVisualDomainValue(groupedKey);
  const normalizedDisplay = normalizeVisualDomainValue(displayValue);
  if (normalizedGrouped === normalizedDisplay) return true;

  const groupedMonthKey = toTemporalMonthKey(groupedKey);
  const displayMonthKey = toTemporalMonthKey(displayValue);
  if (groupedMonthKey && displayMonthKey && groupedMonthKey === displayMonthKey) return true;

  return false;
}

function buildStableCartesianDatum(
  templateDatum: unknown,
  categoryName: string,
  value: number
): { name: string; value: number } | Record<string, unknown> {
  const stableId = `datum:${normalizeVisualDomainValue(categoryName)}`;
  if (templateDatum && typeof templateDatum === 'object' && !Array.isArray(templateDatum)) {
    return {
      ...(templateDatum as Record<string, unknown>),
      id: String((templateDatum as Record<string, unknown>).id ?? stableId),
      name: categoryName,
      value,
    };
  }

  return {
    id: stableId,
    name: categoryName,
    value,
  };
}

function buildStableHierarchyDatum(
  templateDatum: unknown,
  itemName: string,
  value: number,
  fallbackColor?: string
): Record<string, unknown> {
  const stableId = `node:${normalizeVisualDomainValue(itemName)}`;
  const normalizedName = normalizeVisualDomainValue(itemName);
  if (templateDatum && typeof templateDatum === 'object' && !Array.isArray(templateDatum)) {
    const templateRecord = templateDatum as Record<string, unknown>;
    const templateItemStyle =
      templateRecord.itemStyle && typeof templateRecord.itemStyle === 'object'
        ? (templateRecord.itemStyle as Record<string, unknown>)
        : null;
    const explicitColor = typeof templateItemStyle?.color === 'string'
      ? templateItemStyle.color
      : null;
    return {
      ...templateRecord,
      id: String(templateRecord.id ?? stableId),
      name: itemName,
      raw_name: itemName,
      normalized_name: normalizedName,
      value,
      ...(fallbackColor && !explicitColor
        ? {
            itemStyle: {
              ...(templateItemStyle || {}),
              color: fallbackColor,
            },
          }
        : {}),
    };
  }

  const nextItem: Record<string, unknown> = {
    id: stableId,
    name: itemName,
    raw_name: itemName,
    normalized_name: normalizedName,
    value,
  };
  if (fallbackColor) {
    nextItem.itemStyle = { color: fallbackColor };
  }
  return nextItem;
}

function compactHierarchyItems<T extends Record<string, unknown>>(items: T[]): T[] {
  return items.filter((item) => Math.abs(toNumber((item as any)?.value)) > 0);
}

function syncVisualSourcePayloadRows(
  option: Record<string, unknown>,
  rows: Array<{ name: string; value: number }>
): void {
  if (!option || typeof option !== 'object') return;
  if (!Array.isArray(rows)) return;

  const currentPayload =
    option.visual_source_payload && typeof option.visual_source_payload === 'object'
      ? (option.visual_source_payload as Record<string, unknown>)
      : {};

  option.visual_source_payload = {
    ...currentPayload,
    rows: rows.map((row) => ({
      name: row.name,
      value: Number.isFinite(row.value) ? row.value : 0,
    })),
  };
}

function getMappedValues(
  grouped: Map<string, number[]>,
  displayValue: string
): number[] {
  const direct = grouped.get(displayValue);
  if (direct) return direct;

  const normalizedDisplayValue = normalizeVisualDomainValue(displayValue);
  if (!normalizedDisplayValue) return [];

  const normalizedDirect = grouped.get(normalizedDisplayValue);
  if (normalizedDirect) return normalizedDirect;

  const temporalMatches: number[] = [];
  for (const [groupedKey, values] of grouped.entries()) {
    if (domainValuesMatch(groupedKey, displayValue)) {
      temporalMatches.push(...values);
    }
  }

  if (temporalMatches.length > 0) {
    return temporalMatches;
  }

  for (const [groupedKey, values] of grouped.entries()) {
    if (normalizeVisualDomainValue(groupedKey) === normalizedDisplayValue) {
      return values;
    }
  }

  return [];
}

function getMappedNestedValues(
  grouped: Map<string, Map<string, number[]>>,
  primaryDisplayValue: string,
  secondaryDisplayValue: string
): number[] {
  const candidatePrimaryKeys = [
    primaryDisplayValue,
    normalizeVisualDomainValue(primaryDisplayValue),
  ].filter(Boolean);

  const candidateSecondaryKeys = [
    secondaryDisplayValue,
    normalizeVisualDomainValue(secondaryDisplayValue),
  ].filter(Boolean);

  for (const primaryKey of candidatePrimaryKeys) {
    const secondaryMap = grouped.get(primaryKey);
    if (!secondaryMap) continue;

    for (const secondaryKey of candidateSecondaryKeys) {
      const values = secondaryMap.get(secondaryKey);
      if (values) return values;
    }

    for (const [groupedSecondaryKey, values] of secondaryMap.entries()) {
      if (normalizeVisualDomainValue(groupedSecondaryKey) === normalizeVisualDomainValue(secondaryDisplayValue)) {
        return values;
      }
    }
  }

  for (const [groupedPrimaryKey, secondaryMap] of grouped.entries()) {
    if (!domainValuesMatch(groupedPrimaryKey, primaryDisplayValue)) {
      continue;
    }

    for (const secondaryKey of candidateSecondaryKeys) {
      const values = secondaryMap.get(secondaryKey);
      if (values) return values;
    }

    for (const [groupedSecondaryKey, values] of secondaryMap.entries()) {
      if (normalizeVisualDomainValue(groupedSecondaryKey) === normalizeVisualDomainValue(secondaryDisplayValue)) {
        return values;
      }
    }
  }

  return [];
}

function buildHierarchyColorMap(baseOption: EChartsOption, primarySeries: any): Map<string, string> {
  const colorMap = new Map<string, string>();
  const paletteFromOption = Array.isArray((baseOption as any)?.color) ? (baseOption as any).color : [];
  const paletteFromSeries = Array.isArray(primarySeries?.color) ? primarySeries.color : [];
  const palette = [...paletteFromSeries, ...paletteFromOption, ...HIERARCHY_FALLBACK_COLORS]
    .filter((color) => typeof color === 'string') as string[];

  if (!Array.isArray(primarySeries?.data)) {
    return colorMap;
  }

  primarySeries.data.forEach((item: any, index: number) => {
    const itemName = String(item?.name ?? '').trim();
    if (!itemName) return;

    const explicitColor = typeof item?.itemStyle?.color === 'string' ? item.itemStyle.color : null;
    const fallbackColor = explicitColor || (palette.length > 0 ? palette[index % palette.length] : null);
    if (!fallbackColor) return;

    colorMap.set(itemName, fallbackColor);
    const normalized = normalizeVisualDomainValue(itemName);
    if (normalized) {
      colorMap.set(normalized, fallbackColor);
    }
  });

  return colorMap;
}

export function buildReactiveChartOption(
  baseOption: EChartsOption,
  filteredRows: Record<string, unknown>[],
  contract: WidgetQueryContract
): EChartsOption {
  const cacheKey = buildReactiveChartOptionCacheKey(contract);
  const cachedOption = getCachedReactiveChartOption(baseOption, filteredRows, cacheKey);
  if (cachedOption) {
    return cachedOption;
  }

  const finalizeReactiveChartOption = (nextOption: EChartsOption): EChartsOption =>
    rememberReactiveChartOption(baseOption, filteredRows, cacheKey, nextOption);

  const temporalTrend = isTemporalTrendChart(baseOption);
  const requestedMetric = getMetricColumn(contract);
  const frozenTemporalCategories = getFrozenCategories(baseOption);
  const temporalDetectedDimension = temporalTrend
    ? resolveTemporalDimensionColumn(
        filteredRows,
        frozenTemporalCategories,
        new Set([contract.group_by?.[0]].filter(Boolean) as string[])
      )
    : null;
  const contractTemporalDimension = temporalTrend && isUsableDimensionColumn(filteredRows, contract.dimension)
    ? contract.dimension
    : null;
  const contractTemporalCoverage = contractTemporalDimension
    ? getTemporalRowCoverageScore(filteredRows, contractTemporalDimension, frozenTemporalCategories)
    : 0;
  const detectedTemporalCoverage = temporalDetectedDimension
    ? getTemporalRowCoverageScore(filteredRows, temporalDetectedDimension, frozenTemporalCategories)
    : 0;
  const provisionalPrimaryDimension = temporalTrend
    ? (
        contractTemporalDimension && contractTemporalCoverage >= Math.max(0.25, detectedTemporalCoverage)
          ? contractTemporalDimension
          : temporalDetectedDimension || contractTemporalDimension || contract.dimension || null
      )
    : contract.dimension || null;

  let metricColumn = resolveMetricColumn(
    filteredRows,
    contract,
    provisionalPrimaryDimension,
    contract.group_by?.[0]
  );

  if (temporalTrend) {
    metricColumn = resolveTemporalTrendMetricColumn(
      filteredRows,
      contract,
      provisionalPrimaryDimension,
      contract.group_by?.[0],
      metricColumn
    );
  }

  const primaryDimension = resolvePrimaryDimension(
    filteredRows,
    provisionalPrimaryDimension,
    baseOption,
    metricColumn,
    contract.group_by?.[0]
  );
  const secondaryDimension = resolveSecondaryDimension(filteredRows, contract.group_by?.[0], primaryDimension);

  // Guardrail de contrato autoritativo para tendencia temporal:
  // si el backend especifica una métrica válida en las filas filtradas,
  // se fuerza para evitar desvíos por heurística local.
  if (
    temporalTrend &&
    requestedMetric &&
    requestedMetric !== primaryDimension &&
    requestedMetric !== secondaryDimension &&
    hasColumn(filteredRows, requestedMetric)
  ) {
    if (metricColumn !== requestedMetric) {
      console.warn("🕵️ [REACTIVE METRIC] override_to_contract_metric", {
        requestedMetric,
        selectedBeforeOverride: metricColumn,
        primaryDimension,
        secondaryDimension,
      });
    }
    metricColumn = requestedMetric;
  }

  const aggregation = contract.aggregation || 'sum';
  const limit = contract.limit ?? 10;

  if (!metricColumn || !primaryDimension) {
    if (temporalTrend) {
      console.warn("🕵️ [REACTIVE METRIC] aborted_missing_columns", {
        requestedMetric,
        selectedMetric: metricColumn,
        primaryDimension,
        secondaryDimension,
        rows: filteredRows.length,
      });
    }
    return finalizeReactiveChartOption(baseOption);
  }

  if (temporalTrend) {
    const excluded = new Set([primaryDimension, secondaryDimension].filter(Boolean) as string[]);
    const numericCandidates = filteredRows.length > 0
      ? Object.keys(filteredRows[0] || {}).filter((column) => !excluded.has(column) && isUsableMetricColumn(filteredRows, column))
      : [];
    const preferredCandidates = numericCandidates.filter((column) => isPreferredMetricColumn(column));

    console.log("🕵️ [REACTIVE METRIC] trend_resolution", {
      requestedMetric,
      selectedMetric: metricColumn,
      primaryDimension,
      secondaryDimension,
      provisionalPrimaryDimension,
      numericCandidates,
      preferredCandidates,
      aggregation,
      rows: filteredRows.length,
      frozenCategories: frozenTemporalCategories.slice(0, 12),
      contractTemporalDimension,
      contractTemporalCoverage,
      temporalDetectedDimension,
      detectedTemporalCoverage,
    });
  }

  const nextOption: any = {
    ...baseOption,
    xAxis: Array.isArray(baseOption.xAxis) ? [...baseOption.xAxis] : baseOption.xAxis ? { ...baseOption.xAxis } : undefined,
    yAxis: Array.isArray(baseOption.yAxis) ? [...baseOption.yAxis] : baseOption.yAxis ? { ...baseOption.yAxis } : undefined,
    series: Array.isArray(baseOption.series) ? [...baseOption.series] : []
  };
  nextOption.query_contract = {
    ...(((baseOption as any)?.query_contract && typeof (baseOption as any).query_contract === 'object')
      ? (baseOption as any).query_contract
      : {}),
    ...contract,
    aggregation,
    metric: metricColumn,
    value_column: metricColumn,
    dimension: primaryDimension,
    group_by: secondaryDimension ? [secondaryDimension] : undefined,
  };

  if (secondaryDimension) {
    const grouped = new Map<string, Map<string, number[]>>();
    filteredRows.forEach((row) => {
      const primaryValue = String(row[primaryDimension] ?? 'N/A');
      const secondaryValue = String(row[secondaryDimension] ?? 'N/A');
      const metricValue = toNumber(row[metricColumn]);

      if (!grouped.has(primaryValue)) grouped.set(primaryValue, new Map());
      const secondaryMap = grouped.get(primaryValue)!;
      if (!secondaryMap.has(secondaryValue)) secondaryMap.set(secondaryValue, []);
      secondaryMap.get(secondaryValue)!.push(metricValue);
    });

    const primaryTotals = Array.from(grouped.entries()).map(([primary, secondaryMap]) => ({
      primary,
      total: Array.from(secondaryMap.values()).reduce((sum, values) => sum + aggregate(values, aggregation), 0)
    }));

    const frozenPrimary = getFrozenCategories(baseOption);
    const orderedPrimary = frozenPrimary.length > 0
      ? frozenPrimary
      : primaryTotals
          .sort((a, b) => b.total - a.total)
          .slice(0, limit)
          .map((item) => item.primary);

    const orderedSecondary = (() => {
      const fromBase = getFrozenSeriesNames(baseOption);
      if (fromBase.length > 0) return fromBase;
      const discovered = new Set<string>();
      orderedPrimary.forEach((primary) => {
        const secondaryMap = grouped.get(primary);
        if (!secondaryMap) return;
        secondaryMap.forEach((_, secondary) => discovered.add(secondary));
      });
      return Array.from(discovered);
    })();

    const horizontal = !Array.isArray(nextOption.xAxis) && nextOption.yAxis && !Array.isArray(nextOption.yAxis)
      ? nextOption.yAxis?.type === 'category'
      : Array.isArray(nextOption.yAxis)
        ? nextOption.yAxis[0]?.type === 'category'
        : false;

    if (horizontal) {
      const yAxis = Array.isArray(nextOption.yAxis) ? { ...nextOption.yAxis[0], data: orderedPrimary } : { ...(nextOption.yAxis || {}), data: orderedPrimary };
      nextOption.yAxis = Array.isArray(nextOption.yAxis) ? [yAxis] : yAxis;
    } else {
      const xAxis = Array.isArray(nextOption.xAxis) ? { ...nextOption.xAxis[0], data: orderedPrimary } : { ...(nextOption.xAxis || {}), data: orderedPrimary };
      nextOption.xAxis = Array.isArray(nextOption.xAxis) ? [xAxis] : xAxis;
    }

    nextOption.series = orderedSecondary.map((secondary, index) => {
      const template = getSeriesTemplate(baseOption, index, 'bar');
      const data = orderedPrimary.map((primary, primaryIndex) => {
        const values = getMappedNestedValues(grouped, primary, secondary);
        return buildStableCartesianDatum(
          Array.isArray(template.data) ? template.data[primaryIndex] : undefined,
          primary,
          aggregate(values, aggregation)
        );
      });

      return {
        ...template,
        name: secondary,
        type: template.type || 'bar',
        stack: contract.barmode === 'stacked' ? 'total' : undefined,
        data,
      };
    });

    syncVisualSourcePayloadRows(
      nextOption,
      orderedPrimary.map((primary) => {
        const secondaryMap = grouped.get(primary);
        const total = secondaryMap
          ? Array.from(secondaryMap.values()).reduce((sum, values) => sum + aggregate(values, aggregation), 0)
          : 0;
        return { name: primary, value: total };
      })
    );

    return finalizeReactiveChartOption(nextOption);
  }

  const grouped = new Map<string, number[]>();
  filteredRows.forEach((row) => {
    const dimensionValue = String(row[primaryDimension] ?? 'N/A');
    const metricValue = toNumber(row[metricColumn]);
    if (!grouped.has(dimensionValue)) grouped.set(dimensionValue, []);
    grouped.get(dimensionValue)!.push(metricValue);
  });

  const frozenCategories = getFrozenCategories(baseOption);
  const ordered = (frozenCategories.length > 0 ? frozenCategories : Array.from(grouped.keys()).slice(0, limit))
    .map((dimensionValue) => ({
      dimensionValue,
      value: aggregate(getMappedValues(grouped, dimensionValue), aggregation),
    }));

  const categories = ordered.map((item) => item.dimensionValue);
  const primarySeries = getSeriesTemplate(baseOption, 0, 'bar');
  let values = ordered.map((item, index) =>
    buildStableCartesianDatum(
      Array.isArray(primarySeries.data) ? primarySeries.data[index] : undefined,
      item.dimensionValue,
      item.value
    )
  );

  // Fallback robusto para line charts temporales:
  // si el eje es temporal y el resultado quedó plano en cero, re-agregar por columna temporal detectada.
  if (
    primarySeries.type === 'line' &&
    categoriesLookTemporal(categories) &&
    filteredRows.length > 0 &&
    values.every((datum: any) => toNumber(datum?.value) === 0)
  ) {
    const temporalColumn = resolveTemporalDimensionColumn(
      filteredRows,
      categories,
      new Set([metricColumn, primaryDimension, secondaryDimension].filter(Boolean) as string[])
    );

    if (temporalColumn) {
      const byMonth = new Map<string, number[]>();
      const byWeek = new Map<string, number[]>();
      filteredRows.forEach((row) => {
        const monthKey = toTemporalMonthKey(row[temporalColumn]);
        const metricValue = toNumber(row[metricColumn]);
        if (monthKey) {
          if (!byMonth.has(monthKey)) byMonth.set(monthKey, []);
          byMonth.get(monthKey)!.push(metricValue);
        }

        const weekKey = toTemporalWeekKey(row[temporalColumn]);
        if (weekKey) {
          if (!byWeek.has(weekKey)) byWeek.set(weekKey, []);
          byWeek.get(weekKey)!.push(metricValue);
        }
      });

      values = categories.map((category, index) => {
        const weekKey = toTemporalWeekKey(category);
        const monthKey = toTemporalMonthKey(category);
        const temporalValues = weekKey
          ? (byWeek.get(weekKey) || [])
          : monthKey
            ? (byMonth.get(monthKey) || [])
            : [];
        return buildStableCartesianDatum(
          Array.isArray(primarySeries.data) ? primarySeries.data[index] : undefined,
          category,
          aggregate(temporalValues, aggregation)
        );
      });
    }
  }

  if (primarySeries.type === 'pie' || primarySeries.type === 'funnel') {
    const templateByName = new Map<string, unknown>();
    if (Array.isArray(primarySeries.data)) {
      primarySeries.data.forEach((item: any) => {
        if (item && typeof item === 'object' && item.name) {
          templateByName.set(String(item.name), item);
        }
      });
    }

    const frozenItems = Array.isArray(primarySeries.data)
      ? primarySeries.data
          .map((item: any) => String(item?.name ?? ''))
          .filter(Boolean)
      : [];
    const hierarchyColorMap = buildHierarchyColorMap(baseOption, primarySeries);
    const orderedItems = (frozenItems.length > 0 ? frozenItems : categories).map((dimensionValue: string) =>
      buildStableHierarchyDatum(
        templateByName.get(dimensionValue),
        dimensionValue,
        aggregate(getMappedValues(grouped, dimensionValue), aggregation),
        hierarchyColorMap.get(dimensionValue) || hierarchyColorMap.get(normalizeVisualDomainValue(dimensionValue))
      )
    );
    const visibleItems = compactHierarchyItems(orderedItems);

    nextOption.series = [{
      ...primarySeries,
      data: visibleItems
    }];
    syncVisualSourcePayloadRows(
      nextOption,
      visibleItems.map((item: any) => ({
        name: String(item?.name ?? ''),
        value: toNumber(item?.value),
      }))
    );
    return finalizeReactiveChartOption(nextOption);
  }

  if (primarySeries.type === 'treemap') {
    const templateByName = new Map<string, unknown>();
    if (Array.isArray(primarySeries.data)) {
      primarySeries.data.forEach((item: any) => {
        if (item && typeof item === 'object' && item.name) {
          templateByName.set(String(item.name), item);
        }
      });
    }

    const frozenItems = Array.isArray(primarySeries.data)
      ? primarySeries.data
          .map((item: any) => String(item?.name ?? ''))
          .filter(Boolean)
      : [];
    const hierarchyColorMap = buildHierarchyColorMap(baseOption, primarySeries);
    const orderedItems = (frozenItems.length > 0 ? frozenItems : categories).map((dimensionValue: string) =>
      buildStableHierarchyDatum(
        templateByName.get(dimensionValue),
        dimensionValue,
        aggregate(getMappedValues(grouped, dimensionValue), aggregation),
        hierarchyColorMap.get(dimensionValue) || hierarchyColorMap.get(normalizeVisualDomainValue(dimensionValue))
      )
    );
    const visibleItems = compactHierarchyItems(orderedItems);

    nextOption.series = [{
      ...primarySeries,
      data: visibleItems
    }];
    syncVisualSourcePayloadRows(
      nextOption,
      visibleItems.map((item: any) => ({
        name: String(item?.name ?? ''),
        value: toNumber(item?.value),
      }))
    );
    if ((baseOption as any).cross_filter_context) {
      (nextOption as any).cross_filter_context = (baseOption as any).cross_filter_context;
    }
    return finalizeReactiveChartOption(nextOption);
  }

  const horizontal = !Array.isArray(nextOption.xAxis) && nextOption.yAxis && !Array.isArray(nextOption.yAxis)
    ? nextOption.yAxis?.type === 'category'
    : Array.isArray(nextOption.yAxis)
      ? nextOption.yAxis[0]?.type === 'category'
      : false;

  if (horizontal) {
    const yAxis = Array.isArray(nextOption.yAxis) ? { ...nextOption.yAxis[0], data: categories } : { ...(nextOption.yAxis || {}), data: categories };
    nextOption.yAxis = Array.isArray(nextOption.yAxis) ? [yAxis] : yAxis;
  } else {
    const xAxis = Array.isArray(nextOption.xAxis) ? { ...nextOption.xAxis[0], data: categories } : { ...(nextOption.xAxis || {}), data: categories };
    nextOption.xAxis = Array.isArray(nextOption.xAxis) ? [xAxis] : xAxis;
  }

  nextOption.series = [{
    ...primarySeries,
    data: values
  }];

  syncVisualSourcePayloadRows(
    nextOption,
    categories.map((category, index) => ({
      name: category,
      value: toNumber((values[index] as any)?.value),
    }))
  );

  if ((baseOption as any).cross_filter_context) {
    (nextOption as any).cross_filter_context = (baseOption as any).cross_filter_context;
  }

  return finalizeReactiveChartOption(nextOption);
}
