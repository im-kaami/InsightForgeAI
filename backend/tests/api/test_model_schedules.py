import json
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import select

from insightforge.core.llm import FakeLLMClient
from insightforge.db.models import ModelSchedule, ModelScoring, Run
from insightforge.db.session import SessionLocal
from insightforge.services.runs import execute_run
from insightforge.services.scheduler import scheduler

FIXTURES = Path(__file__).parents[1] / "fixtures"


async def _saved_model(client, headers):
    """Upload churn.csv, run a prediction with the fake planner and save the model."""
    upload = await client.post(
        "/api/datasets/upload",
        headers=headers,
        files=[("files", ("churn.csv", (FIXTURES / "churn.csv").read_bytes(), "text/csv"))],
    )
    assert upload.status_code == 201, upload.text
    dataset = upload.json()
    session = (
        await client.post("/api/sessions", headers=headers, json={"dataset_id": dataset["id"]})
    ).json()
    user = (await client.get("/api/auth/me", headers=headers)).json()
    plan = {
        "steps": [
            {
                "name": "rows",
                "action": "sql",
                "query": f'SELECT tenure, monthly, plan, churned FROM "{dataset["tables"][0]}"',
            },
            {
                "name": "churn_model",
                "action": "test",
                "method": "predict",
                "data_source": "rows",
                "y": "churned",
                "x": "",
                "features": ["tenure", "monthly", "plan"],
            },
            {"name": "summary", "action": "summary"},
        ]
    }
    db = SessionLocal()
    try:
        run = Run(
            session_id=session["id"],
            owner_id=user["id"],
            goal="Predict churn",
            status="pending",
            request_json={"kind": "exploratory", "privacy_mode": "schema_only"},
        )
        db.add(run)
        db.commit()
        run_id = run.id
    finally:
        db.close()
    execute_run(run_id, llm=FakeLLMClient(lambda _messages: json.dumps(plan)))
    artifacts = (await client.get(f"/api/runs/{run_id}", headers=headers)).json()["artifacts"]
    position = next(
        index
        for index, item in enumerate(artifacts)
        if item["type"] == "stat" and item.get("method") == "predict"
    )
    saved = await client.post(
        f"/api/runs/{run_id}/artifacts/{position}/model", headers=headers, json={"name": "churn"}
    )
    assert saved.status_code == 201, saved.text
    return dataset, saved.json()


async def _replace_with_shifted_data(client, headers, dataset):
    rng = np.random.default_rng(7)
    n = 600
    frame = pd.DataFrame(
        {
            "customer_id": range(n),
            "tenure": rng.integers(1, 60, n),
            "monthly": rng.normal(400, 20, n).round(2),
            "plan": rng.choice(["basic", "plus", "pro"], n),
            "churned": rng.choice(["yes", "no"], n),
        }
    )
    replacement = await client.post(
        f"/api/datasets/{dataset['id']}/versions",
        headers=headers,
        files=[("files", ("churn.csv", frame.to_csv(index=False).encode(), "text/csv"))],
    )
    assert replacement.status_code == 201, replacement.text
    confirm = await client.post(
        f"/api/datasets/{dataset['id']}/versions/{replacement.json()['id']}/confirm",
        headers=headers,
        json={"confirmed": True, "expected_current_version_id": dataset["current_version_id"]},
    )
    assert confirm.status_code == 200, confirm.text


