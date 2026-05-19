from __future__ import annotations

from app.services.canonical_header_normalizer import (
    compact_header_semantic_text,
    fold_header_text,
    normalize_canonical_header,
)


def test_phase8_header_normalizer_folds_unicode_and_symbols() -> None:
    assert fold_header_text("Área / Variación %") == "Area / Variacion  percent "


def test_phase8_header_normalizer_builds_ascii_safe_slug() -> None:
    assert normalize_canonical_header("Área / Variación %", index=1) == "area_variacion_percent"


def test_phase8_header_normalizer_builds_compact_semantic_text() -> None:
    assert compact_header_semantic_text("Costo Total") == "costototal"
