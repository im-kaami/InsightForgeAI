from pathlib import Path

import pandas as pd
import pytest

from insightforge.core.catalog import DataCatalog
from insightforge.core.metrics import (
    Metric,
    MetricError,
    MetricFilter,
    MetricQuery,
    MetricSet,
    compile_query,
    definition_problems,
    mentioned_metrics,
    metrics_block,
    offline_query,
    suggest_metrics,
)
from insightforge.core.relationships import Relationship
from insightforge.core.sql_guard import guard_query

DATA = Path(__file__).resolve().parents[1] / "evals" / "data"
ORDERS_TO_CUSTOMERS = Relationship(
    from_table="orders", from_column="customer_id", to_table="customers", to_column="customer_id"
)


@pytest.fixture
def shop():
    catalog = DataCatalog()
    for table in ("orders", "customers"):
        path = (DATA / f"{table}.csv").as_posix()
        catalog.connection.execute(f"CREATE TABLE {table} AS SELECT * FROM read_csv_auto('{path}')")
    yield catalog, catalog.introspect()
    catalog.close()


@pytest.fixture
def frames():
    orders = pd.read_csv(DATA / "orders.csv", parse_dates=["order_date"])
    customers = pd.read_csv(DATA / "customers.csv")
    return orders, customers


def revenue(**changes) -> Metric:
    base = {
        "name": "revenue",
        "label": "Revenue",
        "unit": "GBP",
        "synonyms": ["sales", "turnover"],
        "table": "orders",
        "aggregation": "sum",
        "column": "amount",
        "filters": [MetricFilter(column="status", value="completed")],
        "date_column": "order_date",
        "dimensions": ["region", "category", "customers.segment"],
        "approved": True,
    }
    return Metric(**{**base, **changes})


def _run(catalog, compiled):
    return catalog.query(guard_query(compiled.sql).sql)


def test_grouped_total_matches_pandas(shop, frames):
    catalog, schema = shop
    orders, _ = frames
    compiled = compile_query(revenue(), MetricQuery(metric="revenue", group_by=["region"]), schema)
    result = _run(catalog, compiled).set_index("region")["revenue"]
    expected = orders[orders["status"] == "completed"].groupby("region")["amount"].sum()
    pd.testing.assert_series_equal(result.sort_index(), expected.sort_index(), check_names=False)
    assert list(result.values) == sorted(result.values, reverse=True)  # largest first
    assert "status equals completed" in compiled.description


def test_monthly_totals_with_a_date_range_match_pandas(shop, frames):
    catalog, schema = shop
    orders, _ = frames
    query = MetricQuery(metric="revenue", grain="month", date_from="2025-02-01", date_to="2025-05-01")
    result = _run(catalog, compile_query(revenue(), query, schema))
    window = orders[
        (orders["status"] == "completed")
        & (orders["order_date"] >= "2025-02-01")
        & (orders["order_date"] < "2025-05-01")
    ]
    expected = window.groupby(window["order_date"].dt.to_period("M"))["amount"].sum()
    assert [str(value)[:7] for value in result["month"]] == ["2025-02", "2025-03", "2025-04"]
    assert result["revenue"].tolist() == pytest.approx(expected.tolist())


def test_groupings_from_another_table_use_the_approved_relationship(shop, frames):
    catalog, schema = shop
    orders, customers = frames
    query = MetricQuery(metric="revenue", group_by=["segment"])
    compiled = compile_query(revenue(), query, schema, [ORDERS_TO_CUSTOMERS])
    result = _run(catalog, compiled).set_index("segment")["revenue"]
    merged = orders[orders["status"] == "completed"].merge(customers, on="customer_id", how="left")
    expected = merged.groupby("segment")["amount"].sum()
    pd.testing.assert_series_equal(result.sort_index(), expected.sort_index(), check_names=False)
    assert result.sum() == pytest.approx(orders.loc[orders["status"] == "completed", "amount"].sum())
    with pytest.raises(MetricError, match="approved relationship"):
        compile_query(revenue(), query, schema, [])
    # A one-to-many direction (customers -> orders) is never used: it would repeat customer rows.
    customers_metric = Metric(
        name="customers_total",
        table="customers",
        aggregation="count",
        dimensions=["orders.region"],
        approved=True,
    )
    assert definition_problems([customers_metric], schema, [ORDERS_TO_CUSTOMERS])


