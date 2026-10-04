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


def create_access_token(user_id: str) -> str:
    settings = get_settings()
    expires = datetime.now(UTC) + timedelta(minutes=settings.access_token_minutes)
    return jwt.encode({"sub": user_id, "exp": expires}, settings.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> str:
    payload = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
    return str(payload["sub"])


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
