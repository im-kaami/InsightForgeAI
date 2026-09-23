from datetime import datetime


async def test_schedule_crud_and_run_now(client, auth_headers, hr_dataset):
    session = (
        await client.post(
            "/api/sessions",
            headers=auth_headers,
            json={"dataset_id": hr_dataset["id"]},
        )
    ).json()
    body = {
        "dataset_id": hr_dataset["id"],
        "session_id": session["id"],
        "goal": "daily review",
        "cron": "0 9 * * *",
        "timezone": "Asia/Karachi",
    }
    created = await client.post("/api/schedules", headers=auth_headers, json=body)
    assert created.status_code == 201, created.text
    assert created.json()["timezone"] == "Asia/Karachi"
    next_run = datetime.fromisoformat(created.json()["next_run_at"])
    assert next_run.hour == 4
    invalid_timezone = await client.post(
        "/api/schedules",
        headers=auth_headers,
        json={**body, "timezone": "Mars/Olympus"},
    )
    assert invalid_timezone.status_code == 422
    assert invalid_timezone.json()["detail"] == "Unknown timezone"
    invalid = await client.post(
        "/api/schedules", headers=auth_headers, json={**body, "cron": "invalid"}
    )
    assert invalid.status_code == 422
    schedule_id = created.json()["id"]
    run = await client.post(f"/api/schedules/{schedule_id}/run-now", headers=auth_headers)
    assert run.status_code == 200, run.text
    assert run.json()["status"] == "completed"
    listed = (await client.get("/api/schedules", headers=auth_headers)).json()
    assert listed[0]["last_run_id"] == run.json()["id"]
    assert (
        await client.delete(f"/api/schedules/{schedule_id}", headers=auth_headers)
    ).status_code == 204
