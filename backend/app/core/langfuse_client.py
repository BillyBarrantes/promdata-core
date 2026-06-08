"""
Cliente centralizado de Langfuse para PromData.

Arquitectura:
  - get_langfuse() → singleton thread-safe. Retorna None si no hay credenciales.
  - record_llm_call() → context manager que abre un Generation span antes de
    la llamada a Gemini y lo cierra (con output + latencia real) al salir.
    Es un no-op silencioso si Langfuse no está configurado, jamás crashea
    el flujo principal.

Uso estándar:
    with record_llm_call("planning", model_name, prompt, trace_id=task_id) as lf_span:
        response = model.generate_content(prompt)
        lf_span["output"] = response.text

Uso en threads (narrativas paralelas — ThreadPoolExecutor):
    record_llm_event("narrative", model_name, prompt, response.text, trace_id=task_id)

API de Langfuse:
  Esta implementación usa la API de Langfuse v3+/v4+ (start_as_current_observation
  + update_current_generation). La API antigua (lf_client.trace() + .generation())
  fue removida en Langfuse 3.x — ver https://langfuse.com/docs/observability/sdk-python
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any, Generator

from app.core.config import settings
from app.core.structured_logging import emit_structured_log

# ---------------------------------------------------------------------------
# Singleton thread-safe del cliente Langfuse
# ---------------------------------------------------------------------------
_langfuse_lock = threading.Lock()
_langfuse_client: Any = None   # tipo Langfuse | None
_langfuse_initialized: bool = False


def get_langfuse() -> Any | None:
    """
    Retorna el cliente Langfuse singleton, o None si las credenciales
    no están configuradas o si el SDK no está disponible.
    Es seguro llamarlo desde múltiples threads.
    """
    global _langfuse_client, _langfuse_initialized

    if _langfuse_initialized:
        return _langfuse_client

    with _langfuse_lock:
        if _langfuse_initialized:
            return _langfuse_client

        if not (settings.LANGFUSE_SECRET_KEY and settings.LANGFUSE_PUBLIC_KEY):
            emit_structured_log(
                "langfuse_disabled",
                level="warning",
                reason="credentials_not_configured",
            )
            _langfuse_initialized = True
            return None

        try:
            from langfuse import Langfuse  # import lazy para no crashear si no está instalado

            _langfuse_client = Langfuse(
                secret_key=settings.LANGFUSE_SECRET_KEY,
                public_key=settings.LANGFUSE_PUBLIC_KEY,
                host=settings.LANGFUSE_HOST,
                # Flush automático en background — no bloquea el thread de Celery
                flush_interval=5.0,
                flush_at=10,
            )
            emit_structured_log(
                "langfuse_initialized",
                host=settings.LANGFUSE_HOST,
                sdk_version=getattr(_langfuse_client, "_version", "unknown"),
            )
        except Exception as exc:
            emit_structured_log(
                "langfuse_init_failed",
                level="warning",
                error=str(exc)[:500],
            )
            _langfuse_client = None

        _langfuse_initialized = True
        return _langfuse_client


# ---------------------------------------------------------------------------
# Context manager principal: record_llm_call
# Para llamadas sincrónicas directas a generate_content()
# ---------------------------------------------------------------------------
@contextmanager
def record_llm_call(
    span_name: str,
    model_name: str,
    prompt: str,
    trace_id: str | None = None,
    trace_name: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Generator[dict[str, Any], None, None]:
    """
    Context manager que instrumenta una llamada a Gemini en Langfuse.

    El caller debe asignar la respuesta al dict yielded:
        lf_span["output"] = response.text

    Si Langfuse no está configurado o falla, es un no-op transparente.
    Nunca propaga excepciones propias al flujo principal.

    Args:
        span_name:   Nombre descriptivo de la etapa (ej: "planning", "synthesis").
        model_name:  Nombre del modelo Gemini usado.
        prompt:      Texto del prompt enviado (se trunca a 15k chars para seguridad).
        trace_id:    ID de traza padre (normalmente el task_id de Celery).
        trace_name:  Nombre de la traza raíz (ej: "analysis_task").
        metadata:    Dict de metadata adicional para contexto.

    Yields:
        dict con clave "output" para que el caller inyecte la respuesta.
    """
    lf_client = get_langfuse()
    result: dict[str, Any] = {}

    if lf_client is None:
        yield result
        return

    # Construir trace_context para conectar este span a un trace_id existente
    # (típicamente el task_id de Celery). Si no se pasa, Langfuse genera uno.
    trace_context: dict[str, str] | None = None
    if trace_id:
        trace_context = {"trace_id": str(trace_id)}

    try:
        with lf_client.start_as_current_observation(
            trace_context=trace_context,
            name=trace_name or span_name,
            as_type="generation",
            input=[{"role": "user", "content": prompt[:15_000]}],
            model=model_name,
            metadata=metadata or {},
        ) as generation:
            try:
                yield result
            except Exception as call_exc:
                # La llamada a Gemini falló — registrar el error en Langfuse
                try:
                    lf_client.update_current_generation(
                        level="ERROR",
                        status_message=str(call_exc)[:500],
                    )
                except Exception:
                    pass
                raise  # re-raise para no silenciar el error real
            else:
                # Llamada exitosa — registrar output via update_current_generation
                try:
                    lf_client.update_current_generation(
                        output=str(result.get("output", ""))[:15_000],
                    )
                except Exception as end_exc:
                    emit_structured_log(
                        "langfuse_span_close_failed",
                        level="warning",
                        span=span_name,
                        error=str(end_exc)[:500],
                    )
    except Exception as setup_exc:
        # Si falla el setup de Langfuse, el análisis sigue funcionando
        emit_structured_log(
            "langfuse_span_setup_failed",
            level="warning",
            span=span_name,
            error=str(setup_exc)[:500],
        )
        yield result
        return


# ---------------------------------------------------------------------------
# Función fire-and-forget: record_llm_event
# Para llamadas en threads (ThreadPoolExecutor — narrativas paralelas)
# donde el context manager no puede cruzar boundaries de thread.
# ---------------------------------------------------------------------------
def record_llm_event(
    span_name: str,
    model_name: str,
    prompt: str,
    output: str,
    trace_id: str | None = None,
    trace_name: str | None = None,
    metadata: dict[str, Any] | None = None,
    level: str = "DEFAULT",
) -> None:
    """
    Registra una llamada LLM ya completada en Langfuse (fire-and-forget).
    Diseñado para uso en threads donde el context manager no puede cruzar
    el boundary del executor.

    Nunca lanza excepciones.
    """
    lf_client = get_langfuse()
    if lf_client is None:
        return

    trace_context: dict[str, str] | None = None
    if trace_id:
        trace_context = {"trace_id": str(trace_id)}

    try:
        # Crear observation manual (no es context manager) y cerrarla inmediatamente
        observation = lf_client.start_observation(
            trace_context=trace_context,
            name=trace_name or span_name,
            as_type="generation",
            input=[{"role": "user", "content": prompt[:15_000]}],
            model=model_name,
            output=output[:15_000],
            metadata=metadata or {},
            level=level,
        )
        # Langfuse v4 cierra automáticamente cuando el objeto sale de scope
    except Exception as exc:
        emit_structured_log(
            "langfuse_event_failed",
            level="warning",
            span=span_name,
            error=str(exc)[:500],
        )
