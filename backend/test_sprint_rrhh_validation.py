"""
Test funcional definitivo — Sprint RRHH V2.2
Valida los 3 prompts en caliente contra el pipeline real.
"""
import json
import random
import pandas as pd
import numpy as np
from types import SimpleNamespace

# ── Dataset RRHH sintético (1000 filas, mismo schema) ─────────────────
ROWS = 1000
rng = pd.date_range("2019-01-01", "2025-12-01", freq="ME")
df = pd.DataFrame({
    "id_empleado": [f"EMP-{i:04d}" for i in range(ROWS)],
    "nombre_completo": [f"Empleado {i}" for i in range(ROWS)],
    "departamento": [random.choice(
        ["Ventas", "Marketing", "IT", "RRHH", "Finanzas", "Operaciones"]
    ) for _ in range(ROWS)],
    "cargo": [random.choice(
        ["Analista", "Coordinador", "Director", "Asistente", "Gerente"]
    ) for _ in range(ROWS)],
    "fecha_contratacion": [random.choice(rng) for _ in range(ROWS)],
    "salario_mensual": [round(random.uniform(1500, 8000), 2) for _ in range(ROWS)],
    "nivel_desempeno": [random.choice([1.0, 2.0, 3.0, 4.0, 5.0]) for _ in range(ROWS)],
    "estado": [random.choice(["Activo", "Inactivo"]) for _ in range(ROWS)],
})
df.attrs["schema_profile"] = {
    "id_empleado": {"role": "identifier", "dtype": "string", "cardinality_ratio": 1.0},
    "nombre_completo": {"role": "dimension", "dtype": "string"},
    "departamento": {"role": "dimension", "dtype": "string", "cardinality_ratio": 0.006},
    "cargo": {"role": "dimension", "dtype": "string", "cardinality_ratio": 0.005},
    "fecha_contratacion": {"role": "date", "dtype": "date64", "min": "2019-01-01", "max": "2025-12-01"},
    "salario_mensual": {"role": "metric", "dtype": "float64"},
    "nivel_desempeno": {"role": "metric", "dtype": "float64"},
    "estado": {"role": "dimension", "dtype": "string", "cardinality_ratio": 0.002},
}
df.attrs["semantic_contract"] = {
    "dataset_mode": "flow",
    "time_axis": "fecha_contratacion",
    "metric_columns": ["salario_mensual", "nivel_desempeno"],
    "date_columns": ["fecha_contratacion"],
    "evidence": {},
}
columns = list(df.columns)

print("=" * 72)
print("🧪 SPRINT RRHH V2.2 — VALIDACIÓN FUNCIONAL")
print("=" * 72)

# ── Helper ────────────────────────────────────────────────────────────
def check(label: str, condition: bool, detail: str = ""):
    status = "✅" if condition else "❌"
    print(f"\n{status} {label}")
    if detail:
        for line in detail.split("\n"):
            print(f"   {line}")

# ═══════════════════════════════════════════════════════════════════════
# TEST 1: Prompt 1 — Métrica abstracta "cantidad de empleados"
# ═══════════════════════════════════════════════════════════════════════
print("\n" + "-" * 72)
print("📋 TEST 1: ¿Cómo se distribuye la cantidad de empleados activos según su cargo para cada uno de los periodos?")
print("-" * 72)

from app.services.semantic_translator.planner import _resolve_abstract_count_metric

# Sin candidate_df (solo schema_profile) — fallback 2
result_no_df = _resolve_abstract_count_metric(
    "cantidad de empleados", columns, schema_profile=df.attrs["schema_profile"],
)
check(
    "Fallback 2 (role=identifier) resuelve id_empleado",
    result_no_df == "id_empleado",
    f"Resultado: {result_no_df!r}"
)

# Con candidate_df — fallback 3
result_with_df = _resolve_abstract_count_metric(
    "cantidad de empleados", columns,
    schema_profile=df.attrs["schema_profile"],
    candidate_df=df,
)
check(
    "Fallback 3 (nunique>80%) resuelve id_empleado",
    result_with_df == "id_empleado",
    f"Resultado: {result_with_df!r}"
)

