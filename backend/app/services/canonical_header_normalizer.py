from __future__ import annotations

import re
import unicodedata
from typing import Any


_SYMBOL_EXPANSIONS = {
    "%": " percent ",
    "#": " number ",
    "&": " and ",
    "@": " at ",
    "+": " plus ",
}


def fold_header_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKD", text)
    without_diacritics = "".join(
        character
        for character in normalized
        if not unicodedata.combining(character)
    )
    for source, replacement in _SYMBOL_EXPANSIONS.items():
        without_diacritics = without_diacritics.replace(source, replacement)
    return without_diacritics


def normalize_canonical_header(value: Any, *, index: int | None = None) -> str:
    folded = fold_header_text(value).lower()
    normalized = re.sub(r"[^a-z0-9]+", "_", folded).strip("_")
    if normalized:
        return normalized
    if index is not None:
        return f"column_{index}"
    return ""


def compact_header_semantic_text(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", fold_header_text(value).lower())
