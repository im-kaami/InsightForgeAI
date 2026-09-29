import json

from insightforge.core.llm import FakeLLMClient
from insightforge.db.models import Run
from insightforge.db.session import SessionLocal
from insightforge.services.runs import execute_run


async def test_test_steps_are_stored_shown_and_exported(client, auth_headers, hr_dataset):
    await client.patch(
        f"/api/datasets/{hr_dataset['id']}/privacy",
        headers=auth_headers,
        json={"mode": "schema_only", "acknowledged": True},
    )
    session = (
        await client.post(
            "/api/sessions", headers=auth_headers, json={"dataset_id": hr_dataset["id"], "title": "Stats"}
        )
    ).json()
    user = (await client.get("/api/auth/me", headers=auth_headers)).json()
    table = hr_dataset["tables"][0]
    plan = {
        "steps": [
            {"name": "rows", "action": "sql", "query": f'SELECT department, salary FROM "{table}"'},
            {
                "name": "salary_by_department",
                "action": "test",
                "method": "compare_groups",
                "data_source": "rows",
                "x": "department",
                "y": "salary",
            },
            {"name": "summary", "action": "summary"},
        ]
    }
    prompts: list[str] = []

    def reply(messages):
        prompts.append(json.dumps(messages))
        return json.dumps(plan)

    db = SessionLocal()
    try:
        run = Run(
            session_id=session["id"],
            owner_id=user["id"],
            goal="Do salaries really differ by department?",
            status="pending",
            request_json={"kind": "exploratory", "privacy_mode": "schema_only"},
        )
        db.add(run)
        db.commit()
        run_id = run.id
    finally:
        db.close()
    execute_run(run_id, llm=FakeLLMClient(reply))

    payload = (await client.get(f"/api/runs/{run_id}", headers=auth_headers)).json()
    assert payload["status"] == "completed", payload["error"]
    stat = next(artifact for artifact in payload["artifacts"] if artifact["type"] == "stat")
    assert stat["test"] == "Kruskal-Wallis test"
    assert stat["trust"] == "tested method"
    assert stat["n"] == 60
    assert "Kruskal-Wallis test (tested method)" in payload["summary"]
    assert len(prompts) == 2 and "asks for a statistical test" in prompts[0]
    assert not any("106" in prompt or "Engineering" in prompt for prompt in prompts)

    report = await client.get(f"/api/runs/{run_id}/report?format=md", headers=auth_headers)
    assert "### salary_by_department: Kruskal-Wallis test (tested method)" in report.text
    assert "- p &lt; 0.001; n = 60; epsilon_squared = 0.361 (large)" in report.text
    html = await client.get(f"/api/runs/{run_id}/report?format=html", headers=auth_headers)
    assert "<h3>salary_by_department: Kruskal-Wallis test (tested method)</h3>" in html.text
