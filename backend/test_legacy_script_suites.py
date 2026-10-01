"""
[P0-4] Wrappers pytest para suites legacy estilo-script.

Estas suites fueron escritas como scripts ejecutables (`python test_X.py`)
con código a nivel de módulo y `sys.exit(1)` ante fallos. No son
pytest-collectable directamente (por eso estaban en `--ignore`), pero su
lógica de validación es correcta y NO se modifica: este wrapper las ejecuta
via subprocess y aserta exit code 0, convirtiéndolas en tests pytest reales.

Si una suite falla, el stdout/stderr completo del script se incluye en el
assert para diagnóstico inmediato.
"""
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent

SCRIPT_SUITES = {
    "test_filter_hardening_v2.py": "Filter Engine Hardening V2 (fuzzy/ilike, empty_result, anti-KPI-zombie)",
    "test_multivalue_guard_v4.py": "Multi-Value Filter Guard + Split Dimension Inference",
    "test_orchestrator_v3.py": "Relajación de Paranoia del Orquestador",
}


@pytest.mark.parametrize("script_name", sorted(SCRIPT_SUITES))
def test_legacy_script_suite_exits_zero(script_name: str):
    script_path = BACKEND_DIR / script_name
    assert script_path.exists(), f"{script_name} no existe en {BACKEND_DIR}"

    proc = subprocess.run(
        [sys.executable, script_path.name],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        timeout=300,
    )

    assert proc.returncode == 0, (
        f"{script_name} ({SCRIPT_SUITES[script_name]}) falló con exit code "
        f"{proc.returncode}.\n--- STDOUT (últimas 60 líneas) ---\n"
        + "\n".join(proc.stdout.splitlines()[-60:])
        + f"\n--- STDERR ---\n{proc.stderr[-3000:]}"
    )
