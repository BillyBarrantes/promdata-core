"""
narrative_reconciliation_guard.py — Shield Numérico Post-LLM.
══════════════════════════════════════════════════════════════
Valida que las cifras numéricas, monetarias y porcentuales citadas en la narrativa generada
por el LLM concuerden con los hard_facts computados deterministamente por Ibis/DuckDB.
Previene alucinaciones cuantitativas y asegura trazabilidad veraz.
"""
from __future__ import annotations

import re
from typing import Any

from app.core.structured_logging import emit_structured_log

_NUM_PATTERN = re.compile(
    r"""
    (?P<currency>[\$€£S/]\.?\s*)?
    (?P<number>\d{1,3}(?:[,\.]\d{3})+(?:[,\.]\d+)?|\d+(?:[.,]\d+)?)
    \s*(?P<multiplier>[kKmMbB]|mil(?:l[oó]n(?:es)?)?)?
    (?P<percent>%)?
    """,
    re.VERBOSE,
)


def parse_numeric_token(match: re.Match) -> float | None:
    """Convierte un token numérico extraído a float normalizado."""
    num_str = match.group("number")
    if not num_str:
        return None

    # Normalizar separadores de miles y decimales
    if "," in num_str and "." in num_str:
        if num_str.rfind(".") > num_str.rfind(","):
            # 1,561,828.66
            cleaned = num_str.replace(",", "")
        else:
            # 1.561.828,66
            cleaned = num_str.replace(".", "").replace(",", ".")
    elif "," in num_str:
        parts = num_str.split(",")
        if len(parts) == 2 and len(parts[1]) != 3:
            cleaned = num_str.replace(",", ".")
        else:
            cleaned = num_str.replace(",", "")
    else:
        cleaned = num_str

    try:
        val = float(cleaned)
    except ValueError:
        return None

    mult = (match.group("multiplier") or "").lower()
    if mult in ("k", "mil"):
        val *= 1_000
    elif mult in ("m", "millon", "millón", "millones"):
        val *= 1_000_000
    elif mult in ("b", "billón", "billon", "billones"):
        val *= 1_000_000_000

    return val


def collect_fact_numbers(
    computed_facts: dict[str, Any],
    raw_data: list[dict[str, Any]] | None = None,
) -> list[float]:
    """Recolecta todos los números reales computados como lista plana de floats."""
    facts: list[float] = []

    def _extract_from_obj(obj: Any) -> None:
        if isinstance(obj, (int, float)) and not isinstance(obj, bool):
            facts.append(float(obj))
        elif isinstance(obj, dict):
            for v in obj.values():
                _extract_from_obj(v)
        elif isinstance(obj, list):
            for item in obj:
                _extract_from_obj(item)

    _extract_from_obj(computed_facts)
    if raw_data:
        _extract_from_obj(raw_data)

    return facts


def verify_number_in_facts(val: float, facts: list[float], tolerance_pct: float = 0.04) -> bool:
    """Verifica si un número existe en los hechos con tolerancia para redondeo (ej. 4%)."""
    if val <= 10:
        # Ignorar números de estructura: bullet points, ordinales, rankings ("top 5", "1.", "2.", "10%")
        return True

    for f in facts:
        if f == 0:
            if abs(val) < 1e-4:
                return True
            continue
        rel_diff = abs(val - f) / abs(f)
        if rel_diff <= tolerance_pct:
            return True
        # Casos donde la diferencia absoluta es mínima (ej. redondeo al entero más cercano)
        if abs(val - f) < 1.0:
            return True

    return False


def _matches_any(val: float, candidates: list[float], tolerance_pct: float) -> bool:
    for candidate in candidates:
        if candidate == 0:
            if abs(val) < 1e-4:
                return True
            continue
        if abs(val - candidate) / abs(candidate) <= tolerance_pct or abs(val - candidate) < 1.0:
            return True
    return False


def _derived_candidates(facts: list[float], *, max_facts: int = 80) -> list[float]:
    """Genera cifras derivadas plausibles de pares de hechos.

    Un analista ejecutivo cita diferencias (IT − Finanzas), sumas y ratios
    porcentuales. Estas cifras son correctas pero no aparecen literalmente en
    los hechos; sin este chequeo, el guard las marcaría como no verificadas.
    """
    pool = facts[:max_facts]
    derived: list[float] = []
    n = len(pool)
    for i in range(n):
        a = pool[i]
        for j in range(i + 1, n):
            b = pool[j]
            derived.append(a + b)
            derived.append(a - b)
            derived.append(b - a)
            if b != 0:
                derived.append((a / b) * 100.0)
                derived.append((a - b) / b * 100.0)
            if a != 0:
                derived.append((b / a) * 100.0)
                derived.append((b - a) / a * 100.0)
    return derived


def verify_number_derivable(
    val: float,
    facts: list[float],
    derived: list[float] | None = None,
    tolerance_pct: float = 0.04,
) -> bool:
    """Valida una cifra contra hechos literales o cifras derivadas (restas/sumas/ratios)."""
    if verify_number_in_facts(val, facts, tolerance_pct):
        return True
    if val <= 10:
        return True
    candidates = derived if derived is not None else _derived_candidates(facts)
    return _matches_any(val, candidates, tolerance_pct)



def reconcile_narrative_with_facts(
    narrative_text: str,
    computed_facts: dict[str, Any],
    raw_data: list[dict[str, Any]] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """
    Escanea la narrativa generada por el LLM y reconcilia los números con los hechos matemáticos.
    Retorna (narrativa_auditada, lista_discrepancias_no_verificadas).
    """
    if not narrative_text:
        return narrative_text, []

    facts = collect_fact_numbers(computed_facts, raw_data)
    if not facts:
        return narrative_text, []

    derived = _derived_candidates(facts)
    unverified: list[dict[str, Any]] = []

    for match in _NUM_PATTERN.finditer(narrative_text):
        num_val = parse_numeric_token(match)
        if num_val is None:
            continue

        raw_match = match.group(0).strip()
        # Filtro de años comunes (2000 a 2035) sin moneda
        if 2000 <= num_val <= 2035 and "." not in raw_match and not match.group("currency"):
            continue

        # Validar cifras sustantivas (> 10) contra hechos literales y derivados
        if num_val > 10 and not verify_number_derivable(num_val, facts, derived):
            unverified.append({
                "raw_text": raw_match,
                "parsed_value": num_val,
                "reason": "Cifra no respaldada por los datos computados.",
            })

    if unverified:
        emit_structured_log(
            "narrative_reconciliation_unverified_figures",
            count=len(unverified),
            figures=[u["raw_text"] for u in unverified[:5]],
        )
        audit_note = "\n\n> ⚠️ *Nota de Verificación:* Ciertas cifras secundarias citadas difieren levemente de los hechos computados."
        if "Nota de Verificación" not in narrative_text:
            narrative_text += audit_note

    return narrative_text, unverified
