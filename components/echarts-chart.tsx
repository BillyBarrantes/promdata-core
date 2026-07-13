// components/echarts-chart.tsx
"use client"

import React, { useRef, useEffect } from 'react';
import * as echarts from 'echarts';
import { EChartsOption } from 'echarts';
import { useAtomValue } from 'jotai';
import { drillDownVisibleAtom } from '@/lib/state';

import { useTheme } from 'next-themes';
import { Info, MousePointerClick } from 'lucide-react';

interface EChartsChartProps {
  option: EChartsOption;
  style?: React.CSSProperties;
  isThumbnail?: boolean;
  onChartClick?: (params: any) => void;
  onEvents?: Record<string, (params: any, instance: echarts.ECharts) => void>;
  interactionMode?: 'explore' | 'filter';
}

const resolveRawCategoryFromEvent = (params: any, fullOption: any): string | null => {
  const seriesType = String(
    params?.seriesType
      || params?.series?.type
      || (
        Array.isArray(fullOption?.series)
          ? fullOption?.series?.[params?.seriesIndex ?? 0]?.type
          : fullOption?.series?.type
      )
      || ''
  ).toLowerCase();

  const isHierarchySeries = seriesType === 'treemap' || seriesType === 'sunburst';

  const candidates: unknown[] = [
    params?.rawCategory,
    params?.data?.raw_name,
    params?.data?.rawName,
    params?.data?.full_name,
    params?.data?.fullName,
  ];

  // Treemap/Sunburst: priorizar SIEMPRE el nodo realmente clickeado.
  if (isHierarchySeries) {
    candidates.push(params?.data?.name);
    candidates.push(params?.name);
    if (Array.isArray(params?.treePathInfo) && params.treePathInfo.length > 0) {
      candidates.push(params.treePathInfo[params.treePathInfo.length - 1]?.name);
    }
  }

  const dataIndex = typeof params?.dataIndex === 'number' ? params.dataIndex : -1;
  if (dataIndex >= 0 && fullOption) {
    const sourceSeries = Array.isArray(fullOption.series)
      ? (fullOption.series[params?.seriesIndex] || fullOption.series[0])
      : fullOption.series;
    const sourceData = sourceSeries?.data;

    if (Array.isArray(sourceData) && sourceData[dataIndex] !== undefined) {
      const rawDatum = sourceData[dataIndex];
      if (rawDatum && typeof rawDatum === 'object') {
        candidates.push((rawDatum as any).raw_name);
        candidates.push((rawDatum as any).rawName);
        candidates.push((rawDatum as any).full_name);
        candidates.push((rawDatum as any).fullName);
        if (!isHierarchySeries) {
          candidates.push((rawDatum as any).name);
        }
        if (Array.isArray((rawDatum as any).value)) {
          candidates.push((rawDatum as any).value[3]);
          candidates.push((rawDatum as any).value[0]);
        }
      } else {
        candidates.push(rawDatum);
      }
    }

    const xAxis = Array.isArray(fullOption.xAxis) ? fullOption.xAxis[0] : fullOption.xAxis;
    const yAxis = Array.isArray(fullOption.yAxis) ? fullOption.yAxis[0] : fullOption.yAxis;
    const axisData = xAxis?.type === 'category'
      ? xAxis?.data
      : yAxis?.type === 'category'
        ? yAxis?.data
        : null;
    if (Array.isArray(axisData) && axisData[dataIndex] !== undefined) {
      candidates.push(axisData[dataIndex]);
    }
  }

  if (!isHierarchySeries) {
    candidates.push(params?.data?.name);
  }
  if (Array.isArray(params?.value)) {
    candidates.push(params.value[3]);
    candidates.push(params.value[0]);
  }
  if (Array.isArray(params?.data?.value)) {
    candidates.push(params.data.value[3]);
    candidates.push(params.data.value[0]);
  }
  if (!isHierarchySeries) {
    candidates.push(params?.name);
  }
  candidates.push(params?.axisValue);
  candidates.push(params?.axisValueLabel);

  for (const candidate of candidates) {
    if (typeof candidate !== 'string') continue;
    const trimmed = candidate.replace(/\0/g, '').trim();
    if (!trimmed) continue;
    return trimmed;
  }

  return null;
};

const resolveHeatmapSecondaryCategory = (params: any, fullOption: any): string | null => {
  const seriesType = String(
    params?.seriesType
      || params?.series?.type
      || (
        Array.isArray(fullOption?.series)
          ? fullOption?.series?.[params?.seriesIndex ?? 0]?.type
          : fullOption?.series?.type
      )
      || ''
  ).toLowerCase();

  if (seriesType !== 'heatmap') return null;

  const yAxis = Array.isArray(fullOption?.yAxis) ? fullOption.yAxis[0] : fullOption?.yAxis;
  const yAxisData = Array.isArray(yAxis?.data) ? yAxis.data : [];
  const point = Array.isArray(params?.data)
    ? params.data
    : (Array.isArray(params?.value) ? params.value : []);

  const yIndex = Number(point?.[1]);
  if (!Number.isInteger(yIndex) || yIndex < 0 || yIndex >= yAxisData.length) {
    return null;
  }

  const raw = yAxisData[yIndex];
  if (raw === null || raw === undefined) return null;
  const text = String(raw).replace(/\0/g, '').normalize('NFC').replace(/\s+/g, ' ').trim();
  return text || null;
};

const isCurrencyLike = (value: unknown): boolean => {
  const normalized = String(value || "").toLowerCase();
  return ["venta", "ingreso", "costo", "precio", "monto", "revenue", "utilidad", "$", "s/"].some((token) => normalized.includes(token));
};

const isQuantityLike = (value: unknown): boolean => {
  const normalized = String(value || "").toLowerCase();
  return ["stock", "inventario", "cantidad", "qty", "units", "unidades", "volumen", "piezas", "disponible"].some((token) => normalized.includes(token));
};

const isPercentLike = (value: unknown): boolean => {
  const normalized = String(value || "").toLowerCase();
  return normalized.includes("%") || normalized.includes("porcent");
};

const parseDisplayNumber = (value: string): number | null => {
  const cleaned = value
    .replace(/\0/g, "")
    .replace(/[^\d,.\-+]/g, "")
    .trim();

  if (!cleaned) return null;

  if (/^[+-]?\d{1,3}(,\d{3})+(\.\d+)?$/.test(cleaned)) {
    const parsed = Number(cleaned.replace(/,/g, ""));
    return Number.isFinite(parsed) ? parsed : null;
  }

  if (/^[+-]?\d{1,3}(\.\d{3})+(,\d+)?$/.test(cleaned)) {
    const normalized = cleaned.replace(/\./g, "").replace(",", ".");
    const parsed = Number(normalized);
    return Number.isFinite(parsed) ? parsed : null;
  }

  if (/^[+-]?\d+([.,]\d+)?$/.test(cleaned)) {
    const parsed = Number(cleaned.replace(",", "."));
    return Number.isFinite(parsed) ? parsed : null;
  }

  return null;
};