async def test_schedule_is_validated_saved_and_run_without_alert(client, auth_headers):
    _, model = await _saved_model(client, auth_headers)
    url = f"/api/models/{model['id']}/schedule"
    bad_cron = await client.put(url, headers=auth_headers, json={"cron": "nope"})
    assert bad_cron.status_code == 422
    bad_zone = await client.put(
        url, headers=auth_headers, json={"cron": "0 6 * * 1", "timezone": "Mars/Olympus"}
    )
    assert bad_zone.status_code == 422
    saved = await client.put(
        url, headers=auth_headers, json={"cron": "0 6 * * 1", "timezone": "Asia/Karachi"}
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["next_run_at"] is not None
    fetched = (await client.get(f"/api/models/{model['id']}", headers=auth_headers)).json()
    assert fetched["schedule"]["cron"] == "0 6 * * 1"
    assert fetched["open_alerts"] == 0

    manual = await client.post(f"/api/models/{model['id']}/score", headers=auth_headers, json={})
    assert manual.status_code == 200
    assert manual.json()["scoring_id"]
    checked = await client.post(f"/api/models/{model['id']}/schedule/run-now", headers=auth_headers)
    assert checked.status_code == 200, checked.text
    body = checked.json()
    assert body["trigger"] == "scheduled" and body["status"] == "completed"
    assert body["verdict"] == "no retraining signal"
    assert body["alert"] is False and body["rows_scored"] == 600
    history = (await client.get(f"/api/models/{model['id']}/scorings", headers=auth_headers)).json()
    assert [item["trigger"] for item in history] == ["scheduled", "manual"]
    # Manual scorings never raise alerts, even when they recommend retraining.
    assert all(item["alert"] is False for item in history)

    disabled = await client.put(
        url, headers=auth_headers, json={"cron": "0 6 * * 1", "enabled": False}
    )
    assert disabled.json()["enabled"] is False and disabled.json()["next_run_at"] is None
    assert (await client.delete(url, headers=auth_headers)).status_code == 204
    assert (await client.delete(url, headers=auth_headers)).status_code == 404


async def test_drift_raises_an_alert_that_can_be_acknowledged(client, auth_headers):
    dataset, model = await _saved_model(client, auth_headers)
    await client.put(
        f"/api/models/{model['id']}/schedule", headers=auth_headers, json={"cron": "0 6 * * *"}
    )
    await _replace_with_shifted_data(client, auth_headers, dataset)
    checked = (
        await client.post(f"/api/models/{model['id']}/schedule/run-now", headers=auth_headers)
    ).json()
    assert checked["verdict"] == "retrain recommended"
    assert checked["alert"] is True and checked["max_psi"] > 0.25
    assert any("monthly" in reason for reason in checked["reasons"])
    assert (await client.get(f"/api/models/{model['id']}", headers=auth_headers)).json()[
        "open_alerts"
    ] == 1
    alerts = (await client.get("/api/model-alerts", headers=auth_headers)).json()
    assert [(item["id"], item["model_name"], item["dataset_id"]) for item in alerts] == [
        (checked["id"], "churn", dataset["id"])
    ]
    ack = await client.post(
        f"/api/models/{model['id']}/scorings/{checked['id']}/acknowledge", headers=auth_headers
    )
    assert ack.status_code == 200 and ack.json()["acknowledged_at"]
    assert (await client.get("/api/model-alerts", headers=auth_headers)).json() == []


async def test_a_failing_check_is_recorded_as_an_alert(client, auth_headers):
    dataset, model = await _saved_model(client, auth_headers)
    await client.put(
        f"/api/models/{model['id']}/schedule", headers=auth_headers, json={"cron": "0 6 * * *"}
    )
    rules = await client.put(
        f"/api/datasets/{dataset['id']}/rules",
        headers=auth_headers,
        json={
            "rules": [
                {
                    "kind": "row_count",
                    "table": dataset["tables"][0],
                    "severity": "blocking",
                    "min": 10_000_000,
                }
            ]
        },
    )
    assert rules.status_code == 200, rules.text
    checked = (
        await client.post(f"/api/models/{model['id']}/schedule/run-now", headers=auth_headers)
    ).json()
    assert checked["status"] == "failed" and checked["alert"] is True
    assert "blocking" in checked["error"].lower()
    assert checked["verdict"] is None


async def test_the_scheduler_job_scores_and_records_the_run(client, auth_headers):
    _, model = await _saved_model(client, auth_headers)
    schedule = (
        await client.put(
            f"/api/models/{model['id']}/schedule", headers=auth_headers, json={"cron": "*/5 * * * *"}
        )
    ).json()
    scoring_id = scheduler.run_model_schedule(schedule["id"])
    assert scoring_id is not None
    db = SessionLocal()
    try:
        stored = db.get(ModelSchedule, schedule["id"])
        assert stored.last_run_at is not None and stored.next_run_at is not None
        assert db.get(ModelScoring, scoring_id).trigger == "scheduled"
    finally:
        db.close()
    await client.put(
        f"/api/models/{model['id']}/schedule",
        headers=auth_headers,
        json={"cron": "*/5 * * * *", "enabled": False},
    )
    assert scheduler.run_model_schedule(schedule["id"]) is None  # disabled schedules do nothing


async def test_model_schedules_are_owner_scoped(client, auth_headers):
    _, model = await _saved_model(client, auth_headers)
    await client.put(
        f"/api/models/{model['id']}/schedule", headers=auth_headers, json={"cron": "0 6 * * *"}
    )
    checked = (
        await client.post(f"/api/models/{model['id']}/schedule/run-now", headers=auth_headers)
    ).json()
    credentials = {"email": "sched-intruder@example.com", "password": "correct horse battery staple"}
    await client.post("/api/auth/register", json=credentials)
    token = (await client.post("/api/auth/login", json=credentials)).json()["access_token"]
    other = {"Authorization": f"Bearer {token}"}
    base = f"/api/models/{model['id']}"
    put = await client.put(f"{base}/schedule", headers=other, json={"cron": "0 6 * * *"})
    assert put.status_code == 404
    assert (await client.delete(f"{base}/schedule", headers=other)).status_code == 404
    assert (await client.post(f"{base}/schedule/run-now", headers=other)).status_code == 404
    assert (await client.get(f"{base}/scorings", headers=other)).status_code == 404
    assert (
        await client.post(f"{base}/scorings/{checked['id']}/acknowledge", headers=other)
    ).status_code == 404
    assert (await client.get("/api/model-alerts", headers=other)).json() == []


async def test_deleting_models_and_datasets_removes_schedules_and_history(client, auth_headers):
    _, model = await _saved_model(client, auth_headers)
    await client.put(
        f"/api/models/{model['id']}/schedule", headers=auth_headers, json={"cron": "0 6 * * *"}
    )
    await client.post(f"/api/models/{model['id']}/schedule/run-now", headers=auth_headers)
    deleted = await client.delete(f"/api/models/{model['id']}", headers=auth_headers)
    assert deleted.status_code == 204

    _, model2 = await _saved_model(client, auth_headers)
    await client.put(
        f"/api/models/{model2['id']}/schedule", headers=auth_headers, json={"cron": "0 6 * * *"}
    )
    await client.post(f"/api/models/{model2['id']}/schedule/run-now", headers=auth_headers)
    removed = await client.delete(f"/api/datasets/{model2['dataset_id']}", headers=auth_headers)
    assert removed.status_code == 204

    db = SessionLocal()
    try:
        for model_id in (model["id"], model2["id"]):
            assert db.scalar(select(ModelSchedule).where(ModelSchedule.model_id == model_id)) is None
            assert db.scalar(select(ModelScoring).where(ModelScoring.model_id == model_id)) is None
    finally:
        db.close()
