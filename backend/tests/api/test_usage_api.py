import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from insightforge.config import get_settings
from insightforge.core.llm import FakeLLMClient, LLMResponse
from insightforge.db.models import Run
from insightforge.db.session import SessionLocal
from insightforge.services.runs import execute_run

FIXTURES = Path(__file__).parents[1] / "fixtures"
PASSWORD = "correct horse battery staple"
MODEL = "test/model-1"
PRICES = json.dumps({MODEL: {"input": 2.0, "output": 10.0}})


class BilledClient:
    """Answers like the fake model but reports token counts, and is not treated as offline."""

    def __init__(self, model: str, table: str):
        self.model = model
        self.inner = FakeLLMClient(self._reply)
        self.table = table

    def _reply(self, messages):
        if "data-analysis planner" in messages[0]["content"]:
            query = f'SELECT department, COUNT(*) AS people FROM "{self.table}" GROUP BY 1'
            steps = [
                {"name": "headcount", "action": "sql", "query": query},
                {"name": "summary", "action": "summary"},
            ]
            return json.dumps({"steps": steps})
        return json.dumps({"headline": "Summary", "findings": [], "actions": []})

    @staticmethod
    def _billed(response: LLMResponse) -> LLMResponse:
        response.prompt_tokens, response.completion_tokens = 1000, 500
        return response

    def chat(self, messages, *, temperature=0.0):
        return self._billed(self.inner.chat(messages, temperature=temperature))

    def chat_json(self, messages, *, temperature=0.0, schema=None):
        raw, response = self.inner.chat_json(messages, temperature=temperature, schema=schema)
        return raw, self._billed(response)


async def _login(client, email):
    await client.post("/api/auth/register", json={"email": email, "password": PASSWORD})
    token = (await client.post("/api/auth/login", json={"email": email, "password": PASSWORD})).json()
    return {"Authorization": f"Bearer {token['access_token']}"}


async def _dataset(client, headers, policy, fixture_bytes):
    upload = await client.post(
        "/api/datasets/upload",
        headers=headers,
        files=[("files", ("hr.csv", fixture_bytes, "text/csv"))],
    )
    assert upload.status_code == 201, upload.text
    dataset = upload.json()
    if policy != "local":
        response = await client.patch(
            f"/api/datasets/{dataset['id']}/privacy",
            headers=headers,
            json={"mode": policy, "acknowledged": True},
        )
        assert response.status_code == 200, response.text
    session = await client.post(
        "/api/sessions", headers=headers, json={"dataset_id": dataset["id"], "title": "Usage"}
    )
    return dataset, session.json()


async def _run(client, headers, dataset, session, policy, llm, local=None, goal="Headcount"):
    user = (await client.get("/api/auth/me", headers=headers)).json()
    db = SessionLocal()
    try:
        run = Run(
            session_id=session["id"],
            owner_id=user["id"],
            goal=goal,
            status="pending",
            request_json={"kind": "exploratory", "privacy_mode": policy},
        )
        db.add(run)
        db.commit()
        run_id = run.id
    finally:
        db.close()
    execute_run(run_id, llm=llm, local_llm=local)
    payload = (await client.get(f"/api/runs/{run_id}", headers=headers)).json()
    assert payload["status"] == "completed", payload["error"]
    return payload


def _configure(monkeypatch, prices=PRICES):
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("LLM_MODEL", MODEL)
    monkeypatch.setenv("LLM_PRICES", prices)
    get_settings.cache_clear()


async def test_cloud_run_with_a_configured_price_records_model_and_cost(
    client, auth_headers, hr_dataset, monkeypatch
):
    _configure(monkeypatch)
    dataset, session = await _dataset(client, auth_headers, "full", _hr_bytes())
    llm = BilledClient(MODEL, dataset["tables"][0])
    payload = await _run(client, auth_headers, dataset, session, "full", llm)
    assert (payload["llm_provider"], payload["llm_model"]) == ("groq", MODEL)
    tokens = payload["token_usage"]
    assert tokens["prompt_tokens"] == 2000 and tokens["completion_tokens"] == 1000
    assert payload["cost_usd"] == round((2.0 * 2000 + 10.0 * 1000) / 1e6, 6)
    cost = payload["provenance"]["cost"]
    assert cost["price"] == {"input": 2.0, "output": 10.0}
    assert cost["cost_usd"] == payload["cost_usd"] and cost["model"] == MODEL


