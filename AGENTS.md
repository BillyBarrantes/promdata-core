# PromData — Engineering Standard & Operations Manual

> **Audience:** Engineers, AI agents, on-call rotation.
> **Purpose:** Single source of truth for stack, services, configuration,
> architectural decisions, and operational procedures. **Read this file
> before modifying any code, infra, or configuration.**

---

## 1. Stack Overview

| Layer       | Technology                              | Notes                                              |
|-------------|-----------------------------------------|----------------------------------------------------|
| Frontend    | Next.js 14 + TypeScript (App Router)    | Lives at repo root. Deployed as static/Node app.   |
| Backend API | Python 3.11 + FastAPI + Uvicorn         | Lives in `/backend`. Deployed on Cloud Run.        |
| Workers     | Celery 5 + Redis broker + Redis backend | `prefork` pool, `--concurrency=2` (production).    |
| Data        | Ibis + DuckDB (in-process)              | Lazy evaluation; **no** Pandas in hot path.        |
| LLM         | Vertex AI (`google-genai` SDK)          | Default model `gemini-3.5-flash`.                  |
| Auth/DB     | Supabase (Postgres + Auth + Storage)    | Multi-tenant via `tenant_id` / `file_id`.          |
| Cache       | Redis Cloud (Pro 1 plan, 1000 conn / 250MB) | Centralized pool, see §5.                          |
| CI/CD       | Google Cloud Build                      | `cloudbuild.yaml` (backend) + Cloud Build trigger (frontend) + manual worker deploy.|
| Hosting     | Google Cloud Run (`us-east4`)           | 3 services: `promdata-core` (frontend) + `promdata-backend` (FastAPI) + `promdata-worker` (Celery). See §2. |

---

## 2. Services (Cloud Run)

The project has **3 independent deployable units** in Cloud Run, each
managed by a different mechanism. **Do not delete any of them without
reading §10.1 and §10.2 first** — the names overlap with auto-triggers.

### 2.0 `promdata-core` — Frontend (Next.js)
- **Source:** repo root `Dockerfile` (Node 22 + pnpm, Next.js build)
- **Auto-deploy:** **ENABLED** via Cloud Build trigger
  `rmgpgab-promdata-core-us-east4-BillyBarrantes-promdata-core-iym`
  (id `5680bca7-...`). Trigger fires on every `git push` to `main`,
  builds the root Dockerfile, pushes to Artifact Registry
  (`us-east4-docker.pkg.dev/.../promdata-core/promdata-core`), and
  runs `gcloud run services update promdata-core`.
- **Public URL:** `https://promdata-core-698138140658.us-east4.run.app`
  (serves custom domain `livion.lat`)
- **⚠️ Do NOT delete this service manually.** The trigger will recreate
  it on the next push, but in the meantime `livion.lat` will be down.
  See §10.2.
