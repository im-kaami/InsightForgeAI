import sqlite3

import httpx
import respx

from .conftest import FIXTURES


async def test_upload_rejects_file_over_configured_limit(client, auth_headers):
    from insightforge.config import get_settings

    settings = get_settings()
    original = settings.max_upload_bytes
    settings.max_upload_bytes = 1000
    try:
        response = await client.post(
            "/api/datasets/upload",
            headers=auth_headers,
            files=[("files", ("large.csv", b"a" * 1001, "text/csv"))],
        )
    finally:
        settings.max_upload_bytes = original
    assert response.status_code == 413
    assert response.json()["detail"] == "File too large"


async def test_upload_schema_preview_and_owner_scope(client, auth_headers):
    response = await client.post(
        "/api/datasets/upload",
        headers=auth_headers,
        files=[
            ("files", ("hr.csv", (FIXTURES / "hr.csv").read_bytes(), "text/csv")),
            (
                "files",
                ("workbook.xlsx", (FIXTURES / "workbook.xlsx").read_bytes(), "application/xlsx"),
            ),
        ],
    )
    assert response.status_code == 201, response.text
    dataset = response.json()
    assert {"hr", "workbook__orders", "workbook__customers"} <= set(dataset["tables"])
    schema = await client.get(f"/api/datasets/{dataset['id']}/schema", headers=auth_headers)
    assert "employee_id" in str(schema.json())
    hr_schema = next(table for table in schema.json()["tables"] if table["name"] == "hr")
    salary = next(column for column in hr_schema["columns"] if column["name"] == "salary")
    assert salary["sensitivity"] == "financial"
    assert salary["sample_values"] == ["<redacted>"]
    preview = await client.get(
        f"/api/datasets/{dataset['id']}/preview",
        params={"table": "hr", "limit": 50},
        headers=auth_headers,
    )
    assert len(preview.json()["rows"]) <= 50
    missing = await client.get(
        f"/api/datasets/{dataset['id']}/preview",
        params={"table": "missing"},
        headers=auth_headers,
    )
    assert missing.status_code == 404

    other = {"email": "other@example.com", "password": "long-enough-password"}
    await client.post("/api/auth/register", json=other)
    token = (await client.post("/api/auth/login", json=other)).json()["access_token"]
    assert (
        await client.get(
            f"/api/datasets/{dataset['id']}",
            headers={"Authorization": f"Bearer {token}"},
        )
    ).status_code == 404


async def test_from_url(client, auth_headers):
    with respx.mock:
        respx.get("https://example.com/data.csv").mock(
            return_value=httpx.Response(
                200,
                content=(FIXTURES / "hr.csv").read_bytes(),
                headers={"Content-Type": "text/csv"},
            )
        )
        response = await client.post(
            "/api/datasets/from-url",
            headers=auth_headers,
            json={"url": "https://example.com/data.csv", "name": "Remote"},
        )
    assert response.status_code == 201, response.text
    assert response.json()["tables"] == ["data"]


async def test_connection_dataset_reattaches(client, auth_headers, tmp_path):
    path = tmp_path / "remote.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE widgets (id INTEGER, name TEXT)")
        connection.execute("INSERT INTO widgets VALUES (1, 'one')")
    response = await client.post(
        "/api/datasets/from-connection",
        headers=auth_headers,
        json={"uri": f"sqlite:///{path.as_posix()}", "name": "Remote DB"},
    )
    assert response.status_code == 201, response.text
    dataset = response.json()
    assert "remote_db.widgets" in dataset["tables"]
    preview = await client.get(
        f"/api/datasets/{dataset['id']}/preview",
        params={"table": "remote_db.widgets"},
        headers=auth_headers,
    )
    assert preview.status_code == 200, preview.text
    added = await client.post(
        f"/api/datasets/{dataset['id']}/sources",
        headers=auth_headers,
        files=[("files", ("hr.csv", (FIXTURES / "hr.csv").read_bytes(), "text/csv"))],
    )
    assert added.status_code == 200, added.text
    assert {"remote_db.widgets", "hr"} <= set(added.json()["tables"])
    schema_names = {table["name"] for table in added.json()["schema"]["tables"]}
    assert {"remote_db.widgets", "hr"} <= schema_names
    connections = await client.get("/api/connections", headers=auth_headers)
    assert "***" not in connections.text or "redacted_uri" in connections.text


async def test_delete_removes_storage(client, auth_headers, hr_dataset, app):
    from insightforge.config import get_settings

    me = (await client.get("/api/auth/me", headers=auth_headers)).json()
    path = (
        get_settings().storage_dir
        / "users"
        / me["id"]
        / "datasets"
        / hr_dataset["id"]
    )
    assert path.exists()
    response = await client.delete(f"/api/datasets/{hr_dataset['id']}", headers=auth_headers)
    assert response.status_code == 204
    assert not path.exists()
