"use client"

import { useCallback } from "react"
import { toast } from "sonner"
import * as duckdbEngine from "@/lib/duckdb-engine"
import { API_BASE_URL } from "@/lib/api-config"
import {
  buildAnalysisTableName,
  sanitizeFilterValue,
  enrichFiltersWithSyntheticBucket,
  type ChatMessage,
} from "@/lib/chat-utils"
import type { AnalysisComponent } from "@/lib/state"

export interface UseChatFilterHandlerDeps {
  workspacePreloadPromiseRef: React.MutableRefObject<Promise<any> | null>
  workspaceVisualsRef: React.MutableRefObject<any[] | null>
  workspaceItems: AnalysisComponent[]
  messages: ChatMessage[]
  analysisFileId: string | null
  lastCompletedTaskId: string | null
  activeTaskId: string | null
  getChatAccessToken: () => Promise<string | null>
  setIsDuckDBReady: (ready: boolean) => void
  setWorkspaceItems: React.Dispatch<React.SetStateAction<AnalysisComponent[]>> | ((updater: (prev: AnalysisComponent[]) => AnalysisComponent[]) => void)
  setMessages: React.Dispatch<React.SetStateAction<ChatMessage[]>> | ((updater: (prev: ChatMessage[]) => ChatMessage[]) => void)
}

