import logging
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from insightforge.api.deps import CurrentUser, Db, SignedInUser
from insightforge.api.schemas import (
    ApiTokenCreated,
    ApiTokenIn,
    ApiTokenOut,
    Credentials,
    ForgotPassword,
    NewCredentials,
    PasswordChange,
    PasswordReset,
    Preferences,
    Token,
    UserOut,
)
from insightforge.config import get_settings
from insightforge.db.models import ApiToken, User
from insightforge.db.models import PasswordReset as PasswordResetRecord
from insightforge.services import oidc
from insightforge.services.auth import (
    API_TOKEN_PREFIX,
    create_access_token,
    hash_password,
    new_api_token,
    spend_password_check_time,
    verify_password,
)
from insightforge.services.login_limits import lockout_message, login_limiter
from insightforge.services.mailer import MailError, email_enabled, send_email, send_in_background
from insightforge.services.password_reset import create_reset, redeem_reset

router = APIRouter(prefix="/auth", tags=["auth"])
MAX_ACTIVE_TOKENS = 20
TEST_EMAIL_SECONDS = 60
FORGOT_MINUTES = 5


@router.post("/register", response_model=UserOut, status_code=201)
def register(body: NewCredentials, db: Db):
    if db.scalar(select(User).where(User.email == body.email)):
        raise HTTPException(409, "Email is already registered")
    user = User(email=body.email, password_hash=hash_password(body.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _refuse_if_locked(email: str) -> None:
    wait = login_limiter.retry_after(email)
    if wait:
        raise HTTPException(429, lockout_message(wait), headers={"Retry-After": str(wait)})


@router.post("/login", response_model=Token)
def login(body: Credentials, db: Db):
    email = body.email.strip().lower()
    _refuse_if_locked(email)
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        spend_password_check_time(body.password)
    if user is None or not verify_password(user.password_hash, body.password):
        login_limiter.record_failure(email)
        raise HTTPException(401, "Invalid email or password")
    login_limiter.clear(email)
    return Token(access_token=create_access_token(user.id, user.token_version or 0))


@router.post("/password", response_model=Token)
def change_password(body: PasswordChange, db: Db, user: SignedInUser):
    """Change the password; every other session ends and this one gets a fresh token."""
    _refuse_if_locked(user.email)
    if not verify_password(user.password_hash, body.current_password):
        login_limiter.record_failure(user.email)
        raise HTTPException(400, "Current password is incorrect")
    login_limiter.clear(user.email)
    user.password_hash = hash_password(body.new_password)
    user.token_version = (user.token_version or 0) + 1
    db.commit()
    return Token(access_token=create_access_token(user.id, user.token_version))


@router.post("/logout-all", status_code=204)
def logout_all(db: Db, user: SignedInUser):
    user.token_version = (user.token_version or 0) + 1
    db.commit()
    return Response(status_code=204)


@router.get("/features")
def features():
    """What this server can do for sign-in pages (public, reveals nothing about accounts)."""
    return {"email": email_enabled()}


@router.put("/preferences", response_model=UserOut)
def update_preferences(body: Preferences, db: Db, user: SignedInUser):
    user.email_alerts = body.email_alerts
    db.commit()
    db.refresh(user)
    return user


_last_test_email: dict[str, float] = {}


@router.post("/test-email", status_code=204)
def test_email(user: SignedInUser):
    if not email_enabled():
        raise HTTPException(503, "Email is not set up on this server")
    now = time.monotonic()
    if now - _last_test_email.get(user.id, -TEST_EMAIL_SECONDS) < TEST_EMAIL_SECONDS:
        raise HTTPException(429, "A test email was just sent; wait a minute before sending another")
    _last_test_email[user.id] = now
    try:
        send_email(
            user.email,
            "InsightForge test email",
            "This is a test email from InsightForge. Email alerts will reach this address.",
        )
    except MailError as error:
        logging.getLogger("insightforge").warning("test email failed: %s", error)
        raise HTTPException(502, "The email could not be sent; check the SMTP settings") from None
    return Response(status_code=204)


@router.post("/password/forgot", status_code=204)
def forgot_password(body: ForgotPassword, db: Db):
    """Email a reset link. The answer is the same for known and unknown addresses."""
    if not email_enabled():
        raise HTTPException(503, "Email is not set up on this server; ask whoever runs it for a reset link")
    email = body.email.strip().lower()
    user = db.scalar(select(User).where(User.email == email))
    if user is not None:
        recent = db.scalar(
            select(PasswordResetRecord.id).where(
                PasswordResetRecord.user_id == user.id,
                PasswordResetRecord.created_at > datetime.now(UTC) - timedelta(minutes=FORGOT_MINUTES),
            )
        )
        if recent is None:
            secret, _ = create_reset(db, email, hours=1)
            link = f"{get_settings().app_base_url.rstrip('/')}/reset#{secret}"
            send_in_background(
                user.email,
                "Reset your InsightForge password",
                f"Open this link to choose a new password. It works once and for 1 hour:\n\n{link}\n\n"
                "If you did not ask for this, ignore this email.\n",
            )
    return Response(status_code=204)


@router.post("/password/reset", status_code=204)
def reset_password(body: PasswordReset, db: Db):
    user = redeem_reset(db, body.token, body.new_password)
    if user is None:
        raise HTTPException(400, "This reset link is invalid or has expired")
    login_limiter.clear(user.email)
    return Response(status_code=204)


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser):
    return user


@router.get("/oidc/config")
def oidc_config():
    """Whether single sign-on is available, and the name to show on the button."""
    settings = get_settings()
    return {"enabled": oidc.enabled(settings), "name": settings.oidc_provider_name}


@router.get("/oidc/start")
def oidc_start():
    """Send the browser to the identity provider."""
    settings = get_settings()
    try:
        started = oidc.start(settings)
    except (oidc.OIDCError, httpx.HTTPError) as error:
        raise HTTPException(503, f"Single sign-on is unavailable: {error}") from error
    response = RedirectResponse(started.url, status_code=302)
    response.set_cookie(
        oidc.STATE_COOKIE,
        started.state,
        max_age=oidc.STATE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=settings.environment == "production",
        path="/api/auth/oidc",
    )
    return response


@router.get("/oidc/callback")
def oidc_callback(
    request: Request, db: Db, code: str | None = None, state: str | None = None, error: str | None = None
):
    """The identity provider returns here. The session token is passed back after "#", so it never
    reaches a server log, and the state cookie is cleared."""
    settings = get_settings()
    login = settings.oidc_frontend_url.rstrip("/") + "/login"
    try:
        if error or not code or not state:
            raise oidc.OIDCError("The identity provider did not complete the sign-in")
        token = oidc.finish(db, settings, code, state, request.cookies.get(oidc.STATE_COOKIE))
        target = f"{login}#{urlencode({'oidc_token': token})}"
    except (oidc.OIDCError, httpx.HTTPError) as problem:
        target = f"{login}#{urlencode({'oidc_error': str(problem)[:200]})}"
    response = RedirectResponse(target, status_code=302)
    response.delete_cookie(oidc.STATE_COOKIE, path="/api/auth/oidc")
    return response


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
