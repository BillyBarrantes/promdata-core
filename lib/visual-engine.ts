import type { EChartsOption } from "echarts";

export type VisualId =
  | "bar_chart"
  | "stacked_bar_chart"
  | "line_chart"
  | "area_chart"
  | "pie_chart"
  | "treemap"
  | "gauge_chart"
  | "scatter_plot"
  | "bubble_chart"
  | "heatmap_chart"
  | "waterfall_chart"
  | "funnel_chart"
  | "boxplot_chart"
  | "dual_axis_chart"
  | "combo_chart"
  | "smart_table"
  | "histogram_chart"
  | "gantt_chart"
  | "pareto_chart";

export type VisualCatalogEntry = {
  id: VisualId;
  label: string;
  enabled: boolean;
  recommended?: boolean;
  applied?: boolean;
  reason?: string | null;
};

export type VisualGovernancePayload = {
  requested_visual?: VisualId;
  recommended_visual?: VisualId;
  applied_visual?: VisualId;
  requested_label?: string;
  recommended_label?: string;
  applied_label?: string;
  recommendation_reason?: string;
  blocked_reason?: string | null;
  advisory_reason?: string | null;
  override_applied?: boolean;
  allowed_replacements?: VisualId[];
  catalog?: VisualCatalogEntry[];
};

export type VisualSourcePayload = {
  title?: string;
  chart_type?: VisualId | string;
  requested_chart_type?: VisualId | string;
  rows?: unknown[];
  x_label?: string | null;
  y_label?: string | null;
  barmode?: string | null;
  metric_unit?: string | null;
};

const COLORS = ["#2563eb", "#10b981", "#f59e0b", "#8b5cf6", "#f43f5e", "#0ea5e9"];

const TRANSFORMABLE_VISUALS = new Set<VisualId>([
  "bar_chart",
  "stacked_bar_chart",
  "line_chart",
  "area_chart",
  "pie_chart",
  "treemap",
  "gauge_chart",
  "scatter_plot",
  "bubble_chart",
  "heatmap_chart",
  "waterfall_chart",
  "funnel_chart",
  "boxplot_chart",
  "dual_axis_chart",
  "combo_chart",
  "histogram_chart",
  "gantt_chart",
  "pareto_chart",
]);

const baseOption = (): EChartsOption => ({
  animation: true,
  grid: { top: 48, left: 36, right: 24, bottom: 32, containLabel: true },
  tooltip: { trigger: "item" },
  legend: { bottom: 0 },
});

const toFiniteNumber = (value: unknown): number | null => {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const parsed = Number(value.replace(/%/g, "").trim());
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
};

const getRecordEntries = (row: Record<string, unknown>): [string, unknown][] =>
  Object.entries(row).filter(([key]) => key !== "extra_info");

const extractNamedValues = (rows: unknown[]): Array<{ name: string; value: number; extraInfo?: Record<string, unknown> }> => {
  const normalized: Array<{ name: string; value: number; extraInfo?: Record<string, unknown> }> = [];

  rows.forEach((row, index) => {
    if (!row) return;

    if (Array.isArray(row) && row.length >= 2) {
      const numeric = toFiniteNumber(row[1]);
      if (numeric === null) return;
      normalized.push({
        name: String(row[0] ?? `Item ${index + 1}`),
        value: numeric,
      });
      return;
    }

    if (typeof row === "object") {
      const record = row as Record<string, unknown>;
      const extraInfo = typeof record.extra_info === "object" && record.extra_info ? record.extra_info as Record<string, unknown> : undefined;

      const directName = typeof record.name === "string" ? record.name : null;
      const directValue = toFiniteNumber(record.value);
      if (directName && directValue !== null) {
        normalized.push({ name: directName, value: directValue, extraInfo });
        return;
      }

      const entries = getRecordEntries(record);
      const firstText = entries.find(([, value]) => typeof value === "string");
      const firstNumeric = entries.find(([, value]) => toFiniteNumber(value) !== null);
      if (firstText && firstNumeric) {
        normalized.push({
          name: String(firstText[1]),
          value: toFiniteNumber(firstNumeric[1]) as number,
          extraInfo,
        });
      }
    }
  });

  return normalized;
};

