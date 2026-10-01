#!/usr/bin/env python3
"""
Post-Deploy Smoke Test — Sprint 2 / Fase 0
═══════════════════════════════════════════
Smoke REAL contra el entorno desplegado (no solo /health/ready):

  1. Health: /health/ready + /health/observability.
  2. Análisis real: usuario efímero → upload CSV a dash-uploads →
     POST /api/v1/analyze → poll hasta completed → chart_options ≥ 1.
  3. Cross-filter real: toma el Arrow payload del análisis, lo carga en
     DuckDB local (pyarrow) y aplica un filtro real, verificando que las
     filas filtradas son consistentes con los datos del chart.

Limpieza estricta: usuario, team, storage object y filas se eliminan siempre.

Uso:
    cd backend && venv/bin/python scripts/smoke_post_deploy.py [BASE_URL]
Exit 0 = smoke verde.
"""
from __future__ import annotations

import base64
import io
import json
import os
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit
from urllib import request as urlreq
from urllib.error import HTTPError

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND_DIR / ".env")

from supabase import create_client  # noqa: E402

BASE_URL = (
    sys.argv[1]
    if len(sys.argv) > 1
    else (os.getenv("BACKEND_PUBLIC_URL") or "http://localhost:8000")
).strip().rstrip("/")
parsed_base_url = urlsplit(BASE_URL)
if (
    parsed_base_url.scheme not in {"http", "https"}
    or not parsed_base_url.netloc
    or parsed_base_url.path not in {"", "/"}
    or parsed_base_url.query
    or parsed_base_url.fragment
):
    raise SystemExit("BASE_URL debe ser un origen sin path; el smoke agrega /api/v1 y /health por su cuenta.")
SUPABASE_URL = os.environ["SUPABASE_URL"]
SERVICE_KEY = os.environ["SUPABASE_KEY"]
ANON_KEY = os.environ["SUPABASE_ANON_KEY"]

STAMP = uuid.uuid4().hex[:8]
EMAIL = f"smoke-{STAMP}@promdata-audit.invalid"
PASSWORD = f"Smoke!{STAMP}24"
POLL_TIMEOUT_S = 240
POLL_INTERVAL_S = 4

CSV_CONTENT = """fecha,producto,monto
2025-01-05,Alpha,1200
2025-01-12,Beta,850
2025-01-20,Gamma,940
2025-02-03,Alpha,1500
2025-02-11,Beta,720
2025-02-19,Gamma,1100
2025-03-02,Alpha,1350
2025-03-10,Beta,980
2025-03-18,Gamma,1250
2025-04-01,Alpha,1600
2025-04-09,Beta,1050
2025-04-17,Gamma,1180
"""

checks: list[dict] = []


def record(name: str, ok: bool, detail: str) -> None:
    checks.append({"check": name, "pass": bool(ok), "detail": detail})
    print(f"  {'✅' if ok else '❌'} {name} → {detail}")


