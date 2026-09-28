import json

import duckdb
import pytest

from insightforge.core.llm import FakeLLMClient
from insightforge.db.models import Dataset, Run
from insightforge.db.session import SessionLocal
from insightforge.services.datasets import open_catalog
from insightforge.services.runs import execute_run


async def test_health_reports_no_local_model_by_default(client):
    response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["local_model"] is None


async def test_snapshot_catalogs_cannot_read_outside_files(hr_dataset, tmp_path):
    outside = tmp_path / "outside.csv"
    outside.write_text("secret\n1\n")
    db = SessionLocal()
    try:
        catalog = open_catalog(db.get(Dataset, hr_dataset["id"]))
    finally:
        db.close()
    try:
        table = catalog.table_names()[0]
        assert catalog.query(f'SELECT COUNT(*) AS n FROM "{table}"')["n"].iloc[0] == 60
        with pytest.raises(duckdb.Error, match="disabled"):
            catalog.query(f"SELECT * FROM read_csv('{outside.as_posix()}')")
    finally:
        catalog.close()


async def test_cut_off_results_are_flagged_end_to_end(client, auth_headers, app, tmp_path):
    rows = 10_050
    source = tmp_path / "orders.csv"
    source.write_text(
        "order_id,amount\n" + "".join(f"{index},{index % 7}\n" for index in range(rows)), encoding="utf-8"
    )
    upload = await client.post(
        "/api/datasets/upload",
        headers=auth_headers,
        files=[("files", ("orders.csv", source.read_bytes(), "text/csv"))],
    )
    assert upload.status_code == 201, upload.text
    dataset = upload.json()
    await client.patch(
        f"/api/datasets/{dataset['id']}/privacy",
        headers=auth_headers,
        json={"mode": "full", "acknowledged": True},
    )
    session = (
        await client.post(
            "/api/sessions", headers=auth_headers, json={"dataset_id": dataset["id"], "title": "Orders"}
        )
    ).json()
    user = (await client.get("/api/auth/me", headers=auth_headers)).json()
    table = dataset["tables"][0]
    plan = {
        "steps": [
            {"name": "orders", "action": "sql", "query": f'SELECT * FROM "{table}" ORDER BY order_id'},
            {
                "name": "amounts",
                "action": "plot",
                "kind": "histogram",
                "data_source": "orders",
                "x": "amount",
            },
            {"name": "summary", "action": "summary"},
        ]
    }
    cloud = FakeLLMClient(
        lambda messages: json.dumps(plan) if "data-analysis planner" in messages[0]["content"] else "Done"
    )
    db = SessionLocal()
    try:
        run = Run(
            session_id=session["id"],
            owner_id=user["id"],
            goal="Every order",
            status="pending",
            request_json={"kind": "exploratory", "privacy_mode": "full"},
        )
        db.add(run)
        db.commit()
        run_id = run.id
    finally:
        db.close()
    execute_run(run_id, llm=cloud)
    payload = (await client.get(f"/api/runs/{run_id}", headers=auth_headers)).json()
    assert payload["status"] == "completed", payload["error"]
    table_artifact, plot_artifact = payload["artifacts"][0], payload["artifacts"][1]
    assert (table_artifact["total_rows"], table_artifact["truncated"]) == (10_000, True)
    assert table_artifact["full_row_count"] == rows
    assert plot_artifact["note"] == f"Binned all {rows:,} rows into 40 ranges of amount."
    note = f"The query produced {rows:,} rows; only the first 10,000 were kept."
    assert any(note in warning for warning in payload["warnings"])
    assert "The query produced 10,050 rows" in str(cloud.calls[-1])
    report = await client.get(f"/api/runs/{run_id}/report", params={"format": "md"}, headers=auth_headers)
    assert note in report.text


async def test_local_mode_run_uses_the_local_model_and_records_it(
    client, auth_headers, hr_dataset, app
):
    session = (
        await client.post(
            "/api/sessions",
            headers=auth_headers,
            json={"dataset_id": hr_dataset["id"], "title": "Local"},
        )
    ).json()
    user = (await client.get("/api/auth/me", headers=auth_headers)).json()
    table = hr_dataset["tables"][0]

    def local_reply(messages):
        if "data-analysis planner" in messages[0]["content"]:
            query = f'SELECT department, COUNT(*) AS people FROM "{table}" GROUP BY 1'
            return json.dumps(
                {
                    "steps": [
                        {"name": "headcount", "action": "sql", "query": query},
                        {"name": "summary", "action": "summary"},
                    ]
                }
            )
        return json.dumps({"headline": "Local model summary", "findings": [], "actions": []})

    local = FakeLLMClient(local_reply)
    local.model = "qwen3:4b"
    db = SessionLocal()
    try:
        run = Run(
            session_id=session["id"],
            owner_id=user["id"],
            goal="Headcount by department",
            status="pending",
            request_json={"kind": "exploratory", "privacy_mode": "local"},
        )
        db.add(run)
        db.commit()
        run_id = run.id
    finally:
        db.close()
    execute_run(run_id, llm=app.state.llm, local_llm=local)
    db = SessionLocal()
    try:
        finished = db.get(Run, run_id)
        assert finished.status == "completed", finished.error
        assert finished.summary == "Local model summary"
        assert finished.provenance_json["model"] == "local: qwen3:4b"
        assert not finished.used_fallback_plan
    finally:
        db.close()
    assert local.calls