- **Why the HTML says "v0 App":** the frontend was originally
  generated with v0 (Vercel's AI UI builder). This is the legitimate
  production frontend, not a leftover demo.

### 2.1 `promdata-backend` — FastAPI API
- **Source:** `/backend/Dockerfile`
- **Auto-deploy:** **BROKEN AS OF 2026-06-08.** The Cloud Build trigger
  intended for `cloudbuild.yaml` (`promdata-backend-auto-deploy`,
  us-east4) **does not exist** in the project. After a `git push`,
  the image is built and pushed (step 1+2 of `cloudbuild.yaml`) by
  the *worker* trigger (which is wired to the same image), but the
  *backend* service is NOT updated. The backend deploy must be done
  manually with `gcloud run deploy promdata-backend ...` (see §6.7).
  This was confirmed on 2026-06-09 by
  `gcloud builds triggers list` showing only the `promdata-core`
  (frontend) trigger.
- **Public URL:** `https://promdata-backend-698138140658.us-east4.run.app`
- **Image tag:** `gcr.io/$PROJECT_ID/promdata-backend:$COMMIT_SHA`
- **Port:** 8080 (Cloud Run default; uvicorn reads `$PORT`).
- **Min instances:** 1 (no cold starts in prod).
- **Env vars of interest (full list has 42 entries as of 2026-06-09):**
  - `ALLOWED_ORIGINS=https://livion.lat,https://www.livion.lat`
  - `FRONTEND_APP_URL=https://livion.lat`
  - `BACKEND_PUBLIC_URL=https://promdata-backend-698138140658.us-east4.run.app`
  - `SUPABASE_URL`, `SUPABASE_KEY`, `SUPABASE_ANON_KEY`
  - `GEMINI_API_KEY`, `GEMINI_VERTEX_PROJECT`, `GEMINI_VERTEX_LOCATION`
  - `GOOGLE_DRIVE_CLIENT_ID`, `GOOGLE_DRIVE_CLIENT_SECRET`
  - `MICROSOFT_ONEDRIVE_CLIENT_ID`, `MICROSOFT_ONEDRIVE_CLIENT_SECRET`
  - `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`, `REDIS_URL`
  - `SENTRY_DSN`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`
  - `AI_MODEL_NAME`, `NARRATIVE_FAST_MODEL_NAME`, `NARRATIVE_STRICT_MODEL_NAME`
  - 16 `CANONICAL_*` feature flags
  - `UNIVERSAL_TABULAR_PRODUCTION_EXECUTOR_ENABLED`
  - `UNIVERSAL_TABULAR_RESULT_SOFT_LIMIT_BYTES`

### 2.2 `promdata-worker` — Celery worker pool
**NOT** a regular Cloud Run service. It's a **Cloud Run Worker Pool**
(beta), which runs the Celery consumer process long-lived.
- **Source:** same image (`gcr.io/$PROJECT_ID/promdata-backend:$COMMIT_SHA`)
- **Auto-deploy:** **ENABLED** via Cloud Build trigger
  `d4a8317c-0666-4e68-ae81-450eb11aa6d4` (us-east4) pointing at
  `cloudbuild.worker.yaml`. The trigger builds the image, pushes it,
  and runs `gcloud beta run worker-pools deploy promdata-worker`.
  **WARNING (2026-06-09):** the trigger fires on every push to `main`
  because there is no `--branch-pattern` filter excluding
  frontend-only commits. The current `cloudbuild.worker.yaml` deploys
  with `--concurrency=4 --max-tasks-per-child=` (NONE), which violates
  the constraint below. **TODO:** pin to `--concurrency=2
  --max-tasks-per-child=50` and re-create the trigger with a
  path filter.
- **Command override** (set in `cloudbuild.worker.yaml`, currently active):
  ```
  celery -A app.celery_app worker --loglevel=info --pool=prefork \
         --concurrency=4 -Ofair --prefetch-multiplier=1
  ```
  **Currently in safe Pro 1 range.** For local dev, see §2.2.1 below.
- **Why `--concurrency=4`:** Redis Cloud Pro 1 plan has 1000 max connections.
  With `CELERY_BROKER_POOL_LIMIT=30` and the Pro 1 headroom (1000 conn),
  this worker consumes ~30 connections — well within the Pro 1 budget.
  Can be raised to 8-20 when traffic grows.

### 2.3 Local Dev
- `docker compose up` brings up `api`, `worker`, `redis` containers.
- **Recommendation during active dev:** stop the `worker` container and
  run `celery -A app.celery_app worker ...` on the host to avoid two
  workers competing for the local Redis.
- Hot-reload: uvicorn `--reload` for the API only.

---

## 3. Repository Layout

```
/                         # Next.js frontend
  app/                    # App Router pages
  components/             # UI components
  lib/                    # Frontend utilities
  public/                 # Static assets
  cloudbuild.yaml         # Backend CI/CD (only)
/backend                  # FastAPI + Celery
  app/
    core/                 # Config, Redis pool, structured logging
    services/             # Business logic (data engine, semantic, charts, cache)
    tasks/                # Celery tasks (analysis, document, cloud_sync)
    api/                  # FastAPI routers
    main.py               # FastAPI app entrypoint
    celery_app.py         # Celery app + signal hooks
  Dockerfile
  docker-compose.yml
  requirements.txt
  .env (gitignored)
/supabase                 # Migrations + RLS policies
AGENTS.md                 # THIS FILE — read first
cloudbuild.worker.yaml    # Optional: rebuilds worker image (rarely used)
```

---

## 4. Core Architectural Decisions (Read Before Changing)

### 4.1 Data Engine: Ibis + DuckDB
- **Pandas is BANNED in the request hot path** for any operation > 100K rows.
- All transformations use Ibis expressions compiled to DuckDB in-process.
- Schema-agnostic: column roles (metric, dimension, time) are inferred from
  `dtype`, `cardinality`, and statistical properties — **never** from names.
- Guards in `app/services/` (ID Shield, Text Guard, Mixed Content Guard,
  Date Guard, Entropy Sanitization, Snapshot Logic) are **intocables**.
  Extending is allowed; replacing is not, without explicit user override.

### 4.2 Semantic Translator V3
- `app/services/semantic_translator.py:1036-1056` contains the **V3 fallback**
  for exact metric match. Do not remove. It is the safety net when the
  primary allowed-roles match fails.

### 4.3 AI Response Cache v2
- `app/services/ai_response_cache.py` uses `_CACHE_KEY_SCHEMA_VERSION = "v2"`.
- **Two prompts with the same schema but different intent MUST produce
  different cache keys.** See the long docstring at the top of that file
  for the canonical governance rules.
- TTL: 1800s by default. Configurable via `AI_RESPONSE_CACHE_TTL_SECONDS`.
- Graceful degradation: if Redis is unreachable, cache writes/reads are
  skipped silently (logged at `warning` level). **The pipeline never
  blocks on cache failures.**

### 4.4 Redis Pool (Essentials 256-conn — Production-safe defaults)
- Centralized in `app/core/redis_client.py`.
- Three named pools, each with its own connection cap (Essentials-tuned):

  | purpose             | max_connections | env override                                    |
  |---------------------|----------------:|-------------------------------------------------|
  | `rate_limit`        |               6 | `REDIS_MAX_CONNECTIONS_RATE_LIMIT`              |
  | `ai_response_cache` |               6 | `REDIS_MAX_CONNECTIONS_AI_CACHE`                |
  | `healthcheck`       |               2 | `REDIS_MAX_CONNECTIONS_HEALTHCHECK`             |
  | (other)             |               3 | `REDIS_MAX_CONNECTIONS_DEFAULT`                 |

- Celery broker/backend capped at 5 each (Essentials headroom)
  (`CELERY_BROKER_POOL_LIMIT`, `CELERY_RESULT_BACKEND_MAX_CONNECTIONS`).
- **Plan actual:** Redis Cloud Essentials (256 conn / 250MB memory).
  Los defaults del codigo estan calibrados para mantener un techo
  de ~160 conexiones totales con 5 instancias backend (256/5 = 51
  conexiones disponibles por instancia). Ver §4.4b para comparacion
  con Pro 1.
- **Per-child pool reset:** `celery_app.py:_on_celery_worker_init` calls
  `reset_redis_pools()` in the `worker_init` signal. This prevents the
  parent process's sockets from being inherited by child workers after
  `fork()` (which would cause them to be in an invalid state).

### 4.4b Redis Pool: Essentials vs Pro 1 tuning
- **Motivación:** los defaults del código deben ser seguros para el plan
  actual (Essentials, 256 conn) sin requerir env vars de override. Si en
  el futuro se migra a Pro 1 (1000 conn), basta con cambiar los env vars
  en Cloud Run (o actualizar los defaults del código).

| Pool / param              | Essentials (256 conn) — **DEFAULT** | Pro 1 (1000 conn) — via env vars |
|---------------------------|-------------------------------------|----------------------------------|
| `rate_limit`              | 6                                   | 30                               |
| `ai_response_cache`       | 6                                   | 30                               |
| `healthcheck`             | 2                                   | 10                               |
| (other / default)         | 3                                   | 20                               |
| `CELERY_BROKER_POOL_LIMIT`| 5                                   | 30                               |
| `CELERY_RESULT_BACKEND_MAX_CONNECTIONS` | 5                    | 30                               |
| Worker `--concurrency`    | 4                                   | 6+                               |
| Worker `--max-tasks-per-child` | 100                              | 100                              |
| `socket_*_timeout`        | 2.0s                                | 2.0s                             |
| `result_expires`          | 24h                                 | 24h                              |
| Total por instancia backend (estimado) | ~25 conexiones        | ~150 conexiones                 |
| Techo con 5 instancias backend | ~160 conexiones (37% margen vs 256) | ~750 conexiones (25% margen vs 1000) |
| Techo con `max-instances=20` (auto-escala pico) | ~640 (reventaría 256) | ~3000 (sin riesgo)              |

- **Operación actual:** Essentials-tuned defaults. Los env vars de
  Cloud Run **NO deben** inyectar los valores Pro-tuned (30/30/10/20)
  porque eso haría que el backend use esos valores en vez de los
  defaults del código, y reventaría el límite de 256 al escalar.
  Ver §11.5 para la limpieza de env vars.
- **Migración a Pro 1:** cuando llegue el momento, además de cambiar la
  URL de Redis (Free → Pro), hay que actualizar los env vars en Cloud Run
  del backend y worker a los valores de la columna "Pro 1" de arriba.

### 4.5 TLS + Redis Cloud
- `rediss://` URLs are required for Redis Cloud from Cloud Run.
- `ssl_cert_reqs=None` is set both in `redis_client.py` and `celery_app.py`.
- **DO NOT add `socket_keepalive_options` (TCP_KEEPIDLE/INTVL/CNT).**
  Incompatibility confirmed: redis-py applies `setsockopt(TCP_KEEP*)` to
  the socket BEFORE the SSL handshake completes. The socket rejects the
  call with `OSError: [Errno 22] Invalid argument` and the worker can't
  start. If you need fine-grained keepalive tuning, use OS-level sysctls
  (see the long comment in `celery_app.py:16-27`).

### 4.6 Google GenAI SDK
- We use `google-genai` (new SDK). **DO NOT install `google-generativeai`**
  (legacy SDK). Having both causes a namespace conflict where
  `from google import genai` imports the wrong package.
- The library was removed from `requirements.txt` precisely to prevent
  this regression.

### 4.7 Celery Result Expiration (24h)
- `celery_app.conf.result_expires = 24 * 3600` (24 hours).
- Default Celery value is 86400s (1 day). We keep the full day because
  Redis Cloud Pro 1 plan has 250MB of memory — plenty of room for
  task results. The frontend polls task status in the first few
  seconds after submit, so 24h is more than enough for retries and
  late debugging.

---

## 5. Environment Variables (Production Reference)

| Variable                                  | Purpose                                                      | Default                                                |
|-------------------------------------------|--------------------------------------------------------------|--------------------------------------------------------|
| `CELERY_BROKER_URL`                       | Redis broker URL (Celery queue)                              | `redis://localhost:6379/0`                             |
| `CELERY_RESULT_BACKEND`                   | Redis URL for task results                                   | same as broker                                         |
| `RATE_LIMIT_STORAGE_URL`                  | Redis URL for rate-limit counters                            | falls back to `CELERY_BROKER_URL`                      |
| `CELERY_BROKER_POOL_LIMIT`                | Max broker connections per process                           | `30`                                                   |
| `CELERY_RESULT_BACKEND_MAX_CONNECTIONS`   | Max result-backend connections per process                   | `30`                                                   |
| `REDIS_MAX_CONNECTIONS_RATE_LIMIT`        | Pool size: rate_limit                                        | `30`                                                   |
| `REDIS_MAX_CONNECTIONS_AI_CACHE`          | Pool size: ai_response_cache                                 | `30`                                                   |
| `REDIS_MAX_CONNECTIONS_HEALTHCHECK`       | Pool size: healthcheck                                       | `10`                                                   |
| `REDIS_MAX_CONNECTIONS_DEFAULT`           | Pool size: catch-all                                         | `20`                                                   |
| `AI_RESPONSE_CACHE_TTL_SECONDS`           | TTL for AI response cache                                    | `1800`                                                 |
| `GEMINI_API_KEY`                          | Vertex AI / Gemini API key                                   | (empty)                                                |
| `GEMINI_CLIENT_PROVIDER`                  | `genai` (new SDK) or `legacy`                                | `genai`                                                |
| `GEMINI_VERTEX_PROJECT`                   | GCP project for Vertex AI                                    | `promdata-enterprise`                                  |
| `GEMINI_VERTEX_LOCATION`                   | Vertex AI region                                             | `global`                                               |
| `AI_MODEL_NAME`                           | Default model                                                | `gemini-3.5-flash`                                     |
| `SUPABASE_URL` / `SUPABASE_KEY` / etc.    | Supabase config                                              | (empty)                                                |
| `FRONTEND_APP_URL`                        | Frontend URL for CORS / redirects                            | `http://localhost:3000`                                |
| `ALLOWED_ORIGINS`                         | Comma-separated CORS origins (see `cloudbuild.yaml` quoting) | (empty)                                                |

---

## 6. Operational Procedures

### 6.1 Deploying Backend
1. `git add -A && git commit -m "..."`
2. `git push origin main`
3. Cloud Build triggers automatically (`cloudbuild.yaml`).
4. Cloud Build runs 4 steps: `build` → `push` → `deploy` → `verify-deploy`.
5. Monitor deploy in Cloud Console → Cloud Build → History. The
   `verify-deploy` step curls `/health/ready` with 10 retries (~30s).
   If the build fails at this step, the service is missing or unhealthy
   — see §10.1 for recovery procedure.
6. Healthcheck: `curl https://promdata-backend-698138140658.us-east4.run.app/health/ready`.

### 6.2 Redeploying Worker (manual)
The worker does **not** auto-deploy. After pushing a new backend image:
1. Cloud Console → Cloud Run → `promdata-worker` → **Edit & Deploy New Revision**
2. Image: `gcr.io/promdata-enterprise/promdata-backend:$COMMIT_SHA`
3. **Command override:** verify it still contains
   `--concurrency=2 --max-tasks-per-child=50`.
4. Deploy.

### 6.3 Monitoring Redis Connections
From any host with `redis-cli` configured:
```bash
redis-cli -u "$REDIS_URL" CLIENT LIST | wc -l
# Healthy: < 20
# Warning: 20-27
# Critical: > 28 (rate-limit rejections imminent)
```
For a per-purpose breakdown:
```bash
redis-cli -u "$REDIS_URL" CLIENT LIST | grep -oE 'name=[^ ]+' | sort | uniq -c
```

### 6.4 Investigating Task Failures
1. Get the task_id from frontend logs.
2. In Cloud Run logs, filter by the task_id.
3. Common patterns:
   - `redis_pool_init_failed`: Redis is saturated → see §6.3.
   - `429 RESOURCE_EXHAUSTED` from Gemini: rate-limited → reduce concurrency.
   - `Langfuse' object has no attribute 'trace'`: cosmetic; safe to ignore.
4. For deep analysis: `celery -A app.celery_app result <task_id>`.

### 6.5 Restarting Workers (Belt-and-Suspenders)
If Redis is misbehaving or the worker has stale state:
```bash
# Local
pkill -f "celery -A app.celery_app"
celery -A app.celery_app worker --loglevel=info --pool=prefork \
       --concurrency=6 --max-tasks-per-child=100 -Ofair --prefetch-multiplier=1 &

# Production
# Cloud Console → promdata-worker → ⋮ → Delete revision, then redeploy.
```

### 6.6 Cache Invalidation
- Schema version bump (`_CACHE_KEY_SCHEMA_VERSION = "v3"`) is the
  nuclear option. It invalidates all cached AI responses.
- Targeted invalidation: `redis-cli -u "$REDIS_URL" --scan --pattern
  "promdata:ai_cache:v2:*" | xargs -L 100 redis-cli -u "$REDIS_URL" DEL`.
- **Never use FLUSHALL in production.** It wipes rate-limit counters too.

### 6.7 Manual deploy of `promdata-backend` (recovery procedure)
Use this when the Cloud Build auto-deploy trigger is broken, missing,
or you need to roll back to a specific image SHA. **Read §10.4 before
running `gcloud run deploy` with env vars** — `--env-vars-file` and
`--update-env-vars` have **opposite semantics** and a wrong choice
silently wipes the service's credentials.

**Step 1: Extract the full env-var list from a known-good revision.**
```bash
gcloud run revisions describe promdata-backend-00011-d9t \
  --project=promdata-enterprise --region=us-east4 \
  --format="value(spec.containers[0].env)" > /tmp/env_raw.txt
```
The output is a Python repr (`{'name': 'X', 'value': 'Y'};{...}`) that
must be parsed into a YAML file (one `KEY: "value"` per line) before
feeding back to `gcloud run deploy`. Use the parser in
`/tmp/promdata_recovery/` as a reference.

**Step 2: Deploy with the COMPLETE env file.**
```bash
gcloud run deploy promdata-backend \
  --image=gcr.io/promdata-enterprise/promdata-backend:<SHA> \
  --region=us-east4 --platform=managed --allow-unauthenticated \
  --env-vars-file=/tmp/promdata_recovery/env_full.yaml \
  --project=promdata-enterprise
```

**Step 3: Verify.**
```bash
# Must show sentry.enabled=true, langfuse.enabled=true
curl https://promdata-backend-698138140658.us-east4.run.app/health/observability
# Must return 200
curl https://promdata-backend-698138140658.us-east4.run.app/health/ready
# Any auth-protected endpoint must return 401 (NOT 500)
curl https://promdata-backend-698138140658.us-east4.run.app/api/v1/chat/<file_id>
```

**Flag semantics (CRITICAL — see §10.4):**
- `--env-vars-file=FILE`: **REPLACES** the full env-var list with the
  file's contents. If the file has only 2 vars, the other 40 are gone.
- `--update-env-vars=K1=V1,K2=V2`: **ADDS/UPDATES** the listed vars
  and preserves everything else. The `cloudbuild.yaml` step 3 uses
  this flag — that's why the auto-deploy never lost env vars.
- **Rule:** if you don't have a complete env-var file, use
  `--update-env-vars` for the 1-2 vars you actually want to change,
  not `--env-vars-file`.

---

## 7. Anti-Patterns (Forbidden)

| Anti-pattern                                                                 | Why                                                                                       |
|------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------|
| `if col_name == "gastos"`                                                    | Hardcodes user-specific data; breaks for other tenants.                                   |
| `pd.read_csv()` + `.apply(lambda)` in the hot path                           | O(n) Python loop; 100K rows → 30s. Use Ibis.                                              |
| `except: pass`                                                               | Hides bugs. Always log context.                                                            |
| Adding `socket_keepalive_options` to redis client config                      | Breaks SSL handshakes on `rediss://`. See §4.5.                                            |
| Installing `google-generativeai` alongside `google-genai`                     | Namespace conflict. Already removed from requirements.txt — keep it removed.               |
| Raising `--concurrency` above 6 in production (Pro 1)                      | With Pro 1 (1000 conn) and CELERY_BROKER_POOL_LIMIT=30, the worker consumes ~30 conn. Headroom allows 8-20. See §4.4.                  |
| Modifying guards (ID Shield, Text Guard, ...) instead of extending them      | Violates §4.1. Extending is allowed; replacing is not.                                     |
| `FLUSHALL` on production Redis                                               | Wipes rate-limit counters, locks out users. Use `--scan --pattern` instead.                |
| Auto-deploying `promdata-worker` via Cloud Build                             | Worker uses a command override. Auto-deploy would not preserve it. See §2.2.                |

---

## 8. On-Call Runbook (Quick Reference)

| Symptom                                              | First action                                                  |
|------------------------------------------------------|---------------------------------------------------------------|
| Backend returns 503 on `/health/ready`               | `curl` both broker and backend URLs separately with `redis-cli` |
| Frontend shows "rate limit exceeded"                 | `redis-cli CLIENT LIST`; if > 27, restart the worker           |
| Tasks stuck in PENDING forever                       | Worker may be down. `gcloud run services describe promdata-worker` |
| `429 RESOURCE_EXHAUSTED` from Gemini                 | Wait 60s. Consider lowering prompt frequency.                 |
| Slow analysis (>30s for SIMPLE route)                | Check `langfuse` latency; check Redis pool stats via `/health/ready` |
| Chart renders blank after success                    | Frontend bug, not backend. Check browser console.             |

---

## 9. Versioning

- Cache schema: `_CACHE_KEY_SCHEMA_VERSION` in `ai_response_cache.py`.
- Semantic translator: see version comments in `semantic_translator.py`.
- Data engine: see `Version:` docstring at the top of `data_engine*.py`.

When bumping any of these, document the change in the commit message and
in this file (add a row to §9 if it's a breaking change).

---

## 10. Incident Log

### 10.1 Backend service missing (2026-06-06)
- **Symptom:** Frontend on `livion.lat` returned 404 for every
  `/api/v1/*` request because `NEXT_PUBLIC_API_BASE_URL` was pointing to
  a stale Next.js v0 demo (`promdata-core-...run.app`) instead of the
  FastAPI backend.
- **Root cause (compound):**
  1. `cloudbuild.yaml` step 3 (`gcloud run deploy promdata-backend`) had
     not created the service on a recent deploy (image built, but service
     missing from Cloud Run).
  2. The frontend at Vercel had `NEXT_PUBLIC_API_BASE_URL` pointing to
     `promdata-core-...run.app`. **`promdata-core` is the legitimate
     Next.js frontend service** (see §2.0 and §10.2) — it has no API
     routes, so every call to `/api/v1/*` returned 404.
  3. **Historical context:** the FastAPI backend was originally deployed
     to a service also named `promdata-core`. When the user switched
     the backend's auto-deploy to a new service named `promdata-backend`
     in `cloudbuild.yaml`, the Vercel-managed Cloud Build trigger (see
     §10.2) kept rebuilding the frontend into the same `promdata-core`
     service — overwriting any backend that had been there. The deploy
     step in `cloudbuild.yaml` then failed silently on the new service
     name, leaving `promdata-backend` uncreated. The Vercel env var
     still pointed at `promdata-core` (the now-frontend service).
- **Resolution:**
  1. Manually created `promdata-backend` Cloud Run service from image
     `gcr.io/promdata-enterprise/promdata-backend:8a15bb7f` (the SHA
     that contains the `result_expires=12h` change).
  2. Set `BACKEND_PUBLIC_URL` in the service env vars.
  3. Updated `NEXT_PUBLIC_API_BASE_URL` in Vercel to
     `https://promdata-backend-698138140658.us-east4.run.app` and
     triggered a manual redeploy (NEXT_PUBLIC_* requires rebuild).
  4. Healthcheck confirmed 200 OK.
- **Preventive action (implemented 2026-06-07):** Added step 4 to
  `cloudbuild.yaml` (`verify-deploy`). After the deploy, the build
  queries the service URL via `gcloud run services describe` and
  `curl`s `/health/ready` with 10 retries (3s apart, ~30s total). If
  the service is missing or unhealthy, the build fails immediately so
  the engineer notices in Cloud Build History instead of waiting for
  the frontend to break in production.

### 10.2 `promdata-core` is the FRONTEND, not an orphan (2026-06-07)
- **Symptom:** During incident 10.1 investigation, `promdata-core` was
  misidentified as a leftover Next.js v0 demo and was deleted.
- **Reality:** A Cloud Build trigger exists in the project
  (id `5680bca7-8a67-41a6-a8ec-59c0d76740e5`,
  name `rmgpgab-promdata-core-us-east4-BillyBarrantes-promdata-core-iym`)
  that fires on every push to `main` and deploys the **root `Dockerfile`**
  (the Next.js frontend) to the `promdata-core` service. `promdata-core`
  is the legitimate production frontend that serves `livion.lat` (via
  custom domain mapping).
- **Auto-recreation:** Within ~5 minutes of the deletion, the trigger
  fired on the next push and rebuilt the service. Net downtime of
  `livion.lat`: ~5 minutes.
- **Lesson learned:** **Never delete a Cloud Run service in this project
  without first checking `gcloud builds triggers list`**. A trigger may
  auto-recreate it, and a brief outage of the legitimate production
  service is the worst-case outcome.
- **Update in this doc:** §2.0 was added to document `promdata-core`
  as the production frontend, with a prominent "do NOT delete" warning.

### 10.4 Backend wiped env vars via `--env-vars-file` in manual deploy (2026-06-09)
- **Symptom:** Systemic 500 errors on ALL backend endpoints from
  `livion.lat`: `/analyze`, `/chat`, `/connectors/providers`,
  `/connectors/watchdog/status`. Sentry captured **nothing** (the
  SDK never initialized because `SENTRY_DSN` was missing).
  Browser console showed all 4 endpoints returning
  `{"detail":"Error interno del servidor. Por favor, inténtelo de nuevo."}`.
- **Root cause:** A manual `gcloud run deploy promdata-backend` was
  issued to apply the supabase-py fix (commit `5f542174`). The
  command used `--env-vars-file=/tmp/cloud_run_env.yaml`, but that
  file contained **only 2 env vars** (`ALLOWED_ORIGINS`,
  `FRONTEND_APP_URL`). Per gcloud semantics,
  `--env-vars-file=FILE` **REPLACES** the entire env-var list with
  the file's contents — the other 40 env vars (`SUPABASE_URL`,
  `SUPABASE_KEY`, `SUPABASE_ANON_KEY`, `GEMINI_*`, `GOOGLE_DRIVE_*`,
  `MICROSOFT_ONEDRIVE_*`, `CELERY_*`, `REDIS_URL`, `SENTRY_DSN`,
  `LANGFUSE_*`, 16 `CANONICAL_*`, etc.) were silently dropped.
  Cloud Run accepted the deploy without warning.
- **Detection:** Log query via
  `gcloud logging read "resource.type=cloud_run_revision AND
  resource.labels.service_name=promdata-backend" --limit=10`
  revealed the exact error in stderr:
  ```json
  {"error":"supabase_url is required",
   "event":"api_chat_history_error",
   "status_code":500}
  ```
  Confirmed by `gcloud run revisions describe promdata-backend-00012-n95
  --format="value(spec.containers[0].env)"` showing only 2 env vars
  (vs 42 in the prior functional revision `00011-d9t`).
- **Resolution (no code changes):**
  1. Extracted the full env-var list from `00011-d9t` (functional):
     ```bash
     gcloud run revisions describe promdata-backend-00011-d9t \
       --format="value(spec.containers[0].env)" > /tmp/env_raw.txt
     ```
  2. Parsed the Python repr into a clean YAML file with all 42 vars.
  3. Re-deployed the **same** image (5f542174) with the **complete**
     env file:
     ```bash
     gcloud run deploy promdata-backend \
       --image=gcr.io/promdata-enterprise/promdata-backend:5f542174... \
       --env-vars-file=/tmp/promdata_recovery/env_full.yaml
     ```
  4. Result: revision `promdata-backend-00013-9cf` with 42 env vars
     and `/health/observability` showing `sentry.enabled=true,
     langfuse.enabled=true`.
- **Lesson learned (CRITICAL for future manual deploys):**
  - `--env-vars-file=FILE` **REPLACES** the full env-var list.
  - `--update-env-vars=K1=V1,K2=V2` **ADDS/UPDATES** and preserves
    everything else. The `cloudbuild.yaml` step 3 uses this flag
    (which is why the auto-deploy never lost env vars).
  - **Rule:** when manually deploying with a complete env file,
    use `--env-vars-file` with ALL vars. When changing 1-2 vars,
    use `--update-env-vars`. Never mix them.
  - **Sanity check after ANY manual deploy:**
    `gcloud run revisions describe <NEW_REV> --format="value(spec.containers[0].env)"`
    must show the same env-var count as the prior functional revision.
  - If you don't have a complete env-var file, the only safe path is
    `gcloud run services update <service> --update-env-vars=...`
    which is purely additive.
- **Update in this doc:**
  - §6.7 added: step-by-step manual deploy procedure with the
    gotcha prominently flagged.
  - §2.1 updated: `--update-env-vars` vs `--env-vars-file` semantics
    now documented in the deploy context.
  - §2.1 also documents that the
    `promdata-backend-auto-deploy` trigger (us-east4) is missing —
    manual deploys are the norm until the trigger is re-created.

---

## 11. Approved Skills Catalog

The `.agents/skills/` directory contains context bundles that AI coding
agents load on-demand. **Not all skills are equal** — some are first-party
official (e.g., `shadcn`, `supabase-postgres-best-practices`), some are
community-contributed, and some contain prompt-injection patterns
designed to manipulate the agent.

This section is the **canonical allow-list**. Before installing a new
skill or trusting an existing one, read §11.3 and §11.4.

### 11.1 Approved (17)

| Skill | Why it's approved | When to load |
|---|---|---|
| `promdata-engineering-standard` | **Project-specific.** Mandatory. Encodes the fortress rules from §1–§4. | ALWAYS on PromData work. |
| `shadcn` | First-party. `allowed-tools` limited to `shadcn@latest` CLI. License MIT. | Adding/fixing shadcn/ui components. |
| `supabase-postgres-best-practices` | First-party (Supabase). License MIT. PromData uses Supabase intensively. | Writing/optimizing SQL, schema, RLS policies. |
| `playwright-best-practices` | Community. License MIT. No executable `allowed-tools`. | E2E testing, debug, CI setup. |
| `bash-defensive-patterns` | Community. Fomenta `set -Eeuo pipefail`. Improves `cloudbuild.yaml` step quality. | Writing CI/CD or admin scripts. |
| `react-best-practices` | Community (Vercel Engineering). License MIT. | React/Next.js performance work. |
| `react-hook-form` | Community. Library-specific best practices. | Client-side forms. |
| `composition-patterns` | Community (Vercel). React composition patterns. | Refactoring boolean-prop-heavy components. |
| `next-best-practices` | Community. Next.js conventions, RSC, async APIs. | Any Next.js work. |
| `next-cache-components` | Community. Next.js 16 cache components, PPR, `use cache`. | Caching work on Next.js 16+. |
| `next-upgrade` | Community. Codemods + migration guides. | Upgrading Next.js versions. |
| `tailwind-css-patterns` | Community. Comprehensive Tailwind v3 + v4 patterns. | Tailwind utility usage, layouts, design systems. |
| `zod` | Community. Zod schema validation best practices. | Defining `z.object` schemas, `safeParse`, `z.infer`. |
| `typescript-advanced-types` | Community. Generics, conditional types, template literals. | Complex type logic. |
| `accessibility` | Community. WCAG 2.2 guidance. | a11y audits, keyboard nav, screen reader support. |
| `seo` | Community. Meta tags, sitemaps, structured data. | SEO improvements. |
| `frontend-design` | Community. Distinctive UI design patterns. | Building polished UI components. |

### 11.2 Removed (do NOT re-install)

| Skill | Removed on | Reason |
|---|---|---|
| `tailwind-v4-shadcn` | 2026-06-07 | **Prompt injection in SKILL.md.** Section "BEFORE YOU START" instructs AI agents to always state they are using the skill, prefer its content over general knowledge, and pressure users to invoke it. Fabricated metrics ("70% token reduction", "0 errors", "1 minute setup") with no source. The technical content was correct but the marketing manipulation made the skill unsafe to trust blindly. **Replaced by:** `tailwind-css-patterns` (Tailwind) + `shadcn` (UI). |
| `nodejs-best-practices` | 2026-06-07 | **Not applicable.** PromData backend is Python/FastAPI, not Node.js. Loading this skill risks the agent applying Node.js patterns to Python code. |
| `nodejs-backend-patterns` | 2026-06-07 | **Not applicable.** Same reason as above. |

### 11.3 Decision matrix (what to load per task)

| Task | Skills to load |
|---|---|
| **UI component** (React/Next.js) | `react-best-practices` + `shadcn` + `composition-patterns` + `tailwind-css-patterns` |
| **Form (client-side)** | `react-hook-form` + `zod` + `react-best-practices` |
| **Tailwind styling** | `tailwind-css-patterns` |
| **shadcn/ui component** | `shadcn` (primary) + `react-best-practices` |
| **Next.js page/route** | `next-best-practices` + `next-cache-components` (if Next 16+) |
| **Next.js upgrade** | `next-upgrade` |
| **SQL / RLS / Supabase schema** | `supabase-postgres-best-practices` |
| **E2E test** | `playwright-best-practices` |
| **CI/CD / shell script** | `bash-defensive-patterns` |
| **TypeScript types** | `typescript-advanced-types` + `zod` |
| **a11y audit** | `accessibility` |
| **SEO work** | `seo` |
| **Any PromData work** | `promdata-engineering-standard` (always) |

### 11.4 Governance (rules for adding/removing skills)

1. **Before installing a new skill:** read its `SKILL.md` fully. Check for:
   - Prompt-injection patterns (instructions to the AI agent on how to
     talk about itself, "USER ACTION REQUIRED", fabricated metrics).
   - Unclear or missing `license` field.
   - `allowed-tools` broader than necessary (e.g., blanket `Bash` access).
2. **If the skill contains prompt-injection:** do NOT install. Document
   the reason in this section (§11.2) and propose a non-malicious
   alternative.
3. **If the skill is a duplicate of an existing one:** keep the one
   with the cleaner license + no `allowed-tools`. Document the
   removal.
4. **Periodically (quarterly):** re-audit this catalog. Remove skills
   that have been superseded or are no longer applicable.
5. **Skills are tooling, not application code.** They live in
   `.agents/skills/` which is intentionally **not** in `.gitignore` for
   the approved list — version-controlling the curated allow-list lets
   new engineers/agents reproduce the setup. Skills removed from
   approval should also be removed from disk.

### 11.5 Redis Cloud plan tuning: Essentials (256 conn) vs Pro 1 (1000 conn)

**Estado actual (2026-06-10):** el código está calibrado para Redis Cloud
**Essentials** (256 conn / 250MB memory). Los defaults de `redis_client.py`
y `config.py` reflejan este plan: techo de ~160 conexiones totales
con 5 instancias backend (37% de margen vs el límite de 256). Ver
§4.4b para la tabla comparativa Essentials vs Pro 1.

**Importante:** los env vars de Cloud Run **NO deben** inyectar los
valores Pro-tuned (30/30/10/20) porque eso haría que el backend use
esos valores en vez de los defaults del código, y reventaría el límite
de 256 al escalar. Si los env vars inflados están activos, hay que
eliminarlos con `--remove-env-vars` (ver comandos al final de esta
sección).

**Cómo migrar al Pro 1 cuando llegue el momento:**

1. **Provisionar la DB Pro 1 en Redis Cloud console** (15-20 min):
   - New database → Pro plans tab → Pro 1 (1000 conn)
   - Region: us-east4 (misma que Cloud Run)
   - Persistence: SSD (RDB)
   - Anotar: endpoint público + password

2. **Actualizar `.env` local + .env.example:**
   ```bash
   # Essentials (actual)
   REDIS_URL=redis://default:CIJFa2G8d6uOqteanaY7nn55qWBRmbW3@grape-cloth-driftwood-89364.db.redis.io:13366/0
   # Pro 1 (futuro)
   REDIS_URL=rediss://default:<password>@redis-12345.c12345.us-east4-1.gcp.cloud.redislabs.com:12345
   ```
   ⚠️ Importante: el esquema cambia de `redis://` a `rediss://` (TLS).

3. **Migrar producción gradualmente (worker → backend):**
   - Editar `promdata-worker` en Cloud Run → cambiar
     `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` a la URL Pro
   - Validar 5+ tareas sin errores antes de continuar
   - Repetir para `promdata-backend` (también `REDIS_URL`,
     `RATE_LIMIT_STORAGE_URL`)

4. **Actualizar los env vars de pool en Cloud Run (NO solo la URL):**
   - Sin este paso, los defaults Essentials-tuned (6/6/2/3) no se activan
   - Backend: `--update-env-vars=REDIS_MAX_CONNECTIONS_RATE_LIMIT=30,...`
     con los valores de la columna "Pro 1" de §4.4b
   - Worker: igual

5. **Cleanup 7 días después:**
   - Eliminar la DB Essentials de Redis Cloud console
   - Actualizar los defaults del código a Pro 1
   - Documentar la migración en §10.3

**Comandos de limpieza de producción (estado actual Essentials):**
```bash
# Backend: eliminar env vars inflados Pro-tuned para forzar defaults Essentials
gcloud run services update promdata-backend \
  --region=us-east4 --project=promdata-enterprise \
  --remove-env-vars=REDIS_MAX_CONNECTIONS_RATE_LIMIT,REDIS_MAX_CONNECTIONS_AI_CACHE,REDIS_MAX_CONNECTIONS_HEALTHCHECK,REDIS_MAX_CONNECTIONS_DEFAULT,CELERY_BROKER_POOL_LIMIT,CELERY_RESULT_BACKEND_MAX_CONNECTIONS

# Worker: igual
gcloud beta run worker-pools update promdata-worker \
  --region=us-east4 --project=promdata-enterprise \
  --remove-env-vars=REDIS_MAX_CONNECTIONS_RATE_LIMIT,REDIS_MAX_CONNECTIONS_AI_CACHE,REDIS_MAX_CONNECTIONS_HEALTHCHECK,REDIS_MAX_CONNECTIONS_DEFAULT,CELERY_BROKER_POOL_LIMIT,CELERY_RESULT_BACKEND_MAX_CONNECTIONS
```

**Regla de oro:** "Primero se paga el plan grande, luego se sube la
variable." No subir los pool limits antes de migrar la URL — sin el
plan Pro, el sistema Essentials colapsa.

1. **Provisionar la DB Pro 1 en Redis Cloud console** (15-20 min):
   - New database → Pro plans tab → Pro 1 (1000 conn)
   - Region: us-east4 (misma que Cloud Run)
   - Persistence: SSD (RDB)
   - Anotar: endpoint público + password

2. **Actualizar `.env` local + .env.example:**
   ```bash
   # Free (actual)
   REDIS_URL=redis://default:CIJFa2G8d6uOqteanaY7nn55qWBRmbW3@grape-cloth-driftwood-89364.db.redis.io:13366/0
   # Pro 1 (futuro)
   REDIS_URL=rediss://default:<password>@redis-12345.c12345.us-east4-1.gcp.cloud.redislabs.com:12345
   ```
   ⚠️ Importante: el esquema cambia de `redis://` a `rediss://` (TLS).

3. **Migrar producción gradualmente (worker → backend):**
   - Editar `promdata-worker` en Cloud Run → cambiar
     `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` a la URL Pro
   - Validar 5+ tareas sin errores antes de continuar
   - Repetir para `promdata-backend` (también `REDIS_URL`,
     `RATE_LIMIT_STORAGE_URL`)

4. **Cleanup 7 días después:**
   - Eliminar la DB Free de Redis Cloud console
   - Eliminar referencias a "Free" de AGENTS.md
   - Documentar la migración en §10.3

**Regla de oro:** "Primero se paga el plan grande, luego se sube la
variable." No subir `--concurrency` ni los pool limits antes de
migrar la URL — sin el plan Pro, el sistema Free colapsa.

---

## 12. Escalabilidad a 1000 Usuarios — Roadmap Completo

**Estado actual (2026-06-10):** el sistema está calibrado con defaults
Essentials-tuned (techo de 160 conexiones) y soporta cómodamente
**150-250 usuarios simultáneos** con 37% de margen. Esto cubre la
prueba real de usuarios actual.

**Objetivo:** cuando el tráfico lo justifique, escalar a 1000 usuarios
concurrentes sin reescribir código. La estrategia es **3 fases
secuenciales** que solo requieren cambios de configuración en la
nube (no cambios de código).

### 12.1 Estado actual por capa (250 usuarios)

| Capa | Configuración actual | Capacidad estimada | Status |
|---|---|---|---|
| **Redis** | Essentials 250MB, 256 conn, 11 conn activas | 160 conn totales (5 instancias backend) | ✅ Listo |
| **Backend Cloud Run** | `max-instances=20`, `min-instances=0`, 1GB RAM | 20 instancias × 150 conn = 3000 conn | ⚠️ Saturaría Redis |
| **Worker** | `concurrency=4`, `max-tasks-per-child=50`, 2GB | 4 procesos × 30 conn = 120 conn | ✅ Conservador |
| **Vertex AI** | 60 RPM (default) | 60 RPM | 🔴 Pendiente (manual) |
| **Supabase** | Plan actual (Free/Pro) | Sin medir | 🔴 Medir primero |
| **Frontend Vercel** | Auto-scale | Sin límite práctico | ✅ Listo |
| **Triggers CI/CD** | Backend (con quota intermitente), Worker | OK | ✅ Listo |
| **Sentry + Langfuse** | Activos con tracing | Sin cambios | ✅ Listo |

**Veredicto:** 90% listo. Solo 2 pendientes manuales (Vertex AI quota
y Supabase plan). El código no requiere cambios.

### 12.2 Fase A — Pre-requisitos manuales (1-2 semanas antes del lanzamiento)

**Objetivo:** desbloquear los cuellos de botella no relacionados con
infraestructura antes de invertir en Pro 1.

#### A.1 Solicitar cuota de Vertex AI (10 min + 1-3 días aprobación)

**Síntoma si no se hace:** 429 RESOURCE_EXHAUSTED en picos de tráfico.

1. Ve a https://console.cloud.google.com/
2. Selecciona proyecto `promdata-enterprise`
3. Barra de búsqueda: **"IAM & Admin"** → click en resultado
4. Menú lateral: **"Quotas & System Limits"**
5. Filtro: **"Vertex AI API requests per minute"**
6. Selecciona la región `us-east4`
7. Click **"EDIT QUOTAS"** (ícono de lápiz)
8. New value: **`600`**
9. Justificación:
   ```
   "Cloud Run production workload using Vertex AI (Gemini 3.5 Flash)
   for natural language data analysis. Need to increase RPM quota
   to support up to 1000 concurrent users without hitting 60 RPM
   default limit. Analytics platform with SQL/aggregation plan
   generation."
   ```
10. Click **"Submit"** → esperar aprobación

**Costo:** $0 inmediato. Solo pagas por uso real más allá de 60 RPM.

#### A.2 Medir Supabase con tráfico real (1 semana de monitoreo)

**Síntoma si no se hace:** connection limit reached, bandwidth exceeded.

1. Lanzar prueba con 50-100 usuarios reales durante 1 semana
2. Monitorear en Supabase Dashboard → Logs → Database
3. Métricas a observar:
   - Conexiones activas (límite Free: 60, Pro: 200+)
   - Bandwidth (límite Free: 2GB, Pro: 250GB)
   - Query time p95 (objetivo: <100ms)
4. **Si ves límites cerca del 80%** durante la prueba:
   - Upgrade a Supabase Pro ($25/mes) → 250GB storage, 250GB bandwidth
5. **Optimizar queries (opcional pero recomendado):**
   - Índices en `analysis_tasks(user_id)` (columna usada en `WHERE user_id=eq...`)
   - Índices en `chat_messages(file_id, created_at)` (usado en ORDER BY)
   - Índices en `cloud_oauth_connections(user_id, status)` (filtro común)

**Costo:** $0 si Free aguanta. $25/mes si necesita Pro.

### 12.3 Fase B — Migración a Pro 1 (cuando tráfico >500 usuarios)

**Objetivo:** desbloquear 1000 conexiones Redis + escalar backend/worker
para sostener 1000 usuarios concurrentes.

**Trigger para ejecutar esta fase:** cuando las métricas indiquen
>200 conexiones Redis activas O >500 usuarios concurrentes O latencia
backend p95 >500ms.

#### B.1 Provisionar DB Pro 1 en Redis Cloud (15-20 min)

1. https://cloud.redislabs.com/ → Login
2. **"Databases"** → **"New database"**
3. Tab **"Pro plans"** → seleccionar **"Pro 1"** (1000 conn, 250MB)
4. **Cloud vendor:** Google Cloud
5. **Region:** `us-east4 (Iowa)` ← misma que Cloud Run
6. **Persistence:** Storage type SSD (RDB)
7. **Naming:** `promdata-prod-us-east4` (o el nombre que prefieras)
8. Click **"Create database"** → esperar 5-10 min
9. Anotar credenciales (host, port, password)

**Costo:** ~$5-15/mes (varía según cloud vendor).

#### B.2 Migrar worker a Pro 1 (5 min, riesgo bajo)

1. **Por qué worker primero:** si falla, las tareas se acumulan en
   Redis Free (no se pierden) sin afectar a los usuarios.
2. Cloud Console → `promdata-worker` → Edit & Deploy New Revision
3. Cambiar env vars:
   - `CELERY_BROKER_URL`: `redis://...free...` → `rediss://...pro...`
   - `CELERY_RESULT_BACKEND`: misma URL Pro
4. Deploy → esperar 1-2 min
5. Verificar logs: buscar `redis_pool_initialized` con valores Pro
6. Smoke test: lanzar 1 tarea desde `livion.lat`

**Criterio GO/NO-GO:**
- ✅ Worker procesó 5+ tareas sin errores
- ✅ Logs no muestran `redis_pool_init_failed`
- ❌ Si falla: revertir cambiando env vars de vuelta a Free

#### B.3 Migrar backend a Pro 1 (5 min, riesgo medio)

1. Cloud Console → `promdata-backend` → Edit & Deploy New Revision
2. Cambiar env vars:
   - `CELERY_BROKER_URL`: `rediss://...pro...` (mismo que worker)
   - `CELERY_RESULT_BACKEND`: misma URL Pro
   - `RATE_LIMIT_STORAGE_URL`: misma URL Pro
3. **ACTUALIZAR env vars de pool a Pro-tuned** (sino el sistema sigue en Essentials-tuned):
   ```bash
   gcloud run services update promdata-backend \
     --region=us-east4 --project=promdata-enterprise \
     --update-env-vars=^|^REDIS_MAX_CONNECTIONS_RATE_LIMIT=30|REDIS_MAX_CONNECTIONS_AI_CACHE=30|REDIS_MAX_CONNECTIONS_HEALTHCHECK=10|REDIS_MAX_CONNECTIONS_DEFAULT=20|CELERY_BROKER_POOL_LIMIT=30|CELERY_RESULT_BACKEND_MAX_CONNECTIONS=30
   ```
4. Healthcheck: `curl /health/ready` → 200 OK
5. Smoke test E2E: login en `livion.lat`, prompt simple, validar chart
6. Verificar `redis-cli CLIENT LIST | wc -l` → entre 20-50 (no debe estar saturado)

#### B.4 Escalar backend Cloud Run (5 min)

1. Cloud Console → `promdata-backend` → Edit & Deploy
2. **Subir `max-instances` de 20 a 30-50:**
   - Con Pro 1 (1000 conn), 30 instancias × 30 conn = 900 conn (justo)
   - Recomendar empezar con 30, monitorear, subir a 50 si hace falta
3. **Configurar `min-instances=2-3`** (eliminar cold starts):
   - Costo: ~$30/mes (3 instancias always-on × 1GB × 730h)
   - Beneficio: 0 cold starts para el primer usuario
4. Deploy → esperar 1-2 min

**Costo adicional:** ~$30-50/mes (3-5 instancias always-on).

#### B.5 Escalar worker concurrencia (5 min)

1. Editar `cloudbuild.worker.yaml`:
   ```yaml
   - '--args=-A,app.celery_app,worker,--loglevel=info,--pool=prefork,--concurrency=16,--max-tasks-per-child=50,-Ofair,--prefetch-multiplier=1'
   ```
   (cambiar `--concurrency=4` a `--concurrency=16`)
2. Commit + push → trigger dispara redeploy del worker
3. Verificar: `promdata-worker-00036+` con nueva concurrencia
4. Monitorear memoria: con 16 procesos × 150MB = 2.4GB (cerca del límite 2GB)
   - Si OOM: subir memoria a 4GB (`--memory=4Gi` en `cloudbuild.worker.yaml`)

#### B.6 Monitoreo intensivo 24h

| Métrica | Umbral OK | Acción si excede |
|---|---|---|
| Conexiones Pro activas | <200 (20% capacidad) | Investigar, posible leak |
| Memoria Pro usada | <50MB (50% de 100MB) | Revisar TTLs |
| Latencia backend p99 | <500ms | Revisar queries Ibis lentas |
| Worker queue length | <10 | Escalar worker concurrencia |
| 5xx rate | <1% | Revisar logs Sentry |
| Vertex AI 429 | <5% | Solicitar más cuota |

Comando de monitoreo continuo:
```bash
watch -n 60 '
  redis-cli -h <pro-host> -p <pro-port> --tls -a "<pro-password>" \
    INFO stats | grep -E "used_memory|total_connections|evicted_keys"
'
```

### 12.4 Fase C — Cleanup + optimizaciones opcionales (5-7 días después de Fase B)

**Objetivo:** limpiar recursos legacy y aplicar optimizaciones de costo.

#### C.1 Cleanup de Redis Essentials (5 min)

**Cuándo:** 7 días después de Fase B, cuando se confirma que Pro 1
está estable.

1. Verificar que NO hay tareas pendientes en Redis Free:
   ```bash
   redis-cli -h grape-cloth-driftwood-89364.db.redis.io -p 13366 \
     -a '<free-password>' LLEN celery
   ```
   **Esperado:** 0
2. Backup final de Free (export con `redis-cli --pipe` o RDB download)
3. Eliminar DB Free en Redis Cloud console
4. Actualizar `backend/.env` local con la URL Pro
5. Actualizar defaults del código a Pro-tuned (commit chore):
   - `redis_client.py`: `_PURPOSE_MAX_CONNECTIONS` → 30/30/10
   - `redis_client.py`: `_resolve_max_connections` default → 20
   - `config.py`: defaults → 30/30/10/20/30/30
   - `docker-compose.yml`: `--concurrency=16`
6. Documentar en `AGENTS.md §10.3` la migración completada

#### C.2 Optimizaciones opcionales de costo

| Optimización | Beneficio | Costo | Esfuerzo |
|---|---|---|---|
| **VPC peering con Redis** | -5-10ms latencia | $0 (config GCP) | 2h setup |
| **Cloud CDN para assets estáticos** | -30% bandwidth Vercel | $0-20/mes | 30 min |
| **Cache de queries Ibis en Redis (TTL 5min)** | -50% CPU backend | $0 (código) | 4h código |
| **Rate limiting por usuario (no global)** | Previene abuse individual | $0 (código) | 2h código |
| **Índices en Supabase** | -50% tiempo de queries | $0 (migration) | 1h migration |
| **Redis Cluster (3+ nodos)** | 3x throughput | +$50/mes | 1h config |

**Recomendación:** implementar VPC peering primero (mayor beneficio
inmediato, $0), luego cache de queries Ibis (mejora CPU).

### 12.5 Trigger de decisión para cada fase

| Fase | Trigger para ejecutar | Costo incremental |
|---|---|---|
| **A.1** Vertex AI quota | Antes de lanzar 50+ usuarios reales | $0 |
| **A.2** Medir Supabase | Semana 1 de prueba real | $0 o $25/mes |
| **B.1-B.6** Migración Pro 1 | Cuando métricas indiquen >200 conn Redis O >500 usuarios | +$30-80/mes |
| **C.1** Cleanup Free | 7 días después de B (cuando Pro está estable) | -$0 (ahorro) |
| **C.2** Optimizaciones | Cuando haya tiempo de engineering | Variable |

### 12.6 Resumen ejecutivo de costos

| Estado | Costo mensual estimado | Usuarios soportados |
|---|---|---|
| **Actual (Essentials)** | ~$0-50 (Cloud Run + Redis) | 150-250 |
| **+ Fase A** (Supabase Pro + Vertex AI) | +$25/mes | 150-250 |
| **+ Fase B** (Pro 1 + 3 always-on backend) | +$30-80/mes | 500-1000 |
| **+ Fase C.2** (VPC peering + cache Ibis) | +$0-20/mes | 1000+ optimizado |

**Costo total estimado para 1000 usuarios:** $80-150/mes
(asumiendo Cloud Run ~$30/mes, Redis Pro 1 ~$15/mes, Supabase Pro $25/mes, Vertex AI variable, Backend always-on $30-50/mes).

### 12.7 Filosofía: "paga el plan grande primero"

**Regla de oro:** "Primero se paga el plan grande, luego se sube la
variable." No subir concurrencia, pool limits, ni max-instances
antes de:

1. Confirmar que el tráfico lo justifica (métricas reales, no estimación)
2. Pagar el plan Pro correspondiente (Redis Pro 1, Supabase Pro, etc.)
3. Aplicar los env vars de override correspondientes
4. Monitorear 24-48h para validar que el sistema sostiene

**Anti-pattern:** subir variables a Pro-tuned mientras el plan es
Essentials → el sistema se vuelve frágil, cualquier pico lo revienta.

---

## 15. Roadmap Comercial: Plan 1 (B2C) + Plan 2 (B2B)

**Fecha:** 2026-06-10
**Visión estratégica:** dividir el roadmap en dos planes secuenciales
para gestionar riesgo y tiempo. Plan 1 enfoca B2C (suscripción) para
tracción rápida; Plan 2 evolución a B2B (Enterprise) a mediano plazo.

**Regla de oro inquebrantable:** toda optimización debe mantener la
armonía del sistema. Prohibido romper funcionalidades existentes al
mejorar otras.

**Cómo lo garantizamos**:
1. **Tests E2E antes de cada cambio**: Playwright suite (crossfilter,
   happy-path, knowledge) corre antes de merge.
2. **Refactor incremental**: 1 archivo movido por commit, con tests
   pasando.
3. **Compatibilidad hacia atrás**: `__init__.py` re-exporta todo, los
   call sites siguen funcionando sin cambios.
4. **Smoke test en producción**: después de cada deploy, 1 análisis
   real en `livion.lat` para validar.
5. **Monitoreo activo**: Sentry + Langfuse detectan regresiones
   automáticamente.

### 15.1 Plan 1: B2C Launch (Corto Plazo) — 4-5 meses

**Objetivo:** PromData listo para vender suscripciones individuales
(mercado LATAM, personas y freelancers). Tracción rápida, revenue
recurrente desde día 1.

#### Fase 0 — Cimientos y Seguridad B2C (Meses 1-2)

**0.1 — Operación Refactor** (PRIORIDAD #1 — ejecutado el 2026-06-10)

- **Diagnóstico**: `backend/app/tasks/analysis_tasks.py` (3823 líneas) y
  `backend/app/services/semantic_translator.py` (3322 líneas) son
  bombas de relojería de mantenibilidad. Ambos monolíticos.
- **Decisión**: empezar por `semantic_translator.py` (cerebro IA,
  invocado por todos los análisis, tiene fronteras naturales).
- **Plan de división** (3322 líneas → 4 archivos):

  ```
  backend/app/services/semantic_translator/
  ├── __init__.py        # Re-exports (mantener API pública)
  ├── core.py            # Estado compartido + constantes
  ├── router.py          # SemanticRouter (clasifica intent)
  ├── validator.py       # Anti-alucinación, validadores
  ├── planner.py         # Plan generation, Triple Vista
  └── memory.py          # Memoria de sesión
  ```

- **Estrategia**: refactor incremental con `__init__.py` re-exports
  (compatibilidad hacia atrás garantizada).
- **Validación**: tests E2E Playwright + curl de endpoints clave.
- **Criterio de éxito**: 0 regresiones, todos los call sites siguen
  importando el mismo símbolo, tests pasando.
- **Tiempo**: 3-5 días.

**0.2 — Seguridad Básica B2C: Aislamiento de Datos por Usuario**

- Sin RBAC complejo (eso es Plan 2). Solo garantizar que cada usuario
  SOLO ve sus propios datos.
- Auditar Supabase RLS policies en `analysis_tasks`, `chat_messages`,
  `uploaded_files`, `cloud_oauth_connections`, `cloud_watch_targets`.
- Tests E2E que intenten acceder a datos de otro usuario (deben
  recibir 403/404).
- **Tiempo**: 1-2 días.

**0.3 — Diagnóstico de Base de Conocimiento (RAG)**

- Verificación del estado (ejecutado 2026-06-10):
  - `backend/app/services/document_rag.py`: 583 líneas, funcional
  - `backend/app/services/knowledge_qa.py`: 210 líneas, funcional
  - `backend/app/tasks/document_tasks.py`: Celery task funcional
  - `routes.py:814` y siguientes: endpoints `/knowledge/*` funcionales
  - `app/conocimiento/page.tsx`: UI completa con Q&A y citas
  - `components/sidebar.tsx:45`: link "Conocimiento" presente
- **Veredicto**: el RAG está conectado y funcional. Los cables NO se
  desconectaron.
- **Acción**: lanzar 1 prueba E2E completa (subir PDF → esperar → preguntar
  → verificar respuesta con citas). Tiempo: 1 día.

**0.4 — Exportación Inteligente** (Crítico B2C)

- **Visión confirmada**: exportar dataframes PROCESADOS, resúmenes
  o datos detrás de gráficos, NO el archivo original.
- **Arquitectura**: Frontend dispara → Backend genera → Supabase
  Storage temporal → Link de descarga.
- **Scopes soportados**:
  - `chart_data`: CSV/XLSX con datos que ECharts renderizó
  - `analysis_summary`: XLSX multi-sheet (narrativa + métricas + data)
  - `full_analysis`: todo el análisis completo
  - `cross_filter_result`: resultado del cross-filter
- **Endpoint backend**: `POST /api/v1/export {task_id, scope, format}`
- **Librerías**: `openpyxl` (XLSX), `csv` (stdlib), `reportlab` (PDF)
- **Tiempo**: 3-5 días.

#### Fase 1 — Aceleración de Valor (Meses 3-4)

**1.1 — Onboarding con Plantillas Rápidas**

- `auto_analyst.py` ya existe (lógica pre-armada).
- Frontend: en `chat-interface.tsx`, sección "Análisis Sugeridos" después
  de subir un archivo.
- 4-6 templates: Resumen Ejecutivo, Tendencia Temporal, Distribución
  por Categoría, Detección de Anomalías.
- **Tiempo**: 3-5 días.

**1.2 — Pricing / Suscripción B2C**

- Stripe o MercadoPago (LATAM)
- 3 tiers: Free (5 análisis/mes, 1GB) / Pro $29/mes (100 análisis, 10GB)
  / Business $99/mes (ilimitado, multi-usuario limitado)
- Webhook de Stripe
- UI de billing en `app/billing/page.tsx` (nueva)
- **Tiempo**: 7-10 días.

**1.3 — Marketing Site + Landing Page**

- `app/page.tsx` mejorada con hero, features, pricing, testimonios
- SEO (metadata, sitemap, robots.txt)
- Blog con casos de uso (opcional)
- **Tiempo**: 5-7 días.

**1.4 — AI Insights Proactivos (conectar al frontend)**

- Diagnóstico previo (1 día): ¿existe lógica proactiva? ¿Hay tabla
  `insights` en Supabase? ¿Hay endpoint?
- Si existe: panel lateral con cards + notificación in-app.
- Si no: 10-15 días para construir desde cero.
- **Tiempo estimado si existe**: 3-5 días.

**Output del Plan 1**: PromData listo para vender suscripciones B2C.

### 15.2 Plan 2: B2B Enterprise (Mediano Plazo) — 6-7 meses

**Objetivo:** PromData compite con Power BI en features enterprise.
Multi-tenancy, RBAC, integraciones corporativas, API pública.

#### Fase 2 — Enterprise Core (Meses 5-8)

**2.1 — RBAC Estricto**

- 3 roles: `owner` (control total, billing), `editor`
  (crear/editar), `viewer` (solo lectura)
- Supabase: columna `role` en `team_members`, RLS policies
- Backend: decorator `@require_role("editor")` en rutas de escritura
- Frontend: condicional en botones de edición
- **Tiempo**: 7-10 días.

**2.2 — Multi-Datasource (SQL)**

- PostgreSQL (prioridad #1), MySQL (#2), BigQuery (#3)
- Nuevo servicio `datasource_connector.py` con Ibis backends
- Supabase: tabla `datasources` con credenciales cifradas
- Frontend: UI en `cargar-datos/` para configurar conexiones
- Query caching (1h TTL en Redis)
- **Tiempo**: 15-20 días.

**2.3 — Scheduled Reports + Email**

- Tabla Supabase `scheduled_reports` (file_id, prompt, cron, email)
- Celery Beat scheduler
- Task que ejecuta análisis + genera PDF + envía email
- Frontend: modal "Programar este análisis"
- **Tiempo**: 7-10 días.

#### Fase 3 — Líder del Sector (Meses 9-12)

**3.1 — API Pública + SDK JavaScript**

- Autenticación por API key (tabla `api_keys` con rate limits)
- Documentación OpenAPI (FastAPI ya la genera)
- SDK npm `@promdata/sdk`
- **Tiempo**: 10-15 días.

**3.2 — Integraciones Corporativas**

- Slack, Microsoft Teams (notificaciones de insights)
- Google Workspace (Sheets, Drive)
- Salesforce, HubSpot (leer datos CRM)
- **Tiempo**: 15-20 días.

**3.3 — Testing + Observability Enterprise**

- Unit tests por servicio (target 80% coverage)
- Integration tests del pipeline completo
- E2E con Playwright (ya hay base)
- Load tests con k6 (simular 1000 usuarios)
- Distributed tracing (OpenTelemetry)
- Real User Monitoring
- SLOs definidos
- **Tiempo**: 15-20 días.

**3.4 — Disaster Recovery + Multi-Region**

- Backup policy automatizado
- RTO/RPO definidos
- Failover entre regiones
- Runbooks
- **Tiempo**: 10-15 días.

**Output del Plan 2**: PromData compite con Power BI en features enterprise.

### 15.3 Timeline Consolidado

```
2026 H2 (Plan 1 B2C) — 4-5 meses
├── Mes 1: Refactor semantic_translator (0.1) + Seguridad B2C (0.2)
├── Mes 1-2: Diagnostico RAG (0.3) + Exportacion (0.4)
├── Mes 2: Plantillas rapidas (1.1)
├── Mes 3: Pricing/Stripe (1.2) + Marketing site (1.3)
└── Mes 4: AI Insights UI (1.4)

2027 H1 (Plan 2 B2B) — 6-7 meses
├── Mes 5-6: RBAC estricto (2.1)
├── Mes 6-8: Multi-datasource SQL (2.2)
├── Mes 7-8: Scheduled Reports (2.3)
├── Mes 9-10: API Publica + SDK (3.1)
├── Mes 10-11: Integraciones corporativas (3.2)
├── Mes 11-12: Testing + Observability (3.3)
└── Mes 12: Disaster Recovery (3.4)
```

### 15.4 Reglas de Armonía (NO Romper Funcionalidades)

**Prohibido**:
- Romper tests E2E existentes
- Cambiar firma de funciones públicas sin deprecation cycle
- Eliminar endpoints API sin versionado
- Modificar payloads de respuesta sin avisar

**Permitido**:
- Refactor interno con misma API pública
- Agregar nuevos endpoints/funciones
- Mejorar performance sin cambiar semántica
- Optimizar queries manteniendo mismo resultado

---

## 13. Defensive Supabase Code (2026-06-08)

### 13.1 Symptom (incident post-Supabase recovery)

Al recuperar Supabase del outage por Disk IO budget, las API routes
comenzaron a fallar con `IndexError: list index out of range` en
`supabase_auth/_sync/gotrue_client.py:733` (línea
`if access_token and access_token.split(".")[1]:`). El JWT corrupto
provocaba un 500 opaco que el frontend no podía distinguir de un bug
real del código.

### 13.2 Fix (5 cambios backend, ~70 líneas)

1. **`backend/app/core/config.py`**: 4 env vars nuevas
   `SUPABASE_*_TIMEOUT_SECONDS` con defaults seguros
   (connect=3s, read=8s, write=5s, pool=3s).

2. **`backend/app/core/supabase_client.py`**: `_build_client` ahora
   inyecta `httpx.Client(timeout=...)` en TODOS los clientes
   (service, user, anon). Fail-fast en 3s en vez de 10s default.

3. **`backend/app/api/routes.py` — `/analyze`**: usa
   `get_supabase_user_client(token)` (centraliza timeouts).

4. **`backend/app/api/routes.py` — handlers `/analyze` y `/chat/{file_id}`**:
   nuevo bloque try/except que distingue `httpx.TimeoutException`,
   `IndexError`, `KeyError`, o mensajes con "list index out of range" /
   "Invalid API key" → retorna **HTTP 503** con mensaje claro "El servicio
   de base de datos está temporalmente no disponible. Por favor, inténtalo
   de nuevo en unos minutos." El frontend puede reintentar mejor con 503
   que con 500.

5. **`backend/app/main.py`**: log de startup con versiones de
   `httpx` + `supabase-py` + timeouts configurados. Primer punto de
   referencia para diagnosticar futuras incidencias de upstream.

### 13.3 Cero impacto en funcionalidades existentes

- Sentry sigue capturando excepciones (mejor — más precisas)
- Langfuse v4 sigue emitiendo trazas
- Cross-filter fix (§12) sigue funcionando
- Redis + Cloud Run + Vercel sin cambios

---

## 14. Cross-Filter chart_base_filters Herencia (2026-06-08)

### 14.1 Bug lógico detectado por el usuario

Prompt: "realiza un gráfico que muestre la evolución de los ingresos en el tiempo"
→ El chart solo graficaba Ingresos (filtro base `Tipo Movimiento = Ingreso`).
→ Click en `Jan-2025` + "Filtrar aquí" → DuckDB retornaba 666 registros
mezclando Ingresos + Egresos.

### 14.2 Root cause

El canary executor aplicaba el filtro `Tipo Movimiento = Ingreso` a nivel
SQL para generar el chart, pero NO propagaba ese filtro al frontend. El
usuario al hacer clic solo enviaba `global_chart_filter="Jan-2025"` y
DuckDB retornaba TODOS los registros de Jan-2025 (Ingresos + Egresos)
porque no sabía que el chart original solo graficaba Ingresos.

### 14.3 Fix (3 archivos)

1. **`backend/app/services/canonical_tabular_canary_executor.py`**:
   `_build_chart_option` ahora extrae los filtros del `plan.main_intent`
   y los inyecta en `option["chart_base_filters"]` como dict
   `{col: op_value}`. Cada chart lleva los filtros que el canary
   aplicó para generarlo.

2. **`components/chat-interface.tsx` — handleCrossFilter**:
   - Extrae `chart_option.chart_base_filters` del matchedComponent
   - Mergea: `{...baseFilters, ...clickFilters}` (clic gana en conflicto)
   - Pasa el merged al `duckdbEngine.crossFilter()`
   - Muestra en el filterMsg la suma explícita:
     ```
     📊 Filtros base del chart: Tipo Movimiento="Ingreso"
     ➕ Filtros del clic: global_chart_filter="Jan-2025"
     📌 Se encontraron 42 registros en <50ms.
     ```

3. **`components/drill-down-menu.tsx`**:
   - Añade `BaseFilterBadge` component que muestra `+ N base` al lado
     de "Instantáneo · Sin servidor" cuando el chart tiene filtros
     base. El usuario ve ANTES de hacer click que el filtro del clic
     se va a combinar con N filtros del chart original.

### 14.4 Cero impacto en funcionalidades existentes

- Si el chart no tiene `chart_base_filters` (caso legacy), el
  comportamiento es idéntico al anterior.
- `BaseFilterBadge` retorna `null` cuando no hay filtros base.
- Sentry + Langfuse + Redis + CI/CD sin cambios.

### 14.5 Validación esperada

1. Lanzar el mismo prompt del usuario: "evolución de los ingresos en el tiempo"
2. Click en un punto del chart
3. Click en "Filtrar aquí"
4. Verificar:
   - La tabla muestra SOLO registros de Ingreso + la fecha clickeada
   - El filterMsg muestra los 2 filtros (base + clic)
   - El badge `+ 1 base` aparece en el menú antes del click

---

## 12. Cross-Filter "Filtrar aquí" (incident 10.3)

### 12.1 Symptom (resuelto en commit 2026-06-08)

El botón "⚡ Filtrar aquí" sobre cualquier chart en `livion.lat` mostraba
"⚠️ No hay datos cargados para filtrar localmente." El frontend DuckDB-WASM
no encontraba ninguna tabla cargada para hacer cross-filter.

### 12.2 Root cause (medido con 3 tasks reales en Supabase)

| Task | total | arrow_data | snapshot_arrow | granular_arrow |
|---|---|---|---|---|
| `f9425c6b` | 1.24MB | 21KB | **1.16MB ✅** | 0KB |
| `3eaa1869` | 97KB | **0KB ❌** | **0KB ❌** | **0KB ❌** |
| `6c0c4709` | 23KB | **0KB** | **0KB** | **0KB** |

El canary executor (`backend/app/services/canonical_tabular_canary_executor.py`)
tenía 3 bugs que vaciaban los arrow payloads:

1. **Solo el primer chart recibía `data`** en `final_struct` (los demás
   charts quedaban con `data: []` → `arrow_data` no se podía serializar).
2. **`granular_arrow` solo se generaba** si el plan inyectaba explícitamente
   `filtered_granular_df` (casi nunca).
3. **`snapshot_arrow` se descartaba** si `candidate_df` era `None` o
   vacío (lo cual ocurre en la mayoría de los paths del canary).

Además, el `_apply_progressive_soft_shedding` en
`backend/app/tasks/analysis_tasks.py` descartaba `snapshot_arrow` PRIMERO
(el más valioso para cross-filter), preservando `granular_arrow`
(regenerable per-chart).

### 12.3 Fix aplicado (3 archivos, 4 cambios)

1. **`backend/app/services/canonical_tabular_canary_executor.py`** (3 cambios):
   - `filtered_granular_df` ahora se deriva de `result_payload['data']`
     si no viene explícito (regenera `granular_arrow` para >80% de charts).
   - Nuevo `data_by_chart: {chart_id: [records]}` poblado para TODOS
     los charts del plan (no solo el primero). `data` se mantiene para
     compatibilidad legacy.
   - Cascade de fallbacks para `snapshot_arrow`: si `candidate_df` es
     None, intenta `attrs.candidate_dataframe` → `data_by_chart` →
     reconstrucción desde records acumulados.

2. **`backend/app/tasks/analysis_tasks.py`** (1 cambio):
   - `_apply_progressive_soft_shedding` ahora descarta PRIMERO
     `granular_arrow` (regenerable), luego `arrow_data`, luego
     `snapshot_arrow` (último en descartar — es la cópia completa).

3. **`backend/.env.production.example`** (1 cambio):
   - `UNIVERSAL_TABULAR_RESULT_SOFT_LIMIT_BYTES=4000000` (subido de
     1.5MB default → 4MB para preservar `snapshot_arrow` y `arrow_data`).

### 12.4 Validación post-deploy

- Ejecutar 1 prompt en `livion.lat` y verificar que
  `data.result.arrow_data` O `data.result.snapshot_arrow` O
  `data.result.chart_options[*].granular_arrow` están presentes.
- Sin esto, "Filtrar aquí" sigue roto.
- Monitorear Sentry para <1% errores en `canonical_tabular_canary_executor`
  (sin timeouts nuevos en PostgREST).

### 12.5 Zero impacto confirmado

Cambios contenidos al canary executor. CERO impacto en: frontend,
DuckDB engine, Redis, Sentry, Langfuse, CI/CD, skills.

---

**Last updated:** 2026-06-10 — perf(redis) Essentials-Tuned defaults
COMPLETADO. Pool sizes 30/30/10/20 → 6/6/2/3 (default), `CELERY_BROKER_POOL_LIMIT`
30 → 5, `CELERY_RESULT_BACKEND_MAX_CONNECTIONS` 30 → 5, worker `--concurrency`
6 → 4. Deploy: `promdata-backend-00017-zl2` + `promdata-worker-00035-drm`
con imagen `632b093c`. **Limpieza de producción ejecutada:** env vars
inflados (REDIS_MAX_CONNECTIONS_*, CELERY_BROKER_POOL_LIMIT,
CELERY_RESULT_BACKEND_MAX_CONNECTIONS) eliminados de backend y worker
via `gcloud run services update --remove-env-vars` y
`gcloud beta run worker-pools update --remove-env-vars`. Verificación:
`redis-cli CLIENT LIST | wc -l` = **11 conexiones activas** (96% por
debajo del techo de 256 del plan Essentials). Infraestructura blindada
y lista para la prueba de usuarios (10-250 usuarios con 37% de margen).

**§12 (NUEVA) — Escalabilidad a 1000 Usuarios:** roadmap completo en
3 fases (A: pre-requisitos manuales, B: migración Pro 1, C: cleanup
y optimizaciones opcionales). Costo total estimado para 1000 usuarios:
$80-150/mes. Sistema 90% listo; solo 2 pendientes manuales (Vertex AI
quota, Supabase plan). Regla de oro: "paga el plan grande primero,
luego sube la variable." Trabajo de backend/infraestructura
formalmente finalizado al 100%.

**§15 (NUEVA) — Roadmap Comercial:** Plan 1 (B2C Launch, 4-5 meses)
+ Plan 2 (B2B Enterprise, 6-7 meses). Plan 1 enfoca B2C con
suscripción rápida; Plan 2 evolución a Enterprise con RBAC,
multi-datasource, scheduled reports, API pública. Regla de oro:
toda optimización mantiene armonía del sistema, prohibido romper
funcionalidades existentes. **Fase 0.1 (Refactor de
semantic_translator.py)** iniciada hoy con estrategia incremental
(véase commits subsiguientes).
para que producción use los nuevos defaults sanos (no los Pro-tuned
inflados que saturarían 256).
(deployed to backend via manual `gcloud run deploy`, revision
`promdata-backend-00013-9cf` with full 42 env vars restored from
`00011-d9t`) + Incident 10.4 documented (`--env-vars-file` REPLACES
vs `--update-env-vars` ADDS) + §6.7 manual deploy procedure + §2.1
flagged missing `promdata-backend-auto-deploy` trigger + §2.2
flagged worker `--concurrency=4` regression in `cloudbuild.worker.yaml`.

---

## 4. Protocolo Obligatorio de Operación y Verificación

Antes de proponer un plan técnico o aplicar modificaciones en el código, el agente debe:

1. Cargar y procesar de forma secuencial las directrices del skill maestro `.agents/skills/promdata-orchestrator/SKILL.md`.
2. Generar un plan numerado de cambios atómicos especificando archivos, funciones afectadas y comandos de prueba.
3. Finalizar obligatoriamente cada respuesta que contenga un plan o solución con el siguiente bloque:
   `Skills cargados: [Lista de skills] | Verificación técnica: [Comando ejecutado + Estado Pass/Fail]`

## 5. Integración con Validadores Locales y Git Hooks

- Todo plan generado bajo el comando `/plan-promdata` se evaluará de forma mandatoria mediante el script local `validate_plan.py`.
- Si `validate_plan.py` o la suite de pruebas locales devuelven un exit code distinto de 0, el cambio se rechaza automáticamente.
- Ninguna tarea se considera finalizada si el hook de pre-commit de Git bloquea el commit local debido a fallos en la suite de 32/32 tests.

---

## 6. Protocolo de Honestidad Absoluta en Testing

### 6.1 Principio

Ninguna respuesta del agente puede incluir la frase "tests passed" ni "32/32 tests passed" sin haber ejecutado físicamente la suite de pruebas en la terminal y capturado la salida literal. Queda prohibido:
- Reportar tests como "passed" basándose en ejecuciones anteriores.
- Simular resultados exitosos.
- Omitir deliberadamente la ejecución de pruebas.
- Usar frases como "asumiendo que los tests pasan" para cerrar la tarea.

### 6.2 Sanción automática

Si un agente reporta que los tests pasaron sin evidencia terminal:
- El bloque de verificación técnica en su respuesta (Skills cargados + Verificación técnica) debe ser ignorado.
- La tarea se considera automáticamente no finalizada.
- El agente debe re-ejecutar la suite completa y mostrar las últimas 3 líneas literales de la terminal.

### 6.3 Procedimiento obligatorio

1. Al terminar los cambios de código, ejecutar:
   ```bash
   cd backend && ./run_backend_tests.sh
   ```
2. Capturar las últimas 3 líneas de la salida de la terminal.
3. Insertar esas líneas literales en la respuesta como evidencia.

### 6.4 Manejo de fallos

Si el conteo final es menor a 32:
- Imprimir las últimas 20 líneas de error de la terminal (incluyendo tracebacks).
- La tarea se bloquea automáticamente.
- No se puede cerrar la tarea hasta que el conteo sea 32/32.

---

**Last updated:** 2026-06-16 — feat(governance): add mandatory protocol §4 and validation hooks §5, add testing honesty protocol §6
