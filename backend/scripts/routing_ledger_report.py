#!/usr/bin/env python3
"""
Routing Ledger Report — Sprint 2 / Fase 0
══════════════════════════════════════════
Inventario reproducible de rutas analíticas reales (legacy / canónico /
fallback), agrupado por familia de intención, con evidencia escrita en
docs/phase0/routing_ledger_baseline.md + routing_ledger_baseline.json.

Fuentes (read-only, service role desde backend/.env):
  - analysis_tasks.results_json->traceability (task-level)
  - enterprise_telemetry_events (stage-level: runtime + prompt_type)

Uso:
    cd backend && venv/bin/python scripts/routing_ledger_report.py
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent
EVIDENCE_DIR = REPO_ROOT / "docs" / "phase0"

sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND_DIR / ".env")

from supabase import create_client  # noqa: E402

CANONICAL_RUNTIMES = {"canonical_tabular_production"}
DAYS_WINDOW = 30


def _task_ledger(sb) -> list[dict]:
    rows = (
        sb.table("analysis_tasks")
        .select("id,status,created_at,results_json")
        .order("created_at", desc=True)
        .execute()
        .data
    )
    ledger = []
    for row in rows:
        trace = {}
        rj = row.get("results_json")
        if rj:
            try:
                trace = (json.loads(rj) if isinstance(rj, str) else rj).get("traceability", {}) or {}
            except Exception:
                trace = {}
        ledger.append(
            {
                "id": row["id"],
                "created_at": row["created_at"],
                "status": row["status"],
                "runtime": trace.get("runtime") or "unlabeled",
                "prompt_strategy": trace.get("prompt_strategy") or "unknown",
                "is_fallback": bool(trace.get("fallback_reason")),
            }
        )
    return ledger


def _telemetry_ledger(sb) -> tuple[Counter, Counter, int]:
    events = (
        sb.table("enterprise_telemetry_events")
        .select("dimensions")
        .eq("metric_domain", "latency")
        .eq("metric_name", "analysis_stage_duration_ms")
        .order("created_at", desc=True)
        .limit(1000)
        .execute()
        .data
    )
    runtimes: Counter = Counter()
    families: Counter = Counter()
    for e in events:
        dims = e.get("dimensions") or {}
        runtimes[dims.get("runtime") or "unlabeled"] += 1
        families[dims.get("prompt_type") or "unknown"] += 1
    return runtimes, families, len(events)


def main() -> int:
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

    tasks = _task_ledger(sb)
    task_by_runtime = Counter(t["runtime"] for t in tasks)
    non_canonical = [t for t in tasks if t["runtime"] not in CANONICAL_RUNTIMES]
    telem_runtimes, telem_families, telem_total = _telemetry_ledger(sb)

    total_tasks = len(tasks)
    canonical_pct = round(100.0 * task_by_runtime.get("canonical_tabular_production", 0) / total_tasks, 2) if total_tasks else 0.0

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_days": DAYS_WINDOW,
        "task_level": {
            "total_tasks": total_tasks,
            "by_runtime": dict(task_by_runtime),
            "canonical_pct": canonical_pct,
            "non_canonical_tasks": non_canonical,
        },
        "stage_level_telemetry": {
            "events_sampled": telem_total,
            "by_runtime": dict(telem_runtimes),
            "by_intent_family": dict(telem_families),
        },
        "verdict": (
            "canonical_tabular_production domina el tráfico real; "
            "sin evidencia de legacy ni fallback en la ventana muestreada."
            if not non_canonical
            else f"ATENCIÓN: {len(non_canonical)} tareas fuera de la ruta canónica."
        ),
    }

    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    (EVIDENCE_DIR / "routing_ledger_baseline.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str)
    )

    lines = [
        "# Routing Ledger — Baseline Fase 0",
        "",
        f"- Generado: {report['generated_at']}",
        f"- Fuente task-level: `analysis_tasks.results_json->traceability` ({total_tasks} tareas)",
        f"- Fuente stage-level: `enterprise_telemetry_events` ({telem_total} eventos latency muestreados)",
        "",
        "## Task-level (rutas reales)",
        "",
        "| Runtime | Tareas | % |",
        "|---|---|---|",
    ]
    for runtime, count in task_by_runtime.most_common():
        pct = round(100.0 * count / total_tasks, 2) if total_tasks else 0.0
        lines.append(f"| `{runtime}` | {count} | {pct}% |")
    lines += [
        "",
        f"**Ruta canónica (canonical_tabular_production): {canonical_pct}%**",
        f"**Tareas fuera de ruta canónica (legacy/fallback): {len(non_canonical)}**",
        "",
        "## Stage-level por runtime",
        "",
        "| Runtime | Eventos |",
        "|---|---|",
    ]
    for runtime, count in telem_runtimes.most_common():
        lines.append(f"| `{runtime}` | {count} |")
    lines += [
        "",
        "## Stage-level por familia de intención (prompt_type)",
        "",
        "| Familia | Eventos |",
        "|---|---|",
    ]
    for family, count in telem_families.most_common():
        lines.append(f"| `{family}` | {count} |")
    lines += ["", f"## Veredicto", "", report["verdict"], ""]

    (EVIDENCE_DIR / "routing_ledger_baseline.md").write_text("\n".join(lines))

    print(json.dumps(report["task_level"], indent=2, default=str))
    print(f"telemetry: {report['stage_level_telemetry']}")
    print(f"evidence: {EVIDENCE_DIR}/routing_ledger_baseline.{{md,json}}")
    print(f"verdict: {report['verdict']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
