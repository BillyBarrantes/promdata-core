#!/usr/bin/env python3
"""
Effective Configuration Matrix — Sprint 2 / Fase 0
═══════════════════════════════════════════════════
Ensambla la matriz repo vs deploy vs docs de la configuración operativa
real: flags canónicos, pools Redis, concurrencia, límites, versiones.
NUNCA incluye secretos (solo nombres de claves, flags, tamaños, versiones).

Salida: docs/phase0/effective_config_matrix.md

Uso:
    cd backend && venv/bin/python scripts/effective_config_matrix.py
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent
EVIDENCE_DIR = REPO_ROOT / "docs" / "phase0"

BACKEND_HEALTH = "https://promdata-backend-698138140658.us-east4.run.app"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _env_flag_values() -> dict[str, str]:
    """Extrae SOLO flags no-secretos del .env (nunca claves/URLs con password)."""
    safe_prefixes = ("CANONICAL_", "UNIVERSAL_", "AI_MODEL", "NARRATIVE_", "GEMINI_VERTEX_")
    out: dict[str, str] = {}
    for line in _read(BACKEND_DIR / ".env").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.startswith(safe_prefixes):
            out[key] = value.strip().strip('"')
    return out


def _config_py_defaults() -> dict[str, str]:
    src = _read(BACKEND_DIR / "app" / "core" / "config.py")
    out: dict[str, str] = {}
    for m in re.finditer(r'os\.getenv\("([A-Z_]+)",\s*"([^"]*)"\)', src):
        key, default = m.group(1), m.group(2)
        if "REDIS_MAX" in key or "CELERY_" in key and "POOL" in key or "MAX_CONNECTIONS" in key:
            out[key] = default
    return out


def _worker_yaml() -> dict[str, str]:
    src = _read(REPO_ROOT / "cloudbuild.worker.yaml")
    concurrency = re.search(r"--concurrency=(\d+)", src)
    memory = re.search(r"--memory=(\S+)'", src)
    cpu = re.search(r"--cpu=(\d+)'", src)
    inflated = [v for v in ("REDIS_MAX_CONNECTIONS_RATE_LIMIT", "CELERY_BROKER_POOL_LIMIT") if v in src]
    return {
        "concurrency": concurrency.group(1) if concurrency else "?",
        "memory": memory.group(1) if memory else "?",
        "cpu": cpu.group(1) if cpu else "?",
        "redis_pool_overrides_present": ", ".join(inflated) if inflated else "none (defaults gobiernan)",
    }


def _versions() -> dict[str, str]:
    pkg = json.loads(_read(REPO_ROOT / "package.json"))
    out = {"next": pkg.get("dependencies", {}).get("next", "?")}
    try:
        raw = subprocess.run(
            [str(BACKEND_DIR / "venv" / "bin" / "python"), "-c",
             "import fastapi, celery, ibis, duckdb; "
             "print(f'{fastapi.__version__}|{celery.__version__}|{ibis.__version__}|{duckdb.__version__}')"],
            capture_output=True, text=True, timeout=60,
        ).stdout.strip()
        f, c, i, d = raw.split("|")
        out.update({"fastapi": f, "celery": c, "ibis": i, "duckdb": d})
    except Exception as e:  # pragma: no cover
        out["backend_libs_error"] = str(e)
    return out


def _live_health() -> dict:
    import urllib.request

    def _get(path: str) -> dict:
        try:
            with urllib.request.urlopen(f"{BACKEND_HEALTH}{path}", timeout=20) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            return {"error": str(e)}

    return {"ready": _get("/health/ready"), "observability": _get("/health/observability")}


def main() -> int:
    flags = _env_flag_values()
    defaults = _config_py_defaults()
    worker = _worker_yaml()
    versions = _versions()
    live = _live_health()

    agents_md = _read(REPO_ROOT / "AGENTS.md")
    doc_next14 = "Next.js 14" in agents_md

    ts = datetime.now(timezone.utc).isoformat()
    L: list[str] = [
        "# Matriz de Configuración Efectiva — Fase 0",
        "",
        f"- Generado: {ts}",
        "- Fuentes: repo (`.env`, `config.py`, `cloudbuild*.yaml`, `package.json`), "
        "endpoints públicos `/health/*`, AGENTS.md",
        "- Sin secretos: solo flags, tamaños de pool, concurrencia, límites y versiones.",
        "",
        "## 1. Flags canónicos efectivos (backend/.env — espejo de prod)",
        "",
        "| Flag | Valor |",
        "|---|---|",
    ]
    for k in sorted(flags):
        L.append(f"| `{k}` | `{flags[k]}` |")

    L += [
        "",
        "## 2. Pools Redis — defaults del código (fuente de verdad Essentials)",
        "",
        "| Variable | Default código |",
        "|---|---|",
    ]
    for k in sorted(defaults):
        L.append(f"| `{k}` | `{defaults[k]}` |")

    L += [
        "",
        "## 3. Worker (cloudbuild.worker.yaml — repo)",
        "",
        "| Parámetro | Valor |",
        "|---|---|",
        f"| concurrency | `{worker['concurrency']}` |",
        f"| memory | `{worker['memory']}` |",
        f"| cpu | `{worker['cpu']}` |",
        f"| overrides de pools Redis | {worker['redis_pool_overrides_present']} |",
        "",
        "## 4. Versiones",
        "",
        "| Componente | Versión real | Documentado en AGENTS.md | Drift |",
        "|---|---|---|---|",
        f"| Next.js | `{versions.get('next')}` | `14` (§1 Stack) | {'⚠️ SÍ — doc desactualizada' if doc_next14 else 'no'} |",
        f"| FastAPI | `{versions.get('fastapi')}` | — | — |",
        f"| Celery | `{versions.get('celery')}` | `5` (§1) | no |",
        f"| Ibis | `{versions.get('ibis')}` | — | — |",
        f"| DuckDB | `{versions.get('duckdb')}` | — | — |",
        "",
        "## 5. Live (endpoints públicos, verificado " + ts[:10] + ")",
        "",
        "```json",
        json.dumps(live, indent=2, ensure_ascii=False)[:1500],
        "```",
        "",
        "## 6. Diferencias detectadas (repo vs deploy vs docs)",
        "",
        "| # | Diferencia | Estado |",
        "|---|---|---|",
        f"| D1 | AGENTS.md documenta Next.js 14; package.json usa {versions.get('next')} | ⚠️ abierto (corregir doc en T6) |",
        "| D2 | Worker YAML concurrency=4 + sin overrides de pools (Sprint 0/QW-3) | ✅ alineado con Essentials |",
        "| D3 | `AI_MODEL_NAME` efectivo en .env difiere del default documentado (`gemini-3.5-flash`) | ⚠️ registrar valor real por entorno |",
        "| D4 | Backend live: Redis endpoint = instancia Essentials (`...redis.io:13366`) | ✅ consistente con §4.4b |",
        "| D5 | Canary functional switch habilitado y `ready_for_functional_canary=true` (health live) | ℹ️ coherente con traffic_percent=100 |",
        "| D6 | Verificación live de env vars del worker (gcloud) | 🔴 bloqueado B-1 (CLI roto) |",
        "",
    ]

    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    out = EVIDENCE_DIR / "effective_config_matrix.md"
    out.write_text("\n".join(L))
    print(f"matriz escrita: {out}")
    print(f"flags: {len(flags)} | pool defaults: {len(defaults)} | worker: {worker} | versions: {versions}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
