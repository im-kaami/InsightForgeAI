import json

import pandas as pd

from insightforge.core.agent import InsightForgeAgent
from insightforge.core.artifacts import TableArtifact
from insightforge.core.catalog import DataCatalog
from insightforge.core.checks import SERIOUS_FINDINGS
from insightforge.core.executor import Executor
from insightforge.core.llm import FakeLLMClient
from insightforge.core.planner import Plan, Planner, SqlStep, StatStep, SummaryStep
from insightforge.core.query_fixes import add_superlative_order, match_stored_values
from insightforge.core.summarizer import Summarizer

GROUPED = "SELECT category, SUM(amount) AS total FROM orders GROUP BY category"


def _agent_run(catalog, goal, query):
    plan = {
        "steps": [
            {"name": "answer", "action": "sql", "query": query},
            {"name": "summary", "action": "summary"},
        ]
    }

    def reply(messages):
        if "data-analysis planner" in messages[0]["content"]:
            return json.dumps(plan)
        return "Done."

    return InsightForgeAgent(FakeLLMClient(reply), privacy_mode="full").run(goal, catalog)


def _table(result):
    return next(item for item in result.artifacts if isinstance(item, TableArtifact))


def test_remote_filter_is_matched_to_the_stored_spelling(catalog):
    query = "SELECT COUNT(*) AS n FROM employees WHERE location = 'remote'"
    result = _agent_run(catalog, "How many employees work remotely?", query)
    table = _table(result)
    assert "'Remote'" in table.sql and table.rows[0]["n"] > 0
    expected = "Matched 'remote' to the stored value 'Remote' in employees.location"
    assert any(expected in item for item in result.assumptions)
    codes = [finding.code for finding in result.findings]
    assert "value_matched" in codes and not set(codes) & SERIOUS_FINDINGS
    assert any(event.step == "value_match:answer" for event in result.trace)


def test_ambiguous_or_missing_matches_are_left_alone():
    catalog = DataCatalog()
    catalog.register_df("t", pd.DataFrame({"place": ["remote", "REMOTE ", "Office"], "n": [1, 2, 3]}))
    try:
        assert match_stored_values("SELECT * FROM t WHERE place = 'Remote'", catalog, 5) is None
        assert match_stored_values("SELECT * FROM t WHERE place = 'nowhere'", catalog, 5) is None
        assert match_stored_values("SELECT * FROM t WHERE n = 5", catalog, 5) is None
        query = "SELECT COUNT(*) AS c FROM t WHERE place = 'Remote'"
        result = _agent_run(catalog, "How many are remote?", query)
        assert _table(result).rows[0]["c"] == 0
        assert "filter_matches_nothing" in [finding.code for finding in result.findings]
        assert not any("Matched" in item for item in result.assumptions)
    finally:
        catalog.close()


def test_exactly_one_case_insensitive_match_is_fixed():
    catalog = DataCatalog()
    catalog.register_df("t", pd.DataFrame({"place": [" Remote ", "Office"], "n": [1, 2]}))
    try:
        query, notes = match_stored_values("SELECT * FROM t WHERE place = 'REMOTE'", catalog, 5)
        assert "' Remote '" in query and "case and spaces differ" in notes[0]
    finally:
        catalog.close()


def test_superlative_questions_sort_the_aggregate():
    for goal, direction, word in (
        ("Which category has the highest total amount?", "DESC", "highest"),
        ("What is the most profitable category?", "DESC", "most"),
        ("Which category has the lowest total?", "ASC", "lowest"),
        ("Show the fewest orders per category", "ASC", "fewest"),
    ):
        fixed = add_superlative_order(GROUPED, goal)
        assert fixed is not None, goal
        sql, note = fixed
        assert sql.endswith(f"ORDER BY total {direction}")
        order = "descending" if direction == "DESC" else "ascending"
        assert note == f"Sorted the result by total {order} because the question asks for the {word}"


def test_superlative_sort_uses_a_position_for_unnamed_aggregates():
    query = "SELECT category, SUM(amount) FROM orders GROUP BY 1"
    sql, note = add_superlative_order(query, "top category")
    assert sql.endswith("ORDER BY 2 DESC") and "column 2" in note


def test_superlative_sort_is_skipped_when_unsafe():
    assert add_superlative_order(GROUPED, "Total amount per category") is None
    assert add_superlative_order(GROUPED, "highest and lowest category") is None
    assert add_superlative_order(GROUPED + " ORDER BY category", "highest category") is None
    assert add_superlative_order(GROUPED + " LIMIT 3", "highest category") is None
    assert add_superlative_order(
        "SELECT category, SUM(amount) AS a, AVG(amount) AS b FROM orders GROUP BY category", "highest"
    ) is None
    assert add_superlative_order("SELECT category, amount FROM orders", "highest") is None
    assert add_superlative_order("not sql at all (", "highest") is None
    assert add_superlative_order(GROUPED, "maximizing things") is None


