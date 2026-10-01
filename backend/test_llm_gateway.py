from __future__ import annotations

import os
from unittest.mock import MagicMock, patch
try:
    import pytest
except ImportError:
    pytest = None

from app.core.circuit_breaker import (
    GeminiCircuitBreaker,
    GeminiCircuitOpenError,
    GeminiQuotaExceededError,
    LLMCircuitBreaker,
    LLMCircuitOpenError,
    LLMQuotaExceededError,
    is_recoverable_gemini_error,
    is_recoverable_llm_error,
)
from app.core.config import settings
from app.core.gemini_client import _GenAIGateway, _build_provider, genai
from app.core.llm_providers.base import LLMProvider, LLMResponse
from app.core.llm_providers.gemini import GeminiProvider
from app.core.llm_providers.gemini_model import GeminiModelAdapter
from app.core.llm_providers.openai_compat import OpenAICompatProvider


def test_circuit_breaker_aliases():
    """Valida que los aliases del Circuit Breaker sean exactamente las clases originales."""
    assert LLMCircuitBreaker is GeminiCircuitBreaker
    assert LLMQuotaExceededError is GeminiQuotaExceededError
    assert LLMCircuitOpenError is GeminiCircuitOpenError
    assert is_recoverable_llm_error is is_recoverable_gemini_error


def test_gemini_provider_explicit_override(monkeypatch):
    """Valida que una selección explícita conserve Gemini como proveedor soportado."""
    monkeypatch.setattr(settings, "LLM_PROVIDER", "gemini")
    provider = _build_provider()
    assert isinstance(provider, GeminiProvider)
    assert provider.provider_name == "gemini"


def test_deepseek_provider_selection(monkeypatch):
    """Valida que con LLM_PROVIDER=deepseek se construya OpenAICompatProvider con settings de DeepSeek."""
    monkeypatch.setattr(settings, "LLM_PROVIDER", "deepseek")
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "sk-test-deepseek")
    monkeypatch.setattr(settings, "DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setattr(settings, "DEEPSEEK_MODEL", "deepseek-chat")

    with patch("app.core.llm_providers.openai_compat._HAS_OPENAI_SDK", True), \
         patch("app.core.llm_providers.openai_compat._OPENAI_CLIENT_CLASS") as mock_openai:
        provider = _build_provider()
        assert isinstance(provider, OpenAICompatProvider)
        assert provider.provider_name == "openai_compat"
        assert provider._default_model == "deepseek-chat"
        mock_openai.assert_called_once_with(api_key="sk-test-deepseek", base_url="https://api.deepseek.com")


def test_empty_provider_falls_back_to_deepseek_v41_flash(monkeypatch):
    """El gateway conserva DeepSeek como default seguro si LLM_PROVIDER está vacío."""
    monkeypatch.setattr(settings, "LLM_PROVIDER", "")
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "sk-test-deepseek")
    monkeypatch.setattr(settings, "DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setattr(settings, "DEEPSEEK_MODEL", "DeepSeek-V4.1-flash")

    with patch("app.core.llm_providers.openai_compat._HAS_OPENAI_SDK", True), \
         patch("app.core.llm_providers.openai_compat._OPENAI_CLIENT_CLASS") as mock_openai:
        provider = _build_provider()

    assert isinstance(provider, OpenAICompatProvider)
    assert provider._default_model == "DeepSeek-V4.1-flash"
    mock_openai.assert_called_once_with(
        api_key="sk-test-deepseek",
        base_url="https://api.deepseek.com",
    )


def test_openai_provider_selection(monkeypatch):
    """Valida que con LLM_PROVIDER=openai se construya OpenAICompatProvider con settings de OpenAI."""
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test-openai")
    monkeypatch.setattr(settings, "OPENAI_BASE_URL", "")
    monkeypatch.setattr(settings, "OPENAI_MODEL", "gpt-4o")

    with patch("app.core.llm_providers.openai_compat._HAS_OPENAI_SDK", True), \
         patch("app.core.llm_providers.openai_compat._OPENAI_CLIENT_CLASS") as mock_openai:
        provider = _build_provider()
        assert isinstance(provider, OpenAICompatProvider)
        assert provider._default_model == "gpt-4o"
        mock_openai.assert_called_once_with(api_key="sk-test-openai", base_url="https://api.openai.com/v1")


def test_model_name_remap():
    """Valida que modelos con 'gemini' se remapeen automáticamente al default del proveedor."""
    with patch("app.core.llm_providers.openai_compat._HAS_OPENAI_SDK", True), \
         patch("app.core.llm_providers.openai_compat._OPENAI_CLIENT_CLASS"):
        provider = OpenAICompatProvider(api_key="sk-test", base_url="", model="deepseek-chat")
        assert provider._resolve_model("gemini-2.5-flash") == "deepseek-chat"
        assert provider._resolve_model("gemini-3.7-flash") == "deepseek-chat"
        assert provider._resolve_model("") == "deepseek-chat"
        assert provider._resolve_model("deepseek-reasoner") == "deepseek-reasoner"
        assert provider._resolve_model("gpt-4o-mini") == "gpt-4o-mini"


def test_json_word_injection():
    """Valida que se inyecte la palabra 'JSON' sólo cuando es application/json y no existe previamente."""
    with patch("app.core.llm_providers.openai_compat._HAS_OPENAI_SDK", True), \
         patch("app.core.llm_providers.openai_compat._OPENAI_CLIENT_CLASS"):
        provider = OpenAICompatProvider(api_key="sk-test", base_url="", model="deepseek-chat")
        
        # Caso 1: Requiere JSON y no tiene la palabra
        c1 = provider._ensure_json_word("Dame una lista de métricas", {"response_mime_type": "application/json"})
        assert "Responde exclusivamente en formato JSON válido." in c1

        # Caso 2: Ya contiene la palabra json
        c2 = provider._ensure_json_word("Genera un json con métricas", {"response_mime_type": "application/json"})
        assert c2 == "Genera un json con métricas"

        # Caso 3: No requiere JSON (texto plano)
        c3 = provider._ensure_json_word("Explica el resultado", {})
        assert c3 == "Explica el resultado"


def test_embeddings_delegation_principle3(monkeypatch):
    """[PRINCIPIO #3] Valida que con DeepSeek activo, embed_content SIEMPRE delegue a Gemini."""
    monkeypatch.setattr(settings, "LLM_PROVIDER", "deepseek")
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "sk-test")

    with patch("app.core.llm_providers.openai_compat._HAS_OPENAI_SDK", True), \
         patch("app.core.llm_providers.openai_compat._OPENAI_CLIENT_CLASS"):
        gateway = _GenAIGateway()
        assert isinstance(gateway._provider, OpenAICompatProvider)
        assert isinstance(gateway._embeddings_provider, GeminiProvider)

        # Mock de embed_content en _embeddings_provider
        gateway._embeddings_provider.embed_content = MagicMock(return_value={"embedding": [0.1, 0.2, 0.3]})

        result = gateway.embed_content(model="models/gemini-embedding-001", contents="Texto prueba")
        assert result == {"embedding": [0.1, 0.2, 0.3]}
        gateway._embeddings_provider.embed_content.assert_called_once_with(
            model="models/gemini-embedding-001",
            content="Texto prueba",
            task_type=None,
            output_dimensionality=None,
        )


