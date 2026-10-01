from __future__ import annotations

import logging
from typing import Any

from app.core.circuit_breaker import GeminiCircuitBreaker
from app.core.config import settings
from app.core.llm_providers.base import LLMProvider, LLMResponse
from app.core.structured_logging import emit_structured_log

_HAS_OPENAI_SDK = False
_OPENAI_CLIENT_CLASS = None

try:
    from openai import OpenAI as _openai_class  # type: ignore[import]
    _OPENAI_CLIENT_CLASS = _openai_class
    _HAS_OPENAI_SDK = True
except Exception as _import_err:
    logging.warning("openai SDK import failed: %r", _import_err)
    _HAS_OPENAI_SDK = False


class OpenAICompatProvider(LLMProvider):
    """Adaptador para proveedores compatibles con el protocolo OpenAI (DeepSeek, OpenAI, etc.)."""

    provider_name = "openai_compat"

    def __init__(self, api_key: str, base_url: str | None = None, model: str = "deepseek-chat") -> None:
        if not _HAS_OPENAI_SDK or _OPENAI_CLIENT_CLASS is None:
            raise RuntimeError(
                "El SDK 'openai' no está instalado. Instálalo con: pip install 'openai>=1.0.0,<2.0'"
            )
        self._api_key = str(api_key or "").strip()
        self._base_url = str(base_url or "").strip()
        self._default_model = str(model or "deepseek-chat").strip()

        client_kwargs: dict[str, Any] = {"api_key": self._api_key}
        if self._base_url:
            client_kwargs["base_url"] = self._base_url

        self._client = _OPENAI_CLIENT_CLASS(**client_kwargs)

        # Circuit breaker con mismos parámetros probados en producción
        self._circuit_breaker = GeminiCircuitBreaker(
            enabled=bool(settings.GEMINI_CIRCUIT_BREAKER_ENABLED),
            failure_threshold=int(settings.GEMINI_CIRCUIT_FAILURE_THRESHOLD),
            recovery_timeout_seconds=int(settings.GEMINI_CIRCUIT_RECOVERY_TIMEOUT_SECONDS),
            half_open_max_calls=int(settings.GEMINI_CIRCUIT_HALF_OPEN_MAX_CALLS),
            max_retries=int(settings.GEMINI_RETRY_MAX_RETRIES),
            base_delay=float(settings.GEMINI_RETRY_BASE_DELAY_SECONDS),
            max_delay=float(settings.GEMINI_RETRY_MAX_DELAY_SECONDS),
            jitter=float(settings.GEMINI_RETRY_JITTER_SECONDS),
        )

    def _resolve_model(self, model_name: str | None) -> str:
        """Ajuste A: Sobrescribe nombres de modelos Gemini con el modelo por defecto del proveedor."""
        candidate = str(model_name or "").strip()
        if not candidate or "gemini" in candidate.lower():
            return self._default_model
        return candidate

    def _ensure_json_word(self, contents: str, config: dict[str, Any]) -> str:
        """Ajuste B: Inyecta la palabra 'JSON' si response_format lo requiere para evitar 400 Bad Request."""
        if config.get("response_mime_type") == "application/json":
            if "json" not in contents.lower():
                return contents + "\n\nResponde exclusivamente en formato JSON válido."
        return contents

    def generate_content(
        self,
        model_name: str,
        contents: Any,
        generation_config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        config = dict(generation_config or {})
        if kwargs:
            config.update(kwargs)

        model = self._resolve_model(model_name)

        # Safeguard: si contents es lista de strings o partes, concatenar
        if isinstance(contents, (list, tuple)):
            clean_contents = "\n\n".join(str(c) for c in contents if c is not None)
        else:
            clean_contents = str(contents or "")

        clean_contents = self._ensure_json_word(clean_contents, config)

        system_instruction = config.pop("system_instruction", None)

        response_format = None
        if config.get("response_mime_type") == "application/json":
            response_format = {"type": "json_object"}

        max_tokens = min(int(config.get("max_output_tokens", 8192) or 8192), 8192)
        temperature = float(config.get("temperature", 0.1) or 0.1)

        messages: list[dict[str, str]] = []
        if system_instruction:
            messages.append({"role": "system", "content": str(system_instruction)})
        messages.append({"role": "user", "content": clean_contents})

        request_kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format is not None:
            request_kwargs["response_format"] = response_format

        # Invocación envuelta en Circuit Breaker con retry exponencial automático
        response = self._circuit_breaker.call(
            self._client.chat.completions.create,
            **request_kwargs,
        )

        text = ""
        try:
            text = response.choices[0].message.content or ""
        except (AttributeError, IndexError):
            pass

        usage = getattr(response, "usage", None)
        if usage:
            prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
            completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
            total_tokens = int(getattr(usage, "total_tokens", prompt_tokens + completion_tokens) or 0)

            # DeepSeek Prompt Cache Hit (si está presente en prompt_tokens_details)
            prompt_tokens_details = getattr(usage, "prompt_tokens_details", None)
            cache_hit_tokens = (
                int(getattr(prompt_tokens_details, "cached_tokens", 0) or 0)
                if prompt_tokens_details
                else 0
            )
            cache_miss_tokens = max(0, prompt_tokens - cache_hit_tokens)

            # Tarifas oficiales DeepSeek V3/Flash: $0.27/M miss, $0.07/M hit, $1.10/M completion
            is_deepseek = "deepseek" in self._default_model.lower() or "deepseek" in str(self._base_url).lower()
            if is_deepseek:
                estimated_cost_usd = (
                    (cache_miss_tokens * 0.00000027)
                    + (cache_hit_tokens * 0.00000007)
                    + (completion_tokens * 0.00000110)
                )
            else:
                estimated_cost_usd = (prompt_tokens * 0.0000025) + (completion_tokens * 0.000010)

            emit_structured_log(
                "llm_token_usage",
                provider="deepseek" if is_deepseek else "openai_compat",
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cache_hit_tokens=cache_hit_tokens,
                total_tokens=total_tokens,
                estimated_cost_usd=round(estimated_cost_usd, 6),
            )

        return LLMResponse(text=text, raw=response)

    def embed_content(
        self,
        model: str,
        content: Any,
        task_type: str | None = None,
        output_dimensionality: int | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """DeepSeek y endpoints de chat no soportan embeddings vectoriales directamente."""
        raise NotImplementedError(
            f"{self.provider_name} no soporta embeddings vectoriales directamente. "
            "El Gateway debe delegar las operaciones de embedding a GeminiProvider."
        )
