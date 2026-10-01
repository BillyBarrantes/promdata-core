"use client";

import { cn } from "@/lib/utils";

export interface TemporalProvenance {
    cut_applied?: boolean;
    authority?: string;
    item_count?: number;
}

/**
 * Badge compacto de procedencia temporal (F1).
 *
 * Lee exclusivamente metadatos de linaje (`temporal_provenance`) generados por
 * el backend. No altera `chart_base_filters`, Arrow ni Grid: si no hay traza,
 * retorna `null` y el flujo legacy queda idéntico.
 */
export function TemporalProvenanceBadge({
    provenance,
    className,
}: {
    provenance?: TemporalProvenance | null;
    className?: string;
}) {
    if (!provenance || typeof provenance !== "object") {
        return null;
    }

    const cutApplied = Boolean(provenance.cut_applied);
    const authority = String(provenance.authority || "unknown").toLowerCase();
    const itemCount = Number(provenance.item_count || 0);

    if (!cutApplied && authority === "unknown" && itemCount === 0) {
        return null;
    }

    const label = cutApplied
        ? "Corte inferido (F0)"
        : authority === "explicit"
          ? "Filtro de usuario"
          : "Sin autoridad";

    const tone = cutApplied
        ? "bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-900/20 dark:text-amber-400 dark:border-amber-900/40"
        : authority === "explicit"
          ? "bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-900/20 dark:text-emerald-400 dark:border-emerald-900/40"
          : "bg-muted text-muted-foreground border-border/50";

    return (
        <span
            className={cn(
                "inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[9px] font-medium border",
                tone,
                className
            )}
            title="Procedencia del filtro temporal aplicado por el motor analítico"
        >
            {label}
            {itemCount > 0 ? <span className="opacity-70">· {itemCount}</span> : null}
        </span>
    );
}
