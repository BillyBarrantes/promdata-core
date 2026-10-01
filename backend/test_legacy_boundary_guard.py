"""Guarda de frontera del pipeline productivo (Fase 2, no destructiva).

El clúster legacy (`chart_generator`, `plan_generator`, `plan_executor`,
`narrative_generator`, `legacy_codegen`, `visual_contract_validator`) sigue
existiendo por la Ley de No-Eliminación, pero NO debe ser importado por la ruta
productiva (`universal_tabular`). Este test congela esa frontera con análisis
estático (AST), de modo que un futuro cambio no vuelva a enganchar la producción
a código muerto (que fue exactamente la causa del desacople del combo: la lógica
`growth→dual` vivía solo en `chart_generator.py`, legacy).

No borra ni modifica nada: solo audita imports.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_APP = Path(__file__).resolve().parent / "app"

# Módulos legacy que la producción NO debe importar.
_LEGACY_MODULES = {
    "app.tasks.analysis_pipeline.chart_generator",
    "app.tasks.analysis_pipeline.plan_generator",
    "app.tasks.analysis_pipeline.plan_executor",
    "app.tasks.analysis_pipeline.narrative_generator",
    "app.tasks.analysis_pipeline.legacy_codegen",
    "app.services.visual_contract_validator",
}

# Ruta productiva viva (universal_tabular) y su capa semántica.
_PRODUCTION_MODULES = [
    "services/canonical_tabular_production_executor.py",
    "services/canonical_tabular_canary_executor.py",
    "services/ibis_engine.py",
    "services/semantic_translator/validator.py",
    "services/semantic_translator/planner.py",
    "services/semantic_translator/unified_translator.py",
    "core/temporal_axis.py",
]


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module)
                for alias in node.names:
                    names.add(f"{node.module}.{alias.name}")
    return names


@pytest.mark.parametrize("relative_path", _PRODUCTION_MODULES)
def test_production_module_does_not_import_legacy_cluster(relative_path: str) -> None:
    path = _APP / relative_path
    assert path.exists(), f"módulo productivo esperado no encontrado: {relative_path}"
    imports = _imported_modules(path)
    violations = sorted(
        legacy
        for legacy in _LEGACY_MODULES
        if any(name == legacy or name.startswith(f"{legacy}.") for name in imports)
    )
    assert not violations, (
        f"{relative_path} importa el clúster legacy {violations}; la ruta productiva "
        "debe ser autónoma. Si se necesita una capacidad del legacy, se porta al "
        "pipeline productivo, no se reengancha el módulo muerto."
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