const formatBusinessValue = (value: unknown, label?: string): string => {
  if (value === null || value === undefined || value === "") return "Sin dato";
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = parseDisplayNumber(value);
    if (parsed === null) return value;
    value = parsed;
  }
  if (typeof value !== "number" || !Number.isFinite(value)) return String(value);

  if (isPercentLike(label)) {
    return `${value.toLocaleString(undefined, { maximumFractionDigits: 2 })}%`;
  }

  if (isQuantityLike(label)) {
    return Math.round(value).toLocaleString(undefined, { maximumFractionDigits: 0 });
  }

  if (isCurrencyLike(label)) {
    return `S/ ${value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  }

  if (Math.abs(value) >= 1000) {
    return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
  }

  return Number.isInteger(value)
    ? value.toLocaleString()
    : value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
};

const buildSemanticValueHint = (
  currentOption: any,
  ...parts: Array<unknown>
): string => {
  const contract = currentOption?.query_contract || {};
  return [
    contract?.metric,
    contract?.value_column,
    contract?.dimension,
    currentOption?.title?.text,
    ...parts,
  ]
    .filter((part) => part !== null && part !== undefined && String(part).trim() !== "")
    .map((part) => String(part))
    .join(" | ");
};

const normalizeSeriesValue = (rawValue: unknown): number | null => {
  if (Array.isArray(rawValue) && rawValue.length > 0) {
    const numericCandidates = [...rawValue].reverse().filter((entry) => typeof entry === "number" && Number.isFinite(entry));
    return numericCandidates.length > 0 ? Number(numericCandidates[0]) : null;
  }

  if (typeof rawValue === "number" && Number.isFinite(rawValue)) return rawValue;

  if (rawValue && typeof rawValue === "object" && "value" in (rawValue as Record<string, unknown>)) {
    return normalizeSeriesValue((rawValue as Record<string, unknown>).value);
  }

  return null;
};

const describeVariation = (currentValue: number | null, previousValue: number | null, label?: string): string | null => {
  if (currentValue === null || previousValue === null || previousValue === 0) return null;
  const delta = ((currentValue - previousValue) / Math.abs(previousValue)) * 100;
  if (!Number.isFinite(delta)) return null;

  const formatted = `${Math.abs(delta).toFixed(1)}%`;
  if (Math.abs(delta) < 0.2) {
    return `Se mantiene estable frente al punto previo (${formatted}).`;
  }

  const subject = label ? `en ${label}` : "";
  if (delta > 0) return `Sube ${formatted} ${subject} frente al punto previo.`.trim();
  return `Cae ${formatted} ${subject} frente al punto previo.`.trim();
};

const describeShareLevel = (percent: number | null): string => {
  if (percent === null) return "Mide composición y peso relativo dentro del total.";
  if (percent >= 50) return "Es el componente dominante del total analizado.";
  if (percent >= 20) return "Tiene un peso material dentro de la composición.";
  if (percent >= 5) return "Aporta una fracción secundaria del total.";
  return "Su participación es marginal dentro del total.";
};

const describeHeatLevel = (value: number | null, allSeriesValues: number[]): string => {
  if (value === null || allSeriesValues.length === 0) return "La intensidad refleja concentración o relación entre variables.";
  const max = Math.max(...allSeriesValues);
  const ratio = max > 0 ? value / max : 0;
  if (ratio >= 0.75) return "Es una zona de intensidad alta y requiere atención prioritaria.";
  if (ratio >= 0.4) return "Muestra una intensidad media con relevancia operativa.";
  return "Representa una intensidad baja frente al resto del cruce.";
};

const describeGaugeLevel = (value: number | null): string => {
  if (value === null) return "Lectura ejecutiva de cumplimiento del indicador.";
  if (value >= 90) return "Nivel sobresaliente, cerca del objetivo ideal.";
  if (value >= 70) return "Nivel saludable con margen de mejora controlado.";
  if (value >= 50) return "Nivel intermedio; conviene seguimiento cercano.";
  return "Nivel crítico o rezagado; requiere acción correctiva.";
};

const describeScatterPoint = (xValue: number | null, yValue: number | null, xSeries: number[], ySeries: number[]): string => {
  if (xValue === null || yValue === null || xSeries.length === 0 || ySeries.length === 0) {
    return "Permite detectar relaciones, agrupaciones y puntos atípicos.";
  }

  const avgX = xSeries.reduce((acc, value) => acc + value, 0) / xSeries.length;
  const avgY = ySeries.reduce((acc, value) => acc + value, 0) / ySeries.length;

  if (xValue >= avgX && yValue >= avgY) return "Se ubica en el cuadrante alto-alto frente al universo visible.";
  if (xValue < avgX && yValue >= avgY) return "Combina eje X rezagado con eje Y fuerte frente al promedio.";
  if (xValue >= avgX && yValue < avgY) return "Combina eje X fuerte con eje Y rezagado frente al promedio.";
  return "Se concentra en el cuadrante de menor desempeño relativo.";
};

const applyBusinessTooltip = (currentOption: any): any => {
  if (!currentOption || typeof currentOption?.tooltip?.formatter === "function") return currentOption;

  const seriesList = Array.isArray(currentOption.series) ? currentOption.series : currentOption.series ? [currentOption.series] : [];
  const primarySeries = seriesList[0];
  const primaryType = String(primarySeries?.type || "").toLowerCase();
  const xAxis = Array.isArray(currentOption.xAxis) ? currentOption.xAxis[0] : currentOption.xAxis;
  const yAxis = Array.isArray(currentOption.yAxis) ? currentOption.yAxis[0] : currentOption.yAxis;
  const vsp = (currentOption as any)?.visual_source_payload;
  const xName = xAxis?.name || vsp?.x_label || "Dimensión";
  const yName = yAxis?.name || vsp?.y_label || "Valor";

  currentOption.tooltip = {
    confine: true,
    backgroundColor: "rgba(15, 23, 42, 0.94)",
    borderWidth: 0,
    textStyle: { color: "#f8fafc" },
    extraCssText: "border-radius:12px;padding:12px;box-shadow:0 12px 32px rgba(15,23,42,.25);",
    ...(currentOption.tooltip || {}),
  };

  if (primaryType === "bar" || primaryType === "line") {
    currentOption.tooltip.trigger = "axis";
    currentOption.tooltip.formatter = (params: any) => {
      const items = Array.isArray(params) ? params : [params];
      const axisLabel = items[0]?.axisValueLabel || items[0]?.name || "Punto";
      const primaryItem = items[0];
      const currentValue = normalizeSeriesValue(primaryItem?.value);
      const previousData = Array.isArray(primaryItem?.seriesData) ? primaryItem.seriesData : [];
      const previousValue = typeof primaryItem?.dataIndex === "number" && primaryItem.dataIndex > 0
        ? normalizeSeriesValue(previousData[primaryItem.dataIndex - 1])
        : null;
      const seriesLines = items.map((item: any) => {
        const rawValue = normalizeSeriesValue(item?.value) ?? (Array.isArray(item?.value) ? item.value[item.value.length - 1] : item?.value);
        const label = item?.seriesName || yName;
        const semanticHint = buildSemanticValueHint(currentOption, label, yName);
        return `${item.marker || ""} ${label}: <b>${formatBusinessValue(rawValue, semanticHint)}</b>`;
      }).join("<br/>");
      const executiveNote = primaryType === "line"
        ? describeVariation(currentValue, previousValue, primaryItem?.seriesName || yName) || "Lectura temporal o secuencial del comportamiento."
        : "Comparación directa de magnitudes entre categorías.";
      return `<div><div style="font-weight:700;margin-bottom:4px;">${axisLabel}</div>${seriesLines}<div style="margin-top:8px;color:#cbd5e1;font-size:11px;line-height:1.45;"><span style="display:block;font-weight:600;color:#e2e8f0;margin-bottom:2px;">Lectura ejecutiva</span>${executiveNote}</div></div>`;
    };
    return currentOption;
  }

  if (primaryType === "pie" || primaryType === "treemap" || primaryType === "funnel") {
    currentOption.tooltip.trigger = "item";
    currentOption.tooltip.formatter = (params: any) => {
      const value = normalizeSeriesValue(params?.value) ?? params?.value?.value ?? params?.value;
      const percentNumber = typeof params?.percent === "number" ? params.percent : null;
      const percent = percentNumber !== null ? ` · ${percentNumber.toFixed(1)}% del total` : "";
      const semanticHint = buildSemanticValueHint(currentOption, params?.seriesName, params?.name);
      return `<div><div style="font-weight:700;margin-bottom:4px;">${params?.name || "Categoría"}</div><div>${params?.marker || ""} Valor: <b>${formatBusinessValue(value, semanticHint)}</b>${percent}</div><div style="margin-top:8px;color:#cbd5e1;font-size:11px;line-height:1.45;"><span style="display:block;font-weight:600;color:#e2e8f0;margin-bottom:2px;">Lectura ejecutiva</span>${describeShareLevel(percentNumber)}</div></div>`;
    };
    return currentOption;
  }

  if (primaryType === "scatter") {
    currentOption.tooltip.trigger = "item";
    currentOption.tooltip.formatter = (params: any) => {
      const point = Array.isArray(params?.value) ? params.value : [];
      const pointName = params?.data?.name || params?.name || "Punto";
      const sizeValue = point.length >= 3 ? `<br/>Impacto: <b>${formatBusinessValue(point[2], "Impacto")}</b>` : "";
      const scatterPoints = seriesList
        .flatMap((series: any) => Array.isArray(series?.data) ? series.data : [])
        .map((entry: any) => Array.isArray(entry?.value) ? entry.value : Array.isArray(entry) ? entry : [])
        .filter((entry: any[]) => entry.length >= 2);
      const xSeries = scatterPoints.map((entry: any[]) => Number(entry[0])).filter((value: number) => Number.isFinite(value));
      const ySeries = scatterPoints.map((entry: any[]) => Number(entry[1])).filter((value: number) => Number.isFinite(value));
      return `<div><div style="font-weight:700;margin-bottom:4px;">${pointName}</div><div>${xName}: <b>${formatBusinessValue(point[0], xName)}</b></div><div>${yName}: <b>${formatBusinessValue(point[1], yName)}</b></div>${sizeValue}<div style="margin-top:8px;color:#cbd5e1;font-size:11px;line-height:1.45;"><span style="display:block;font-weight:600;color:#e2e8f0;margin-bottom:2px;">Lectura ejecutiva</span>${describeScatterPoint(Number(point[0]), Number(point[1]), xSeries, ySeries)}</div></div>`;
    };
    return currentOption;
  }

  if (primaryType === "heatmap") {
    currentOption.tooltip.trigger = "item";
    currentOption.tooltip.formatter = (params: any) => {
      const point = Array.isArray(params?.data) ? params.data : [];
      const xValue = Array.isArray(xAxis?.data) ? xAxis.data[point[0]] : point[0];
      const yValue = Array.isArray(yAxis?.data) ? yAxis.data[point[1]] : point[1];
      const allHeatValues = seriesList
        .flatMap((series: any) => Array.isArray(series?.data) ? series.data : [])
        .map((entry: any) => Array.isArray(entry) ? Number(entry[2]) : null)
        .filter((value: number | null): value is number => value !== null && Number.isFinite(value));
      return `<div><div style="font-weight:700;margin-bottom:4px;">Cruce relevante</div><div>${xName}: <b>${xValue}</b></div><div>${yName}: <b>${yValue}</b></div><div>Intensidad: <b>${formatBusinessValue(point[2], "Intensidad")}</b></div><div style="margin-top:8px;color:#cbd5e1;font-size:11px;line-height:1.45;"><span style="display:block;font-weight:600;color:#e2e8f0;margin-bottom:2px;">Lectura ejecutiva</span>${describeHeatLevel(Number(point[2]), allHeatValues)}</div></div>`;
    };
    return currentOption;
  }

  if (primaryType === "gauge") {
    currentOption.tooltip.trigger = "item";
    currentOption.tooltip.formatter = (params: any) => {
      const gaugeValue = normalizeSeriesValue(params?.value) ?? params?.value?.value ?? params?.value;
      return `<div><div style="font-weight:700;margin-bottom:4px;">${params?.name || "Indicador"}</div><div>Resultado: <b>${formatBusinessValue(gaugeValue, "Porcentaje")}</b></div><div style="margin-top:8px;color:#cbd5e1;font-size:11px;line-height:1.45;"><span style="display:block;font-weight:600;color:#e2e8f0;margin-bottom:2px;">Lectura ejecutiva</span>${describeGaugeLevel(typeof gaugeValue === "number" ? gaugeValue : Number(gaugeValue))}</div></div>`;
    };
    return currentOption;
  }

  if (primaryType === "boxplot") {
    currentOption.tooltip.trigger = "item";
    currentOption.tooltip.formatter = (params: any) => {
      const value = Array.isArray(params?.data) ? params.data : [];
      const dispersion = typeof value[4] === "number" && typeof value[0] === "number"
        ? value[4] - value[0]
        : null;
      const dispersionNote = dispersion !== null && Number.isFinite(dispersion)
        ? `La dispersión visible es de ${formatBusinessValue(dispersion, yName)}, útil para detectar volatilidad.`
        : "Resume la dispersión y la mediana del grupo.";
      return `<div><div style="font-weight:700;margin-bottom:4px;">${params?.name || "Distribución"}</div><div>Mín: <b>${formatBusinessValue(value[0], yName)}</b></div><div>Q1: <b>${formatBusinessValue(value[1], yName)}</b></div><div>Mediana: <b>${formatBusinessValue(value[2], yName)}</b></div><div>Q3: <b>${formatBusinessValue(value[3], yName)}</b></div><div>Máx: <b>${formatBusinessValue(value[4], yName)}</b></div><div style="margin-top:8px;color:#cbd5e1;font-size:11px;line-height:1.45;"><span style="display:block;font-weight:600;color:#e2e8f0;margin-bottom:2px;">Lectura ejecutiva</span>${dispersionNote}</div></div>`;
    };
    return currentOption;
  }

  if (primaryType === "custom") {
    currentOption.tooltip.trigger = "item";
    currentOption.tooltip.formatter = (params: any) => {
      const value = Array.isArray(params?.value) ? params.value : [];
      const startDate = value[1] ? new Date(value[1]) : null;
      const endDate = value[2] ? new Date(value[2]) : null;
      const durationHours = startDate && endDate ? Math.max(0, (endDate.getTime() - startDate.getTime()) / 36e5) : null;
      const durationNote = durationHours !== null
        ? `Duración estimada: ${durationHours.toFixed(durationHours >= 24 ? 0 : 1)} h.`
        : "Lectura operativa de duración y secuencia.";
      return `<div><div style="font-weight:700;margin-bottom:4px;">${value[3] || params?.name || "Actividad"}</div><div>Inicio: <b>${startDate ? startDate.toLocaleString() : "Sin dato"}</b></div><div>Fin: <b>${endDate ? endDate.toLocaleString() : "Sin dato"}</b></div><div style="margin-top:8px;color:#cbd5e1;font-size:11px;line-height:1.45;"><span style="display:block;font-weight:600;color:#e2e8f0;margin-bottom:2px;">Lectura ejecutiva</span>${durationNote}</div></div>`;
    };
    return currentOption;
  }

  return currentOption;
};

const applyBusinessPieLabels = (currentOption: any): any => {
  if (!currentOption) return currentOption;

  const seriesList = Array.isArray(currentOption.series)
    ? currentOption.series
    : currentOption.series
      ? [currentOption.series]
      : [];

  if (!seriesList.some((series: any) => String(series?.type || "").toLowerCase() === "pie")) {
    return currentOption;
  }

  currentOption.series = seriesList.map((series: any) => {
    if (String(series?.type || "").toLowerCase() !== "pie") {
      return series;
    }

    const baseLabel = series?.label && typeof series.label === "object" ? series.label : {};
    const baseLabelLine = series?.labelLine && typeof series.labelLine === "object" ? series.labelLine : {};
    const baseEmphasis = series?.emphasis && typeof series.emphasis === "object" ? series.emphasis : {};
    const baseEmphasisLabel = baseEmphasis?.label && typeof baseEmphasis.label === "object" ? baseEmphasis.label : {};

    return {
      ...series,
      avoidLabelOverlap: true,
      minShowLabelAngle: series?.minShowLabelAngle ?? 4,
      labelLayout: series?.labelLayout ?? { hideOverlap: true },
      labelLine: {
        show: true,
        length: 12,
        length2: 8,
        smooth: 0.2,
        lineStyle: {
          color: "rgba(148, 163, 184, 0.85)",
          width: 1,
        },
        ...baseLabelLine,
      },
      label: {
        ...baseLabel,
        show: true,
        position: "outside",
        alignTo: "edge",
        edgeDistance: 8,
        bleedMargin: 4,
        distanceToLabelLine: 4,
        fontSize: 12,
        lineHeight: 16,
        color: "#64748b",
        formatter: (params: any) => {
          const value = normalizeSeriesValue(params?.value) ?? params?.value?.value ?? params?.value;
          const semanticHint = buildSemanticValueHint(currentOption, series?.name, params?.name);
          return `${params?.name || "Categoría"}\n${formatBusinessValue(value, semanticHint)}`;
        },
      },
      emphasis: {
        ...baseEmphasis,
        label: {
          ...baseEmphasisLabel,
          show: true,
          fontWeight: "bold",
        },
      },
    };
  });

  return currentOption;
};

const shouldHardResetOption = (option: any): boolean => {
  const seriesList = Array.isArray(option?.series)
    ? option.series
    : option?.series
      ? [option.series]
      : [];

  return seriesList.some((series: any) =>
    ["pie", "funnel", "gauge"].includes(String(series?.type || "").toLowerCase())
  );
};

const setOptionSafely = (
  chart: echarts.ECharts,
  option: any,
  opts: { notMerge?: boolean; lazyUpdate?: boolean; replaceMerge?: string[] } = {},
): void => {
  chart.dispatchAction({ type: "hideTip" });

  if (shouldHardResetOption(option)) {
    chart.clear();
    chart.setOption(option, {
      notMerge: true,
      lazyUpdate: opts.lazyUpdate ?? true,
    });
    return;
  }

  chart.setOption(option, opts);
};

// --- GANTT RENDERER ---
const renderGanttItem = (params: any, api: any) => {
  const categoryIndex = api.value(0);
  const start = api.coord([api.value(1), categoryIndex]);
  const end = api.coord([api.value(2), categoryIndex]);
  const height = api.size([0, 1])[1] * 0.6;

  if (isNaN(start[0]) || isNaN(end[0])) return;

  const width = end[0] - start[0];
  const rectShape = echarts.graphic.clipRectByRect({
    x: start[0],
    y: start[1] - height / 2,
    width: width,
    height: height
  }, {
    x: params.coordSys.x,
    y: params.coordSys.y,
    width: params.coordSys.width,
    height: params.coordSys.height
  });

  return rectShape && {
    type: 'rect',
    transition: ['shape'],
    shape: rectShape,
    style: api.style()
  };
};

// --- 🔍 ZOOM FILTER: crea opción filtrada mostrando solo el elemento clickeado ---
const applyZoomFilter = (fullOpt: any, clickedNamesInput: string | string[]): any => {
  const zoomed = { ...fullOpt };
  const clickedNames = Array.isArray(clickedNamesInput) ? clickedNamesInput : [clickedNamesInput];
  const selectedSet = new Set(clickedNames);

  // Detectar eje de categorías (horizontal o vertical)
  const xAxis = Array.isArray(zoomed.xAxis) ? zoomed.xAxis[0] : zoomed.xAxis;
  const yAxis = Array.isArray(zoomed.yAxis) ? zoomed.yAxis[0] : zoomed.yAxis;
  const categoryAxis = (xAxis?.type === 'category' && xAxis?.data) ? 'x'
                     : (yAxis?.type === 'category' && yAxis?.data) ? 'y'
                     : null;

  if (categoryAxis) {
    // Bar/Line con Category Axis
    const axis = categoryAxis === 'x' ? xAxis : yAxis;
    const selectedIndexes = Array.isArray(axis.data)
      ? axis.data
          .map((label: string, index: number) => selectedSet.has(label) ? index : -1)
          .filter((index: number) => index >= 0)
      : [];
    if (selectedIndexes.length === 0) return zoomed; // No encontrado, devolver sin filtrar

    // Clonar eje con solo la categoría clickeada
    const filteredAxis = { ...axis, data: selectedIndexes.map((index: number) => axis.data[index]) };
    if (categoryAxis === 'x') {
      zoomed.xAxis = Array.isArray(fullOpt.xAxis) ? [filteredAxis] : filteredAxis;
    } else {
      zoomed.yAxis = Array.isArray(fullOpt.yAxis) ? [filteredAxis] : filteredAxis;
    }

    // Filtrar datos de cada serie al índice correspondiente
    if (Array.isArray(zoomed.series)) {
      zoomed.series = zoomed.series.map((s: any) => {
        if (Array.isArray(s.data)) {
          const nextData = selectedIndexes
            .filter((index: number) => s.data[index] !== undefined)
            .map((index: number) => s.data[index]);
          if (nextData.length > 0) {
            return { ...s, data: nextData };
          }
        }
        return s;
      });
    }
  } else {
    // Pie/Map/Scatter: filtrar por name en data items
    if (Array.isArray(zoomed.series)) {
      zoomed.series = zoomed.series.map((s: any) => {
        if (Array.isArray(s.data)) {
          const filtered = s.data.filter((item: any) => {
            if (item && typeof item === 'object' && item.name) return selectedSet.has(item.name);
            return false;
          });
          if (filtered.length > 0) return { ...s, data: filtered };
        }
        return s;
      });
    }
  }

  return zoomed;
};


const EChartsChartInner: React.FC<EChartsChartProps> = ({ option, style, isThumbnail = false, onChartClick, onEvents, interactionMode = 'explore' }) => {

  const chartRef = useRef<HTMLDivElement>(null);
  const chartInstance = useRef<echarts.ECharts | null>(null);
  const { resolvedTheme } = useTheme();

  // 🎨 Visual feedback state (ref = sin React re-render)
  const selectedRef = useRef<string[]>([]);
  const [selectionCount, setSelectionCount] = React.useState(0);
  // 🔍 Zoom: almacena la opción completa para restaurar tras el filtro
  const fullOptionRef = useRef<any>(null);
  const lastFilterPreviewRef = useRef<{ seriesIndex?: number; dataIndex?: number } | null>(null);
  const filterCallbackTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const cancelPendingFilterCallback = React.useCallback(() => {
    if (filterCallbackTimeoutRef.current) {
      clearTimeout(filterCallbackTimeoutRef.current);
      filterCallbackTimeoutRef.current = null;
    }
  }, []);

  const clearFilterPreview = React.useCallback(() => {
    if (!chartInstance.current) return;
    cancelPendingFilterCallback();
    chartInstance.current.dispatchAction({ type: 'downplay' });
    lastFilterPreviewRef.current = null;
  }, [cancelPendingFilterCallback]);

  const applyFilterPreview = React.useCallback((params: any) => {
    if (!chartInstance.current) return;

    chartInstance.current.dispatchAction({ type: 'downplay' });
    chartInstance.current.dispatchAction({
      type: 'highlight',
      seriesIndex: params?.seriesIndex,
      dataIndex: params?.dataIndex,
    });

    lastFilterPreviewRef.current = {
      seriesIndex: typeof params?.seriesIndex === 'number' ? params.seriesIndex : undefined,
      dataIndex: typeof params?.dataIndex === 'number' ? params.dataIndex : undefined,
    };
  }, []);

  // 🎨 Auto-clear + restore when DrillDown menu closes
  const isDrillDownVisible = useAtomValue(drillDownVisibleAtom);
  const prevVisibleRef = useRef(false);
  const hasTreemapRef = useRef(false);
  useEffect(() => {
    if (prevVisibleRef.current && !isDrillDownVisible && chartInstance.current) {
      // Menu se cerró → restaurar gráfico completo
      if (selectedRef.current.length > 0 && fullOptionRef.current) {
        setOptionSafely(chartInstance.current, fullOptionRef.current, { notMerge: true, lazyUpdate: true });
        clearFilterPreview();
        selectedRef.current = [];
        setSelectionCount(0);
      }
    }
    prevVisibleRef.current = isDrillDownVisible;
  }, [clearFilterPreview, isDrillDownVisible]);

  // 🔒 Refs estables para callbacks — evitan re-runs del useEffect de init
  const onChartClickRef = useRef(onChartClick);
  const onEventsRef = useRef(onEvents);
  useEffect(() => { onChartClickRef.current = onChartClick; }, [onChartClick]);
  useEffect(() => { onEventsRef.current = onEvents; }, [onEvents]);

  // Initialize ECharts instance ONCE (no dependencies de callbacks)
  useEffect(() => {
    if (chartRef.current && !chartInstance.current) {
      chartInstance.current = echarts.init(chartRef.current, undefined, { renderer: 'svg' });
    }

    const handleChartClick = (params: any) => {
      const nativeEvent = params.event?.event;
      const clientX = nativeEvent?.clientX || 0;
      const clientY = nativeEvent?.clientY || 0;
      const chart = chartInstance.current;
      const fullOpt = fullOptionRef.current;
      const rawCategory = resolveRawCategoryFromEvent(params, fullOpt);
      const rawSecondaryCategory = resolveHeatmapSecondaryCategory(params, fullOpt);
      const hasTreemap = hasTreemapRef.current;
      const hasExternalDrillDown = Boolean(onChartClickRef.current);

      // Zoom visual solo cuando NO hay drilldown externo.
      // En chat (/inicio), el click dispara menú/filtro; mutar setOption aquí genera flicker + race en mouseout.
      const shouldApplyExploreZoom = interactionMode === 'explore' && chart && fullOpt && !hasTreemap && !hasExternalDrillDown;
      const shouldApplyFilterPreview = interactionMode === 'filter' && chart && !hasTreemap && hasExternalDrillDown;

      if (shouldApplyExploreZoom) {
        const clickedName = rawCategory || params.name;
        const isAdditiveSelection = Boolean(nativeEvent?.metaKey || nativeEvent?.ctrlKey || nativeEvent?.shiftKey);
        const previousSelection = [...selectedRef.current];

        let nextSelection: string[] = [];
        if (isAdditiveSelection) {
          nextSelection = previousSelection.includes(clickedName)
            ? previousSelection.filter((item) => item !== clickedName)
            : [...previousSelection, clickedName];
        } else if (previousSelection.length === 1 && previousSelection[0] === clickedName) {
          nextSelection = [];
        } else {
          nextSelection = [clickedName];
        }

        if (nextSelection.length === 0) {
          setOptionSafely(chart, fullOpt, { notMerge: true, lazyUpdate: true });
          chart.dispatchAction({ type: 'downplay' });
          selectedRef.current = [];
          setSelectionCount(0);
        } else {
          const zoomed = applyZoomFilter(fullOpt, nextSelection);
          setOptionSafely(chart, zoomed, { notMerge: true, lazyUpdate: true });
          selectedRef.current = nextSelection;
          setSelectionCount(nextSelection.length);
        }
      }

      if (shouldApplyFilterPreview) {
        applyFilterPreview(params);
      }

      const callbackPayload = {
        ...params,
        rawCategory,
        rawSecondaryCategory,
        eventCoordinates: { x: clientX, y: clientY }
      };

      // Callback externo (DrillDown / Cross-filter)
      if (onChartClickRef.current) {
        if (shouldApplyFilterPreview) {
          cancelPendingFilterCallback();
          filterCallbackTimeoutRef.current = setTimeout(() => {
            onChartClickRef.current?.(callbackPayload);
            filterCallbackTimeoutRef.current = null;
          }, 36);
        } else {
          onChartClickRef.current(callbackPayload);
        }
      }
    };

    if (chartInstance.current) {
      chartInstance.current.off('click');

      // Bind eventos personalizados via ref
      const events = onEventsRef.current;
      if (events) {
        Object.entries(events).forEach(([eventName, handler]) => {
          chartInstance.current?.off(eventName);
          chartInstance.current?.on(eventName, (params: any) => {
            if (chartInstance.current) handler(params, chartInstance.current);
          });
        });
      }

      if (!events || !events['click']) {
        chartInstance.current.on('click', 'series', handleChartClick);
      }

      // 🔍 Background click: restaurar gráfico completo
      const zr = chartInstance.current.getZr();
      const handleBgClick = (e: any) => {
        if (!e.target && chartInstance.current) {
          if (selectedRef.current.length > 0 && fullOptionRef.current) {
            setOptionSafely(chartInstance.current, fullOptionRef.current, { notMerge: true, lazyUpdate: true });
          }
          clearFilterPreview();
          selectedRef.current = [];
          setSelectionCount(0);
        }
      };
      (chartInstance.current as any).__bgClickHandler = handleBgClick;
      zr.on('click', handleBgClick);
    }

    return () => {
      cancelPendingFilterCallback();
      if (chartInstance.current) {
        chartInstance.current.off('click');
        // Cleanup background click handler (via stored ref)
        const bgHandler = (chartInstance.current as any).__bgClickHandler;
        if (bgHandler) {
          chartInstance.current.getZr().off('click', bgHandler);
        }
        const events = onEventsRef.current;
        if (events) {
          Object.keys(events).forEach(evt => chartInstance.current?.off(evt));
        }
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cancelPendingFilterCallback]); // Sin filtersAtom; callbacks internos via refs

  // Update chart option when option or theme change
  useEffect(() => {
    if (chartInstance.current) {
      // 🚀 Shallow clone: preserva TODAS las funciones (formatters, renderItem, etc.)
      // Solo clonamos los niveles que vamos a mutar
      let currentOption: any = { ...option };

      // 1. Inject Gantt Renderer if requested (series-level clone)
      if (Array.isArray(currentOption.series)) {
        currentOption.series = currentOption.series.map((s: any) => {
          if (s.type === 'custom' && s.renderItem === 'renderGanttItem') {
            return { ...s, renderItem: renderGanttItem };
          }
          return s;
        });
      }
      hasTreemapRef.current = Array.isArray(currentOption.series) && currentOption.series.some((s: any) => s?.type === 'treemap');

      // --- THEME OVERRIDES FOR DARK MODE ---
      const isDark = resolvedTheme === 'dark';
      const textColor = isDark ? '#e5e7eb' : '#374151';
      const mutedColor = isDark ? '#9ca3af' : '#6b7280';
      const gridColor = isDark ? '#374151' : '#e5e7eb';

      if (currentOption.title) {
        if (Array.isArray(currentOption.title)) {
          currentOption.title = currentOption.title.map((t: any) => ({
            ...t, textStyle: { ...t.textStyle, color: textColor }
          }));
        } else {
          currentOption.title = { ...currentOption.title, textStyle: { ...currentOption.title.textStyle, color: textColor } };
        }
      }
      if (currentOption.legend) {
        currentOption.legend = { ...currentOption.legend, textStyle: { ...currentOption.legend.textStyle, color: textColor } };
      }

      const cloneAxisWithTheme = (axis: any): any => {
        if (!axis) return axis;
        const axes = Array.isArray(axis) ? axis : [axis];
        const themed = axes.map((ax: any) => {
          const cloned = { ...ax };
          cloned.axisLabel = { ...cloned.axisLabel, color: mutedColor };
          cloned.nameTextStyle = { ...cloned.nameTextStyle, color: mutedColor };
          cloned.axisLine = { ...cloned.axisLine, lineStyle: { ...cloned.axisLine?.lineStyle, color: mutedColor } };
          if (cloned.splitLine) {
            cloned.splitLine = { ...cloned.splitLine, lineStyle: { ...cloned.splitLine.lineStyle, color: gridColor, opacity: 0.3 } };
          }
          return cloned;
        });
        return Array.isArray(axis) ? themed : themed[0];
      };

      if (currentOption.xAxis) currentOption.xAxis = cloneAxisWithTheme(currentOption.xAxis);
      if (currentOption.yAxis) currentOption.yAxis = cloneAxisWithTheme(currentOption.yAxis);

      currentOption.animation = currentOption.animation ?? true;
      currentOption.animationDurationUpdate = currentOption.animationDurationUpdate ?? 240;
      currentOption.animationEasingUpdate = currentOption.animationEasingUpdate ?? 'cubicOut';

      // 🎨 Series: emphasis.focus + transiciones suaves para cross-filter
      if (currentOption.series && Array.isArray(currentOption.series)) {
        currentOption.series = currentOption.series.map((s: any, index: number) => {
          const cloned = { ...s };
          const seriesType = String(cloned.type ?? '');
          const stableSeriesId = String(cloned.id ?? cloned.name ?? `${seriesType || 'series'}-${index}`);
          cloned.id = stableSeriesId;
          if (cloned.label) {
            const nextLabel = { ...cloned.label };
            if (seriesType === 'heatmap') {
              nextLabel.formatter = (params: any) => {
                const point = Array.isArray(params?.data)
                  ? params.data
                  : (Array.isArray(params?.value) ? params.value : []);
                const raw = point.length >= 3 ? point[2] : params?.value;
                const numeric = typeof raw === "number" ? raw : Number(raw);
                if (Number.isFinite(numeric)) {
                  return Math.round(numeric).toLocaleString();
                }
                return String(raw ?? "");
              };
            } else {
              nextLabel.color = textColor;
            }
            cloned.label = nextLabel;
          }

          // Inyectar emphasis.focus para highlight/downplay visual
          cloned.emphasis = {
            ...(cloned.emphasis || {}),
            focus: interactionMode === 'filter' ? 'self' : 'series',
            blurScope: 'coordinateSystem',
            scale: seriesType === 'pie' || seriesType === 'scatter' ? true : false,
          };
          cloned.blur = {
            ...(cloned.blur || {}),
            itemStyle: {
              ...(cloned.blur?.itemStyle || {}),
              opacity: seriesType === 'line' ? 0.72 : 0.62,
            },
            lineStyle: {
              ...(cloned.blur?.lineStyle || {}),
              opacity: 0.58,
            },
            areaStyle: {
              ...(cloned.blur?.areaStyle || {}),
              opacity: 0.2,
            },
            label: {
              ...(cloned.blur?.label || {}),
              opacity: 0.62,
            },
          };
          cloned.select = {
            ...(cloned.select || {}),
            disabled: false,
            itemStyle: {
              ...(cloned.select?.itemStyle || {}),
              opacity: 1,
            },
          };

          // Pie charts: emphasis label color
          if (cloned.type === 'pie' && cloned.emphasis?.label) {
            cloned.emphasis = { ...cloned.emphasis, label: { ...cloned.emphasis.label, color: textColor } };
          }
          cloned.animationDuration = cloned.animationDuration ?? 180;
          cloned.animationDurationUpdate = cloned.animationDurationUpdate ?? (seriesType === 'treemap' ? 140 : 160);
          cloned.animationEasingUpdate = cloned.animationEasingUpdate ?? 'cubicOut';
          cloned.animationDelayUpdate = cloned.animationDelayUpdate ?? 0;
          const supportsSmoothUniversalTransition = seriesType === 'bar' || seriesType === 'line' || seriesType === 'pie' || seriesType === 'funnel';
          cloned.universalTransition = interactionMode === 'filter'
            ? false
            : (cloned.universalTransition ?? supportsSmoothUniversalTransition);
          return cloned;
        });
      }

      currentOption.stateAnimation = {
        duration: 150,
        easing: 'cubicOut',
        ...(currentOption.stateAnimation || {}),
      };
      // --- END THEME OVERRIDES ---

      // --- SANITIZATION: Remove 'valueFormatter' strings (non-destructive) ---
      const sanitizeOption = (obj: any): any => {
        if (typeof obj !== 'object' || obj === null) return obj;
        if (Array.isArray(obj)) return obj.map(item => sanitizeOption(item));
        if (typeof obj === 'function') return obj; // 🔒 Preservar funciones
        const result: any = {};
        for (const key of Object.keys(obj)) {
          if (key === 'valueFormatter') continue; // Skip forbidden key
          result[key] = sanitizeOption(obj[key]);
        }
        return result;
      };
      currentOption = sanitizeOption(currentOption);
      // --- END SANITIZATION ---
      currentOption = applyBusinessTooltip(currentOption);
      currentOption = applyBusinessPieLabels(currentOption);

      // 🚀 [PERF] Filtrado legacy ELIMINADO — reemplazado por DuckDB cross-filter
      // El sistema anterior usaba filtersAtom para filtrar DENTRO de cada gráfico,
      // causando re-renders en cascada de TODOS los charts. Ahora el filtrado
      // se hace via DuckDB-WASM y los resultados se inyectan como nuevo mensaje.

      // --- Add resilience for xAxis/yAxis ---
      const isBarOrLine = Array.isArray(currentOption.series) && currentOption.series.some((s: any) => s.type === 'bar' || s.type === 'line' || s.type === 'scatter');
      if (isBarOrLine) {
        if (!currentOption.xAxis) {
          currentOption.xAxis = { type: 'category' };
        }
        if (!currentOption.yAxis) {
          currentOption.yAxis = { type: 'value' };
        }
      }
      // --- End resilience ---

      // --- Dynamic Map Loading Logic ---
      const mapSeries = Array.isArray(currentOption.series)
        ? currentOption.series.find((s: any) => s.type === 'map' && s.map)
        : null;

      const renderChart = () => {
        requestAnimationFrame(() => {
          if (chartInstance.current) {
            const isTreemap = Array.isArray(currentOption.series) && currentOption.series.some((s: any) => s?.type === 'treemap');
            const isFilterMode = interactionMode === 'filter';
            cancelPendingFilterCallback();
            lastFilterPreviewRef.current = null;

            try {
              const seriesTypes = Array.isArray(currentOption.series)
                ? currentOption.series.map((s: any) => String(s?.type ?? ''))
                : [];
              const isNonCartesianIsolatedChart = seriesTypes.some((seriesType: string) =>
                ['pie', 'funnel', 'gauge'].includes(seriesType)
              );

              if (isTreemap || isNonCartesianIsolatedChart) {
                setOptionSafely(chartInstance.current, currentOption, {
                  notMerge: true,
                  lazyUpdate: true,
                });
              } else {
                setOptionSafely(chartInstance.current, currentOption, {
                  notMerge: false,
                  lazyUpdate: true,
                  replaceMerge: isFilterMode ? ['series'] : ['series', 'legend', 'dataset']
                });
              }
            } catch (setOptionError) {
              console.error('⚠️ [ECharts] setOption falló, aplicando fallback robusto', setOptionError);
              chartInstance.current.clear();
              chartInstance.current.setOption(currentOption, {
                notMerge: true,
                lazyUpdate: true,
              });
            }

            if (!isFilterMode) {
              chartInstance.current.resize();
            }
            fullOptionRef.current = currentOption;
            if (!isFilterMode) {
              selectedRef.current = [];
              setSelectionCount(0);
            }
          }
        });
      };

      if (mapSeries) {
        const mapName = mapSeries.map; // e.g. 'USA', 'world'
        // Try to fetch loosely (assuming file is lowercase in /public/maps/)
        const fileName = mapName.toLowerCase();

        // Optimistic check: if already registered, just render? 
        // ECharts doesn't expose easy check. We'll just fetch. Browser cache handles repeated requests efficiently.
        fetch(`/maps/${fileName}.json`)
          .then(res => {
            if (!res.ok) throw new Error(`Map file not found: ${fileName}`);
            return res.json();
          })
          .then(geoJson => {
            echarts.registerMap(mapName, geoJson);
            renderChart();
          })
          .catch(err => {
            console.warn(`Could not load map '${mapName}'. Trying 'world' fallback...`, err);
            // Fallback to world if specific map fails (and if we haven't already tried world)
            if (fileName !== 'world') {
              fetch('/maps/world.json')
                .then(r => r.json())
                .then(worldJson => {
                  // Register 'world' as the requested map name so ECharts finds it
                  echarts.registerMap(mapName, worldJson);
                  renderChart();
                })
                .catch(() => renderChart()); // Give up
            } else {
              renderChart();
            }
          });
      } else {
        renderChart();
      }

      // --- SCATTER PLOT ENHANCEMENT ---
      // Fix tooltip data formatting (prevent "251,301.53" concatenation and {c1} errors)
      const isScatter = Array.isArray(currentOption.series) && currentOption.series.some((s: any) => s.type === 'scatter');
      if (isScatter && !currentOption.tooltip?.formatter) {
        if (!currentOption.tooltip) currentOption.tooltip = {};

        // Define a client-side JS formatter to parse [x, y] data correctly
        currentOption.tooltip.formatter = (params: any) => {
          // 'params' usually comes as a single object for scatter hover
          const p = Array.isArray(params) ? params[0] : params;
          if (!p || !Array.isArray(p.value) || p.value.length < 2) return '';

          const xVal = p.value[0];
          const yVal = p.value[1];
          // FIX: Scatter data encoded as [x, y, size, name] -> Name is index 3
          // Fallback to p.name if index 3 is missing
          const nameRaw = (p.value.length > 3) ? p.value[3] : p.name;
          const dataName = nameRaw ? `<b>${nameRaw}</b><br/>` : '';
          const seriesName = p.seriesName ? `<span style="font-size:10px;color:#888">${p.seriesName}</span><br/>` : '';

          // Infer labels from axes definitions + visual_source_payload fallback
          const xAx = Array.isArray(currentOption.xAxis) ? currentOption.xAxis[0] : currentOption.xAxis;
          const yAx = Array.isArray(currentOption.yAxis) ? currentOption.yAxis[0] : currentOption.yAxis;
          const vsp = (currentOption as any)?.visual_source_payload;

          const xLabel = xAx?.name || vsp?.x_label || 'Eje X';
          const yLabel = yAx?.name || vsp?.y_label || 'Eje Y';

          // Detección Inteligente de Moneda en Frontend (Mirroring Backend Logic)
          const isCurrency = (label: string) => {
            const lower = label.toLowerCase();
            return lower.includes('venta') || lower.includes('ingraso') || lower.includes('costo') ||
              lower.includes('precio') || lower.includes('monto') || lower.includes('revenue') ||
              lower.includes('s/') || lower.includes('$');
          };

          const showCurrency = isCurrency(yLabel);

          // Format Numbers
          const xDisplay = typeof xVal === 'number' ? Math.round(xVal).toLocaleString() : xVal;
          const yFormatted = typeof yVal === 'number' ? yVal.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : yVal;
          const yDisplay = showCurrency ? `S/ ${yFormatted}` : yFormatted;

          return `
            ${seriesName}
            ${dataName}
            ${xLabel}: <b>${xDisplay}</b><br/>
            ${yLabel}: <b>${yDisplay}</b>
          `;
        };
      }
      // --- END SCATTER ENHANCEMENT ---
    }
  }, [clearFilterPreview, option, resolvedTheme, interactionMode]); // 🚀 Sin `filters` — DuckDB cross-filter reemplazó filtrado legacy

  // Resize observer
  useEffect(() => {
    const handleResize = () => {
      chartInstance.current?.resize();
    };
    window.addEventListener('resize', handleResize);

    // ResizeObserver para reaccionar a cambios del contenedor (ej: react-grid-layout)
    let resizeObserver: ResizeObserver | null = null;
    if (chartRef.current && typeof window !== 'undefined' && 'ResizeObserver' in window) {
      let previousWidth = 0;
      let previousHeight = 0;
      const observedElements = [chartRef.current, chartRef.current.parentElement].filter(Boolean) as Element[];

      resizeObserver = new ResizeObserver((entries) => {
        const sizeChanged = entries.some((entry) => {
          const nextWidth = entry.contentRect.width;
          const nextHeight = entry.contentRect.height;
          const changed = nextWidth > 0 && nextHeight > 0 && (nextWidth !== previousWidth || nextHeight !== previousHeight);
          if (changed) {
            previousWidth = nextWidth;
            previousHeight = nextHeight;
          }
          return changed;
        });

        if (sizeChanged) {
          requestAnimationFrame(() => {
            chartInstance.current?.resize();
          });
        }
      });

      observedElements.forEach((element) => resizeObserver?.observe(element));
    }

    return () => {
      window.removeEventListener('resize', handleResize);
      if (resizeObserver && chartRef.current) {
        resizeObserver.unobserve(chartRef.current);
        if (chartRef.current.parentElement) {
          resizeObserver.unobserve(chartRef.current.parentElement);
        }
        resizeObserver.disconnect();
      }
    };
  }, []);

  // Check if it's a map to show controls
  const isMap = React.useMemo(() => {
    return Array.isArray(option.series) && option.series.some((s: any) => s.type === 'map');
  }, [option]);

  const handleZoom = (direction: 'in' | 'out') => {
    if (!chartInstance.current) return;

    // Para mapas geográficos, la acción 'geoRoam' a veces requiere especificar el componente.
    // Intentamos una forma genérica que suele funcionar mejor para Zoom manual.
    chartInstance.current.dispatchAction({
      type: 'geoRoam',
      zoom: direction === 'in' ? 1.2 : 0.8,
      originX: chartInstance.current.getWidth() / 2, // Zoom al centro
      originY: chartInstance.current.getHeight() / 2,
    });
  };

  const handleRestore = () => {
    if (!chartInstance.current) return;
    selectedRef.current = [];
    setSelectionCount(0);
    if (fullOptionRef.current) {
      setOptionSafely(chartInstance.current, fullOptionRef.current, { notMerge: true, lazyUpdate: true });
    }
    clearFilterPreview();
    chartInstance.current.dispatchAction({
      type: 'restore',
    });
  };

  return (
    <div className="relative group w-full min-w-0 overflow-hidden">
      <div ref={chartRef} className="w-full min-w-0" style={style || { width: '100%', height: '400px' }} />

      {selectionCount > 0 && !isThumbnail && (
        <div className="absolute bottom-6 left-6 flex items-center gap-2 rounded-lg border bg-card/92 px-3 py-2 text-xs shadow-lg backdrop-blur-sm">
          <Info className="h-3.5 w-3.5 text-primary/80" />
          <span className="text-muted-foreground">
            {selectionCount === 1 ? "1 segmento enfocado" : `${selectionCount} segmentos enfocados`}
          </span>
          <button
            onClick={handleRestore}
            className="rounded-md px-2 py-1 font-medium text-foreground transition-colors hover:bg-muted"
            title="Restablecer foco"
          >
            Reset
          </button>
        </div>
      )}

      {/* Controles de Mapa Flotantes */}
      {isMap && (
        <div className="absolute bottom-6 right-6 flex flex-col gap-2 rounded-lg border bg-card/82 p-2 shadow-lg backdrop-blur-sm transition-all duration-200 opacity-75 group-hover:opacity-100">
          <button
            onClick={() => handleZoom('in')}
            className="p-2 hover:bg-muted rounded-md transition-colors text-foreground"
            title="Acercar (+)"
          >
            <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M5 12h14" /><path d="M12 5v14" /></svg>
          </button>
          <button
            onClick={() => handleZoom('out')}
            className="p-2 hover:bg-muted rounded-md transition-colors text-foreground"
            title="Alejar (-)"
          >
            <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M5 12h14" /></svg>
          </button>
          <div className="h-px bg-border my-1" />
          <button
            onClick={handleRestore}
            className="p-2 hover:bg-muted rounded-md transition-colors text-foreground"
            title="Restablecer Vista"
          >
            <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 12" /></svg>
          </button>
        </div>
      )}
    </div>
  );
};

// 🚀 React.memo con comparación profunda de option para evitar re-renders por referencia
export const EChartsChart = React.memo(EChartsChartInner, (prevProps, nextProps) => {
  // Si option cambió (por contenido), re-renderizar
  if (prevProps.option !== nextProps.option) {
    try {
      if (JSON.stringify(prevProps.option) !== JSON.stringify(nextProps.option)) return false;
    } catch { return false; }
  }
  // style, isThumbnail — comparación superficial
  if (prevProps.style !== nextProps.style) return false;
  if (prevProps.isThumbnail !== nextProps.isThumbnail) return false;
  if (prevProps.interactionMode !== nextProps.interactionMode) return false;
  // callbacks: NO comparar — usamos refs internamente, así que cambios no importan
  return true;
});
