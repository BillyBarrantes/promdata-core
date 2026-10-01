from __future__ import annotations

from app.core.config import settings


def active_llm_provider() -> str:
    """Proveedor de IA activo para análisis, narración y traducción (LLM_PROVIDER)."""
    return str(getattr(settings, "LLM_PROVIDER", "deepseek") or "deepseek").strip().lower()


def active_llm_model() -> str:
    """Nombre del modelo efectivo del proveedor activo.

    Cada proveedor tiene su propia variable de modelo; si no está definida, cae a
    `AI_MODEL_NAME` como fuente neutral. Función pura: sólo lee configuración.
    """
    provider = active_llm_provider()
    if provider == "deepseek":
        return str(
            getattr(settings, "DEEPSEEK_MODEL", "") or getattr(settings, "AI_MODEL_NAME", "")
        ).strip()
    if provider == "openai":
        return str(
            getattr(settings, "OPENAI_MODEL", "") or getattr(settings, "AI_MODEL_NAME", "")
        ).strip()
    return str(getattr(settings, "AI_MODEL_NAME", "")).strip()


def llm_provider_ready() -> bool:
    """True si el proveedor de análisis activo tiene credenciales configuradas."""
    provider = active_llm_provider()
    if provider == "deepseek":
        return bool(str(getattr(settings, "DEEPSEEK_API_KEY", "") or "").strip())
    if provider == "openai":
        return bool(str(getattr(settings, "OPENAI_API_KEY", "") or "").strip())
    return bool(
        str(getattr(settings, "GEMINI_API_KEY", "") or "").strip()
        or str(getattr(settings, "GEMINI_VERTEX_PROJECT", "") or "").strip()
    )


def embeddings_provider_ready() -> bool:
    """Estado del proveedor de embeddings.

    El gateway (`app/core/gemini_client.py`) delega SIEMPRE los embeddings a Gemini
    (Knowledge/RAG y memoria de sesión). El motor de análisis ya no depende de
    Gemini, pero estas dos capacidades auxiliares sí. Su ausencia degrada esas
    funciones sin bloquear el análisis.
    """
    return bool(
        str(getattr(settings, "GEMINI_API_KEY", "") or "").strip()
        or str(getattr(settings, "GEMINI_VERTEX_PROJECT", "") or "").strip()
    )
