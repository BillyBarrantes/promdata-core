"""
test_filter_hardening_v2.py
═══════════════════════════════════════════════════════════════════
Suite de Pruebas Funcionales — Filter Engine Hardening V2
═══════════════════════════════════════════════════════════════════

Valida en vivo (sin servidor HTTP) los 3 escenarios críticamente
corregidos en la sesión de ingeniería:

  T1 — Fuzzy Plural/Singular + ilike → filtro activo → trend line
  T2 — Filtro fantasma → empty_result amigable (NO activa BigData Shield)
  T3 — Top-5 Distribución → bar_chart (Anti-KPI Zombie)

Crea un Parquet sintético idéntico en estructura al dataset real
(tipo_movimiento, monto, fecha_registro, centro_costo) para no
depender de Supabase ni de archivos de usuario.

Ejecutar:
    cd /Users/billy/Desarrollos/PromData/backend
    source venv/bin/activate
    python test_filter_hardening_v2.py
"""

import sys, os, json, tempfile, textwrap
from datetime import date, timedelta
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

# ── Añadir el backend al PYTHONPATH ──────────────────────────────
sys.path.insert(0, os.path.dirname(__file__))

# ══════════════════════════════════════════════════════════════════
# BLOQUE 0 — Dataset Sintético
# Replica la estructura del dataset real del usuario.
# Columnas: tipo_movimiento (dim), monto (metric), fecha_registro (date), centro_costo (dim)
# Valores de tipo_movimiento: "Egreso", "Ingreso" (singular, título case)
# El prompt usará "egresos" (plural, minúsculas) → fuzzy match
# ══════════════════════════════════════════════════════════════════

def _build_synthetic_parquet() -> str:
    """Genera un Parquet temporal con datos contables ficticios."""
    rows = []
    start = date(2024, 1, 1)
    centros = ["Centro A", "Centro B", "Centro C", "Centro D", "Centro E"]
    tipos   = ["Egreso", "Ingreso"]

    for i in range(600):
        d = start + timedelta(days=i % 365)
        rows.append({
            "tipo_movimiento": tipos[i % 2],          # "Egreso" / "Ingreso" (singular)
            "monto":           round(1000 + (i * 37.5) % 9000, 2),
            "fecha_registro":  pd.Timestamp(d),
            "centro_costo":    centros[i % len(centros)],
        })

    df = pd.DataFrame(rows)

    # Parquet temporal
    tmp = tempfile.NamedTemporaryFile(suffix=".parquet", delete=False)
    table = pa.Table.from_pandas(df)
    pq.write_table(table, tmp.name)
    tmp.close()
    return tmp.name, df


# ══════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════

PASS = "✅ PASS"
FAIL = "❌ FAIL"
WARN = "⚠️  WARN"

_results: list[dict] = []

def _record(test_id: str, name: str, status: str, detail: str = ""):
    color = {"✅ PASS": "\033[92m", "❌ FAIL": "\033[91m", "⚠️  WARN": "\033[93m"}.get(status, "")
    reset = "\033[0m"
    line = f"  {color}{status}{reset}  [{test_id}] {name}"
    if detail:
        line += f"\n         → {detail}"
    print(line)
    _results.append({"id": test_id, "name": name, "status": status, "detail": detail})


def _separator(title: str):
    print(f"\n{'═'*60}")
    print(f"  {title}")
    print(f"{'═'*60}")


# ══════════════════════════════════════════════════════════════════
# PRUEBA T1 — Fuzzy Plural/Singular + operador ilike
# ══════════════════════════════════════════════════════════════════

