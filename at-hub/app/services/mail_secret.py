"""Mailbox passwords kept encrypted in the database (key derived from the server's SECRET_KEY): a copied database alone
doesn't give the passwords away, and no screen ever gets one back."""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.config.settings import settings


def _fernet() -> Fernet:
    key = hashlib.sha256(("at-hub-mail:" + settings.secret_key).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def seal(password: str) -> str:
    return _fernet().encrypt(password.encode()).decode()


def unseal(token: str) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:  # the server's SECRET_KEY changed (another server's database): enter it again
        return ""
