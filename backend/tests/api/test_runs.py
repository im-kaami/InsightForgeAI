import asyncio


async def _wait(client, headers, run_id):
    for _ in range(100):
        response = await client.get(f"/api/runs/{run_id}", headers=headers)
        if response.json()["status"] in {"completed", "failed"}:
            return response
        await asyncio.sleep(0.1)
    raise AssertionError("run did not finish")


async def test_runs_events_memory_and_reports(client, auth_headers, hr_dataset, app):
    privacy = await client.patch(
        f"/api/datasets/{hr_dataset['id']}/privacy",
        headers=auth_headers,
        json={"mode": "full", "acknowledged": True},
    )
    assert privacy.status_code == 200, privacy.text
    session = await client.post(
        "/api/sessions",
        headers=auth_headers,
        json={"dataset_id": hr_dataset["id"], "title": "HR"},
    )
    session_id = session.json()["id"]
    first = await client.post(
        f"/api/sessions/{session_id}/runs",
        headers=auth_headers,
        json={"goal": "profile departments"},
    )
    assert first.status_code == 202
    assert app.state.queue.threads
    completed = await _wait(client, auth_headers, first.json()["id"])
    payload = completed.json()
    assert payload["status"] == "completed", payload
    buffered_events = list(app.state.bus.buffers[first.json()["id"]])
    done_events = [event for event in buffered_events if event["type"] == "done"]
    assert len(done_events) == 1
    assert buffered_events[-1] == done_events[0]
    assert done_events[0]["run_id"] == payload["id"]
    assert {item["type"] for item in payload["artifacts"]} >= {"table", "plot", "text"}
    assert payload["summary"]
    table_position = next(
        index for index, artifact in enumerate(payload["artifacts"]) if artifact["type"] == "table"
    )
    csv_response = await client.get(
        f"/api/runs/{payload['id']}/artifacts/{table_position}/csv",
        headers=auth_headers,
    )
    assert csv_response.status_code == 200
    assert csv_response.headers["content-type"].startswith("text/csv")
    assert "row_count" in csv_response.text
    listed_sessions = await client.get("/api/sessions", headers=auth_headers)
    listed = next(item for item in listed_sessions.json() if item["id"] == session_id)
    assert listed["run_count"] == 1
    assert listed["last_activity_at"]
    session_runs = await client.get(f"/api/sessions/{session_id}/runs", headers=auth_headers)
    assert [item["id"] for item in session_runs.json()] == [payload["id"]]

    events = await client.get(f"/api/runs/{payload['id']}/events", headers=auth_headers)
    assert '"type": "done"' in events.text

    second = await client.post(
        f"/api/sessions/{session_id}/runs",
        headers=auth_headers,
        json={"goal": "follow up"},
    )
    second_done = await _wait(client, auth_headers, second.json()["id"])
    assert second_done.json()["status"] == "completed"
    planner_calls = [
        call
        for call in app.state.llm.calls
        if call and "data-analysis planner" in call[0]["content"]
    ]
    assert "profile departments" in planner_calls[-1][0]["content"]

    md = await client.get(
        f"/api/runs/{payload['id']}/report", params={"format": "md"}, headers=auth_headers
    )
    assert "profile departments" in md.text and "|" in md.text
    html = await client.get(
        f"/api/runs/{payload['id']}/report", params={"format": "html"}, headers=auth_headers
    )
    assert "<table>" in html.text
    pdf = await client.get(
        f"/api/runs/{payload['id']}/report", params={"format": "pdf"}, headers=auth_headers
    )
    assert pdf.status_code in {200, 501}


async def test_lifespan_fails_runs_that_used_all_their_attempts(app):
    from insightforge.db.models import Run
    from insightforge.db.session import SessionLocal

    db = SessionLocal()
    interrupted = Run(
        session_id="missing-session",
        owner_id="missing-owner",
        goal="interrupted",
        status="running",
        attempts=2,
    )
    db.add(interrupted)
    db.commit()
    run_id = interrupted.id
    db.close()

    app.state.queue.stop()
    async with app.router.lifespan_context(app):
        db = SessionLocal()
        try:
            restored = db.get(Run, run_id)
            assert restored.status == "failed"
            assert restored.error == "Interrupted by server restart (tried 2 times)"
        finally:
            db.close()
