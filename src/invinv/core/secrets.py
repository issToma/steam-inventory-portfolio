"""Encryption of secrets kept in the database (the Telegram bot token).

The key is derived from INVINV_UI_STORAGE_SECRET, which lives in .env rather than in the
database: a copy of the database alone does not give the token away. Changing the secret
makes stored secrets unreadable; they then count as not set and are entered again.
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

# Stored values start with this, so that values saved before encryption still read.
PREFIX = "enc:v1:"
_SALT = b"invinv secrets v1"


def _fernet(secret: str) -> Fernet:
    key = hashlib.pbkdf2_hmac("sha256", secret.encode(), _SALT, 200_000)
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt(value: str, secret: str) -> str:
    return PREFIX + _fernet(secret).encrypt(value.encode()).decode()


def decrypt(stored: str, secret: str) -> str | None:
    """The secret, or None when it was encrypted with another key (or is damaged)."""
    if not stored.startswith(PREFIX):
        return stored  # saved before encryption
    try:
        return _fernet(secret).decrypt(stored[len(PREFIX) :].encode()).decode()
    except InvalidToken:
        return None
