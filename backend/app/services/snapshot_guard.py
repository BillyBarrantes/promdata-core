from __future__ import annotations

# DEPRECATED (2026-07-01): Replaced by precise time_axis check at L35.
# The trend-conditional guard refactor eliminated the need for broad
# pattern matching on filter column names. Keep for historical reference
# only — do NOT re-introduce as primary guard decision logic.
_TEMPORAL_PATTERNS = (
    'fecha', 'date', 'fecaduc', 'vencim', 'expir',
    'caduc', 'vigencia', 'deadline', 'due_date',
)


def should_apply_latest_snapshot_filter(intent, table_columns: list[str], dataset_contract: dict | None = None) -> bool:
    """
    Decide si corresponde aplicar el filtro `is_latest_snapshot == True`.
    Mantiene la protección para métricas de snapshot, pero la omite
    cuando el propio análisis usa explícitamente el eje temporal.
    """
    dataset_contract = dataset_contract or {}

    if 'is_latest_snapshot' not in table_columns:
        return False

    if not dataset_contract.get('snapshot_guard_allowed'):
        if dataset_contract:
            print(
                "🧠 [IBIS SNAPSHOT GUARD] Desactivado por contrato: "
                f"mode={dataset_contract.get('dataset_mode')}"
            )
        return False

    existing_filters = getattr(intent, 'filters', []) or []
    time_axis_val = str(dataset_contract.get('time_axis') or '').strip()
    time_axis_lower = time_axis_val.lower()
    for filt in existing_filters:
        filter_col = str(getattr(filt, 'column', '')).lower()
        if filter_col == 'is_latest_snapshot':
            return False
        if time_axis_lower and filter_col == time_axis_lower:
            print(
                "🧠 [IBIS SNAPSHOT GUARD] Omitido por filtro explícito en "
                f"time_axis={time_axis_val}"
            )
            return False

    time_axis = str(dataset_contract.get('time_axis') or '').strip()
    temporal_references = set()
    for attr in ("date_column", "dimension"):
        value = getattr(intent, attr, None)
        if value:
            temporal_references.add(str(value))
    for group_col in list(getattr(intent, 'group_by', None) or []):
        if group_col:
            temporal_references.add(str(group_col))

    if time_axis and time_axis in temporal_references:
        print(
            "🧠 [IBIS SNAPSHOT GUARD] Omitido por análisis temporal explícito: "
            f"time_axis={time_axis}"
        )
        return False

    # ═══════════════════════════════════════════════════════════════════
    # ADR-TEMPORAL-001: Trend-Conditional Snapshot Guard
    # Date: 2026-07-01
    # Status: ACCEPTED — DO NOT MODIFY without test_temporal_fortress.py GREEN
    #
    # DECISION: Los intents type="trend" SOLO omiten el snapshot guard
    # cuando su dimension temporal (date_column/dimension) coincide con
    # el time_axis del contrato del dataset.
    #
    # RAZON: Un trend sobre una columna temporal DIFERENTE al time_axis
    # (ej. fecha de vencimiento vs fecha de stock) NO es evolucion
    # historica — es agregacion cross-temporal que DEBE aislarse al
    # snapshot mas reciente para evitar sumar stock de multiples periodos.
    #
    # RIESGO DE ALTERAR: Si se simplifica a "trend siempre omite guard",
    # los graficos de evolucion mostraran datos inflados (stock de
    # marzo+junio+julio en vez de solo julio). Regresion silenciosa.
    #
    # VALIDACION: test_temporal_fortress.py (T3, T4, T5, T6)
    # ═══════════════════════════════════════════════════════════════════
    intent_type = getattr(intent, 'type', None)

    # Trend intents only skip the guard when their time dimension
    # matches the contract's time_axis (true historical evolution).
    # If the trend uses a different temporal column (e.g., expiry date),
    # the guard still applies to enforce snapshot isolation.
    if intent_type == 'trend':
        trend_dim = (
            str(getattr(intent, 'date_column', '') or '')
            or str(getattr(intent, 'dimension', '') or '')
        ).strip().lower()

        # [FIX 2026-07-04] Verificar contra date_columns del contrato.
        # Si el trend usa CUALQUIER columna temporal del dataset (no solo time_axis),
        # es una evolución histórica legítima. Omitir el guard.
        date_columns = [col.lower() for col in dataset_contract.get('date_columns', []) or []]
        if trend_dim and trend_dim in date_columns:
            print(
                "🧠 [IBIS SNAPSHOT GUARD] Omitido para trend sobre columna temporal "
                f"del contrato: {trend_dim}"
            )
            return False

        if trend_dim and trend_dim == time_axis_lower:
            print(
                "🧠 [IBIS SNAPSHOT GUARD] Omitido para trend sobre time_axis="
                f"{time_axis_val}"
            )
            return False
        print(
            "📸 [IBIS SNAPSHOT GUARD] Trend sobre columna no-time_axis "
            f"({trend_dim}), aplicando guard por contrato"
        )
        return True

    # Contract-based decision: if the dataset IS a snapshot, apply guard
    # Schema-agnostic — no keyword matching needed for known snapshot datasets.
    dataset_mode = str(dataset_contract.get('dataset_mode', '')).strip()
    if dataset_mode in ('snapshot', 'hybrid'):
        print(
            "📸 [IBIS SNAPSHOT GUARD] Aplicado por contrato: "
            f"dataset_mode={dataset_mode}"
        )
        return True

    # Legacy fallback: keyword matching for undetermined datasets
    metric_candidates: list[str] = []
    if intent_type == "distribution":
        metric_candidates.append(str(getattr(intent, 'metric', '') or ''))
    elif intent_type == "descriptive":
        metric_candidates.extend([str(metric) for metric in getattr(intent, 'metrics', []) or []])
    elif intent_type == "diagnostic":
        metric_candidates.extend([str(metric) for metric in getattr(intent, 'metrics', []) or []])
        metric_candidates.append(str(getattr(intent, 'metric', '') or ''))
    else:
        return False

    snapshot_keywords = ['stock', 'inventario', 'saldo', 'balance', 'disponible', 'on_hand', 'existencia']
    return any(
        any(keyword in metric.lower() for keyword in snapshot_keywords)
        for metric in metric_candidates
        if metric
    )
