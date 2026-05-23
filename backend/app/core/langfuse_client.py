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
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any, Generator

from app.core.config import settings

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
            print("[LANGFUSE] Credenciales no configuradas — trazas LLM desactivadas.", flush=True)
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
            print(
                f"[LANGFUSE] Inicializado → host='{settings.LANGFUSE_HOST}'.",
                flush=True,
            )
        except Exception as exc:
            print(f"[LANGFUSE] Error inicializando SDK: {exc} — trazas desactivadas.", flush=True)
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

    generation = None
    try:
        trace = lf_client.trace(
            id=trace_id,
            name=trace_name or "llm_call",
            metadata=metadata or {},
        )
        generation = trace.generation(
            name=span_name,
            model=model_name,
            input=[{"role": "user", "content": prompt[:15_000]}],
            metadata=metadata or {},
        )
    except Exception as setup_exc:
        # Si falla el setup de Langfuse, el análisis sigue funcionando
        print(f"[LANGFUSE] Error creando span '{span_name}': {setup_exc}", flush=True)
        yield result
        return

    try:
        yield result
    except Exception as call_exc:
        # La llamada a Gemini falló — registrar el error en Langfuse
        if generation is not None:
            try:
                generation.end(
                    level="ERROR",
                    status_message=str(call_exc)[:500],
                )
            except Exception:
                pass
        raise  # re-raise para no silenciar el error real
    else:
        # Llamada exitosa — registrar output
        if generation is not None:
            try:
                generation.end(
                    output=str(result.get("output", ""))[:15_000],
                )
            except Exception as end_exc:
                print(f"[LANGFUSE] Error cerrando span '{span_name}': {end_exc}", flush=True)


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

    try:
        trace = lf_client.trace(
            id=trace_id,
            name=trace_name or "llm_call",
            metadata=metadata or {},
        )
        trace.generation(
            name=span_name,
            model=model_name,
            input=[{"role": "user", "content": prompt[:15_000]}],
            output=output[:15_000],
            metadata=metadata or {},
            level=level,
        ).end()
    except Exception as exc:
        print(f"[LANGFUSE] Error registrando evento '{span_name}': {exc}", flush=True)
