"""Runs against local PostgreSQL and SQL Server containers.

Set INSIGHTFORGE_TEST_PG_URI (postgresql://user:password@127.0.0.1:54320/db) and
INSIGHTFORGE_TEST_MSSQL_URI (mssql+pymssql://sa:password@127.0.0.1:14330/master) to run them;
the databases are seeded from tests/fixtures/db_seed by tests/db_seed.py.
"""

import asyncio
import os

import pytest

from insightforge.core.catalog import DataCatalog

from ..db_seed import seed_mssql, seed_postgres

pytestmark = pytest.mark.integration

BACKENDS = {
    "postgres": ("INSIGHTFORGE_TEST_PG_URI", seed_postgres, "orders", "customers"),
    "mssql": ("INSIGHTFORGE_TEST_MSSQL_URI", seed_mssql, "orders", "customers"),
}


@pytest.fixture(params=sorted(BACKENDS))
def database(request):
    variable, seed, *_ = BACKENDS[request.param]
    uri = os.getenv(variable)
    if not uri:
        pytest.skip(f"{variable} is not set")
    seed(uri)
    return request.param, uri


def _short(names):
    return sorted(name.split(".")[-1].split("__")[-1] for name in names)


async def _create(client, headers, uri, name, **extra):
    response = await client.post(
        "/api/datasets/from-connection", headers=headers, json={"uri": uri, "name": name, **extra}
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_the_schema_option_picks_the_tables(client, auth_headers, database):
    _, uri = database
    default = await client.post("/api/connections/test", headers=auth_headers, json={"uri": uri})
    assert default.status_code == 200, default.text
    assert {"orders", "customers"} <= set(default.json()["tables"])
    assert "invoices" not in default.json()["tables"]
    sales = await client.post(
        "/api/connections/test", headers=auth_headers, json={"uri": uri, "schema": "sales"}
    )
    assert sorted(sales.json()["tables"]) == ["invoices", "regions"]
    assert "***" in sales.json()["redacted_uri"] or "@" not in sales.json()["redacted_uri"]
    assert (await client.get("/api/connections", headers=auth_headers)).json() == []


async def test_schema_and_allow_list_decide_what_the_dataset_holds(client, auth_headers, database):
    _, uri = database
    sales = await _create(client, auth_headers, uri, "Sales db", schema="sales")
    assert _short(sales["tables"]) == ["invoices", "regions"]
    assert sales["sources"][0]["options"]["schema"] == "sales"
    only = await _create(client, auth_headers, uri, "Invoices only", schema="sales", tables=["invoices"])
    assert _short(only["tables"]) == ["invoices"]
    default = await _create(client, auth_headers, uri, "Default db", tables=["orders", "customers"])
    assert _short(default["tables"]) == ["customers", "orders"]
    preview = await client.get(
        f"/api/datasets/{default['id']}/preview",
        params={"table": next(name for name in default["tables"] if name.endswith("orders"))},
        headers=auth_headers,
    )
    assert preview.status_code == 200 and len(preview.json()["rows"]) > 0


async def test_row_counts_and_saved_connections(client, auth_headers, database):
    _, uri = database
    dataset = await _create(client, auth_headers, uri, "Counts", tables=["orders", "customers"])
    counts = {
        table["name"].split(".")[-1].split("__")[-1]: table["row_count"]
        for table in dataset["schema"]["tables"]
    }
    assert counts == {"orders": 1000, "customers": 50}
    connection = (await client.get("/api/connections", headers=auth_headers)).json()[0]
    listed = await client.get(
        f"/api/connections/{connection['id']}/tables", headers=auth_headers, params={"schema": "sales"}
    )
    assert sorted(listed.json()["tables"]) == ["invoices", "regions"]


async def test_an_analysis_runs_on_the_connected_data(client, auth_headers, database):
    _, uri = database
    dataset = await _create(client, auth_headers, uri, "Analysis", tables=["orders", "customers"])
    session = (
        await client.post(
            "/api/sessions", headers=auth_headers, json={"dataset_id": dataset["id"], "title": "Live"}
        )
    ).json()
    goal = {"goal": "How many orders are there?"}
    run = await client.post(f"/api/sessions/{session['id']}/runs", headers=auth_headers, json=goal)
    assert run.status_code == 202, run.text
    for _ in range(240):
        current = (await client.get(f"/api/runs/{run.json()['id']}", headers=auth_headers)).json()
        if current["status"] in {"completed", "failed"}:
            break
        await asyncio.sleep(0.5)
    assert current["status"] == "completed", current.get("error")
    assert any(artifact["type"] == "table" for artifact in current["artifacts"])


async def test_the_sql_guard_refuses_writes(client, auth_headers, database):
    _, uri = database
    dataset = await _create(client, auth_headers, uri, "Guarded", tables=["orders"])
    table = next(name for name in dataset["tables"] if name.endswith("orders"))
    quoted = ".".join(f'"{part}"' for part in table.split("."))
    for sql in (f"DELETE FROM {quoted}", f"DROP TABLE {quoted}", f"UPDATE {quoted} SET amount = 0"):
        refused = await client.post(
            f"/api/datasets/{dataset['id']}/sql", headers=auth_headers, json={"sql": sql}
        )
        assert refused.status_code == 422, sql
    counted = await client.post(
        f"/api/datasets/{dataset['id']}/sql",
        headers=auth_headers,
        json={"sql": f"SELECT COUNT(*) AS n FROM {quoted}"},
    )
    assert counted.json()["rows"] == [{"n": 1000}]


def test_postgres_is_attached_read_only():
    uri = os.getenv("INSIGHTFORGE_TEST_PG_URI")
    if not uri:
        pytest.skip("INSIGHTFORGE_TEST_PG_URI is not set")
    seed_postgres(uri)
    catalog = DataCatalog()
    try:
        catalog.attach("pg", uri, "postgres")
        assert catalog.connection.execute("SELECT COUNT(*) FROM pg.public.customers").fetchone()[0] == 50
        with pytest.raises(Exception, match="read-only|READ_ONLY|attached in"):
            catalog.connection.execute("INSERT INTO pg.public.customers VALUES (999, 'x', 'y')")
        with pytest.raises(Exception, match="read-only|READ_ONLY|attached in"):
            catalog.connection.execute("DELETE FROM pg.public.orders")
    finally:
        catalog.close()
