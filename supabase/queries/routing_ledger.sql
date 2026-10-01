-- ═══════════════════════════════════════════════════════════════════
-- ROUTING LEDGER — Sprint 2 / Fase 0 (2026-07-25)
-- Inventario consultable de rutas analíticas reales.
-- Fuentes: analysis_tasks.results_json->traceability (task-level)
--          enterprise_telemetry_events (stage-level, runtime+prompt_type)
-- Ejecutar en Supabase SQL Editor. Read-only.
-- ═══════════════════════════════════════════════════════════════════

-- 1) Task-level: % de tareas por runtime real (últimos 30 días)
SELECT
  COALESCE(results_json->'traceability'->>'runtime', 'unlabeled')      AS runtime,
  COALESCE(results_json->'traceability'->>'prompt_strategy', 'unknown') AS prompt_strategy,
  status,
  COUNT(*)                                                             AS tasks,
  ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2)                   AS pct
FROM public.analysis_tasks
WHERE created_at >= now() - interval '30 days'
GROUP BY 1, 2, 3
ORDER BY tasks DESC;

-- 2) Fallback detector: tareas que NO usaron la ruta dominante canónica
SELECT
  id, created_at, status,
  results_json->'traceability'->>'runtime' AS runtime,
  left(results_json->>'error_trace', 120)  AS error_trace
FROM public.analysis_tasks
WHERE created_at >= now() - interval '30 days'
  AND COALESCE(results_json->'traceability'->>'runtime', 'unlabeled')
      NOT IN ('canonical_tabular_production')
ORDER BY created_at DESC;

-- 3) Stage-level (telemetría): distribución por runtime × familia de intención
SELECT
  COALESCE(dimensions->>'runtime', 'unlabeled')     AS runtime,
  COALESCE(dimensions->>'prompt_type', 'unknown')   AS intent_family,
  COUNT(*)                                          AS stage_events,
  ROUND(AVG(metric_value)::numeric, 0)              AS avg_duration_ms
FROM public.enterprise_telemetry_events
WHERE metric_domain = 'latency'
  AND metric_name = 'analysis_stage_duration_ms'
  AND created_at >= now() - interval '30 days'
GROUP BY 1, 2
ORDER BY stage_events DESC;

-- 4) Fallbacks registrados en logs estructurados (si se persistieran como métrica)
SELECT
  metric_name, dimensions->>'reason' AS reason, COUNT(*)
FROM public.enterprise_telemetry_events
WHERE metric_name ILIKE '%fallback%'
  AND created_at >= now() - interval '30 days'
GROUP BY 1, 2;
