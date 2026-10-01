// components/workspace-canvas.tsx
"use client"

import { useState, useCallback } from "react"
import { useRouter } from "next/navigation"
import { LayoutDashboard, LoaderCircle, MousePointerClick, X, Database } from "lucide-react"
import { useAtomValue, useSetAtom } from "jotai"
import { useSearchParams } from "next/navigation"
import { workspaceItemsAtom, workspaceRenderStateAtom, drillDownAtom, AnalysisComponent, activePresentationIdAtom, presentationsListAtom } from "@/lib/state"
import { ChartsReport } from "@/components/charts-report"
import { SmartTable } from "@/components/smart-table"
import { AnalysisReport } from "@/components/analysis-report"
import { useSupabase } from "@/lib/supabase-provider"
import { API_BASE_URL } from "@/lib/api-config"
import { fetchPresentationsShared } from "@/components/presentations-fetch"
import { getAccessToken } from "@/components/auth-helpers"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog"
import { useCrossFilter } from "@/hooks/useCrossFilter"

const WorkspaceLoadingCard = ({ compact = false }: { compact?: boolean }) => (
  <div className={`rounded-xl border border-border/50 bg-card p-6 shadow-[var(--cursor-shadow-xs)] ${compact ? "min-h-[220px]" : "min-h-[320px]"} animate-pulse transition-shadow duration-300 ease-[cubic-bezier(0.2,0,0,1)]`}>
    <div className="mb-5 flex items-center justify-between gap-4">
      <div className="h-6 w-48 rounded-md bg-muted/60" />
      <div className="h-7 w-24 rounded-md bg-muted/50" />
    </div>
    <div className="space-y-3">
      <div className="h-4 w-36 rounded-md bg-muted/50" />
      <div className="h-44 rounded-lg bg-muted/30" />
      <div className="grid grid-cols-3 gap-3">
        <div className="h-10 rounded-lg bg-muted/30" />
        <div className="h-10 rounded-lg bg-muted/25" />
        <div className="h-10 rounded-lg bg-muted/20" />
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
  const { activeSelection, applyCrossFilter, clearCrossFilter } = useCrossFilter();

  // Estados para el modal de guardar reporte
  const [isSaveDialogOpen, setIsSaveDialogOpen] = useState(false);
  const [reportTitle, setReportTitle] = useState("");
  const [reportToSave, setReportToSave] = useState<any>(null);
  const [selectedSavePresentationId, setSelectedSavePresentationId] = useState("");
  const [isSaveActionLoading, setIsSaveActionLoading] = useState(false);
  const [isSaveDestinationsLoading, setIsSaveDestinationsLoading] = useState(false);

  const getWorkspaceAccessToken = useCallback(
    () => getAccessToken(supabase, "chat"),
    [supabase]
  );

  const refreshSaveDestinations = useCallback(async () => {
    setIsSaveDestinationsLoading(true);
    try {
      const accessToken = await getWorkspaceAccessToken();
      if (!accessToken) return;

      const payload = await fetchPresentationsShared(accessToken);
      setPresentations(payload as any);
    } catch (error) {
      console.error("Error cargando presentaciones para guardado:", error);
    } finally {
      setIsSaveDestinationsLoading(false);
    }
  }, [getWorkspaceAccessToken, setPresentations]);

  const handleOpenSaveDialog = (data: any, type: string, defaultTitle?: string, layout?: { w: number; h: number; minW?: number; minH?: number }) => {
    setReportToSave({ type, content: data, ...(layout ? { layout } : {}) });
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

  const handleChartDrillDown = useCallback((params: any, tableName?: string, option?: any) => {
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

      // [FASE 6.3] Emitir automáticamente al store reactivo de Cross-Filter con Anti-Self-Filter y Toggle
      const chartOption = option;
      const contractDimension = typeof chartOption?.query_contract?.dimension === 'string'
        ? chartOption.query_contract.dimension.trim()
        : (typeof chartOption?.xAxis?.name === 'string' ? chartOption.xAxis.name : 'categoria');

      applyCrossFilter(contractDimension || 'categoria', safeCategory, tableName, safeSeries);

      setDrillDown({
        isVisible: true,
        position: { x, y },
        dataContext: {
          category: safeCategory,
          value: value,
          series: safeSeries,
          tableName: tableName,
          secondaryCategory: rawSecondaryCategory || undefined,
          option: option,
        }
      });
    }
  }, [applyCrossFilter, setDrillDown]);

  const isWorkspaceBusy = workspaceRenderState.status === "analyzing" || workspaceRenderState.status === "staging";
  const hasItems = Array.isArray(items) && items.length > 0;

  return (
    <div className="h-full w-full p-6 overflow-y-auto bg-muted/30">
      {!hasItems && !isWorkspaceBusy ? (
        <div className="h-full min-h-[400px] w-full flex items-center justify-center transition-opacity duration-300 ease-[cubic-bezier(0.2,0,0,1)] animate-in fade-in">
          <div className="flex flex-col items-center gap-5 max-w-sm px-6 text-center animate-premium-entrance">
            <div className="w-14 h-14 rounded-xl bg-muted/80 flex items-center justify-center transition-all duration-300 ease-[cubic-bezier(0.2,0,0,1)] hover:bg-muted hover:shadow-[var(--cursor-shadow-sm)] hover:scale-105">
              <LayoutDashboard className="h-7 w-7 text-muted-foreground/60" />
            </div>
            <div className="space-y-1.5">
              <h2 className="text-lg font-medium text-foreground tracking-tight">
                Centro de Comando
              </h2>
              <p className="text-sm text-muted-foreground leading-relaxed">
                Ancla gráficos, arrastra visualizaciones y construye dashboards personalizados desde el chat.
              </p>
            </div>
            <div className="flex items-center gap-2 text-xs text-muted-foreground bg-muted/50 px-3 py-1.5 rounded-md border border-border/50">
              <MousePointerClick className="h-3.5 w-3.5" />
              <span>Usa el chat para generar tu primer análisis</span>
            </div>
            <Button
              variant="outline"
              className="mt-4 gap-2 text-sm transition-all duration-200 ease-[cubic-bezier(0.2,0,0,1)] hover:shadow-xs hover:scale-[1.02] active:scale-[0.98]"
              onClick={() => router.push('/cargar-datos')}
            >
              <Database className="h-4 w-4" />
              Cargar datos
            </Button>
          </div>
        </div>
      ) : (
        <div className="max-w-6xl mx-auto space-y-6 transition-opacity duration-200 animate-in fade-in">
          {isWorkspaceBusy && (
            <div className="sticky top-0 z-10 rounded-xl border border-border/50 bg-background/95 px-5 py-4 shadow-[var(--cursor-shadow-sm)] backdrop-blur-sm animate-premium-fade">
              <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                <div className="flex items-center gap-3">
                  <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary/10 text-primary">
                    <LoaderCircle className="h-4 w-4 animate-spin" />
                  </div>
                  <div>
                    <div className="text-sm font-medium text-foreground">
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

          {!hasItems && isWorkspaceBusy && (
            <>
              <WorkspaceLoadingCard />
              <WorkspaceLoadingCard compact />
            </>
          )}

          {/* 🦆 [FASE 6.3] Banner Reactivo de Filtro Cruzado Activo */}
          {activeSelection && (
            <div className="rounded-2xl border border-primary/40 bg-primary/10 px-4 py-2.5 flex items-center justify-between gap-3 text-xs text-primary shadow-sm backdrop-blur-sm transition-all duration-300 ease-[cubic-bezier(0.2,0,0,1)]">
              <div className="flex items-center gap-2">
                <span className="h-2 w-2 rounded-full bg-primary animate-pulse" />
                <span>
                  Filtro interactivo: <strong>{activeSelection.dimension} = {activeSelection.value}</strong>
                </span>
              </div>
              <Button
                variant="outline"
                size="sm"
                className="h-7 px-3 text-xs font-medium border-primary/40 text-primary hover:bg-primary/20 transition-all duration-200 ease-[cubic-bezier(0.2,0,0,1)]"
                onClick={clearCrossFilter}
              >
                Restablecer todo
              </Button>
            </div>
          )}

          {!isWorkspaceBusy && items.map((component, index) => {
            const componentKey = component.task_id
              ? `${component.type}-${component.task_id}-${index}`
              : (component.title
                  ? `${component.type}-${component.title}`
                  : (component.table_name
                      ? `${component.type}-${component.table_name}`
                      : `canvas-item-${component.type}-${index}`
                    )
                );
            
            switch(component.type) {
              case 'metricas_clave':
                if (component.data && Object.keys(component.data).length > 0) {
                  return (
                    <div key={componentKey} className="w-full animate-fade-slide-in">
                      <AnalysisReport
                        data={{ metrics: component.data, tableData: [] }}
                        onSave={() => handleOpenSaveDialog(component.data, "metrics", component.title || "Métricas Clave", { w: 2, h: 1, minW: 2, minH: 1 })}
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
                      onChartClick={(params) => handleChartDrillDown(params, component.table_name, component.option)} 
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
                      onChartClick={(params) => handleChartDrillDown(params, component.table_name, component.original_chart_option || component.option)}
                    />
                  );
                }
                return null;
                
              case 'tabla_datos':
                 if (component.data && Array.isArray(component.data) && component.data.length > 0) {
                   return (
                     <div key={componentKey} className="w-full animate-fade-slide-in">
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
          {isWorkspaceBusy && hasItems && workspaceRenderState.pendingVisuals > workspaceRenderState.renderedVisuals && (
            <WorkspaceLoadingCard compact />
          )}
        </div>
      )}

      {/* Modal de Guardado */}
      <Dialog open={isSaveDialogOpen && !!reportToSave} onOpenChange={(open) => { if (!open) setIsSaveDialogOpen(false) }}>
        <DialogContent className="sm:max-w-md" data-testid="save-report-dialog">
          <DialogHeader>
            <DialogTitle>Guardar Reporte</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <label className="text-sm font-medium text-foreground">Título del Reporte</label>
              <input
                type="text"
                value={reportTitle}
                onChange={(e) => setReportTitle(e.target.value)}
                placeholder="Ej: Análisis de Ventas Q3"
                className="w-full px-3 py-2 rounded-lg border border-border bg-background text-sm outline-none focus:ring-2 focus:ring-ring/30"
                autoFocus
                data-testid="save-report-title-input"
                disabled={isSaveActionLoading}
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium text-foreground">Presentación destino (Opcional)</label>
              <select
                value={selectedSavePresentationId}
                onChange={(event) => setSelectedSavePresentationId(event.target.value)}
                className="w-full px-3 py-2 rounded-lg border border-border bg-background text-sm outline-none focus:ring-2 focus:ring-ring/30"
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
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setIsSaveDialogOpen(false)} disabled={isSaveActionLoading}>Cancelar</Button>
            <Button onClick={handleConfirmSave} data-testid="save-report-confirm" disabled={isSaveActionLoading}>
              {isSaveActionLoading ? "Guardando..." : "Guardar Reporte"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
