from datetime import date

import pandas as pd
import pytest

from insightforge.core.catalog import DataCatalog
from insightforge.core.metric_report import calculate_metric_report
from insightforge.core.metrics import MetricError
from insightforge.core.verified_report import ReportPeriod, ReportValidationError

from .test_metric_planning import DATA, RELATIONSHIPS, REVENUE, orders, shop  # noqa: F401

PERIOD = ReportPeriod(start_date=date(2025, 7, 1), end_date=date(2025, 12, 31))


def _expected(frame: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    days = pd.to_datetime(frame["order_date"])
    keep = (frame["status"] == "completed") & (days >= start) & (days <= end)
    return frame[keep]


def test_totals_and_groups_match_pandas(shop, orders):  # noqa: F811
    result = calculate_metric_report(
        shop, REVENUE, PERIOD, shop.introspect(), RELATIONSHIPS, group_by="segment", revision=2
    )
    customers = pd.read_csv(DATA / "customers.csv")
    current = _expected(orders, "2025-07-01", "2025-12-31").merge(customers, on="customer_id")
    previous = _expected(orders, "2025-01-01", "2025-06-30").merge(customers, on="customer_id")
    total = result.rows[0]
    assert result.columns == ["segment", "current", "previous", "change", "change_percent"]
    assert total["segment"] == "Total"
    assert total["current"] == pytest.approx(current["amount"].sum())
    assert total["previous"] == pytest.approx(previous["amount"].sum())
    assert total["change"] == pytest.approx(current["amount"].sum() - previous["amount"].sum())
    assert total["change_percent"] == pytest.approx(
        round((current["amount"].sum() / previous["amount"].sum() - 1) * 100, 2)
    )
    by_segment = current.groupby("segment")["amount"].sum().to_dict()
    assert {row["segment"]: row["current"] for row in result.rows[1:]} == pytest.approx(by_segment)
    assert result.verification == "checks_passed" and result.warnings == []
    assert {check.code for check in result.checks} >= {"invalid_dates", "join_lookup_unique", "join_matches"}
    assert "Evidence: `0.current`" in result.summary and "revision 2" in result.summary
    assert any(item.id == "0.change_percent" for item in result.evidence)


def test_missing_previous_rows_and_values_need_review():
    catalog = DataCatalog()
    catalog.connection.execute(
        "CREATE TABLE orders AS SELECT * FROM (VALUES "
        "(1, DATE '2025-03-02', 'West', 10.0, 'completed'), "
        "(2, DATE '2025-03-05', 'East', NULL, 'completed'), "
        "(3, NULL, 'East', 5.0, 'completed')) "
        "AS t(order_id, order_date, region, amount, status)"
    )
    catalog.lock()
    metric = REVENUE.model_copy(update={"dimensions": ["region"]})
    period = ReportPeriod(start_date=date(2025, 3, 1), end_date=date(2025, 3, 31))
    result = calculate_metric_report(catalog, metric, period, catalog.introspect(), group_by="region")
    assert result.rows[0]["current"] == 10 and result.rows[0]["previous"] is None
    assert result.rows[0]["change"] is None
    assert result.verification == "needs_review"
    assert any("No rows in the previous period" in item for item in result.warnings)
    assert any("Rows with no amount" in item for item in result.warnings)
    assert any("Rows without a date" in item for item in result.warnings)
    catalog.close()


def test_bad_dates_drafts_and_empty_periods_are_refused(shop):  # noqa: F811
    catalog = DataCatalog()
    catalog.connection.execute(
        "CREATE TABLE orders AS SELECT * FROM (VALUES (1, 'not a date', 'West', 1.0, 'completed')) "
        "AS t(order_id, order_date, region, amount, status)"
    )
    catalog.lock()
    with pytest.raises(ReportValidationError, match="cannot be read as dates"):
        calculate_metric_report(catalog, REVENUE, PERIOD, catalog.introspect())
    catalog.close()
    with pytest.raises(MetricError, match="draft"):
        calculate_metric_report(
            shop, REVENUE.model_copy(update={"approved": False}), PERIOD, shop.introspect()
        )
    with pytest.raises(MetricError, match="no date column"):
        calculate_metric_report(
            shop, REVENUE.model_copy(update={"date_column": None}), PERIOD, shop.introspect()
        )
    empty = ReportPeriod(start_date=date(2030, 1, 1), end_date=date(2030, 1, 31))
    with pytest.raises(ReportValidationError, match="No rows match"):
        calculate_metric_report(shop, REVENUE, empty, shop.introspect())
    with pytest.raises(MetricError, match="cannot be grouped"):
        calculate_metric_report(shop, REVENUE, PERIOD, shop.introspect(), group_by="status")
