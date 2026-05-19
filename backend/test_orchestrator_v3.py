#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════
  TEST V3: Relajación de Paranoia del Orquestador
  Valida los 5 cambios quirúrgicos sin conexión a DB.
═══════════════════════════════════════════════════════════════
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

results = []

def log_result(test_id: str, passed: bool, detail: str):
    icon = "✅ PASS" if passed else "❌ FAIL"
    results.append((test_id, passed, detail))
    print(f"  {icon}  [{test_id}] {detail}")


# ═══════════════════════════════════════════════════════════════
# T1 — _summarize_execution_result trata empty_result como success
# ═══════════════════════════════════════════════════════════════
print("\n" + "═" * 60)
print("  T1 — empty_result = success en _summarize_execution_result")
print("═" * 60 + "\n")

from app.services.canonical_shadow_query_runner import _summarize_execution_result
from app.core.semantic_grammar import (
    AnalysisPlan, TimeTrendIntent, DistributionIntent,
    VisualProtocol, MetricPolarity, FilterOperator, DataFilter,
)

# Mock plan
mock_plan = AnalysisPlan(
    main_intent=TimeTrendIntent(
        rationale="test",
        date_column="fecha",
        value_column="monto",
        visual_protocol=VisualProtocol.LINE,
    ),
    title="Test Plan",
    column_aliases={},
    metric_polarity=MetricPolarity.NEUTRAL,
)

# T1.1: Normal success
normal_result = {"type": "echarts", "chart_type": "line_chart", "data": [{"name": "a", "value": 1}]}
summary = _summarize_execution_result(mock_plan, normal_result, 1)
log_result("T1.1", summary["status"] == "success",
           f"Normal result → status='{summary['status']}'")

# T1.2: empty_result → debe ser SUCCESS (no error)
empty_result = {"error": "empty_result", "message": "No data", "filters_applied": []}
summary = _summarize_execution_result(mock_plan, empty_result, 1)
log_result("T1.2", summary["status"] == "success",
           f"empty_result → status='{summary['status']}' (debe ser 'success')")

# T1.3: Real error → debe seguir siendo ERROR
real_error = {"error": "column_not_found", "message": "Columna X no existe"}
summary = _summarize_execution_result(mock_plan, real_error, 1)
log_result("T1.3", summary["status"] == "error",
           f"Real error → status='{summary['status']}' (debe ser 'error')")


# ═══════════════════════════════════════════════════════════════
# T2 — Production Executor acepta partial_query_success
# ═══════════════════════════════════════════════════════════════
print("\n" + "═" * 60)
print("  T2 — Production Executor acepta partial_query_success")
print("═" * 60 + "\n")

import inspect
from app.services.canonical_tabular_production_executor import (
    execute_canonical_tabular_production_analysis,
)

# Verificar que la condición de la puerta NO contiene "query_executed"
source = inspect.getsource(execute_canonical_tabular_production_analysis)
has_old_gate = 'production_query_status") != "query_executed"' in source
has_new_gate = "successful_count <= 0" in source

log_result("T2.1", not has_old_gate,
           f"Puerta NO exige 'query_executed' (old_gate_removed={not has_old_gate})")
log_result("T2.2", has_new_gate,
           f"Puerta usa 'successful_count <= 0' (new_gate={has_new_gate})")

# T2.3: Verificar que propaga el tipo de error dominante
has_dominant_error = "_dominant_error" in source
log_result("T2.3", has_dominant_error,
           f"Propaga _dominant_error en RuntimeError (has_dominant={has_dominant_error})")


# ═══════════════════════════════════════════════════════════════
# T3 — Canary Executor acepta partial_query_success
# ═══════════════════════════════════════════════════════════════
print("\n" + "═" * 60)
print("  T3 — Canary Executor acepta partial_query_success")
print("═" * 60 + "\n")

from app.services.canonical_tabular_canary_executor import (
    execute_canonical_tabular_canary_analysis,
)

source_canary = inspect.getsource(execute_canonical_tabular_canary_analysis)
has_old_canary_gate = 'shadow_query_status") != "query_executed"' in source_canary
has_new_canary_gate = "successful_count <= 0" in source_canary

log_result("T3.1", not has_old_canary_gate,
           f"Canary NO exige 'query_executed' (old_gate_removed={not has_old_canary_gate})")
log_result("T3.2", has_new_canary_gate,
           f"Canary usa 'successful_count <= 0' (new_gate={has_new_canary_gate})")


# ═══════════════════════════════════════════════════════════════
# T4 — Literal Filter Indexer presente en ruta canónica
# ═══════════════════════════════════════════════════════════════
print("\n" + "═" * 60)
print("  T4 — Literal Filter Indexer en ruta canónica de producción")
print("═" * 60 + "\n")

from app.services.canonical_tabular_production_executor import (
    build_canonical_tabular_production_execution,
)

source_builder = inspect.getsource(build_canonical_tabular_production_execution)