const extractScatterValues = (rows: unknown[]): number[][] => {
  const points: number[][] = [];

  rows.forEach((row) => {
    if (Array.isArray(row) && row.length >= 2) {
      const x = toFiniteNumber(row[0]);
      const y = toFiniteNumber(row[1]);
      if (x !== null && y !== null) points.push([x, y]);
      return;
    }

    if (typeof row === "object" && row) {
      const record = row as Record<string, unknown>;
      const directX = toFiniteNumber(record.x_value);
      const directY = toFiniteNumber(record.y_value);
      if (directX !== null && directY !== null) {
        points.push([directX, directY]);
        return;
      }

      const numericValues = Object.values(record)
        .map((value) => toFiniteNumber(value))
        .filter((value): value is number => value !== null);
      if (numericValues.length >= 2) points.push([numericValues[0], numericValues[1]]);
    }
  });

  return points;
};

const extractScatterSeries = (
  rows: unknown[],
): Array<{ name: string; data: Array<{ name: string; value: [number, number] }> }> => {
  const grouped = new Map<string, Array<{ name: string; value: [number, number] }>>();

  rows.forEach((row, index) => {
    if (typeof row !== "object" || !row || Array.isArray(row)) return;
    const record = row as Record<string, unknown>;
    const x = toFiniteNumber(record.x_value);
    const y = toFiniteNumber(record.y_value);
    if (x === null || y === null) return;

    const seriesNameRaw = record.series ?? record.category ?? record.name ?? "Serie";
    const pointNameRaw = record.raw_name ?? record.name ?? `Punto ${index + 1}`;
    const seriesName = String(seriesNameRaw);
    const pointName = String(pointNameRaw);

    const current = grouped.get(seriesName) || [];
    current.push({
      name: pointName,
      value: [x, y],
    });
    grouped.set(seriesName, current);
  });

  return Array.from(grouped.entries()).map(([name, data]) => ({ name, data }));
};

const extractBubbleValues = (rows: unknown[]): Array<{ name: string; value: [number, number, number] }> => {
  const points: Array<{ name: string; value: [number, number, number] }> = [];

  rows.forEach((row, index) => {
    if (Array.isArray(row) && row.length >= 3) {
      const numeric = row
        .map((value) => toFiniteNumber(value))
        .filter((value): value is number => value !== null);
      if (numeric.length >= 3) {
        points.push({
          name: String(row[3] ?? `Punto ${index + 1}`),
          value: [numeric[0], numeric[1], numeric[2]],
        });
      }
      return;
    }

    if (typeof row !== "object" || !row) return;
    const record = row as Record<string, unknown>;
    const extraInfo = typeof record.extra_info === "object" && record.extra_info ? record.extra_info as Record<string, unknown> : {};
    const numericValues = Object.entries(record)
      .filter(([key]) => key !== "extra_info")
      .map(([, value]) => toFiniteNumber(value))
      .filter((value): value is number => value !== null);

    if (numericValues.length >= 3) {
      points.push({
        name: String(record.name ?? record.label ?? `Punto ${index + 1}`),
        value: [numericValues[0], numericValues[1], numericValues[2]],
      });
      return;
    }

    const bubbleSize = toFiniteNumber(extraInfo.bubble_size ?? extraInfo.size);
    if (numericValues.length >= 2 && bubbleSize !== null) {
      points.push({
        name: String(record.name ?? record.label ?? `Punto ${index + 1}`),
        value: [numericValues[0], numericValues[1], bubbleSize],
      });
    }
  });

  return points;
};

