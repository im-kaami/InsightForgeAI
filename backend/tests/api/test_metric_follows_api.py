from datetime import date

import pandas as pd
import pytest

from insightforge.core.catalog import DataCatalog
from insightforge.core.metric_report import latest_date, rolling_period
from insightforge.services import metric_reports

from ..test_metric_planning import DATA, REVENUE
from .test_metrics_api import REVENUE as REVENUE_BODY
from .test_metrics_api import _with_relationship


def _completed() -> pd.DataFrame:
    orders = pd.read_csv(DATA / "orders.csv")
    return orders[orders["status"] == "completed"]


LATEST = pd.to_datetime(_completed()["order_date"]).max().date()


def _expected_change(days: int) -> tuple[float, float, float]:
    orders = _completed()
    when = pd.to_datetime(orders["order_date"])
    period = rolling_period(LATEST, days)
    current = orders[(when >= str(period.start_date)) & (when <= str(period.end_date))]["amount"].sum()
    previous = orders[(when >= str(period.previous_start)) & (when <= str(period.previous_end))][
        "amount"
    ].sum()
    return current, previous, round((current - previous) / previous * 100, 2)


def test_the_window_ends_on_the_latest_date_in_the_data():
    shop = DataCatalog()
    path = (DATA / "orders.csv").as_posix()
    shop.connection.execute(f"CREATE TABLE orders AS SELECT * FROM read_csv_auto('{path}')")
    shop.lock()
    # The latest completed order, not the latest row: the metric's fixed condition applies.
    assert latest_date(shop, REVENUE, shop.introspect()) == LATEST == date(2025, 12, 29)
    assert latest_date(shop, REVENUE.model_copy(update={"date_column": None}), shop.introspect()) is None
    shop.close()
    period = rolling_period(date(2025, 12, 31), 30)
    assert (period.start_date, period.previous_start, period.previous_end) == (
        date(2025, 12, 2),
        date(2025, 11, 2),
        date(2025, 12, 1),
    )


async def _follow(client, headers, **overrides):
    dataset = await _with_relationship(client, headers)
    await client.put(
        f"/api/datasets/{dataset['id']}/metrics", headers=headers, json={"metrics": [REVENUE_BODY]}
    )
    body = {"metric": "revenue", "days": 30, "threshold_percent": 10, **overrides}
    response = await client.post(f"/api/datasets/{dataset['id']}/follows", headers=headers, json=body)
    return dataset, response


async def test_checking_a_followed_metric_records_the_change_and_alerts(client, auth_headers):
    dataset, response = await _follow(client, auth_headers, threshold_percent=1)
    assert response.status_code == 201, response.text
    follow = response.json()
    assert follow["cron"] is None and follow["on_new_data"] is True and follow["open_alerts"] == 0
    check = await client.post(f"/api/follows/{follow['id']}/check", headers=auth_headers)
    assert check.status_code == 200, check.text
    body = check.json()
    current, previous, change = _expected_change(30)
    assert body["status"] == "completed" and body["trigger"] == "manual"
    assert body["current"] == pytest.approx(current) and body["previous"] == pytest.approx(previous)
    assert body["change_percent"] == pytest.approx(change)
    assert body["alert"] is bool(abs(change) >= 1)
    assert f"the 30 days to {LATEST.isoformat()}" in body["message"] and "alert threshold" in body["message"]
    assert body["session_id"] and body["run_id"] and body["metric_label"] == "Revenue"
    run = (await client.get(f"/api/runs/{body['run_id']}", headers=auth_headers)).json()
    assert run["provenance"]["kind"] == "metric_report_v1"
    alerts = (await client.get("/api/metric-alerts", headers=auth_headers)).json()
    assert [item["id"] for item in alerts] == ([body["id"]] if body["alert"] else [])
    if body["alert"]:
        acknowledged = await client.post(
            f"/api/follows/{follow['id']}/checks/{body['id']}/acknowledge", headers=auth_headers
        )
        assert acknowledged.json()["acknowledged_at"]
        assert (await client.get("/api/metric-alerts", headers=auth_headers)).json() == []
    listed = (await client.get(f"/api/datasets/{dataset['id']}/follows", headers=auth_headers)).json()
    assert listed[0]["last_check"]["id"] == body["id"]


async def test_small_changes_do_not_alert_and_failed_checks_do(client, auth_headers):
    dataset, response = await _follow(client, auth_headers, threshold_percent=1000)
    follow = response.json()
    quiet = (await client.post(f"/api/follows/{follow['id']}/check", headers=auth_headers)).json()
    assert quiet["status"] == "completed" and quiet["alert"] is False
    assert "below the 1000% alert threshold" in quiet["message"]
    # Removing the metric makes the next check fail, which is itself an alert.
    await client.put(f"/api/datasets/{dataset['id']}/metrics", headers=auth_headers, json={"metrics": []})
    failed = (await client.post(f"/api/follows/{follow['id']}/check", headers=auth_headers)).json()
    assert failed["status"] == "failed" and failed["alert"] is True
    assert "No approved metric" in failed["message"]


async def test_new_data_triggers_a_check(client, auth_headers, monkeypatch):
    calls = []
    monkeypatch.setattr(metric_reports, "start_background", lambda target, *args: calls.append(args))
    dataset, response = await _follow(client, auth_headers)
    from insightforge.db.models import Dataset
    from insightforge.db.session import SessionLocal

    db = SessionLocal()
    try:
        metric_reports.on_new_version(db, dataset["id"])
        assert calls == [(dataset["id"],)]
        follow = response.json()
        await client.put(
            f"/api/follows/{follow['id']}",
            headers=auth_headers,
            json={"metric": "revenue", "on_new_data": False},
        )
        calls.clear()
        metric_reports.on_new_version(db, dataset["id"])
        assert calls == []
        metric_reports._check_new_data(dataset["id"])  # runs nothing now that new data is off
        assert db.get(Dataset, dataset["id"]) is not None
    finally:
        db.close()


async def test_follow_requests_are_validated_and_owner_scoped(client, auth_headers):
    dataset, response = await _follow(client, auth_headers, cron="not a cron")
    assert response.status_code == 422
    base = f"/api/datasets/{dataset['id']}/follows"
    assert (await client.post(base, headers=auth_headers, json={"metric": "nope"})).status_code == 404
    bad_group = {"metric": "revenue", "group_by": "status"}
    assert (await client.post(base, headers=auth_headers, json=bad_group)).status_code == 422
    scheduled = await client.post(base, headers=auth_headers, json={"metric": "revenue", "cron": "0 8 * * 1"})
    assert scheduled.status_code == 201 and scheduled.json()["next_run_at"]
    follow_id = scheduled.json()["id"]
    credentials = {"email": "follow-intruder@example.com", "password": "correct horse battery staple"}
    await client.post("/api/auth/register", json=credentials)
    token = (await client.post("/api/auth/login", json=credentials)).json()["access_token"]
    other = {"Authorization": f"Bearer {token}"}
    assert (await client.get(base, headers=other)).status_code == 404
    assert (await client.post(f"/api/follows/{follow_id}/check", headers=other)).status_code == 404
    assert (await client.delete(f"/api/follows/{follow_id}", headers=other)).status_code == 404
    assert (await client.get("/api/metric-alerts", headers=other)).json() == []
    assert (await client.delete(f"/api/follows/{follow_id}", headers=auth_headers)).status_code == 204
    assert (await client.get(f"/api/follows/{follow_id}/checks", headers=auth_headers)).status_code == 404
