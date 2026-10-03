import json
from pathlib import Path

import pandas as pd
import pytest

from insightforge.core.agent import InsightForgeAgent
from insightforge.core.artifacts import TableArtifact
from insightforge.core.catalog import DataCatalog
from insightforge.core.llm import FakeLLMClient
from insightforge.core.metrics import Metric, MetricFilter
from insightforge.core.planner import Planner, SqlStep, validate_plan
from insightforge.core.relationships import Relationship

DATA = Path(__file__).resolve().parents[1] / "evals" / "data"
RELATIONSHIPS = [
    Relationship(
        from_table="orders", from_column="customer_id", to_table="customers", to_column="customer_id"
    )
]
REVENUE = Metric(
    name="revenue",
    label="Revenue",
    unit="GBP",
    synonyms=["sales"],
    table="orders",
    aggregation="sum",
    column="amount",
    filters=[MetricFilter(column="status", value="completed")],
    date_column="order_date",
    dimensions=["region", "category", "customers.segment"],
    approved=True,
)


@pytest.fixture
def shop():
    catalog = DataCatalog()
    for table in ("orders", "customers"):
        path = (DATA / f"{table}.csv").as_posix()
        catalog.connection.execute(f"CREATE TABLE {table} AS SELECT * FROM read_csv_auto('{path}')")
    catalog.lock()
    yield catalog
    catalog.close()


@pytest.fixture
def orders():
    return pd.read_csv(DATA / "orders.csv")


def _replies(choice: dict):
    def reply(messages):
        system = messages[0]["content"]
        if "value of one of the approved" in system:
            return json.dumps(choice)
        if "data-analysis planner" in system:
            return json.dumps({"steps": [{"name": "rows", "action": "sql", "query": "SELECT 1 AS one"}]})
        return "Done."

    return FakeLLMClient(reply)


def test_a_metric_question_is_answered_by_code_written_sql(shop, orders):
    llm = _replies({"metric": True, "name": "revenue", "group_by": ["segment"]})
    agent = InsightForgeAgent(llm, privacy_mode="full")
    result = agent.run(
        "What are sales by customer segment?",
        shop,
        relationships=RELATIONSHIPS,
        metrics=[REVENUE],
        metrics_revision=4,
    )
    table = next(item for item in result.artifacts if isinstance(item, TableArtifact))
    assert table.metric is not None and table.metric["name"] == "revenue"
    assert table.metric["revision"] == 4
    customers = pd.read_csv(DATA / "customers.csv")
    merged = orders[orders["status"] == "completed"].merge(customers, on="customer_id", how="left")
    expected = merged.groupby("segment")["amount"].sum().to_dict()
    assert {row["segment"]: row["revenue"] for row in table.rows} == pytest.approx(expected)
    assert result.assumptions[0].startswith("metric_revenue: approved metric (revision 4) Revenue = sum")
    assert any("metric_choice" == event.step for event in result.trace)


def test_periods_the_question_never_asked_for_are_dropped(shop):
    choice = {"metric": True, "name": "revenue", "grain": "day", "date_from": "2025-01-01"}
    planner = Planner(_replies(choice), privacy_mode="full")
    planner.metrics = [REVENUE]
    step = planner.plan("What is total revenue?", shop.introspect()).steps[0]
    assert step.metric is not None and "date_trunc" not in step.query and "WHERE" in step.query
    assert "order_date" not in step.query
    monthly = planner.plan("What is revenue per day in 2025?", shop.introspect()).steps[0]
    assert "date_trunc('day'" in monthly.query and "2025-01-01" in monthly.query


def test_questions_without_a_metric_name_never_ask_about_metrics(shop):
    llm = _replies({"metric": True, "name": "revenue"})
    InsightForgeAgent(llm, privacy_mode="full").run("How many customers are there?", shop, metrics=[REVENUE])
    assert llm.calls and all("value of one of the approved" not in call[0]["content"] for call in llm.calls)


def test_drafts_and_bad_requests_fall_back_to_normal_planning(shop):
    draft = REVENUE.model_copy(update={"approved": False})
    llm = _replies({"metric": True, "name": "revenue"})
    result = InsightForgeAgent(llm, privacy_mode="full").run("What is revenue?", shop, metrics=[draft])
    assert all(item.metric is None for item in result.artifacts if isinstance(item, TableArtifact))
    bad = _replies({"metric": True, "name": "revenue", "group_by": ["status"]})
    planner = Planner(bad, privacy_mode="full")
    planner.metrics = [REVENUE]
    plan = planner.plan("What is revenue by status?", shop.introspect())
    assert all(getattr(step, "metric", None) is None for step in plan.steps)
    assert any("cannot be grouped or filtered by status" in item for item in planner.last_plan_issues)


def test_a_model_cannot_label_its_own_sql_as_an_approved_metric(shop):
    raw = {"steps": [{"name": "x", "action": "sql", "query": "SELECT 1", "metric": {"name": "revenue"}}]}
    plan = validate_plan(raw, shop.introspect())
    assert isinstance(plan.steps[0], SqlStep) and plan.steps[0].metric is None


def test_schema_only_prompts_hide_fixed_filter_values(shop):
    llm = _replies({"metric": False})
    planner = Planner(llm, privacy_mode="schema_only")
    planner.metrics = [REVENUE]
    planner.plan("What is revenue?", shop.introspect())
    prompt = next(
        call[0]["content"] for call in llm.calls if "approved metrics" in call[0]["content"].lower()
    )
    assert "a fixed filter on status" in prompt
    assert "completed" not in prompt
    full = _replies({"metric": False})
    shared = Planner(full, privacy_mode="full")
    shared.metrics = [REVENUE]
    shared.plan("What is revenue?", shop.introspect())
    assert any("status equals completed" in call[0]["content"] for call in full.calls)


def test_offline_mode_answers_metric_questions_without_any_model(shop, orders):
    agent = InsightForgeAgent(FakeLLMClient(["unused"]), privacy_mode="local")
    result = agent.run("What is revenue by region?", shop, metrics=[REVENUE])
    table = next(item for item in result.artifacts if isinstance(item, TableArtifact))
    assert table.metric and table.metric["name"] == "revenue"
    expected = orders[orders["status"] == "completed"].groupby("region")["amount"].sum().to_dict()
    assert {row["region"]: row["revenue"] for row in table.rows} == pytest.approx(expected)
    assert not result.used_fallback_plan
