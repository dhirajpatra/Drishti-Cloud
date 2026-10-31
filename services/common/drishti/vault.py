"""Encrypts camera passwords before they are stored in Postgres (Fernet = AES-128-CBC + HMAC).

Key: SECRETS_KEY (a Fernet key: python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())").
Without it a key is derived from POSTGRES_PASSWORD, which still keeps passwords out of plain text in a database dump, but anyone
who knows your .env can decrypt - set SECRETS_KEY for anything beyond a laptop.
"""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from .config import settings


def key_is_custom() -> bool:
    return bool(settings.secrets_key)


def _fernet() -> Fernet:
    if settings.secrets_key:
        return Fernet(settings.secrets_key.encode())
    digest = hashlib.sha256(("drishti-vault:" + settings.postgres_password).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(text: str) -> str:
    return _fernet().encrypt(text.encode()).decode()


def decrypt(token: str) -> str:
    """'' when the token cannot be decrypted (key changed): the camera then fails visibly and the admin re-enters the password."""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return ""
