"""Checked metric reports and followed metrics (Phases 4c and 4e).

``new_report_run`` creates the run for one checked report (the API route and followed-metric checks use
the same code). A follow re-runs that report for a rolling window that ends on the latest date in the
data, on a schedule, when a new data version is confirmed, or on request. Each check stores the change
and raises an in-app alert when the total moves by at least the follow's threshold, or when the check
fails. Code writes every message; no AI model is involved.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from insightforge.config import get_settings
from insightforge.core.metric_report import latest_date, rolling_period
from insightforge.core.metrics import (
    Metric,
    MetricError,
    MetricQuery,
    SavedMetrics,
    compile_query,
    find_metric,
)
from insightforge.core.relationships import SavedRelationships
from insightforge.core.schema import SchemaInfo
from insightforge.core.verified_report import ReportPeriod
from insightforge.db.models import (
    Artifact,
    ChatSession,
    Dataset,
    DatasetVersion,
    MetricCheck,
    MetricFollow,
    Run,
)
from insightforge.services.mailer import notify_user

MAX_CHECKS_KEPT = 100
log = logging.getLogger("insightforge")


class FollowError(ValueError):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def approved_metric(dataset: Dataset, name: str) -> tuple[Metric, int]:
    saved = SavedMetrics.model_validate(dataset.metrics_json or {})
    metric = find_metric([item for item in saved.metrics if item.approved], name)
    if metric is None:
        raise FollowError("No approved metric with that name", status=404)
    return metric, saved.revision


def check_request(dataset: Dataset, version: DatasetVersion, metric: Metric, group_by: str | None) -> None:
    """Raise MetricError when this metric cannot be reported per period (with this grouping)."""
    if not metric.date_column:
        raise MetricError(f"{metric.name} has no date column, so it cannot be reported per period")
    relationships = SavedRelationships.model_validate(dataset.relationships_json or {}).relationships
    query = MetricQuery(metric=metric.name, group_by=[group_by] if group_by else [])
    compile_query(metric, query, SchemaInfo.model_validate(version.schema_json), relationships)


def new_report_run(
    db: Session,
    owner_id: str,
    dataset: Dataset,
    version: DatasetVersion,
    metric: Metric,
    revision: int,
    group_by: str | None,
    period: ReportPeriod,
) -> Run:
    """Create (and commit) a pending checked-report run in the metric's report session."""
    relationships = SavedRelationships.model_validate(dataset.relationships_json or {}).relationships
    title = f"Checked report: {metric.display}"
    session = db.scalar(
        select(ChatSession).where(
            ChatSession.owner_id == owner_id,
            ChatSession.dataset_id == dataset.id,
            ChatSession.title == title,
        )
    )
    if session is None:
        session = ChatSession(owner_id=owner_id, dataset_id=dataset.id, title=title)
        db.add(session)
        db.flush()
    grouping = f" by {group_by}" if group_by else ""
    run = Run(
        session_id=session.id,
        owner_id=owner_id,
        goal=f"{metric.display}{grouping}: {period.start_date} to {period.end_date}",
        status="pending",
        dataset_version_id=version.id,
        request_json={
            "kind": "metric_report_v1",
            "metric": metric.model_dump(mode="json"),
            "metrics_revision": revision,
            "relationships": [item.model_dump(mode="json") for item in relationships],
            "group_by": group_by,
            "period": period.model_dump(mode="json"),
            "privacy_mode": "local",
        },
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _percent_text(value: float) -> str:
    return f"{abs(value):.2f}".rstrip("0").rstrip(".")


def _number_text(value: float | None, unit: str) -> str:
    if value is None:
        return "not available"
    text = f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"
    return f"{unit} {text}".strip()


def _record(db: Session, follow: MetricFollow, trigger: str, **values: Any) -> MetricCheck:
    check = MetricCheck(owner_id=follow.owner_id, follow_id=follow.id, trigger=trigger, **values)
    db.add(check)
    follow.last_checked_at = datetime.now(UTC)
    if values.get("run_id"):
        follow.last_run_id = values["run_id"]
    db.flush()
    stale = list(
        db.scalars(
            select(MetricCheck.id)
            .where(MetricCheck.follow_id == follow.id)
            .order_by(MetricCheck.created_at.desc(), MetricCheck.id.desc())
            .offset(MAX_CHECKS_KEPT)
        )
    )
    if stale:
        db.execute(delete(MetricCheck).where(MetricCheck.id.in_(stale)))
    db.commit()
    db.refresh(check)
    if check.alert:
        _email_alert(db, follow, check)
    return check


def _email_alert(db: Session, follow: MetricFollow, check: MetricCheck) -> None:
    """Email the owner (if they opted in) the name of the metric and a link, never any numbers."""
    try:
        dataset = db.get(Dataset, follow.dataset_id)
        if dataset is None:
            return
        defined = find_metric(SavedMetrics.model_validate(dataset.metrics_json or {}).metrics, follow.metric)
        display = defined.display if defined else follow.metric
        what = (
            "its check could not run"
            if check.status == "failed"
            else "moved past its alert threshold"
        )
        notify_user(
            db,
            follow.owner_id,
            f"InsightForge alert: {display} on {dataset.name}",
            f"Your followed metric {display} on the dataset {dataset.name}: {what}.\n\n"
            f"Open InsightForge for the details: {get_settings().app_base_url.rstrip('/')}"
            f"/datasets/{dataset.id}\n",
        )
    except Exception as error:  # noqa: BLE001 - a mail problem must not break the check
        log.warning("metric alert email skipped: %s", type(error).__name__)


def rolling_report(
    db: Session,
    owner_id: str,
    dataset: Dataset | None,
    metric_name: str,
    days: int,
    group_by: str | None,
    execute: Callable[[str], None] | None = None,
) -> tuple[Metric, ReportPeriod, Run]:
    """Run the checked report for the last ``days`` days with data, now, and return the finished run.

    Raises FollowError for anything that stops the report from starting (no confirmed version, a
    removed metric, no readable dates). A report that starts and is then blocked returns its run.
    """
    from insightforge.services.datasets import ensure_current_version, open_catalog
    from insightforge.services.runs import execute_run

    version = ensure_current_version(db, dataset) if dataset else None
    if dataset is None or version is None or version.state != "ready":
        raise FollowError("the dataset has no confirmed version")
    metric, revision = approved_metric(dataset, metric_name)
    try:
        check_request(dataset, version, metric, group_by)
    except MetricError as error:
        raise FollowError(str(error)) from error
    catalog = open_catalog(dataset, for_run=True, version_id=version.id)
    try:
        end = latest_date(catalog, metric, SchemaInfo.model_validate(version.schema_json))
    finally:
        catalog.close()
    if end is None:
        raise FollowError(f"{metric.date_column} has no readable dates")
    period = rolling_period(end, days)
    run_id = new_report_run(db, owner_id, dataset, version, metric, revision, group_by, period).id
    (execute or execute_run)(run_id)
    db.expire_all()
    run = db.get(Run, run_id)
    assert run is not None
    return metric, period, run


def run_check(
    db: Session,
    follow_id: str,
    trigger: str,
    execute: Callable[[str], None] | None = None,
) -> MetricCheck:
    """Run the follow's checked report now and record the outcome. Never raises for data problems."""
    from insightforge.services.datasets import ensure_current_version

    follow = db.get(MetricFollow, follow_id)
    if follow is None:
        raise FollowError("Follow not found", status=404)
    dataset = db.get(Dataset, follow.dataset_id)
    version = ensure_current_version(db, dataset) if dataset else None

    def failed(message: str, run_id: str | None = None) -> MetricCheck:
        return _record(
            db,
            follow,
            trigger,
            run_id=run_id,
            version_id=version.id if version else None,
            status="failed",
            message=message,
            alert=True,
        )

    try:
        metric, period, run = rolling_report(
            db, follow.owner_id, dataset, follow.metric, follow.days, follow.group_by, execute
        )
    except FollowError as error:
        return failed(f"The check could not run: {error}")
    except Exception as error:  # noqa: BLE001 - recorded as a failed check with an alert
        log.warning("metric follow %s failed: %s", follow.id, error)
        return failed(f"The check could not run: {error}")
    run_id = run.id
    window = f"the {follow.days} days to {period.end_date.isoformat()}"
    if run is None or run.status != "completed":
        reason = (run.error if run else None) or "the report did not finish"
        return failed(f"{metric.display} check for {window} was blocked: {reason}", run_id)
    table = db.scalar(
        select(Artifact)
        .where(Artifact.run_id == run_id, Artifact.type == "table")
        .order_by(Artifact.position)
    )
    total = ((table.payload_json or {}).get("rows") or [{}])[0] if table else {}
    current, previous, change = total.get("current"), total.get("previous"), total.get("change_percent")
    numbers = f"{_number_text(current, metric.unit)} against {_number_text(previous, metric.unit)}"
    if change is None:
        message = f"{metric.display} for {window}: {numbers}; the change is unavailable."
        alert = False
    else:
        direction = "rose" if change > 0 else "fell" if change < 0 else "did not change"
        moved = f" {_percent_text(change)}%" if change else ""
        alert = abs(change) >= follow.threshold_percent
        limit = "at or above" if alert else "below"
        message = (
            f"{metric.display} {direction}{moved} in {window} ({numbers} in the previous "
            f"{follow.days} days); {limit} the {_percent_text(follow.threshold_percent)}% alert threshold."
        )
    if run.verification_status == "needs_review":
        message += " The report needs review; see its notes."
    return _record(
        db,
        follow,
        trigger,
        run_id=run_id,
        version_id=version.id,
        status="completed",
        current=current,
        previous=previous,
        change_percent=change,
        message=message,
        alert=alert,
    )


def _in_background(target: Callable[..., Any], *args: Any) -> None:
    threading.Thread(target=target, args=args, daemon=True).start()


start_background: Callable[..., None] = _in_background


def _check_new_data(dataset_id: str) -> None:
    from insightforge.db.session import SessionLocal, configure

    configure()
    db = SessionLocal()
    try:
        follows = db.scalars(
            select(MetricFollow.id).where(
                MetricFollow.dataset_id == dataset_id,
                MetricFollow.enabled.is_(True),
                MetricFollow.on_new_data.is_(True),
            )
        ).all()
        for follow_id in follows:
            try:
                run_check(db, follow_id, "new_data")
            except Exception as error:  # noqa: BLE001 - one follow must not stop the others
                db.rollback()
                log.warning("metric follow %s failed on new data: %s", follow_id, error)
    finally:
        db.close()


def on_new_version(db: Session, dataset_id: str) -> None:
    """Called after a version becomes current: re-check follows that watch for new data."""
    watched = db.scalar(
        select(MetricFollow.id).where(
            MetricFollow.dataset_id == dataset_id,
            MetricFollow.enabled.is_(True),
            MetricFollow.on_new_data.is_(True),
        )
    )
    if watched:
        start_background(_check_new_data, dataset_id)


def owned_follow(db: Session, owner_id: str, follow_id: str) -> MetricFollow:
    follow = db.scalar(
        select(MetricFollow).where(MetricFollow.id == follow_id, MetricFollow.owner_id == owner_id)
    )
    if follow is None:
        raise FollowError("Follow not found", status=404)
    return follow


def list_checks(db: Session, follow: MetricFollow, limit: int = 20) -> list[MetricCheck]:
    return list(
        db.scalars(
            select(MetricCheck)
            .where(MetricCheck.follow_id == follow.id)
            .order_by(MetricCheck.created_at.desc(), MetricCheck.id.desc())
            .limit(limit)
        )
    )


def open_alerts(db: Session, owner_id: str) -> list[MetricCheck]:
    return list(
        db.scalars(
            select(MetricCheck)
            .where(
                MetricCheck.owner_id == owner_id,
                MetricCheck.alert.is_(True),
                MetricCheck.acknowledged_at.is_(None),
            )
            .order_by(MetricCheck.created_at.desc())
        )
    )


def acknowledge(db: Session, owner_id: str, follow_id: str, check_id: str) -> MetricCheck:
    follow = owned_follow(db, owner_id, follow_id)
    check = db.scalar(
        select(MetricCheck).where(MetricCheck.id == check_id, MetricCheck.follow_id == follow.id)
    )
    if check is None:
        raise FollowError("Check not found", status=404)
    if check.acknowledged_at is None:
        check.acknowledged_at = datetime.now(UTC)
        db.commit()
        db.refresh(check)
    return check


def delete_follow(db: Session, follow: MetricFollow) -> None:
    db.execute(delete(MetricCheck).where(MetricCheck.follow_id == follow.id))
    db.delete(follow)
    db.commit()


def delete_follows_for_dataset(db: Session, dataset_id: str) -> list[str]:
    """Remove every member's follows of a dataset and their checks; returns the removed ids."""
    ids = list(db.scalars(select(MetricFollow.id).where(MetricFollow.dataset_id == dataset_id)))
    if ids:
        db.execute(delete(MetricCheck).where(MetricCheck.follow_id.in_(ids)))
        db.execute(delete(MetricFollow).where(MetricFollow.id.in_(ids)))
    return ids
