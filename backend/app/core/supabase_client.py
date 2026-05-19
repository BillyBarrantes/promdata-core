from supabase import create_client, Client
from app.core.config import settings


def _build_client(key: str) -> Client:
    url: str = settings.SUPABASE_URL
    if not url or not key:
        print("CRITICAL WARNING: Faltan credenciales de Supabase en .env del Backend")
    return create_client(url, key)


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
