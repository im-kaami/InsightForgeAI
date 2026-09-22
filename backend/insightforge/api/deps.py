from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from insightforge.config import get_settings
from insightforge.core.llm import LLMClient
from insightforge.db.models import User
from insightforge.db.session import get_db
from insightforge.services.auth import decode_token
from insightforge.services.events import RunEventBus
from insightforge.services.storage import Storage

_bearer = HTTPBearer(auto_error=False)
Db = Annotated[Session, Depends(get_db)]


def get_current_user(
    db: Db, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]
) -> User:
    if not credentials:
        raise HTTPException(401, "Authentication required")
    try:
        user_id = decode_token(credentials.credentials)
    except (jwt.PyJWTError, KeyError):
        raise HTTPException(401, "Invalid access token") from None
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(401, "Invalid access token")
    return user


def get_llm(request: Request) -> LLMClient:
    return request.app.state.llm


def get_bus(request: Request) -> RunEventBus:
    return request.app.state.bus


def get_storage() -> Storage:
    return Storage(get_settings().storage_dir)


CurrentUser = Annotated[User, Depends(get_current_user)]
LLMDep = Annotated[LLMClient, Depends(get_llm)]
BusDep = Annotated[RunEventBus, Depends(get_bus)]
StorageDep = Annotated[Storage, Depends(get_storage)]
