import React from "react"
import { useAtomValue } from "jotai"
import { Card } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { SaveIcon } from '@/components/icons/save-icon';
import { EChartsChart } from "@/components/echarts-chart"
import { BarChart3, Table2, Download, X } from "lucide-react"
import { cn } from "@/lib/utils"
import { useKpiReactivity } from "@/components/use-kpi-reactivity"
import {
  CURRENCY_LOCALES,
  userPreferencesAtom,
} from "@/components/user-preferences"


// Interfaz flexible para los datos
type AnalysisData = any;

interface AnalysisReportProps {
  data: AnalysisData;
  onSave: () => void;
  isWidget?: boolean;
}

function KpiCard({ metric, isSingleKpi, isWidget }: { metric: { label: string; value: string }; isSingleKpi: boolean; isWidget?: boolean }) {
  const { filteredValue, isFiltered, originalValue } = useKpiReactivity({
    label: metric.label,
    originalValue: metric.value,
    isWidget,
  })

  return (
    <div
      className={cn(
        "flex flex-col justify-between transition-all duration-150",
        isWidget
          ? "items-center text-center justify-center border-0 bg-transparent shadow-none p-0 w-full"
          : "rounded-lg border border-border/50 bg-muted/20 p-3.5 shadow-xs hover:border-border hover:bg-muted/40",
        !isWidget && isSingleKpi ? "max-w-xs w-fit min-w-[200px]" : (!isWidget ? "w-full" : ""),
        isFiltered && !isWidget && "border-primary/50"
      )}
    >
      <span className={cn(
        "text-[11px] font-medium uppercase tracking-wider text-muted-foreground line-clamp-2",
        isWidget && "text-center"
      )} title={metric.label}>
        {metric.label.replace(/_/g, ' ')}
      </span>
      <span className={cn(
        "mt-1.5 font-semibold tracking-tight font-mono tabular-nums text-2xl",
        isWidget && "text-center",
        isFiltered ? "text-primary" : "text-foreground"
      )}>
        {filteredValue || metric.value}
      </span>
      {isFiltered && (
        <span className={cn(
          "mt-1 text-[10px] text-muted-foreground",
          isWidget && "text-center"
        )}>
          Total: {originalValue}
        </span>
      )}
    </div>
  )
}

