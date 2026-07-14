from base64 import b64decode, b64encode

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings
from app.core.structured_logging import emit_structured_log

_FERNET_CACHE: Fernet | None = None


def _get_fernet() -> Fernet | None:
    global _FERNET_CACHE
    if _FERNET_CACHE is not None:
        return _FERNET_CACHE
    key = settings.OAUTH_TOKEN_ENCRYPTION_KEY
    if not key:
        return None
    try:
        _FERNET_CACHE = Fernet(key.encode() if isinstance(key, str) else key)
        return _FERNET_CACHE
    except Exception as exc:
        emit_structured_log(
            "oauth_encryption_init_failed",
            level="error",
            error=str(exc)[:180],
        )
        return None


def encrypt_token(plaintext: str) -> str | None:
    if not plaintext:
        return None
    fernet = _get_fernet()
    if not fernet:
        return plaintext
    try:
        return b64encode(fernet.encrypt(plaintext.encode())).decode()
    except Exception as exc:
        emit_structured_log(
            "oauth_encryption_encrypt_failed",
            level="error",
            error=str(exc)[:180],
        )
        return None


def decrypt_token(ciphertext: str) -> str | None:
    if not ciphertext:
        return None
    fernet = _get_fernet()
    if not fernet:
        return ciphertext
    try:
        return fernet.decrypt(b64decode(ciphertext.encode())).decode()
    except InvalidToken:
        emit_structured_log(
            "oauth_encryption_decrypt_invalid",
            level="error",
        )
        return None
    except Exception as exc:
        emit_structured_log(
            "oauth_encryption_decrypt_failed",
            level="error",
            error=str(exc)[:180],
        )
        return None
