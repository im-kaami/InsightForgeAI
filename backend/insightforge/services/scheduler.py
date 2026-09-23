from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session

from insightforge.db.models import Run, Schedule
from insightforge.db.session import SessionLocal, configure
from insightforge.services.runs import execute_run


def schedule_timezone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError as error:
        raise ValueError(f"Unknown timezone: {value}") from error


class SchedulerService:
    def __init__(self):
        self.scheduler = BackgroundScheduler(timezone="UTC")

    def start(self, app: Any = None) -> None:
        del app
        if not self.scheduler.running:
            self.scheduler.start()

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def sync_jobs(self, db: Session) -> None:
        for schedule in db.query(Schedule).filter(Schedule.enabled.is_(True)).all():
            self.add(schedule)

    def add(self, schedule: Schedule) -> None:
        trigger = CronTrigger.from_crontab(
            schedule.cron, timezone=schedule_timezone(schedule.timezone)
        )
        self.scheduler.add_job(
            self.run_schedule,
            trigger,
            args=[schedule.id],
            id=schedule.id,
            replace_existing=True,
        )
        next_fire = trigger.get_next_fire_time(None, datetime.now(UTC))
        schedule.next_run_at = next_fire.astimezone(UTC) if next_fire else None

    def remove(self, schedule_id: str) -> None:
        if self.scheduler.get_job(schedule_id):
            self.scheduler.remove_job(schedule_id)

    def reschedule(self, schedule: Schedule) -> None:
        self.remove(schedule.id)
        if schedule.enabled:
            self.add(schedule)

    def run_schedule(self, schedule_id: str) -> str | None:
        configure()
        db = SessionLocal()
        try:
            schedule = db.get(Schedule, schedule_id)
            if not schedule or not schedule.enabled:
                return None
            run = Run(
                session_id=schedule.session_id,
                owner_id=schedule.owner_id,
                goal=schedule.goal,
                status="pending",
            )
            db.add(run)
            db.flush()
            schedule.last_run_at = datetime.now(UTC)
            schedule.last_run_id = run.id
            db.commit()
            execute_run(run.id)
            trigger = CronTrigger.from_crontab(
                schedule.cron, timezone=schedule_timezone(schedule.timezone)
            )
            next_fire = trigger.get_next_fire_time(None, datetime.now(UTC))
            schedule.next_run_at = next_fire.astimezone(UTC) if next_fire else None
            db.commit()
            return run.id
        finally:
            db.close()


scheduler = SchedulerService()
