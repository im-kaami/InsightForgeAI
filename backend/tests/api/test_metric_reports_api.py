from sqlalchemy import select

from insightforge.db.models import Run
from insightforge.db.session import SessionLocal
from insightforge.services.runs import execute_run

from .test_metrics_api import REVENUE, _with_relationship

PERIOD = {"start_date": "2025-07-01", "end_date": "2025-12-31"}


async def _report(client, headers, dataset, body):
    response = await client.post(f"/api/datasets/{dataset['id']}/reports/metric", headers=headers, json=body)
    if response.status_code != 202:
        return response, None
    db = SessionLocal()
    try:
        run_id = db.scalar(select(Run.id).where(Run.id == response.json()["id"]))
    finally:
        db.close()
    execute_run(run_id)
    return response, (await client.get(f"/api/runs/{run_id}", headers=headers)).json()


async def test_a_checked_report_for_an_approved_metric(client, auth_headers):
    dataset = await _with_relationship(client, auth_headers)
    await client.put(
        f"/api/datasets/{dataset['id']}/metrics", headers=auth_headers, json={"metrics": [REVENUE]}
    )
    response, run = await _report(
        client, auth_headers, dataset, {**PERIOD, "metric": "revenue", "group_by": "segment"}
    )
    assert response.status_code == 202, response.text
    assert run["status"] == "completed", run["error"]
    assert run["verification_status"] == "checks_passed"
    assert run["goal"] == "Revenue by segment: 2025-07-01 to 2025-12-31"
    table = next(item for item in run["artifacts"] if item["type"] == "table")
    assert table["metric"]["name"] == "revenue" and table["metric"]["revision"] == 1
    assert table["rows"][0]["segment"] == "Total"
    provenance = run["provenance"]
    assert provenance["kind"] == "metric_report_v1" and provenance["engine_version"] == "metric_report_v1"
    assert provenance["metric"]["name"] == "revenue" and provenance["group_by"] == "segment"
    assert all(check["passed"] for check in provenance["checks"])
    assert run["token_usage"] == {} or not any(run["token_usage"].values())
    # A second report reuses the same session.
    _, again = await _report(client, auth_headers, dataset, {**PERIOD, "metric": "Revenue"})
    assert again["session_id"] == run["session_id"]


async def test_metric_reports_refuse_bad_requests(client, auth_headers):
    dataset = await _with_relationship(client, auth_headers)
    draft = {**REVENUE, "approved": False}
    await client.put(
        f"/api/datasets/{dataset['id']}/metrics", headers=auth_headers, json={"metrics": [draft]}
    )
    response, _ = await _report(client, auth_headers, dataset, {**PERIOD, "metric": "revenue"})
    assert response.status_code == 404
    await client.put(
        f"/api/datasets/{dataset['id']}/metrics", headers=auth_headers, json={"metrics": [REVENUE]}
    )
    bad_group = {**PERIOD, "metric": "revenue", "group_by": "status"}
    assert (await _report(client, auth_headers, dataset, bad_group))[0].status_code == 422
    backwards = {"start_date": "2025-12-31", "end_date": "2025-01-01", "metric": "revenue"}
    assert (await _report(client, auth_headers, dataset, backwards))[0].status_code == 422
    _, blocked = await _report(
        client,
        auth_headers,
        dataset,
        {"start_date": "2030-01-01", "end_date": "2030-01-31", "metric": "revenue"},
    )
    assert blocked["status"] == "failed" and blocked["verification_status"] == "blocked"
    assert "No rows match" in blocked["error"]


async def test_metric_reports_are_owner_scoped(client, auth_headers):
    dataset = await _with_relationship(client, auth_headers)
    credentials = {"email": "report-intruder@example.com", "password": "correct horse battery staple"}
    await client.post("/api/auth/register", json=credentials)
    token = (await client.post("/api/auth/login", json=credentials)).json()["access_token"]
    other = {"Authorization": f"Bearer {token}"}
    response, _ = await _report(client, other, dataset, {**PERIOD, "metric": "revenue"})
    assert response.status_code == 404