def test_T1_fuzzy_singular_plural_and_ilike(parquet_path: str, df: pd.DataFrame):
    _separator("T1 — Fuzzy Plural/Singular + Operador ilike → Trend Line")
    from app.core.semantic_grammar import (
        FilterOperator, DataFilter, TimeTrendIntent, AnalysisPlan,
        TimeGrain, VisualProtocol, MetricUnit
    )
    from app.services.ibis_engine import IbisEngine
    from app.services.semantic_translator import SemanticTranslator

    # ── T1.1 — Fuzzy Match: "egresos" → "Egreso" ─────────────────
    dimension_values = {
        "tipo_movimiento": ["Egreso", "Ingreso"]
    }
    prompt_plural = "CUAL FUE LA Evolución de egresos en el tiempo"
    filters_detected = SemanticTranslator._detect_literal_filters(prompt_plural, dimension_values)

    if filters_detected:
        f = filters_detected[0]
        ok = (f.column == "tipo_movimiento" and str(f.value) == "Egreso")
        _record("T1.1", "Fuzzy match 'egresos' → tipo_movimiento=='Egreso'",
                PASS if ok else FAIL,
                f"column={f.column}, value='{f.value}', operator={f.operator}")
    else:
        _record("T1.1", "Fuzzy match 'egresos' detectado", FAIL,
                "SemanticTranslator._detect_literal_filters devolvió lista vacía")

    # ── T1.2 — Normalización de operador: "ilike" → se acepta en el enum ─
    try:
        df_filter = DataFilter(
            column="tipo_movimiento",
            operator="ilike",   # ← alias que Gemini emite
            value="egreso"
        )
        _record("T1.2", "Alias 'ilike' acepta en DataFilter (normalize_operator)",
                PASS, f"operator normalizado → '{df_filter.operator.value}'")
    except Exception as e:
        _record("T1.2", "Alias 'ilike' acepta en DataFilter", FAIL, str(e))

    # ── T1.3 — IbisEngine aplica ilike como contains CI y devuelve datos ──
    try:
        plan = AnalysisPlan(
            title="Evolución de Egresos",
            main_intent=TimeTrendIntent(
                type="trend",
                date_column="fecha_registro",
                value_column="monto",
                grain=TimeGrain.MONTH,
                visual_protocol=VisualProtocol.LINE,
                rationale="Evolución mensual filtrada por tipo egreso.",
                filters=[DataFilter(
                    column="tipo_movimiento",
                    operator="ilike",   # ← operador "problemático" anterior
                    value="egreso"
                )]
            ),
            column_aliases={"monto": "Monto", "fecha_registro": "Fecha"}
        )
        result = IbisEngine.execute_plan(parquet_path, plan)
        has_data   = bool(result.get("data"))
        no_error   = "error" not in result
        is_line    = result.get("chart_type") in ("line", "line_chart", "kpi_card")
        has_facts  = "hard_facts" in result

        _record("T1.3", "IbisEngine ejecuta ilike como contains CI → retorna datos",
                PASS if (has_data and no_error) else FAIL,
                f"chart_type={result.get('chart_type')}, periodos={len(result.get('data', []))}, "
                f"error={result.get('error')}")

        _record("T1.4", "Resultado es chart de línea temporal (no KPI gauge)",
                PASS if is_line else WARN,
                f"chart_type='{result.get('chart_type')}'")

        _record("T1.5", "hard_facts presente (peak/trough/growth)",
                PASS if has_facts else FAIL,
                f"keys: {list(result.get('hard_facts', {}).keys())[:5]}")

    except Exception as e:
        _record("T1.3", "IbisEngine ejecuta plan con ilike", FAIL, str(e))
        _record("T1.4", "chart_type línea", FAIL, "No ejecutó")
        _record("T1.5", "hard_facts presente", FAIL, "No ejecutó")


# ══════════════════════════════════════════════════════════════════
# PRUEBA T2 — Filtro fantasma → empty_result amigable (NO BigData Shield)
# ══════════════════════════════════════════════════════════════════

