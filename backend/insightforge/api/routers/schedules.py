from datetime import UTC, datetime

from apscheduler.triggers.cron import CronTrigger
from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from insightforge.api.deps import CurrentUser, Db
from insightforge.api.routers.sessions import run_output
from insightforge.api.schemas import ScheduleCreate, ScheduleOut, SchedulePatch
from insightforge.db.models import ChatSession, Dataset, Run, Schedule, User
from insightforge.services.runs import execute_run
from insightforge.services.scheduler import scheduler

router = APIRouter(prefix="/schedules", tags=["schedules"])


def validate_cron(value: str) -> CronTrigger:
    try:
        return CronTrigger.from_crontab(value, timezone="UTC")
    except ValueError as error:
        raise HTTPException(422, f"Invalid cron expression: {error}") from error


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
    trigger = validate_cron(body.cron)
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
        enabled=body.enabled,
        next_run_at=trigger.get_next_fire_time(None, datetime.now(UTC)),
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
    body: SchedulePatch,
    db: Db,
    user: CurrentUser,
):
    schedule = owned(db, user, schedule_id)
    values = body.model_dump(exclude_none=True)
    if "cron" in values:
        trigger = validate_cron(values["cron"])
        schedule.next_run_at = trigger.get_next_fire_time(None, datetime.now(UTC))
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
    run = Run(
        session_id=schedule.session_id,
        owner_id=user.id,
        goal=schedule.goal,
        status="pending",
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
