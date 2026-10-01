"use client"

import React from "react"
import { Trash2, LayoutDashboard, Link2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { cn } from "@/lib/utils"
import { AnalysisReport } from "@/components/analysis-report"
import { ChartsReport } from "@/components/charts-report"
import { SmartTable } from "@/components/smart-table"
import { TemporalProvenanceBadge } from "@/components/temporal-provenance-badge"
import { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider } from "@/components/ui/tooltip"
import type { ChatMessage } from "@/lib/chat-utils"
import type { AnalysisComponent } from "@/lib/state"

export interface ChatMessageListProps {
  messages: ChatMessage[]
  activeTaskId: string | null
  isAnalyzing: boolean
  onDeleteMessage: (id: string) => void
  onOpenSaveDialog: (data: any, type: string, defaultTitle?: string) => void
  onChartDrillDown: (params: any, tableName?: string, option?: any) => void
  onRestoreDashboard: (visuals: AnalysisComponent[]) => void
  messagesEndRef: React.RefObject<HTMLDivElement | null>
}

export const ChatMessageList = React.memo(function ChatMessageList({
  messages,
  activeTaskId,
  isAnalyzing,
  onDeleteMessage,
  onOpenSaveDialog,
  onChartDrillDown,
  onRestoreDashboard,
  messagesEndRef,
}: ChatMessageListProps) {
  return (
    <div className="space-y-6 min-w-0">
      {messages.map((msg) => (
        <div
          key={msg.id}
          className={cn("flex items-start gap-3 min-w-0", msg.type === "user" && "justify-end")}
        >
          {msg.type === "assistant" && (
            <div className="w-8 h-8 bg-accent rounded-full flex items-center justify-center text-accent-foreground text-sm font-medium flex-shrink-0">
              P
            </div>
          )}

          <div
            className={cn(
              "rounded-2xl space-y-4 relative group min-w-0",
              msg.type === "user"
                ? "p-4 pr-12 bg-muted/50 text-foreground max-w-[85%] ml-auto shadow-sm break-words"
                : "w-full bg-card/90 dark:bg-card/60 border border-border/70 rounded-2xl p-4 sm:p-5 pr-12 sm:pr-14 shadow-xs transition-all duration-200 min-w-0 relative"
            )}
          >
            {/* Botón de eliminar — esquina superior derecha, sobre la línea del borde */}
            <div className="absolute -top-3.5 right-0 -translate-x-1/2 z-20">
              <TooltipProvider delayDuration={0}>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <button
                      type="button"
                      onClick={() => onDeleteMessage(msg.id)}
                      className="flex h-7 w-7 items-center justify-center rounded-lg border border-border/50 bg-background/90 text-muted-foreground/70 opacity-0 shadow-2xs backdrop-blur-xs transition-all duration-150 ease-out group-hover:opacity-100 hover:border-destructive/30 hover:bg-destructive/10 hover:text-destructive hover:scale-105 active:scale-95 cursor-pointer"
                      aria-label="Eliminar mensaje"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </TooltipTrigger>
                  <TooltipContent side="top" align="end" className="text-[11px] py-1 px-2 font-medium shadow-md">
                    Eliminar mensaje
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
            </div>

            <div
              className={cn(
                "text-sm whitespace-pre-wrap leading-relaxed min-w-0",
                msg.taskId === activeTaskId && isAnalyzing
                  ? "text-muted-foreground animate-pulse"
                  : "prose-sm max-w-none text-foreground/90 font-sans"
              )}
            >
              {typeof msg.content === "string" ? (
                msg.content
              ) : (
                <AnalysisReport
                  data={msg.content as any}
                  onSave={() => onOpenSaveDialog(msg.content, "report", "Análisis Completo")}
                />
              )}
            </div>

            {msg.type === "assistant" &&
              msg.components &&
              msg.components.map((component, index) => {
                const componentKey = `${msg.id}-component-${index}`
                switch (component.type) {
                  case "metricas_clave":
                    if (component.data && Object.keys(component.data).length > 0) {
                      return (
                        <AnalysisReport
                          key={componentKey}
                          data={{ metrics: component.data, tableData: [] }}
                          onSave={() =>
                            onOpenSaveDialog(component.data, "metrics", "Métricas Clave")
                          }
                        />
                      )
                    }
                    return null

                  case "tabla_datos":
                    if (
                      component.data &&
                      Array.isArray(component.data) &&
                      component.data.length > 0
                    ) {
                      return (
                        <AnalysisReport
                          key={componentKey}
                          data={{
                            tableData: component.data,
                            title: component.title,
                            metrics: {},
                          }}
                          onSave={() =>
                            onOpenSaveDialog(
                              { data: component.data, title: component.title },
                              "table",
                              component.title
                            )
                          }
                        />
                      )
                    }
                    return null

                  case "correlaciones":
                    if (
                      component.data &&
                      Array.isArray(component.data) &&
                      component.data.length > 0
                    ) {
                      return (
                        <Card
                          key={componentKey}
                          className="p-4 bg-muted/30 border border-border/50 mt-4"
                        >
                          <h4 className="font-medium mb-3 text-sm flex items-center gap-2 text-foreground">
                            <Link2 className="h-4 w-4 text-muted-foreground" />
                            <span>Correlaciones y Patrones Detectados</span>
                          </h4>
                          <div className="grid gap-2">
                            {component.data.map((corr: any, idx: number) => {
                              if (!corr.variable_1 || !corr.variable_2 || isNaN(corr.fuerza))
                                return null
                              return (
                                <div
                                  key={idx}
                                  className="text-sm flex flex-wrap justify-between items-center bg-background p-3 rounded-lg border border-border/50"
                                >
                                  <div className="flex items-center gap-2">
                                    <span className="font-medium">{corr.variable_1}</span>
                                    <span className="text-muted-foreground">↔</span>
                                    <span className="font-medium">{corr.variable_2}</span>
                                  </div>
                                  <span
                                    className={cn(
                                      "text-xs font-medium px-2 py-0.5 rounded-full border",
                                      corr.fuerza > 0
                                        ? "bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-950/50 dark:text-emerald-400 dark:border-emerald-800"
                                        : "bg-rose-50 text-rose-700 border-rose-200 dark:bg-rose-950/50 dark:text-rose-400 dark:border-rose-800"
                                    )}
                                  >
                                    {corr.fuerza > 0 ? "Positiva" : "Inversa"} ({Math.abs(corr.fuerza)})
                                  </span>
                                </div>
                              )
                            })}
                          </div>
                        </Card>
                      )
                    }
                    return null

                  case "configuracion_echarts":
                    if (component.option) {
                      return (
                        <div key={componentKey}>
                          <TemporalProvenanceBadge
                            provenance={(component.option as any)?.temporal_provenance}
                            className="mb-2"
                          />
                          <ChartsReport
                            option={component.option}
                            title={component.title}
                            onSave={(optionOverride) =>
                              onOpenSaveDialog(
                                optionOverride || component.option,
                                "chart",
                                component.title
                              )
                            }
                            onChartClick={(params) =>
                              onChartDrillDown(params, component.table_name, component.option)
                            }
                          />
                        </div>
                      )
                    }
                    return null

                  case "smart_table":
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
                          onSave={() => onOpenSaveDialog(component, "table", component.title)}
                          onChartClick={(params) =>
                            onChartDrillDown(
                              params,
                              component.table_name,
                              component.original_chart_option || component.option
                            )
                          }
                        />
                      )
                    }
                    return null

                  case "recomendaciones":
                    if (
                      component.data &&
                      Array.isArray(component.data) &&
                      component.data.length > 0
                    ) {
                      return (
                        <div
                          key={componentKey}
                          className="mt-4 p-4 bg-secondary/50 border border-border/40 rounded-lg"
                        >
                          <h4 className="font-medium text-foreground mb-2 flex items-center gap-2">
                            💡 Recomendaciones de IA
                          </h4>
                          <ul className="list-disc list-inside space-y-1 text-sm text-foreground/80">
                            {component.data.map((rec: string, idx: number) => (
                              <li key={idx}>{rec}</li>
                            ))}
                          </ul>
                        </div>
                      )
                    }
                    return null

                  case "explicabilidad_analitica":
                    if (component.data && typeof component.data === "object") {
                      const explainability = component.data as any
                      const confidence = explainability.confidence || {}
                      return (
                        <details key={componentKey} className="mt-4 group">
                          <summary className="list-none cursor-pointer rounded-lg border border-border/40 bg-muted/30 px-4 py-3 text-sm text-foreground transition-colors hover:bg-muted/40 dark:border-border/40 dark:bg-muted/20 dark:text-foreground dark:hover:bg-muted/30">
                            <div className="flex items-center justify-between gap-4">
                              <span className="font-medium">
                                🔍 Ver trazabilidad y auditoría del dato
                              </span>
                              <span className="text-xs text-muted-foreground group-open:hidden">
                                {confidence.level || "Verificado"}{" "}
                                {typeof confidence.score === "number"
                                  ? `· ${Math.round(confidence.score * 100)}%`
                                  : ""}
                              </span>
                            </div>
                          </summary>
                            <Card className="mt-2 border border-border/40 bg-muted/30 dark:bg-muted/20 dark:border-border/40">
                            <div className="p-4 space-y-4">
                              <div className="flex flex-col gap-2">
                                <div className="flex items-center justify-between gap-4">
                                  <div>
                                    <h4 className="text-sm font-semibold text-foreground dark:text-foreground">
                                      Trazabilidad del análisis
                                    </h4>
                                    <p className="text-xs text-muted-foreground">
                                      {explainability.title || "Análisis"} ·{" "}
                                      {explainability.intent_type || "Directo"} ·{" "}
                                      {explainability.visual_protocol || "Estándar"}
                                    </p>
                                  </div>
                                </div>
                              </div>
                            </div>
                          </Card>
                        </details>
                      )
                    }
                    return null

                  case "mensaje_resumen":
                    const hasMetrics =
                      component.metricas_destacadas &&
                      Object.keys(component.metricas_destacadas).length > 0
                    const hasPoints =
                      component.puntos_clave &&
                      Array.isArray(component.puntos_clave) &&
                      component.puntos_clave.length > 0

                    if (!hasMetrics && !hasPoints) return null

                    return (
                      <div key={componentKey} className="space-y-4 mt-4 w-full">
                        {hasMetrics && (
                          <AnalysisReport
                            data={{ metrics: component.metricas_destacadas }}
                            onSave={() =>
                              onOpenSaveDialog(
                                component.metricas_destacadas,
                                "metrics",
                                "Métricas Clave"
                              )
                            }
                          />
                        )}

                        {hasPoints && (
                          <div className="p-4 bg-amber-50/50 dark:bg-amber-900/10 border border-amber-100 dark:border-amber-900/30 rounded-lg">
                            <h4 className="font-medium text-amber-700 dark:text-amber-400 mb-2 flex items-center gap-2 text-sm">
                              ⚠️ Hallazgos Críticos
                            </h4>
                            <ul className="list-disc list-inside space-y-1 text-sm text-foreground/80">
                              {component.puntos_clave?.map((point: string, idx: number) => (
                                <li key={idx}>{point}</li>
                              ))}
                            </ul>
                          </div>
                        )}
                      </div>
                    )

                  default:
                    return null
                }
              })}

            {/* Botón Restaurar Dashboard */}
            {msg.type === "assistant" && msg._visuals && msg._visuals.length > 0 && (
              <div className="mt-3 flex">
                <Button
                  variant="secondary"
                  size="sm"
                  className="text-xs flex items-center gap-1.5 h-8 bg-primary/10 hover:bg-primary/20 text-primary hover:text-primary border border-primary/20 transition-all font-medium"
                  onClick={() => onRestoreDashboard(msg._visuals!)}
                >
                  <LayoutDashboard className="w-3.5 h-3.5" />
                  Restaurar Dashboard
                </Button>
              </div>
            )}
          </div>
        </div>
      ))}
      <div ref={messagesEndRef} />
    </div>
  )
})
