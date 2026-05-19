import React from "react"
import { Card } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { SaveIcon } from '@/components/icons/save-icon';
import { EChartsChart } from "@/components/echarts-chart"


// Interfaz flexible para los datos
type AnalysisData = any;

interface AnalysisReportProps {
  data: AnalysisData;
  onSave: () => void;
}

const AnalysisReportComponent = ({ data, onSave }: AnalysisReportProps) => {

  const [chartFilter, setChartFilter] = React.useState<string | null>(null);

  // --- HELPER: Motor de Localización Monetaria (Smart Formatting) ---
  const formatCurrency = (value: number) => {
    // Intentamos detectar si 'data' tiene metadata de moneda (si viene del backend)
    // Pero como 'data' es flexible, asumimos PEN por defecto o buscamos 'currency_code' si existiera en un futuro standar
    const currency = 'PEN'; // Por defecto Perú
    return new Intl.NumberFormat('es-PE', {
      style: 'currency',
      currency: currency,
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
      return value.toLocaleString('es-PE', { maximumFractionDigits: 2 });
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
    <div className="mt-6 space-y-6 min-w-0 overflow-hidden">

      {/* Botón de Reset Filtro (Flotante o en Cabecera) */}
      {chartFilter && (
        <div className="flex justify-end p-2 bg-muted/20 rounded-lg border border-dashed border-primary/30">
          <Button variant="secondary" size="sm" onClick={() => setChartFilter(null)} className="animate-in fade-in zoom-in">
            🚫 Quitar Filtro: <span className="font-bold ml-1 text-primary">{chartFilter}</span>
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
          // Asumimos que es un objeto de métricas K:V si no tiene structura conocida
          Object.entries(data).forEach(([k, v]) => {
            if (typeof v !== 'object' && k !== 'title') {
              metricsArray.push({ label: k, value: formatValue(v) });
            }
          });
        }

        if (metricsArray.length === 0) return null;

        return (
          <Card className="p-6 relative !mt-4">
            <div className="absolute top-4 right-4 flex items-center gap-2">
              <Button variant="ghost" size="icon" className="h-8 w-8" onClick={onSave} title="Guardar">
                <SaveIcon className="h-4 w-4" />
              </Button>
            </div>
            <h3 className="text-lg font-semibold text-foreground mb-4 flex items-center gap-2 pr-20">
              📊 Métricas Clave
            </h3>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 text-sm">
              {metricsArray.map((metric, idx) => (
                <div key={idx} className="flex flex-col space-y-1 p-4 bg-muted/40 rounded-lg border hover:bg-muted/60 transition-colors">
                  <span className="text-muted-foreground capitalize text-xs font-medium truncate" title={metric.label}>
                    {metric.label.replace(/_/g, ' ')}
                  </span>
                  <strong className="text-foreground text-xl font-bold tracking-tight text-primary break-words whitespace-normal leading-tight" title={metric.value}>
                    {metric.value}
                  </strong>
                </div>
              ))}
            </div>
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

        return (
          <Card className="p-6">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-semibold text-foreground flex items-center gap-2">
                {tableTitle ? `📋 ${tableTitle}` : '📋 Tabla de Datos'}
                {chartFilter && <span className="text-xs bg-primary/10 text-primary px-2 py-1 rounded ml-2">Filtrado por: {chartFilter}</span>}
              </h3>
              <div className="flex items-center gap-2">
                <Button variant="outline" size="sm" className="bg-transparent h-8 text-xs">
                  Exportar
                </Button>
                <Button variant="ghost" size="icon" className="h-8 w-8" onClick={onSave} title="Guardar">
                  <SaveIcon className="h-4 w-4" />
                </Button>
              </div>
            </div>

            <div className="overflow-x-auto border rounded-lg shadow-sm max-h-[400px] overflow-y-auto">
              <table className="w-full text-sm text-left">
                <thead className="bg-muted/50 text-muted-foreground">
                  <tr className="border-b border-border">
                    {Object.keys(firstRow).map((key) => (
                      <th key={key} className="py-3 px-4 font-medium capitalize whitespace-nowrap">
                        {key.replace(/_/g, ' ')}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {displayTable.map((row: any, index: number) => (
                    <tr key={index} className="hover:bg-muted/50 transition-colors">
                      {Object.entries(row).map(([key, value], cellIndex) => (
                        <td key={cellIndex} className="py-3 px-4 whitespace-nowrap">
                          {formatValue(value, key)}
                        </td>
                      ))}
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