def test_filters_counts_and_quoted_values(shop, frames):
    catalog, schema = shop
    orders, _ = frames
    metric = Metric(
        name="orders_placed",
        table="orders",
        aggregation="count",
        filters=[MetricFilter(column="status", op="not_equals", value="cancelled")],
        dimensions=["region", "category"],
        approved=True,
    )
    query = MetricQuery(
        metric="orders_placed",
        filters=[MetricFilter(column="region", op="in", value=["West", "East"])],
    )
    total = _run(catalog, compile_query(metric, query, schema)).iat[0, 0]
    expected = orders[(orders["status"] != "cancelled") & orders["region"].isin(["West", "East"])]
    assert total == len(expected)
    sneaky = MetricQuery(
        metric="orders_placed", filters=[MetricFilter(column="region", value="O'Brien'; DROP")]
    )
    compiled = compile_query(metric, sneaky, schema)
    assert _run(catalog, compiled).iat[0, 0] == 0
    customers = Metric(
        name="buyers", table="orders", aggregation="count_distinct", column="customer_id", approved=True
    )
    distinct = _run(catalog, compile_query(customers, MetricQuery(metric="buyers"), schema)).iat[0, 0]
    assert distinct == orders["customer_id"].nunique()


def test_requests_outside_the_definition_are_refused(shop):
    _, schema = shop
    with pytest.raises(MetricError, match="cannot be grouped"):
        compile_query(revenue(), MetricQuery(metric="revenue", group_by=["status"]), schema)
    no_date = revenue(date_column=None)
    with pytest.raises(MetricError, match="no date column"):
        compile_query(no_date, MetricQuery(metric="revenue", grain="week"), schema)
    problems = definition_problems(
        [revenue(column="region"), revenue(name="x", table="missing")], schema
    )
    assert any("needs a number column" in item for item in problems)
    assert any("table missing" in item for item in problems)
    with pytest.raises(ValueError):
        MetricQuery(metric="revenue", date_from="last week")
    with pytest.raises(ValueError):
        MetricSet(metrics=[revenue(), revenue()])
    with pytest.raises(ValueError):
        Metric(name="1bad", table="orders", aggregation="count")


def test_cues_offline_requests_and_prompt_text():
    metrics = [revenue(), revenue(name="draft_metric", synonyms=[], label="Draft", approved=False)]
    assert [m.name for m in mentioned_metrics("What were total sales by region?", metrics)] == ["revenue"]
    assert mentioned_metrics("What is the wholesales figure?", metrics) == []
    assert mentioned_metrics("Show the draft metric", metrics) == []  # drafts are never used
    query = offline_query("What was revenue by region per month?", revenue())
    assert query.group_by == ["region"] and query.grain == "month"
    assert offline_query("Revenue for each customer segment", revenue()).group_by == ["customers.segment"]
    shared = metrics_block(metrics, show_values=True)
    hidden = metrics_block(metrics, show_values=False)
    assert "status equals completed" in shared and "draft_metric" not in shared
    assert "completed" not in hidden and "a fixed filter on status" in hidden
    assert metrics_block([], show_values=True) == ""


def test_suggestions_come_from_types_and_skip_existing(shop):
    _, schema = shop
    suggestions = suggest_metrics(schema, existing=[revenue(name="total_amount")])
    names = [item.name for item in suggestions]
    assert "orders_count" in names and "total_amount" not in names
    assert all(not item.approved for item in suggestions)
    assert not definition_problems(suggestions, schema)
    orders_count = next(item for item in suggestions if item.name == "orders_count")
    assert orders_count.date_column == "order_date"
    assert "region" in orders_count.dimensions and "customer_id" not in orders_count.dimensions