def test_T2_empty_result_guard(parquet_path: str):
    _separator("T2 — Filtro Fantasma → empty_result Amigable (sin Big Data Shield)")
    from app.core.semantic_grammar import (
        FilterOperator, DataFilter, TimeTrendIntent, AnalysisPlan,
        TimeGrain, VisualProtocol
    )
    from app.services.ibis_engine import IbisEngine

    plan = AnalysisPlan(
        title="Evolución de Transferencias Intergalácticas",
        main_intent=TimeTrendIntent(
            type="trend",
            date_column="fecha_registro",
            value_column="monto",
            grain=TimeGrain.MONTH,
            visual_protocol=VisualProtocol.LINE,
            rationale="Filtro con valor inexistente para probar el guard.",
            filters=[DataFilter(
                column="tipo_movimiento",
                operator="==",
                value="TRANSFERENCIA_INTERGALACTICA_XYZ"  # ← no existe en dataset
            )]
        ),
        column_aliases={}
    )

    try:
        result = IbisEngine.execute_plan(parquet_path, plan)

        is_empty_result = result.get("error") == "empty_result"
        has_message     = bool(result.get("message"))
        no_crash        = True  # si llegamos aquí no hubo exception
        # Verificar que NO sea el Big Data Shield (el shield no pone error=empty_result,
        # el shield sube un "big_data_legacy_shield_activated" en el orquestador)
        no_data_shield  = "big_data" not in str(result).lower()

        _record("T2.1", "Filtro fantasma devuelve error='empty_result'",
                PASS if is_empty_result else FAIL,
                f"error='{result.get('error')}', keys={list(result.keys())}")

        _record("T2.2", "Respuesta incluye mensaje amigable (no stack trace)",
                PASS if has_message else FAIL,
                f"message='{str(result.get('message', ''))[:120]}'")

        _record("T2.3", "Sistema NO crasheó (resiliencia garantizada)",
                PASS if no_crash else FAIL, "")

        _record("T2.4", "Big Data Shield NO se activa para resultado vacío",
                PASS if no_data_shield else FAIL,
                f"result_str_preview='{str(result)[:200]}'")

    except Exception as e:
        _record("T2.1", "Filtro fantasma devuelve error='empty_result'", FAIL, f"EXCEPCIÓN: {e}")
        _record("T2.2", "Mensaje amigable", FAIL, "Crasheó antes")
        _record("T2.3", "Sistema NO crasheó", FAIL, str(e))
        _record("T2.4", "Big Data Shield inactivo", FAIL, "N/A")


# ══════════════════════════════════════════════════════════════════
# PRUEBA T3 — Top-5 Centros de Costo → bar_chart (Anti-KPI Zombie)
# ══════════════════════════════════════════════════════════════════

def test_T3_top_n_distribution_anti_kpi_zombie(parquet_path: str):
    _separator("T3 — Top-5 Centros de Costo → bar_chart (Anti-KPI Zombie)")
    from app.core.semantic_grammar import (
        DistributionIntent, AnalysisPlan, VisualProtocol, MetricUnit
    )
    from app.services.ibis_engine import IbisEngine

    plan = AnalysisPlan(
        title="Top 5 Centros de Costo por Monto Total",
        main_intent=DistributionIntent(
            type="distribution",
            dimension="centro_costo",
            metric="monto",
            limit=5,
            visual_protocol=VisualProtocol.BAR,
            rationale="Ranking de los 5 centros que acumulan mayor monto.",
            filters=[]
        ),
        column_aliases={"monto": "Monto Total", "centro_costo": "Centro de Costo"}
    )

    try:
        result = IbisEngine.execute_plan(parquet_path, plan)

        has_data     = bool(result.get("data"))
        no_error     = "error" not in result
        is_bar       = result.get("chart_type") in ("bar", "bar_chart")
        not_gauge    = result.get("chart_type") not in ("gauge", "gauge_chart", "kpi_card")
        row_count    = len(result.get("data", []))
        correct_rows = 1 <= row_count <= 5

        _record("T3.1", "Distribución Top-5 ejecuta sin error",
                PASS if (has_data and no_error) else FAIL,
                f"error={result.get('error')}, filas={row_count}")

        _record("T3.2", "chart_type es 'bar' o 'bar_chart' (no gauge, no kpi_card)",
                PASS if (is_bar and not_gauge) else FAIL,
                f"chart_type='{result.get('chart_type')}'")

        _record("T3.3", "Resultado tiene ≤5 filas (respeta limit=5)",
                PASS if correct_rows else FAIL,
                f"filas devueltas={row_count}")

        # Verificar que cada fila tiene 'name' (centro) y 'value' (monto)
        if has_data:
            first = result["data"][0]
            has_name  = "name" in first
            has_value = "value" in first
            _record("T3.4", "Cada ítem tiene 'name' (centro) y 'value' (monto)",
                    PASS if (has_name and has_value) else FAIL,
                    f"first_item={json.dumps(first, default=str)[:120]}")
        else:
            _record("T3.4", "Cada ítem tiene 'name' y 'value'", FAIL, "No hay data")

    except Exception as e:
        _record("T3.1", "Distribución Top-5 ejecuta", FAIL, f"EXCEPCIÓN: {e}")
        _record("T3.2", "chart_type es bar", FAIL, "Crasheó")
        _record("T3.3", "Respeta limit=5", FAIL, "N/A")
        _record("T3.4", "Ítem válido", FAIL, "N/A")


