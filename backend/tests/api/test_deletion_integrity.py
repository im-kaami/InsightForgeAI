from insightforge.db.models import Run
from insightforge.db.session import SessionLocal


async def test_dataset_and_session_delete_reject_schedules_and_active_runs(
    client, auth_headers, hr_dataset
):
    session = (
        await client.post(
            "/api/sessions",
            headers=auth_headers,
            json={"dataset_id": hr_dataset["id"], "title": "Protected"},
        )
    ).json()
    schedule = (
        await client.post(
            "/api/schedules",
            headers=auth_headers,
            json={
                "dataset_id": hr_dataset["id"],
                "session_id": session["id"],
                "goal": "scheduled",
                "cron": "0 9 * * *",
            },
        )
    ).json()
    dataset_delete = await client.delete(
        f"/api/datasets/{hr_dataset['id']}", headers=auth_headers
    )
    session_delete = await client.delete(
        f"/api/sessions/{session['id']}", headers=auth_headers
    )
    assert dataset_delete.status_code == 409
    assert session_delete.status_code == 409
    await client.delete(f"/api/schedules/{schedule['id']}", headers=auth_headers)

    user = (await client.get("/api/auth/me", headers=auth_headers)).json()
    db = SessionLocal()
    try:
        db.add(
            Run(
                session_id=session["id"],
                owner_id=user["id"],
                goal="active",
                status="running",
            )
        )
        db.commit()
    finally:
        db.close()
    dataset_active = await client.delete(
        f"/api/datasets/{hr_dataset['id']}", headers=auth_headers
    )
    session_active = await client.delete(
        f"/api/sessions/{session['id']}", headers=auth_headers
    )
    assert dataset_active.status_code == 409
    assert session_active.status_code == 409
