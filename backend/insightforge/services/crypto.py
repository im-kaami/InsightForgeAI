import base64
import hashlib

from cryptography.fernet import Fernet

from insightforge.config import get_settings


def _fernet() -> Fernet:
    digest = hashlib.sha256(get_settings().app_secret.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    return _fernet().decrypt(value.encode()).decode()