export function useChatFilterHandler({
  workspacePreloadPromiseRef,
  workspaceVisualsRef,
  workspaceItems,
  messages,
  analysisFileId,
  lastCompletedTaskId,
  activeTaskId,
  getChatAccessToken,
  setIsDuckDBReady,
  setWorkspaceItems,
  setMessages,
}: UseChatFilterHandlerDeps) {
  // 🦆 [FASE 4] Cross-Filter Handler (DuckDB-WASM local, Multidimensional)
  const handleCrossFilter = useCallback(
    async (filters: Record<string, string>, tableName?: string) => {
      // ── PURE LOCAL CROSS-FILTER (DuckDB-WASM, <50ms) ────────────────
      // Filters directly on loaded pd_chart_* tables in WASM memory.
      // If tableName is undefined, probes ALL loaded tables to find the match.
      // NEVER invokes the LLM — this is a pure data-grid operation.
      try {
        if (workspacePreloadPromiseRef.current) {
          await workspacePreloadPromiseRef.current.catch(() => {})
        }
        const loadedTables = duckdbEngine.getTableNames()

        // ── Step 1: Resolve the target table ──
        let tName = tableName

        if (!tName || !loadedTables.includes(tName)) {
          // tableName missing or not loaded — find it from visual components
          const visualSources: any[] = []
          if (Array.isArray(workspaceVisualsRef.current)) {
            visualSources.push(...workspaceVisualsRef.current)
          }
          if (Array.isArray(workspaceItems)) {
            visualSources.push(...workspaceItems)
          }
          const lastVisuals =
            [...messages]
              .reverse()
              .find((msg) => Array.isArray(msg._visuals) && msg._visuals.length > 0)?._visuals || []
          if (Array.isArray(lastVisuals)) {
            visualSources.push(...lastVisuals)
          }

          // Try to find a table_name from the visual components that IS loaded
          for (const comp of visualSources) {
            if (!comp || typeof comp !== "object") continue
            const candidates = [
              comp?.table_name,
              comp?.option?.table_name,
              comp?.original_chart_option?.table_name,
            ].filter(Boolean) as string[]
            for (const candidate of candidates) {
              if (loadedTables.includes(candidate)) {
                tName = candidate
                break
              }
            }
            if (tName) break
          }

          // Still nothing? Try loading Arrow from the component on-demand
          if (!tName || !loadedTables.includes(tName)) {
            const findArrowPayload = (component: any): string | null => {
              if (!component || typeof component !== "object") return null
              return (
                component?.granular_arrow ||
                component?.arrow_data ||
                component?.option?.granular_arrow ||
                component?.option?.arrow_data ||
                component?.original_chart_option?.granular_arrow ||
                component?.original_chart_option?.arrow_data ||
                null
              )
            }

            for (const comp of visualSources) {
              const arrow = findArrowPayload(comp)
              const compTable =
                comp?.table_name || comp?.option?.table_name || comp?.original_chart_option?.table_name
              if (arrow && compTable) {
                await duckdbEngine.loadArrowData(arrow, compTable)
                setIsDuckDBReady(true)
                tName = compTable
                console.log(`🦆 [CROSS-FILTER] On-demand Arrow cargado para tabla: ${tName}`)
                break
              }
            }
          }

          // Last resort: use first loaded table or rehydrate on-demand from task
          if (!tName || !loadedTables.includes(tName)) {
            let refreshedTables = duckdbEngine.getTableNames()
            if (refreshedTables.length > 0) {
              tName = refreshedTables[0]
              console.log(`🦆 [CROSS-FILTER] Usando primera tabla disponible: ${tName}`)
            } else {
              // [FASE 6.1 & 6.2] Re-hidratación on-demand desde backend task
              const candidateTaskId =
                visualSources.find((c: any) => c?.task_id)?.task_id ||
                lastCompletedTaskId ||
                activeTaskId
              if (candidateTaskId) {
                console.log(
                  `🦆 [CROSS-FILTER] Tablas no encontradas en memoria. Re-hidratando desde task: ${candidateTaskId}`
                )
                try {
                  const token = await getChatAccessToken()
                  if (token) {
                    const res = await fetch(`${API_BASE_URL}/api/v1/tasks/${candidateTaskId}`, {
                      headers: { Authorization: `Bearer ${token}` },
                    })
                    if (res.ok) {
                      const taskPayload = await res.json()
                      const resData = taskPayload?.results_json || taskPayload?.result
                      const arrowToLoad = resData?.snapshot_arrow || resData?.arrow_data
                      if (arrowToLoad) {
                        const rehydrateTable = buildAnalysisTableName(
                          analysisFileId,
                          "snapshot",
                          "latest"
                        )
                        await duckdbEngine.loadArrowData(arrowToLoad, rehydrateTable)
                        setIsDuckDBReady(true)
                        tName = rehydrateTable
                        console.log(`🦆 [CROSS-FILTER] Re-hidratación on-demand exitosa: ${tName}`)
                      }
                    }
                  }
                } catch (rehydrateErr) {
                  console.warn("⚠️ [CROSS-FILTER] Re-hidratación on-demand falló:", rehydrateErr)
                }
              }

              refreshedTables = duckdbEngine.getTableNames()
              if (refreshedTables.length > 0) {
                tName = tName || refreshedTables[0]
              } else {
                console.error("⚠️ [CROSS-FILTER] No hay tablas cargadas en DuckDB-WASM.")
                toast.error("No hay datos cargados para filtrar localmente.")
                return
              }
            }
          }
        }

        console.log(`🦆 [CROSS-FILTER] Tabla resuelta: "${tName}" (de ${loadedTables.length} cargadas)`)

        // ── Step 2: Enrich filters (synthetic bucket for "Otros") ──
        const findComponentByTableName = (list: any[]): any | null => {
          for (const component of list) {
            if (!component || typeof component !== "object") continue
            if (component?.table_name === tName) return component
            if (component?.option?.table_name === tName) return component
            if (component?.original_chart_option?.table_name === tName) return component
          }
          return null
        }
        const allVisualSources: any[] = []
        if (Array.isArray(workspaceItems)) allVisualSources.push(...workspaceItems)
        const lastVis =
          [...messages]
            .reverse()
            .find((msg) => Array.isArray(msg._visuals) && msg._visuals.length > 0)?._visuals || []
        if (Array.isArray(lastVis)) allVisualSources.push(...lastVis)

        const matchedComponent = findComponentByTableName(allVisualSources)

        // [FIX 2026-06-08] Extraer los filtros base del chart original (e.g.
        // "Tipo Movimiento = Ingreso" si el chart solo graficaba ingresos).
        // El backend (canary_executor) ahora inyecta estos en chart_option.chart_base_filters.
        // Sin mergearlos, DuckDB solo filtra por el clic y retorna registros
        // que no pertenezcan al subset que el chart estaba visualizando.
        //
        // [FIX 2026-06-09] Aplicar sanitizeFilterValue a cada valor para
        // limpiar prefijos como "FilterOperator.EQUALS " que el backend
        // serializa accidentalmente. Sin esto, DuckDB busca la cadena
        // literal y nunca encuentra coincidencia.
        let baseFilters: Record<string, string> = {}
        if (matchedComponent) {
          const chartOption =
            (matchedComponent as any).option ||
            (matchedComponent as any).original_chart_option ||
            null
          if (chartOption?.chart_base_filters && typeof chartOption.chart_base_filters === "object") {
            baseFilters = Object.fromEntries(
              Object.entries(chartOption.chart_base_filters).map(([k, v]) => [
                k,
                sanitizeFilterValue(String(v)),
              ])
            )
          }
        }

        if (Object.keys(baseFilters).length > 0) {
          console.log("📋 [BASE FILTERS] chart_base_filters del backend:", {
            baseFilters,
            count: Object.keys(baseFilters).length,
            columns: Object.keys(baseFilters),
          })
        }

        // [FIX 2026-06-09] RUTEO INTELIGENTE DE TABLA: Si la tabla resuelta
        // es per-chart (pd_chart_*, agregada), preferir SIEMPRE la tabla
        // snapshot (pd_snapshot_*, raw con todas las dimensiones).
        //
        // Por que: el requerimiento de negocio es que el usuario SIEMPRE
        // vea el detalle crudo (drill-down) cuando hace clic en 'Filtrar aquí',
        // independientemente de si el chart tiene filtros base o no.
        //
        // La tabla per-chart contiene el resultado agregado (e.g. sum(monto)
        // by month), NO contiene todas las columnas dimensionales. El usuario
        // espera ver los registros crudos subyacentes, no una sola fila
        // agregada (Name, Value) que es lo que retorna la tabla per-chart
        // cuando se filtra por un valor dimensional.
        //
        // La tabla snapshot (pd_snapshot_<fileId>_latest) SÍ tiene todas
        // las dimensiones raw: fecha_operacion, placa_unidad, tipo_unidad,
        // origen, destino, km_recorridos, horas_manejo, galones_consumidos,
        // gasto_combustible_s, etc. (truncada a 10,000 filas por el
        // BIG DATA SHIELD del backend). Esta tabla siempre se pre-carga
        // junto con el chart (linea 1014-1027) cuando data.result.snapshot_arrow
        // existe.
        //
        // [FIX 2026-06-09 2da iteracion] Se elimino la condicion
        // `Object.keys(baseFilters).length > 0` porque el ruteo debe ocurrir
        // siempre que la tabla per-chart sea agregada, no solo cuando hay
        // filtros dimensionales.
        //
        // [FIX 2026-06-09 3ra iteracion] Se cambio la tabla destino de
        // `pd_analysis_*_detail` (que resulto ser AGREGADA, no raw) a
        // `pd_snapshot_*_latest` (que es RAW con todas las dimensiones).
        // La tabla pd_analysis_*_detail solo contiene la salida agregada
        // del chart (e.g. name='Aug-2021', value=sum), NO las columnas
        // dimensionales como fecha_operacion. Por eso DuckDB retornaba
        // solo 1 fila para 'Aug-2021': matcheaba contra la columna 'name'
        // agregada. La tabla pd_snapshot_*_latest tiene 10K filas raw
        // con fecha_operacion como timestamp, donde el motor DuckDB puede
        // aplicar el predicate temporal EXTRACT(YEAR|MONTH).
        //
        // [FIX 2026-06-09 4ta iteracion] Se guarda la tabla original
        // (per-chart, agregada) en `originalChartTableName` como fallback.
        // Si el cross-filter contra la tabla snapshot retorna 0 rows
        // (porque el snapshot de 10K head() no contiene el periodo
        // seleccionado por el usuario, e.g. 'Dec-2023' cae fuera del
        // rango de las primeras 10K filas), hacemos fallback a la tabla
        // per-chart y mostramos la fila agregada con un mensaje claro.
        // Esto preserva el contrato de UX: el usuario SIEMPRE ve datos
        // del periodo que selecciono, ya sea crudos o agregados.
        let originalChartTableName: string | null = null
        if (
          tName &&
          (tName.startsWith("pd_chart_") || tName.startsWith("pd_analysis_")) &&
          analysisFileId
        ) {
          originalChartTableName = tName
          const snapshotTableName = buildAnalysisTableName(
            analysisFileId,
            "snapshot",
            "latest"
          )
          const refreshedTables = duckdbEngine.getTableNames()
          if (refreshedTables.includes(snapshotTableName)) {
            const reason =
              Object.keys(baseFilters).length > 0
                ? `hay ${Object.keys(baseFilters).length} filtro(s) dimensional(es) que requieren columnas raw`
                : `el usuario espera ver el detalle crudo subyacente (drill-down)`
            console.log(
              `🦆 [CROSS-FILTER] Re-ruteo a tabla snapshot '${snapshotTableName}' ` +
                `porque ${reason} (la tabla '${tName}' solo tiene datos agregados).`
            )
            tName = snapshotTableName
          }
        }

        // Merge: los filtros base del chart (e.g. "Tipo Movimiento=Ingreso")
        // se combinan con el clic del usuario (e.g. "mes=Jan-2025").
        // Si el usuario hace click en algo que YA está en los filtros base,
        // gana el clic (overwrite) para evitar duplicación.
        const mergedFilters = { ...baseFilters, ...filters }
        const effectiveFilters = matchedComponent
          ? enrichFiltersWithSyntheticBucket(mergedFilters, matchedComponent)
          : mergedFilters

        // Build summaries for UI feedback
        const baseFilterSummary = Object.entries(baseFilters)
          .filter(([key]) => !key.startsWith("__"))
          .map(([k, v]) => `${k}="${v}"`)
          .join(" AND ")
        const clickFilterSummary = Object.entries(filters)
          .filter(([key]) => !key.startsWith("__"))
          .map(([k, v]) => `${k}="${v}"`)
          .join(" AND ")
        const filterSummary = Object.entries(mergedFilters)
          .filter(([key]) => !key.startsWith("__"))
          .map(([k, v]) => `${k}="${v}"`)
          .join(" AND ")

        // ── Step 3: Execute local DuckDB cross-filter ──
        console.log(
          `🦆 [CROSS-FILTER] Filtrando: ${filterSummary} en tabla '${tName}'`,
          { baseFilters, clickFilters: filters, merged: mergedFilters }
        )
        let filtered = await duckdbEngine.crossFilter(effectiveFilters, tName)

        // [FIX 2026-06-09 4ta iteracion] FALLBACK al per-chart si el snapshot
        // retorna 0 rows. Esto pasa cuando el snapshot de 10K head() no
        // contiene el periodo seleccionado (e.g. 'Dec-2023' cae fuera del
        // rango de las primeras 10K filas). En ese caso, mostramos la fila
        // agregada del per-chart como fallback honesto.
        let isAggregatedFallback = false
        if (filtered.length === 0 && originalChartTableName) {
          console.log(
            `🦆 [CROSS-FILTER] Snapshot sin resultados. Fallback a tabla per-chart ` +
              `'${originalChartTableName}' para mostrar datos agregados del periodo.`
          )
          const fallbackFiltered = await duckdbEngine.crossFilter(
            effectiveFilters,
            originalChartTableName
          )
          if (fallbackFiltered.length > 0) {
            filtered = fallbackFiltered
            isAggregatedFallback = true
          }
        }

        if (filtered.length === 0) {
          console.warn("⚠️ [CROSS-FILTER] Sin resultados, manteniendo vista actual")
          toast.info("No se encontraron registros con ese filtro.")
          return
        }

        // ── Step 4: Dispatch to workspace canvas (ORIGINAL BEHAVIOR) ──
        const newTableComponent: AnalysisComponent = {
          type: "tabla_datos" as const,
          data: filtered,
          title: `Datos Filtrados: ${filterSummary}`,
        }

        console.log("4. [Bridge/Chat] Despachando a workspaceItemsAtom y messagesAtom:", {
          newTableComponent,
        })
        setWorkspaceItems((prev: AnalysisComponent[]) => [...prev, newTableComponent])

        // [FIX 2026-06-08] UI feedback que muestra EXPLÍCITAMENTE la suma de
        // los filtros (base + clic) para que el usuario confíe en la tabla
        // resultante y entienda por qué algunos registros fueron excluidos.
        let filterDescription = `⚡ **Filtro local aplicado:** \n\n`
        if (baseFilterSummary) {
          filterDescription += `📊 **Filtros base del chart:** ${baseFilterSummary}\n`
        }
        if (clickFilterSummary) {
          filterDescription += `➕ **Filtros del clic:** ${clickFilterSummary}\n`
        }
        if (!baseFilterSummary && !clickFilterSummary) {
          filterDescription += `🔍 Sin filtros activos\n`
        }
        if (isAggregatedFallback) {
          filterDescription += `\n⚠️ **Nota:** El detalle crudo no estaba disponible para este periodo (el snapshot solo incluye las primeras 10,000 filas del dataset). Mostrando la **fila agregada** del chart. Para ver el detalle completo, intenta con un periodo cubierto por el snapshot.\n`
        }
        filterDescription += `\n📌 *Se encontraron ${filtered.length} registros en <50ms.* \nLa tabla filtrada ha sido añadida a tu Lienzo.`

        const filterMsg: ChatMessage = {
          id: `crossfilter-${Date.now()}`,
          type: "assistant",
          content: filterDescription,
          timestamp: new Date(),
          components: [],
          _visuals: [newTableComponent],
        }
        setMessages((prev: ChatMessage[]) => [...prev, filterMsg])
        console.log(`🦆 [CROSS-FILTER] Completado: ${filtered.length} filas`)
      } catch (error) {
        console.error("⚠️ [CROSS-FILTER] Error en filtro local:", error)
        toast.error("Error al aplicar filtro local. Intenta de nuevo.")
      }
    },
    [
      workspacePreloadPromiseRef,
      workspaceVisualsRef,
      workspaceItems,
      messages,
      analysisFileId,
      lastCompletedTaskId,
      activeTaskId,
      getChatAccessToken,
      setIsDuckDBReady,
      setWorkspaceItems,
      setMessages,
    ]
  )

  return { handleCrossFilter }
}
