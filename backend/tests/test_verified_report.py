from decimal import Decimal

import pandas as pd
import pytest

from insightforge.core.catalog import DataCatalog
from insightforge.core.verified_report import (
    ReportPeriod,
    ReportValidationError,
    SalesDefinition,
    calculate_sales_report,
)


@pytest.fixture
def sales_catalog():
    catalog = DataCatalog()
    catalog.register_df(
        "sales",
        pd.DataFrame(
            {
                "sale_id": ["p1", "p2", "c1", "c2", "c3"],
                "order_id": ["o1", "o2", "o3", "o3", "o4"],
                "sold_on": ["2026-08-25", "2026-08-27", "2026-09-01", "2026-09-02", "2026-09-03"],
                "revenue": ["100", "200", "120", "80", "300"],
                "refund": ["0", "20", "0", "10", "30"],
                "cost": ["60", "100", "70", "40", "180"],
                "currency": ["USD"] * 5,
                "status": ["paid"] * 5,
            }
        ),
    )
    yield catalog
    catalog.close()


@pytest.fixture
def definition():
    return SalesDefinition(
        fact_table="sales",
        row_key="sale_id",
        order_id_column="order_id",
        date_column="sold_on",
        revenue_column="revenue",
        refunds={"table": "sales", "column": "refund"},
        cost={"table": "sales", "column": "cost"},
        currency="USD",
        currency_column="currency",
    )


@pytest.fixture
def period():
    return ReportPeriod(start_date="2026-09-01", end_date="2026-09-07")


def test_exact_sales_metrics_and_evidence(sales_catalog, definition, period):
    result = calculate_sales_report(sales_catalog, definition, period)
    current, previous = result.rows
    assert current["period"] == "current"
    assert current["row_count"] == 3
    assert current["order_count"] == 2
    assert Decimal(current["gross_sales"]) == Decimal("500")
    assert Decimal(current["refunds"]) == Decimal("40")
    assert Decimal(current["net_sales"]) == Decimal("460")
    assert Decimal(current["cost"]) == Decimal("290")
    assert Decimal(current["gross_profit"]) == Decimal("170")
    assert current["margin_percent"] == "36.96"
    assert Decimal(previous["net_sales"]) == Decimal("280")
    assert previous["margin_percent"] == "42.86"
    assert result.comparison["net_sales_change_percent"] == "64.29"
    assert Decimal(result.comparison["net_sales_change"]) == Decimal("180")
    claim = next(item for item in result.evidence if item.id == "current.net_sales")
    assert claim.row == 0 and claim.column == "net_sales" and claim.value == current["net_sales"]
    assert "460.00" in result.summary and "current.net_sales" in result.summary
    assert result.verification == "checks_passed"
    assert all(check.passed for check in result.checks)


@pytest.mark.parametrize("value", [None, "", "oops", "NaN", "Infinity", "12,345", "$12", "1.1234567"])
def test_invalid_revenue_is_blocked(sales_catalog, definition, period, value):
    sales_catalog.connection.execute("UPDATE sales SET revenue = ? WHERE sale_id = 'c1'", [value])
    with pytest.raises(ReportValidationError) as error:
        calculate_sales_report(sales_catalog, definition, period)
    assert any(check.code == "invalid_revenue" for check in error.value.checks)


@pytest.mark.parametrize("value", [None, "", "2026-02-30", "01/09/2026", "not-a-date"])
def test_ambiguous_or_invalid_dates_are_blocked(sales_catalog, definition, period, value):
    sales_catalog.connection.execute("UPDATE sales SET sold_on = ? WHERE sale_id = 'c1'", [value])
    with pytest.raises(ReportValidationError) as error:
        calculate_sales_report(sales_catalog, definition, period)
    assert any(check.code == "invalid_dates" for check in error.value.checks)


@pytest.mark.parametrize("value", [None, "EUR", "", "USD/EUR"])
def test_currency_mismatch_is_blocked(sales_catalog, definition, period, value):
    sales_catalog.connection.execute("UPDATE sales SET currency = ? WHERE sale_id = 'c1'", [value])
    with pytest.raises(ReportValidationError) as error:
        calculate_sales_report(sales_catalog, definition, period)
    assert any(check.code == "currency_mismatch" for check in error.value.checks)


def test_duplicate_grain_is_blocked(sales_catalog, definition, period):
    sales_catalog.connection.execute("UPDATE sales SET sale_id = 'c1' WHERE sale_id = 'c2'")
    with pytest.raises(ReportValidationError) as error:
        calculate_sales_report(sales_catalog, definition, period)
    assert any(check.code == "duplicate_grain" for check in error.value.checks)


def test_negative_refunds_require_sign_confirmation(sales_catalog, definition, period):
    sales_catalog.connection.execute("UPDATE sales SET refund = '-10' WHERE sale_id = 'c1'")
    with pytest.raises(ReportValidationError) as error:
        calculate_sales_report(sales_catalog, definition, period)
    assert any(check.code == "negative_refunds" for check in error.value.checks)


