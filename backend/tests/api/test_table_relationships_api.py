import json
from pathlib import Path

from insightforge.core.llm import FakeLLMClient
from insightforge.db.models import Run
from insightforge.db.session import SessionLocal
from insightforge.services.runs import execute_run

SHOP = Path(__file__).parents[2] / "evals" / "data"


async def _upload_shop(client, headers):
    response = await client.post(
        "/api/datasets/upload",
        headers=headers,
        files=[
            ("files", ("orders.csv", (SHOP / "orders.csv").read_bytes(), "text/csv")),
            ("files", ("customers.csv", (SHOP / "customers.csv").read_bytes(), "text/csv")),
        ],
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_suggest_then_save_relationships(client, auth_headers):
    dataset = await _upload_shop(client, auth_headers)
    assert sorted(dataset["tables"]) == ["customers", "orders"]
    found = await client.get(
        f"/api/datasets/{dataset['id']}/relationships/suggestions", headers=auth_headers
    )
    assert found.status_code == 200, found.text
    body = found.json()
    assert "no AI model" in body["method"]
    [item] = body["suggestions"]
    rel = item["relationship"]
    assert (rel["from_table"], rel["from_column"], rel["to_table"], rel["to_column"]) == (
        "orders",
        "customer_id",
        "customers",
        "customer_id",
    )
    assert rel["kind"] == "many_to_one"
    assert item["match_share"] == 1.0 and item["checked_rows"] == 400

    saved = await client.put(
        f"/api/datasets/{dataset['id']}/relationships",
        headers=auth_headers,
        json={"relationships": [rel]},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["relationships"]["revision"] == 1
    assert saved.json()["relationships"]["relationships"][0]["to_table"] == "customers"
    # Once approved, it is no longer suggested.
    again = await client.get(
        f"/api/datasets/{dataset['id']}/relationships/suggestions", headers=auth_headers
    )
    assert again.json()["suggestions"] == []


async def test_unknown_columns_and_same_table_are_rejected(client, auth_headers):
    dataset = await _upload_shop(client, auth_headers)
    url = f"/api/datasets/{dataset['id']}/relationships"
    unknown = await client.put(
        url,
        headers=auth_headers,
        json={
            "relationships": [
                {
                    "from_table": "orders",
                    "from_column": "client",
                    "to_table": "customers",
                    "to_column": "customer_id",
                }
            ]
        },
    )
    assert unknown.status_code == 422
    assert "orders.client" in unknown.json()["detail"]
    same = await client.put(
        url,
        headers=auth_headers,
        json={
            "relationships": [
                {
                    "from_table": "orders",
                    "from_column": "order_id",
                    "to_table": "orders",
                    "to_column": "order_id",
                }
            ]
        },
    )
    assert same.status_code == 422


async def test_single_table_has_no_suggestions(client, auth_headers, hr_dataset):
    found = await client.get(
        f"/api/datasets/{hr_dataset['id']}/relationships/suggestions", headers=auth_headers
    )
    assert found.status_code == 200
    assert found.json()["suggestions"] == []


async def test_relationships_are_owner_scoped(client, auth_headers):
    dataset = await _upload_shop(client, auth_headers)
    credentials = {"email": "joins-intruder@example.com", "password": "correct horse battery staple"}
    await client.post("/api/auth/register", json=credentials)
    token = (await client.post("/api/auth/login", json=credentials)).json()["access_token"]
    other = {"Authorization": f"Bearer {token}"}
    assert (
        await client.get(f"/api/datasets/{dataset['id']}/relationships/suggestions", headers=other)
    ).status_code == 404
    assert (
        await client.put(
            f"/api/datasets/{dataset['id']}/relationships", headers=other, json={"relationships": []}
        )
    ).status_code == 404


async def test_runs_give_approved_relationships_to_the_planner(client, auth_headers):
    dataset = await _upload_shop(client, auth_headers)
    rel = {
        "from_table": "orders",
        "from_column": "customer_id",
        "to_table": "customers",
        "to_column": "customer_id",
    }
    await client.put(
        f"/api/datasets/{dataset['id']}/relationships", headers=auth_headers, json={"relationships": [rel]}
    )
    session = (
        await client.post(
            "/api/sessions", headers=auth_headers, json={"dataset_id": dataset["id"], "title": "Joins"}
        )
    ).json()
    user = (await client.get("/api/auth/me", headers=auth_headers)).json()
    query = (
        'SELECT c.segment, SUM(o.amount) AS revenue FROM "orders" o '
        'JOIN "customers" c ON o.customer_id = c.customer_id GROUP BY 1 ORDER BY 1'
    )
    prompts: list[str] = []

    def reply(messages):
        system = messages[0]["content"]
        if "data-analysis planner" in system:
            prompts.append(system)
            return json.dumps({"steps": [{"name": "by_segment", "action": "sql", "query": query}]})
        return "Revenue by segment."

    db = SessionLocal()
    try:
        run = Run(
            session_id=session["id"],
            owner_id=user["id"],
            goal="Revenue by customer segment",
            status="pending",
            request_json={"kind": "exploratory", "privacy_mode": "schema_only"},
        )
        db.add(run)
        db.commit()
        run_id = run.id
    finally:
        db.close()
    execute_run(run_id, llm=FakeLLMClient(reply))
    result = (await client.get(f"/api/runs/{run_id}", headers=auth_headers)).json()
    assert result["status"] == "completed", result["error"]
    assert "Table relationships approved by the data owner" in prompts[0]
    assert "orders.customer_id -> customers.customer_id" in prompts[0]
