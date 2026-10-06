from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session

from insightforge.db.models import Dataset, MetricFollow, ModelSchedule, Run, Schedule
from insightforge.db.session import SessionLocal, configure
from insightforge.services.datasets import ensure_current_version
from insightforge.services.runs import execute_run


def schedule_timezone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError as error:
        raise ValueError(f"Unknown timezone: {value}") from error


def model_job_id(schedule_id: str) -> str:
    """APScheduler job id for a model-scoring schedule (kept apart from analysis schedules)."""
    return f"model-{schedule_id}"


def follow_job_id(follow_id: str) -> str:
    """APScheduler job id for a followed metric's checks."""
    return f"follow-{follow_id}"


def next_run(cron: str, timezone: str) -> datetime | None:
    trigger = CronTrigger.from_crontab(cron, timezone=schedule_timezone(timezone))
    fire = trigger.get_next_fire_time(None, datetime.now(UTC))
    return fire.astimezone(UTC) if fire else None


class SchedulerService:
    def __init__(self):
        self.scheduler = BackgroundScheduler(timezone="UTC")
        self.app: Any = None

    def start(self, app: Any = None) -> None:
        self.app = app
        if not self.scheduler.running:
            self.scheduler.start()

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def sync_jobs(self, db: Session) -> None:
        for schedule in db.query(Schedule).filter(Schedule.enabled.is_(True)).all():
            self.add(schedule)
        for model_schedule in db.query(ModelSchedule).filter(ModelSchedule.enabled.is_(True)).all():
            self.add_model(model_schedule)
        follows = db.query(MetricFollow).filter(
            MetricFollow.enabled.is_(True), MetricFollow.cron.is_not(None)
        )
        for follow in follows.all():
            self.add_follow(follow)
        db.commit()

    def add_follow(self, follow: MetricFollow) -> None:
        trigger = CronTrigger.from_crontab(follow.cron, timezone=schedule_timezone(follow.timezone))
        self.scheduler.add_job(
            self.run_follow,
            trigger,
            args=[follow.id],
            id=follow_job_id(follow.id),
            replace_existing=True,
        )
        follow.next_run_at = next_run(follow.cron, follow.timezone)

    def reschedule_follow(self, follow: MetricFollow) -> None:
        self.remove(follow_job_id(follow.id))
        if follow.enabled and follow.cron:
            self.add_follow(follow)

    def run_follow(self, follow_id: str) -> str | None:
        """Job body: run the followed metric's checked report and record the check."""
        from insightforge.services.metric_reports import run_check

        configure()
        db = SessionLocal()
        try:
            follow = db.get(MetricFollow, follow_id)
            if not follow or not follow.enabled:
                return None
            check = run_check(db, follow_id, "scheduled")
            follow = db.get(MetricFollow, follow_id)
            if follow is not None and follow.cron:
                follow.next_run_at = next_run(follow.cron, follow.timezone)
                db.commit()
            return check.id
        finally:
            db.close()

    def add_model(self, schedule: ModelSchedule) -> None:
        trigger = CronTrigger.from_crontab(
            schedule.cron, timezone=schedule_timezone(schedule.timezone)
        )
        self.scheduler.add_job(
            self.run_model_schedule,
            trigger,
            args=[schedule.id],
            id=model_job_id(schedule.id),
            replace_existing=True,
        )
        schedule.next_run_at = next_run(schedule.cron, schedule.timezone)

    def reschedule_model(self, schedule: ModelSchedule) -> None:
        self.remove(model_job_id(schedule.id))
        if schedule.enabled:
            self.add_model(schedule)

    def run_model_schedule(self, schedule_id: str) -> str | None:
        """Job body: score the model on the current version and store the result."""
        from insightforge.services.models import run_scheduled_scoring

        configure()
        db = SessionLocal()
        try:
            schedule = db.get(ModelSchedule, schedule_id)
            if not schedule or not schedule.enabled:
                return None
            model_id = schedule.model_id
            scoring = run_scheduled_scoring(db, model_id)
            schedule = db.get(ModelSchedule, schedule_id)
            if schedule is not None:
                schedule.last_run_at = datetime.now(UTC)
                schedule.next_run_at = next_run(schedule.cron, schedule.timezone)
                db.commit()
            return scoring.id
        finally:
            db.close()

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
            dataset = db.get(Dataset, schedule.dataset_id)
            version = ensure_current_version(db, dataset)
            run = Run(
                session_id=schedule.session_id,
                owner_id=schedule.owner_id,
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
            if self.app is not None:
                self.app.state.queue.enqueue(run.id)
            else:
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
