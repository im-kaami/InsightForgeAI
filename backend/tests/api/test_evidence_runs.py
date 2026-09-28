import json

from insightforge.core.llm import FakeLLMClient
from insightforge.db.models import Run
from insightforge.db.session import SessionLocal
from insightforge.services.runs import execute_run


async def _run(client, auth_headers, dataset, summary: str) -> dict:
    await client.patch(
        f"/api/datasets/{dataset['id']}/privacy",
        headers=auth_headers,
        json={"mode": "full", "acknowledged": True},
    )
    session = (
        await client.post(
            "/api/sessions", headers=auth_headers, json={"dataset_id": dataset["id"], "title": "Checks"}
        )
    ).json()
    user = (await client.get("/api/auth/me", headers=auth_headers)).json()
    table = dataset["tables"][0]
    plan = {
        "steps": [
            {
                "name": "headcount",
                "action": "sql",
                "query": f'SELECT department, COUNT(*) AS people FROM "{table}" GROUP BY 1 ORDER BY 1',
            },
            {"name": "summary", "action": "summary"},
        ]
    }
    llm = FakeLLMClient(
        lambda messages: json.dumps(plan) if "data-analysis planner" in messages[0]["content"] else summary
    )
    db = SessionLocal()
    try:
        run = Run(
            session_id=session["id"],
            owner_id=user["id"],
            goal="Headcount by department",
            status="pending",
            request_json={"kind": "exploratory", "privacy_mode": "full"},
        )
        db.add(run)
        db.commit()
        run_id = run.id
    finally:
        db.close()
    execute_run(run_id, llm=llm)
    return (await client.get(f"/api/runs/{run_id}", headers=auth_headers)).json()


async def test_summary_numbers_are_linked_to_evidence(client, auth_headers, hr_dataset):
    payload = await _run(
        client, auth_headers, hr_dataset, "Every department has 12 people, 60 in total."
    )
    assert payload["status"] == "completed", payload["error"]
    assert payload["verification_status"] == "exploratory"
    provenance = payload["provenance"]
    assert provenance["number_check"] == {"checked": 2, "matched": 2, "unmatched": []}
    assert [item["kind"] for item in provenance["evidence"]] == ["cell", "column_total"]
    assert provenance["checks"] == [
        {
            "code": "summary_numbers",
            "passed": True,
            "message": "2 of 2 numbers in the summary were found in the results",
        }
    ]
    assert provenance["assumptions"][0].startswith("headcount: reads hr; uses every row")


async def test_unmatched_summary_numbers_need_review(client, auth_headers, hr_dataset):
    payload = await _run(
        client, auth_headers, hr_dataset, "Engineering has 12 people and a budget of 4,500,000."
    )
    assert payload["verification_status"] == "needs_review"
    assert payload["warnings"] == [
        "1 number(s) in the summary were not found in the results: 4,500,000. "
        "Check them before relying on the summary."
    ]
    report = await client.get(
        f"/api/runs/{payload['id']}/report", params={"format": "md"}, headers=auth_headers
    )
    assert "## Assumptions" in report.text
    assert "N1: 12 (summary says 12) (headcount, row 1, column people)" in report.text
