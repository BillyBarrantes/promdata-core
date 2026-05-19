from __future__ import annotations


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
    for filt in existing_filters:
        filter_col = str(getattr(filt, 'column', '')).lower()
        if filter_col == 'is_latest_snapshot' or 'fecha' in filter_col or 'date' in filter_col:
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

    metric_candidates: list[str] = []
    intent_type = getattr(intent, 'type', None)
    if intent_type == "distribution":
        metric_candidates.append(str(getattr(intent, 'metric', '') or ''))
    elif intent_type == "descriptive":
        metric_candidates.extend([str(metric) for metric in getattr(intent, 'metrics', []) or []])
    else:
        return False

    snapshot_keywords = ['stock', 'inventario', 'saldo', 'balance', 'disponible', 'on_hand', 'existencia']
    return any(
        any(keyword in metric.lower() for keyword in snapshot_keywords)
        for metric in metric_candidates
        if metric
    )
