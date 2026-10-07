import sqlite3

import pytest

from insightforge.config import get_settings

PASSWORD = "correct horse battery staple"


@pytest.fixture
def allow_files(monkeypatch):
    monkeypatch.setenv("ALLOW_SQLITE_FILES", "true")
    get_settings.cache_clear()


def _sqlite(path):
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE widgets (id INTEGER, name TEXT)")
        connection.execute("INSERT INTO widgets VALUES (1, 'one')")
        connection.execute("CREATE TABLE gadgets (id INTEGER)")
    return f"sqlite:///{path.as_posix()}"


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


async def _other_user(client):
    await client.post("/api/auth/register", json={"email": "other@example.com", "password": PASSWORD})
    login = await client.post("/api/auth/login", json={"email": "other@example.com", "password": PASSWORD})
    return _bearer(login.json()["access_token"])


async def test_test_endpoint_lists_tables_and_saves_nothing(client, auth_headers, tmp_path, allow_files):
    uri = _sqlite(tmp_path / "shop.sqlite")
    response = await client.post("/api/connections/test", headers=auth_headers, json={"uri": uri})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kind"] == "sqlite" and body["tables"] == ["gadgets", "widgets"]
    assert (await client.get("/api/connections", headers=auth_headers)).json() == []
    assert (await client.get("/api/datasets", headers=auth_headers)).json() == []


async def test_test_endpoint_reports_problems_without_the_password(client, auth_headers):
    response = await client.post(
        "/api/connections/test", headers=auth_headers, json={"uri": "mssql://sa:topsecret@127.0.0.1:1/db"}
    )
    assert response.status_code == 422
    assert "topsecret" not in response.text
    missing = await client.post(
        "/api/connections/test", headers=auth_headers, json={"uri": "sqlite:///nowhere/none.sqlite"}
    )
    assert missing.status_code == 422


async def test_schema_names_are_validated(client, auth_headers, tmp_path, allow_files):
    uri = _sqlite(tmp_path / "shop.sqlite")
    for bad in ("", "has space", "semi;colon", "1abc", "x" * 129, "a'b"):
        tested = await client.post(
            "/api/connections/test", headers=auth_headers, json={"uri": uri, "schema": bad}
        )
        assert tested.status_code == 422, bad
        created = await client.post(
            "/api/datasets/from-connection",
            headers=auth_headers,
            json={"uri": uri, "name": "Shop", "schema": bad},
        )
        assert created.status_code == 422, bad
    saved = await client.post("/api/connections", headers=auth_headers, json={"name": "Shop", "uri": uri})
    tables = await client.get(
        f"/api/connections/{saved.json()['id']}/tables", headers=auth_headers, params={"schema": "bad name"}
    )
    assert tables.status_code == 422


async def test_saved_connection_tables_are_owner_scoped(client, auth_headers, tmp_path, allow_files):
    uri = _sqlite(tmp_path / "shop.sqlite")
    saved = await client.post("/api/connections", headers=auth_headers, json={"name": "Shop", "uri": uri})
    assert saved.status_code == 201, saved.text
    url = f"/api/connections/{saved.json()['id']}/tables"
    mine = await client.get(url, headers=auth_headers)
    assert mine.status_code == 200 and mine.json()["tables"] == ["gadgets", "widgets"]
    other = await _other_user(client)
    assert (await client.get(url, headers=other)).status_code == 404
    assert (await client.get("/api/connections", headers=other)).json() == []


async def test_api_tokens_cannot_test_connections(client, auth_headers, tmp_path, allow_files):
    uri = _sqlite(tmp_path / "shop.sqlite")
    created = await client.post(
        "/api/auth/tokens", headers=auth_headers, json={"name": "ci", "scope": "ask"}
    )
    token = created.json()["token"]
    refused = await client.post("/api/connections/test", headers=_bearer(token), json={"uri": uri})
    assert refused.status_code in {401, 403}
    saved = await client.post("/api/connections", headers=auth_headers, json={"name": "Shop", "uri": uri})
    listed = await client.get(f"/api/connections/{saved.json()['id']}/tables", headers=_bearer(token))
    assert listed.status_code in {200, 401, 403}


async def test_a_dataset_keeps_the_allow_list(client, auth_headers, tmp_path, allow_files):
    uri = _sqlite(tmp_path / "shop.sqlite")
    created = await client.post(
        "/api/datasets/from-connection",
        headers=auth_headers,
        json={"uri": uri, "name": "Shop", "tables": ["widgets"]},
    )
    assert created.status_code == 201, created.text
    assert created.json()["sources"][0]["options"]["tables"] == ["widgets"]
    assert [name.split(".")[-1] for name in created.json()["tables"]] == ["widgets"]


@pytest.mark.parametrize("scheme", ["mssql", "sqlserver", "mssql+pymssql"])
async def test_sql_server_connections_fail_cleanly_when_unreachable(client, auth_headers, scheme):
    response = await client.post(
        "/api/connections",
        headers=auth_headers,
        json={"name": "Warehouse", "uri": f"{scheme}://sa:topsecret@127.0.0.1:1/db"},
    )
    assert response.status_code == 400
    assert "topsecret" not in response.text


