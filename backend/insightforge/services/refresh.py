"""Long-lived sign-in sessions: rotating refresh tokens kept in an HttpOnly cookie.

Only the SHA-256 of a secret is stored. Each use revokes the presented token and issues the next one in
the same family. Presenting a token that was already used revokes the whole family.
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import Response
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from insightforge.config import get_settings
from insightforge.db.models import RefreshToken, User, new_id

COOKIE = "if_refresh"
COOKIE_PATH = "/api/auth"
HEADER = "X-InsightForge-Refresh"
PREFIX = "ifs_"
KEEP_REVOKED_DAYS = 7
ROTATION_GRACE_SECONDS = 10


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _prune(db: Session, user_id: str, now: datetime) -> None:
    cutoff = now - timedelta(days=KEEP_REVOKED_DAYS)
    db.execute(
        delete(RefreshToken).where(
            RefreshToken.user_id == user_id,
            (RefreshToken.expires_at < now)
            | (RefreshToken.revoked_at.is_not(None) & (RefreshToken.revoked_at < cutoff)),
        ).execution_options(synchronize_session=False)
    )


def issue(db: Session, user: User, family_id: str | None = None) -> str:
    """Start (or continue) a family and return the secret for the cookie."""
    now = datetime.now(UTC)
    _prune(db, user.id, now)
    secret = PREFIX + secrets.token_urlsafe(32)
    db.add(
        RefreshToken(
            user_id=user.id,
            family_id=family_id or new_id(),
            token_hash=hash_secret(secret),
            token_version=user.token_version or 0,
            expires_at=now + timedelta(days=get_settings().refresh_token_days),
        )
    )
    db.commit()
    return secret


def revoke_family(db: Session, family_id: str) -> None:
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    db.commit()


def revoke_all(db: Session, user_id: str) -> None:
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    db.commit()


def revoke_secret(db: Session, secret: str | None) -> None:
    """Sign out one browser: revoke the family of the presented cookie, if it is a known token."""
    if not secret:
        return
    record = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_secret(secret)))
    if record is not None:
        revoke_family(db, record.family_id)


def rotate(db: Session, secret: str | None) -> tuple[User, str] | None:
    """Exchange a refresh secret for the next one, or None when it cannot be used."""
    if not secret:
        return None
    record = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_secret(secret)))
    if record is None:
        return None
    now = datetime.now(UTC)
    user = db.get(User, record.user_id)
    if _utc(record.expires_at) <= now or user is None or record.token_version != (user.token_version or 0):
        return None
    if record.revoked_at is not None:
        # Two tabs refreshing at the same moment both present the old cookie; the loser of that race
        # is given a sibling token instead of being treated as a thief.
        recent = now - _utc(record.revoked_at) < timedelta(seconds=ROTATION_GRACE_SECONDS)
        successor = db.get(RefreshToken, record.replaced_by) if record.replaced_by else None
        if recent and successor is not None and successor.revoked_at is None:
            return user, issue(db, user, record.family_id)
        revoke_family(db, record.family_id)
        return None
    claimed = db.execute(
        update(RefreshToken)
        .where(RefreshToken.id == record.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now, last_used_at=now)
    )
    if claimed.rowcount != 1:
        db.rollback()
        revoke_family(db, record.family_id)
        return None
    fresh = issue(db, user, record.family_id)
    successor = db.scalar(select(RefreshToken.id).where(RefreshToken.token_hash == hash_secret(fresh)))
    db.execute(update(RefreshToken).where(RefreshToken.id == record.id).values(replaced_by=successor))
    db.commit()
    return user, fresh


def set_cookie(response: Response, secret: str) -> None:
    settings = get_settings()
    response.set_cookie(
        COOKIE,
        secret,
        max_age=settings.refresh_token_days * 86400,
        httponly=True,
        samesite="strict",
        secure=settings.environment == "production",
        path=COOKIE_PATH,
    )


def clear_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(
        COOKIE,
        path=COOKIE_PATH,
        httponly=True,
        samesite="strict",
        secure=settings.environment == "production",
    )
