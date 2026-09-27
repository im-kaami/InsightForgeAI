import asyncio
import html
import re

import pytest

SALES_CSV = b"""sale_id,order_id,sold_on,revenue,refund,cost,currency,status
p1,o1,2026-08-25,100,0,60,USD,paid
p2,o2,2026-08-27,200,20,100,USD,paid
c1,o3,2026-09-01,120,0,70,USD,paid
c2,o3,2026-09-02,80,10,40,USD,paid
c3,o4,2026-09-03,300,30,180,USD,paid
"""
DEFINITION = {
    "kind": "sales_margin_v1",
    "fact_table": "sales",
    "row_key": "sale_id",
    "order_id_column": "order_id",
    "date_column": "sold_on",
    "date_format": "%Y-%m-%d",
    "revenue_column": "revenue",
    "refunds": {"table": "sales", "column": "refund"},
    "refunds_confirmed_absent": False,
    "cost": {"table": "sales", "column": "cost"},
    "currency": "USD",
    "currency_column": "currency",
    "single_currency_confirmed": False,
    "display_decimals": 2,
    "joins": [],
    "filters": [],
    "business_notes": "",
}


async def _wait(client, headers, run_id):
    for _ in range(100):
        response = await client.get(f"/api/runs/{run_id}", headers=headers)
        if response.json()["status"] in {"completed", "failed"}:
            return response.json()
        await asyncio.sleep(0.1)
    raise AssertionError("report run did not finish")


async def test_review_confirm_and_verified_report_run(client, auth_headers, app):
    uploaded = await client.post(
        "/api/datasets/upload",
        headers=auth_headers,
        data={"name": "Sales", "review": "true"},
        files=[("files", ("sales.csv", SALES_CSV, "text/csv"))],
    )
    assert uploaded.status_code == 201, uploaded.text
    dataset = uploaded.json()
    assert dataset["current_version_id"] is None
    version_id = dataset["review_version_id"]
    version = await client.get(
        f"/api/datasets/{dataset['id']}/versions", headers=auth_headers
    )
    assert version.json()[0]["state"] == "draft"
    preview = await client.get(
        f"/api/datasets/{dataset['id']}/preview",
        params={"table": "sales", "version_id": version_id},
        headers=auth_headers,
    )
    assert len(preview.json()["rows"]) == 5
    session = await client.post(
        "/api/sessions", headers=auth_headers, json={"dataset_id": dataset["id"]}
    )
    blocked = await client.post(
        f"/api/sessions/{session.json()['id']}/runs",
        headers=auth_headers,
        json={"goal": "before review"},
    )
    assert blocked.status_code == 409
    confirmed = await client.post(
        f"/api/datasets/{dataset['id']}/versions/{version_id}/confirm",
        headers=auth_headers,
        json={"confirmed": True, "expected_current_version_id": None},
    )
    assert confirmed.status_code == 200, confirmed.text
    definition = await client.post(
        f"/api/datasets/{dataset['id']}/reports",
        headers=auth_headers,
        json={
            "name": "Weekly <script>alert(1)</script>",
            "definition": DEFINITION,
            "approved": True,
        },
    )
    assert definition.status_code == 201, definition.text
    report = await client.post(
        f"/api/datasets/{dataset['id']}/reports/{definition.json()['id']}/runs",
        headers=auth_headers,
        json={
            "version_id": version_id,
            "start_date": "2026-09-01",
            "end_date": "2026-09-07",
        },
    )
    assert report.status_code == 202, report.text
    result = await _wait(client, auth_headers, report.json()["id"])
    assert result["status"] == "completed", result
    assert result["verification_status"] == "checks_passed"
    assert result["dataset_version_id"] == version_id
    assert result["definition_id"] == definition.json()["id"]
    table = next(item for item in result["artifacts"] if item["type"] == "table")
    current = next(row for row in table["rows"] if row["period"] == "current")
    assert current["net_sales"] == "460.000000"
    assert current["margin_percent"] == "36.96"
    assert result["provenance"]["source_version_id"] == version_id
    markdown = await client.get(
        f"/api/runs/{result['id']}/report",
        headers=auth_headers,
        params={"format": "md"},
    )
    assert "## Evidence" in markdown.text
    assert "current.net_sales" in markdown.text
    assert "```sql" in markdown.text
    assert table["sql"] in markdown.text
    html_response = await client.get(
        f"/api/runs/{result['id']}/report",
        headers=auth_headers,
        params={"format": "html"},
    )
    assert "<script>" not in html_response.text
    codes = re.findall(r"<pre><code>(.*?)</code></pre>", html_response.text, re.DOTALL)
    assert html.unescape(codes[0]) == table["sql"]
    assert app.state.llm.calls == []


async def _approved_dataset(client, headers, content=SALES_CSV):
    dataset = (
        await client.post(
            "/api/datasets/upload",
            headers=headers,
            files=[("files", ("sales.csv", content, "text/csv"))],
        )
    ).json()
    definition = (
        await client.post(
            f"/api/datasets/{dataset['id']}/reports",
            headers=headers,
            json={"name": "Sales", "definition": DEFINITION, "approved": True},
        )
    ).json()
    return dataset, definition


async def _report(client, headers, dataset_id, definition_id, version_id):
    created = await client.post(
        f"/api/datasets/{dataset_id}/reports/{definition_id}/runs",
        headers=headers,
        json={
            "version_id": version_id,
            "start_date": "2026-09-01",
            "end_date": "2026-09-07",
        },
    )
    assert created.status_code == 202, created.text
    return await _wait(client, headers, created.json()["id"])


