import hashlib
import re
import secrets
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError

from insightforge.config import get_settings

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerificationError:
        return False


def create_access_token(user_id: str, token_version: int = 0) -> str:
    settings = get_settings()
    expires = datetime.now(UTC) + timedelta(minutes=settings.access_token_minutes)
    claims = {"sub": user_id, "exp": expires, "tv": token_version}
    return jwt.encode(claims, settings.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> tuple[str, int]:
    """Return the user id and the token version the token was issued for (missing means 0)."""
    payload = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
    return str(payload["sub"]), int(payload.get("tv", 0))


_dummy_hash: str | None = None


def spend_password_check_time(password: str) -> None:
    """Hash-verify against a dummy so unknown accounts take as long to reject as known ones."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = hash_password("insightforge-dummy-password")
    verify_password(_dummy_hash, password)


def create_verify_token(user_id: str, hours: int = 48) -> str:
    claims = {"sub": user_id, "purpose": "verify-email", "exp": datetime.now(UTC) + timedelta(hours=hours)}
    return jwt.encode(claims, get_settings().jwt_secret, algorithm="HS256")


def decode_verify_token(token: str) -> str:
    payload = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
    if payload.get("purpose") != "verify-email":
        raise jwt.InvalidTokenError("wrong purpose")
    return str(payload["sub"])


def new_reset_secret() -> tuple[str, str]:
    """Return (secret, sha256 hex). The secret is shown once; only the hash is stored."""
    secret = "ifr_" + secrets.token_urlsafe(32)
    return secret, hashlib.sha256(secret.encode("utf-8")).hexdigest()


def hash_reset_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


API_TOKEN_PREFIX = "ifk_"
API_TOKEN_SCOPES = ("read", "ask")
# Besides reading (GET), an "ask" token may only start sessions and runs.
_ASK_ROUTES = (
    re.compile(r"^/api/sessions/?$"),
    re.compile(r"^/api/sessions/[^/]+/runs/?$"),
    # A checked metric report reads data and writes only a run, like asking a question.
    re.compile(r"^/api/datasets/[^/]+/reports/metric/?$"),
)


def new_api_token() -> tuple[str, str]:
    """Return (token, sha256 hex). The token is shown once; only the hash is stored."""
    token = API_TOKEN_PREFIX + secrets.token_urlsafe(32)
    return token, hash_api_token(token)


def hash_api_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def is_api_token(value: str) -> bool:
    return value.startswith(API_TOKEN_PREFIX)


def api_token_allows(scope: str, method: str, path: str) -> bool:
    """Whether a token with ``scope`` may call ``method path``. Token management is never allowed."""
    if path.rstrip("/").startswith("/api/auth/tokens"):
        return False
    if method in {"GET", "HEAD", "OPTIONS"}:
        return scope in API_TOKEN_SCOPES
    return scope == "ask" and method == "POST" and any(item.match(path) for item in _ASK_ROUTES)
