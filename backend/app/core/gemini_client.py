from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import Any

from app.core.circuit_breaker import GeminiCircuitBreaker
from app.core.config import settings
from app.core.structured_logging import emit_structured_log

_HTTP_TIMEOUT_MS = int(os.getenv("GEMINI_HTTP_TIMEOUT_MS", "60000") or "60000")

_HAS_GENAI_SDK = False
_GENAI_SDK = None
_GENAI_TYPES = None

try:
    from google import genai as _genai_sdk  # type: ignore[import]
    from google.genai import types as _genai_types  # type: ignore[import]

    _GENAI_SDK = _genai_sdk
    _GENAI_TYPES = _genai_types
    _HAS_GENAI_SDK = True
except Exception as _genai_import_error:
    # [GUARD] Logging explícito del fallo de import para diagnóstico futuro.
    # Antes este try/except tragaba el error silenciosamente y solo se
    # manifestaba como RuntimeError genérico en _build_runtime().
    import logging
    logging.warning(
        "google-genai SDK import failed: %r", _genai_import_error
    )
    _HAS_GENAI_SDK = False


def _normalized_generation_config(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return {str(k): v for k, v in value.items()}
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            raw = model_dump(exclude_none=True)
            if isinstance(raw, dict):
                return {str(k): v for k, v in raw.items()}
        except Exception:
            pass
    dict_method = getattr(value, "dict", None)
    if callable(dict_method):
        try:
            raw = dict_method(exclude_none=True)
            if isinstance(raw, dict):
                return {str(k): v for k, v in raw.items()}
        except Exception:
            pass
    return {}


def _merge_generation_config(base: Any, override: Any) -> dict[str, Any]:
    merged = _normalized_generation_config(base)
    merged.update(_normalized_generation_config(override))
    return merged


def _extract_text_from_response(response: Any) -> str:
    text_value = getattr(response, "text", None)
    if isinstance(text_value, str):
        return text_value
    if callable(text_value):
        try:
            candidate = text_value()
            if isinstance(candidate, str):
                return candidate
        except Exception:
            pass
    try:
        candidates = getattr(response, "candidates", None)
        if candidates:
            first_candidate = candidates[0]
            content = getattr(first_candidate, "content", None)
            if content:
                parts = getattr(content, "parts", None) or []
                texts: list[str] = []
                for part in parts:
                    part_text = getattr(part, "text", None)
                    if isinstance(part_text, str) and part_text:
                        texts.append(part_text)
                if texts:
                    return "\n".join(texts).strip()
    except Exception:
        pass
    return ""


def _extract_embedding_values(response: Any) -> list[float] | None:
    if isinstance(response, dict):
        embedding = response.get("embedding")
        if isinstance(embedding, list):
            return [float(v) for v in embedding]
        embeddings = response.get("embeddings")
        if isinstance(embeddings, list) and embeddings:
            first = embeddings[0]
            if isinstance(first, dict):
                values = first.get("values") or first.get("embedding")
                if isinstance(values, list):
                    return [float(v) for v in values]

    embeddings = getattr(response, "embeddings", None)
    if embeddings and isinstance(embeddings, list):
        first = embeddings[0]
        values = getattr(first, "values", None)
        if isinstance(values, list):
            return [float(v) for v in values]

    embedding = getattr(response, "embedding", None)
    if isinstance(embedding, list):
        return [float(v) for v in embedding]

    return None


@dataclass
class _CompatGenerateResponse:
    text: str
    raw: Any

    def __getattr__(self, item: str) -> Any:
        return getattr(self.raw, item)


class _GenAiModelAdapter:
    def __init__(self, runtime: "_GenAiRuntime", *, model_name: str, generation_config: Any = None) -> None:
        self._runtime = runtime
        self.model_name = str(model_name)
        self._generation_config = generation_config

    def generate_content(self, contents: Any, generation_config: Any = None, **kwargs: Any) -> _CompatGenerateResponse:
        config = _merge_generation_config(self._generation_config, generation_config)
        if kwargs:
            config.update({str(k): v for k, v in kwargs.items()})
        request: dict[str, Any] = {
            "model": self.model_name,
            "contents": contents,
        }
        if config:
            request["config"] = config
        response = self._runtime.circuit_breaker.call(
            self._runtime.client.models.generate_content,
            **request,
        )
        return _CompatGenerateResponse(text=_extract_text_from_response(response), raw=response)


class _GenAiRuntime:
    provider = "genai"

    def __init__(self) -> None:
        if _GENAI_SDK is None:
            raise RuntimeError("Google GenAI SDK no está disponible.")
        self._sdk = _GENAI_SDK
        self._types = _GENAI_TYPES
        self._api_key = str(settings.GEMINI_API_KEY or "")
        self._vertex_project = str(settings.GEMINI_VERTEX_PROJECT or "")
        self._vertex_location = str(settings.GEMINI_VERTEX_LOCATION or "global")
        self._client_lock = threading.Lock()
        self._client = None
        self.circuit_breaker = GeminiCircuitBreaker(
            enabled=bool(settings.GEMINI_CIRCUIT_BREAKER_ENABLED),
            failure_threshold=int(settings.GEMINI_CIRCUIT_FAILURE_THRESHOLD),
            recovery_timeout_seconds=int(settings.GEMINI_CIRCUIT_RECOVERY_TIMEOUT_SECONDS),
            half_open_max_calls=int(settings.GEMINI_CIRCUIT_HALF_OPEN_MAX_CALLS),
            max_retries=int(settings.GEMINI_RETRY_MAX_RETRIES),
            base_delay=float(settings.GEMINI_RETRY_BASE_DELAY_SECONDS),
            max_delay=float(settings.GEMINI_RETRY_MAX_DELAY_SECONDS),
            jitter=float(settings.GEMINI_RETRY_JITTER_SECONDS),
        )

    def _build_client(self) -> Any:
        kwargs: dict[str, Any] = {}
        if self._api_key.strip():
            # Modo AI Studio — autenticación por API Key
            kwargs["api_key"] = self._api_key
        else:
            # Modo Vertex AI Enterprise — autenticación por ADC
            kwargs["enterprise"] = True
            if self._vertex_project:
                kwargs["project"] = self._vertex_project
            if self._vertex_location:
                kwargs["location"] = self._vertex_location
        if self._types is not None:
            try:
                kwargs["http_options"] = self._types.HttpOptions(timeout=_HTTP_TIMEOUT_MS)
            except Exception:
                pass
        emit_structured_log(
            "gemini_client_auth_mode",
            mode="api_key" if self._api_key.strip() else "vertex_ai_enterprise",
            project=self._vertex_project if not self._api_key.strip() else None,
        )
        return self._sdk.Client(**kwargs)

    @property
    def client(self) -> Any:
        if self._client is not None:
            return self._client
        with self._client_lock:
            if self._client is None:
                self._client = self._build_client()
        return self._client

    def configure(self, api_key: str | None = None, **_: Any) -> None:
        candidate = str(api_key or "").strip()
        if candidate:
            self._api_key = candidate
            with self._client_lock:
                self._client = self._build_client()

    def GenerativeModel(self, model_name: str, generation_config: Any = None, **_: Any) -> _GenAiModelAdapter:
        return _GenAiModelAdapter(
            self,
            model_name=model_name,
            generation_config=generation_config,
        )

    def embed_content(self, **kwargs: Any) -> dict[str, Any]:
        payload = dict(kwargs)
        model = payload.pop("model", None)
        contents = payload.pop("contents", payload.pop("content", None))
        if not model:
            raise ValueError("embed_content requiere 'model'.")
        if contents is None:
            raise ValueError("embed_content requiere 'content' o 'contents'.")
        config = _normalized_generation_config(payload.pop("config", None))
        task_type = payload.pop("task_type", None)
        title = payload.pop("title", None)
        output_dimensionality = payload.pop("output_dimensionality", None)
        if task_type is not None:
            config["task_type"] = task_type
        if title is not None:
            config["title"] = title
        if output_dimensionality is not None:
            config["output_dimensionality"] = output_dimensionality
        if payload:
            config.update({str(k): v for k, v in payload.items()})

        request: dict[str, Any] = {"model": model, "contents": contents}
        if config:
            request["config"] = config
        response = self.client.models.embed_content(**request)
        embedding = _extract_embedding_values(response)
        if not embedding:
            raise ValueError("Gemini no devolvió un embedding válido.")
        return {"embedding": embedding}

    @staticmethod
    def GenerationConfig(**kwargs: Any) -> dict[str, Any]:
        return dict(kwargs)


def _build_runtime() -> _GenAiRuntime:
    if not _HAS_GENAI_SDK:
        raise RuntimeError(
            "El SDK 'google-genai' no está disponible. Instálalo con: pip install google-genai"
        )
    emit_structured_log("gemini_client_provider_selected", provider="genai")
    return _GenAiRuntime()


genai = _build_runtime()
