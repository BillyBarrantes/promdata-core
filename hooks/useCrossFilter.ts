"use client"

import { useCallback } from "react"
import { useAtom } from "jotai"
import { activeCrossFilterAtom, type CrossFilterSelection } from "@/lib/state"

export interface UseCrossFilterReturn {
  activeSelection: CrossFilterSelection | null
  applyCrossFilter: (
    dimension: string,
    value: string,
    sourceTable?: string,
    sourceSeries?: string
  ) => void
  clearCrossFilter: () => void
  isSelfChart: (tableName?: string) => boolean
  isActiveDimensionValue: (dimension: string, value: string) => boolean
}

export function useCrossFilter(): UseCrossFilterReturn {
  const [activeSelection, setActiveSelection] = useAtom(activeCrossFilterAtom)

  /**
   * Aplica un filtro cruzado con soporte de Toggle (clic repetido deselecciona).
   */
  const applyCrossFilter = useCallback((
    dimension: string,
    value: string,
    sourceTable?: string,
    sourceSeries?: string
  ) => {
    const cleanDim = String(dimension).trim()
    const cleanVal = String(value).trim()

    // 1. Mecanismo TOGGLE: si el usuario hace clic en el mismo elemento ya seleccionado, lo desactiva
    if (
      activeSelection &&
      activeSelection.dimension === cleanDim &&
      activeSelection.value === cleanVal
    ) {
      console.log(`🦆 [useCrossFilter] Toggle off: deseleccionando "${cleanDim} = ${cleanVal}"`)
      setActiveSelection(null)
      return
    }

    // 2. Nuevo filtro activo
    console.log(`🦆 [useCrossFilter] Aplicando filtro global: "${cleanDim} = ${cleanVal}" (origen: ${sourceTable || "unknown"})`)
    setActiveSelection({
      dimension: cleanDim,
      value: cleanVal,
      sourceTable,
      sourceSeries,
    })
  }, [activeSelection, setActiveSelection])

  const clearCrossFilter = useCallback(() => {
    setActiveSelection(null)
  }, [setActiveSelection])

  /**
   * [Anti-Self-Filter Guard]:
   * Retorna true si el gráfico solicitante es la fuente de la selección.
   * El gráfico origen resalta la selección pero NO colapsa sus demás barras.
   */
  const isSelfChart = useCallback((tableName?: string): boolean => {
    if (!activeSelection || !tableName || !activeSelection.sourceTable) return false
    return activeSelection.sourceTable === tableName
  }, [activeSelection])

  const isActiveDimensionValue = useCallback((dimension: string, value: string): boolean => {
    if (!activeSelection) return false
    return activeSelection.dimension === dimension && activeSelection.value === value
  }, [activeSelection])

  return {
    activeSelection,
    applyCrossFilter,
    clearCrossFilter,
    isSelfChart,
    isActiveDimensionValue,
  }
}
