import pytest

from .test_table_relationships_api import _upload_shop
from .test_workspaces import _team, _user


async def _upload_rows(client, headers, count, name="big"):
    lines = ["id,value"] + [f"{index},{index * 2}" for index in range(count)]
    response = await client.post(
        "/api/datasets/upload",
        headers=headers,
        files=[("files", (f"{name}.csv", "\n".join(lines).encode(), "text/csv"))],
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _run(client, headers, dataset, sql, **extra):
    return await client.post(
        f"/api/datasets/{dataset['id']}/sql", headers=headers, json={"sql": sql, **extra}
    )


async def test_select_returns_columns_rows_and_timing(client, auth_headers, hr_dataset):
    response = await _run(client, auth_headers, hr_dataset, "SELECT employee_id, department FROM hr")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["columns"] == ["employee_id", "department"]
    assert body["row_count"] == body["total_rows"] == len(body["rows"]) > 0
    assert body["rows"][0] == {"employee_id": 1, "department": "Engineering"}
    assert body["truncated"] is False and body["full_row_count"] is None
    assert isinstance(body["elapsed_ms"], int) and body["elapsed_ms"] >= 0
    assert "LIMIT" in body["sql"].upper()


async def test_aggregate_query(client, auth_headers, hr_dataset):
    response = await _run(
        client,
        auth_headers,
        hr_dataset,
        "SELECT department, COUNT(*) AS n FROM hr GROUP BY department ORDER BY department",
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["columns"] == ["department", "n"]
    assert sum(row["n"] for row in body["rows"]) > 0


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM hr",
        "DROP TABLE hr",
        "INSERT INTO hr VALUES (1)",
        "SELECT 1; SELECT 2",
        "SELECT * FROM read_csv('x.csv')",
    ],
)
async def test_unsafe_queries_are_refused(client, auth_headers, hr_dataset, sql):
    response = await _run(client, auth_headers, hr_dataset, sql)
    assert response.status_code == 422, response.text
    assert response.json()["detail"]


async def test_syntax_and_execution_errors_are_422(client, auth_headers, hr_dataset):
    syntax = await _run(client, auth_headers, hr_dataset, "SELEC * FRM hr")
    assert syntax.status_code == 422
    missing = await _run(client, auth_headers, hr_dataset, "SELECT * FROM nope")
    assert missing.status_code == 422
    assert missing.json()["detail"].startswith("The query failed:")


async def test_empty_and_oversized_sql_are_rejected(client, auth_headers, hr_dataset):
    assert (await _run(client, auth_headers, hr_dataset, "")).status_code == 422
    assert (await _run(client, auth_headers, hr_dataset, "SELECT 1 " + " " * 20000)).status_code == 422


async def test_more_than_1000_rows_are_cut_in_the_response_only(client, auth_headers):
    dataset = await _upload_rows(client, auth_headers, 1500)
    response = await _run(client, auth_headers, dataset, "SELECT * FROM big")
    body = response.json()
    assert response.status_code == 200, response.text
    assert body["row_count"] == len(body["rows"]) == 1000
    assert body["total_rows"] == 1500
    assert body["truncated"] is False and body["full_row_count"] is None


async def test_result_over_10000_rows_reports_truncation(client, auth_headers):
    dataset = await _upload_rows(client, auth_headers, 10500)
    body = (await _run(client, auth_headers, dataset, "SELECT * FROM big")).json()
    assert body["total_rows"] == 10000
    assert body["row_count"] == 1000
    assert body["truncated"] is True
    assert body["full_row_count"] == 10500
    own_limit = (await _run(client, auth_headers, dataset, "SELECT * FROM big LIMIT 10000")).json()
    assert own_limit["truncated"] is False and own_limit["total_rows"] == 10000


async def test_exactly_10000_rows_is_not_truncated(client, auth_headers):
    dataset = await _upload_rows(client, auth_headers, 10000)
    body = (await _run(client, auth_headers, dataset, "SELECT * FROM big")).json()
    assert body["total_rows"] == 10000 and body["truncated"] is False


async def test_version_pinning_and_foreign_versions(client, auth_headers):
    dataset = await _upload_rows(client, auth_headers, 5)
    version_id = dataset["current_version_id"]
    pinned = await _run(client, auth_headers, dataset, "SELECT COUNT(*) AS n FROM big", version_id=version_id)
    assert pinned.status_code == 200, pinned.text
    assert pinned.json()["rows"] == [{"n": 5}]
    other = await _user(client, "other@example.com")
    foreign = await _upload_rows(client, other, 3)
    refused = await _run(
        client, auth_headers, dataset, "SELECT * FROM big", version_id=foreign["current_version_id"]
    )
    assert refused.status_code == 404, refused.text


async def test_other_accounts_get_404(client, auth_headers, hr_dataset):
    other = await _user(client, "other@example.com")
    assert (await _run(client, other, hr_dataset, "SELECT 1")).status_code == 404
    csv = await client.post(
        f"/api/datasets/{hr_dataset['id']}/sql/csv", headers=other, json={"sql": "SELECT 1"}
    )
    assert csv.status_code == 404


async def test_workspace_viewer_can_run_queries(client, auth_headers):
    _, dataset, people = await _team(client, auth_headers)
    response = await _run(client, people["viewer"], dataset, "SELECT COUNT(*) AS n FROM orders")
    assert response.status_code == 200, response.text
    assert response.json()["rows"][0]["n"] > 0
    assert (await _run(client, people["viewer"], dataset, "DELETE FROM orders")).status_code == 422


async def test_csv_download(client, auth_headers):
    dataset = await _upload_shop(client, auth_headers)
    response = await client.post(
        f"/api/datasets/{dataset['id']}/sql/csv",
        headers=auth_headers,
        json={"sql": "SELECT customer_id FROM customers ORDER BY customer_id LIMIT 3"},
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert 'filename="query.csv"' in response.headers["content-disposition"]
    lines = response.text.strip().splitlines()
    assert lines[0] == "customer_id" and len(lines) == 4
    refused = await client.post(
        f"/api/datasets/{dataset['id']}/sql/csv", headers=auth_headers, json={"sql": "DROP TABLE orders"}
    )
    assert refused.status_code == 422
