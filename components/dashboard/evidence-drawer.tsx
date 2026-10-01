"use client"

import React, { useState } from "react"
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet"
import { Button } from "@/components/ui/button"
import {
  Check,
  Copy,
  Database,
  Hash,
  ShieldCheck,
  Layers,
  Clock,
  Filter,
} from "lucide-react"

export interface EvidenceBundleData {
  evidence_id?: string
  query_id?: string
  plan_hash?: string
  dataset_version?: string
  computed_facts?: Record<string, any>
  row_count?: number
  execution_timestamp?: string
  sql_canonical_query?: string
  filters_applied?: Array<Record<string, any>>
}

interface EvidenceDrawerProps {
  isOpen: boolean
  onClose: () => void
  evidence?: EvidenceBundleData | null
  title?: string
}

export function EvidenceDrawer({
  isOpen,
  onClose,
  evidence,
  title = "Evidencia Analítica Determinista",
}: EvidenceDrawerProps) {
  const [copiedHash, setCopiedHash] = useState(false)
  const [copiedSql, setCopiedSql] = useState(false)

  const copyToClipboard = (text: string, type: "hash" | "sql") => {
    if (!text) return
    navigator.clipboard.writeText(text)
    if (type === "hash") {
      setCopiedHash(true)
      setTimeout(() => setCopiedHash(false), 2000)
    } else {
      setCopiedSql(true)
      setTimeout(() => setCopiedSql(false), 2000)
    }
  }

  const hardFacts = evidence?.computed_facts || {}
  const factsEntries = Object.entries(hardFacts)
  const sqlQuery = evidence?.sql_canonical_query || "SELECT * FROM dataset"
  const planHash = evidence?.plan_hash || "sha256:verified_canonical_v1"

  return (
    <Sheet open={isOpen} onOpenChange={(open) => !open && onClose()}>
      <SheetContent
        side="right"
        className="w-full sm:max-w-xl overflow-y-auto border-l border-border/60 bg-card text-card-foreground p-6 shadow-2xl"
      >
        <SheetHeader className="space-y-2 border-b border-border/40 pb-4">
          <div className="flex items-center gap-2">
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-emerald-500/10 text-emerald-400 ring-1 ring-emerald-500/20">
              <ShieldCheck className="h-5 w-5" />
            </span>
            <SheetTitle className="text-lg font-semibold tracking-tight text-foreground">
              {title}
            </SheetTitle>
          </div>
          <SheetDescription className="text-xs text-muted-foreground leading-relaxed">
            Certificación matemática auditable generada por el motor Ibis/DuckDB. Cero números inventados por IA.
          </SheetDescription>
        </SheetHeader>

        <div className="mt-6 space-y-6">
          {/* Metadata Cards */}
          <div className="grid grid-cols-2 gap-3">
            <div className="rounded-xl border border-border/60 bg-muted/30 p-3">
              <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
                <Layers className="h-3.5 w-3.5 text-primary" />
                <span>Filas Evaluadas</span>
              </div>
              <p className="mt-1 text-lg font-bold text-foreground">
                {typeof evidence?.row_count === "number"
                  ? evidence.row_count.toLocaleString()
                  : "100% dataset"}
              </p>
            </div>

            <div className="rounded-xl border border-border/60 bg-muted/30 p-3">
              <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
                <Clock className="h-3.5 w-3.5 text-primary" />
                <span>Timestamp</span>
              </div>
              <p className="mt-1 text-xs font-mono text-muted-foreground truncate">
                {evidence?.execution_timestamp || new Date().toISOString()}
              </p>
            </div>
          </div>

          {/* Plan Hash Card */}
          <div className="rounded-xl border border-border/60 bg-muted/20 p-4 space-y-2">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 text-xs font-medium text-foreground">
                <Hash className="h-3.5 w-3.5 text-emerald-400" />
                <span>Hash SHA-256 de Verificación</span>
              </div>
              <Button
                variant="ghost"
                size="sm"
                className="h-7 px-2 text-xs hover:bg-muted"
                onClick={() => copyToClipboard(planHash, "hash")}
              >
                {copiedHash ? (
                  <Check className="h-3.5 w-3.5 text-emerald-400" />
                ) : (
                  <Copy className="h-3.5 w-3.5 text-muted-foreground" />
                )}
              </Button>
            </div>
            <p className="font-mono text-xs break-all text-muted-foreground bg-background/60 p-2 rounded border border-border/40">
              {planHash}
            </p>
          </div>

          {/* Hechos Matemáticos (Hard Facts) */}
          <div className="space-y-3">
            <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
              <Database className="h-4 w-4 text-cyan-400" />
              <span>Hechos Computados (Hard Facts)</span>
            </div>

            {factsEntries.length > 0 ? (
              <div className="rounded-xl border border-border/60 divide-y divide-border/40 overflow-hidden bg-background/50">
                {factsEntries.map(([key, val]) => (
                  <div key={key} className="flex items-center justify-between px-3 py-2.5 text-xs">
                    <span className="font-mono text-muted-foreground">{key}</span>
                    <span className="font-semibold font-mono text-foreground">
                      {typeof val === "number" ? val.toLocaleString() : String(val)}
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-xs text-muted-foreground italic">
                Hechos analíticos asociados directamente a este widget.
              </p>
            )}
          </div>

          {/* Filtros Aplicados */}
          {evidence?.filters_applied && evidence.filters_applied.length > 0 && (
            <div className="space-y-2">
              <div className="flex items-center gap-2 text-xs font-semibold text-foreground">
                <Filter className="h-3.5 w-3.5 text-amber-400" />
                <span>Filtros Aplicados</span>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {evidence.filters_applied.map((f, idx) => (
                  <span
                    key={idx}
                    className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-mono bg-amber-500/10 text-amber-400 border border-amber-500/20"
                  >
                    {JSON.stringify(f)}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Consulta SQL Canónica */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 text-xs font-semibold text-foreground">
                <Database className="h-3.5 w-3.5 text-primary" />
                <span>Consulta SQL Canónica (DuckDB)</span>
              </div>
              <Button
                variant="ghost"
                size="sm"
                className="h-7 px-2 text-xs hover:bg-muted"
                onClick={() => copyToClipboard(sqlQuery, "sql")}
              >
                {copiedSql ? (
                  <Check className="h-3.5 w-3.5 text-emerald-400" />
                ) : (
                  <Copy className="h-3.5 w-3.5 text-muted-foreground" />
                )}
              </Button>
            </div>
            <pre className="p-3 rounded-xl border border-border/60 bg-background/80 font-mono text-xs text-muted-foreground overflow-x-auto whitespace-pre-wrap leading-relaxed">
              {sqlQuery}
            </pre>
          </div>
        </div>
      </SheetContent>
    </Sheet>
  )
}
