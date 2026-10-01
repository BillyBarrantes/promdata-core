"""
test_security_isolation.py — Suite de Seguridad Multi-Tenant y Control de Datos (Fase 2)
═════════════════════════════════════════════════════════════════════════════════════════
Verifica:
  1. Cifrado OAuth Fail-Closed: tokens nunca se persisten en texto plano sin clave Fernet.
  2. Resolución de Equipos Unívoca: eliminación de .limit(1) y bloqueo ante ambigüedad multi-equipo.
  3. Aislamiento Cross-Tenant de Archivos: rechazo estricto (404/403) ante acceso a archivos ajenos.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
from unittest.mock import MagicMock

from cryptography.fernet import Fernet
from fastapi import HTTPException
import pytest

sys.path.insert(0, os.path.dirname(__file__))

from app.core.config import settings
from app.core.oauth_encryption import (
    _reset_fernet_cache,
    decrypt_token,
    encrypt_token,
)
from app.services.governance import (
    get_user_uploaded_file_scope_or_404,
    resolve_user_team_scope,
)


# ── 1. PRUEBAS DE CIFRADO OAUTH FAIL-CLOSED ───────────────────────────────────

def test_oauth_encryption_fail_closed_when_key_missing(monkeypatch):
    """Garantiza que sin clave de cifrado, encrypt_token NUNCA retorna texto plano."""
    monkeypatch.setattr(settings, "OAUTH_TOKEN_ENCRYPTION_KEY", "")
    _reset_fernet_cache()

    plaintext = "super-secret-oauth-token-12345"

    # En el código anterior vulnerable retornaba 'plaintext'
    # Con el blindaje de Fase 2 DEBE retornar None (fail-closed)
    encrypted = encrypt_token(plaintext)
    assert encrypted is None, "VULNERABILIDAD: encrypt_token no debe devolver texto plano si falta la clave"

    decrypted = decrypt_token("any-ciphertext")
    assert decrypted is None, "decrypt_token debe devolver None si falta la clave"


def test_oauth_encryption_roundtrip_with_valid_key(monkeypatch):
    """Garantiza que con clave válida el cifrado y descifrado funcionan en redondo."""
    valid_key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "OAUTH_TOKEN_ENCRYPTION_KEY", valid_key)
    _reset_fernet_cache()

    plaintext = "google-oauth-refresh-token-xyz"
    encrypted = encrypt_token(plaintext)

    assert encrypted is not None
    assert encrypted != plaintext, "El texto cifrado no debe ser igual al texto plano"

    decrypted = decrypt_token(encrypted)
    assert decrypted == plaintext, "El descifrado debe recuperar el token original"

    _reset_fernet_cache()


# ── 2. PRUEBAS DE RESOLUCIÓN UNÍVOCA DE EQUIPOS (GOVERNANCE) ──────────────────

def test_resolve_team_scope_zero_teams_returns_none():
    """Usuario individual B2C sin equipos retorna None (ámbito personal seguro)."""
    mock_client = MagicMock()
    mock_query = MagicMock()
    mock_client.table.return_value = mock_query
    mock_query.select.return_value = mock_query
    mock_query.eq.return_value = mock_query
    mock_query.limit.return_value = mock_query
    mock_query.execute.return_value = MagicMock(data=[])

    scope = resolve_user_team_scope(user_id="usr-individual", service_client=mock_client)
    assert scope is None


def test_resolve_team_scope_single_team_returns_team_id():
    """Usuario con 1 solo equipo resuelve dicho equipo directamente."""
    mock_client = MagicMock()
    mock_query = MagicMock()
    mock_client.table.return_value = mock_query
    mock_query.select.return_value = mock_query
    mock_query.eq.return_value = mock_query
    mock_query.limit.return_value = mock_query
    mock_query.execute.return_value = MagicMock(data=[{"team_id": "team-finance-01"}])

    scope = resolve_user_team_scope(user_id="usr-finance", service_client=mock_client)
    assert scope == "team-finance-01"


def test_resolve_team_scope_multiple_teams_without_explicit_raises_400():
    """Usuario con 2+ equipos sin explicit_team_id debe fallar con 400 Bad Request por ambigüedad."""
    mock_client = MagicMock()
    mock_query = MagicMock()
    mock_client.table.return_value = mock_query
    mock_query.select.return_value = mock_query
    mock_query.eq.return_value = mock_query
    mock_query.limit.return_value = mock_query
    # Retorna 2 equipos para simular ambigüedad
    mock_query.execute.return_value = MagicMock(data=[
        {"team_id": "team-ventas"},
        {"team_id": "team-marketing"},
    ])

    with pytest.raises(HTTPException) as exc_info:
        resolve_user_team_scope(user_id="usr-multi-team", service_client=mock_client)

    assert exc_info.value.status_code == 400
    assert "ambiguo" in exc_info.value.detail.lower()


def test_resolve_team_scope_multiple_teams_with_valid_explicit_team_id():
    """Usuario con múltiples equipos pero con explicit_team_id válido resuelve satisfactoriamente."""
    mock_client = MagicMock()
    mock_query = MagicMock()
    mock_client.table.return_value = mock_query
    mock_query.select.return_value = mock_query
    mock_query.eq.return_value = mock_query
    mock_query.limit.return_value = mock_query
    mock_query.execute.return_value = MagicMock(data=[{"team_id": "team-ventas"}])

    scope = resolve_user_team_scope(
        user_id="usr-multi-team",
        service_client=mock_client,
        explicit_team_id="team-ventas",
    )
    assert scope == "team-ventas"


def test_resolve_team_scope_explicit_team_unauthorized_raises_403():
    """Intento de acceder a un equipo al que el usuario no pertenece lanza 403 Forbidden."""
    mock_client = MagicMock()
    mock_query = MagicMock()
    mock_client.table.return_value = mock_query
    mock_query.select.return_value = mock_query
    mock_query.eq.return_value = mock_query
    mock_query.limit.return_value = mock_query
    # data vacía: el usuario NO es miembro de ese equipo
    mock_query.execute.return_value = MagicMock(data=[])

    with pytest.raises(HTTPException) as exc_info:
        resolve_user_team_scope(
            user_id="usr-attacker",
            service_client=mock_client,
            explicit_team_id="team-victim",
        )

    assert exc_info.value.status_code == 403


# ── 3. PRUEBAS DE AISLAMIENTO CROSS-TENANT DE ARCHIVOS ────────────────────────

def test_user_cannot_access_other_users_file():
    """get_user_uploaded_file_scope_or_404 lanza 404 cuando el archivo no pertenece al usuario."""
    mock_client = MagicMock()
    mock_query = MagicMock()
    mock_client.table.return_value = mock_query
    mock_query.select.return_value = mock_query
    mock_query.eq.return_value = mock_query
    mock_query.limit.return_value = mock_query
    mock_query.execute.return_value = MagicMock(data=[])  # No encontrado para ese user_id

    with pytest.raises(HTTPException) as exc_info:
        get_user_uploaded_file_scope_or_404(
            user_id="usr-alice",
            team_id=None,
            file_id="file-of-bob",
            service_client=mock_client,
        )

    assert exc_info.value.status_code == 404


def test_user_cannot_access_file_from_different_active_team():
    """Si el archivo pertenece al usuario pero bajo otro team_id activo, rechaza con 403."""
    mock_client = MagicMock()
    mock_query = MagicMock()
    mock_client.table.return_value = mock_query
    mock_query.select.return_value = mock_query
    mock_query.eq.return_value = mock_query
    mock_query.limit.return_value = mock_query
    mock_query.execute.return_value = MagicMock(data=[{
        "id": "file-123",
        "user_id": "usr-alice",
        "team_id": "team-marketing",
        "file_name": "marketing.csv",
        "storage_path": "usr-alice/marketing.csv",
        "created_at": "2026-09-02T21:00:00Z",
    }])

    with pytest.raises(HTTPException) as exc_info:
        get_user_uploaded_file_scope_or_404(
            user_id="usr-alice",
            team_id="team-ventas",  # Equipo activo no coincide con team-marketing
            file_id="file-123",
            service_client=mock_client,
        )

    assert exc_info.value.status_code == 403