const extractSeriesMatrix = (rows: unknown[]): { categories: string[]; seriesNames: string[]; data: number[][] } => {
  const categories: string[] = [];
  const seriesNames: string[] = [];
  const matrix: number[][] = [];

  rows.forEach((row, index) => {
    if (typeof row !== "object" || !row || Array.isArray(row)) return;
    const record = row as Record<string, unknown>;
    const entries = Object.entries(record).filter(([key]) => key !== "extra_info");
    const categoryEntry = entries.find(([, value]) => typeof value === "string");
    const numericEntries = entries.filter(([, value]) => toFiniteNumber(value) !== null);
    if (!categoryEntry || numericEntries.length < 2) return;

    const category = String(categoryEntry[1] ?? `Grupo ${index + 1}`);
    const currentValues: number[] = [];

    numericEntries.forEach(([key, value], seriesIndex) => {
      if (!seriesNames.includes(key)) seriesNames.push(key);
      currentValues[seriesIndex] = toFiniteNumber(value) ?? 0;
    });

    categories.push(category);
    matrix.push(currentValues);
  });

  const normalizedData = seriesNames.map((_, seriesIndex) =>
    matrix.map((values) => values[seriesIndex] ?? 0),
  );

  return { categories, seriesNames, data: normalizedData };
};

const extractHistogram = (rows: unknown[]): { labels: string[]; values: number[] } => {
  const numericValues: number[] = [];

  rows.forEach((row) => {
    if (typeof row === "number" && Number.isFinite(row)) {
      numericValues.push(row);
      return;
    }
    if (Array.isArray(row)) {
      row.forEach((value) => {
        const parsed = toFiniteNumber(value);
        if (parsed !== null) numericValues.push(parsed);
      });
      return;
    }
    if (typeof row !== "object" || !row) return;
    const record = row as Record<string, unknown>;
    const values = Object.entries(record)
      .filter(([key]) => key !== "extra_info")
      .map(([, value]) => value);
    if (values.some((value) => typeof value === "string")) return;
    values.forEach((value) => {
      const parsed = toFiniteNumber(value);
      if (parsed !== null) numericValues.push(parsed);
    });
  });

  if (numericValues.length === 0) return { labels: [], values: [] };

  const min = Math.min(...numericValues);
  const max = Math.max(...numericValues);
  const binCount = Math.max(5, Math.min(10, Math.round(Math.sqrt(numericValues.length))));
  const width = max === min ? 1 : (max - min) / binCount;
  const buckets = new Array(binCount).fill(0);

  numericValues.forEach((value) => {
    const rawIndex = width === 0 ? 0 : Math.floor((value - min) / width);
    const index = Math.max(0, Math.min(binCount - 1, rawIndex));
    buckets[index] += 1;
  });

  const labels = buckets.map((_, index) => {
    const start = min + index * width;
    const end = start + width;
    return `${start.toFixed(1)} - ${end.toFixed(1)}`;
  });

  return { labels, values: buckets };
};

const extractHeatmapValues = (rows: unknown[]): { xAxis: string[]; yAxis: string[]; data: number[][] } => {
  const triples: Array<[string, string, number]> = [];

  rows.forEach((row) => {
    if (Array.isArray(row) && row.length >= 3) {
      const value = toFiniteNumber(row[2]);
      if (value === null) return;
      triples.push([String(row[0]), String(row[1]), value]);
      return;
    }

    if (typeof row === "object" && row) {
      const entries = Object.entries(row as Record<string, unknown>).filter(([key]) => key !== "extra_info");
      if (entries.length < 3) return;
      const axisEntries = entries.filter(([, value]) => typeof value !== "number");
      const numericEntry = entries.find(([, value]) => toFiniteNumber(value) !== null);
      if (axisEntries.length < 2 || !numericEntry) return;
      const value = toFiniteNumber(numericEntry[1]);
      if (value === null) return;
      triples.push([String(axisEntries[0][1]), String(axisEntries[1][1]), value]);
    }
  });

  const xAxis = Array.from(new Set(triples.map((item) => item[0])));
  const yAxis = Array.from(new Set(triples.map((item) => item[1])));
  const data = triples
    .map(([x, y, value]) => {
      const xIndex = xAxis.indexOf(x);
      const yIndex = yAxis.indexOf(y);
      if (xIndex === -1 || yIndex === -1) return null;
      return [xIndex, yIndex, value];
    })
    .filter((value): value is number[] => Array.isArray(value));

  return { xAxis, yAxis, data };
};

