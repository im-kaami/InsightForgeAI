import asyncio

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from insightforge.api.deps import CurrentUser, Db
from insightforge.api.routers.datasets import owned
from insightforge.api.routers.schedules import validate_cron
from insightforge.api.schemas import FollowIn, FollowOut, MetricCheckOut
from insightforge.core.metrics import MetricError, SavedMetrics, find_metric
from insightforge.db.models import Dataset, MetricCheck, MetricFollow, Run
from insightforge.db.session import SessionLocal
from insightforge.services import metric_reports as service
from insightforge.services.datasets import ensure_current_version
from insightforge.services.metric_reports import FollowError
from insightforge.services.scheduler import follow_job_id, next_run, scheduler

router = APIRouter(tags=["metric follows"])


def _check_out(db: Db, check: MetricCheck, follow: MetricFollow | None = None) -> MetricCheckOut:
    follow = follow or db.get(MetricFollow, check.follow_id)
    run = db.get(Run, check.run_id) if check.run_id else None
    dataset = db.get(Dataset, follow.dataset_id) if follow else None
    defined = (
        find_metric(SavedMetrics.model_validate(dataset.metrics_json or {}).metrics, follow.metric)
        if (dataset and follow)
        else None
    )
    out = MetricCheckOut.model_validate(check)
    return out.model_copy(
        update={
            "metric": follow.metric if follow else None,
            "metric_label": defined.display if defined else (follow.metric if follow else None),
            "dataset_id": follow.dataset_id if follow else None,
            "session_id": run.session_id if run else None,
        }
    )


def _follow_out(db: Db, follow: MetricFollow) -> FollowOut:
    checks = service.list_checks(db, follow, limit=1)
    alerts = [item for item in service.open_alerts(db, follow.owner_id) if item.follow_id == follow.id]
    return FollowOut.model_validate(follow).model_copy(
        update={
            "last_check": _check_out(db, checks[0], follow) if checks else None,
            "open_alerts": len(alerts),
        }
    )


def _guard(error: FollowError) -> HTTPException:
    return HTTPException(error.status, str(error))


def _apply(db: Db, user: CurrentUser, follow: MetricFollow, body: FollowIn) -> None:
    dataset = owned(db, user, follow.dataset_id)
    version = ensure_current_version(db, dataset)
    if version is None or version.state != "ready":
        raise HTTPException(409, "Confirm the dataset version before following a metric")
    try:
        metric, _ = service.approved_metric(dataset, body.metric)
        service.check_request(dataset, version, metric, body.group_by)
    except FollowError as error:
        raise _guard(error) from error
    except MetricError as error:
        raise HTTPException(422, str(error)) from error
    cron = (body.cron or "").strip() or None
    if cron:
        validate_cron(cron, body.timezone)
    follow.metric = metric.name
    follow.group_by = body.group_by or None
    follow.days = body.days
    follow.threshold_percent = body.threshold_percent
    follow.cron = cron
    follow.timezone = body.timezone
    follow.on_new_data = body.on_new_data
    follow.enabled = body.enabled
    follow.next_run_at = next_run(cron, body.timezone) if cron and body.enabled else None


def _reschedule(follow: MetricFollow) -> None:
    if scheduler.scheduler.running:
        scheduler.reschedule_follow(follow)


@router.get("/datasets/{dataset_id}/follows", response_model=list[FollowOut])
def list_follows(dataset_id: str, db: Db, user: CurrentUser):
    dataset = owned(db, user, dataset_id)
    follows = db.scalars(
        select(MetricFollow)
        .where(MetricFollow.dataset_id == dataset.id, MetricFollow.owner_id == user.id)
        .order_by(MetricFollow.created_at)
    ).all()
    return [_follow_out(db, follow) for follow in follows]


@router.post("/datasets/{dataset_id}/follows", response_model=FollowOut, status_code=201)
def create_follow(dataset_id: str, body: FollowIn, db: Db, user: CurrentUser):
    dataset = owned(db, user, dataset_id)
    follow = MetricFollow(owner_id=user.id, dataset_id=dataset.id, metric=body.metric)
    _apply(db, user, follow, body)
    db.add(follow)
    db.commit()
    db.refresh(follow)
    _reschedule(follow)
    return _follow_out(db, follow)


@router.put("/follows/{follow_id}", response_model=FollowOut)
def update_follow(follow_id: str, body: FollowIn, db: Db, user: CurrentUser):
    try:
        follow = service.owned_follow(db, user.id, follow_id)
    except FollowError as error:
        raise _guard(error) from error
    _apply(db, user, follow, body)
    db.commit()
    db.refresh(follow)
    _reschedule(follow)
    return _follow_out(db, follow)


@router.delete("/follows/{follow_id}", status_code=204)
def delete_follow(follow_id: str, db: Db, user: CurrentUser):
    try:
        follow = service.owned_follow(db, user.id, follow_id)
    except FollowError as error:
        raise _guard(error) from error
    scheduler.remove(follow_job_id(follow.id))
    service.delete_follow(db, follow)


@router.post("/follows/{follow_id}/check", response_model=MetricCheckOut)
async def check_now(follow_id: str, db: Db, user: CurrentUser):
    """Run the followed metric's checked report now (code only; no AI) and record the result."""
    try:
        follow = service.owned_follow(db, user.id, follow_id)
    except FollowError as error:
        raise _guard(error) from error

    def run() -> str:
        session = SessionLocal()
        try:
            return service.run_check(session, follow.id, "manual").id
        finally:
            session.close()

    check_id = await asyncio.to_thread(run)
    db.expire_all()
    return _check_out(db, db.get(MetricCheck, check_id))


@router.get("/follows/{follow_id}/checks", response_model=list[MetricCheckOut])
def list_checks(follow_id: str, db: Db, user: CurrentUser):
    try:
        follow = service.owned_follow(db, user.id, follow_id)
    except FollowError as error:
        raise _guard(error) from error
    return [_check_out(db, check, follow) for check in service.list_checks(db, follow)]


@router.post("/follows/{follow_id}/checks/{check_id}/acknowledge", response_model=MetricCheckOut)
def acknowledge(follow_id: str, check_id: str, db: Db, user: CurrentUser):
    try:
        return _check_out(db, service.acknowledge(db, user.id, follow_id, check_id))
    except FollowError as error:
        raise _guard(error) from error


@router.get("/metric-alerts", response_model=list[MetricCheckOut])
def metric_alerts(db: Db, user: CurrentUser):
    return [_check_out(db, check) for check in service.open_alerts(db, user.id)]
