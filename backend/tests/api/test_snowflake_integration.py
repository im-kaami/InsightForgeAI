"""Runs against a real Snowflake account (the sample data SNOWFLAKE_SAMPLE_DATA.TPCH_SF1).

Needs INSIGHTFORGE_TEST_SNOWFLAKE_ACCOUNT, _USER, _PASSWORD and _WAREHOUSE in the environment or in
backend/.env. Only the small NATION (25 rows) and REGION (5 rows) tables are ever copied.
"""

import asyncio
import os
from pathlib import Path
from urllib.parse import quote

import pytest
from dotenv import dotenv_values

pytestmark = pytest.mark.integration

PREFIX = "INSIGHTFORGE_TEST_SNOWFLAKE_"
KEYS = ("ACCOUNT", "USER", "PASSWORD", "WAREHOUSE")
SAMPLE = "SNOWFLAKE_SAMPLE_DATA/TPCH_SF1"


def leaked(text: str, secret: str) -> bool:
    """A plain bool, so a failing assertion never prints the secret."""
    return secret in text


def _settings() -> dict[str, str]:
    stored = dotenv_values(Path(__file__).parents[2] / ".env")
    values = {key: os.getenv(PREFIX + key) or stored.get(PREFIX + key) or "" for key in KEYS}
    if not all(values.values()):
        pytest.skip("INSIGHTFORGE_TEST_SNOWFLAKE_* settings are not set")
    return values


def _uri(values: dict[str, str], password: str | None = None, path: str = SAMPLE) -> str:
    secret = quote(values["PASSWORD"] if password is None else password, safe="")
    return (
        f"snowflake://{quote(values['USER'], safe='')}:{secret}@{values['ACCOUNT']}/{path}"
        f"?warehouse={quote(values['WAREHOUSE'], safe='')}"
    )


@pytest.fixture(scope="module")
def values():
    return _settings()


async def test_tables_are_listed_in_lower_case_and_the_schema_option_works(client, auth_headers, values):
    listed = await client.post("/api/connections/test", headers=auth_headers, json={"uri": _uri(values)})
    assert listed.status_code == 200, listed.text
    tables = listed.json()["tables"]
    assert {"nation", "region", "customer", "orders"} <= set(tables)
    assert all(name == name.lower() for name in tables)
    assert not leaked(listed.text, values["PASSWORD"])
    other = await client.post(
        "/api/connections/test",
        headers=auth_headers,
        json={"uri": _uri(values, path="SNOWFLAKE_SAMPLE_DATA"), "schema": "TPCH_SF1"},
    )
    assert other.status_code == 200, other.text
    assert other.json()["tables"] == tables
    assert (await client.get("/api/connections", headers=auth_headers)).json() == []


async def _dataset(client, headers, values):
    response = await client.post(
        "/api/datasets/from-connection",
        headers=headers,
        json={"uri": _uri(values), "name": "Sample", "tables": ["nation", "region"]},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_the_allow_list_copies_exactly_nation_and_region(client, auth_headers, values):
    dataset = await _dataset(client, auth_headers, values)
    counts = {
        table["name"].split("__")[-1]: table["row_count"] for table in dataset["schema"]["tables"]
    }
    assert counts == {"nation": 25, "region": 5}
    assert not leaked(dataset["sources"][0]["location"], values["PASSWORD"])


async def test_an_analysis_runs_and_the_guard_refuses_writes(client, auth_headers, values):
    dataset = await _dataset(client, auth_headers, values)
    session = (
        await client.post(
            "/api/sessions", headers=auth_headers, json={"dataset_id": dataset["id"], "title": "Sample"}
        )
    ).json()
    goal = {"goal": "How many nations are there per region?"}
    run = await client.post(f"/api/sessions/{session['id']}/runs", headers=auth_headers, json=goal)
    assert run.status_code == 202, run.text
    for _ in range(240):
        current = (await client.get(f"/api/runs/{run.json()['id']}", headers=auth_headers)).json()
        if current["status"] in {"completed", "failed"}:
            break
        await asyncio.sleep(0.5)
    assert current["status"] == "completed", current.get("error")
    table = next(name for name in dataset["tables"] if name.endswith("nation"))
    for sql in (f'DELETE FROM "{table}"', f'DROP TABLE "{table}"'):
        refused = await client.post(
            f"/api/datasets/{dataset['id']}/sql", headers=auth_headers, json={"sql": sql}
        )
        assert refused.status_code == 422, sql
    counted = await client.post(
        f"/api/datasets/{dataset['id']}/sql",
        headers=auth_headers,
        json={"sql": f'SELECT COUNT(*) AS n FROM "{table}"'},
    )
    assert counted.json()["rows"] == [{"n": 25}]


async def test_a_wrong_password_is_a_clean_error_without_the_password(client, auth_headers, values):
    wrong = "Not-The-Password-123!"
    response = await client.post(
        "/api/connections/test", headers=auth_headers, json={"uri": _uri(values, password=wrong)}
    )
    assert response.status_code == 422
    assert not leaked(response.text, wrong)
    assert not leaked(response.text, values["PASSWORD"])
    assert "Could not read tables" in response.json()["detail"]
