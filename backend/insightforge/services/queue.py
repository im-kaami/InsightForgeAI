import logging
import queue
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime

from fastapi import FastAPI
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from insightforge.db.models import Run
from insightforge.db.session import SessionLocal
from insightforge.services.runs import REPORT_KINDS, execute_run

logger = logging.getLogger("insightforge")


class RunQueue:
    """Runs wait in the database and are executed by a fixed pool of worker threads.

    Only ids passed to ``enqueue`` (or found as unfinished at ``start``) are processed; the table is
    never polled. A run left "running" by a restart goes back to "pending" until it has used all its
    attempts.
    """

    def __init__(
        self,
        app: FastAPI,
        workers: int,
        max_attempts: int,
        db_factory: Callable[[], Session] = SessionLocal,
    ):
        self.app = app
        self.workers = workers
        self.max_attempts = max_attempts
        self.db_factory = db_factory
        self.queue: queue.Queue[str] = queue.Queue()
        self.stopping = threading.Event()
        self.threads: list[threading.Thread] = []

    def start(self) -> None:
        self.stopping.clear()
        self._recover()
        for number in range(self.workers):
            thread = threading.Thread(target=self._work, name=f"run-worker-{number}", daemon=True)
            thread.start()
            self.threads.append(thread)

    def enqueue(self, run_id: str) -> None:
        self.queue.put(run_id)

    def stop(self, timeout: float = 30.0) -> None:
        self.stopping.set()
        deadline = time.monotonic() + timeout
        for thread in self.threads:
            thread.join(max(0.0, deadline - time.monotonic()))
        self.threads = [thread for thread in self.threads if thread.is_alive()]

    def _recover(self) -> None:
        db = self.db_factory()
        requeued = failed = 0
        try:
            for run in db.scalars(select(Run).where(Run.status == "running")).all():
                if (run.attempts or 0) < self.max_attempts:
                    run.status = "pending"
                    requeued += 1
                    continue
                message = f"Interrupted by server restart (tried {run.attempts} times)"
                run.status = "failed"
                run.error = message
                run.warnings_json = [message]
                run.verification_status = (
                    "blocked" if (run.request_json or {}).get("kind") in REPORT_KINDS else "needs_review"
                )
                run.finished_at = datetime.now(UTC)
                failed += 1
            db.commit()
            pending = db.scalars(
                select(Run.id).where(Run.status == "pending").order_by(Run.created_at)
            ).all()
        finally:
            db.close()
        for run_id in pending:
            self.enqueue(run_id)
        logger.info(
            "Run queue recovered: %d pending, %d requeued after restart, %d failed",
            len(pending),
            requeued,
            failed,
        )

    def _claim(self, run_id: str) -> bool:
        db = self.db_factory()
        try:
            result = db.execute(
                update(Run)
                .where(Run.id == run_id, Run.status == "pending")
                .values(status="running", attempts=Run.attempts + 1)
            )
            db.commit()
            return result.rowcount == 1
        finally:
            db.close()

    def _work(self) -> None:
        while not self.stopping.is_set():
            try:
                run_id = self.queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                if not self._claim(run_id):
                    continue
                state = self.app.state
                execute_run(run_id, self.db_factory, state.bus, state.llm, state.local_llm)
            except Exception:
                logger.exception("Run worker failed while processing run %s", run_id)
