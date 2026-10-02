# PromData — Runbook de rotación de credenciales

> Contexto: durante la auditoría de despliegue se inspeccionó el entorno de
> `promdata-worker` y quedaron visibles credenciales en la salida de la terminal.
> Además, `backend/test_stress_ia.py` tuvo un JWT real en su historial Git.
> **Toda credencial expuesta debe considerarse comprometida y rotarse.**

Este documento es un checklist operativo. **No contiene valores de secretos.**
Las credenciales viven en `backend/.env` (VPS) y en las variables de entorno de
Cloud Run; nunca en el repositorio.

## 1. Inventario a rotar

| Credencial | Dónde se emite | Dónde se actualiza |
|---|---|---|
| Redis (Essentials) password | Redis Cloud Console | `backend/.env` (`CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`, `REDIS_URL`, `RATE_LIMIT_STORAGE_URL`); Cloud Run env vars |
| Supabase `SUPABASE_SERVICE_ROLE_KEY` / `SUPABASE_KEY` | Supabase → Project Settings → API | `backend/.env`; Cloud Run env vars |
| Supabase `SUPABASE_JWT_SECRET` | Supabase → Settings → API → JWT Settings | `backend/.env`; Cloud Run env vars |
| Google OAuth client secret | Google Cloud Console → Credentials | `backend/.env` (`GOOGLE_DRIVE_CLIENT_SECRET`); Cloud Run |
| Microsoft OAuth client secret | Azure Portal → App registrations | `backend/.env` (`MICROSOFT_ONEDRIVE_CLIENT_SECRET`); Cloud Run |
| DeepSeek API key | DeepSeek platform | `backend/.env` (`DEEPSEEK_API_KEY`); Cloud Run |
| Gemini API key | Google AI Studio / Vertex | `backend/.env` (`GEMINI_API_KEY`); Cloud Run |
| Langfuse public/secret | Langfuse project settings | `backend/.env`; Cloud Run |
| Sentry DSN | Sentry project settings | `backend/.env`; Cloud Run |
| `OAUTH_TOKEN_ENCRYPTION_KEY` (Fernet) | generado localmente | `backend/.env`; rotar **invalida** conexiones OAuth existentes |

Generar una nueva clave Fernet:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## 2. Procedimiento por credencial

1. **Emitir la nueva** credencial en el proveedor (no revocar la vieja todavía).
2. **Actualizar** `backend/.env` en el VPS y/o las env vars de Cloud Run.
3. **Reiniciar** `api` y `worker` (VPS: `docker compose -f docker-compose.prod.yml up -d api worker`;
   Cloud Run: nueva revisión).
4. **Validar**: `curl /health/ready|runtime|observability` y un análisis real.
5. **Revocar la credencial vieja** en el proveedor.
6. Registrar la rotación (fecha, operador, sistemas) fuera del repositorio.

Para Redis/Supabase, rotar en una ventana de baja actividad: el cambio obliga a
recrear el pool de conexiones (VPS reinicia contenedores; Cloud Run crea revisión).

## 3. Historial Git (JWT en `test_stress_ia.py`)

- El JWT se eliminó del código (ahora se lee de `SUPABASE_TEST_TOKEN`), pero
  **sigue en el historial** de commits anteriores.
- **Recomendación:** rotar el JWT (sección 1) y **no** reescribir el historial.
  Reescribir con `git filter-repo` cambia todos los SHAs, invalida clones y
  rompe referencias; el beneficio es nulo si la credencial ya se rotó.
- Si se optara por reescribir, hacerlo **después** de rotar y coordinando el
  re-clone de todos los entornos (VPS incluido).

## 4. Validación posterior

- `GET /health/runtime`: `active_provider` = proveedor esperado (`deepseek`).
- `GET /health/observability`: `sentry.enabled=true`, `langfuse.enabled=true`.
- `GET /health/release-candidate`: sin `supabase_jwt_secret_missing`.
- Un análisis E2E real (carga → análisis → cross-filter) sobre el host público.
