# Matriz de Configuración Efectiva — Fase 0

- Generado: 2026-07-26T09:56:40.166311+00:00
- Fuentes: repo (`.env`, `config.py`, `cloudbuild*.yaml`, `package.json`), endpoints públicos `/health/*`, AGENTS.md
- Sin secretos: solo flags, tamaños de pool, concurrencia, límites y versiones.

## 1. Flags canónicos efectivos (backend/.env — espejo de prod)

| Flag | Valor |
|---|---|
| `AI_MODEL_NAME` | `gemini-3.1-pro-preview` |
| `CANONICAL_ANALYTICAL_CONTRACT_ADAPTER_ENABLED` | `true` |
| `CANONICAL_IBIS_PREVIEW_RUNTIME_ENABLED` | `true` |
| `CANONICAL_NATIVE_TABULAR_EXTRACTION_ENABLED` | `true` |
| `CANONICAL_SHADOW_METRIC_VALIDITY_GATE_ENABLED` | `true` |
| `CANONICAL_SHADOW_QUERY_RUNTIME_ENABLED` | `true` |
| `CANONICAL_SHADOW_TRAFFIC_MIRROR_ENABLED` | `false` |
| `CANONICAL_SHADOW_TRAFFIC_MIRROR_MAX_PLANS` | `3` |
| `CANONICAL_SHADOW_TRAFFIC_MIRROR_TABULAR_ONLY` | `true` |
| `CANONICAL_TABULAR_CANARY_ALLOWLIST_TEAM_IDS` | `72936d63-e550-4acf-b492-b5368718214b` |
| `CANONICAL_TABULAR_CANARY_FAIL_OPEN_ENABLED` | `true` |
| `CANONICAL_TABULAR_CANARY_FUNCTIONAL_SWITCH_ENABLED` | `true` |
| `CANONICAL_TABULAR_CANARY_MAX_DIVERGENCE_SCORE` | `0.02` |
| `CANONICAL_TABULAR_CANARY_MIN_ALIGNMENT_RATE` | `0.98` |
| `CANONICAL_TABULAR_CANARY_MIN_OBSERVED_TASKS` | `1` |
| `CANONICAL_TABULAR_CANARY_REQUIRE_SHADOW_EVIDENCE` | `false` |
| `CANONICAL_TABULAR_CANARY_ROUTER_ENABLED` | `true` |
| `CANONICAL_TABULAR_CANARY_TRAFFIC_PERCENT` | `100` |
| `GEMINI_VERTEX_LOCATION` | `global` |
| `GEMINI_VERTEX_PROJECT` | `promdata-enterprise` |
| `NARRATIVE_FAST_MODEL_NAME` | `gemini-3.5-flash` |
| `NARRATIVE_STRICT_MODEL_NAME` | `gemini-3.5-flash` |
| `UNIVERSAL_TABULAR_PRODUCTION_EXECUTOR_ENABLED` | `true` |
| `UNIVERSAL_TABULAR_RESULT_SOFT_LIMIT_BYTES` | `4000000` |

## 2. Pools Redis — defaults del código (fuente de verdad Essentials)

| Variable | Default código |
|---|---|
| `CELERY_BROKER_POOL_LIMIT` | `5` |
| `CELERY_RESULT_BACKEND_MAX_CONNECTIONS` | `5` |
| `REDIS_MAX_CONNECTIONS_AI_CACHE` | `6` |
| `REDIS_MAX_CONNECTIONS_DEFAULT` | `3` |
| `REDIS_MAX_CONNECTIONS_HEALTHCHECK` | `2` |
| `REDIS_MAX_CONNECTIONS_PUBSUB` | `2` |
| `REDIS_MAX_CONNECTIONS_RATE_LIMIT` | `6` |

## 3. Worker (cloudbuild.worker.yaml — repo)

| Parámetro | Valor |
|---|---|
| concurrency | `4` |
| memory | `16Gi` |
| cpu | `4` |
| overrides de pools Redis | none (defaults gobiernan) |

## 4. Versiones

| Componente | Versión real | Documentado en AGENTS.md | Drift |
|---|---|---|---|
| Next.js | `16.2.6` | `14` (§1 Stack) | ⚠️ SÍ — doc desactualizada |
| FastAPI | `0.136.3` | — | — |
| Celery | `5.6.3` | `5` (§1) | no |
| Ibis | `12.0.0` | — | — |
| DuckDB | `1.5.3` | — | — |

## 5. Live (endpoints públicos, verificado 2026-07-26)

```json
{
  "ready": {
    "status": "ok",
    "probe": "readiness",
    "checks": {
      "celery_broker": {
        "ok": true,
        "target": "redis://default:****@grape-cloth-driftwood-89364.db.redis.io:13366/0",
        "error": null
      },
      "celery_result_backend": {
        "ok": true,
        "target": "redis://default:****@grape-cloth-driftwood-89364.db.redis.io:13366/0",
        "error": null
      },
      "canonical_tabular_canary": {
        "ok": true,
        "status": "ready",
        "summary": "Canary tabular listo para activación funcional controlada.",
        "functional_switch_enabled": true,
        "ready_for_functional_canary": true
      }
    }
  },
  "observability": {
    "status": "ok",
    "sentry": {
      "enabled": true,
      "dsn_configured": true,
      "environment": "development",
      "release": "0.1.0-local",
      "host": "https://sentry.io"
    },
    "langfuse": {
      "enabled": true,
      "public_key_configured": true,
      "secret_key_configured": true,
      "host": "https://us.cloud.langfuse.com"
    },
    "note": "Verifica también que promdata-worker Cloud Run tenga SENTRY_DSN y LANGFUSE_* configuradas (no solo promdata-backend)."
  }
}
```

## 6. Diferencias detectadas (repo vs deploy vs docs)

| # | Diferencia | Estado |
|---|---|---|
| D1 | AGENTS.md documenta Next.js 14; package.json usa 16.2.6 | ⚠️ abierto (corregir doc en T6) |
| D2 | Worker YAML concurrency=4 + sin overrides de pools (Sprint 0/QW-3) | ✅ alineado con Essentials |
| D3 | `AI_MODEL_NAME` efectivo en .env difiere del default documentado (`gemini-3.5-flash`) | ⚠️ registrar valor real por entorno |
| D4 | Backend live: Redis endpoint = instancia Essentials (`...redis.io:13366`) | ✅ consistente con §4.4b |
| D5 | Canary functional switch habilitado y `ready_for_functional_canary=true` (health live) | ℹ️ coherente con traffic_percent=100 |
| D6 | Verificación live de env vars del worker (gcloud) | 🔴 bloqueado B-1 (CLI roto) |
