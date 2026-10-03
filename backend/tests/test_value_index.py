import json

from insightforge.core.agent import InsightForgeAgent
from insightforge.core.artifacts import TableArtifact
from insightforge.core.catalog import DataCatalog
from insightforge.core.llm import FakeLLMClient
from insightforge.core.metrics import offline_query
from insightforge.core.value_index import ValueIndex, ValueMatch, build_index, match_values

from .test_metric_planning import REVENUE, orders, shop  # noqa: F401 - shared fixtures

INDEX = ValueIndex(
    columns={
        "orders.region": ["East", "North", "South", "West"],
        "orders.category": ["Electronics", "Home", "Toys"],
        "orders.status": ["cancelled", "completed", "refunded"],
        "customers.segment": ["Business", "Consumer"],
        "stores.city": ["New York", "York"],
    }
)


def _found(goal: str) -> list[str]:
    return [f"{item.table}.{item.column}={item.value}" for item in match_values(goal, INDEX)]


def test_words_match_exact_values_despite_case_plurals_and_typos():
    assert _found("How many orders in the west?") == ["orders.region=West"]
    assert _found("refunded ELECTRONICS orders") == ["orders.status=refunded", "orders.category=Electronics"]
    assert _found("Sales of electronic goods") == ["orders.category=Electronics"]
    assert _found("revenue from businesses") == ["customers.segment=Business"]
    close = match_values("Electronnics sales", INDEX)
    assert close[0].value == "Electronics" and close[0].exact is False
    assert "(close spelling)" in close[0].describe()
    # The longer phrase wins; "york" inside "new york" is not matched again.
    assert _found("stores in New York") == ["stores.city=New York"]
    assert _found("What is total revenue?") == []
    assert match_values("anything", None) == []


def test_only_short_category_columns_are_indexed():
    catalog = DataCatalog()
    catalog.connection.execute(
        "CREATE TABLE t AS SELECT i AS row_id, 'note ' || i AS note, "
        "CASE WHEN i % 2 = 0 THEN 'even' ELSE 'odd' END AS parity, "
        "CAST(i AS VARCHAR) AS customer_id, i * 1.5 AS amount FROM range(600) AS r(i)"
    )
    catalog.lock()
    index = build_index(catalog, catalog.introspect())
    assert index.columns == {"t.parity": ["even", "odd"]}
    catalog.close()


def _planner_prompts(llm: FakeLLMClient) -> list[str]:
    return [call[0]["content"] for call in llm.calls if "data-analysis planner" in call[0]["content"]]


def _replies():
    def reply(messages):
        if "data-analysis planner" in messages[0]["content"]:
            return json.dumps(
                {"steps": [{"name": "rows", "action": "sql", "query": "SELECT COUNT(*) AS n FROM orders"}]}
            )
        return "Done."

    return FakeLLMClient(reply)


def test_matches_reach_the_prompt_only_when_values_may_be_shared(shop):  # noqa: F811
    goal = "How many orders came from the west?"
    full = _replies()
    result = InsightForgeAgent(full, privacy_mode="full").run(goal, shop)
    assert "\"west\" -> orders.region = 'West'" in _planner_prompts(full)[0]
    assert any("Words matched to stored values by code" in item for item in result.assumptions)
    assert any(event.step == "value_index" for event in result.trace)
    hidden = _replies()
    result = InsightForgeAgent(hidden, privacy_mode="schema_only").run(goal, shop)
    assert all(
        "'West'" not in prompt and "match values stored" not in prompt for prompt in _planner_prompts(hidden)
    )
    # The user still sees how the word was read; only the model is kept from the value.
    assert any("orders.region = 'West'" in item for item in result.assumptions)
    skipped = _replies()
    InsightForgeAgent(skipped, privacy_mode="full").run(goal, shop, value_index=False)
    assert "match values stored" not in _planner_prompts(skipped)[0]


def test_offline_metric_questions_filter_on_named_values(shop, orders):  # noqa: F811
    matches = [
        ValueMatch(table="orders", column="region", value="West", phrase="west"),
        ValueMatch(table="orders", column="status", value="refunded", phrase="refunded"),
        ValueMatch(table="customers", column="segment", value="Business", phrase="business"),
    ]
    query = offline_query("revenue in the west for business customers", REVENUE, matches)
    # status is fixed by the metric, so "refunded" is not added as a filter.
    assert [(item.column, item.value) for item in query.filters] == [
        ("region", "West"),
        ("customers.segment", "Business"),
    ]
    agent = InsightForgeAgent(FakeLLMClient(["unused"]), privacy_mode="local")
    result = agent.run("What is revenue in the west?", shop, metrics=[REVENUE])
    table = next(item for item in result.artifacts if isinstance(item, TableArtifact))
    expected = orders[(orders["status"] == "completed") & (orders["region"] == "West")]["amount"].sum()
    assert table.metric is not None
    assert abs(table.rows[0]["revenue"] - expected) < 1e-6
