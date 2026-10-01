"""
F1.5 — Cierre definitivo de F1: hardening de `except` desnudos.

Blinda dos invariantes:

1. **Estático (AGENTS §7 / Orquestador Etapa 1):** cero `except:` desnudos
   en `backend/app`. Se valida con `ast`, no con regex, para que comentarios,
   docstrings y strings no generen falsos positivos ni oculten violaciones.
2. **Comportamiento preservado:** los *fallbacks* explícitos
   (`value = 0`, `continue`, `"DF_Error"`) sobreviven al endurecimiento.
   El único cambio permitido F1.5 es restringir el tipo de excepción
   capturado (los `except:` desnudos capturaban `KeyboardInterrupt` y
   `SystemExit`, que en un worker Celery provocan un apagado sucio).
"""

from __future__ import annotations

import ast
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent
APP_DIR = _BACKEND_DIR / "app"


def _iter_python_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _bare_except_lines(path: Path) -> list[int]:
    """Líneas de `ExceptHandler` sin tipo (`except:` / `except :`)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return sorted(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.ExceptHandler) and node.type is None
    )


# ═══════════════════════════════════════════════════════════════════════
# 1. Estático: ningún `except:` desnudo en la capa de servicio
# ═══════════════════════════════════════════════════════════════════════
def test_no_bare_except_in_app() -> None:
    files = _iter_python_files(APP_DIR)
    # Guard: el escaneo debe cubrir el paquete completo (evita falso verde
    # si un refactor mueve `app/` y el path queda vacío).
    assert len(files) >= 100, f"Escaneo incompleto: solo {len(files)} archivos"

    offenders: dict[str, list[int]] = {}
    for path in files:
        lines = _bare_except_lines(path)
        if lines:
            offenders[str(path.relative_to(_BACKEND_DIR))] = lines

    assert offenders == {}, (
        f"except: desnudos detectados (capturarían KeyboardInterrupt/"
        f"SystemExit): {offenders}"
    )


# ═══════════════════════════════════════════════════════════════════════
# 2. Fallback numérico intacto (chart_factory)
# ═══════════════════════════════════════════════════════════════════════
def test_gauge_numeric_fallback_preserved() -> None:
    from app.services.chart_factory import ChartFactory

    bad = ChartFactory.build_gauge_chart("KPI", "no-numerico")
    assert float(bad["series"][0]["data"][0]["value"]) == 0.0

    good = ChartFactory.build_gauge_chart("KPI", "42")
    assert float(good["series"][0]["data"][0]["value"]) == 42.0


# ═══════════════════════════════════════════════════════════════════════
# 3. Fallback de normalización polimórfica intacto
# ═══════════════════════════════════════════════════════════════════════
def test_polymorphic_normalizer_skips_unconvertible() -> None:
    from app.services.chart_factory import ChartFactory

    normalized = ChartFactory._normalize_data_polymorphic(
        [10, {"value": "3.5", "name": "A"}, "basura", {"value": None, "name": "B"}]
    )
    values = [entry.get("value") for entry in normalized if isinstance(entry, dict)]
    # "basura" → skip (except Exception: continue); None → 0 (except Exception: val = 0)
    assert 10.0 in [entry.get("value") for entry in normalized]
    assert 3.5 in values
    assert 0 in values
