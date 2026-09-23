from insightforge.db.models import Run
from insightforge.db.session import SessionLocal


async def test_create_run_rejects_when_user_limit_reached(client, auth_headers, hr_dataset):
    session = (
        await client.post(
            "/api/sessions",
            headers=auth_headers,
            json={"dataset_id": hr_dataset["id"], "title": "Limited"},
        )
    ).json()
    user = (await client.get("/api/auth/me", headers=auth_headers)).json()
    db = SessionLocal()
    try:
        for goal in ("active one", "active two"):
            db.add(
                Run(
                    session_id=session["id"],
                    owner_id=user["id"],
                    goal=goal,
                    status="running",
                )
            )
        db.commit()
    finally:
        db.close()
    response = await client.post(
        f"/api/sessions/{session['id']}/runs",
        headers=auth_headers,
        json={"goal": "one too many"},
    )
    assert response.status_code == 429
    assert response.json()["detail"] == "Too many analyses running; wait for one to finish"