async def test_sqlite_files_are_refused_by_default(client, auth_headers, tmp_path):
    uri = _sqlite(tmp_path / "shop.sqlite")
    off = "SQLite file connections are turned off on this server (ALLOW_SQLITE_FILES)"
    tested = await client.post("/api/connections/test", headers=auth_headers, json={"uri": uri})
    assert tested.status_code == 422 and off in tested.json()["detail"]
    saved = await client.post("/api/connections", headers=auth_headers, json={"name": "Shop", "uri": uri})
    assert saved.status_code == 400 and off in saved.json()["detail"]
    made = await client.post(
        "/api/datasets/from-connection", headers=auth_headers, json={"uri": uri, "name": "Shop"}
    )
    assert made.status_code == 400 and off in made.json()["detail"]
    alchemy = await client.post(
        "/api/connections/test",
        headers=auth_headers,
        json={"uri": uri.replace("sqlite:", "sqlite+pysqlite:")},
    )
    assert alchemy.status_code == 422 and off in alchemy.json()["detail"]
    bare = await client.post(
        "/api/connections/test", headers=auth_headers, json={"uri": str(tmp_path / "shop.sqlite")}
    )
    assert bare.status_code == 422 and off in bare.json()["detail"]
    assert (await client.get("/api/connections", headers=auth_headers)).json() == []


async def test_options_reflect_the_sqlite_setting(client, auth_headers, monkeypatch):
    options = await client.get("/api/connections/options", headers=auth_headers)
    assert options.json() == {"sqlite_files": False}
    monkeypatch.setenv("ALLOW_SQLITE_FILES", "true")
    get_settings.cache_clear()
    options = await client.get("/api/connections/options", headers=auth_headers)
    assert options.json() == {"sqlite_files": True}
    assert (await client.get("/api/connections/options")).status_code == 401


async def test_the_apps_own_files_stay_off_limits(client, auth_headers, tmp_path, allow_files):
    app_database = tmp_path / "app.db"
    storage = tmp_path / "storage"
    inside = storage / "users" / "x"
    inside.mkdir(parents=True, exist_ok=True)
    _sqlite(inside / "catalog.sqlite")
    candidates = [
        f"sqlite:///{app_database.as_posix()}",
        f"sqlite:///{(storage / '..' / 'app.db').as_posix()}",
        f"sqlite:///{app_database.as_posix().upper()}",
        f"sqlite:///{(inside / 'catalog.sqlite').as_posix()}",
        f"sqlite:///{(storage / 'users' / 'x' / '..' / 'x' / 'catalog.sqlite').as_posix()}",
        f"sqlite:///{(inside / 'CATALOG.SQLITE').as_posix()}",
        f"sqlite:///{(tmp_path / 'missing.sqlite').as_posix()}",
        "sqlite://",
    ]
    for uri in candidates:
        response = await client.post("/api/connections/test", headers=auth_headers, json={"uri": uri})
        assert response.status_code == 422, uri
        assert "turned off" not in response.json()["detail"]


async def test_a_saved_sqlite_connection_stops_loading_when_turned_off(
    client, auth_headers, tmp_path, monkeypatch
):
    monkeypatch.setenv("ALLOW_SQLITE_FILES", "true")
    get_settings.cache_clear()
    uri = _sqlite(tmp_path / "shop.sqlite")
    body = {"name": "Shop", "uri": uri}
    saved = (await client.post("/api/connections", headers=auth_headers, json=body)).json()
    dataset = await client.post(
        "/api/datasets/from-connection",
        headers=auth_headers,
        json={"connection_id": saved["id"], "name": "Shop data"},
    )
    assert dataset.status_code == 201, dataset.text
    monkeypatch.setenv("ALLOW_SQLITE_FILES", "false")
    get_settings.cache_clear()
    preview = await client.get(
        f"/api/datasets/{dataset.json()['id']}/preview",
        params={"table": dataset.json()["tables"][0]},
        headers=auth_headers,
    )
    assert preview.status_code >= 400
    again = await client.get(f"/api/connections/{saved['id']}/tables", headers=auth_headers)
    assert again.status_code == 422 and "turned off" in again.json()["detail"]


@pytest.fixture
def production(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    get_settings.cache_clear()


async def test_private_database_hosts_are_refused_in_production(client, auth_headers, production):
    for host in ("127.0.0.1", "10.1.2.3", "localhost", "192.168.0.5", "[::1]", "db.internal"):
        response = await client.post(
            "/api/connections/test",
            headers=auth_headers,
            json={"uri": f"postgresql://user:pw@{host}:5432/app"},
        )
        assert response.status_code == 422, host
        assert "ALLOW_PRIVATE_DATABASES" in response.json()["detail"], host
    for uri in (
        "mssql://sa:pw@10.0.0.4:1433/db",
        "mysql://u:pw@127.0.0.1/app",
        "postgresql://u:pw@db.example.com/app?host=10.0.0.9",
        "host=127.0.0.1 dbname=app user=u",
    ):
        response = await client.post("/api/connections/test", headers=auth_headers, json={"uri": uri})
        assert response.status_code == 422, uri
        assert "ALLOW_PRIVATE_DATABASES" in response.json()["detail"], uri
    body = {"name": "Local", "uri": "postgresql://u:p@127.0.0.1/app"}
    assert (await client.post("/api/connections", headers=auth_headers, json=body)).status_code == 400


async def test_public_database_hosts_pass_the_address_check(client, auth_headers, production):
    response = await client.post(
        "/api/connections/test",
        headers=auth_headers,
        json={"uri": "postgresql://user:pw@db.example.com:1/app"},
    )
    assert response.status_code == 422
    assert "ALLOW_PRIVATE_DATABASES" not in response.json()["detail"]


async def test_private_databases_can_be_allowed_explicitly(client, auth_headers, production, monkeypatch):
    monkeypatch.setenv("ALLOW_PRIVATE_DATABASES", "true")
    get_settings.cache_clear()
    response = await client.post(
        "/api/connections/test",
        headers=auth_headers,
        json={"uri": "postgresql://user:pw@127.0.0.1:1/app"},
    )
    assert "ALLOW_PRIVATE_DATABASES" not in response.json()["detail"]
