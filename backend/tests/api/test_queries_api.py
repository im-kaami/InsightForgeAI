from insightforge.core.llm import FakeLLMClient
from insightforge.db.models import Run
from insightforge.db.session import SessionLocal
from insightforge.services.runs import execute_run

from .test_table_relationships_api import _upload_shop

BY_REGION = {
    "question": "What is completed revenue by region?",
    "sql": "SELECT region, SUM(amount) AS revenue FROM orders WHERE status = 'completed' GROUP BY region",
    "approved": True,
}


async def _run(client, headers, dataset, goal):
    session = (
        await client.post("/api/sessions", headers=headers, json={"dataset_id": dataset["id"], "title": "Q"})
    ).json()
    user = (await client.get("/api/auth/me", headers=headers)).json()
    db = SessionLocal()
    try:
        run = Run(
            session_id=session["id"],
            owner_id=user["id"],
            goal=goal,
            status="pending",
            request_json={"kind": "exploratory", "privacy_mode": "local"},
        )
        db.add(run)
        db.commit()
        run_id = run.id
    finally:
        db.close()
    execute_run(run_id, llm=FakeLLMClient(["unused"]))
    return (await client.get(f"/api/runs/{run_id}", headers=headers)).json()


async def test_save_approved_queries_and_answer_with_them(client, auth_headers):
    dataset = await _upload_shop(client, auth_headers)
    base = f"/api/datasets/{dataset['id']}/queries"
    saved = await client.put(base, headers=auth_headers, json={"queries": [BY_REGION]})
    assert saved.status_code == 200, saved.text
    assert saved.json()["queries"]["revision"] == 1
    assert saved.json()["queries"]["approved_only"] is False

    result = await _run(client, auth_headers, dataset, "Completed revenue for each region")
    assert result["status"] == "completed", result["error"]
    table = next(item for item in result["artifacts"] if item["type"] == "table")
    assert table["approved_query"]["matched_by"] == "code"
    assert table["approved_query"]["revision"] == 1
    assert table["columns"] == ["region", "revenue"]


async def test_invalid_queries_are_rejected(client, auth_headers):
    dataset = await _upload_shop(client, auth_headers)
    base = f"/api/datasets/{dataset['id']}/queries"
    for sql in ("DELETE FROM orders", "SELECT missing_column FROM orders", "SELECT 1; SELECT 2"):
        response = await client.put(base, headers=auth_headers, json={"queries": [{**BY_REGION, "sql": sql}]})
        assert response.status_code == 422, sql
    twice = {"queries": [BY_REGION, BY_REGION]}
    assert (await client.put(base, headers=auth_headers, json=twice)).status_code == 422


async def test_approved_data_only_refuses_and_a_run_result_can_be_approved(client, auth_headers):
    dataset = await _upload_shop(client, auth_headers)
    base = f"/api/datasets/{dataset['id']}/queries"
    await client.put(base, headers=auth_headers, json={"queries": [], "approved_only": True})
    refused = await _run(client, auth_headers, dataset, "How many customers are there?")
    assert refused["status"] == "completed"
    assert refused["artifacts"] == []
    assert refused["provenance"]["refusal"] and refused["provenance"]["approved_only"] is True
    assert "Nothing has been approved yet" in refused["summary"]

    await client.put(base, headers=auth_headers, json={"queries": [], "approved_only": False})
    answered = await _run(client, auth_headers, dataset, "How many customers are there?")
    position = next(index for index, item in enumerate(answered["artifacts"]) if item["type"] == "table")
    body = {"run_id": answered["id"], "position": position}
    approved = await client.post(f"{base}/from-run", headers=auth_headers, json=body)
    assert approved.status_code == 200, approved.text
    saved = approved.json()["queries"]["queries"][0]
    assert saved["question"] == "How many customers are there?" and saved["approved"] is True
    assert saved["source_run_id"] == answered["id"]
    assert (await client.post(f"{base}/from-run", headers=auth_headers, json=body)).status_code == 409

    strict = {"queries": approved.json()["queries"]["queries"], "approved_only": True}
    await client.put(base, headers=auth_headers, json=strict)
    again = await _run(client, auth_headers, dataset, "How many customers are there?")
    table = next(item for item in again["artifacts"] if item["type"] == "table")
    assert table["approved_query"]["question"] == "How many customers are there?"


async def test_queries_are_owner_scoped(client, auth_headers):
    dataset = await _upload_shop(client, auth_headers)
    credentials = {"email": "queries-intruder@example.com", "password": "correct horse battery staple"}
    await client.post("/api/auth/register", json=credentials)
    token = (await client.post("/api/auth/login", json=credentials)).json()["access_token"]
    other = {"Authorization": f"Bearer {token}"}
    base = f"/api/datasets/{dataset['id']}/queries"
    assert (await client.put(base, headers=other, json={"queries": []})).status_code == 404
    body = {"run_id": "x", "position": 0}
    assert (await client.post(f"{base}/from-run", headers=other, json=body)).status_code == 404
    assert (await client.post(f"{base}/from-run", headers=auth_headers, json=body)).status_code == 404
