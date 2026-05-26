from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import Any

from app.core.config import settings
from app.core.structured_logging import emit_structured_log

_HTTP_TIMEOUT_MS = int(os.getenv("GEMINI_HTTP_TIMEOUT_MS", "60000") or "60000")

_HAS_GENAI_SDK = False
_GENAI_SDK = None
_GENAI_TYPES = None

_HAS_LEGACY_SDK = False
_LEGACY_GENAI_SDK = None

try:
    from google import genai as _genai_sdk  # type: ignore[import]
    from google.genai import types as _genai_types  # type: ignore[import]

    _GENAI_SDK = _genai_sdk
    _GENAI_TYPES = _genai_types
    _HAS_GENAI_SDK = True
except Exception:
    _HAS_GENAI_SDK = False

try:
    import google.generativeai as _legacy_genai_sdk  # type: ignore[import]

    _LEGACY_GENAI_SDK = _legacy_genai_sdk
    _HAS_LEGACY_SDK = True
except Exception:
    _HAS_LEGACY_SDK = False


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
        response = self._runtime.client.models.generate_content(**request)
        return _CompatGenerateResponse(text=_extract_text_from_response(response), raw=response)


class _LegacyModelAdapter:
    def __init__(self, runtime: "_LegacyRuntime", *, model_name: str, generation_config: Any = None) -> None:
        self._runtime = runtime
        self.model_name = str(model_name)
        self._generation_config = generation_config
        self._runtime._ensure_configured()
        self._model = self._runtime._sdk.GenerativeModel(
            model_name=self.model_name,
            generation_config=self._generation_config,
        )

    def generate_content(self, contents: Any, generation_config: Any = None, **kwargs: Any) -> _CompatGenerateResponse:
        merged_config = _merge_generation_config(self._generation_config, generation_config)
        if kwargs:
            merged_config.update({str(k): v for k, v in kwargs.items()})
        if merged_config:
            response = self._model.generate_content(contents, generation_config=merged_config)
        else:
            response = self._model.generate_content(contents)
        return _CompatGenerateResponse(text=_extract_text_from_response(response), raw=response)


class _GenAiRuntime:
    provider = "genai"

    def __init__(self) -> None:
        if _GENAI_SDK is None:
            raise RuntimeError("Google GenAI SDK no está disponible.")
        self._sdk = _GENAI_SDK
        self._types = _GENAI_TYPES
        self._api_key = str(settings.GEMINI_API_KEY or "")
        self._client_lock = threading.Lock()
        self._client = None

    def _build_client(self) -> Any:
        kwargs: dict[str, Any] = {}
        if self._api_key:
            kwargs["api_key"] = self._api_key
        if self._types is not None:
            try:
                kwargs["http_options"] = self._types.HttpOptions(timeout=_HTTP_TIMEOUT_MS)
            except Exception:
                pass
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


class _LegacyRuntime:
    provider = "legacy"

    def __init__(self) -> None:
        if _LEGACY_GENAI_SDK is None:
            raise RuntimeError("Google legacy GenerativeAI SDK no está disponible.")
        self._sdk = _LEGACY_GENAI_SDK
        self._api_key = str(settings.GEMINI_API_KEY or "")
        self._configured = False
        self._configure_lock = threading.Lock()

    def _ensure_configured(self) -> None:
        if self._configured:
            return
        with self._configure_lock:
            if self._configured:
                return
            self._sdk.configure(api_key=self._api_key)
            self._configured = True

    def configure(self, api_key: str | None = None, **_: Any) -> None:
        candidate = str(api_key or "").strip()
        if candidate:
            self._api_key = candidate
        with self._configure_lock:
            self._configured = False
        self._ensure_configured()

    def GenerativeModel(self, model_name: str, generation_config: Any = None, **_: Any) -> _LegacyModelAdapter:
        return _LegacyModelAdapter(
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

        request: dict[str, Any] = {"model": model, "content": contents}
        request.update(config)

        self._ensure_configured()
        try:
            response = self._sdk.embed_content(**request)
        except TypeError:
            if "output_dimensionality" not in request:
                raise
            request.pop("output_dimensionality", None)
            response = self._sdk.embed_content(**request)

        embedding = _extract_embedding_values(response)
        if not embedding:
            raise ValueError("Gemini no devolvió un embedding válido.")
        return {"embedding": embedding}

    def GenerationConfig(self, **kwargs: Any) -> Any:
        generation_config_cls = getattr(self._sdk, "GenerationConfig", None)
        if callable(generation_config_cls):
            return generation_config_cls(**kwargs)
        return dict(kwargs)


def _requested_provider() -> str:
    candidate = str(getattr(settings, "GEMINI_CLIENT_PROVIDER", "genai") or "genai").strip().lower()
    if candidate in {"genai", "legacy"}:
        return candidate
    emit_structured_log(
        "gemini_client_provider_invalid",
        level="warning",
        requested_provider=candidate,
        effective_provider="genai",
    )
    return "genai"


def _build_runtime() -> Any:
    provider = _requested_provider()

    if provider == "legacy":
        if _HAS_LEGACY_SDK:
            emit_structured_log("gemini_client_provider_selected", provider="legacy")
            return _LegacyRuntime()
        raise RuntimeError(
            "GEMINI_CLIENT_PROVIDER=legacy pero no está disponible el SDK 'google-generativeai'."
        )

    if _HAS_GENAI_SDK:
        emit_structured_log("gemini_client_provider_selected", provider="genai")
        return _GenAiRuntime()

    raise RuntimeError(
        "GEMINI_CLIENT_PROVIDER=genai pero no está disponible el SDK 'google-genai'."
    )


genai = _build_runtime()
