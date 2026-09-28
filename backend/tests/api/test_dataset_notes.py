import json

from insightforge.core.llm import FakeLLMClient
from insightforge.db.models import Run
from insightforge.db.session import SessionLocal
from insightforge.services.runs import execute_run

NOTES = {
    "general": "Salary is annual base pay in USD.",
    "columns": {"hr.salary": {"description": "Base pay", "unit": "USD", "synonyms": ["pay"]}},
}


async def test_notes_are_saved_returned_and_validated(client, auth_headers, hr_dataset):
    saved = await client.put(f"/api/datasets/{hr_dataset['id']}/notes", headers=auth_headers, json=NOTES)
    assert saved.status_code == 200, saved.text
    fetched = (await client.get(f"/api/datasets/{hr_dataset['id']}", headers=auth_headers)).json()
    assert fetched["notes"] == {
        "general": "Salary is annual base pay in USD.",
        "columns": {"hr.salary": {"description": "Base pay", "unit": "USD", "synonyms": ["pay"]}},
    }
    too_long = await client.put(
        f"/api/datasets/{hr_dataset['id']}/notes", headers=auth_headers, json={"general": "x" * 4001}
    )
    assert too_long.status_code == 422


async def test_notes_are_owner_scoped(client, auth_headers, hr_dataset):
    credentials = {"email": "notes-intruder@example.com", "password": "correct horse battery staple"}
    await client.post("/api/auth/register", json=credentials)
    token = (await client.post("/api/auth/login", json=credentials)).json()["access_token"]
    response = await client.put(
        f"/api/datasets/{hr_dataset['id']}/notes",
        headers={"Authorization": f"Bearer {token}"},
        json={"general": "hijacked"},
    )
    assert response.status_code == 404


async def test_runs_use_notes_and_remember_earlier_findings(client, auth_headers, hr_dataset):
    await client.put(f"/api/datasets/{hr_dataset['id']}/notes", headers=auth_headers, json=NOTES)
    await client.patch(
        f"/api/datasets/{hr_dataset['id']}/privacy",
        headers=auth_headers,
        json={"mode": "full", "acknowledged": True},
    )
    session = (
        await client.post(
            "/api/sessions", headers=auth_headers, json={"dataset_id": hr_dataset["id"], "title": "Notes"}
        )
    ).json()
    user = (await client.get("/api/auth/me", headers=auth_headers)).json()
    table = hr_dataset["tables"][0]
    query = f'SELECT department, COUNT(*) AS people FROM "{table}" GROUP BY 1 ORDER BY 1'
    prompts: list[str] = []

    def reply(messages):
        system = messages[0]["content"]
        if "data-analysis planner" in system:
            prompts.append(system)
            return json.dumps({"steps": [{"name": "headcount", "action": "sql", "query": query}]})
        return "Each department has 12 people."

    llm = FakeLLMClient(reply)
    for goal in ("Headcount by department", "And by location?"):
        db = SessionLocal()
        try:
            run = Run(
                session_id=session["id"],
                owner_id=user["id"],
                goal=goal,
                status="pending",
                request_json={"kind": "exploratory", "privacy_mode": "full"},
            )
            db.add(run)
            db.commit()
            run_id = run.id
        finally:
            db.close()
        execute_run(run_id, llm=llm)
    first = (await client.get(f"/api/runs/{run_id}", headers=auth_headers)).json()
    assert first["status"] == "completed", first["error"]
    assert "hr.salary: Base pay; unit: USD; also called pay" in prompts[0]
    assert "Q: Headcount by department" in prompts[1]
    assert f"Queries: headcount: {query}" in prompts[1]
    assert "Key numbers: people for department=Engineering = 12 (headcount)" in prompts[1]
