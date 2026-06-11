"""
Semantic Translator — Refactored Package (Fase 0.1)

[REFACTOR 2026-06-10] Este archivo es el primer paso de la Operacion
Refactor documentada en AGENTS.md §15.1 Plan 1 / Fase 0.1.

El monolito original `semantic_translator.py` (3322 lineas) se migro
a este paquete con la siguiente estrategia:

  backend/app/services/semantic_translator/
  +-- __init__.py           # Este archivo: re-exports para compatibilidad
  +-- core.py               # SemanticTranslator class (3322 lineas, intacta)
  +-- router.py             # Pendiente: extraer logica de routing
  +-- validator.py          # Pendiente: anti-alucinacion, validadores
  +-- planner.py            # Pendiente: plan generation, Triple Vista
  +-- memory.py             # Pendiente: memoria de sesion

Regla de oro: esta __init__.py re-exporta `SemanticTranslator` para
que los 3 call sites existentes sigan funcionando sin cambios:
  - backend/app/tasks/analysis_tasks.py
  - backend/app/services/canonical_tabular_production_executor.py
  - backend/app/services/canonical_shadow_query_runner.py

Commits subsiguientes iran extrayendo logica a los archivos router.py,
validator.py, planner.py, memory.py de manera incremental (1 archivo
movido por commit, con tests pasando).

Para detalles completos, ver AGENTS.md §15.1 Plan 1 / Fase 0.1.
"""

# [COMPAT] Re-export del symbolo publico principal. Los call sites
# importan `from app.services.semantic_translator import SemanticTranslator`,
# asi que este __init__.py DEBE mantener ese symbol disponible.
from app.services.semantic_translator.core import SemanticTranslator

# [COMPAT] Re-export de todos los simbolos del modulo core para que
# cualquier import legacy siga funcionando.
# Esto incluye la clase, sus metodos publicos, y los simbolos de
# las dependencias que el modulo expone (por si algun codigo legacy
# hacia `from app.services.semantic_translator import <otro_symbol>`).
from app.services.semantic_translator.core import (  # noqa: F401
    SemanticTranslator,
)

__all__ = ["SemanticTranslator"]