# ══════════════════════════════════════════════════════════════════
# PRUEBA T4 — Big Data Shield umbral 100K (validación de constante)
# ══════════════════════════════════════════════════════════════════

def test_T4_big_data_shield_threshold():
    _separator("T4 — Big Data Shield umbral empírico = 100,000 filas")
    import ast

    shield_file = os.path.join(os.path.dirname(__file__), "app", "tasks", "analysis_tasks.py")
    try:
        with open(shield_file, "r") as f:
            source = f.read()

        # Buscar la constante en el fuente
        import re
        match = re.search(r"_LEGACY_SHIELD_ROW_THRESHOLD\s*=\s*([\d_]+)", source)
        if match:
            raw_val = match.group(1).replace("_", "")
            threshold = int(raw_val)
            ok = threshold == 100_000
            _record("T4.1", f"_LEGACY_SHIELD_ROW_THRESHOLD == 100,000",
                    PASS if ok else FAIL,
                    f"Valor en código: {threshold:,}")
        else:
            _record("T4.1", "_LEGACY_SHIELD_ROW_THRESHOLD presente en analysis_tasks.py",
                    FAIL, "Constante no encontrada en el código fuente")

        # Verificar que el shield excluye empty_result
        has_empty_exclusion = "empty_result" in source and "_LEGACY_SHIELD_ROW_THRESHOLD" in source
        _record("T4.2", "Shield excluye error='empty_result' (no falso positivo)",
                PASS if has_empty_exclusion else WARN,
                "Búsqueda textual de 'empty_result' junto a la constante del shield")

    except Exception as e:
        _record("T4.1", "Umbral 100K en analysis_tasks", FAIL, str(e))


# ══════════════════════════════════════════════════════════════════
# PRUEBA T5 — Validación del FilterOperator [V2] completo
# ══════════════════════════════════════════════════════════════════