async def test_historical_and_replacement_versions_keep_frozen_results(
    client, auth_headers, app
):
    dataset, definition = await _approved_dataset(client, auth_headers)
    original = dataset["current_version_id"]
    changed = SALES_CSV.replace(
        b"c3,o4,2026-09-03,300,30,180", b"c3,o4,2026-09-03,600,30,180"
    )
    replacement = (
        await client.post(
            f"/api/datasets/{dataset['id']}/versions",
            headers=auth_headers,
            files=[("files", ("sales.csv", changed, "text/csv"))],
        )
    ).json()
    confirmed = await client.post(
        f"/api/datasets/{dataset['id']}/versions/{replacement['id']}/confirm",
        headers=auth_headers,
        json={"confirmed": True, "expected_current_version_id": original},
    )
    assert confirmed.status_code == 200
    old_result = await _report(
        client, auth_headers, dataset["id"], definition["id"], original
    )
    new_result = await _report(
        client, auth_headers, dataset["id"], definition["id"], replacement["id"]
    )
    old_table = next(item for item in old_result["artifacts"] if item["type"] == "table")
    new_table = next(item for item in new_result["artifacts"] if item["type"] == "table")
    old_current = next(row for row in old_table["rows"] if row["period"] == "current")
    new_current = next(row for row in new_table["rows"] if row["period"] == "current")
    assert old_current["net_sales"] == "460.000000"
    assert old_current["margin_percent"] == "36.96"
    assert new_current["net_sales"] == "760.000000"
    assert new_current["margin_percent"] == "61.84"
    assert old_result["provenance"]["source_version_id"] == original
    assert new_result["provenance"]["source_version_id"] == replacement["id"]
    assert app.state.llm.calls == []


async def test_version_and_report_resources_are_owner_scoped(client, auth_headers):
    draft = (
        await client.post(
            "/api/datasets/upload",
            headers=auth_headers,
            data={"review": "true"},
            files=[("files", ("sales.csv", SALES_CSV, "text/csv"))],
        )
    ).json()
    other = {"email": "report-other@example.com", "password": "long-enough-password"}
    await client.post("/api/auth/register", json=other)
    token = (await client.post("/api/auth/login", json=other)).json()["access_token"]
    other_headers = {"Authorization": f"Bearer {token}"}
    paths = [
        ("get", f"/api/datasets/{draft['id']}/versions", None),
        (
            "get",
            f"/api/datasets/{draft['id']}/preview?table=sales&version_id={draft['review_version_id']}",
            None,
        ),
        (
            "post",
            f"/api/datasets/{draft['id']}/versions/{draft['review_version_id']}/confirm",
            {"confirmed": True, "expected_current_version_id": None},
        ),
        ("get", f"/api/datasets/{draft['id']}/reports", None),
        (
            "post",
            f"/api/datasets/{draft['id']}/reports",
            {"name": "Alien", "definition": DEFINITION, "approved": True},
        ),
        (
            "post",
            f"/api/datasets/{draft['id']}/reports/missing/runs",
            {
                "version_id": draft["review_version_id"],
                "start_date": "2026-09-01",
                "end_date": "2026-09-07",
            },
        ),
    ]
    for method, path, body in paths:
        response = await client.request(method, path, headers=other_headers, json=body)
        assert response.status_code == 404, (path, response.text)


async def test_same_owner_cannot_cross_dataset_versions(client, auth_headers):
    first, definition = await _approved_dataset(client, auth_headers)
    second, _ = await _approved_dataset(client, auth_headers)
    response = await client.post(
        f"/api/datasets/{first['id']}/reports/{definition['id']}/runs",
        headers=auth_headers,
        json={
            "version_id": second["current_version_id"],
            "start_date": "2026-09-01",
            "end_date": "2026-09-07",
        },
    )
    assert response.status_code == 404


async def test_schema_drift_does_not_rebind_definition(client, auth_headers):
    dataset, definition = await _approved_dataset(client, auth_headers)
    drifted = SALES_CSV.replace(b",revenue,", b",amount,")
    replacement = (
        await client.post(
            f"/api/datasets/{dataset['id']}/versions",
            headers=auth_headers,
            files=[("files", ("sales.csv", drifted, "text/csv"))],
        )
    ).json()
    confirmed = await client.post(
        f"/api/datasets/{dataset['id']}/versions/{replacement['id']}/confirm",
        headers=auth_headers,
        json={
            "confirmed": True,
            "expected_current_version_id": dataset["current_version_id"],
        },
    )
    assert confirmed.status_code == 200
    rejected = await client.post(
        f"/api/datasets/{dataset['id']}/reports/{definition['id']}/runs",
        headers=auth_headers,
        json={
            "version_id": replacement["id"],
            "start_date": "2026-09-01",
            "end_date": "2026-09-07",
        },
    )
    assert rejected.status_code == 422
    assert "revenue" in rejected.text


@pytest.mark.parametrize(
    ("content", "check_code"),
    [
        (
            SALES_CSV.replace(
                b"c1,o3,2026-09-01,120,0,70,USD",
                b"c1,o3,2026-09-01,120,0,70,EUR",
            ),
            "currency_mismatch",
        ),
        (SALES_CSV.replace(b"c2,o3,2026-09-02", b"c1,o3,2026-09-02"), "duplicate_grain"),
    ],
)
async def test_invalid_verified_sources_are_blocked(
    client, auth_headers, content, check_code
):
    dataset, definition = await _approved_dataset(client, auth_headers, content)
    result = await _report(
        client,
        auth_headers,
        dataset["id"],
        definition["id"],
        dataset["current_version_id"],
    )
    assert result["status"] == "failed"
    assert result["verification_status"] == "blocked"
    assert any(check["code"] == check_code for check in result["provenance"]["checks"])
    assert not any(artifact["type"] == "table" for artifact in result["artifacts"])
