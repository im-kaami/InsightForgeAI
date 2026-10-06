import threading
import time
from datetime import UTC, datetime, timedelta

import pytest

from insightforge.db.models import Run
from insightforge.db.session import SessionLocal
from insightforge.services import queue as queue_module
from insightforge.services.queue import RunQueue
from insightforge.services.scheduler import SchedulerService


@pytest.fixture
def queues():
    made = []

    def make(app, workers=2, max_attempts=2):
        item = RunQueue(app, workers, max_attempts)
        made.append(item)
        return item

    yield make
    for item in made:
        item.stop(timeout=10)


async def _session(client, headers, dataset):
    session = await client.post(
        "/api/sessions", headers=headers, json={"dataset_id": dataset["id"], "title": "Queue"}
    )
    user = (await client.get("/api/auth/me", headers=headers)).json()
    return session.json()["id"], user["id"]


def _insert(session_id, owner_id, status, attempts=0, age=0):
    db = SessionLocal()
    try:
        run = Run(
            session_id=session_id,
            owner_id=owner_id,
            goal="Headcount by department",
            status=status,
            attempts=attempts,
            request_json={"kind": "exploratory", "privacy_mode": "local"},
            created_at=datetime.now(UTC) - timedelta(seconds=age),
        )
        db.add(run)
        db.commit()
        return run.id
    finally:
        db.close()


def _state(run_id):
    db = SessionLocal()
    try:
        run = db.get(Run, run_id)
        return run.status, run.attempts, run.error
    finally:
        db.close()


def _wait_for(run_id, statuses, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _state(run_id)[0] in statuses:
            return
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} stayed {_state(run_id)[0]}")


async def test_recovery_requeues_interrupted_runs_and_fails_exhausted_ones(
    client, auth_headers, hr_dataset, app, queues
):
    session_id, owner_id = await _session(client, auth_headers, hr_dataset)
    app.state.queue.stop()
    pending = _insert(session_id, owner_id, "pending", age=30)
    retried = _insert(session_id, owner_id, "running", attempts=1, age=20)
    exhausted = _insert(session_id, owner_id, "running", attempts=2, age=10)
    queues(app, workers=2, max_attempts=2).start()
    _wait_for(pending, {"completed"})
    _wait_for(retried, {"completed"})
    _wait_for(exhausted, {"failed"})
    assert _state(pending)[:2] == ("completed", 1)
    assert _state(retried)[:2] == ("completed", 2)
    status, attempts, error = _state(exhausted)
    assert (status, attempts) == ("failed", 2)
    assert error == "Interrupted by server restart (tried 2 times)"


async def test_duplicate_enqueue_runs_once(client, auth_headers, hr_dataset, app, queues, monkeypatch):
    session_id, owner_id = await _session(client, auth_headers, hr_dataset)
    calls = []

    def fake_execute(run_id, *args):
        calls.append(run_id)
        db = SessionLocal()
        try:
            db.get(Run, run_id).status = "completed"
            db.commit()
        finally:
            db.close()

    monkeypatch.setattr(queue_module, "execute_run", fake_execute)
    run_id = _insert(session_id, owner_id, "pending")
    worker = queues(app, workers=2)
    worker.start()
    for _ in range(3):
        worker.enqueue(run_id)
    _wait_for(run_id, {"completed"})
    time.sleep(1.0)
    assert calls == [run_id]
    assert _state(run_id)[1] == 1


async def test_workers_cap_how_many_runs_execute_at_once(
    client, auth_headers, hr_dataset, app, queues, monkeypatch
):
    session_id, owner_id = await _session(client, auth_headers, hr_dataset)
    release = threading.Event()

    def blocking_execute(run_id, *args):
        release.wait(30)
        db = SessionLocal()
        try:
            db.get(Run, run_id).status = "completed"
            db.commit()
        finally:
            db.close()

    monkeypatch.setattr(queue_module, "execute_run", blocking_execute)
    first = _insert(session_id, owner_id, "pending", age=10)
    second = _insert(session_id, owner_id, "pending", age=5)
    worker = queues(app, workers=1)
    worker.start()
    try:
        _wait_for(first, {"running"})
        time.sleep(1.0)
        assert _state(first)[0] == "running"
        assert _state(second)[0] == "pending"
    finally:
        release.set()
    _wait_for(first, {"completed"})
    _wait_for(second, {"completed"})


async def test_worker_survives_an_exception(client, auth_headers, hr_dataset, app, queues, monkeypatch):
    session_id, owner_id = await _session(client, auth_headers, hr_dataset)

    def flaky_execute(run_id, *args):
        if run_id == broken:
            raise RuntimeError("boom")
        db = SessionLocal()
        try:
            db.get(Run, run_id).status = "completed"
            db.commit()
        finally:
            db.close()

    monkeypatch.setattr(queue_module, "execute_run", flaky_execute)
    broken = _insert(session_id, owner_id, "pending", age=10)
    healthy = _insert(session_id, owner_id, "pending", age=5)
    queues(app, workers=1).start()
    _wait_for(healthy, {"completed"})
    assert _state(broken)[0] == "running"


async def test_scheduled_runs_go_through_the_queue(client, auth_headers, hr_dataset, app):
    session_id, _ = await _session(client, auth_headers, hr_dataset)
    schedule = await client.post(
        "/api/schedules",
        headers=auth_headers,
        json={
            "dataset_id": hr_dataset["id"],
            "session_id": session_id,
            "goal": "Headcount by department",
            "cron": "0 8 * * 1",
            "timezone": "UTC",
        },
    )
    assert schedule.status_code in {200, 201}, schedule.text
    queued = []
    app.state.queue.enqueue = queued.append
    service = SchedulerService()
    service.app = app
    run_id = service.run_schedule(schedule.json()["id"])
    assert queued == [run_id]
    assert _state(run_id)[0] == "pending"