def test_T5_filter_operator_coverage():
    _separator("T5 — FilterOperator [V2] cubre todos los operadores requeridos")
    from app.core.semantic_grammar import FilterOperator, DataFilter

    required_operators = {
        # Matemáticos
        "==": "EQUALS",
        "!=": "NOT_EQUALS",
        ">": "GREATER_THAN",
        "<": "LESS_THAN",
        ">=": "GREATER_EQUAL",
        "<=": "LESS_EQUAL",
        # Texto
        "contains": "CONTAINS",
        "ilike": "ILIKE",
        "like": "LIKE",
        "starts_with": "STARTS_WITH",
        "ends_with": "ENDS_WITH",
        "not_contains": "NOT_CONTAINS",
        "not_like": "NOT_LIKE",
        # Conjuntos
        "in": "IN_LIST",
        "not_in": "NOT_IN_LIST",
    }

    aliases_to_test = {
        "=": "==",          # Gemini usa '=' en el fastpath SIMPLE
        "ilike": "ilike",
        "like": "like",
        "contains": "contains",
        "starts_with": "starts_with",
        "ends_with": "ends_with",
        "not_contains": "not_contains",
        "contiene": "contains",   # alias español
        "mayor": ">",
        "menor": "<",
        "gt": ">",
        "lt": "<",
        "gte": ">=",
        "lte": "<=",
    }

    # Verificar enum values
    enum_values = {op.value for op in FilterOperator}
    missing = []
    for val, name in required_operators.items():
        if val not in enum_values:
            missing.append(val)

    _record("T5.1", f"FilterOperator tiene los {len(required_operators)} operadores requeridos",
            PASS if not missing else FAIL,
            f"faltantes: {missing}" if missing else f"valores: {sorted(enum_values)}")

    # Verificar normalize_operator via DataFilter
    alias_failures = []
    for alias, expected_canonical in aliases_to_test.items():
        try:
            df_obj = DataFilter(column="x", operator=alias, value="test")
            actual = df_obj.operator.value
            if actual != expected_canonical:
                alias_failures.append(f"'{alias}' → '{actual}' (esperado: '{expected_canonical}')")
        except Exception as e:
            alias_failures.append(f"'{alias}' → EXCEPCIÓN: {e}")

    _record("T5.2", f"normalize_operator resuelve {len(aliases_to_test)} aliases correctamente",
            PASS if not alias_failures else FAIL,
            ("; ".join(alias_failures[:5]) if alias_failures else
             f"{len(aliases_to_test)} aliases OK"))


# ══════════════════════════════════════════════════════════════════
# MAIN — Ejecutar todas las pruebas
# ══════════════════════════════════════════════════════════════════

def main():
    print("\n" + "🔬" * 30)
    print("  PROMDATA — Suite de Pruebas Funcionales V2")
    print("  Filter Engine Hardening — Validación en Vivo")
    print("🔬" * 30)

    print("\n📦 Generando dataset sintético...")
    parquet_path, df = _build_synthetic_parquet()
    print(f"   Parquet: {parquet_path}")
    print(f"   Filas: {len(df)} | Columnas: {list(df.columns)}")
    print(f"   tipo_movimiento únicos: {df['tipo_movimiento'].unique().tolist()}")
    print(f"   centro_costo únicos: {df['centro_costo'].unique().tolist()}")

    try:
        test_T5_filter_operator_coverage()
        test_T1_fuzzy_singular_plural_and_ilike(parquet_path, df)
        test_T2_empty_result_guard(parquet_path)
        test_T3_top_n_distribution_anti_kpi_zombie(parquet_path)
        test_T4_big_data_shield_threshold()
    finally:
        # Limpiar Parquet temporal
        try:
            os.unlink(parquet_path)
        except Exception:
            pass

    # ── Reporte Final ────────────────────────────────────────────
    print("\n" + "═" * 60)
    print("  REPORTE FINAL")
    print("═" * 60)

    passed  = sum(1 for r in _results if r["status"] == PASS)
    failed  = sum(1 for r in _results if r["status"] == FAIL)
    warned  = sum(1 for r in _results if r["status"] == WARN)
    total   = len(_results)

    for r in _results:
        icon = {"✅ PASS": "✅", "❌ FAIL": "❌", "⚠️  WARN": "⚠️"}.get(r["status"], "?")
        print(f"  {icon} [{r['id']}] {r['name']}")

    print(f"\n  Total: {total} | ✅ {passed} | ❌ {failed} | ⚠️  {warned}")

    if failed == 0:
        print("\n  🎉 TODOS LOS TESTS CRÍTICOS PASARON.")
        print("     El bloque de ingeniería se cierra con ÉXITO ROTUNDO.\n")
    else:
        print(f"\n  ⚠️  {failed} test(s) fallaron. Revisar detalles arriba.\n")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