const extractBoxplotValues = (rows: unknown[]): { categories: string[]; data: number[][] } => {
  const categories: string[] = [];
  const data: number[][] = [];

  rows.forEach((row, index) => {
    if (!row || typeof row !== "object") return;
    const record = row as Record<string, unknown>;
    const stats = [
      toFiniteNumber(record.min),
      toFiniteNumber(record.q1),
      toFiniteNumber(record.median),
      toFiniteNumber(record.q3),
      toFiniteNumber(record.max),
    ];
    if (stats.some((value) => value === null)) return;

    categories.push(String(record.name ?? record.category ?? `Grupo ${index + 1}`));
    data.push(stats as number[]);
  });

  return { categories, data };
};

const extractDualAxisValues = (rows: unknown[]): { categories: string[]; bars: number[]; line: number[] } => {
  const categories: string[] = [];
  const bars: number[] = [];
  const line: number[] = [];

  extractNamedValues(rows).forEach((item) => {
    const secondaryCandidate = item.extraInfo?.secondary_value
      ?? item.extraInfo?.growth
      ?? item.extraInfo?.yoy;
    const secondary = toFiniteNumber(secondaryCandidate);
    categories.push(item.name);
    bars.push(item.value);
    line.push(secondary ?? 0);
  });

  return { categories, bars, line };
};

const hasNamedValueShape = (rows: unknown[]): boolean => extractNamedValues(rows).length > 0;

const hasScatterShape = (rows: unknown[]): boolean => extractScatterValues(rows).length > 0;

const hasBubbleShape = (rows: unknown[]): boolean => extractBubbleValues(rows).length > 0;

const hasHeatmapShape = (rows: unknown[]): boolean => extractHeatmapValues(rows).data.length > 0;

const hasBoxplotShape = (rows: unknown[]): boolean => extractBoxplotValues(rows).data.length > 0;

const hasHistogramShape = (rows: unknown[]): boolean => extractHistogram(rows).values.length > 0;

const hasGanttShape = (rows: unknown[]): boolean => extractGanttValues(rows).data.length > 0;

const hasStackedShape = (rows: unknown[]): boolean => {
  const matrix = extractSeriesMatrix(rows);
  return matrix.categories.length > 0 && matrix.seriesNames.length >= 2;
};

const hasSecondarySeriesShape = (rows: unknown[]): boolean => {
  if (!Array.isArray(rows) || rows.length === 0) return false;

  return rows.some((row) => {
    if (!row || typeof row !== "object" || Array.isArray(row)) return false;
    const record = row as Record<string, unknown>;
    const extraInfo = typeof record.extra_info === "object" && record.extra_info
      ? record.extra_info as Record<string, unknown>
      : null;

    return (
      toFiniteNumber(extraInfo?.secondary_value) !== null
      || toFiniteNumber(extraInfo?.growth) !== null
      || toFiniteNumber(extraInfo?.yoy) !== null
    );
  });
};

