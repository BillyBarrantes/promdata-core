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
| Cache       | Redis Cloud (Free plan, 30 conn / 30MB) | Centralized pool, see §5.                          |
| CI/CD       | Google Cloud Build                      | `cloudbuild.yaml` (backend) + manual worker deploy.|
| Hosting     | Google Cloud Run (`us-east4`)           | 2 services: `promdata-backend` + `promdata-worker`.|

---

## 2. Services (Cloud Run)

### 2.1 `promdata-backend`
- **Source:** `/backend/Dockerfile`
- **Auto-deploy:** **ENABLED** (triggered by `git push` to `main` via `cloudbuild.yaml`)
- **Public URL:** `https://promdata-backend-18829055607.us-east4.run.app`
- **Image tag:** `gcr.io/$PROJECT_ID/promdata-backend:$COMMIT_SHA`
- **Env vars of interest:**
  - `ALLOWED_ORIGINS=https://livion.lat,https://www.livion.lat`
  - `FRONTEND_APP_URL=https://livion.lat`

### 2.2 `promdata-worker`
- **Source:** same image (`gcr.io/$PROJECT_ID/promdata-backend:$COMMIT_SHA`)
- **Auto-deploy:** **DISABLED**. Manual redeploy from Cloud Console.
- **Command override** (set in Cloud Run):
  ```
  celery -A app.celery_app worker --loglevel=info --pool=prefork \
         --concurrency=2 --max-tasks-per-child=50 -Ofair --prefetch-multiplier=1
  ```
- **Why `--concurrency=2`:** Redis Cloud Free plan has 30 max connections.
  With broker_pool_limit=5, a single worker with concurrency=4 saturates
  the plan when combined with API + healthchecks. **Do not raise to 4.**

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

### 4.4 Redis Pool (Fase 2) — CRITICAL
- Centralized in `app/core/redis_client.py`.
- Three named pools, each with its own connection cap:

  | purpose             | max_connections | env override                                    |
  |---------------------|----------------:|-------------------------------------------------|
  | `rate_limit`        |               5 | `REDIS_MAX_CONNECTIONS_RATE_LIMIT`              |
  | `ai_response_cache` |               5 | `REDIS_MAX_CONNECTIONS_AI_CACHE`                |
  | `healthcheck`       |               2 | `REDIS_MAX_CONNECTIONS_HEALTHCHECK`             |
  | (other)             |               5 | `REDIS_MAX_CONNECTIONS_DEFAULT`                 |

- Celery broker/backend also capped at 5 each
  (`CELERY_BROKER_POOL_LIMIT`, `CELERY_RESULT_BACKEND_MAX_CONNECTIONS`).
- **Per-child pool reset:** `celery_app.py:_on_celery_worker_init` calls
  `reset_redis_pools()` in the `worker_init` signal. This prevents the
  parent process's sockets from being inherited by child workers after
  `fork()` (which would cause them to be in an invalid state).

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

### 4.7 Celery Result Expiration (12h)
- `celery_app.conf.result_expires = 12 * 3600` (12 hours).
- Default Celery value is 86400s (1 day). We halved it to reduce Redis
  Cloud memory pressure on the Free plan. The frontend polls task status
  in the first few seconds after submit, so 12h is more than enough.

---

## 5. Environment Variables (Production Reference)

| Variable                                  | Purpose                                                      | Default                                                |
|-------------------------------------------|--------------------------------------------------------------|--------------------------------------------------------|
| `CELERY_BROKER_URL`                       | Redis broker URL (Celery queue)                              | `redis://localhost:6379/0`                             |
| `CELERY_RESULT_BACKEND`                   | Redis URL for task results                                   | same as broker                                         |
| `RATE_LIMIT_STORAGE_URL`                  | Redis URL for rate-limit counters                            | falls back to `CELERY_BROKER_URL`                      |
| `CELERY_BROKER_POOL_LIMIT`                | Max broker connections per process                           | `5`                                                    |
| `CELERY_RESULT_BACKEND_MAX_CONNECTIONS`   | Max result-backend connections per process                   | `5`                                                    |
| `REDIS_MAX_CONNECTIONS_RATE_LIMIT`        | Pool size: rate_limit                                        | `5`                                                    |
| `REDIS_MAX_CONNECTIONS_AI_CACHE`          | Pool size: ai_response_cache                                 | `5`                                                    |
| `REDIS_MAX_CONNECTIONS_HEALTHCHECK`       | Pool size: healthcheck                                       | `2`                                                    |
| `REDIS_MAX_CONNECTIONS_DEFAULT`           | Pool size: catch-all                                         | `5`                                                    |
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
4. Monitor deploy in Cloud Console → Cloud Build → History.
5. Verify with `curl https://promdata-backend-18829055607.us-east4.run.app/health/ready`.

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
       --concurrency=2 --max-tasks-per-child=50 -Ofair --prefetch-multiplier=1 &

# Production
# Cloud Console → promdata-worker → ⋮ → Delete revision, then redeploy.
```

### 6.6 Cache Invalidation
- Schema version bump (`_CACHE_KEY_SCHEMA_VERSION = "v3"`) is the
  nuclear option. It invalidates all cached AI responses.
- Targeted invalidation: `redis-cli -u "$REDIS_URL" --scan --pattern
  "promdata:ai_cache:v2:*" | xargs -L 100 redis-cli -u "$REDIS_URL" DEL`.
- **Never use FLUSHALL in production.** It wipes rate-limit counters too.

---

## 7. Anti-Patterns (Forbidden)

| Anti-pattern                                                                 | Why                                                                                       |
|------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------|
| `if col_name == "gastos"`                                                    | Hardcodes user-specific data; breaks for other tenants.                                   |
| `pd.read_csv()` + `.apply(lambda)` in the hot path                           | O(n) Python loop; 100K rows → 30s. Use Ibis.                                              |
| `except: pass`                                                               | Hides bugs. Always log context.                                                            |
| Adding `socket_keepalive_options` to redis client config                      | Breaks SSL handshakes on `rediss://`. See §4.5.                                            |
| Installing `google-generativeai` alongside `google-genai`                     | Namespace conflict. Already removed from requirements.txt — keep it removed.               |
| Raising `--concurrency` above 2 in production                                | Saturates Redis Cloud Free plan. See §4.4.                                                |
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

**Last updated:** 2026-06-05 — Phase 2 (Redis pool) + Fix C v2 (cache schema)
+ Fix V3 (semantic translator) + result_expires 12h optimization.
