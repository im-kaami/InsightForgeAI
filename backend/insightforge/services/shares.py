"""Read-only share links for a dashboard or one answer (Phase 5d).

A link carries a random secret (``ifs_...``); only its SHA-256 hash is stored, so the link is shown
once. Links expire (1 to 90 days) and can be revoked. The public view contains what the owner sees as
the result, never more: dashboard tiles as stored, or a completed run's summary, tables, charts,
assumptions, checks and evidence. It never includes refresh, downloads, the run trace, prompts, the
plan, sources, file paths or the owner's account.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from insightforge.db.models import Artifact, Dashboard, DashboardItem, Run, ShareLink

SHARE_PREFIX = "ifs_"
MAX_ACTIVE_LINKS = 50
_ARTIFACT_KEYS = {
    "table": (
        "name",
        "type",
        "columns",
        "rows",
        "total_rows",
        "truncated",
        "full_row_count",
        "metric",
        "approved_query",
    ),
    "plot": ("name", "type", "kind", "title", "figure", "note"),
    "text": ("name", "type", "text"),
    "stat": None,  # statistical results are shown as stored, minus file paths
}
_PROVENANCE_KEYS = (
    "assumptions",
    "checks",
    "evidence",
    "number_check",
    "kind",
    "period",
    "metric_definitions",
)


class ShareError(ValueError):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def hash_share(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def target_title(db: Session, owner_id: str, kind: str, target_id: str) -> str:
    if kind == "dashboard":
        dashboard = db.scalar(
            select(Dashboard).where(Dashboard.id == target_id, Dashboard.owner_id == owner_id)
        )
        if dashboard is None:
            raise ShareError("Dashboard not found", status=404)
        return dashboard.name
    run = db.scalar(select(Run).where(Run.id == target_id, Run.owner_id == owner_id))
    if run is None:
        raise ShareError("Answer not found", status=404)
    if run.status != "completed":
        raise ShareError("Only completed answers can be shared", status=409)
    return run.goal.split("\n\nClarification:")[0][:200]


def create(db: Session, owner_id: str, kind: str, target_id: str, days: int) -> tuple[ShareLink, str]:
    title = target_title(db, owner_id, kind, target_id)
    now = datetime.now(UTC)
    active = [
        item
        for item in db.scalars(
            select(ShareLink).where(ShareLink.owner_id == owner_id, ShareLink.revoked_at.is_(None))
        )
        if _utc(item.expires_at) > now
    ]
    if len(active) >= MAX_ACTIVE_LINKS:
        raise ShareError(f"You already have {MAX_ACTIVE_LINKS} active links; revoke one first", status=409)
    secret = SHARE_PREFIX + secrets.token_urlsafe(32)
    link = ShareLink(
        owner_id=owner_id,
        kind=kind,
        target_id=target_id,
        title=title,
        token_hash=hash_share(secret),
        prefix=secret[: len(SHARE_PREFIX) + 6],
        expires_at=now + timedelta(days=days),
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    return link, secret


def resolve(db: Session, secret: str) -> ShareLink:
    """The active link for this secret; every other case looks the same to the visitor."""
    link = db.scalar(select(ShareLink).where(ShareLink.token_hash == hash_share(secret)))
    if link is None or link.revoked_at is not None or _utc(link.expires_at) <= datetime.now(UTC):
        raise ShareError("This link does not exist, has expired or was revoked", status=404)
    return link


def _artifact(payload: dict[str, Any]) -> dict[str, Any] | None:
    kind = payload.get("type")
    if kind not in _ARTIFACT_KEYS:
        return None
    keys = _ARTIFACT_KEYS[kind]
    if keys is None:
        return {key: value for key, value in payload.items() if not key.endswith("_path")}
    return {key: payload.get(key) for key in keys}


def run_view(db: Session, run: Run) -> dict[str, Any]:
    artifacts = db.scalars(select(Artifact).where(Artifact.run_id == run.id).order_by(Artifact.position))
    provenance = run.provenance_json or {}
    return {
        "type": "run",
        "goal": run.goal.split("\n\nClarification:")[0],
        "summary": run.summary,
        "verification_status": run.verification_status,
        "warnings": run.warnings_json or [],
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "artifacts": [view for item in artifacts if (view := _artifact(item.payload_json or {}))],
        "provenance": {key: provenance[key] for key in _PROVENANCE_KEYS if key in provenance},
    }


def dashboard_view(db: Session, dashboard: Dashboard) -> dict[str, Any]:
    items = db.scalars(
        select(DashboardItem)
        .where(DashboardItem.dashboard_id == dashboard.id)
        .order_by(DashboardItem.position, DashboardItem.created_at)
    )
    return {
        "type": "dashboard",
        "name": dashboard.name,
        "description": dashboard.description or "",
        "items": [
            {
                "id": item.id,
                "kind": item.kind,
                "title": item.title,
                "position": item.position,
                "snapshot": {key: value for key, value in (item.snapshot_json or {}).items() if key != "sql"},
                "refreshed_at": item.refreshed_at.isoformat() if item.refreshed_at else None,
                "error": item.error,
                "config": {},
                "dataset_id": None,
                "source_run_id": None,
                "version_id": None,
                "created_at": None,
            }
            for item in items
        ],
    }


def public_view(db: Session, link: ShareLink) -> dict[str, Any]:
    if link.kind == "dashboard":
        dashboard = db.scalar(
            select(Dashboard).where(Dashboard.id == link.target_id, Dashboard.owner_id == link.owner_id)
        )
        if dashboard is None:
            raise ShareError("This link does not exist, has expired or was revoked", status=404)
        content = dashboard_view(db, dashboard)
    else:
        run = db.scalar(select(Run).where(Run.id == link.target_id, Run.owner_id == link.owner_id))
        if run is None or run.status != "completed":
            raise ShareError("This link does not exist, has expired or was revoked", status=404)
        content = run_view(db, run)
    link.view_count = (link.view_count or 0) + 1
    link.last_viewed_at = datetime.now(UTC)
    db.commit()
    return {"title": link.title, "expires_at": _utc(link.expires_at).isoformat(), "content": content}