def test_the_executor_applies_and_records_the_sort():
    catalog = DataCatalog()
    catalog.register_df(
        "orders", pd.DataFrame({"category": ["a", "b", "c", "a"], "amount": [10, 50, 30, 5]})
    )
    llm = FakeLLMClient(["Summary"])
    plan = Plan(steps=[SqlStep(name="top", query=GROUPED), SummaryStep(name="summary")])
    executor = Executor(catalog, Planner(llm), Summarizer(llm))
    try:
        state = executor.start("Which category has the highest total amount?", plan, catalog.introspect())
        executor.run_steps(state, plan.steps)
        table = next(item for item in state.artifacts if isinstance(item, TableArtifact))
        assert table.rows[0]["category"] == "b" and "ORDER BY" in table.sql
        assert state.assumptions == [
            "top: Sorted the result by total descending because the question asks for the highest"
        ]
        assert any(event.step == "sorted:top" for event in executor.tracer.events)
    finally:
        catalog.close()


def test_the_planning_and_review_prompts_ask_for_a_single_average_only_for_average_questions():
    llm = FakeLLMClient([json.dumps({"steps": [{"name": "summary", "action": "summary"}]})])
    catalog = DataCatalog()
    catalog.register_df("orders", pd.DataFrame({"customer_id": [1, 2], "amount": [1, 2]}))
    planner = Planner(llm, privacy_mode="full")
    try:
        planner.plan("On average, how many orders per customer?", catalog.introspect())
        assert "return the single average in one row" in llm.calls[-1][0]["content"]
        planner.plan("What share of all orders were refunded?", catalog.introspect())
        assert "single average in one row" not in llm.calls[-1][0]["content"]
        llm.responses = [json.dumps({"verdict": "answer", "reason": "ok"})]
        planner.review("Average orders per customer", catalog.introspect(), {}, [], 1)
        assert "single average in one row" in llm.calls[-1][0]["content"]
        planner.review("Which region is biggest", catalog.introspect(), {}, [], 1)
        assert "single average in one row" not in llm.calls[-1][0]["content"]
    finally:
        catalog.close()


def test_a_one_period_explain_change_gets_a_specific_repair_hint():
    catalog = DataCatalog()
    catalog.register_df(
        "orders",
        pd.DataFrame(
            {
                "half": ["H1", "H1", "H2", "H2"],
                "region": ["East", "West", "East", "West"],
                "orders": [3, 4, 5, 6],
            }
        ),
    )
    seen = []

    def reply(messages):
        seen.append(messages)
        if "Correct the DuckDB SQL" in messages[0]["content"]:
            return json.dumps({"query": "SELECT half, region, orders FROM orders"})
        return "Done."

    llm = FakeLLMClient(reply)
    plan = Plan(
        steps=[
            SqlStep(name="rows", query="SELECT half, region, orders FROM orders WHERE half = 'H1'"),
            StatStep(
                name="why",
                method="explain_change",
                data_source="rows",
                x="half",
                y="orders",
                by=["region"],
            ),
            SummaryStep(name="summary"),
        ]
    )
    executor = Executor(catalog, Planner(llm, privacy_mode="full"), Summarizer(llm))
    try:
        state = executor.start("Why did orders change?", plan, catalog.introspect())
        executor.run_steps(state, plan.steps)
    finally:
        catalog.close()
    repair = next(m for m in seen if "Correct the DuckDB SQL" in m[0]["content"])
    text = repair[1]["content"]
    assert "The column half must contain exactly two periods, but the result had only 1." in text
    assert "Remove WHERE conditions that keep only one period" in text
    assert "Return one row per record" not in text


def test_value_matching_waits_when_the_model_may_not_see_values(catalog):
    query = "SELECT COUNT(*) AS n FROM employees WHERE location = 'remote'"
    plan = {
        "steps": [
            {"name": "answer", "action": "sql", "query": query},
            {"name": "summary", "action": "summary"},
        ]
    }

    def reply(messages):
        if "data-analysis planner" in messages[0]["content"]:
            return json.dumps(plan)
        return "Done."

    result = InsightForgeAgent(FakeLLMClient(reply), privacy_mode="schema_only").run("Remote?", catalog)
    assert _table(result).rows[0]["n"] == 0
    assert not any("Matched" in item for item in result.assumptions)
