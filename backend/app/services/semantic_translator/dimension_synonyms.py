"""
[V1] Diccionario de sinónimos de dimensiones + motor de resolución por stems.

Domain-agnostic: mapea conceptos de usuario a STEMS que deben contener las
columnas reales del dataset. NUNCA hardcodea nombres de columnas específicas
de un tenant (multi-tenant safe, §7 anti-patterns de AGENTS.md).

Se invoca desde ibis_engine (Data Shield) cuando el LLM alucina el nombre de
una columna de dimensión, como paso de auto-reparación ANTES de retornar el
error duro al usuario.

Principios:
- Degradación segura: si no hay match confiable, retorna None y el caller
  preserva el error original (fail closed, nunca adivina).
- Determinista: el orden de resultado NO depende del set de columnas.
- Schema-agnostic: la resolución se basa en los stems y en el contenido de
  available_columns, no en un schema fijo.
"""
from __future__ import annotations

from difflib import SequenceMatcher

# ── Diccionario de stems conceptuales ──────────────────────────────────
# Cada entrada: {concepto_alucinado: [stems que la columna real debe contener]}.
# El orden de los stems define la PRIORIDAD: el primero encontrado será la
# dimensión principal y el resto se agregan como group_by.
#
# REGLAS DE CURACIÓN (críticas para evitar colisiones con métricas):
#   - PROHIBIDO "recorrido" → colisiona con "km_recorridos" (métrica continua).
#   - PROHIBIDO "unidad"    → colisiona con "unidades_vendidas" (métrica).
#   - PROHIBIDO "item"      → colisiona con "items_vendidos" (métrica).
#   - PROHIBIDO "dia"/"ano" → colisionan con "diagnostico"/"hermano".
SYNONYM_STEMS: dict[str, list[str]] = {
    # ── Geolocalización / rutas ─────────────────────────────────────
    "ruta": ["origen", "destino", "tramo", "trayecto", "corredor"],
    "rutas": ["origen", "destino", "tramo", "trayecto", "corredor"],
    "trayecto": ["origen", "destino", "tramo", "trayecto", "corredor"],
    "viaje": ["origen", "destino", "tramo", "trayecto", "corredor"],
    "ubicacion": ["origen", "destino", "direccion", "sede", "distrito"],
    "zona": ["zona", "region", "distrito", "sede", "provincia"],
    "region": ["zona", "region", "distrito", "sede", "provincia"],
    # ── Vehículos / equipos ─────────────────────────────────────────
    "vehiculo": ["placa", "vehiculo", "camion", "flota", "unidad_id", "id_unidad"],
    "equipo": ["placa", "equipo", "maquina", "flota", "unidad_id", "id_unidad"],
    # ── Tiempo ──────────────────────────────────────────────────────
    "tiempo": ["fecha", "periodo", "year", "mes", "trimestre"],
    "periodo": ["fecha", "periodo", "year", "mes", "trimestre"],
    "fecha": ["fecha", "date", "periodo"],
    # ── Entidades de negocio ────────────────────────────────────────
    "producto": ["producto", "articulo", "sku"],
    "cliente": ["cliente", "customer", "comprador"],
    "proveedor": ["proveedor", "supplier", "vendor"],
    "categoria": ["categoria", "clase", "segmento", "grupo"],
    "responsable": ["responsable", "operador", "conductor", "chofer"],
}

# Columnas internas del engine que nunca deben convertirse en dimensión.
_INTERNAL_COLUMN_PREFIX = "_"
_INTERNAL_COLUMN_NAMES = frozenset({"is_latest_snapshot"})


def _iter_public_columns(available_columns: set[str]) -> list[str]:
    """Devuelve columnas deterministas y sin columnas internas del engine.

    Determinista: `sorted()` fija el orden entre reinicios del worker (un set
    de Python itera en orden no reproducible). Internas: excluye claves como
    `_dataset_year`, `_dataset_year_min/max`, `_snapshot_resolved_date` y
    `is_latest_snapshot` (mismo patrón que canonical_schema_profiler.py).

    Args:
        available_columns: Set de columnas disponibles en el dataset.

    Returns:
        Lista ordenada alfabéticamente de columnas públicas.
    """
    return sorted(
        c
        for c in available_columns
        if not str(c).startswith(_INTERNAL_COLUMN_PREFIX)
        and c not in _INTERNAL_COLUMN_NAMES
    )


def _match_stems_to_columns(
    stems: list[str],
    available_columns: set[str],
) -> list[str] | None:
    """Resuelve stems a columnas reales con orden determinista por prioridad.

    El orden de `stems` define la prioridad (ej: "origen" antes que "destino").
    Dentro de cada stem, las columnas se recorren alfabéticamente, de modo que
    el resultado es 100% reproducible entre ejecuciones.

    Args:
        stems: Lista de stems en orden de prioridad.
        available_columns: Set de columnas disponibles en el dataset.

    Returns:
        Lista de columnas que contienen algún stem, o None si no hay match.
    """
    public_columns = _iter_public_columns(available_columns)
    matched: list[str] = []
    for stem in stems:  # prioridad: orden declarado en el diccionario
        stem_lower = stem.lower()
        for col in public_columns:  # determinista: orden alfabético
            if stem_lower in col.lower() and col not in matched:
                matched.append(col)
    return matched if matched else None


def resolve_synonym(
    term: str,
    available_columns: set[str],
    fuzzy_threshold: float = 0.6,
) -> list[str] | None:
    """Resuelve un término alucinado del usuario a columnas reales del dataset.

    Estrategia en cascada:
    1. Match exacto contra las keys de SYNONYM_STEMS.
    2. Match difuso (SequenceMatcher) contra las keys del diccionario.
    3. Si nada coincide, retorna None (fail closed → el caller preserva el
       error original). No se inventan columnas ni se hacen matches parciales
       inseguros que podrían capturar métricas.

    Args:
        term: Término de dimensión producido por el LLM (posiblemente alucinado).
        available_columns: Set de columnas disponibles en el dataset.
        fuzzy_threshold: Umbral mínimo de similitud para el match difuso.

    Returns:
        Lista determinista de columnas, o None si no hay match confiable.
    """
    normalized = str(term).lower().strip()

    # 1. Match exacto contra el diccionario
    if normalized in SYNONYM_STEMS:
        result = _match_stems_to_columns(SYNONYM_STEMS[normalized], available_columns)
        if result:
            return result

    # 2. Match difuso contra las keys del diccionario
    best_key: str | None = None
    best_score = 0.0
    for key in SYNONYM_STEMS:
        score = SequenceMatcher(None, normalized, key).ratio()
        if score > best_score:
            best_score = score
            best_key = key
    if best_key and best_score >= fuzzy_threshold:
        return _match_stems_to_columns(SYNONYM_STEMS[best_key], available_columns)

    # 3. Fail closed: sin match confiable no se adivina.
    return None


__all__ = [
    "SYNONYM_STEMS",
    "resolve_synonym",
]