function getPayloadTransformFailureReason(
  payload: VisualSourcePayload | null | undefined,
  visualId: VisualId,
): string | null {
  const rows = Array.isArray(payload?.rows) ? payload.rows : [];

  if (rows.length === 0) {
    return "Este visual no expone datos fuente suficientes para reconstruccion local.";
  }

  if (visualId === "smart_table") {
    return "Smart Table se activa desde la vista tabular o por densidad automatica.";
  }

  if (visualId === "stacked_bar_chart" && !hasStackedShape(rows)) {
    return "Stacked Bar requiere categorias con al menos dos series comparables.";
  }

  if ((visualId === "bar_chart"
    || visualId === "line_chart"
    || visualId === "area_chart"
    || visualId === "pie_chart"
    || visualId === "treemap"
    || visualId === "waterfall_chart"
    || visualId === "funnel_chart"
    || visualId === "pareto_chart") && !hasNamedValueShape(rows)) {
    return "Este visual requiere una serie agregada con nombre y valor.";
  }

  if (visualId === "gauge_chart") {
    const namedValues = extractNamedValues(rows);
    if (namedValues.length !== 1) {
      return "Gauge requiere una sola observacion principal.";
    }
  }

  if (visualId === "scatter_plot" && !hasScatterShape(rows)) {
    return "Scatter requiere al menos dos metricas numericas por punto.";
  }

  if (visualId === "bubble_chart" && !hasBubbleShape(rows)) {
    return "Bubble requiere X, Y y una tercera magnitud para el tamano.";
  }

  if (visualId === "heatmap_chart" && !hasHeatmapShape(rows)) {
    return "Heatmap requiere dos ejes y un valor de intensidad por celda.";
  }

  if (visualId === "boxplot_chart" && !hasBoxplotShape(rows)) {
    return "Boxplot requiere estadisticos de distribucion (min, q1, median, q3, max).";
  }

  if ((visualId === "dual_axis_chart" || visualId === "combo_chart") && !hasSecondarySeriesShape(rows)) {
    return "Este visual requiere una serie principal y una metrica secundaria real.";
  }

  if (visualId === "histogram_chart" && !hasHistogramShape(rows)) {
    return "Histogram requiere valores numericos crudos, no categorias agregadas.";
  }

  if (visualId === "gantt_chart" && !hasGanttShape(rows)) {
    return "Gantt requiere campos de inicio y fin por tarea.";
  }

  return null;
}

const extractGanttValues = (rows: unknown[]): { categories: string[]; data: unknown[] } => {
  const categories: string[] = [];
  const seriesData: unknown[] = [];

  rows.forEach((row) => {
    if (!row || typeof row !== "object") return;
    const record = row as Record<string, unknown>;
    const category = String(record.category ?? record.categoria ?? record.tarea ?? "");
    const start = record.start_date ?? record.inicio;
    const end = record.end_date ?? record.fin;
    if (!category || !start || !end) return;
    if (!categories.includes(category)) categories.push(category);
    const categoryIndex = categories.indexOf(category);
    seriesData.push([
      categoryIndex,
      new Date(String(start)).getTime(),
      new Date(String(end)).getTime(),
      category,
    ]);
  });

  return { categories, data: seriesData };
};

export const getTransformSupportReason = (
  visualId: VisualId,
  payload?: VisualSourcePayload | null,
): string | null => {
  if (payload) {
    return getPayloadTransformFailureReason(payload, visualId);
  }
  if (TRANSFORMABLE_VISUALS.has(visualId)) return null;
  if (visualId === "smart_table") return "Smart Table se activa desde la vista tabular o por densidad automatica.";
  return "Este visual aun no tiene reemplazo local instantaneo.";
};

export const isVisualTransformSupported = (
  visualId: VisualId,
  payload?: VisualSourcePayload | null,
): boolean => getTransformSupportReason(visualId, payload) === null;