def api(method: str, path: str, token: str | None = None, payload: dict | None = None) -> tuple[int, dict]:
    req = urlreq.Request(
        f"{BASE_URL}{path}",
        method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    try:
        with urlreq.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except HTTPError as e:
        body = e.read().decode()
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, {"raw": body[:400]}


def main() -> int:
    admin = create_client(SUPABASE_URL, SERVICE_KEY)
    user_id = team_id = file_id = None
    storage_path = None
    task_id = None

    try:
        # ── 1. Health ────────────────────────────────────────────────
        s, body = api("GET", "/health/ready")
        record("health/ready", s == 200 and body.get("status") == "ok", f"HTTP {s}")
        s, body = api("GET", "/health/observability")
        record("health/observability", s == 200 and body.get("sentry", {}).get("enabled") is True, f"HTTP {s}")

        # ── 2. Usuario efímero (team auto-creado por trigger del proyecto) ──
        u = admin.auth.admin.create_user({"email": EMAIL, "password": PASSWORD, "email_confirm": True})
        user_id = u.user.id
        try:
            admin.table("profiles").upsert({"id": user_id}).execute()
        except Exception:
            pass
        # El proyecto tiene un trigger on_auth_user_created (NO presente en
        # migraciones — finding F-ENV-1) que auto-crea team + membership.
        # Se reutiliza ese team: crear otro rompe el scope (LIMIT 1 arbitrario).
        time.sleep(1)
        membership = admin.table("team_members").select("team_id").eq("user_id", user_id).limit(1).execute()
        if not membership.data:
            record("team del usuario", False, "sin membership tras crear usuario")
            return 1
        team_id = membership.data[0]["team_id"]

        client = create_client(SUPABASE_URL, ANON_KEY)
        session = client.auth.sign_in_with_password({"email": EMAIL, "password": PASSWORD})
        token = session.session.access_token

        # ── 3. Upload CSV a dash-uploads + fila uploaded_files ───────
        storage_path = f"{user_id}/{int(time.time() * 1000)}_smoke_{STAMP}.csv"
        admin.storage.from_("dash-uploads").upload(
            storage_path, CSV_CONTENT.encode(), {"content-type": "text/csv"},
        )
        row = client.table("uploaded_files").insert({
            "user_id": user_id, "team_id": team_id,
            "file_name": f"smoke_{STAMP}.csv", "storage_path": storage_path,
        }).execute()
        file_id = row.data[0]["id"]
        record("upload CSV", bool(file_id), f"file_id={file_id[:8]}…")

        # ── 4. Análisis real ─────────────────────────────────────────
        s, body = api("POST", "/api/v1/analyze", token, {
            "file_id": file_id,
            "prompt": "Evolución del monto por producto en el tiempo",
        })
        record("POST /analyze aceptado", s == 202, f"HTTP {s} {str(body)[:160]}")
        task_id = body.get("task_id") or body.get("id")

        final = None
        if task_id:
            deadline = time.time() + POLL_TIMEOUT_S
            while time.time() < deadline:
                s, body = api("GET", f"/api/v1/tasks/{task_id}", token)
                status = body.get("status")
                if status in ("completed", "failed"):
                    final = body
                    break
                time.sleep(POLL_INTERVAL_S)
        record("análisis completado", bool(final and final.get("status") == "completed"),
               f"status={final.get('status') if final else 'timeout'}")

        # ── 5. Contrato de resultado: charts + Arrow payload ─────────
        results = {}
        if final:
            rj = final.get("result") or final.get("results_json") or final.get("results") or {}
            results = json.loads(rj) if isinstance(rj, str) else rj
        charts = results.get("chart_options") or []
        record("chart_options ≥ 1", len(charts) >= 1, f"{len(charts)} chart(s)")

        arrow_b64 = (
            results.get("snapshot_arrow")
            or results.get("arrow_data")
            or (charts[0].get("granular_arrow") if charts else None)
        )
        record("Arrow payload presente (base cross-filter)", bool(arrow_b64),
               f"{'snapshot_arrow' if results.get('snapshot_arrow') else 'arrow_data/granular' if arrow_b64 else 'ausente'}")

        # ── 6. Cross-filter REAL sobre el payload ────────────────────
        if arrow_b64:
            try:
                import duckdb  # local, mismo motor que DuckDB-WASM en browser
                import pyarrow as pa

                raw = base64.b64decode(arrow_b64)
                table = pa.ipc.open_stream(io.BytesIO(raw)).read_all()
                con = duckdb.connect()
                con.register("analysis_data", table)
                total = con.execute("SELECT COUNT(*) FROM analysis_data").fetchone()[0]
                filtered = con.execute(
                    "SELECT COUNT(*) FROM analysis_data WHERE producto = 'Alpha'"
                ).fetchone()[0]
                sums = con.execute(
                    "SELECT SUM(monto) FROM analysis_data WHERE producto = 'Alpha'"
                ).fetchone()[0]
                expected = 1200 + 1500 + 1350 + 1600
                ok = total == 12 and filtered == 4 and int(sums) == expected
                record(
                    "cross-filter real (DuckDB sobre Arrow del análisis)",
                    ok,
                    f"total={total} filtrado={filtered} sum={int(sums)} (esperado 4 / {expected})",
                )
            except Exception as e:
                record("cross-filter real (DuckDB sobre Arrow del análisis)", False, f"{type(e).__name__}: {e}")

    finally:
        # ── Limpieza estricta ────────────────────────────────────────
        try:
            if task_id:
                admin.table("analysis_tasks").delete().eq("id", task_id).execute()
        except Exception:
            pass
        try:
            if file_id:
                admin.table("uploaded_files").delete().eq("id", file_id).execute()
        except Exception:
            pass
        try:
            if storage_path:
                admin.storage.from_("dash-uploads").remove([storage_path])
        except Exception:
            pass
        try:
            if user_id:
                admin.auth.admin.delete_user(user_id)
        except Exception:
            pass
        try:
            if team_id:
                admin.table("teams").delete().eq("id", team_id).execute()
        except Exception:
            pass
        print("limpieza: usuario, team, storage y filas eliminados")

    passed = sum(1 for c in checks if c["pass"])
    failed = [c for c in checks if not c["pass"]]
    print(f"\n{'═' * 60}")
    print(f"  SMOKE: {passed}/{len(checks)} checks OK contra {BASE_URL}")
    for c in failed:
        print(f"  ❌ {c['check']}: {c['detail']}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
