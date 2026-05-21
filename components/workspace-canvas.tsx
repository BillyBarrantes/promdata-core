// components/workspace-canvas.tsx
"use client"

import { useState, useCallback } from "react"
import { useRouter } from "next/navigation"
import { LayoutDashboard, LoaderCircle, MousePointerClick, Sparkles, X } from "lucide-react"
import { useAtomValue, useSetAtom } from "jotai"
import { useSearchParams } from "next/navigation"
import { workspaceItemsAtom, workspaceRenderStateAtom, drillDownAtom, AnalysisComponent, activePresentationIdAtom, presentationsListAtom } from "@/lib/state"
import { ChartsReport } from "@/components/charts-report"
import { SmartTable } from "@/components/smart-table"
import { AnalysisReport } from "@/components/analysis-report"
import { useSupabase } from "@/lib/supabase-provider"
import { API_BASE_URL } from "@/lib/api-config"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"

const WorkspaceLoadingCard = ({ compact = false }: { compact?: boolean }) => (
  <div className={`rounded-[28px] border border-border/60 bg-card/70 p-6 shadow-sm backdrop-blur-sm ${compact ? "min-h-[220px]" : "min-h-[320px]"} animate-pulse`}>
    <div className="mb-5 flex items-center justify-between gap-4">
      <div className="h-7 w-56 rounded-md bg-muted/60" />
      <div className="h-8 w-28 rounded-full bg-muted/50" />
    </div>
    <div className="space-y-3">
      <div className="h-4 w-40 rounded-md bg-muted/50" />
      <div className="h-48 rounded-2xl bg-muted/35" />
      <div className="grid grid-cols-3 gap-3">
        <div className="h-10 rounded-xl bg-muted/35" />
        <div className="h-10 rounded-xl bg-muted/30" />
        <div className="h-10 rounded-xl bg-muted/25" />
      </div>
    </div>
  </div>
)

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
      return candidate.replace(/\0/g, "").normalize('NFC').replace(/\s+/g, ' ').trim();
    }
  }

  return null;
};

