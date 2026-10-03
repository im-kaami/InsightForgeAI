import json

import pytest
from pydantic import ValidationError

from insightforge.core.agent import InsightForgeAgent
from insightforge.core.artifacts import TableArtifact
from insightforge.core.llm import FakeLLMClient
from insightforge.core.planner import Planner, SqlStep, validate_plan
from insightforge.core.queries import (
    ApprovedQuery,
    QuerySet,
    match_query,
    match_score,
    queries_block,
    query_problems,
)

from .test_metric_planning import REVENUE, shop  # noqa: F401 - shared fixture

BY_REGION = ApprovedQuery(
    id="q1",
    question="What is completed revenue by region?",
    sql="SELECT region, SUM(amount) AS revenue FROM orders WHERE status = 'completed' GROUP BY region",
    approved=True,
)
TOP_5 = ApprovedQuery(
    id="q2",
    question="Top 5 customers by completed order amount",
    sql="SELECT customer_id, SUM(amount) AS total FROM orders WHERE status = 'completed' "
    "GROUP BY customer_id ORDER BY total DESC LIMIT 5",
    approved=True,
)


def _replies(choice: dict | None = None, plan_sql: str = "SELECT 1 AS one"):
    def reply(messages):
        system = messages[0]["content"]
        if "approved questions listed below" in system:
            return json.dumps(choice or {"query": None})
        if "data-analysis planner" in system:
            return json.dumps({"steps": [{"name": "rows", "action": "sql", "query": plan_sql}]})
        return "Done."

    return FakeLLMClient(reply)


def test_code_matching_accepts_rewording_and_refuses_different_questions():
    assert match_query("completed revenue by region?", [BY_REGION]) is BY_REGION
    assert match_query("Show me the completed revenue for each region", [BY_REGION]) is BY_REGION
    assert match_query("What is completed revenue by segment?", [BY_REGION]) is None
    # Different numbers never match, even when every word is shared.
    assert match_score("Top 10 customers by completed order amount", TOP_5.question) == 0
    assert match_query("Top 5 customers by completed order amount", [TOP_5]) is TOP_5
    draft = BY_REGION.model_copy(update={"approved": False})
    assert match_query(BY_REGION.question, [draft]) is None
    twin = BY_REGION.model_copy(update={"id": "q3"})
    assert match_query(BY_REGION.question, [BY_REGION, twin]) is None  # a tie is not a match


def test_only_one_read_only_query_can_be_saved():
    assert query_problems([BY_REGION]) == []
    bad = [
        BY_REGION.model_copy(update={"sql": "DELETE FROM orders"}),
        BY_REGION.model_copy(update={"sql": "SELECT 1; SELECT 2"}),
        BY_REGION.model_copy(update={"sql": "SELECT * FROM read_csv('x.csv')"}),
    ]
    assert len(query_problems(bad)) == 3
    with pytest.raises(ValidationError):
        QuerySet(queries=[BY_REGION, BY_REGION.model_copy(update={"id": "q9"})])


def test_prompt_lists_questions_but_never_the_sql():
    block = queries_block([BY_REGION, TOP_5.model_copy(update={"approved": False})])
    assert "q1: What is completed revenue by region?" in block
    assert "SELECT" not in block and "completed'" not in block and "q2" not in block


def test_a_matching_question_runs_the_approved_sql_unchanged(shop):  # noqa: F811
    llm = _replies()
    agent = InsightForgeAgent(llm, privacy_mode="full")
    result = agent.run(
        "Completed revenue for each region", shop, queries=[BY_REGION], queries_revision=3
    )
    table = next(item for item in result.artifacts if isinstance(item, TableArtifact))
    assert table.approved_query == {
        "id": "q1",
        "question": BY_REGION.question,
        "description": "",
        "revision": 3,
        "matched_by": "code",
    }
    assert "SUM(amount)" in table.sql and len(table.rows) >= 2
    assert any('approved query (revision 3) for "What is completed' in item for item in result.assumptions)
    assert all("data-analysis planner" not in call[0]["content"] for call in llm.calls)


def test_the_ai_may_pick_an_approved_question_by_id_only(shop):  # noqa: F811
    planner = Planner(_replies({"query": "q1"}), privacy_mode="full")
    planner.queries = [BY_REGION]
    step = planner.plan("How much did each area bring in from finished orders?", shop.introspect()).steps[0]
    assert step.approved_query["matched_by"] == "AI" and step.query == BY_REGION.sql
    unknown = Planner(_replies({"query": "nope"}), privacy_mode="full")
    unknown.queries = [BY_REGION]
    plan = unknown.plan("How much did each area bring in from finished orders?", shop.introspect())
    assert all(getattr(item, "approved_query", None) is None for item in plan.steps)
    assert any("unknown approved question" in item for item in unknown.last_plan_issues)


def test_a_model_cannot_label_its_own_sql_as_approved(shop):  # noqa: F811
    raw = {
        "steps": [
            {"name": "rows", "action": "sql", "query": "SELECT 1 AS one", "approved_query": {"id": "q1"}},
            {"name": "summary", "action": "summary", "focus": "x"},
        ]
    }
    step = validate_plan(raw, shop.introspect()).steps[0]
    assert isinstance(step, SqlStep) and step.approved_query is None


def test_approved_data_only_refuses_unmatched_questions_and_skips_ai_sql(shop):  # noqa: F811
    llm = _replies()
    agent = InsightForgeAgent(llm, privacy_mode="full")
    result = agent.run(
        "Is order amount significantly different between regions?",
        shop,
        mode="deep",
        queries=[BY_REGION],
        metrics=[REVENUE],
        approved_only=True,
    )
    assert result.artifacts == [] and result.refusal == result.summary
    assert "approved metrics and approved questions" in result.summary
    assert "Revenue" in result.summary and BY_REGION.question in result.summary
    assert result.mode == "quick"
    assert all("data-analysis planner" not in call[0]["content"] for call in llm.calls)
    # Approved answers still work in the same mode.
    ok = agent.run(BY_REGION.question, shop, queries=[BY_REGION], approved_only=True)
    assert ok.refusal is None and any(isinstance(item, TableArtifact) for item in ok.artifacts)


def test_without_approved_data_only_unmatched_questions_are_planned_as_before(shop):  # noqa: F811
    llm = _replies()
    agent = InsightForgeAgent(llm, privacy_mode="full")
    result = agent.run("How many customers are there?", shop, queries=[BY_REGION])
    assert result.refusal is None
    assert any("data-analysis planner" in call[0]["content"] for call in llm.calls)