const AnalysisReportComponent = ({ data, onSave, isWidget = false }: AnalysisReportProps) => {

  const [chartFilter, setChartFilter] = React.useState<string | null>(null);
  const userPreferences = useAtomValue(userPreferencesAtom);
  const numberLocale = CURRENCY_LOCALES[userPreferences.currency] ?? "es-PE";

  // --- HELPER: Motor de Localización Monetaria (Smart Formatting) ---
  const formatCurrency = (value: number) => {
    return new Intl.NumberFormat(numberLocale, {
      style: 'currency',
      currency: userPreferences.currency,
      minimumFractionDigits: 2
    }).format(value);
  };

  // --- HELPER: Función para formatear valores de forma segura y localizada ---
  const formatValue = (value: any, keyName?: string): string => {
    if (value === null || value === undefined) return "N/A";
    if (typeof value === 'number') {
      // 📅 Heurística de fechas: número de 13 dígitos + nombre de columna relacionado a fecha
      if (
        value > 1000000000000 &&
        keyName &&
        /fecha|fec|date/i.test(keyName)
      ) {
        try {
          return new Intl.DateTimeFormat('es-PE', {
            day: '2-digit',
            month: '2-digit',
            year: 'numeric'
          }).format(new Date(value));
        } catch {
          // Si falla la conversión, caer al formato numérico normal
        }
      }
      return value.toLocaleString(numberLocale, { maximumFractionDigits: 2 });
    }
    if (typeof value === 'object') {
      return value.value || value.amount || value.total || JSON.stringify(value);
    }
    return String(value);
  };

  const handleChartClick = (params: any) => {
    if (params && params.name) {
      // Toggle filter
      setChartFilter(prev => prev === params.name ? null : params.name);
    }
  };

  // Verificación simple de existencia de datos
  if (!data) {
    return (
      <Card className="p-6 mt-6">
        <p className="text-sm text-muted-foreground">Esperando datos del análisis...</p>
      </Card>
    );
  }

  // --- RENDERIZADO DE GRÁFICOS INTERACTIVOS (SI EXISTEN) ---
  // Necesitamos pasar 'onChartClick' a los componentes ECharts si estuvieran aquí.
  // Como AnalysisReportComponent actualmente solo renderiza Métricas y Tabla (según el código visto),
  // asumimos que el gráfico se renderiza en un componente padre o hermano 'ReportLayout'.
  // PERO, si 'data' contiene componentes de gráficos como objetos, deberíamos renderizarlos nosotros o 
  // la tabla filtra basada en un gráfico externo.
  // EL PEDIDO DICE: "Modifica components/echarts-chart.tsx" y "Implementar Lógica en AnalysisReport".
  // Si AnalysisReport NO renderiza el gráfico, ¿dónde está el gráfico?
  // Asumamos que el gráfico es parte de 'data' o que necesitamos renderizarlo aquí si está en data.

  // BUSQUEDA DE COMPONENTES DE GRÁFICO EN DATA
  const chartComponents = Array.isArray(data) ? data.filter((item: any) => item.type === 'chart' || item.chart_options) : [];

  return (
    <div className={cn(
      "min-w-0 overflow-hidden",
      isWidget ? "h-full w-full flex items-center justify-center p-1.5" : "mt-6 space-y-6"
    )}>

      {/* Botón de Reset Filtro (Flotante o en Cabecera) */}
      {chartFilter && (
        <div className="flex justify-end p-1.5">
          <Button
            variant="secondary"
            size="sm"
            onClick={() => setChartFilter(null)}
            className="h-7 gap-1.5 rounded-full border border-border/60 bg-muted/60 px-3 text-xs font-medium text-foreground hover:bg-muted transition-all duration-200 ease-[cubic-bezier(0.2,0,0,1)]"
          >
            <X className="h-3 w-3 text-muted-foreground" />
            <span>Filtro activo:</span>
            <span className="font-semibold text-primary font-mono">{chartFilter}</span>
          </Button>
        </div>
      )}

      {/* --- SECCIÓN 0: GRÁFICOS (Inyectados si existen en data) --- */}
      {chartComponents.length > 0 && (
        <div className="grid grid-cols-1 gap-6">
          {chartComponents.map((chartItem: any, idx: number) => {
            if (!chartItem.chart_options) return null;
            return (
              <Card key={idx} className="p-6">
                <div className="flex justify-between items-center mb-4">
                  <h3 className="text-lg font-semibold">{chartItem.title}</h3>
                  <div className="text-xs text-muted-foreground">Interactúe con el gráfico para filtrar la tabla</div>
                </div>
                <div className="h-[400px] w-full">
                  <EChartsChart
                    option={chartItem.chart_options}
                    onChartClick={handleChartClick}
                    style={{ height: '100%', width: '100%' }}
                  />
                </div>
              </Card>
            );
          })}
        </div>
      )}

      {/* --- SECCIÓN 1: MÉTRICAS CLAVE (KPIs) - LÓGICA CORREGIDA V3 --- */}
      {(() => {
        let metricsArray: { label: string; value: string }[] = [];

        // A. SI DATA ES UNA LISTA (Legacy)
        if (Array.isArray(data)) {
          data.forEach((item: any) => {
            if (item.metrics && typeof item.metrics === 'object') {
              Object.entries(item.metrics).forEach(([k, v]) =>
                metricsArray.push({ label: k, value: formatValue(v) })
              );
            }
            else if (item.label && item.value) {
              metricsArray.push({ label: String(item.label), value: formatValue(item.value) });
            }
          });
        }
        // B. SI DATA ES UN OBJETO (Nuevo Flat JSON o Legacy Object)
        else if (data.metrics) {
          if (typeof data.metrics === 'object') {
            Object.entries(data.metrics).forEach(([k, v]) =>
              metricsArray.push({ label: k, value: formatValue(v) })
            );
          }
        }
        // C. CASO ESPECIAL: Si 'data' es directamente un objeto de métricas (flat)
        else if (data && !data.tableData && !data.analysis && !data.chart_options) {
          Object.entries(data).forEach(([k, v]) => {
            if (typeof v !== 'object' && k !== 'title') {
              metricsArray.push({ label: k, value: formatValue(v) });
            }
          });
        }

        if (metricsArray.length === 0) return null;

        const isSingleKpi = metricsArray.length === 1;

        const metricsGrid = (
          <div className={isSingleKpi
            ? isWidget
              ? "flex justify-center items-center w-full py-1"
              : "flex justify-start"
            : isWidget
              ? "grid grid-cols-[repeat(auto-fit,minmax(140px,1fr))] gap-2 w-full content-center"
              : "grid grid-cols-[repeat(auto-fit,minmax(180px,1fr))] gap-3"
          }>
            {metricsArray.map((metric, idx) => (
              <KpiCard key={idx} metric={metric} isSingleKpi={isSingleKpi} isWidget={isWidget} />
            ))}
          </div>
        );

        if (isWidget) {
          return metricsGrid;
        }

        return (
          <Card className={cn(
            "p-5 rounded-xl mt-4",
            isSingleKpi ? "w-fit min-w-[240px] max-w-sm" : "w-full"
          )}>
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-2">
                <BarChart3 className="h-4 w-4 text-muted-foreground" />
                <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  Indicadores Clave
                </h3>
              </div>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7 text-muted-foreground hover:text-foreground transition-colors duration-150"
                onClick={onSave}
                title="Guardar"
              >
                <SaveIcon className="h-3.5 w-3.5" />
              </Button>
            </div>
            {metricsGrid}
          </Card>
        );
      })()}

      {/* --- SECCIÓN 2: TABLA DE DATOS DINÁMICA --- */}
      {(() => {
        // Buscamos tabla en 'tableData', 'table_data' o si 'data' mismo es un array de objetos tabla
        let foundTable: any[] = [];
        let tableTitle = "";

        if (Array.isArray(data)) {
          // Si data es lista de componentes, buscamos uno que tenga tabla
          const tableComp = data.find((i: any) => i.tableData || i.table_data);
          if (tableComp) {
            foundTable = tableComp.tableData || tableComp.table_data;
            tableTitle = tableComp.title;
          }
          // O si la lista misma son datos de tabla (heuristic: array of objects)
          else if (data.length > 0 && typeof data[0] === 'object' && !data[0].type) {
            foundTable = data;
          }
        } else {
          // Flats
          foundTable = data.tableData || data.table_data || data.data;
          tableTitle = data.title;
        }

        if (!foundTable || !Array.isArray(foundTable) || foundTable.length === 0) return null;

        // Validamos que tenga columnas
        const firstRow = foundTable[0];
        if (!firstRow || typeof firstRow !== 'object') return null;

        // --- FILTRADO INTERACTIVO (Drill-Down Logic) ---
        // Filtramos las filas si hay un chartFilter activo
        const displayTable = chartFilter
          ? foundTable.filter((row: any) => {
            // Buscamos coincidencia en cualquier valor de la fila (simple y efectivo)
            return Object.values(row).some(val => String(val) === chartFilter);
          })
          : foundTable;

        const handleExportCSV = () => {
          if (!displayTable || displayTable.length === 0) return;
          const headers = Object.keys(firstRow);
          const csvRows: string[] = [];

          const escapeCSV = (val: any) => {
            if (val === null || val === undefined) return '""';
            return `"${String(val).replace(/"/g, '""')}"`;
          };

          csvRows.push(headers.map(h => escapeCSV(h.replace(/_/g, ' '))).join(","));

          for (const row of displayTable) {
            csvRows.push(headers.map(h => escapeCSV(row[h])).join(","));
          }

          const csvString = csvRows.join("\r\n");
          const blob = new Blob(["\uFEFF" + csvString], { type: "text/csv;charset=utf-8;" });
          const url = URL.createObjectURL(blob);
          const link = document.createElement("a");
          const sanitizedTitle = (tableTitle || "reporte_analisis")
            .toLowerCase()
            .trim()
            .replace(/[^a-z0-9_-]/g, "_")
            .replace(/_+/g, "_");
          link.setAttribute("href", url);
          link.setAttribute("download", `${sanitizedTitle || "reporte"}_${Date.now()}.csv`);
          document.body.appendChild(link);
          link.click();
          document.body.removeChild(link);
          URL.revokeObjectURL(url);
        };

        return (
          <Card className="p-5 rounded-xl">
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-2 flex-wrap">
                <Table2 className="h-4 w-4 text-muted-foreground" />
                <h3 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">
                  {tableTitle || 'Detalle Analítico'}
                </h3>
                {chartFilter && (
                  <span className="text-[11px] bg-primary/10 text-primary border border-primary/20 px-2 py-0.5 rounded-full font-mono font-medium">
                    Filtrado por: {chartFilter}
                  </span>
                )}
              </div>
              <div className="flex items-center gap-2">
                <Button variant="outline" size="sm" className="h-8 text-xs gap-1.5 text-muted-foreground hover:text-foreground transition-all duration-200 ease-[cubic-bezier(0.2,0,0,1)]" onClick={handleExportCSV}>
                  <Download className="h-3.5 w-3.5" />
                  Exportar CSV
                </Button>
                <Button variant="ghost" size="icon" className="h-7 w-7 text-muted-foreground hover:text-foreground transition-all duration-200 ease-[cubic-bezier(0.2,0,0,1)]" onClick={onSave} title="Guardar">
                  <SaveIcon className="h-3.5 w-3.5" />
                </Button>
              </div>
            </div>

            <div className="overflow-x-auto border border-border/40 rounded-lg shadow-xs max-h-[400px] overflow-y-auto">
              <table className="w-full text-xs text-left">
                <thead className="bg-muted/40 text-muted-foreground sticky top-0 z-10 backdrop-blur-xs">
                  <tr className="border-b border-border/30">
                    {Object.keys(firstRow).map((key) => {
                      const isColNumeric = displayTable.some((r: any) => typeof r[key] === 'number');
                      return (
                        <th key={key} className={`py-2.5 px-3.5 font-medium uppercase tracking-wider text-[11px] whitespace-nowrap ${isColNumeric ? 'text-right' : 'text-left'}`}>
                          {key.replace(/_/g, ' ')}
                        </th>
                      );
                    })}
                  </tr>
                </thead>
                <tbody className="divide-y divide-border/30">
                  {displayTable.map((row: any, index: number) => (
                    <tr key={index} className="hover:bg-muted/30 transition-all duration-150 ease-[cubic-bezier(0.2,0,0,1)]">
                      {Object.entries(row).map(([key, value], cellIndex) => {
                        const isNumeric = typeof value === 'number';
                        return (
                          <td key={cellIndex} className={`py-2 px-3.5 whitespace-nowrap text-foreground ${isNumeric ? 'text-right font-mono tabular-nums font-medium' : 'text-left'}`}>
                            {formatValue(value, key)}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                  {displayTable.length === 0 && (
                    <tr>
                      <td colSpan={Object.keys(firstRow).length} className="py-8 text-center text-muted-foreground">
                        No hay datos para el filtro seleccionado.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </Card>
        );
      })()}
    </div>
  )
}

export const AnalysisReport = React.memo(AnalysisReportComponent);