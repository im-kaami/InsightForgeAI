"""Who may do what with a dataset or dashboard (Phase 5e).

Everything still has one owner (the person who created it). An owner can share a dataset or a
dashboard with one workspace. Members of that workspace then get access by role:

- dataset ``read`` (viewer, editor, owner role): see it, preview it and ask questions;
- dataset ``edit`` (editor and owner roles): change notes, relationships, metrics, approved questions,
  cleaning recipes, validation rules and data versions;
- dataset ``own`` (the dataset's owner only): privacy mode, sharing and deletion;
- dashboards: members can view; only the dashboard's owner changes, refreshes, shares or deletes it.

Without access, a dataset or dashboard is reported as "not found" (404), so its existence is not
revealed; with too little access the answer is 403.
"""

from __future__ import annotations

from typing import Literal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from insightforge.db.models import Dashboard, Dataset, User, WorkspaceMember

Need = Literal["read", "edit", "own"]
Access = Literal["read", "edit", "own"]
ROLES = ("viewer", "editor", "owner")
_RANK = {"read": 1, "edit": 2, "own": 3}


class AccessError(ValueError):
    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


def workspace_role(db: Session, user_id: str, workspace_id: str | None) -> str | None:
    if not workspace_id:
        return None
    return db.scalar(
        select(WorkspaceMember.role).where(
            WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.user_id == user_id
        )
    )


def dataset_access(db: Session, user: User, dataset: Dataset) -> Access | None:
    if dataset.owner_id == user.id:
        return "own"
    role = workspace_role(db, user.id, dataset.workspace_id)
    if role is None:
        return None
    return "read" if role == "viewer" else "edit"


def accessible_dataset(db: Session, user: User, dataset_id: str, need: Need) -> Dataset:
    dataset = db.get(Dataset, dataset_id)
    access = dataset_access(db, user, dataset) if dataset else None
    if dataset is None or access is None:
        raise AccessError("Dataset not found", 404)
    if _RANK[access] < _RANK[need]:
        message = {
            "edit": "You can view this dataset; ask an editor or the owner to change it",
            "own": "Only the dataset's owner can do that",
        }[need]
        raise AccessError(message, 403)
    return dataset


def member_workspaces(db: Session, user_id: str):
    return select(WorkspaceMember.workspace_id).where(WorkspaceMember.user_id == user_id)


def visible_datasets(db: Session, user: User) -> list[Dataset]:
    return list(
        db.scalars(
            select(Dataset).where(
                or_(Dataset.owner_id == user.id, Dataset.workspace_id.in_(member_workspaces(db, user.id)))
            )
        )
    )


def dashboard_access(db: Session, user: User, dashboard: Dashboard) -> Access | None:
    if dashboard.owner_id == user.id:
        return "own"
    return "read" if workspace_role(db, user.id, dashboard.workspace_id) else None


def visible_dashboards(db: Session, user: User) -> list[Dashboard]:
    return list(
        db.scalars(
            select(Dashboard)
            .where(
                or_(
                    Dashboard.owner_id == user.id,
                    Dashboard.workspace_id.in_(member_workspaces(db, user.id)),
                )
            )
            .order_by(Dashboard.updated_at.desc())
        )
    )
