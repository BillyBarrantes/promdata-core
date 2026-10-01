from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class LLMResponse:
    """Respuesta estandarizada que preserva la interfaz de _CompatGenerateResponse de gemini_client."""

    def __init__(self, text: str, parsed: Any = None, raw: Any = None) -> None:
        self.text = text
        self.parsed = parsed
        self._raw = raw

    def __getattr__(self, item: str) -> Any:
        if self._raw is not None:
            return getattr(self._raw, item)
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{item}'")


class LLMProvider(ABC):
    """Protocolo / Clase Base Abstracta para adaptadores de modelos de lenguaje."""

    provider_name: str

    @abstractmethod
    def generate_content(
        self,
        model_name: str,
        contents: Any,
        generation_config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Genera contenido estructurado o texto a partir del prompt."""
        ...

    @abstractmethod
    def embed_content(
        self,
        model: str,
        content: Any,
        task_type: str | None = None,
        output_dimensionality: int | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Genera vector de embedding para memoria o RAG."""
        ...
