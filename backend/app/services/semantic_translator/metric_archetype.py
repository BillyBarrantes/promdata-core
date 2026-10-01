from __future__ import annotations

import math
from typing import Any, Optional

import numpy as np
import pandas as pd

from app.services.semantic_translator.core import normalize_semantic_text
from app.core.temporal_axis import series_monotonic_direction


def _safe_finite(values: np.ndarray) -> np.ndarray:
    return values[np.isfinite(values)]


def _to_finite_numeric(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce").dropna()
    if numeric.empty:
        return numeric.astype(float)
    values = numeric.to_numpy(dtype=np.float64, na_value=np.nan)
    mask = np.isfinite(values)
    return pd.Series(values[mask], index=numeric.index[mask])


def repeated_sum_ratio(series: pd.Series) -> Optional[float]:
    """Fracción del total (en valor absoluto) aportada por valores repetidos.

    Señal estructural de **no aditividad a nivel documento/encabezado**: si un
    monto se repite en la tabla (p.ej. el total de una factura/cabecera repetido
    en cada línea de detalle), sumarlo cuenta esos valores N veces. Este ratio
    mide exactamente la masa re-contada, sin conocer el dominio ni el nombre de
    ninguna columna.

    0.0 → todos los valores distintos (aditiva).
    ~1.0 → la mayor parte del total proviene de valores repetidos (no aditiva).
    """
    clean = _to_finite_numeric(series)
    if clean.empty:
        return None
    absolute = clean.abs()
    total = float(absolute.sum())
    if total <= 0:
        return None
    counts = absolute.value_counts()
    repeated = counts[counts > 1]
    if repeated.empty:
        return 0.0
    mass = float((repeated.index.to_numpy(dtype=np.float64) * (repeated.to_numpy(dtype=np.float64) - 1.0)).sum())
    return max(0.0, min(1.0, mass / total))


def key_repeat_ratio(df: pd.DataFrame, metric_col: str, key_col: Optional[str]) -> Optional[float]:
    """Fracción de filas cuyo valor es constante dentro de un grupo de `key_col`.

    Complemento del anterior: confirma que la repetición ocurre dentro de una
    entidad (cabecera↔detalle). Devuelve None si no hay clave utilizable.
    """
    if not key_col or metric_col not in df.columns or key_col not in df.columns or key_col == metric_col:
        return None
    subset = df[[key_col, metric_col]].dropna()
    if subset.empty or subset[key_col].nunique() < 2 or len(subset) <= subset[key_col].nunique():
        return None
    grouped = subset.groupby(key_col)[metric_col]
    sizes = grouped.transform("size")
    uniques = grouped.transform("nunique")
    repeated = (sizes > 1) & (uniques == 1)
    if not repeated.any():
        return None
    return float(repeated.mean())


def _compute_col_features(series: pd.Series) -> dict[str, Any] | None:
    clean = series.dropna()
    if clean.empty:
        return None
    values = clean.to_numpy(dtype=np.float64, na_value=np.nan)
    values = _safe_finite(values)
    if len(values) < 2:
        return None
    n = len(values)
    mean = float(np.mean(values))
    std = float(np.std(values, ddof=0)) if n > 1 else 0.0
    min_val = float(np.min(values))
    max_val = float(np.max(values))

    is_ratio = False
    if max_val <= 1.0 and min_val >= -1e-9:
        is_ratio = True
    elif max_val <= 100.0 and min_val >= -1e-9 and std < max_val * 0.5 and max_val > 0:
        values_unique = len(np.unique(values))
        range_ratio = (max_val - min_val) / max_val if max_val > 0 else 0.0
        min_ratio = min_val / max_val if max_val > 0 else 1.0
        if values_unique >= 3 and range_ratio > 0.5 and min_ratio < 0.2:
            is_ratio = True

    is_non_negative = min_val >= -1e-9

    abs_max = max(abs(min_val), abs(max_val), 1.0)
    log_scale = int(math.floor(math.log10(abs_max))) if abs_max > 0 else 0

    cv = std / max(abs(mean), 1e-9) if abs(mean) > 1e-9 else 0.0
    cv_tier: str
    if cv < 0.5:
        cv_tier = "LOW"
    elif cv < 2.0:
        cv_tier = "MEDIUM"
    else:
        cv_tier = "HIGH"

    zero_count = int(np.sum(values == 0))
    zero_ratio = zero_count / n if n > 0 else 0.0

    return {
        "is_ratio": is_ratio,
        "is_non_negative": is_non_negative,
        "log_scale": log_scale,
        "cv_tier": cv_tier,
        "zero_ratio": round(zero_ratio, 4),
        "mean": round(mean, 4),
        "std": round(std, 4),
        "min": round(min_val, 4),
        "max": round(max_val, 4),
    }


# ═══════════════════════════════════════════════════════════════════
# [#4 2026-09] Detección de métricas ACUMULADAS (running totals).
#
# Una columna que ya viene acumulada (running total) NO debe sumarse por
# período: eso re-cuenta y infla el resultado. La señal es bifronte y
# fail-closed (ambas deben coincidir para actuar):
#   1. Estructural: la serie ordenada por el eje temporal es monótona
#      (no decreciente o no creciente) en ≥98% de sus diferencias no nulas.
#   2. Léxica: el nombre de la columna o el prompt contiene vocabulario
#      GENÉRICO de acumulación (ES/EN/PT). No es vocabulario de dominio, es
#      vocabulario de agregación — mismo precedente que
#      `IbisEngine._NON_ADDITIVE_METRIC_TOKENS`.
# Una sola señal NO basta (evita falsos positivos en columnas que suben por
# casualidad). La señal estructural sola emite telemetría, sin cambiar nada.
# ═══════════════════════════════════════════════════════════════════

_ACCUMULATION_TOKENS = (
    "acumul", "cumul", "accumul", "running", "ytd",
    "year to date", "year_to_date", "corrido",
)

# Proporción mínima de diferencias con el mismo signo para aceptar monotonía.
_MONOTONIC_RATIO_THRESHOLD = 0.98

# Mínimo de puntos para evaluar monótonicidad de forma fiable.
_MONOTONIC_MIN_POINTS = 3


def has_accumulation_lexicon(*texts: Optional[str]) -> bool:
    """True si alguno de los textos contiene vocabulario genérico de acumulación.

    Domain-agnostic: matchea prefijos/tokens de agregación (acumulad*, cumul*,
    running, ytd), nunca nombres de negocio.
    """
    joined = " ".join(str(t or "").lower() for t in texts)
    if not joined:
        return False
    return any(token in joined for token in _ACCUMULATION_TOKENS)


def monotonic_direction(values: pd.Series) -> Optional[str]:
    """Dirección monótona de una serie YA ordenada temporalmente.

    Devuelve 'inc' (no decreciente), 'dec' (no creciente) o None si la serie
    no es monótona, es constante (ambigua) o tiene menos de 3 puntos finitos.

    [Fase 2 2026-09] Delegado a la autoridad única
    `app.core.temporal_axis.series_monotonic_direction` (elimina duplicación).
    Firma y contrato de retorno preservados.
    """
    return series_monotonic_direction(values)


def cumulative_aggregation(direction: Optional[str]) -> Optional[str]:
    """Agregación correcta para una serie acumulada: último valor del período.

    'inc' → max (el último valor es el máximo); 'dec' → min.
    """
    if direction == "inc":
        return "max"
    if direction == "dec":
        return "min"
    return None


def is_cumulative_metric(
    features: Optional[dict[str, Any]],
    column_name: Optional[str],
    prompt: Optional[str] = None,
) -> bool:
    """Dos señales fail-closed: monotonía temporal AND léxico de acumulación."""
    if not features:
        return False
    direction = features.get("cumulative_monotonic_direction")
    if direction not in ("inc", "dec"):
        return False
    return has_accumulation_lexicon(column_name, prompt)


def compute_metric_features(
    df: pd.DataFrame,
    metric_cols: list[str],
    identifier_cols: Optional[list[str]] = None,
    time_col: Optional[str] = None,
) -> dict[str, dict[str, Any]]:
    features: dict[str, dict[str, Any]] = {}
    identifier_cols = [c for c in (identifier_cols or []) if c in df.columns]

    # [#4 2026-09] Orden temporal estable para evaluar monotonía. Si no hay eje
    # temporal usable, la señal estructural queda en None (fail-closed).
    ordered_index = None
    if time_col and time_col in df.columns:
        try:
            ordered_index = df[time_col].sort_values(kind="stable").index
        except Exception:
            ordered_index = None

    for col in metric_cols:
        if col not in df.columns:
            continue
        feat = _compute_col_features(df[col])
        if feat is None:
            continue
        # Señales estructurales de no aditividad (domain-agnostic, sin nombres).
        feat["repeated_sum_ratio"] = repeated_sum_ratio(df[col])
        if identifier_cols:
            ratios = [
                key_repeat_ratio(df, col, key_col)
                for key_col in identifier_cols[:5]
            ]
            usable = [r for r in ratios if r is not None]
            feat["key_repeat_ratio"] = max(usable) if usable else None
        # [#4] Señal estructural de acumulado (monotonía en el eje temporal).
        series = df[col].loc[ordered_index] if ordered_index is not None else df[col]
        feat["cumulative_monotonic_direction"] = monotonic_direction(series)
        features[col] = feat
    return features


def count_metric_features(
    features: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Vista compacta de las señales de no aditividad/acumulado por métrica."""
    return {
        col: {
            "repeated_sum_ratio": feat.get("repeated_sum_ratio"),
            "key_repeat_ratio": feat.get("key_repeat_ratio"),
            "cumulative_monotonic_direction": feat.get("cumulative_monotonic_direction"),
        }
        for col, feat in features.items()
    }


def is_non_additive(features: Optional[dict[str, Any]]) -> bool:
    """True si la métrica se sospecha a nivel documento/encabezado.

    Umbral conservador: la mayoría del total proviene de valores repetidos, o el
    valor es constante dentro de grupos de una entidad con múltiples filas.
    """
    if not features:
        return False
    repeated = features.get("repeated_sum_ratio")
    within_key = features.get("key_repeat_ratio")
    if repeated is not None and repeated >= 0.5:
        return True
    if within_key is not None and within_key >= 0.5 and (repeated is None or repeated >= 0.25):
        return True
    return False


def _family_key(features: dict[str, Any]) -> str:
    r = "1" if features["is_ratio"] else "0"
    n = "1" if features["is_non_negative"] else "0"
    lvl = features["log_scale"]
    cv = features["cv_tier"]
    return f"R{r}N{n}L{lvl}C{cv}"


def classify_metric_families(
    features: dict[str, dict[str, Any]]
) -> dict[str, list[str]]:
    raw: dict[str, list[str]] = {}
    for col, feat in features.items():
        key = _family_key(feat)
        raw.setdefault(key, []).append(col)

    rn_map: dict[str, list[tuple[int, str, list[str]]]] = {}
    for key, cols in raw.items():
        parts = key.split("L", 1)
        rn_prefix = parts[0]
        rest = parts[1] if len(parts) > 1 else "0CLOW"
        l_str, _, c_str = rest.partition("C")
        lval = int(l_str) if l_str.lstrip("-").isdigit() else 0
        tier = c_str if c_str else "LOW"
        rn_map.setdefault(rn_prefix, []).append((lval, tier, cols))

    result: dict[str, list[str]] = {}
    for rn_prefix, entries in rn_map.items():
        sorted_entries = sorted(entries, key=lambda x: x[0])
        merged_groups: list[list[tuple[int, str, list[str]]]] = []
        current = [sorted_entries[0]] if sorted_entries else []
        for entry in sorted_entries[1:]:
            if entry[0] - current[-1][0] <= 1:
                current.append(entry)
            else:
                merged_groups.append(current)
                current = [entry]
        if current:
            merged_groups.append(current)

        for group in merged_groups:
            lv = group[0][0]
            tiers = [t for _, t, _ in group]
            has_low = any(t == "LOW" for t in tiers)
            has_medium = any(t == "MEDIUM" for t in tiers)
            has_high = any(t == "HIGH" for t in tiers)
            prefix = f"{rn_prefix}L{lv}"
            all_cols: list[str] = []
            for _, _, cc in group:
                all_cols.extend(cc)
            if has_high:
                result[prefix + "CHIGH"] = all_cols
            elif has_medium:
                result[prefix + "CMEDIUM"] = all_cols
            else:
                result[prefix + "CLOW"] = all_cols

    return result


_SCORE_EXACT_MATCH = 100
_SCORE_TOKEN_MATCH = 10


def _score_prompt_relevance(
    column_name: str, surface_prompt: str
) -> int:
    col_norm = normalize_semantic_text(str(column_name).replace("_", " "))
    compact_col = col_norm.replace(" ", "")
    compact_prompt = surface_prompt.replace(" ", "")
    score = 0

    if compact_col and compact_col in compact_prompt:
        score += _SCORE_EXACT_MATCH + len(compact_col)

    for token in col_norm.split():
        if len(token) > 1 and token in surface_prompt:
            score += _SCORE_TOKEN_MATCH

    return score


def _lexical_diversity(col: str, selected: list[str]) -> float:
    """Distancia léxica Jaccard entre una columna y las ya seleccionadas.

    1.0 = completamente diferente, 0.0 = idéntica.
    Dominio-agnóstico: compara tokens normalizados, no nombres hardcodeados.
    """
    if not selected:
        return 1.0
    col_tokens = set(normalize_semantic_text(str(col).replace("_", " ")).split())
    if not col_tokens:
        return 1.0
    min_diversity = 1.0
    for sel in selected:
        sel_tokens = set(normalize_semantic_text(str(sel).replace("_", " ")).split())
        union = col_tokens | sel_tokens
        if not union:
            continue
        jaccard_sim = len(col_tokens & sel_tokens) / len(union)
        min_diversity = min(min_diversity, 1.0 - jaccard_sim)
    return min_diversity


# Umbral de diversidad léxica para el filtro greedy single-family.
# 0.3 bloquea near-duplicados (ej: gastos_en_dolares vs gastos_en_dolares_mensual
# comparten ~75% tokens → diversity=0.25) pero permite métricas genuinamente
# distintas (ej: ventas vs costos → diversity ≥ 0.5).
_DIVERSITY_THRESHOLD = 0.3


def select_metrics_for_broad_analysis(
    metric_cols: list[str],
    features: dict[str, dict[str, Any]],
    families: dict[str, list[str]],
    surface_prompt: str,
    max_metrics: int = 3,
) -> list[str]:
    if not families:
        return []
    if len(families) == 1:
        cols = list(families.values())[0]
        scored = [(col, _score_prompt_relevance(col, surface_prompt)) for col in cols]
        scored.sort(key=lambda x: (-x[1], x[0]))

        # Greedy diversity filter: evita seleccionar near-duplicados léxicos
        # (ej: gastos_en_dolares + gastos_en_dolares_mensual comparten 75% tokens).
        # La primera métrica siempre entra (mayor relevancia). Las siguientes
        # deben superar el umbral de diversidad léxica para no ser redundantes.
        selected: list[str] = []
        for col, _relevance in scored:
            if len(selected) >= max_metrics:
                break
            if not selected:
                selected.append(col)
                continue
            if _lexical_diversity(col, selected) > _DIVERSITY_THRESHOLD:
                selected.append(col)

        # Fallback: si el filtro de diversidad subseleccionó (dataset genuinamente
        # redundante), completar con las métricas de mayor relevancia restantes
        # para no devolver menos de lo que el análisis necesita.
        if len(selected) < max_metrics:
            for col, _relevance in scored:
                if len(selected) >= max_metrics:
                    break
                if col not in selected:
                    selected.append(col)

        return selected

    selected: list[str] = []
    family_list = list(families.items())
    family_list.sort(key=lambda x: -len(x[1]))

    for fam_key, cols in family_list:
        if len(selected) >= max_metrics:
            break
        scored = [(col, _score_prompt_relevance(col, surface_prompt)) for col in cols]
        scored.sort(key=lambda x: (-x[1], x[0]))
        if scored:
            selected.append(scored[0][0])

    return selected[:max_metrics]


def build_metric_coverage_metadata(
    selected_metrics: list[str],
    all_metric_cols: list[str],
    families: dict[str, list[str]],
    non_additive_metrics: Optional[dict[str, dict[str, Any]]] = None,
    cumulative_metrics: Optional[dict[str, dict[str, Any]]] = None,
) -> dict[str, Any]:
    if not all_metric_cols:
        return {
            "families_found": 0,
            "total_metric_columns": 0,
            "families_covered": 0,
            "metrics_selected": [],
            "uncovered_columns": [],
            "message": "El dataset no contiene columnas numéricas analizables como métricas.",
        }

    selected_set = set(selected_metrics)
    uncovered = [c for c in all_metric_cols if c not in selected_set]

    covered_families = 0
    for cols in families.values():
        if any(c in selected_set for c in cols):
            covered_families += 1

    metadata: dict[str, Any] = {
        "families_found": len(families),
        "total_metric_columns": len(all_metric_cols),
        "families_covered": covered_families,
        "metrics_selected": list(selected_metrics),
        "uncovered_columns": uncovered,
        "message": (
            f"Dataset con {len(families)} familia(s) métrica(s) y "
            f"{len(all_metric_cols)} columna(s) métrica(s) en total. "
            f"Bundle cubre {covered_families} familia(s) "
            f"con {len(selected_metrics)} métrica(s)."
        ),
    }
    if non_additive_metrics:
        # Señal estructural (no por nombre): métricas cuyo total proviene
        # mayormente de valores repetidos (cabecera/documento). Sumarlas
        # re-contaría; se exponen para que la narrativa lo advierta.
        metadata["non_additive_metrics"] = {
            col: dict(signals) for col, signals in non_additive_metrics.items()
        }
        metadata["additivity_note"] = (
            "Se detectaron métricas no aditivas (valor repetido a nivel documento). "
            "Se prefirió una métrica aditiva para el KPI cuando estaba disponible."
        )
    if cumulative_metrics:
        # [#4] Métricas acumuladas (running totals): sumarlas por período
        # re-contaría; se reporta el último valor del período (max/min).
        metadata["cumulative_metrics"] = {
            col: dict(signals) for col, signals in cumulative_metrics.items()
        }
        metadata["cumulative_note"] = (
            "Se detectaron métricas acumuladas (serie monótona + léxico de acumulación). "
            "No se suman entre períodos: se reporta el último valor."
        )
    return metadata
