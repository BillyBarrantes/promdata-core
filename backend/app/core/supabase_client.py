import httpx
from supabase import create_client, Client
from app.core.config import settings


def _build_client(key: str) -> Client:
    """
    Construye un cliente Supabase con timeouts defensivos.

    [FIX 2026-06-08] Sin timeouts explícitos, el cliente usa los defaults
    de httpx (~10s en connect/read) — cuando Supabase se vuelve lento
    (Disk IO budget agotado, mantenimiento, etc.), nuestro backend se
    cuelga 10+ segundos y retorna 500 en vez de fallar rápido con 503.
    Configuramos timeouts razonables para que el impacto sea mínimo:
    - connect: 3s (Supabase edge está en Cloudflare, debe ser <500ms)
    - read: 8s (queries grandes pueden tardar)
    - write: 5s (writes son simples INSERTs)
    - pool: 3s (timeouts en la cola de conexiones)
    """
    url: str = settings.SUPABASE_URL
    if not url or not key:
        print("CRITICAL WARNING: Faltan credenciales de Supabase en .env del Backend")

    # httpx.Timeout es el format nativo que supabase-py 2.x acepta vía el
    # parámetro `http_client`. Pasamos None para options/limits para no
    # interferir con el resto del setup.
    timeout = httpx.Timeout(
        connect=settings.SUPABASE_CONNECT_TIMEOUT_SECONDS,
        read=settings.SUPABASE_READ_TIMEOUT_SECONDS,
        write=settings.SUPABASE_WRITE_TIMEOUT_SECONDS,
        pool=settings.SUPABASE_POOL_TIMEOUT_SECONDS,
    )
    http_client = httpx.Client(timeout=timeout)
    return create_client(url, key, http_client=http_client)


def get_supabase_service_client() -> Client:
    """
    Cliente service-role para callbacks OAuth y persistencia segura de secretos.
    """
    service_key = settings.SUPABASE_SERVICE_ROLE_KEY or settings.SUPABASE_KEY
    return _build_client(service_key)

def get_supabase_user_client(access_token: str) -> Client:
    """
    Cliente autenticado con el JWT del usuario para respetar auth.get_user().
    """
    client = _build_client(settings.SUPABASE_ANON_KEY or settings.SUPABASE_KEY)
    client.auth.set_session(access_token=access_token, refresh_token=access_token)
    return client


def get_supabase_client() -> Client:
    """
    Singleton para conexión autenticada Supabase desde Python.
    Vital para leer los archivos crudos y el glosario.
    """
    return get_supabase_service_client()
