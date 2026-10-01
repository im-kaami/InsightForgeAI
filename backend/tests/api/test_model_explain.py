import pytest

from .test_saved_models import (
    _predict_position,
    _prediction_run,
    _register,
    _save_model,
    _upload_churn,
)


async def _saved(client, headers):
    dataset = await _upload_churn(client, headers)
    run_id = await _prediction_run(client, headers, dataset)
    position = await _predict_position(client, headers, run_id)
    saved = await _save_model(client, headers, run_id, position)
    assert saved.status_code == 201, saved.text
    return saved.json()


async def test_a_scored_row_is_explained_and_adds_up(client, auth_headers):
    model = await _saved(client, auth_headers)
    scored = (
        await client.post(f"/api/models/{model['id']}/score", headers=auth_headers, json={})
    ).json()
    row = scored["preview_rows"][0]  # includes prediction columns, which are ignored
    response = await client.post(
        f"/api/models/{model['id']}/explain", headers=auth_headers, json={"values": row}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert {item["feature"] for item in body["contributions"]} == set(model["features"])
    total = body["reference"] + sum(item["contribution"] for item in body["contributions"])
    assert total == pytest.approx(body["output"], abs=1e-6)
    assert abs(body["additivity_gap"]) < 1e-6
    assert body["algorithm"] == "exact Shapley values"
    assert body["explained"].startswith("probability of")
    if "probability" in row:
        assert body["output"] == pytest.approx(row["probability"], abs=1e-9)
    assert str(body["prediction"]) == str(row["prediction"])
    assert body["background_rows"] == 50
    assert body["cautions"]


async def test_explain_rejects_missing_values_and_other_accounts(client, auth_headers):
    model = await _saved(client, auth_headers)
    missing = await client.post(
        f"/api/models/{model['id']}/explain", headers=auth_headers, json={"values": {"tenure": 3}}
    )
    assert missing.status_code == 422
    assert "missing" in missing.json()["detail"].lower()
    other = await _register(client, "explain-intruder@example.com")
    forbidden = await client.post(
        f"/api/models/{model['id']}/explain",
        headers=other,
        json={"values": {"tenure": 3, "monthly": 20, "plan": "basic"}},
    )
    assert forbidden.status_code == 404