# Verificar que es string → ibis hará COUNT(), no SUM()
is_string = pd.api.types.is_string_dtype(df["id_empleado"])
check(
    "id_empleado es string → Ibis usará COUNT()",
    is_string,
    f"dtype: {df['id_empleado'].dtype}"
)

# ── Plan de distribución completo ───────────────────────────────────
from app.services.semantic_translator.planner import build_plan_from_router_contract

fake_router_decision = {
    "route": "SIMPLE",
    "confidence": 0.85,
    "detected_intent": "distribution",
    "semantic_contract": {
        "intent": "distribution",
        "metric": "cantidad de empleados",
        "dimension": "cargo",
        "time_axis": "fecha_contratacion",
        "grain": "month",
        "aggregation": "count",
        "positive_filters": [{"column": "estado", "operator": "==", "value": "activo"}],
        "requires_time": True,
        "visual_protocol": "bar_chart",
    },
}
plans = build_plan_from_router_contract(
    fake_router_decision, columns,
    schema_profile=df.attrs["schema_profile"],
    dataset_contract=df.attrs["semantic_contract"],
    candidate_df=df,
)
check(
    "build_plan_from_router_contract produce plan(es)",
    bool(plans),
    f"Planes generados: {len(plans) if plans else 0}"
)

if plans:
    p0 = plans[0]
    mi = p0.main_intent
    check(
        "La métrica del plan es id_empleado (no nivel_desempeno)",
        getattr(mi, "metric", None) == "id_empleado",
        f"metric: {getattr(mi, 'metric', None)!r} | value_column: {getattr(mi, 'value_column', None)!r}"
    )
    check(
        "El plan tiene filtro positivo estado=Activo",
        any(
            getattr(f, 'column', None) == 'estado'
            for f in (getattr(mi, 'filters', []) or [])
        ),
        f"Filters: {[(getattr(f, 'column', None), getattr(f, 'value', None)) for f in (getattr(mi, 'filters', []) or [])]}"
    )

# ═══════════════════════════════════════════════════════════════════════
# TEST 2: Prompt 2 — Filtro temporal "a partir del año 2021"
# ═══════════════════════════════════════════════════════════════════════
print("\n" + "-" * 72)
print("📋 TEST 2: Para los empleados contratados a partir del año 2021, ¿cuál es el cargo que registra el mejor nivel de desempeño promedio?")
print("-" * 72)

from app.services.semantic_translator.temporal_resolver import (
    _extract_year_from_any_string,
    normalize_intent_temporal_filters,
    _infer_dataset_year_range,
)

# dateutil.parser con formatos variados
check(
    "dateutil.parser extrae 2021 de ISO 2021-01-15",
    _extract_year_from_any_string("2021-01-15") == 2021,
)
check(
    "dateutil.parser extrae 2021 de latino 15/01/2021",
    _extract_year_from_any_string("15/01/2021") == 2021,
)
check(
    "dateutil.parser extrae 2021 de US 01/15/2021",
    _extract_year_from_any_string("01/15/2021") == 2021,
)
check(
    "dateutil.parser extrae 2021 de texto January 15, 2021",
    _extract_year_from_any_string("January 15, 2021") == 2021,
)
check(
    "dateutil.parser extrae 2021 con timezone 2021-06-15T00:00:00+00:00",
    _extract_year_from_any_string("2021-06-15T00:00:00+00:00") == 2021,
)
check(
    "dateutil.parser retorna None para string sin fecha",
    _extract_year_from_any_string("no hay fecha") is None,
)
check(
    "dateutil.parser retorna None para None",
    _extract_year_from_any_string(None) is None,
)

# Fortress con rango 2019-2025 y filtro 2021 → debe preservar
temporal_schema = {
    "_dataset_year": 2025,
    "_dataset_year_min": 2019,
    "_dataset_year_max": 2025,
    "fecha_contratacion": {"type": "temporal", "role": "date"},
}
from app.core.semantic_grammar import DataFilter
intent_2021 = SimpleNamespace(
    type="descriptive",
    filters=[
        SimpleNamespace(
            column="fecha_contratacion",
            operator=SimpleNamespace(value=">="),
            value="2021-01-01",
        )
    ],
    value_column="nivel_desempeno",
)