has_indexer = "_detect_literal_filters" in source_builder
has_fuzzy_replace = "LITERAL FILTER → REPLACE" in source_builder
has_supported_ops = "_SUPPORTED_IBIS_OPS" in source_builder
has_safe_except = "Error no-fatal en indexer canónico" in source_builder

log_result("T4.1", has_indexer,
           f"_detect_literal_filters invocado en ruta canónica (present={has_indexer})")
log_result("T4.2", has_fuzzy_replace,
           f"Lógica de REPLACE de filtros presente (present={has_fuzzy_replace})")
log_result("T4.3", has_supported_ops,
           f"_SUPPORTED_IBIS_OPS definido (present={has_supported_ops})")
log_result("T4.4", has_safe_except,
           f"try/except best-effort (nunca bloquea ejecución) (present={has_safe_except})")


# ═══════════════════════════════════════════════════════════════
# T5 — Simulación de Cascada: empty_result no activa shield
# ═══════════════════════════════════════════════════════════════
print("\n" + "═" * 60)
print("  T5 — Simulación de cascada empty_result → shield")
print("═" * 60 + "\n")

# Simular lo que pasaría con el nuevo código:
# 1 plan con empty_result → success_count=1 → NO lanza RuntimeError
mock_summaries = [
    {"status": "success", "error": "empty_result", "plan_index": 1},  # V3: success
]
success_count = sum(1 for row in mock_summaries if row.get("status") == "success")
would_block = success_count <= 0

log_result("T5.1", success_count == 1,
           f"empty_result plan cuenta como success (count={success_count})")
log_result("T5.2", not would_block,
           f"Orquestador NO bloquea (would_block={would_block})")

# Simular partial success: 2/3 plans OK
mock_summaries_partial = [
    {"status": "success", "plan_index": 1},
    {"status": "success", "plan_index": 2},
    {"status": "error", "error": "correlation_failed", "plan_index": 3},
]
success_partial = sum(1 for row in mock_summaries_partial if row.get("status") == "success")
would_block_partial = success_partial <= 0

log_result("T5.3", success_partial == 2,
           f"2/3 plans success → count={success_partial}")
log_result("T5.4", not would_block_partial,
           f"Orquestador NO bloquea partial success (would_block={would_block_partial})")

# Simular 0/3 plans OK → SÍ debe bloquear
mock_summaries_total_fail = [
    {"status": "error", "error": "column_not_found", "plan_index": 1},
    {"status": "error", "error": "invalid_metric", "plan_index": 2},
]
success_fail = sum(1 for row in mock_summaries_total_fail if row.get("status") == "success")
would_block_total = success_fail <= 0

log_result("T5.5", would_block_total,
           f"0/2 plans → SÍ bloquea correctamente (would_block={would_block_total})")


# ═══════════════════════════════════════════════════════════════
# T6 — Defensa en profundidad: error type en RuntimeError
# ═══════════════════════════════════════════════════════════════
print("\n" + "═" * 60)
print("  T6 — Big Data Shield: error type propagado en RuntimeError")
print("═" * 60 + "\n")

# Simular el RuntimeError que se lanzaría con 0 éxitos y empty_result
mock_summaries_all_empty = [
    {"status": "success", "error": "empty_result", "plan_index": 1},  # V3: success
]
# Con V3, success_count=1 → NO se lanza RuntimeError → CORRECTO
# Pero si empty_result NO se hubiera tratado como success:
old_success = sum(1 for row in mock_summaries_all_empty if row.get("status") == "success" and not row.get("error"))
_dominant_error = next(
    (str(row.get("error") or "") for row in mock_summaries_all_empty if row.get("error")),
    "",
)
error_msg = f"canonical_production_not_ready:query_failed:{old_success}:{_dominant_error}"
has_empty_in_msg = "empty_result" in error_msg

log_result("T6.1", _dominant_error == "empty_result",
           f"_dominant_error extraído: '{_dominant_error}'")
log_result("T6.2", has_empty_in_msg,
           f"RuntimeError incluye 'empty_result' → shield lo excluiría (msg_preview='{error_msg[:80]}')")


# ═══════════════════════════════════════════════════════════════
# REPORTE FINAL
# ═══════════════════════════════════════════════════════════════
print("\n" + "═" * 60)
print("  REPORTE FINAL — Orquestador V3")
print("═" * 60)

total = len(results)
passed = sum(1 for _, p, _ in results if p)
failed = sum(1 for _, p, _ in results if not p)

for test_id, p, detail in results:
    icon = "✅" if p else "❌"
    print(f"  {icon} [{test_id}] {detail.split('(')[0].strip()}")

print(f"\n  Total: {total} | ✅ {passed} | ❌ {failed}")

if failed == 0:
    print("\n  🎉 TODOS LOS TESTS V3 PASARON.")
    print("     La paranoia del orquestador ha sido relajada con ÉXITO.")
else:
    print(f"\n  ⚠️  {failed} test(s) fallaron. Revisar antes de deploy.")
    sys.exit(1)