export function WorkspaceCanvas() {
  const items = useAtomValue(workspaceItemsAtom);
  const workspaceRenderState = useAtomValue(workspaceRenderStateAtom);
  const presentations = useAtomValue(presentationsListAtom);
  const setDrillDown = useSetAtom(drillDownAtom);
  const searchParams = useSearchParams();
  const router = useRouter();
  const fileId = searchParams.get('fileId');
  const supabase = useSupabase();
  const setActivePresentationId = useSetAtom(activePresentationIdAtom);
  const setPresentations = useSetAtom(presentationsListAtom);

  // Estados para el modal de guardar reporte
  const [isSaveDialogOpen, setIsSaveDialogOpen] = useState(false);
  const [reportTitle, setReportTitle] = useState("");
  const [reportToSave, setReportToSave] = useState<any>(null);
  const [selectedSavePresentationId, setSelectedSavePresentationId] = useState("");
  const [isSaveActionLoading, setIsSaveActionLoading] = useState(false);
  const [isSaveDestinationsLoading, setIsSaveDestinationsLoading] = useState(false);

  const getWorkspaceAccessToken = useCallback(async (): Promise<string | null> => {
    const { data: { session } } = await supabase.auth.getSession();
    if (session?.access_token) return session.access_token;

    if (typeof window !== 'undefined' && process.env.NODE_ENV !== 'production') {
      const params = new URLSearchParams(window.location.search);
      if (params.get('__qa_chat') === '1') {
        return params.get('__qa_chat_token') || 'qa-chat-token';
      }
    }

    return null;
  }, [supabase]);

  const refreshSaveDestinations = useCallback(async () => {
    setIsSaveDestinationsLoading(true);
    try {
      const accessToken = await getWorkspaceAccessToken();
      if (!accessToken) return;

      const response = await fetch(`${API_BASE_URL}/api/v1/presentations?_t=${Date.now()}`, {
        headers: {
          'Authorization': `Bearer ${accessToken}`,
          'Cache-Control': 'no-cache, no-store, must-revalidate'
        },
        cache: 'no-store'
      });

      if (!response.ok) return;
      const payload = await response.json().catch(() => []);
      setPresentations(Array.isArray(payload) ? payload : []);
    } catch (error) {
      console.error("Error cargando presentaciones para guardado:", error);
    } finally {
      setIsSaveDestinationsLoading(false);
    }
  }, [getWorkspaceAccessToken, setPresentations]);

  const handleOpenSaveDialog = (data: any, type: string, defaultTitle?: string) => {
    setReportToSave({ type, content: data });
    setReportTitle(defaultTitle || ""); 
    setSelectedSavePresentationId("");
    setIsSaveDialogOpen(true);
    void refreshSaveDestinations();
  };

  const handleConfirmSave = useCallback(async () => {
    if (!reportTitle.trim()) {
      toast.error("Por favor ingresa un título para el reporte.");
      return;
    }

    if (!fileId) {
      toast.error("No hay un archivo activo para asociar el reporte.");
      return;
    }

    setIsSaveActionLoading(true);
    try {
      const accessToken = await getWorkspaceAccessToken();
      if (!accessToken) return;

      const destinationPresentationId = selectedSavePresentationId.trim();
      const response = await fetch(`${API_BASE_URL}/api/v1/reports`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${accessToken}`
        },
        body: JSON.stringify({
          title: reportTitle,
          content: reportToSave,
          file_id: fileId,
          ...(destinationPresentationId ? { presentation_id: destinationPresentationId } : {}),
        })
      });

      if (!response.ok) {
        throw new Error(`Error al guardar reporte (${response.status})`);
      }

      const reportPayload = await response.json().catch(() => ({}));
      const savedPresentation = reportPayload?.presentation;
      const resolvedPresentationId =
        destinationPresentationId
        || (typeof reportPayload?.presentation_id === "string" ? reportPayload.presentation_id : "")
        || (typeof savedPresentation?.id === "string" ? savedPresentation.id : "");

      if (savedPresentation?.id) {
        const currentPresentations = Array.isArray(presentations) ? presentations : [];
        const nextPresentations = [...currentPresentations];
        const existingIndex = nextPresentations.findIndex((presentation: any) => presentation?.id === savedPresentation.id);

        if (existingIndex >= 0) {
          nextPresentations[existingIndex] = {
            ...nextPresentations[existingIndex],
            ...savedPresentation,
          };
        } else {
          nextPresentations.unshift(savedPresentation);
        }

        setPresentations(nextPresentations);
      }

      if (resolvedPresentationId) {
        setActivePresentationId(resolvedPresentationId);
      }

      const knownPresentations = Array.isArray(presentations) ? presentations : [];
      const selectedPresentation = knownPresentations.find((presentation: any) => presentation?.id === resolvedPresentationId);
      const resolvedPresentationName =
        (typeof savedPresentation?.name === "string" && savedPresentation.name.trim())
        || (typeof selectedPresentation?.name === "string" && selectedPresentation.name.trim())
        || null;

      const successMessage = destinationPresentationId
        ? (resolvedPresentationName
            ? `Reporte guardado en "${resolvedPresentationName}".`
            : "Reporte guardado en la presentación seleccionada.")
        : "Reporte guardado en la presentación automática del archivo.";

      toast.success(successMessage, {
        action: resolvedPresentationId
          ? {
              label: "Ir a la presentación",
              onClick: () => router.push(`/dashboard?presentationId=${encodeURIComponent(resolvedPresentationId)}`),
            }
          : undefined,
      });

      setSelectedSavePresentationId("");
      setIsSaveDialogOpen(false);
    } catch (error) {
      toast.error("No se pudo guardar el reporte.");
      console.error(error);
    } finally {
      setIsSaveActionLoading(false);
    }
  }, [
    fileId,
    getWorkspaceAccessToken,
    presentations,
    reportTitle,
    reportToSave,
    router,
    selectedSavePresentationId,
    setActivePresentationId,
    setPresentations,
  ]);

  const handleChartDrillDown = useCallback((params: any, tableName?: string) => {
    const rawCategory = extractRawChartCategory(params);
    if (rawCategory) {
      const rawSecondaryCategory = typeof params?.rawSecondaryCategory === 'string'
        ? params.rawSecondaryCategory.replace(/\0/g, '').normalize('NFC').replace(/\s+/g, ' ').trim()
        : null;
      const seriesName = rawSecondaryCategory || params.seriesName || 'valor';
      const category = rawCategory;
      const heatmapPoint = Array.isArray(params?.data)
        ? params.data
        : (Array.isArray(params?.value) ? params.value : null);
      const value = Array.isArray(heatmapPoint) && heatmapPoint.length >= 3
        ? heatmapPoint[2]
        : params.value;

      const x = params.eventCoordinates?.x || 0;
      const y = params.eventCoordinates?.y || 0;

      const safeCategory = String(category).replace(/\0/g, '');
      const safeSeries = String(seriesName).replace(/\0/g, '');

      setDrillDown({
        isVisible: true,
        position: { x, y },
        dataContext: {
          category: safeCategory,
          value: value,
          series: safeSeries,
          tableName: tableName,
          secondaryCategory: rawSecondaryCategory || undefined,
        }
      });
    }
  }, [setDrillDown]);

  const isWorkspaceBusy = workspaceRenderState.status === "analyzing" || workspaceRenderState.status === "staging";

  // Si no hay items en el workspace, mostramos el placeholder
  if ((!items || items.length === 0) && !isWorkspaceBusy) {
    return (
      <div className="h-full w-full flex items-center justify-center bg-muted/10 relative overflow-hidden">
        {/* Radial glow */}
        <div
          className="absolute inset-0"
          style={{ background: 'radial-gradient(circle at center, rgba(59,130,246,0.04) 0%, transparent 60%)' }}
        />
        {/* Center content */}
        <div className="relative z-10 flex flex-col items-center gap-6 max-w-md px-8">
          {/* Icon cluster */}
          <div className="relative">
            <div className="w-20 h-20 rounded-2xl bg-gradient-to-br from-blue-500/10 to-purple-500/10 border border-blue-500/20 dark:border-blue-400/20 flex items-center justify-center backdrop-blur-sm">
              <LayoutDashboard className="w-9 h-9 text-blue-500/60 dark:text-blue-400/50" />
            </div>
            <div className="absolute -top-1 -right-1 w-6 h-6 rounded-full bg-gradient-to-br from-amber-400 to-orange-500 flex items-center justify-center shadow-lg shadow-amber-500/20">
              <Sparkles className="w-3.5 h-3.5 text-white" />
            </div>
          </div>
          {/* Text */}
          <div className="text-center space-y-2">
            <h2 className="text-xl font-semibold text-foreground/80 tracking-tight">
              Centro de Comando
            </h2>
            <p className="text-sm text-muted-foreground leading-relaxed">
              Aquí podrás anclar gráficos, arrastrar visualizaciones y construir dashboards
              personalizados desde el chat.
            </p>
          </div>
          {/* Hint */}
          <div className="flex items-center gap-2 text-xs text-muted-foreground/60 bg-muted/50 px-4 py-2 rounded-full border border-border/50">
            <MousePointerClick className="w-3.5 h-3.5" />
            <span>Usa el chat para generar tu primer análisis</span>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="h-full w-full p-6 overflow-y-auto bg-muted/5">
      <div className="max-w-6xl mx-auto space-y-6">
        {isWorkspaceBusy && (
          <div className="sticky top-0 z-10 rounded-[24px] border border-border/60 bg-background/92 px-5 py-4 shadow-sm backdrop-blur-sm">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
              <div className="flex items-center gap-3">
                <div className="flex h-10 w-10 items-center justify-center rounded-2xl bg-primary/10 text-primary">
                  <LoaderCircle className="h-5 w-5 animate-spin" />
                </div>
                <div>
                  <div className="text-sm font-semibold text-foreground">
                    {workspaceRenderState.status === "analyzing" ? "Preparando análisis visual" : "Render progresivo en curso"}
                  </div>
                  <div className="text-xs text-muted-foreground">
                    {workspaceRenderState.message || "Priorizando el visual principal para mantener fluidez real."}
                  </div>
                </div>
              </div>
              <div className="text-xs text-muted-foreground">
                {workspaceRenderState.pendingVisuals > 0
                  ? `${workspaceRenderState.renderedVisuals}/${workspaceRenderState.pendingVisuals} bloques listos`
                  : "Esperando resultado del motor analítico"}
              </div>
            </div>
          </div>
        )}

        {(!items || items.length === 0) && isWorkspaceBusy && (
          <>
            <WorkspaceLoadingCard />
            <WorkspaceLoadingCard compact />
          </>
        )}

        {items.map((component, index) => {
          const componentKey = `canvas-item-${index}`;
          
          switch(component.type) {
            case 'metricas_clave':
              if (component.data && Object.keys(component.data).length > 0) {
                return (
                  <div key={componentKey} className="w-full">
                    <AnalysisReport
                      data={{ metrics: component.data, tableData: [] }}
                      onSave={() => handleOpenSaveDialog(component.data, "metrics", component.title || "Métricas Clave")}
                    />
                  </div>
                );
              }
              return null;

            case 'configuracion_echarts':
              if (component.option) {
                return (
                  <ChartsReport 
                    key={componentKey} 
                    option={component.option} 
                    title={component.title} 
                    onSave={(optionOverride) => handleOpenSaveDialog(optionOverride || component.option, "chart", component.title)} 
                    onChartClick={(params) => handleChartDrillDown(params, component.table_name)} 
                  />
                );
              }
              return null;
              
            case 'smart_table':
              if (component.columns && component.data) {
                return (
                  <SmartTable
                    key={componentKey}
                    title={component.title}
                    columns={component.columns}
                    data={component.data}
                    sortBy={component.sort_by}
                    sortOrder={component.sort_order}
                    originalChartOption={component.original_chart_option}
                    defaultViewMode={component.default_view_mode}
                    onSave={() => handleOpenSaveDialog(component, "table", component.title)}
                    onChartClick={(params) => handleChartDrillDown(params, component.table_name)}
                  />
                );
              }
              return null;
              
            case 'tabla_datos':
               if (component.data && Array.isArray(component.data) && component.data.length > 0) {
                 return (
                   <div key={componentKey} className="w-full">
                     <AnalysisReport 
                       data={{ tableData: component.data, title: component.title, metrics: {} }} 
                       onSave={() => handleOpenSaveDialog({ data: component.data, title: component.title }, "table", component.title)} 
                     />
                   </div>
                 );
               }
               return null;

            default:
              return null;
          }
        })}
        {isWorkspaceBusy && items.length > 0 && workspaceRenderState.pendingVisuals > workspaceRenderState.renderedVisuals && (
          <WorkspaceLoadingCard compact />
        )}
      </div>

      {/* Modal de Guardado */}
      {isSaveDialogOpen && reportToSave && (
        <div className="fixed inset-0 bg-background/80 backdrop-blur-sm z-50 flex items-center justify-center p-4" data-testid="save-report-dialog">
          <div className="bg-card border shadow-xl rounded-xl max-w-md w-full p-6 space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="font-semibold text-lg">Guardar Reporte</h3>
              <button onClick={() => setIsSaveDialogOpen(false)} className="p-1 hover:bg-muted rounded-full" disabled={isSaveActionLoading}>
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">Título del Reporte</label>
              <input
                type="text"
                value={reportTitle}
                onChange={(e) => setReportTitle(e.target.value)}
                placeholder="Ej: Análisis de Ventas Q3"
                className="w-full px-3 py-2 rounded-md border text-sm outline-none focus:ring-2 ring-primary/30 bg-background"
                autoFocus
                data-testid="save-report-title-input"
                disabled={isSaveActionLoading}
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">Presentación destino (Opcional)</label>
              <select
                value={selectedSavePresentationId}
                onChange={(event) => setSelectedSavePresentationId(event.target.value)}
                className="w-full px-3 py-2 rounded-md border text-sm outline-none focus:ring-2 ring-primary/30 bg-background"
                disabled={isSaveActionLoading || isSaveDestinationsLoading}
              >
                <option value="">Automático (por archivo)</option>
                {(Array.isArray(presentations) ? presentations : []).map((presentation: any) => (
                  <option key={presentation.id} value={presentation.id}>
                    {presentation.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="outline" onClick={() => setIsSaveDialogOpen(false)} disabled={isSaveActionLoading}>Cancelar</Button>
              <Button onClick={handleConfirmSave} data-testid="save-report-confirm" disabled={isSaveActionLoading}>
                {isSaveActionLoading ? "Guardando..." : "Guardar Reporte"}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
