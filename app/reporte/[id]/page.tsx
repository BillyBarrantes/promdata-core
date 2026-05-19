"use client"

import { useState, useEffect } from "react"
// --- 1. Importación de useParams ---
import { useRouter, useParams } from "next/navigation"
import { Sidebar } from "@/components/sidebar"
import { Card } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
// Eliminamos imports de Recharts
// import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip, Legend } from "recharts"
import { getReportById, deleteReport, SavedReport } from "@/lib/report-storage"
import { toast } from "sonner"
import { EChartsChart } from '@/components/echarts-chart';
import { AnalysisReport } from '@/components/analysis-report';
import { EChartsOption } from 'echarts';

export default function ReportePage() {
  const router = useRouter();
  const params = useParams<{ id: string }>();
  const reportId = Number(params.id);

  const [report, setReport] = useState<SavedReport | null>(null);
  const [chatMessage, setChatMessage] = useState("");
  const [chatMessages, setChatMessages] = useState<Array<{ type: "user" | "assistant"; content: string }>>([]);

  useEffect(() => {
    let mounted = true;

    const loadReport = async () => {
      if (!reportId) return;
      const data = await getReportById(reportId);
      if (mounted) {
        if (data) {
          setReport(data);
        } else {
          // Si falla, intentamos dashboard o mostramos error
          // toast.error("Reporte no encontrado");
          // router.push('/dashboard');
        }
      }
    };

    loadReport();

    return () => { mounted = false; };
  }, [reportId, router]);

  const handleDelete = async () => {
    if (window.confirm("¿Estás seguro de que deseas eliminar este reporte? Esta acción no se puede deshacer.")) {
      const success = await deleteReport(reportId);
      if (success) {
        toast.success("Reporte eliminado correctamente");
        router.push('/dashboard');
      } else {
        toast.error("Error al eliminar el reporte");
      }
    }
  };

  const handleSendMessage = () => {
    if (!chatMessage.trim()) return;
    setChatMessages((prev) => [
      ...prev,
      { type: "user", content: chatMessage },
      { type: "assistant", content: "Perfecto, puedo ayudarte a profundizar en este análisis o crear nuevas visualizaciones." },
    ]);
    setChatMessage("");
  };

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage();
    }
  };

  if (!report) {
    return (
      <div className="flex h-screen w-full items-center justify-center">
        <p>Cargando reporte...</p>
      </div>
    );
  }

  // Transformar datos de Recharts (si los hubiera) a ECharts para retrocompatibilidad/visualización
  // Transformar datos de Recharts (si los hubiera) a ECharts para retrocompatibilidad/visualización

  let chartOption: EChartsOption | null = null;
  let analysisData: any = null; // Para métricas o tablas

  // 1. Desempaquetar contenido (Fix para el bug de "códigos")
  let contentType = report.type;
  let contentBody = report.content;

  // Si el contenido viene empaquetado como { type: "...", content: ... }
  if (contentBody && typeof contentBody === 'object' && contentBody.type && contentBody.content) {
    contentType = contentBody.type;
    contentBody = contentBody.content;
  }

  // 2. Determinar qué renderizar según el tipo
  if (contentType === 'chart') {
    chartOption = contentBody;
  }
  else if (contentType === 'metrics') {
    analysisData = { metrics: contentBody, tableData: [] };
  }
  else if (contentType === 'table') {
    analysisData = { tableData: contentBody.data, title: contentBody.title, metrics: {} };
  }
  // 3. Fallback Legacy (Pie Chart antiguo)
  else if (contentType === 'pie' && report.data && report.data.sections?.[0]?.chartData) {
    const pieData = report.data.sections[0].chartData.map((item: any) => ({
      name: item.name,
      value: item.value,
      itemStyle: { color: item.color }
    }));

    chartOption = {
      tooltip: { trigger: 'item', formatter: '{b}: {c}%' },
      legend: { bottom: '5%', left: 'center' },
      series: [{
        name: 'Participación', type: 'pie', radius: ['40%', '70%'],
        avoidLabelOverlap: false,
        itemStyle: { borderRadius: 10, borderColor: '#fff', borderWidth: 2 },
        label: { show: false, position: 'center' },
        emphasis: { label: { show: true, fontSize: 20, fontWeight: 'bold' } },
        data: pieData
      }]
    };
  }

  return (
    <div className="flex h-screen bg-background">
      <Sidebar />
      <main className="flex-1 flex">
        {/* Main Content */}
        <div className="flex-1 flex flex-col">
          <header className="border-b border-border px-6 py-4">
            <div className="flex items-center justify-between">
              <h1 className="text-lg font-semibold text-foreground">Dashboard de Ventas Q3</h1>
              <div className="flex items-center gap-2">
                <div className="w-8 h-8 bg-blue-600 rounded-full flex items-center justify-center text-white text-sm font-medium">
                  LB
                </div>
              </div>
            </div>
          </header>

          <div className="flex-1 p-6 overflow-auto">
            <div className="max-w-4xl mx-auto">
              <div className="mb-6">
                <div className="flex justify-between items-center mb-1">
                  <h2 className="text-xl font-semibold text-foreground">{report.title}</h2>
                  <Button
                    variant="outline"
                    size="sm"
                    className="text-red-600 border-red-600 hover:bg-red-50 hover:text-red-700"
                    onClick={handleDelete}
                  >
                    Eliminar
                  </Button>
                </div>
                {/* @ts-ignore */}
                <p className="text-sm text-muted-foreground">{report.description}</p>
              </div>

              {chartOption && (
                <Card className="p-6 mb-6">
                  <div className="bg-muted/30 rounded-lg p-6">
                    <EChartsChart option={chartOption} />
                  </div>
                </Card>
              )}

              {analysisData && (
                <div className="mb-6">
                  <AnalysisReport data={analysisData} onSave={() => { }} />
                </div>
              )}

              {/* @ts-ignore */}
              {report.data && report.data.recommendations && (
                <Card className="p-6">
                  <h3 className="text-lg font-semibold text-foreground mb-4">
                    ✓ Recomendaciones Estratégicas Basadas en Datos:
                  </h3>
                  {/* @ts-ignore */}
                  <div className="space-y-3">
                    {report.data.recommendations.map((rec: any, index: number) => (
                      <div key={index} className="flex items-start gap-3">
                        <div className="w-6 h-6 bg-blue-100 rounded-full flex items-center justify-center flex-shrink-0 mt-0.5">
                          <span className="text-blue-600 text-xs font-medium">{index + 1}</span>
                        </div>
                        <div>
                          <h4 className="font-medium text-foreground">{rec.title}</h4>
                          <p className="text-sm text-muted-foreground">{rec.description}</p>
                        </div>
                      </div>
                    ))}
                  </div>
                </Card>
              )}
            </div>
          </div>
        </div>

        {/* Chat Sidebar */}
        <div className="w-80 border-l border-border flex flex-col bg-muted/20">
          <div className="p-4 border-b border-border">
            <h3 className="font-semibold text-foreground">Chat de Análisis</h3>
            <p className="text-xs text-muted-foreground">Continúa configurando tu dashboard</p>
          </div>
          <div className="flex-1 p-4 overflow-auto">
            {chatMessages.length === 0 ? (
              <div className="text-center text-sm text-muted-foreground mt-8">
                <p>Inicia una conversación para profundizar en este análisis.</p>
              </div>
            ) : (
              <div className="space-y-3">
                {chatMessages.map((msg, index) => (
                  <div key={index} className={`p-3 rounded-lg text-sm ${msg.type === "user" ? "bg-primary text-primary-foreground ml-4" : "bg-background border mr-4"}`}>
                    {msg.content}
                  </div>
                ))}
              </div>
            )}
          </div>
          <div className="p-4 border-t border-border">
            <div className="space-y-3">
              <Textarea
                placeholder="Pregunta sobre este análisis..."
                value={chatMessage}
                onChange={(e) => setChatMessage(e.target.value)}
                onKeyDown={handleKeyPress}
                className="min-h-[80px] resize-none"
              />
              <div className="flex gap-2">
                <Button size="sm" onClick={handleSendMessage} className="flex-1">
                  Enviar
                </Button>
              </div>
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}