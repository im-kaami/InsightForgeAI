import json
from pathlib import Path

from sqlalchemy import select

from insightforge.core.llm import FakeLLMClient
from insightforge.db.models import Run, SavedModel
from insightforge.db.session import SessionLocal
from insightforge.services.runs import execute_run

FIXTURES = Path(__file__).parents[1] / "fixtures"


async def _register(client, email):
    credentials = {"email": email, "password": "correct horse battery staple"}
    await client.post("/api/auth/register", json=credentials)
    login = await client.post("/api/auth/login", json=credentials)
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _upload_churn(client, headers, name="churn.csv"):
    content = (FIXTURES / "churn.csv").read_bytes()
    response = await client.post(
        "/api/datasets/upload",
        headers=headers,
        files=[("files", (name, content, "text/csv"))],
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _prediction_run(client, headers, dataset) -> str:
    """Create a completed run whose plan trains a churn prediction model."""
    session = (
        await client.post(
            "/api/sessions",
            headers=headers,
            json={"dataset_id": dataset["id"], "title": "Predict"},
        )
    ).json()
    user = (await client.get("/api/auth/me", headers=headers)).json()
    table = dataset["tables"][0]
    plan = {
        "steps": [
            {
                "name": "rows",
                "action": "sql",
                "query": f'SELECT tenure, monthly, plan, churned FROM "{table}"',
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

    def reply(_messages):
        return json.dumps(plan)

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
    execute_run(run_id, llm=FakeLLMClient(reply))
    return run_id


async def _predict_position(client, headers, run_id) -> int:
    payload = (await client.get(f"/api/runs/{run_id}", headers=headers)).json()
    assert payload["status"] == "completed", payload.get("error")
    for index, artifact in enumerate(payload["artifacts"]):
        if artifact["type"] == "stat" and artifact.get("method") == "predict":
            return index
    raise AssertionError("no prediction artifact found")


async def _save_model(client, headers, run_id, position, name="churn model"):
    return await client.post(
        f"/api/runs/{run_id}/artifacts/{position}/model",
        headers=headers,
        json={"name": name},
    )


async def test_save_then_score_same_version_is_stable(client, auth_headers):
    dataset = await _upload_churn(client, auth_headers)
    run_id = await _prediction_run(client, auth_headers, dataset)
    position = await _predict_position(client, auth_headers, run_id)
    saved = await _save_model(client, auth_headers, run_id, position)
    assert saved.status_code == 201, saved.text
    model = saved.json()
    assert model["task"] == "classification"
    assert model["target"] == "churned"
    assert "file_path" not in model  # the file path is never returned
    scored = await client.post(
        f"/api/models/{model['id']}/score", headers=auth_headers, json={}
    )
    assert scored.status_code == 200, scored.text
    body = scored.json()
    assert body["rows_scored"] == 600
    assert body["drift"]["max_psi"] < 0.01  # same data -> no drift
    assert body["recommendation"]["verdict"] == "no retraining signal"
    assert body["new_data_metrics"] is not None
    assert len(body["preview_rows"]) == 200
    csv = await client.get(f"/api/models/{model['id']}/scores/latest.csv", headers=auth_headers)
    assert csv.status_code == 200
    assert "prediction" in csv.text.splitlines()[0]


async def test_score_a_shifted_version_recommends_retraining(client, auth_headers):
    dataset = await _upload_churn(client, auth_headers)
    run_id = await _prediction_run(client, auth_headers, dataset)
    position = await _predict_position(client, auth_headers, run_id)
    model = (await _save_model(client, auth_headers, run_id, position)).json()
    # Replace with a version whose 'monthly' distribution is shifted far away.
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(7)
    n = 600
    frame = pd.DataFrame(
        {
            "customer_id": range(n),
            "tenure": rng.integers(1, 60, n),
            "monthly": rng.normal(400, 20, n).round(2),  # shifted well beyond training deciles
            "plan": rng.choice(["basic", "plus", "pro"], n),
            "churned": rng.choice(["yes", "no"], n),
        }
    )
    shifted_csv = frame.to_csv(index=False).encode()
    replacement = await client.post(
        f"/api/datasets/{dataset['id']}/versions",
        headers=auth_headers,
        files=[("files", ("churn.csv", shifted_csv, "text/csv"))],
    )
    assert replacement.status_code == 201, replacement.text
    new_version = replacement.json()["id"]
    confirm = await client.post(
        f"/api/datasets/{dataset['id']}/versions/{new_version}/confirm",
        headers=auth_headers,
        json={"confirmed": True, "expected_current_version_id": dataset["current_version_id"]},
    )
    assert confirm.status_code == 200, confirm.text
    scored = await client.post(
        f"/api/models/{model['id']}/score",
        headers=auth_headers,
        json={"version_id": new_version},
    )
    assert scored.status_code == 200, scored.text
    body = scored.json()
    monthly = next(row for row in body["drift"]["features"] if row["feature"] == "monthly")
    assert monthly["band"] == "major shift"
    assert body["recommendation"]["verdict"] == "retrain recommended"


async def test_missing_feature_is_blocked_with_422(client, auth_headers):
    dataset = await _upload_churn(client, auth_headers)
    run_id = await _prediction_run(client, auth_headers, dataset)
    position = await _predict_position(client, auth_headers, run_id)
    model = (await _save_model(client, auth_headers, run_id, position)).json()
    import pandas as pd

    frame = pd.DataFrame(
        {"customer_id": range(80), "tenure": range(80), "churned": ["yes", "no"] * 40}
    )  # drops 'monthly' and 'plan'
    dropped_csv = frame.to_csv(index=False).encode()
    replacement = await client.post(
        f"/api/datasets/{dataset['id']}/versions",
        headers=auth_headers,
        files=[("files", ("churn.csv", dropped_csv, "text/csv"))],
    )
    assert replacement.status_code == 201, replacement.text
    new_version = replacement.json()["id"]
    await client.post(
        f"/api/datasets/{dataset['id']}/versions/{new_version}/confirm",
        headers=auth_headers,
        json={"confirmed": True, "expected_current_version_id": dataset["current_version_id"]},
    )
    scored = await client.post(
        f"/api/models/{model['id']}/score",
        headers=auth_headers,
        json={"version_id": new_version},
    )
    assert scored.status_code == 422
    assert "monthly" in scored.json()["detail"]


async def test_blocking_validation_failure_stops_scoring_with_409(client, auth_headers):
    dataset = await _upload_churn(client, auth_headers)
    run_id = await _prediction_run(client, auth_headers, dataset)
    position = await _predict_position(client, auth_headers, run_id)
    model = (await _save_model(client, auth_headers, run_id, position)).json()
    table = dataset["tables"][0]
    # Save a blocking rule the current version cannot meet, so validation fails hard.
    rules = await client.put(
        f"/api/datasets/{dataset['id']}/rules",
        headers=auth_headers,
        json={
            "rules": [
                {
                    "kind": "row_count",
                    "table": table,
                    "severity": "blocking",
                    "min": 10_000_000,
                }
            ]
        },
    )
    assert rules.status_code == 200, rules.text
    scored = await client.post(
        f"/api/models/{model['id']}/score", headers=auth_headers, json={}
    )
    assert scored.status_code == 409, scored.text
    assert "blocking" in scored.json()["detail"].lower()


async def test_owner_scoping_returns_404(client, auth_headers):
    dataset = await _upload_churn(client, auth_headers)
    run_id = await _prediction_run(client, auth_headers, dataset)
    position = await _predict_position(client, auth_headers, run_id)
    model = (await _save_model(client, auth_headers, run_id, position)).json()
    other = await _register(client, "intruder@example.com")
    assert (await client.get(f"/api/models/{model['id']}", headers=other)).status_code == 404
    assert (
        await client.post(f"/api/models/{model['id']}/score", headers=other, json={})
    ).status_code == 404
    assert (await client.delete(f"/api/models/{model['id']}", headers=other)).status_code == 404
    assert (
        await client.get(f"/api/datasets/{dataset['id']}/models", headers=other)
    ).status_code == 404


async def test_delete_removes_the_folder(client, auth_headers, app):
    from insightforge.config import get_settings

    dataset = await _upload_churn(client, auth_headers)
    run_id = await _prediction_run(client, auth_headers, dataset)
    position = await _predict_position(client, auth_headers, run_id)
    model = (await _save_model(client, auth_headers, run_id, position)).json()
    user = (await client.get("/api/auth/me", headers=auth_headers)).json()
    folder = get_settings().storage_dir / "users" / user["id"] / "models" / model["id"]
    assert folder.is_dir()
    deleted = await client.delete(f"/api/models/{model['id']}", headers=auth_headers)
    assert deleted.status_code == 204
    assert not folder.exists()
    assert (await client.get(f"/api/models/{model['id']}", headers=auth_headers)).status_code == 404


async def test_deleting_the_dataset_removes_its_models(client, auth_headers, app):
    from insightforge.config import get_settings

    dataset = await _upload_churn(client, auth_headers)
    run_id = await _prediction_run(client, auth_headers, dataset)
    position = await _predict_position(client, auth_headers, run_id)
    model = (await _save_model(client, auth_headers, run_id, position)).json()
    user = (await client.get("/api/auth/me", headers=auth_headers)).json()
    folder = get_settings().storage_dir / "users" / user["id"] / "models" / model["id"]
    assert folder.is_dir()
    deleted = await client.delete(f"/api/datasets/{dataset['id']}", headers=auth_headers)
    assert deleted.status_code == 204
    assert not folder.exists()
    db = SessionLocal()
    try:
        remaining = db.scalar(select(SavedModel).where(SavedModel.id == model["id"]))
    finally:
        db.close()
    assert remaining is None
