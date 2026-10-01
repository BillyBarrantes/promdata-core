"use client";

// [QW-4] Test fixture (no es funcionalidad de producto).
// Página mínima que inicializa el motor DuckDB-WASM real (mismo código que
// usa cross-filter en producción) y expone el estado vía data-testid para
// que Playwright verifique que el bootstrap sobrevive a la CSP de producción.
// Patrón consistente con el lab existente /qa/crossfilter.

import React, { useEffect, useState } from "react";
import { warmup, query } from "@/lib/duckdb-engine";

type BootStatus = "booting" | "ready" | "error";

export default function DuckDBQAPage() {
  const [status, setStatus] = useState<BootStatus>("booting");
  const [errorMessage, setErrorMessage] = useState<string>("");

  useEffect(() => {
    let cancelled = false;
    // warmup() inicializa worker+WASM+conexión; el query() posterior ejerce
    // el roundtrip completo (isReady() exige tablas cargadas y no aplica
    // a un fixture sin datos).
    warmup()
      .then(() => query("SELECT 1 AS bootstrap_ok"))
      .then((rows) => {
        if (cancelled) return;
        const ok = rows?.[0]?.bootstrap_ok === 1;
        setStatus(ok ? "ready" : "error");
        if (!ok) setErrorMessage(`Unexpected bootstrap query result: ${JSON.stringify(rows)}`);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setStatus("error");
        setErrorMessage(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main className="min-h-screen bg-background p-6">
      <section className="max-w-xl mx-auto rounded-xl border p-4 bg-card space-y-2">
        <h1 className="text-lg font-semibold">QA DuckDB Bootstrap</h1>
        <div className="text-sm">
          Estado:{" "}
          <span data-testid="duckdb-status" className="font-mono font-semibold">
            {status}
          </span>
        </div>
        {errorMessage && (
          <div data-testid="duckdb-error" className="text-xs text-destructive font-mono">
            {errorMessage}
          </div>
        )}
      </section>
    </main>
  );
}
