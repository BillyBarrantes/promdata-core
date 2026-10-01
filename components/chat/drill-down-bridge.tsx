"use client"

import React, { useCallback } from "react"
import { useAtomValue, useSetAtom } from "jotai"
import { drillDownAtom, duckdbReadyAtom } from "@/lib/state"
import { DrillDownMenu } from "@/components/drill-down-menu"

export interface DrillDownBridgeProps {
  onSelect: (prompt: string) => void
  onCrossFilter: (filters: Record<string, string>, tableName?: string) => void
}

/**
 * 🚀 [PERF] Bridge component — reads drillDownAtom independently.
 * Prevents unnecessary re-renders of the parent chat interface when the drilldown menu opens/closes.
 */
export function DrillDownBridge({ onSelect, onCrossFilter }: DrillDownBridgeProps) {
  const drillDown = useAtomValue(drillDownAtom)
  const isDuckDBReady = useAtomValue(duckdbReadyAtom)
  const setDrillDown = useSetAtom(drillDownAtom)

  const handleClose = useCallback(() => {
    setDrillDown((prev) => ({ ...prev, isVisible: false }))
  }, [setDrillDown])

  return (
    <DrillDownMenu
      isVisible={drillDown.isVisible}
      position={drillDown.position}
      dataContext={drillDown.dataContext}
      onSelect={onSelect}
      onClose={handleClose}
      isDuckDBReady={isDuckDBReady}
      onCrossFilter={onCrossFilter}
    />
  )
}
