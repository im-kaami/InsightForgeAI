from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from insightforge.db.models import PasswordReset, User
from insightforge.services.auth import hash_password, hash_reset_secret, new_reset_secret


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def create_reset(db: Session, email: str, hours: int = 1) -> tuple[str, datetime] | None:
    """Make a single-use secret for the account, replacing its earlier unused ones."""
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None:
        return None
    secret, digest = new_reset_secret()
    expires = datetime.now(UTC) + timedelta(hours=hours)
    db.execute(
        delete(PasswordReset).where(PasswordReset.user_id == user.id, PasswordReset.used_at.is_(None))
    )
    db.add(PasswordReset(user_id=user.id, token_hash=digest, expires_at=expires))
    db.commit()
    return secret, expires


def redeem_reset(db: Session, secret: str, new_password: str) -> User | None:
    """Set the new password if the secret is valid, unused and unexpired; end all sessions."""
    record = db.scalar(
        select(PasswordReset).where(PasswordReset.token_hash == hash_reset_secret(secret.strip()))
    )
    now = datetime.now(UTC)
    if record is None or record.used_at is not None or _utc(record.expires_at) <= now:
        return None
    user = db.get(User, record.user_id)
    if user is None:
        return None
    claimed = db.execute(
        update(PasswordReset)
        .where(PasswordReset.id == record.id, PasswordReset.used_at.is_(None))
        .values(used_at=now)
    )
    if claimed.rowcount != 1:
        db.rollback()
        return None
    user.password_hash = hash_password(new_password)
    user.token_version = (user.token_version or 0) + 1
    db.commit()
    return user
