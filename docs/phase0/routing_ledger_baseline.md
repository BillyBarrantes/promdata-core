# Routing Ledger — Baseline Fase 0

- Generado: 2026-07-26T09:54:40.202477+00:00
- Fuente task-level: `analysis_tasks.results_json->traceability` (7 tareas)
- Fuente stage-level: `enterprise_telemetry_events` (516 eventos latency muestreados)

## Task-level (rutas reales)

| Runtime | Tareas | % |
|---|---|---|
| `canonical_tabular_production` | 7 | 100.0% |

**Ruta canónica (canonical_tabular_production): 100.0%**
**Tareas fuera de ruta canónica (legacy/fallback): 0**

## Stage-level por runtime

| Runtime | Eventos |
|---|---|
| `universal_tabular_production` | 516 |

## Stage-level por familia de intención (prompt_type)

| Familia | Eventos |
|---|---|
| `generic_analysis` | 183 |
| `dimension_analysis` | 96 |
| `kpi_request` | 93 |
| `trend_request` | 81 |
| `other` | 54 |
| `expiry_window_analysis` | 9 |

## Veredicto

canonical_tabular_production domina el tráfico real; sin evidencia de legacy ni fallback en la ventana muestreada.
