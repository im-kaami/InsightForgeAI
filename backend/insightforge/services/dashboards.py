"""Dashboards: pinned results, approved metric reports and approved questions (Phase 5c).

Three kinds of tile:

- ``pinned``: a copy of a table or chart from a session run; it never changes, and shows its date and
  how it was produced (approved metric, approved query or AI-written SQL);
- ``metric``: an approved metric's checked report for the last N days with data; **Refresh** re-runs
  it through the same code as followed metrics (no AI);
- ``question``: an approved question; **Refresh** runs its approved SQL on the current version (no AI).

Each tile stores the result it shows (``snapshot_json``), so a dashboard keeps working when a session
is deleted, and refreshing one tile cannot break another.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from insightforge.config import get_settings
from insightforge.core.queries import SavedQueries
from insightforge.core.sql_guard import guard_sql
from insightforge.db.models import Artifact, ChatSession, Dashboard, DashboardItem, Dataset, Run, User
from insightforge.services.access import AccessError, accessible_dataset, dataset_access
from insightforge.services.metric_reports import FollowError, approved_metric, rolling_report

MAX_ITEMS = 30
MAX_ROWS = 200


class DashboardError(ValueError):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def owned_dashboard(db: Session, owner_id: str, dashboard_id: str) -> Dashboard:
    dashboard = db.scalar(
        select(Dashboard).where(Dashboard.id == dashboard_id, Dashboard.owner_id == owner_id)
    )
    if dashboard is None:
        raise DashboardError("Dashboard not found", status=404)
    return dashboard


def owned_item(db: Session, dashboard: Dashboard, item_id: str) -> DashboardItem:
    item = db.scalar(
        select(DashboardItem).where(DashboardItem.id == item_id, DashboardItem.dashboard_id == dashboard.id)
    )
    if item is None:
        raise DashboardError("Tile not found", status=404)
    return item


def items_of(db: Session, dashboard: Dashboard) -> list[DashboardItem]:
    return list(
        db.scalars(
            select(DashboardItem)
            .where(DashboardItem.dashboard_id == dashboard.id)
            .order_by(DashboardItem.position, DashboardItem.created_at)
        )
    )


def _owned_dataset(db: Session, owner_id: str, dataset_id: str) -> Dataset:
    """A dataset the dashboard owner can read (their own, or one shared with their workspace)."""
    user = db.get(User, owner_id)
    try:
        return accessible_dataset(db, user, dataset_id, "read")
    except AccessError as error:
        raise DashboardError(str(error), status=error.status) from error


def _trust(payload: dict[str, Any]) -> str:
    if payload.get("metric"):
        return "approved metric"
    if payload.get("approved_query"):
        return "approved query"
    return "AI-written SQL"


def _table_snapshot(payload: dict[str, Any], trust: str) -> dict[str, Any]:
    return {
        "type": "table",
        "trust": trust,
        "columns": payload.get("columns") or [],
        "rows": (payload.get("rows") or [])[:MAX_ROWS],
        "total_rows": payload.get("total_rows"),
        "sql": payload.get("sql"),
        "metric": payload.get("metric"),
        "approved_query": payload.get("approved_query"),
    }


def _snapshot(payload: dict[str, Any], run: Run) -> dict[str, Any]:
    kind = payload.get("type")
    if kind == "table":
        snapshot = _table_snapshot(payload, _trust(payload))
    elif kind == "plot":
        snapshot = {
            "type": "plot",
            "trust": "chart",
            "kind": payload.get("kind"),
            "title": payload.get("title"),
            "figure": payload.get("figure") or {},
            "note": payload.get("note"),
            "data_source": payload.get("data_source"),
        }
    else:
        raise DashboardError("Only result tables and charts can be pinned")
    return {
        **snapshot,
        "run_goal": run.goal,
        "run_created_at": run.created_at.isoformat() if run.created_at else None,
    }


def _next_position(db: Session, dashboard: Dashboard) -> int:
    count = db.scalar(
        select(func.count()).select_from(DashboardItem).where(DashboardItem.dashboard_id == dashboard.id)
    )
    if count >= MAX_ITEMS:
        raise DashboardError(f"A dashboard holds at most {MAX_ITEMS} tiles", status=409)
    highest = db.scalar(
        select(func.max(DashboardItem.position)).where(DashboardItem.dashboard_id == dashboard.id)
    )
    return (highest or 0) + 1


def _touch(dashboard: Dashboard) -> None:
    dashboard.updated_at = datetime.now(UTC)


def pin(
    db: Session, owner_id: str, dashboard: Dashboard, run_id: str, position: int, title: str | None
) -> DashboardItem:
    run = db.scalar(select(Run).where(Run.id == run_id, Run.owner_id == owner_id))
    if run is None:
        raise DashboardError("Run not found", status=404)
    artifact = db.scalar(select(Artifact).where(Artifact.run_id == run.id, Artifact.position == position))
    if artifact is None:
        raise DashboardError("That result was not found", status=404)
    session = db.get(ChatSession, run.session_id)
    payload = artifact.payload_json or {}
    snapshot = _snapshot(payload, run)
    item = DashboardItem(
        dashboard_id=dashboard.id,
        owner_id=owner_id,
        dataset_id=session.dataset_id if session else None,
        kind="pinned",
        title=(title or payload.get("title") or artifact.name or run.goal)[:300],
        position=_next_position(db, dashboard),
        snapshot_json=snapshot,
        source_run_id=run.id,
        version_id=run.dataset_version_id,
        refreshed_at=run.finished_at or run.created_at,
    )
    db.add(item)
    _touch(dashboard)
    db.commit()
    db.refresh(item)
    return item


def add_metric(
    db: Session,
    owner_id: str,
    dashboard: Dashboard,
    dataset_id: str,
    metric: str,
    days: int,
    group_by: str | None,
    title: str | None,
) -> DashboardItem:
    dataset = _owned_dataset(db, owner_id, dataset_id)
    try:
        found, _ = approved_metric(dataset, metric)
    except FollowError as error:
        raise DashboardError(str(error), status=error.status) from error
    group = f" by {group_by}" if group_by else ""
    item = DashboardItem(
        dashboard_id=dashboard.id,
        owner_id=owner_id,
        dataset_id=dataset.id,
        kind="metric",
        title=(title or f"{found.display}{group}, last {days} days")[:300],
        position=_next_position(db, dashboard),
        config_json={"metric": found.name, "days": days, "group_by": group_by},
    )
    db.add(item)
    db.flush()
    refresh(db, item)
    _touch(dashboard)
    db.commit()
    db.refresh(item)
    return item


def add_question(
    db: Session, owner_id: str, dashboard: Dashboard, dataset_id: str, query_id: str, title: str | None
) -> DashboardItem:
    dataset = _owned_dataset(db, owner_id, dataset_id)
    saved = SavedQueries.model_validate(dataset.queries_json or {})
    query = next((item for item in saved.queries if item.id == query_id and item.approved), None)
    if query is None:
        raise DashboardError("No approved question with that id", status=404)
    item = DashboardItem(
        dashboard_id=dashboard.id,
        owner_id=owner_id,
        dataset_id=dataset.id,
        kind="question",
        title=(title or query.question)[:300],
        position=_next_position(db, dashboard),
        config_json={"query_id": query.id, "question": query.question},
    )
    db.add(item)
    db.flush()
    refresh(db, item)
    _touch(dashboard)
    db.commit()
    db.refresh(item)
    return item


def _refresh_metric(db: Session, item: DashboardItem, dataset: Dataset) -> None:
    config = item.config_json or {}
    try:
        metric, period, run = rolling_report(
            db,
            item.owner_id,
            dataset,
            config["metric"],
            int(config.get("days") or 30),
            config.get("group_by"),
        )
    except FollowError as error:
        raise DashboardError(str(error)) from error
    if run.status != "completed":
        raise DashboardError(run.error or "The report was blocked")
    artifacts = list(
        db.scalars(select(Artifact).where(Artifact.run_id == run.id).order_by(Artifact.position))
    )
    table = next((value for value in artifacts if value.type == "table"), None)
    text = next((value for value in artifacts if value.type == "text"), None)
    if table is None:
        raise DashboardError("The report produced no table")
    item.snapshot_json = {
        **_table_snapshot(table.payload_json or {}, "approved metric"),
        "summary": (text.payload_json or {}).get("text") if text else None,
        "period": {"start": period.start_date.isoformat(), "end": period.end_date.isoformat()},
        "verification": run.verification_status,
        "warnings": run.warnings_json or [],
        "label": metric.display,
    }
    item.source_run_id = run.id
    item.version_id = run.dataset_version_id


def _refresh_question(db: Session, item: DashboardItem, dataset: Dataset) -> None:
    from insightforge.services.datasets import ensure_current_version, open_catalog

    if dataset.connection_id:
        raise DashboardError("Approved questions on live connections cannot be refreshed here yet")
    config = item.config_json or {}
    saved = SavedQueries.model_validate(dataset.queries_json or {})
    query = next(
        (value for value in saved.queries if value.id == config.get("query_id") and value.approved), None
    )
    if query is None:
        raise DashboardError("The approved question was removed or is no longer approved")
    version = ensure_current_version(db, dataset)
    if version is None or version.state != "ready":
        raise DashboardError("The dataset has no confirmed version")
    catalog = open_catalog(dataset, for_run=True, version_id=version.id)
    try:
        guarded = guard_sql(query.sql, MAX_ROWS)
        frame = catalog.query(guarded, timeout_seconds=get_settings().query_timeout_seconds)
    except Exception as error:  # noqa: BLE001 - shown on the tile
        raise DashboardError(f"The approved SQL could not run: {error}") from error
    finally:
        catalog.close()
    rows = json.loads(frame.to_json(orient="records", date_format="iso"))
    item.snapshot_json = {
        "type": "table",
        "trust": "approved query",
        "columns": [str(column) for column in frame.columns],
        "rows": rows,
        "total_rows": len(rows),
        "sql": guarded,
        "approved_query": {"id": query.id, "question": query.question, "revision": saved.revision},
    }
    item.version_id = version.id


def refresh(db: Session, item: DashboardItem) -> DashboardItem:
    """Re-run a metric or question tile. Problems are stored on the tile, not raised."""
    if item.kind == "pinned":
        return item
    dataset = db.get(Dataset, item.dataset_id) if item.dataset_id else None
    try:
        user = db.get(User, item.owner_id)
        if dataset is None or user is None or dataset_access(db, user, dataset) is None:
            raise DashboardError("The dataset was deleted or is no longer shared with you")
        (_refresh_metric if item.kind == "metric" else _refresh_question)(db, item, dataset)
        item.error = None
    except DashboardError as error:
        item.error = str(error)
    item.refreshed_at = datetime.now(UTC)
    db.commit()
    db.refresh(item)
    return item


def move(db: Session, dashboard: Dashboard, item: DashboardItem, direction: int) -> None:
    items = items_of(db, dashboard)
    index = next(position for position, value in enumerate(items) if value.id == item.id)
    other = index + direction
    if 0 <= other < len(items):
        items[index], items[other] = items[other], items[index]
        for position, value in enumerate(items, start=1):
            value.position = position
        _touch(dashboard)
        db.commit()


def delete_dashboard(db: Session, dashboard: Dashboard) -> None:
    db.execute(delete(DashboardItem).where(DashboardItem.dashboard_id == dashboard.id))
    db.delete(dashboard)
    db.commit()


def forget_dataset(db: Session, dataset_id: str) -> None:
    """Deleting a dataset deletes its tiles on every dashboard, pinned copies included."""
    db.execute(delete(DashboardItem).where(DashboardItem.dataset_id == dataset_id))
