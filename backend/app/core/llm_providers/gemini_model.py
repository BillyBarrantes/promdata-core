from __future__ import annotations

from typing import Any

from app.core.llm_providers.base import LLMProvider, LLMResponse


class GeminiModelAdapter:
    """Adaptador que mantiene la interfaz exacta esperada de genai.GenerativeModel()."""

    def __init__(
        self,
        provider: LLMProvider,
        model_name: str,
        generation_config: Any = None,
    ) -> None:
        self._provider = provider
        self.model_name = str(model_name)
        self._generation_config = generation_config or {}

    def generate_content(
        self,
        contents: Any,
        generation_config: Any = None,
        **kwargs: Any,
    ) -> LLMResponse:
        config: dict[str, Any] = {}
        if isinstance(self._generation_config, dict):
            config.update(self._generation_config)
        elif hasattr(self._generation_config, "model_dump"):
            try:
                config.update(self._generation_config.model_dump(exclude_none=True))
            except Exception:
                pass

        if isinstance(generation_config, dict):
            config.update(generation_config)
        elif hasattr(generation_config, "model_dump"):
            try:
                config.update(generation_config.model_dump(exclude_none=True))
            except Exception:
                pass

        if kwargs:
            config.update(kwargs)

        return self._provider.generate_content(
            model_name=self.model_name,
            contents=contents,
            generation_config=config,
        )