export const buildVisualOptionFromPayload = (
  payload: VisualSourcePayload | null | undefined,
  visualId: VisualId,
): EChartsOption | null => {
  const rows = Array.isArray(payload?.rows) ? payload?.rows : [];
  if (rows.length === 0) return null;

  const option = baseOption();

  if (visualId === "bar_chart" || visualId === "line_chart" || visualId === "area_chart") {
    const data = extractNamedValues(rows);
    if (data.length === 0) return null;
    option.tooltip = { trigger: "axis" };
    option.xAxis = { type: "category", data: data.map((item) => item.name), boundaryGap: visualId === "bar_chart" };
    option.yAxis = { type: "value" };
    option.series = [
      {
        type: visualId === "bar_chart" ? "bar" : "line",
        smooth: visualId !== "bar_chart",
        data: data.map((item) => item.value),
        areaStyle: visualId === "area_chart" ? { opacity: 0.18 } : undefined,
        itemStyle: { color: COLORS[0] },
        lineStyle: { width: 3 },
        symbol: visualId === "bar_chart" ? undefined : "circle",
        symbolSize: visualId === "bar_chart" ? undefined : 7,
      },
    ];
    return option;
  }

  if (visualId === "stacked_bar_chart") {
    const matrix = extractSeriesMatrix(rows);
    if (matrix.categories.length === 0 || matrix.seriesNames.length < 2) return null;
    option.tooltip = { trigger: "axis" };
    option.xAxis = { type: "category", data: matrix.categories };
    option.yAxis = { type: "value" };
    option.series = matrix.seriesNames.map((name, index) => ({
      name,
      type: "bar",
      stack: "total",
      data: matrix.data[index] ?? [],
      itemStyle: { color: COLORS[index % COLORS.length] },
    }));
    return option;
  }

  if (visualId === "pie_chart") {
    const data = extractNamedValues(rows);
    if (data.length === 0) return null;
    option.tooltip = { trigger: "item" };
    option.color = COLORS;
    option.series = [
      {
        type: "pie",
        radius: ["42%", "72%"],
        avoidLabelOverlap: true,
        minShowLabelAngle: 4,
        data: data.map((item, index) => ({
          name: item.name,
          value: item.value,
          itemStyle: { color: COLORS[index % COLORS.length] },
        })),
        itemStyle: { borderColor: "#fff", borderWidth: 2, borderRadius: 6 },
        labelLine: {
          show: true,
          length: 12,
          length2: 8,
          smooth: 0.2,
          lineStyle: { color: "rgba(148, 163, 184, 0.85)", width: 1 },
        },
        label: {
          show: true,
          formatter: "{b}\n{c}",
          position: "outside",
          fontSize: 12,
          lineHeight: 16,
          distanceToLabelLine: 4,
        },
        emphasis: { label: { show: true, fontWeight: "bold" } },
      },
    ];
    return option;
  }

  if (visualId === "treemap") {
    const data = extractNamedValues(rows);
    if (data.length === 0) return null;
    option.tooltip = { trigger: "item" };
    option.color = COLORS;
    option.series = [{
      type: "treemap",
      data: data.map((item, index) => ({
        name: item.name,
        value: item.value,
        itemStyle: { color: COLORS[index % COLORS.length] },
      })),
    }];
    return option;
  }

  if (visualId === "scatter_plot") {
    const groupedSeries = extractScatterSeries(rows);
    if (groupedSeries.length > 1) {
      option.tooltip = { trigger: "item" };
      option.xAxis = { type: "value", name: payload?.x_label || "X", scale: true };
      option.yAxis = { type: "value", name: payload?.y_label || "Y", scale: true };
      option.series = groupedSeries.map((series, index) => ({
        name: series.name,
        type: "scatter",
        data: series.data,
        itemStyle: { color: COLORS[index % COLORS.length] },
        symbolSize: 10,
      }));
      return option;
    }

    const points = extractScatterValues(rows);
    if (points.length === 0) return null;
    option.tooltip = { trigger: "item" };
    option.xAxis = { type: "value", name: payload?.x_label || "X", scale: true };
    option.yAxis = { type: "value", name: payload?.y_label || "Y", scale: true };
    option.series = [{ type: "scatter", data: points, itemStyle: { color: COLORS[0] }, symbolSize: 10 }];
    return option;
  }

  if (visualId === "bubble_chart") {
    const points = extractBubbleValues(rows);
    if (points.length === 0) return null;
    option.tooltip = { trigger: "item" };
    option.xAxis = { type: "value", name: payload?.x_label || "X", scale: true };
    option.yAxis = { type: "value", name: payload?.y_label || "Y", scale: true };
    option.series = [{
      type: "scatter",
      data: points.map((point) => ({
        name: point.name,
        value: point.value,
        symbolSize: Math.max(10, Math.min(42, point.value[2])),
      })),
      itemStyle: { color: COLORS[0], opacity: 0.78 },
    }];
    return option;
  }

  if (visualId === "heatmap_chart") {
    const heatmap = extractHeatmapValues(rows);
    if (heatmap.data.length === 0) return null;
    option.grid = { top: 48, left: 48, right: 20, bottom: 64, containLabel: true };
    option.xAxis = { type: "category", data: heatmap.xAxis, splitArea: { show: true } };
    option.yAxis = { type: "category", data: heatmap.yAxis, splitArea: { show: true } };
    option.visualMap = {
      min: 0,
      max: Math.max(...heatmap.data.map((item) => Number(item[2] ?? 0)), 0),
      calculable: true,
      orient: "horizontal",
      left: "center",
      bottom: 8,
    };
    option.series = [{ type: "heatmap", data: heatmap.data, label: { show: true } }];
    return option;
  }

  if (visualId === "waterfall_chart") {
    const data = extractNamedValues(rows);
    if (data.length === 0) return null;
    let running = 0;
    const base: number[] = [];
    const values: Array<{ value: number; itemStyle: { color: string } }> = [];

    data.forEach((item) => {
      if (item.value >= 0) {
        base.push(running);
        values.push({ value: item.value, itemStyle: { color: COLORS[1] } });
        running += item.value;
      } else {
        running += item.value;
        base.push(running);
        values.push({ value: Math.abs(item.value), itemStyle: { color: COLORS[4] } });
      }
    });

    option.tooltip = { trigger: "axis" };
    option.xAxis = { type: "category", data: data.map((item) => item.name) };
    option.yAxis = { type: "value" };
    option.series = [
      { type: "bar", stack: "total", data: base, itemStyle: { color: "rgba(0,0,0,0)" }, emphasis: { disabled: true } },
      { type: "bar", stack: "total", data: values, label: { show: true, position: "top" } },
    ];
    return option;
  }

  if (visualId === "funnel_chart") {
    const data = extractNamedValues(rows).sort((left, right) => right.value - left.value);
    if (data.length === 0) return null;
    option.tooltip = { trigger: "item" };
    option.color = COLORS;
    option.series = [
      {
        type: "funnel",
        width: "80%",
        left: "10%",
        top: 32,
        bottom: 32,
        sort: "descending",
        min: 0,
        max: data[0]?.value || 100,
        data: data.map((item, index) => ({
          name: item.name,
          value: item.value,
          itemStyle: { color: COLORS[index % COLORS.length] },
        })),
      },
    ];
    return option;
  }

  if (visualId === "pareto_chart") {
    const data = extractNamedValues(rows).sort((left, right) => right.value - left.value);
    if (data.length === 0) return null;
    const total = data.reduce((acc, item) => acc + item.value, 0);
    let cumulative = 0;
    const cumulativeValues = data.map((item) => {
      cumulative += item.value;
      return total > 0 ? Number(((cumulative / total) * 100).toFixed(1)) : 0;
    });
    option.tooltip = { trigger: "axis" };
    option.xAxis = { type: "category", data: data.map((item) => item.name) };
    option.yAxis = [
      { type: "value", name: "Valor" },
      { type: "value", name: "Acumulado %", min: 0, max: 100, axisLabel: { formatter: "{value} %" } },
    ];
    option.series = [
      { type: "bar", name: "Valor", data: data.map((item) => item.value), itemStyle: { color: COLORS[0] } },
      {
        type: "line",
        name: "Acumulado %",
        yAxisIndex: 1,
        data: cumulativeValues,
        smooth: true,
        itemStyle: { color: COLORS[4] },
        markLine: { data: [{ yAxis: 80 }], lineStyle: { type: "dashed", color: COLORS[4] } },
      },
    ];
    return option;
  }

  if (visualId === "gauge_chart") {
    const data = extractNamedValues(rows);
    const value = data[0]?.value;
    if (typeof value !== "number") return null;
    option.series = [
      {
        type: "gauge",
        startAngle: 180,
        endAngle: 0,
        min: 0,
        max: 100,
        radius: "100%",
        center: ["50%", "75%"],
        progress: { show: true, width: 18, itemStyle: { color: COLORS[1] } },
        axisLine: { lineStyle: { width: 18 } },
        axisTick: { show: false },
        detail: { fontSize: 20, offsetCenter: [0, "-20%"], formatter: "{value}%" },
        data: [{ value, name: payload?.title || "KPI" }],
      },
    ];
    return option;
  }

  if (visualId === "dual_axis_chart") {
    const dualAxis = extractDualAxisValues(rows);
    if (dualAxis.categories.length === 0) return null;
    option.tooltip = { trigger: "axis" };
    option.xAxis = { type: "category", data: dualAxis.categories };
    option.yAxis = [
      { type: "value", name: "Valor" },
      { type: "value", name: "Variacion %", axisLabel: { formatter: "{value} %" } },
    ];
    option.series = [
      { type: "bar", name: "Valor", data: dualAxis.bars, itemStyle: { color: COLORS[0] } },
      { type: "line", name: "Variacion %", yAxisIndex: 1, data: dualAxis.line, smooth: true, itemStyle: { color: COLORS[4] } },
    ];
    return option;
  }

  if (visualId === "combo_chart") {
    const matrix = extractSeriesMatrix(rows);
    if (matrix.categories.length > 0 && matrix.seriesNames.length >= 2) {
      option.tooltip = { trigger: "axis" };
      option.xAxis = { type: "category", data: matrix.categories };
      option.yAxis = [{ type: "value" }, { type: "value" }];
      option.series = [
        {
          type: "bar",
          name: matrix.seriesNames[0],
          data: matrix.data[0] ?? [],
          itemStyle: { color: COLORS[0] },
        },
        {
          type: "line",
          name: matrix.seriesNames[1],
          yAxisIndex: 1,
          data: matrix.data[1] ?? [],
          smooth: true,
          itemStyle: { color: COLORS[4] },
        },
      ];
      return option;
    }

    const dualAxis = extractDualAxisValues(rows);
    if (dualAxis.categories.length === 0) return null;
    option.tooltip = { trigger: "axis" };
    option.xAxis = { type: "category", data: dualAxis.categories };
    option.yAxis = [{ type: "value", name: "Valor" }, { type: "value", name: "Variacion %", axisLabel: { formatter: "{value} %" } }];
    option.series = [
      { type: "bar", name: "Valor", data: dualAxis.bars, itemStyle: { color: COLORS[0] } },
      { type: "line", name: "Variacion %", yAxisIndex: 1, data: dualAxis.line, smooth: true, itemStyle: { color: COLORS[4] } },
    ];
    return option;
  }

  if (visualId === "histogram_chart") {
    const histogram = extractHistogram(rows);
    if (histogram.labels.length === 0) return null;
    option.tooltip = { trigger: "axis" };
    option.xAxis = { type: "category", data: histogram.labels, axisLabel: { rotate: 30 } };
    option.yAxis = { type: "value", name: "Frecuencia" };
    option.series = [{
      type: "bar",
      name: "Frecuencia",
      data: histogram.values,
      barMaxWidth: 40,
      itemStyle: { color: COLORS[0], borderRadius: [4, 4, 0, 0] },
    }];
    return option;
  }

  if (visualId === "boxplot_chart") {
    const boxplot = extractBoxplotValues(rows);
    if (boxplot.data.length === 0) return null;
    option.tooltip = { trigger: "item" };
    option.xAxis = { type: "category", data: boxplot.categories };
    option.yAxis = { type: "value" };
    option.series = [{ type: "boxplot", data: boxplot.data }];
    return option;
  }

  if (visualId === "gantt_chart") {
    const gantt = extractGanttValues(rows);
    if (gantt.data.length === 0) return null;
    option.tooltip = { formatter: "Detalle: <br/>{b}" };
    option.xAxis = { type: "time", position: "top" };
    option.yAxis = { type: "category", data: gantt.categories };
    option.series = [
      {
        type: "custom",
        renderItem: "renderGanttItem",
        data: gantt.data,
        itemStyle: { opacity: 0.8, color: COLORS[0] },
      },
    ] as any;
    return option;
  }

  return null;
};
