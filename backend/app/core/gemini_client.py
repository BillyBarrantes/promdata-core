from __future__ import annotations

import logging
from typing import Any

from app.core.config import settings
from app.core.llm_providers.base import LLMProvider, LLMResponse
from app.core.llm_providers.gemini_model import GeminiModelAdapter
from app.core.structured_logging import emit_structured_log


def _build_provider() -> LLMProvider:
    """Instancia el proveedor según la variable de entorno LLM_PROVIDER."""
    provider_key = (getattr(settings, "LLM_PROVIDER", "deepseek") or "deepseek").strip().lower()

    if provider_key == "deepseek":
        from app.core.llm_providers.openai_compat import OpenAICompatProvider
        emit_structured_log(
            "llm_gateway_provider_selected",
            provider="deepseek",
            model=settings.DEEPSEEK_MODEL,
        )
        return OpenAICompatProvider(
            api_key=settings.DEEPSEEK_API_KEY,
            base_url=settings.DEEPSEEK_BASE_URL,
            model=settings.DEEPSEEK_MODEL,
        )

    if provider_key == "openai":
        from app.core.llm_providers.openai_compat import OpenAICompatProvider
        emit_structured_log(
            "llm_gateway_provider_selected",
            provider="openai",
            model=settings.OPENAI_MODEL,
        )
        return OpenAICompatProvider(
            api_key=settings.OPENAI_API_KEY,
            base_url=settings.OPENAI_BASE_URL or "https://api.openai.com/v1",
            model=settings.OPENAI_MODEL,
        )

    # Retained for explicit Gemini-provider selection; default analysis uses DeepSeek.
    from app.core.llm_providers.gemini import GeminiProvider
    emit_structured_log("llm_gateway_provider_selected", provider="gemini")
    return GeminiProvider()


class _GenAIGateway:
    """
    Fachada universal del Gateway LLM.
    Preserva el 100% de la interfaz duck-typed que esperan los 10 módulos consumidores.
    """

    def __init__(self) -> None:
        self._provider = _build_provider()
        # [BLINDAJE PRINCIPIO #3]
        # DeepSeek y chat completions no ofrecen embeddings.
        # Para que document_rag y memory_router nunca fallen, delegamos siempre a Gemini.
        if getattr(self._provider, "provider_name", None) == "gemini":
            self._embeddings_provider = self._provider
        else:
            from app.core.llm_providers.gemini import GeminiProvider
            self._embeddings_provider = GeminiProvider()

    def GenerativeModel(self, model_name: str, generation_config: Any = None, **_: Any) -> GeminiModelAdapter:
        return GeminiModelAdapter(self._provider, model_name, generation_config)

    def embed_content(self, **kwargs: Any) -> dict[str, Any]:
        """Delega SIEMPRE a Gemini para garantizar que RAG y memoria sigan funcionando."""
        payload = dict(kwargs)
        model = payload.pop("model", None)
        content = payload.pop("contents", payload.pop("content", None))
        task_type = payload.pop("task_type", None)
        output_dim = payload.pop("output_dimensionality", None)
        if not model:
            raise ValueError("embed_content requiere 'model'.")
        if content is None:
            raise ValueError("embed_content requiere 'content' o 'contents'.")
        return self._embeddings_provider.embed_content(
            model=model,
            content=content,
            task_type=task_type,
            output_dimensionality=output_dim,
            **payload,
        )

    def configure(self, api_key: str | None = None, **_: Any) -> None:
        candidate = str(api_key or "").strip()
        if candidate and hasattr(self._provider, "configure"):
            self._provider.configure(candidate)
        if candidate and hasattr(self._embeddings_provider, "configure") and self._embeddings_provider is not self._provider:
            self._embeddings_provider.configure(candidate)

    @staticmethod
    def GenerationConfig(**kwargs: Any) -> dict[str, Any]:
        return dict(kwargs)


def _build_runtime() -> _GenAIGateway:
    return _GenAIGateway()


# Instancia singleton exportada como 'genai' (100% compatible hacia atrás)
genai = _build_runtime()