async def test_cloud_run_without_a_price_has_unknown_cost(client, auth_headers, monkeypatch):
    _configure(monkeypatch, prices="{}")
    dataset, session = await _dataset(client, auth_headers, "full", _hr_bytes())
    payload = await _run(
        client, auth_headers, dataset, session, "full", BilledClient(MODEL, dataset["tables"][0])
    )
    assert payload["llm_model"] == MODEL and payload["cost_usd"] is None
    assert payload["provenance"]["cost"]["price"] is None
    usage = (await client.get("/api/usage", headers=auth_headers)).json()
    assert usage["totals"]["runs"] == 1 and usage["totals"]["unpriced_runs"] == 1
    assert usage["totals"]["cost_usd"] == 0
    assert usage["by_model"][0]["unpriced_runs"] == 1


async def test_local_policy_run_is_free_and_fake_runs_are_not_counted(
    client, auth_headers, app, monkeypatch
):
    _configure(monkeypatch)
    dataset, session = await _dataset(client, auth_headers, "local", _hr_bytes())
    local = BilledClient("qwen3:4b", dataset["tables"][0])
    payload = await _run(client, auth_headers, dataset, session, "local", app.state.llm, local=local)
    assert (payload["llm_provider"], payload["llm_model"], payload["cost_usd"]) == (
        "local",
        "qwen3:4b",
        0.0,
    )
    offline = await _run(client, auth_headers, dataset, session, "local", app.state.llm)
    assert (offline["llm_provider"], offline["llm_model"], offline["cost_usd"]) == (None, None, None)
    assert "cost" not in offline["provenance"]
    fake = FakeLLMClient(["{}"])
    full_dataset, full_session = await _dataset(client, auth_headers, "full", _hr_bytes())
    faked = await _run(client, auth_headers, full_dataset, full_session, "full", fake)
    assert faked["llm_model"] is None
    usage = (await client.get("/api/usage", headers=auth_headers)).json()
    assert usage["totals"]["runs"] == 1
    assert [(item["provider"], item["model"]) for item in usage["by_model"]] == [("local", "qwen3:4b")]


async def test_usage_aggregates_by_model_and_day_and_respects_days_and_owner(
    client, auth_headers, monkeypatch
):
    _configure(monkeypatch)
    dataset, session = await _dataset(client, auth_headers, "full", _hr_bytes())
    table = dataset["tables"][0]
    first = await _run(client, auth_headers, dataset, session, "full", BilledClient(MODEL, table))
    await _run(client, auth_headers, dataset, session, "full", BilledClient(MODEL, table))
    db = SessionLocal()
    try:
        old = db.get(Run, first["id"])
        user_id = old.owner_id
        db.add(
            Run(
                session_id=session["id"],
                owner_id=user_id,
                goal="Old",
                status="completed",
                token_usage_json={"prompt_tokens": 7, "completion_tokens": 3},
                llm_provider="groq",
                llm_model="old/model",
                cost_usd=1.5,
                created_at=datetime.now(UTC) - timedelta(days=100),
            )
        )
        db.add(
            Run(
                session_id=session["id"],
                owner_id=user_id,
                goal="Yesterday",
                status="completed",
                llm_provider="groq",
                llm_model="old/model",
                cost_usd=0.25,
                created_at=datetime.now(UTC) - timedelta(days=1),
            )
        )
        db.commit()
    finally:
        db.close()

    month = (await client.get("/api/usage", headers=auth_headers, params={"days": 30})).json()
    assert month["days"] == 30 and month["since"]
    assert month["totals"] == {
        "runs": 3,
        "prompt_tokens": 4000,
        "completion_tokens": 2000,
        "cost_usd": round(2 * 0.014 + 0.25, 6),
        "unpriced_runs": 0,
    }
    assert [(item["model"], item["runs"]) for item in month["by_model"]] == [(MODEL, 2), ("old/model", 1)]
    assert [item["date"] for item in month["by_day"]] == sorted(item["date"] for item in month["by_day"])
    assert sum(item["runs"] for item in month["by_day"]) == 3
    year = (await client.get("/api/usage", headers=auth_headers, params={"days": 365})).json()
    assert year["totals"]["runs"] == 4 and year["totals"]["prompt_tokens"] == 4007
    for days in (0, 366):
        response = await client.get("/api/usage", headers=auth_headers, params={"days": days})
        assert response.status_code == 422

    other = await _login(client, "other@example.com")
    assert (await client.get("/api/usage", headers=other)).json()["totals"]["runs"] == 0
    assert (await client.get("/api/usage")).status_code == 401


def _hr_bytes():
    return (FIXTURES / "hr.csv").read_bytes()

