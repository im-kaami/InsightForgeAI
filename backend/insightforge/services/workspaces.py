"""Workspaces, members and invitations (Phase 5e).

An invitation is for one email address and carries a secret (``ifi_...``) shown once to the person
inviting; only its hash is stored. InsightForge does not send email, so the owner passes the link on.
The invited person must be signed in with that email address to accept it. A workspace always keeps
at least one owner. When someone leaves or is removed, the datasets and dashboards they shared with
the workspace stop being shared, so nobody keeps access to another person's data through it.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from insightforge.db.models import Dashboard, Dataset, User, Workspace, WorkspaceInvite, WorkspaceMember

INVITE_PREFIX = "ifi_"
INVITE_DAYS = 7
MAX_WORKSPACES = 20
MAX_MEMBERS = 100


class WorkspaceError(ValueError):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def membership(db: Session, user_id: str, workspace_id: str) -> WorkspaceMember:
    member = db.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.user_id == user_id
        )
    )
    if member is None:
        raise WorkspaceError("Workspace not found", status=404)
    return member


def require_owner(db: Session, user_id: str, workspace_id: str) -> WorkspaceMember:
    member = membership(db, user_id, workspace_id)
    if member.role != "owner":
        raise WorkspaceError("Only a workspace owner can do that", status=403)
    return member


def create(db: Session, user: User, name: str) -> Workspace:
    count = db.scalar(
        select(func.count()).select_from(WorkspaceMember).where(WorkspaceMember.user_id == user.id)
    )
    if count >= MAX_WORKSPACES:
        raise WorkspaceError(f"You can belong to at most {MAX_WORKSPACES} workspaces", status=409)
    workspace = Workspace(name=name.strip(), created_by=user.id)
    db.add(workspace)
    db.flush()
    db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner"))
    db.commit()
    db.refresh(workspace)
    return workspace


def invite(db: Session, user: User, workspace_id: str, email: str, role: str) -> tuple[WorkspaceInvite, str]:
    require_owner(db, user.id, workspace_id)
    email = email.strip().lower()
    if "@" not in email:
        raise WorkspaceError("Enter an email address")
    existing = db.scalar(select(User).where(User.email == email))
    if existing and db.scalar(
        select(WorkspaceMember.id).where(
            WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.user_id == existing.id
        )
    ):
        raise WorkspaceError("That person is already a member", status=409)
    members = db.scalar(
        select(func.count()).select_from(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace_id)
    )
    if members >= MAX_MEMBERS:
        raise WorkspaceError(f"A workspace holds at most {MAX_MEMBERS} members", status=409)
    secret = INVITE_PREFIX + secrets.token_urlsafe(32)
    record = WorkspaceInvite(
        workspace_id=workspace_id,
        email=email,
        role=role,
        token_hash=hashlib.sha256(secret.encode("utf-8")).hexdigest(),
        invited_by=user.id,
        expires_at=datetime.now(UTC) + timedelta(days=INVITE_DAYS),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record, secret


def accept(db: Session, user: User, secret: str) -> Workspace:
    record = db.scalar(
        select(WorkspaceInvite).where(
            WorkspaceInvite.token_hash == hashlib.sha256(secret.encode("utf-8")).hexdigest()
        )
    )
    unusable = (
        record is None
        or record.revoked_at is not None
        or record.accepted_at is not None
        or _utc(record.expires_at) <= datetime.now(UTC)
    )
    if unusable:
        raise WorkspaceError("This invitation does not exist, has expired or was already used", status=404)
    if record.email != user.email.lower():
        raise WorkspaceError(
            f"This invitation is for {record.email}; sign in with that address to accept it", status=403
        )
    if not db.scalar(
        select(WorkspaceMember.id).where(
            WorkspaceMember.workspace_id == record.workspace_id, WorkspaceMember.user_id == user.id
        )
    ):
        db.add(WorkspaceMember(workspace_id=record.workspace_id, user_id=user.id, role=record.role))
    record.accepted_at = datetime.now(UTC)
    db.commit()
    workspace = db.get(Workspace, record.workspace_id)
    assert workspace is not None
    return workspace


def _owners(db: Session, workspace_id: str) -> int:
    return db.scalar(
        select(func.count())
        .select_from(WorkspaceMember)
        .where(WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.role == "owner")
    )


def set_role(db: Session, user: User, workspace_id: str, member_id: str, role: str) -> None:
    require_owner(db, user.id, workspace_id)
    member = membership(db, member_id, workspace_id)
    if member.role == "owner" and role != "owner" and _owners(db, workspace_id) <= 1:
        raise WorkspaceError("A workspace needs at least one owner", status=409)
    member.role = role
    db.commit()


def _unshare(db: Session, user_id: str, workspace_id: str) -> None:
    for model in (Dataset, Dashboard):
        db.execute(
            update(model)
            .where(model.owner_id == user_id, model.workspace_id == workspace_id)
            .values(workspace_id=None)
        )


def remove(db: Session, user: User, workspace_id: str, member_id: str) -> None:
    """Owners remove anyone; everyone may leave. The last owner cannot leave or be removed."""
    if member_id == user.id:
        member = membership(db, user.id, workspace_id)
    else:
        require_owner(db, user.id, workspace_id)
        member = membership(db, member_id, workspace_id)
    if member.role == "owner" and _owners(db, workspace_id) <= 1:
        raise WorkspaceError(
            "A workspace needs at least one owner; make someone else owner first", status=409
        )
    _unshare(db, member_id, workspace_id)
    db.delete(member)
    db.commit()


def delete(db: Session, user: User, workspace_id: str) -> None:
    require_owner(db, user.id, workspace_id)
    for model in (Dataset, Dashboard):
        db.execute(update(model).where(model.workspace_id == workspace_id).values(workspace_id=None))
    for model in (WorkspaceInvite, WorkspaceMember):
        for item in db.scalars(select(model).where(model.workspace_id == workspace_id)):
            db.delete(item)
    workspace = db.get(Workspace, workspace_id)
    if workspace is not None:
        db.delete(workspace)
    db.commit()


def revoke_invite(db: Session, user: User, workspace_id: str, invite_id: str) -> None:
    require_owner(db, user.id, workspace_id)
    record = db.scalar(
        select(WorkspaceInvite).where(
            WorkspaceInvite.id == invite_id, WorkspaceInvite.workspace_id == workspace_id
        )
    )
    if record is None:
        raise WorkspaceError("Invitation not found", status=404)
    if record.revoked_at is None:
        record.revoked_at = datetime.now(UTC)
        db.commit()
