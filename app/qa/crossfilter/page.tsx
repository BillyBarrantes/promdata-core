"use client";

import React, { useMemo, useState } from "react";
import { ChartsReport } from "@/components/charts-report";
import { DrillDownMenu } from "@/components/drill-down-menu";
import { EChartsOption } from "echarts";

type SampleRow = {
  product: string;
  stock: number;
  risk: number;
  trend: number;
};

const BASE_ROWS: SampleRow[] = [
  { product: "Alpha", stock: 120, risk: 45, trend: 10 },
  { product: "Beta", stock: 90, risk: 32, trend: 24 },
  { product: "Gamma", stock: 70, risk: 20, trend: 16 },
  { product: "Delta", stock: 55, risk: 12, trend: 8 },
  { product: "Epsilon", stock: 40, risk: 8, trend: 5 },
];

const normalizeValue = (value: string): string =>
  value.replace(/\0/g, "").normalize("NFC").replace(/\s+/g, " ").trim();

const extractRawChartCategory = (params: any): string | null => {
  const candidates = [
    params?.rawCategory,
    params?.data?.raw_name,
    params?.data?.rawName,
    params?.data?.full_name,
    params?.data?.fullName,
    params?.data?.name,
    params?.name,
    params?.axisValue,
    params?.axisValueLabel,
    params?.data?.value?.[3],
    params?.value?.[3],
  ];

  for (const candidate of candidates) {
    if (typeof candidate === "string" && candidate.trim()) {
      return normalizeValue(candidate);
    }
  }

  return null;
};

const toNumber = (value: unknown): number => {
  if (typeof value === "number") return Number.isFinite(value) ? value : 0;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
};

const makeTreemapOption = (rows: SampleRow[]): EChartsOption => ({
  title: { text: "Treemap QA", left: "left" },
  tooltip: { trigger: "item" },
  series: [
    {
      type: "treemap",
      roam: false,
      nodeClick: false,
      breadcrumb: { show: false },
      label: { show: true },
      data: rows.map((row) => ({
        name: row.product,
        value: row.stock,
        raw_name: row.product,
      })),
    },
  ],
});

const makeBarOption = (rows: SampleRow[]): EChartsOption => ({
  title: { text: "Bar QA", left: "left" },
  tooltip: { trigger: "axis" },
  xAxis: {
    type: "category",
    data: rows.map((row) => row.product),
    axisLabel: { interval: 0 },
  },
  yAxis: { type: "value" },
  series: [
    {
      name: "Risk",
      type: "bar",
      barWidth: "55%",
      label: { show: true, position: "top" },
      data: rows.map((row) => ({ value: row.risk, raw_name: row.product })),
    },
  ],
});

const makeLineOption = (rows: SampleRow[]): EChartsOption => ({
  title: { text: "Line QA", left: "left" },
  tooltip: { trigger: "axis" },
  xAxis: {
    type: "category",
    data: rows.map((row) => row.product),
    axisLabel: { interval: 0 },
  },
  yAxis: { type: "value" },
  series: [
    {
      name: "Trend",
      type: "line",
      smooth: true,
      symbol: "circle",
      symbolSize: 14,
      areaStyle: { opacity: 0.15 },
      data: rows.map((row) => ({ value: row.trend, raw_name: row.product })),
    },
  ],
});

