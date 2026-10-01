"use client"

import React, { useState } from "react"
import { Card } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { TrendingUp, TrendingDown, Minus, ShieldCheck } from "lucide-react"
import { EvidenceDrawer, type EvidenceBundleData } from "@/components/dashboard/evidence-drawer"

export interface KpiItem {
  key: string
  label: string
  value: number | string
  changePercent?: number
  format?: "currency" | "percent" | "number" | "compact"
  evidence?: EvidenceBundleData
}

interface KpiGridProps {
  metrics: Record<string, any>
  title?: string
  evidence?: EvidenceBundleData
}

export function KpiGrid({ metrics, title = "Métricas Clave", evidence }: KpiGridProps) {
  const [selectedEvidence, setSelectedEvidence] = useState<EvidenceBundleData | null>(null)
  const [evidenceTitle, setEvidenceTitle] = useState<string>("Evidencia")

  if (!metrics || Object.keys(metrics).length === 0) return null

  const formatValue = (val: any): string => {
    if (typeof val === "number") {
      if (Math.abs(val) >= 1_000_000) {
        return `${(val / 1_000_000).toLocaleString(undefined, { maximumFractionDigits: 1 })}M`
      }
      if (Math.abs(val) >= 1_000) {
        return val.toLocaleString(undefined, { maximumFractionDigits: 1 })
      }
      return val.toLocaleString(undefined, { maximumFractionDigits: 2 })
    }
    return String(val ?? "")
  }

  const entries = Object.entries(metrics)

  return (
    <div className="w-full space-y-3">
      {title && (
        <div className="flex items-center justify-between">
          <h4 className="text-sm font-semibold text-foreground tracking-tight">{title}</h4>
          {evidence && (
            <Button
              variant="ghost"
              size="sm"
              className="h-7 px-2 text-xs text-muted-foreground hover:text-foreground"
              onClick={() => {
                setSelectedEvidence(evidence)
                setEvidenceTitle(`Evidencia: ${title}`)
              }}
            >
              <ShieldCheck className="h-3.5 w-3.5 text-emerald-500 mr-1" />
              <span>Evidencia</span>
            </Button>
          )}
        </div>
      )}

      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
        {entries.map(([key, val]) => {
          const isNumeric = typeof val === "number"
          return (
            <Card
              key={key}
              className="p-3.5 border border-border/40 bg-card rounded-xl shadow-[var(--cursor-shadow-sm)] hover:shadow-[var(--cursor-shadow-md)] hover:border-primary/30 transition-all duration-300 ease-[cubic-bezier(0.2,0,0,1)]"
            >
              <div className="flex items-center justify-between text-xs text-muted-foreground">
                <span className="font-medium truncate capitalize">{key.replace(/_/g, " ")}</span>
              </div>
              <div className="mt-1.5 flex items-baseline justify-between">
                <span className="text-lg font-bold font-mono text-foreground tracking-tight">
                  {formatValue(val)}
                </span>
              </div>
            </Card>
          )
        })}
      </div>

      <EvidenceDrawer
        isOpen={Boolean(selectedEvidence)}
        onClose={() => setSelectedEvidence(null)}
        evidence={selectedEvidence}
        title={evidenceTitle}
      />
    </div>
  )
}
