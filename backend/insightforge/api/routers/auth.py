from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from insightforge.api.deps import CurrentUser, Db, SignedInUser
from insightforge.api.schemas import (
    ApiTokenCreated,
    ApiTokenIn,
    ApiTokenOut,
    Credentials,
    Token,
    UserOut,
)
from insightforge.db.models import ApiToken, User
from insightforge.services.auth import (
    API_TOKEN_PREFIX,
    create_access_token,
    hash_password,
    new_api_token,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])
MAX_ACTIVE_TOKENS = 20


@router.post("/register", response_model=UserOut, status_code=201)
def register(body: Credentials, db: Db):
    if db.scalar(select(User).where(User.email == body.email.lower())):
        raise HTTPException(409, "Email is already registered")
    user = User(email=body.email.lower(), password_hash=hash_password(body.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/login", response_model=Token)
def login(body: Credentials, db: Db):
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if not user or not verify_password(user.password_hash, body.password):
        raise HTTPException(401, "Invalid email or password")
    return Token(access_token=create_access_token(user.id))


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser):
    return user


@router.get("/tokens", response_model=list[ApiTokenOut])
def list_tokens(db: Db, user: SignedInUser):
    return db.scalars(
        select(ApiToken).where(ApiToken.owner_id == user.id).order_by(ApiToken.created_at.desc())
    ).all()


@router.post("/tokens", response_model=ApiTokenCreated, status_code=201)
def create_token(body: ApiTokenIn, db: Db, user: SignedInUser):
    now = datetime.now(UTC)
    active = [
        item
        for item in db.scalars(
            select(ApiToken).where(ApiToken.owner_id == user.id, ApiToken.revoked_at.is_(None))
        )
        if (item.expires_at if item.expires_at.tzinfo else item.expires_at.replace(tzinfo=UTC)) > now
    ]
    if len(active) >= MAX_ACTIVE_TOKENS:
        raise HTTPException(409, f"You already have {MAX_ACTIVE_TOKENS} active tokens; revoke one first")
    token, token_hash = new_api_token()
    record = ApiToken(
        owner_id=user.id,
        name=body.name.strip(),
        scope=body.scope,
        token_hash=token_hash,
        prefix=token[: len(API_TOKEN_PREFIX) + 6],
        expires_at=now + timedelta(days=body.expires_in_days),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return ApiTokenCreated(**ApiTokenOut.model_validate(record).model_dump(), token=token)


@router.delete("/tokens/{token_id}", status_code=204)
def revoke_token(token_id: str, db: Db, user: SignedInUser):
    record = db.scalar(select(ApiToken).where(ApiToken.id == token_id, ApiToken.owner_id == user.id))
    if record is None:
        raise HTTPException(404, "Token not found")
    if record.revoked_at is None:
        record.revoked_at = datetime.now(UTC)
        db.commit()
