from datetime import UTC, datetime

from apscheduler.triggers.cron import CronTrigger
from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select

from insightforge.api.deps import CurrentUser, Db
from insightforge.api.routers.sessions import run_output
from insightforge.api.schemas import ScheduleCreate, ScheduleOut, ScheduleUpdate
from insightforge.config import get_settings
from insightforge.db.models import ChatSession, Dataset, Run, Schedule, User
from insightforge.services.datasets import ensure_current_version
from insightforge.services.runs import execute_run
from insightforge.services.scheduler import schedule_timezone, scheduler

router = APIRouter(prefix="/schedules", tags=["schedules"])
MIN_SCHEDULE_SECONDS = 300


def validate_cron(value: str, timezone: str) -> CronTrigger:
    try:
        zone = schedule_timezone(timezone)
    except ValueError as error:
        raise HTTPException(422, "Unknown timezone") from error
    try:
        trigger = CronTrigger.from_crontab(value, timezone=zone)
    except ValueError as error:
        raise HTTPException(422, f"Invalid cron expression: {error}") from error
    first = trigger.get_next_fire_time(None, datetime.now(UTC))
    second = trigger.get_next_fire_time(first, first) if first else None
    if first and second and (second - first).total_seconds() < MIN_SCHEDULE_SECONDS:
        raise HTTPException(422, "Schedules can run at most once every 5 minutes")
    return trigger


def owned(db: Db, user: User, schedule_id: str) -> Schedule:
    schedule = db.scalar(
        select(Schedule).where(Schedule.id == schedule_id, Schedule.owner_id == user.id)
    )
    if not schedule:
        raise HTTPException(404, "Schedule not found")
    return schedule


@router.get("", response_model=list[ScheduleOut])
def list_schedules(db: Db, user: CurrentUser):
    return list(db.scalars(select(Schedule).where(Schedule.owner_id == user.id)))


@router.post("", response_model=ScheduleOut, status_code=201)
def create_schedule(body: ScheduleCreate, db: Db, user: CurrentUser):
    trigger = validate_cron(body.cron, body.timezone)
    dataset = db.scalar(
        select(Dataset).where(Dataset.id == body.dataset_id, Dataset.owner_id == user.id)
    )
    session = db.scalar(
        select(ChatSession).where(
            ChatSession.id == body.session_id,
            ChatSession.owner_id == user.id,
            ChatSession.dataset_id == body.dataset_id,
        )
    )
    if not dataset or not session:
        raise HTTPException(404, "Dataset or session not found")
    schedule = Schedule(
        owner_id=user.id,
        dataset_id=dataset.id,
        session_id=session.id,
        goal=body.goal,
        cron=body.cron,
        timezone=body.timezone,
        enabled=body.enabled,
        next_run_at=trigger.get_next_fire_time(None, datetime.now(UTC)).astimezone(UTC),
    )
    db.add(schedule)
    db.commit()
    db.refresh(schedule)
    if schedule.enabled and scheduler.scheduler.running:
        scheduler.add(schedule)
    return schedule


@router.patch("/{schedule_id}", response_model=ScheduleOut)
def patch_schedule(
    schedule_id: str,
    body: ScheduleUpdate,
    db: Db,
    user: CurrentUser,
):
    schedule = owned(db, user, schedule_id)
    values = body.model_dump(exclude_none=True)
    if "cron" in values or "timezone" in values:
        cron = values.get("cron", schedule.cron)
        timezone = values.get("timezone", schedule.timezone)
        trigger = validate_cron(cron, timezone)
        next_fire = trigger.get_next_fire_time(None, datetime.now(UTC))
        schedule.next_run_at = next_fire.astimezone(UTC) if next_fire else None
    for key, value in values.items():
        setattr(schedule, key, value)
    db.commit()
    if scheduler.scheduler.running:
        scheduler.reschedule(schedule)
    return schedule


@router.delete("/{schedule_id}", status_code=204)
def delete_schedule(schedule_id: str, db: Db, user: CurrentUser):
    schedule = owned(db, user, schedule_id)
    scheduler.remove(schedule.id)
    db.delete(schedule)
    db.commit()


@router.post("/{schedule_id}/run-now")
def run_now(schedule_id: str, db: Db, user: CurrentUser):
    schedule = owned(db, user, schedule_id)
    active = db.scalar(
        select(func.count())
        .select_from(Run)
        .where(Run.owner_id == user.id, Run.status.in_(("pending", "running")))
    )
    if active >= get_settings().max_concurrent_runs_per_user:
        raise HTTPException(429, "Too many analyses running; wait for one to finish")
    dataset = db.get(Dataset, schedule.dataset_id)
    version = ensure_current_version(db, dataset)
    run = Run(
        session_id=schedule.session_id,
        owner_id=user.id,
        goal=schedule.goal,
        status="pending",
        dataset_version_id=version.id if version else None,
        request_json={"kind": "exploratory", "privacy_mode": dataset.llm_policy},
    )
    db.add(run)
    db.flush()
    schedule.last_run_at = datetime.now(UTC)
    schedule.last_run_id = run.id
    db.commit()
    execute_run(run.id)
    db.refresh(run)
    db.refresh(schedule)
    return run_output(db, run)