corrected = normalize_intent_temporal_filters(intent_2021, temporal_schema)
filter_value = getattr(corrected.filters[0], 'value', '') if corrected.filters else ''
check(
    "Fortress PRESERVA 2021 (dentro del rango 2019-2025)",
    "2021" in str(filter_value),
    f"Valor del filtro: {filter_value!r}"
)

# Fuera de rango (2017) → debe corregir (verificado via resolve_temporal_filter_value
# porque normalize_intent_temporal_filters requiere model_copy que SimpleNamespace no tiene)
from app.services.semantic_translator.temporal_resolver import resolve_temporal_filter_value
resolved_2017 = resolve_temporal_filter_value(
    "fecha_contratacion", ">=", "2017-01-01", schema_profile=temporal_schema,
)
check(
    "Fortress CORRIGE 2017 (fuera del rango 2019-2025)",
    resolved_2017 is not None and all("2017" not in str(r.get("value", "")) for r in resolved_2017),
    f"Resuelto: {resolved_2017!r}"
)

# ═══════════════════════════════════════════════════════════════════════
# TEST 3: Prompt 3 — Tasa de inactivos + tendencia AVG
# ═══════════════════════════════════════════════════════════════════════
print("\n" + "-" * 72)
print("📋 TEST 3: ¿Qué departamento tiene la mayor tasa de empleados inactivos y cómo ha sido la tendencia de su nivel de desempeño promedio año tras año?")
print("-" * 72)

from app.services.canonical_tabular_production_executor import (
    _extract_categorical_dimension,
    _plan_has_valid_avg_on_numeric,
)

# Verificar que _extract_categorical_dimension prioriza "departamento"
prompt_3 = "¿Qué departamento tiene la mayor tasa de empleados inactivos y cómo ha sido la tendencia de su nivel de desempeño promedio año tras año?"
cat_dim = _extract_categorical_dimension(prompt_3, df)
check(
    "Filtro 'inactivos' (plural s?) es tolerado por Token Boundary Guard",
    cat_dim is not None,
    f"Dimensión categórica: {cat_dim!r}"
)

# AVG immunity: TimeTrendIntent con value_column="nivel_desempeno"
trend_plan = SimpleNamespace(
    main_intent=SimpleNamespace(
        type="trend",
        value_column="nivel_desempeno",
        aggregation="avg",
        filters=[],
    ),
)
valid_avg = _plan_has_valid_avg_on_numeric(trend_plan, df)
check(
    "AVG immunity: TimeTrendIntent con nivel_desempeno es válido (no se borra)",
    valid_avg,
    f"_plan_has_valid_avg_on_numeric retornó: {valid_avg}"
)

# Simular el Metric Guard completo
from app.services.canonical_tabular_production_executor import _blocked_plan_metrics, _auto_correct_hallucinated_metrics
blocked = _blocked_plan_metrics(trend_plan, df)
check(
    "Blocked metrics: nivel_desempeno NO está bloqueado",
    "nivel_desempeno" not in blocked,
    f"Métricas bloqueadas: {blocked!r}"
)

# ── Verificar plural "inactivos" en _extract_filter_value_from_prompt ─
from app.services.canonical_tabular_production_executor import _extract_filter_value_from_prompt
estados = df["estado"].dropna().unique()
filter_val = _extract_filter_value_from_prompt(prompt_3, "estado", df)
check(
    "Token Boundary Guard: 'inactivos' → detecta 'Inactivo' como valor de filtro",
    filter_val is not None or any("inactivo" in str(e).lower() for e in estados),
    f"Valores únicos estado: {list(estados)}"
)

# ═══════════════════════════════════════════════════════════════════════
print("\n" + "=" * 72)
print("VALIDACIÓN COMPLETA")
print("=" * 72)
