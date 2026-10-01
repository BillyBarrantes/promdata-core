#!/usr/bin/env python3
"""
RLS Isolation Audit — Sprint 2 / Fase 0
═══════════════════════════════════════
Auditoría REAL de aislamiento multi-tenant sobre el Supabase efectivo.
Crea dos usuarios de prueba efímeros (tenant A y tenant B), siembra fixtures
propiedad de A en las 7 tablas críticas y verifica:

  1. B NO puede SELECT/UPDATE/DELETE ninguna fila de A (cross-tenant).
  2. Anónimo NO puede leer nada.
  3. A SÍ puede leer sus propias filas (control positivo).

Limpieza estricta en `finally`: fixtures y usuarios se eliminan siempre.

Uso:
    cd backend && venv/bin/python scripts/rls_isolation_audit.py
Exit code 0 = todos los intentos cross-tenant fueron denegados.
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent
EVIDENCE_DIR = REPO_ROOT / "docs" / "phase0"

sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND_DIR / ".env")

from supabase import create_client  # noqa: E402

SUPABASE_URL = os.environ["SUPABASE_URL"]
SERVICE_KEY = os.environ["SUPABASE_KEY"]
ANON_KEY = os.environ["SUPABASE_ANON_KEY"]

STAMP = uuid.uuid4().hex[:8]
EMAIL_A = f"rls-audit-a-{STAMP}@promdata-audit.invalid"
EMAIL_B = f"rls-audit-b-{STAMP}@promdata-audit.invalid"
PASSWORD = f"RlsAudit!{STAMP}"

results: list[dict] = []


def record(table: str, action: str, actor: str, expected: str, passed: bool, detail: str) -> None:
    results.append(
        {"table": table, "action": action, "actor": actor,
         "expected": expected, "pass": bool(passed), "detail": detail}
    )
    icon = "✅" if passed else "❌"
    print(f"  {icon} [{table}] {action} como {actor} → {detail}")


def seed_team(admin, user_a_id: str) -> str:
    """Reutiliza el team auto-creado por el trigger on_auth_user_created del
    proyecto (finding F-ENV-1: trigger existe en DB, no en migraciones).
    Crear un segundo team hace no-determinista get_my_team_id() (LIMIT 1
    sin ORDER BY) y rompe el seed de uploaded_files por RLS."""
    # Profile defensivo: team_members.user_id referencia public.profiles(id).
    try:
        admin.table("profiles").upsert({"id": user_a_id}).execute()
    except Exception:
        pass
    time.sleep(1)
    membership = admin.table("team_members").select("team_id").eq("user_id", user_a_id).limit(1).execute()
    if membership.data:
        return membership.data[0]["team_id"]
    # Fallback si el trigger no existiera en otro entorno: crear team manual.
    team = admin.table("teams").insert({"team_name": f"rls-audit-{STAMP}"}).execute()
    team_id = team.data[0]["id"]
    admin.table("team_members").insert({"team_id": team_id, "user_id": user_a_id, "role": "admin"}).execute()
    return team_id


def seed_fixtures(user_a_client, user_a_id: str, team_id: str, admin) -> dict[str, str]:
    """Crea una fila por tabla propiedad de A, actuando COMO A (ownership natural)."""
    ids: dict[str, str] = {}

    r = user_a_client.table("uploaded_files").insert({
        "user_id": user_a_id, "team_id": team_id, "file_name": f"rls_audit_{STAMP}.csv",
        "storage_path": f"audit/{STAMP}/f.csv",
    }).execute()
    ids["uploaded_files"] = r.data[0]["id"]

    r = user_a_client.table("analysis_tasks").insert({
        "id": str(uuid.uuid4()),  # id sin default en tabla: el backend lo genera en app
        "user_id": user_a_id, "file_id": ids["uploaded_files"],
        "prompt": f"rls audit {STAMP}", "status": "completed",
        "results_json": json.dumps({"analysis": "audit"}),
    }).execute()
    ids["analysis_tasks"] = r.data[0]["id"]

    r = user_a_client.table("saved_reports").insert({
        "user_id": user_a_id, "title": f"rls audit {STAMP}",
        "content": json.dumps([]), "file_id": ids["uploaded_files"],
    }).execute()
    ids["saved_reports"] = r.data[0]["id"]

    r = user_a_client.table("chat_messages").insert({
        "user_id": user_a_id, "file_id": ids["uploaded_files"],
        "role": "user", "content": json.dumps({"text": f"rls audit {STAMP}"}),
    }).execute()
    ids["chat_messages"] = r.data[0]["id"]

    r = user_a_client.table("cloud_oauth_connections").insert({
        "user_id": user_a_id, "provider": "google_drive",
        "access_token": f"audit-token-{STAMP}", "scopes": ["audit"],
    }).execute()
    ids["cloud_oauth_connections"] = r.data[0]["id"]

    # knowledge_documents: el flujo real inserta vía service role (backend),
    # nunca directo como usuario — se siembra igual que en producción.
    # NOTA AUDITORÍA: el INSERT policy directo (auth.uid()::text = user_id)
    # rechazó la inserción como dueño (42501) → posible mismatch text/uuid
    # en la policy. FINDING F-RLS-1 (ver reporte).
    r = admin.table("knowledge_documents").insert({
        "user_id": user_a_id, "team_id": team_id, "title": f"rls audit {STAMP}",
        "file_name": f"rls_audit_{STAMP}.pdf",
        "bucket_name": "knowledge-documents",
        "storage_path": f"audit/{STAMP}/doc.pdf",
        "mime_type": "application/pdf",
        "file_size_bytes": 128,
        "source_kind": "pdf",
        "status": "queued",
        "chunk_count": 0,
        "word_count": 0,
        "metadata": {"ingestion_mode": "rls_audit"},
    }).execute()
    ids["knowledge_documents"] = r.data[0]["id"]

    return ids


def main() -> int:
    admin = create_client(SUPABASE_URL, SERVICE_KEY)
    anon = create_client(SUPABASE_URL, ANON_KEY)

    user_a_id = user_b_id = None
    team_id = None
    fixtures: dict[str, str] = {}
    exit_code = 0

    try:
        # ── 1. Usuarios efímeros ─────────────────────────────────────
        a = admin.auth.admin.create_user({"email": EMAIL_A, "password": PASSWORD, "email_confirm": True})
        b = admin.auth.admin.create_user({"email": EMAIL_B, "password": PASSWORD, "email_confirm": True})
        user_a_id, user_b_id = a.user.id, b.user.id
        print(f"tenants de prueba: A={user_a_id[:8]}… B={user_b_id[:8]}…")

        team_id = seed_team(admin, user_a_id)

        client_a = create_client(SUPABASE_URL, ANON_KEY)
        client_a.auth.sign_in_with_password({"email": EMAIL_A, "password": PASSWORD})
        client_b = create_client(SUPABASE_URL, ANON_KEY)
        client_b.auth.sign_in_with_password({"email": EMAIL_B, "password": PASSWORD})

        # ── 2. Fixtures de A (actuando como A) ───────────────────────
        try:
            fixtures = seed_fixtures(client_a, user_a_id, team_id, admin)
            print(f"fixtures sembrados: {list(fixtures)}")
        except Exception as e:
            print(f"❌ FALLO AL SEMBRAR FIXTURES (policy INSERT demasiado estricta?): {e}")
            raise

        # audit_logs se escribe con service role (el backend real lo hace así)
        admin.table("audit_logs").insert({
            "user_id": user_a_id, "event": "rls_audit", "method": "POST",
            "path": "/audit", "status_code": 200,
        }).execute()

        tables = ["analysis_tasks", "uploaded_files", "chat_messages", "saved_reports",
                  "cloud_oauth_connections", "knowledge_documents", "audit_logs"]

        # ── 3. Control positivo: A lee lo suyo ───────────────────────
        for t in tables:
            try:
                if t == "audit_logs":
                    rows = client_a.table(t).select("id").eq("event", "rls_audit").execute().data
                else:
                    rows = client_a.table(t).select("id").eq("id", fixtures[t]).execute().data
                record(t, "SELECT", "A (dueño)", "visible", len(rows) >= 1,
                       f"{len(rows)} fila(s) visible(s)")
            except Exception as e:
                record(t, "SELECT", "A (dueño)", "visible", False, f"error: {e}")

        # ── 4. Cross-tenant: B intenta leer/modificar/borrar lo de A ──
        for t in tables:
            fid = fixtures.get(t)
            try:
                if t == "audit_logs":
                    rows = client_b.table(t).select("id").eq("event", "rls_audit").execute().data
                else:
                    rows = client_b.table(t).select("id").eq("id", fid).execute().data
                record(t, "SELECT", "B (otro tenant)", "denegado/vacío", len(rows) == 0,
                       f"{len(rows)} fila(s) visible(s)")
            except Exception as e:
                record(t, "SELECT", "B (otro tenant)", "denegado/vacío", True, f"error esperado: {type(e).__name__}")

            if t != "audit_logs":  # audit_logs no tiene UPDATE policy de usuario
                try:
                    upd = client_b.table(t).update({"metadata": {"hijack": True}}).eq("id", fid).execute()
                    affected = len(upd.data or [])
                    record(t, "UPDATE", "B", "0 filas", affected == 0, f"{affected} fila(s) afectada(s)")
                except Exception as e:
                    record(t, "UPDATE", "B", "0 filas", True, f"error esperado: {type(e).__name__}")

                try:
                    dele = client_b.table(t).delete().eq("id", fid).execute()
                    affected = len(dele.data or [])
                    record(t, "DELETE", "B", "0 filas", affected == 0, f"{affected} fila(s) afectada(s)")
                except Exception as e:
                    record(t, "DELETE", "B", "0 filas", True, f"error esperado: {type(e).__name__}")

        # ── 5. Anónimo no lee nada ────────────────────────────────────
        for t in tables:
            try:
                rows = anon.table(t).select("id").limit(5).execute().data
                record(t, "SELECT", "anónimo", "denegado/vacío", len(rows) == 0,
                       f"{len(rows)} fila(s) visible(s)")
            except Exception as e:
                record(t, "SELECT", "anónimo", "denegado/vacío", True, f"error esperado: {type(e).__name__}")

        # ── 6. Verificación post-ataque: filas de A intactas ──────────
        for t, fid in fixtures.items():
            rows = admin.table(t).select("id").eq("id", fid).execute().data
            record(t, "INTEGRIDAD", "service (verificación)", "fila intacta", len(rows) == 1,
                   "la fila de A sigue intacta tras intentos de B")

    finally:
        # ── Limpieza estricta ────────────────────────────────────────
        for t, fid in fixtures.items():
            try:
                admin.table(t).delete().eq("id", fid).execute()
            except Exception:
                pass
        try:
            admin.table("audit_logs").delete().eq("event", "rls_audit").execute()
        except Exception:
            pass
        for uid in (user_a_id, user_b_id):
            if uid:
                try:
                    admin.auth.admin.delete_user(uid)
                except Exception:
                    pass
        if team_id:
            try:
                admin.table("teams").delete().eq("id", team_id).execute()
            except Exception:
                pass
        print("limpieza: fixtures, team y usuarios de prueba eliminados")

    passed = sum(1 for r in results if r["pass"])
    failed = [r for r in results if not r["pass"]]
    exit_code = 0 if not failed else 1

    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    evidence = {
        "stamp": STAMP,
        "checks": len(results),
        "passed": passed,
        "failed": len(failed),
        "results": results,
        "verdict": "AISLAMIENTO RLS VERIFICADO — todo intento cross-tenant denegado"
        if not failed else f"FALLOS DE AISLAMIENTO: {len(failed)}",
    }
    (EVIDENCE_DIR / "rls_isolation_audit.json").write_text(json.dumps(evidence, indent=2, ensure_ascii=False))

    print(f"\n{'═' * 60}")
    print(f"  RESULTADO: {passed}/{len(results)} checks OK | veredicto: {evidence['verdict']}")
    print(f"  evidencia: {EVIDENCE_DIR}/rls_isolation_audit.json")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