export default function CrossFilterQAPage() {
  const [activeFilter, setActiveFilter] = useState<string | null>(null);
  const [drillDown, setDrillDown] = useState({
    isVisible: false,
    position: { x: 0, y: 0 },
    dataContext: {
      category: "",
      value: 0 as number | string,
      series: "",
      tableName: "qa_crossfilter",
    },
  });

  const rowsForView = useMemo(() => {
    if (!activeFilter) return BASE_ROWS;
    return BASE_ROWS.map((row) =>
      row.product === activeFilter
        ? row
        : { ...row, stock: 0, risk: 0, trend: 0 }
    );
  }, [activeFilter]);

  const treemapOption = useMemo(() => makeTreemapOption(rowsForView), [rowsForView]);
  const barOption = useMemo(() => makeBarOption(rowsForView), [rowsForView]);
  const lineOption = useMemo(() => makeLineOption(rowsForView), [rowsForView]);

  const handleOpenDrillDown = (params: any, seriesName: string) => {
    const category = extractRawChartCategory(params);
    if (!category) return;

    const rawValue = Array.isArray(params?.value)
      ? params.value[1] ?? params.value[0]
      : params?.value;

    const normalizedCategory = normalizeValue(category);

    setDrillDown({
      isVisible: true,
      position: {
        x: params?.eventCoordinates?.x ?? Math.max(220, window.innerWidth / 2),
        y: params?.eventCoordinates?.y ?? 180,
      },
      dataContext: {
        category: normalizedCategory,
        value: toNumber(rawValue),
        series: seriesName,
        tableName: "qa_crossfilter",
      },
    });
  };

  const openDrillDownFromQA = (category: string, seriesName: string, value: number) => {
    const normalizedCategory = normalizeValue(category);
    setDrillDown({
      isVisible: true,
      position: { x: Math.max(260, window.innerWidth * 0.65), y: 220 },
      dataContext: {
        category: normalizedCategory,
        value,
        series: seriesName,
        tableName: "qa_crossfilter",
      },
    });
  };

  const handleCrossFilter = (filters: Record<string, string>) => {
    const selected = filters.category ? normalizeValue(filters.category) : "";
    if (!selected) return;
    setActiveFilter((prev) => (prev === selected ? null : selected));
  };

  return (
    <main className="min-h-screen bg-background p-6 md:p-8">
      <div className="max-w-7xl mx-auto space-y-6">
        <section className="rounded-xl border p-4 bg-card">
          <h1 className="text-xl font-semibold">QA Cross-Filter Lab</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Flujo validado: click -&gt; Filtrar aqui -&gt; hover -&gt; clear.
          </p>
          <div className="mt-3 flex flex-wrap gap-3 text-sm">
            <div>
              Filtro activo:{" "}
              <span data-testid="active-filter" className="font-semibold">
                {activeFilter || "none"}
              </span>
            </div>
            <div>
              Ultimo foco:{" "}
              <span data-testid="last-click-category" className="font-semibold">
                {drillDown.dataContext.category || "none"}
              </span>
            </div>
            <button
              type="button"
              data-testid="clear-filter"
              className="px-3 py-1 rounded border text-xs"
              onClick={() => setActiveFilter(null)}
            >
              Limpiar filtro
            </button>
          </div>
        </section>

        <section data-testid="chart-treemap" className="rounded-xl border bg-card p-3">
          <ChartsReport
            title="Treemap QA"
            option={treemapOption}
            onSave={() => {}}
            interactionMode="filter"
            onChartClick={(params) => handleOpenDrillDown(params, "treemap")}
          />
        </section>

        <section data-testid="chart-bar" className="rounded-xl border bg-card p-3">
          <div className="flex gap-2 mb-2">
            <button
              type="button"
              data-testid="trigger-bar-alpha"
              className="px-2 py-1 text-xs rounded border"
              onClick={() => openDrillDownFromQA("Alpha", "bar", rowsForView[0]?.risk ?? 0)}
            >
              Trigger Bar Alpha
            </button>
          </div>
          <ChartsReport
            title="Bar QA"
            option={barOption}
            onSave={() => {}}
            interactionMode="filter"
            onChartClick={(params) => handleOpenDrillDown(params, "bar")}
          />
        </section>

        <section data-testid="chart-line" className="rounded-xl border bg-card p-3">
          <div className="flex gap-2 mb-2">
            <button
              type="button"
              data-testid="trigger-line-beta"
              className="px-2 py-1 text-xs rounded border"
              onClick={() => openDrillDownFromQA("Beta", "line", rowsForView[1]?.trend ?? 0)}
            >
              Trigger Line Beta
            </button>
          </div>
          <ChartsReport
            title="Line QA"
            option={lineOption}
            onSave={() => {}}
            interactionMode="filter"
            onChartClick={(params) => handleOpenDrillDown(params, "line")}
          />
        </section>
      </div>

      <div data-testid="qa-drilldown-menu">
        <DrillDownMenu
          isVisible={drillDown.isVisible}
          position={drillDown.position}
          dataContext={drillDown.dataContext}
          onSelect={() => {}}
          onClose={() => setDrillDown((prev) => ({ ...prev, isVisible: false }))}
          onCrossFilter={handleCrossFilter}
          isDuckDBReady={true}
        />
      </div>
    </main>
  );
}