def test_zero_net_sales_has_no_invented_margin(sales_catalog, definition, period):
    sales_catalog.connection.execute("UPDATE sales SET refund = revenue")
    result = calculate_sales_report(sales_catalog, definition, period)
    assert result.rows[0]["margin_percent"] is None
    assert result.comparison["net_sales_change_percent"] is None
    assert result.verification == "needs_review"


def test_approved_one_to_one_cost_join(sales_catalog, definition, period):
    sales_catalog.register_df(
        "costs", pd.DataFrame({"id": ["p1", "p2", "c1", "c2", "c3"], "amount": [60, 100, 70, 40, 180]})
    )
    payload = definition.model_dump()
    payload.update(
        cost={"table": "costs", "column": "amount"},
        joins=[{"table": "costs", "fact_key": "sale_id", "lookup_key": "id"}],
    )
    definition = SalesDefinition.model_validate(payload)
    result = calculate_sales_report(sales_catalog, definition, period)
    assert Decimal(result.rows[0]["cost"]) == Decimal("290")
    assert "LEFT JOIN" in result.sql


@pytest.mark.parametrize("ids", [["c1", "c1"], ["c1", "c2"]])
def test_duplicate_or_unmatched_join_keys_block(sales_catalog, definition, period, ids):
    sales_catalog.register_df("costs", pd.DataFrame({"id": ids, "amount": [70, 40]}))
    payload = definition.model_dump()
    payload.update(cost={"table": "costs", "column": "amount"})
    payload["joins"] = [{"table": "costs", "fact_key": "sale_id", "lookup_key": "id"}]
    with pytest.raises(ReportValidationError):
        calculate_sales_report(sales_catalog, SalesDefinition.model_validate(payload), period)


def test_filters_are_explicit_and_injection_safe(sales_catalog, definition, period):
    payload = definition.model_dump()
    payload["filters"] = [{"column": "status", "operator": "equals", "value": "paid' OR 1=1 --"}]
    with pytest.raises(ReportValidationError) as error:
        calculate_sales_report(sales_catalog, SalesDefinition.model_validate(payload), period)
    assert any(check.code == "no_current_rows" for check in error.value.checks)
    assert sales_catalog.query("SELECT COUNT(*) AS n FROM sales").iloc[0, 0] == 5


def test_missing_column_is_blocked_before_query(sales_catalog, definition, period):
    definition = definition.model_copy(update={"revenue_column": "missing"})
    with pytest.raises(ReportValidationError, match="column"):
        calculate_sales_report(sales_catalog, definition, period)


def test_previous_period_without_rows_is_not_zero_growth(sales_catalog, definition, period):
    sales_catalog.connection.execute("DELETE FROM sales WHERE sale_id IN ('p1', 'p2')")
    result = calculate_sales_report(sales_catalog, definition, period)
    assert result.rows[1]["row_count"] == 0
    assert result.comparison["net_sales_change_percent"] is None
    assert result.verification == "needs_review"


def test_definition_requires_explicit_assumptions(definition):
    payload = definition.model_dump()
    payload.update(refunds=None, currency_column=None)
    with pytest.raises(ValueError):
        SalesDefinition.model_validate(payload)
    payload.update(refunds_confirmed_absent=True, single_currency_confirmed=True)
    assert SalesDefinition.model_validate(payload).currency == "USD"


def test_explicit_day_first_dates(sales_catalog, definition, period):
    sales_catalog.connection.execute("UPDATE sales SET sold_on = strftime(CAST(sold_on AS DATE), '%d/%m/%Y')")
    result = calculate_sales_report(
        sales_catalog, definition.model_copy(update={"date_format": "%d/%m/%Y"}), period
    )
    assert Decimal(result.rows[0]["net_sales"]) == Decimal("460")


def test_report_rerun_is_numerically_identical(sales_catalog, definition, period):
    first = calculate_sales_report(sales_catalog, definition, period)
    second = calculate_sales_report(sales_catalog, definition, period)
    assert first.rows == second.rows
    assert first.evidence == second.evidence
    assert first.sql == second.sql


def test_large_decimal_is_not_rounded_through_float(sales_catalog, definition, period):
    sales_catalog.connection.execute("UPDATE sales SET revenue = '9007199254740993.01' WHERE sale_id = 'c1'")
    result = calculate_sales_report(sales_catalog, definition, period)
    assert Decimal(result.rows[0]["gross_sales"]) == Decimal("9007199254741373.01")


@pytest.mark.parametrize("start,end", [("2026-09-08", "2026-09-01"), ("2020-01-01", "2026-09-01")])
def test_invalid_periods_are_rejected(start, end):
    with pytest.raises(ValueError):
        ReportPeriod(start_date=start, end_date=end)
