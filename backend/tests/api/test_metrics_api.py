from insightforge.core.llm import FakeLLMClient
from insightforge.db.models import Run
from insightforge.db.session import SessionLocal
from insightforge.services.runs import execute_run

from .test_table_relationships_api import _upload_shop

RELATIONSHIP = {
    "from_table": "orders",
    "from_column": "customer_id",
    "to_table": "customers",
    "to_column": "customer_id",
}
REVENUE = {
    "name": "revenue",
    "label": "Revenue",
    "unit": "GBP",
    "synonyms": ["sales"],
    "table": "orders",
    "aggregation": "sum",
    "column": "amount",
    "filters": [{"column": "status", "op": "equals", "value": "completed"}],
    "date_column": "order_date",
    "dimensions": ["region", "customers.segment"],
    "approved": True,
}


async def _with_relationship(client, headers):
    dataset = await _upload_shop(client, headers)
    saved = await client.put(
        f"/api/datasets/{dataset['id']}/relationships",
        headers=headers,
        json={"relationships": [RELATIONSHIP]},
    )
    assert saved.status_code == 200, saved.text
    return dataset


async def test_suggest_save_and_preview_metrics(client, auth_headers):
    dataset = await _with_relationship(client, auth_headers)
    base = f"/api/datasets/{dataset['id']}/metrics"
    suggestions = (await client.get(f"{base}/suggestions", headers=auth_headers)).json()
    assert "no AI model" in suggestions["method"]
    assert any(item["name"] == "orders_count" for item in suggestions["suggestions"])
    assert all(item["approved"] is False for item in suggestions["suggestions"])

    saved = await client.put(base, headers=auth_headers, json={"metrics": [REVENUE]})
    assert saved.status_code == 200, saved.text
    assert saved.json()["metrics"]["revision"] == 1
    assert saved.json()["metrics"]["metrics"][0]["name"] == "revenue"

    preview = await client.post(
        f"{base}/preview",
        headers=auth_headers,
        json={"metric": REVENUE, "query": {"metric": "revenue", "group_by": ["segment"]}},
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["columns"] == ["segment", "revenue"]
    assert body["total_rows"] == len(body["rows"]) >= 2
    assert "status equals completed" in body["description"]
    total = await client.post(f"{base}/preview", headers=auth_headers, json={"metric": REVENUE})
    assert total.json()["rows"][0]["revenue"] > 0


async def test_invalid_metrics_are_rejected(client, auth_headers):
    dataset = await _upload_shop(client, auth_headers)  # no relationship saved
    base = f"/api/datasets/{dataset['id']}/metrics"
    joined = await client.put(base, headers=auth_headers, json={"metrics": [REVENUE]})
    assert joined.status_code == 422
    assert "approved relationship" in joined.json()["detail"]
    text_sum = {**REVENUE, "column": "region", "dimensions": []}
    assert (await client.put(base, headers=auth_headers, json={"metrics": [text_sum]})).status_code == 422
    twice = {"metrics": [{**REVENUE, "dimensions": []}, {**REVENUE, "dimensions": []}]}
    assert (await client.put(base, headers=auth_headers, json=twice)).status_code == 422
    bad_group = await client.post(
        f"{base}/preview",
        headers=auth_headers,
        json={
            "metric": {**REVENUE, "dimensions": ["region"]},
            "query": {"metric": "x", "group_by": ["status"]},
        },
    )
    assert bad_group.status_code == 422


async def test_metrics_are_owner_scoped(client, auth_headers):
    dataset = await _upload_shop(client, auth_headers)
    credentials = {"email": "metrics-intruder@example.com", "password": "correct horse battery staple"}
    await client.post("/api/auth/register", json=credentials)
    token = (await client.post("/api/auth/login", json=credentials)).json()["access_token"]
    other = {"Authorization": f"Bearer {token}"}
    base = f"/api/datasets/{dataset['id']}/metrics"
    assert (await client.get(f"{base}/suggestions", headers=other)).status_code == 404
    assert (await client.put(base, headers=other, json={"metrics": []})).status_code == 404
    assert (
        await client.post(f"{base}/preview", headers=other, json={"metric": {**REVENUE, "dimensions": []}})
    ).status_code == 404


async def test_runs_answer_metric_questions_with_approved_metrics(client, auth_headers):
    dataset = await _with_relationship(client, auth_headers)
    await client.put(
        f"/api/datasets/{dataset['id']}/metrics", headers=auth_headers, json={"metrics": [REVENUE]}
    )
    session = (
        await client.post(
            "/api/sessions", headers=auth_headers, json={"dataset_id": dataset["id"], "title": "Metric"}
        )
    ).json()
    user = (await client.get("/api/auth/me", headers=auth_headers)).json()
    db = SessionLocal()
    try:
        run = Run(
            session_id=session["id"],
            owner_id=user["id"],
            goal="What are sales by region?",
            status="pending",
            request_json={"kind": "exploratory", "privacy_mode": "local"},
        )
        db.add(run)
        db.commit()
        run_id = run.id
    finally:
        db.close()
    execute_run(run_id, llm=FakeLLMClient(["unused"]))
    result = (await client.get(f"/api/runs/{run_id}", headers=auth_headers)).json()
    assert result["status"] == "completed", result["error"]
    table = next(item for item in result["artifacts"] if item["type"] == "table")
    assert table["metric"]["name"] == "revenue"
    assert table["metric"]["revision"] == 1
    assert table["columns"] == ["region", "revenue"]
    assert any("approved metric" in item for item in result["provenance"]["assumptions"])
