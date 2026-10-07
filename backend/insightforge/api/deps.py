from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from insightforge.config import get_settings
from insightforge.core.llm import LLMClient
from insightforge.db.models import ApiToken, User
from insightforge.db.session import get_db
from insightforge.services.auth import api_token_allows, decode_token, hash_api_token, is_api_token
from insightforge.services.events import RunEventBus
from insightforge.services.storage import Storage

_bearer = HTTPBearer(auto_error=False)
Db = Annotated[Session, Depends(get_db)]


def _api_token_user(db: Session, request: Request, token: str) -> User:
    record = db.scalar(select(ApiToken).where(ApiToken.token_hash == hash_api_token(token)))
    now = datetime.now(UTC)
    if record is None or record.revoked_at is not None or _utc(record.expires_at) <= now:
        raise HTTPException(401, "Invalid or expired API token")
    if not api_token_allows(record.scope, request.method, request.url.path):
        raise HTTPException(403, f"This API token ({record.scope}) cannot do that")
    user = db.get(User, record.owner_id)
    if not user:
        raise HTTPException(401, "Invalid or expired API token")
    if record.last_used_at is None or now - _utc(record.last_used_at) > timedelta(minutes=1):
        record.last_used_at = now
        db.commit()
    request.state.api_token_id = record.id
    return user


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def get_current_user(
    request: Request,
    db: Db,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    if not credentials:
        raise HTTPException(401, "Authentication required")
    if is_api_token(credentials.credentials):
        return _api_token_user(db, request, credentials.credentials)
    try:
        user_id, version = decode_token(credentials.credentials)
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(401, "Invalid access token") from None
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(401, "Invalid access token")
    if version != (user.token_version or 0):
        raise HTTPException(401, "Your session has ended; sign in again")
    return user


def get_llm(request: Request) -> LLMClient:
    return request.app.state.llm


def get_bus(request: Request) -> RunEventBus:
    return request.app.state.bus


def get_storage() -> Storage:
    return Storage(get_settings().storage_dir)


def get_signed_in_user(request: Request, user: Annotated[User, Depends(get_current_user)]) -> User:
    """Only a signed-in session, never an API token (used to manage tokens)."""
    if getattr(request.state, "api_token_id", None):
        raise HTTPException(403, "API tokens cannot manage API tokens; sign in instead")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
SignedInUser = Annotated[User, Depends(get_signed_in_user)]
LLMDep = Annotated[LLMClient, Depends(get_llm)]
BusDep = Annotated[RunEventBus, Depends(get_bus)]
StorageDep = Annotated[Storage, Depends(get_storage)]
