from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from insightforge.api.deps import CurrentUser, Db, SignedInUser
from insightforge.api.schemas import (
    InviteAccept,
    InviteCreated,
    InviteIn,
    InviteOut,
    MemberOut,
    RoleIn,
    WorkspaceIn,
    WorkspaceOut,
)
from insightforge.db.models import Dashboard, Dataset, User, Workspace, WorkspaceInvite, WorkspaceMember
from insightforge.services import workspaces as service
from insightforge.services.workspaces import WorkspaceError

router = APIRouter(tags=["workspaces"])


def _guard(error: WorkspaceError) -> HTTPException:
    return HTTPException(error.status, str(error))


def _out(db: Db, workspace: Workspace, member: WorkspaceMember) -> WorkspaceOut:
    members = db.execute(
        select(WorkspaceMember, User)
        .join(User, User.id == WorkspaceMember.user_id)
        .where(WorkspaceMember.workspace_id == workspace.id)
        .order_by(WorkspaceMember.created_at)
    ).all()
    invites = (
        db.scalars(
            select(WorkspaceInvite)
            .where(WorkspaceInvite.workspace_id == workspace.id)
            .order_by(WorkspaceInvite.created_at.desc())
        ).all()
        if member.role == "owner"
        else []
    )
    datasets = db.scalars(select(Dataset).where(Dataset.workspace_id == workspace.id)).all()
    dashboards = db.scalars(select(Dashboard).where(Dashboard.workspace_id == workspace.id)).all()
    return WorkspaceOut(
        id=workspace.id,
        name=workspace.name,
        role=member.role,
        created_at=workspace.created_at,
        members=[
            MemberOut(user_id=user.id, email=user.email, role=item.role, joined_at=item.created_at)
            for item, user in members
        ],
        invites=[InviteOut.model_validate(item) for item in invites],
        datasets=[{"id": item.id, "name": item.name} for item in datasets],
        dashboards=[{"id": item.id, "name": item.name} for item in dashboards],
    )


def _member(db: Db, user: User, workspace_id: str) -> tuple[Workspace, WorkspaceMember]:
    try:
        member = service.membership(db, user.id, workspace_id)
    except WorkspaceError as error:
        raise _guard(error) from error
    workspace = db.get(Workspace, workspace_id)
    assert workspace is not None
    return workspace, member


@router.get("/workspaces", response_model=list[WorkspaceOut])
def list_workspaces(db: Db, user: CurrentUser):
    rows = db.execute(
        select(Workspace, WorkspaceMember)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == user.id)
        .order_by(Workspace.created_at)
    ).all()
    return [_out(db, workspace, member) for workspace, member in rows]


@router.post("/workspaces", response_model=WorkspaceOut, status_code=201)
def create_workspace(body: WorkspaceIn, db: Db, user: SignedInUser):
    try:
        workspace = service.create(db, user, body.name)
    except WorkspaceError as error:
        raise _guard(error) from error
    return _out(db, *_member(db, user, workspace.id))


@router.get("/workspaces/{workspace_id}", response_model=WorkspaceOut)
def get_workspace(workspace_id: str, db: Db, user: CurrentUser):
    return _out(db, *_member(db, user, workspace_id))


@router.patch("/workspaces/{workspace_id}", response_model=WorkspaceOut)
def rename_workspace(workspace_id: str, body: WorkspaceIn, db: Db, user: SignedInUser):
    try:
        service.require_owner(db, user.id, workspace_id)
    except WorkspaceError as error:
        raise _guard(error) from error
    workspace, member = _member(db, user, workspace_id)
    workspace.name = body.name.strip()
    db.commit()
    return _out(db, workspace, member)


@router.delete("/workspaces/{workspace_id}", status_code=204)
def delete_workspace(workspace_id: str, db: Db, user: SignedInUser):
    try:
        service.delete(db, user, workspace_id)
    except WorkspaceError as error:
        raise _guard(error) from error


@router.post("/workspaces/{workspace_id}/invites", response_model=InviteCreated, status_code=201)
def create_invite(workspace_id: str, body: InviteIn, db: Db, user: SignedInUser):
    try:
        record, secret = service.invite(db, user, workspace_id, body.email, body.role)
    except WorkspaceError as error:
        raise _guard(error) from error
    return InviteCreated(**InviteOut.model_validate(record).model_dump(), secret=secret)


@router.delete("/workspaces/{workspace_id}/invites/{invite_id}", status_code=204)
def revoke_invite(workspace_id: str, invite_id: str, db: Db, user: SignedInUser):
    try:
        service.revoke_invite(db, user, workspace_id, invite_id)
    except WorkspaceError as error:
        raise _guard(error) from error


@router.post("/workspaces/invites/accept", response_model=WorkspaceOut)
def accept_invite(body: InviteAccept, db: Db, user: SignedInUser):
    """Join a workspace with an invitation link (the secret is posted, never put in a URL path)."""
    try:
        workspace = service.accept(db, user, body.secret)
    except WorkspaceError as error:
        raise _guard(error) from error
    return _out(db, *_member(db, user, workspace.id))


@router.patch("/workspaces/{workspace_id}/members/{member_id}", response_model=WorkspaceOut)
def change_role(workspace_id: str, member_id: str, body: RoleIn, db: Db, user: SignedInUser):
    try:
        service.set_role(db, user, workspace_id, member_id, body.role)
    except WorkspaceError as error:
        raise _guard(error) from error
    return _out(db, *_member(db, user, workspace_id))


@router.delete("/workspaces/{workspace_id}/members/{member_id}", status_code=204)
def remove_member(workspace_id: str, member_id: str, db: Db, user: SignedInUser):
    """Remove a member (owners), or leave (yourself). Their shared datasets stop being shared."""
    try:
        service.remove(db, user, workspace_id, member_id)
    except WorkspaceError as error:
        raise _guard(error) from error
