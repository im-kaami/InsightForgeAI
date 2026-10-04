import pandas as pd
import pytest

from insightforge.core.llm import FakeLLMClient
from insightforge.db.models import Run
from insightforge.db.session import SessionLocal
from insightforge.services.runs import execute_run

from ..test_metric_planning import DATA
from .test_metrics_api import REVENUE, _with_relationship

QUERY = {
    "id": "q-region",
    "question": "What is revenue by region?",
    "sql": (
        "SELECT region, SUM(amount) AS revenue FROM orders WHERE status = 'completed' "
        "GROUP BY 1 ORDER BY 1"
    ),
    "approved": True,
}


async def _setup(client, headers):
    dataset = await _with_relationship(client, headers)
    await client.put(f"/api/datasets/{dataset['id']}/metrics", headers=headers, json={"metrics": [REVENUE]})
    await client.put(f"/api/datasets/{dataset['id']}/queries", headers=headers, json={"queries": [QUERY]})
    board = await client.post("/api/dashboards", headers=headers, json={"name": "Weekly numbers"})
    assert board.status_code == 201, board.text
    return dataset, board.json()


async def _run(client, headers, dataset, goal):
    session = (
        await client.post("/api/sessions", headers=headers, json={"dataset_id": dataset["id"], "title": "x"})
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


def _revenue_by_region() -> dict[str, float]:
    orders = pd.read_csv(DATA / "orders.csv")
    return orders[orders["status"] == "completed"].groupby("region")["amount"].sum().to_dict()


async def test_tiles_show_pinned_results_metric_reports_and_approved_questions(client, auth_headers):
    dataset, board = await _setup(client, auth_headers)
    base = f"/api/dashboards/{board['id']}"
    run = await _run(client, auth_headers, dataset, "What are sales by region?")
    position = next(index for index, item in enumerate(run["artifacts"]) if item["type"] == "table")
    pinned = await client.post(
        f"{base}/items",
        headers=auth_headers,
        json={"kind": "pinned", "run_id": run["id"], "position": position},
    )
    assert pinned.status_code == 201, pinned.text
    assert pinned.json()["snapshot"]["trust"] == "approved metric"
    assert pinned.json()["snapshot"]["run_goal"] == "What are sales by region?"

    metric = await client.post(
        f"{base}/items",
        headers=auth_headers,
        json={"kind": "metric", "dataset_id": dataset["id"], "metric": "revenue", "days": 90},
    )
    assert metric.status_code == 201, metric.text
    tile = metric.json()
    assert tile["error"] is None and tile["title"] == "Revenue, last 90 days"
    assert tile["snapshot"]["rows"][0]["group"] == "Total" and tile["snapshot"]["period"]["end"]
    assert "checked report" in tile["snapshot"]["summary"]

    question = await client.post(
        f"{base}/items",
        headers=auth_headers,
        json={"kind": "question", "dataset_id": dataset["id"], "query_id": "q-region"},
    )
    assert question.status_code == 201, question.text
    rows = question.json()["snapshot"]["rows"]
    assert {row["region"]: row["revenue"] for row in rows} == pytest.approx(_revenue_by_region())
    assert question.json()["snapshot"]["trust"] == "approved query"

    full = (await client.get(base, headers=auth_headers)).json()
    assert [item["kind"] for item in full["items"]] == ["pinned", "metric", "question"]
    moved = (
        await client.post(f"{base}/items/{question.json()['id']}/move?direction=-1", headers=auth_headers)
    ).json()
    assert [item["kind"] for item in moved["items"]] == ["pinned", "question", "metric"]
    listed = (await client.get("/api/dashboards", headers=auth_headers)).json()
    assert listed[0]["item_count"] == 3 and listed[0]["items"] == []

    refreshed = (await client.post(f"{base}/refresh", headers=auth_headers)).json()
    assert all(item["error"] is None for item in refreshed["items"])
    assert refreshed["items"][2]["source_run_id"] != tile["source_run_id"]


async def test_removed_definitions_show_an_error_and_deleting_the_dataset_removes_tiles(client, auth_headers):
    dataset, board = await _setup(client, auth_headers)
    base = f"/api/dashboards/{board['id']}"
    question = (
        await client.post(
            f"{base}/items",
            headers=auth_headers,
            json={"kind": "question", "dataset_id": dataset["id"], "query_id": "q-region"},
        )
    ).json()
    await client.put(f"/api/datasets/{dataset['id']}/queries", headers=auth_headers, json={"queries": []})
    stale = (await client.post(f"{base}/items/{question['id']}/refresh", headers=auth_headers)).json()
    assert "removed or is no longer approved" in stale["error"]
    assert stale["snapshot"]["rows"]  # the last good result stays visible
    missing = await client.post(
        f"{base}/items",
        headers=auth_headers,
        json={"kind": "metric", "dataset_id": dataset["id"], "metric": "nope"},
    )
    assert missing.status_code == 404
    assert (await client.delete(f"/api/datasets/{dataset['id']}", headers=auth_headers)).status_code == 204
    assert (await client.get(base, headers=auth_headers)).json()["items"] == []


async def test_dashboards_are_owner_scoped_and_limited(client, auth_headers):
    dataset, board = await _setup(client, auth_headers)
    credentials = {"email": "dash-intruder@example.com", "password": "correct horse battery staple"}
    await client.post("/api/auth/register", json=credentials)
    token = (await client.post("/api/auth/login", json=credentials)).json()["access_token"]
    other = {"Authorization": f"Bearer {token}"}
    base = f"/api/dashboards/{board['id']}"
    assert (await client.get(base, headers=other)).status_code == 404
    assert (await client.get("/api/dashboards", headers=other)).json() == []
    own = (await client.post("/api/dashboards", headers=other, json={"name": "Mine"})).json()
    # Another user's dataset cannot be added to my dashboard.
    stolen = await client.post(
        f"/api/dashboards/{own['id']}/items",
        headers=other,
        json={"kind": "question", "dataset_id": dataset["id"], "query_id": "q-region"},
    )
    assert stolen.status_code == 404
    bad = await client.post(f"{base}/items", headers=auth_headers, json={"kind": "pinned"})
    assert bad.status_code == 422
    assert (await client.delete(base, headers=other)).status_code == 404
    assert (await client.delete(base, headers=auth_headers)).status_code == 204