def test_backward_compatibility_interface():
    """Valida que genai exportado tenga todos los métodos y propiedades que usan los 10 consumidores."""
    assert hasattr(genai, "GenerativeModel")
    assert hasattr(genai, "embed_content")
    assert hasattr(genai, "configure")
    assert hasattr(genai, "GenerationConfig")

    # GenerativeModel retorna GeminiModelAdapter con generate_content
    model = genai.GenerativeModel("gemini-2.5-flash", generation_config={"temperature": 0.2})
    assert isinstance(model, GeminiModelAdapter)
    assert hasattr(model, "generate_content")
    assert model.model_name == "gemini-2.5-flash"


def test_llm_response_duck_typing():
    """Valida que LLMResponse soporte acceso a .text, .parsed y delegue a ._raw con __getattr__."""
    mock_raw = MagicMock()
    mock_raw.usage_metadata = {"prompt_tokens": 50, "completion_tokens": 100}

    resp = LLMResponse(text="resultado de prueba", parsed={"ok": True}, raw=mock_raw)
    assert resp.text == "resultado de prueba"
    assert resp.parsed == {"ok": True}
    assert resp.usage_metadata == {"prompt_tokens": 50, "completion_tokens": 100}

    # Atributo inexistente en raw debe lanzar AttributeError
    del mock_raw.inexistente
    raised = False
    try:
        _ = resp.inexistente
    except AttributeError:
        raised = True
    assert raised, "Se esperaba AttributeError para atributo inexistente"


def test_list_contents_safeguard():
    """Valida que OpenAICompatProvider concatene listas de contents sin romper el SDK."""
    with patch("app.core.llm_providers.openai_compat._HAS_OPENAI_SDK", True), \
         patch("app.core.llm_providers.openai_compat._OPENAI_CLIENT_CLASS"):
        provider = OpenAICompatProvider(api_key="sk-test", base_url="", model="deepseek-chat")
        
        mock_completion = MagicMock()
        mock_completion.choices = [MagicMock(message=MagicMock(content="Respuesta simulada"))]
        provider._circuit_breaker.call = MagicMock(return_value=mock_completion)

        response = provider.generate_content(
            model_name="deepseek-chat",
            contents=["Instrucción de sistema", "Pregunta del usuario"],
        )
        assert response.text == "Respuesta simulada"
        # Verificar que el mensaje enviado a la API fue string concatenado con \n\n
        call_kwargs = provider._circuit_breaker.call.call_args[1]
        sent_messages = call_kwargs["messages"]
        assert sent_messages[0]["content"] == "Instrucción de sistema\n\nPregunta del usuario"


if __name__ == "__main__":
    class MockMonkeyPatch:
        def setattr(self, target, name, value):
            setattr(target, name, value)

    all_tests = [
        ("test_circuit_breaker_aliases", lambda: test_circuit_breaker_aliases()),
        ("test_gemini_provider_explicit_override", lambda: test_gemini_provider_explicit_override(MockMonkeyPatch())),
        ("test_deepseek_provider_selection", lambda: test_deepseek_provider_selection(MockMonkeyPatch())),
        ("test_openai_provider_selection", lambda: test_openai_provider_selection(MockMonkeyPatch())),
        ("test_model_name_remap", lambda: test_model_name_remap()),
        ("test_json_word_injection", lambda: test_json_word_injection()),
        ("test_embeddings_delegation_principle3", lambda: test_embeddings_delegation_principle3(MockMonkeyPatch())),
        ("test_backward_compatibility_interface", lambda: test_backward_compatibility_interface()),
        ("test_llm_response_duck_typing", lambda: test_llm_response_duck_typing()),
        ("test_list_contents_safeguard", lambda: test_list_contents_safeguard()),
    ]

    print("Ejecutando suite de pruebas unitarias para LLM Gateway...")
    for name, func in all_tests:
        func()
        print(f"  PASS: {name}")
    print(f"\n✅ {len(all_tests)}/{len(all_tests)} pruebas pasaron exitosamente.")
