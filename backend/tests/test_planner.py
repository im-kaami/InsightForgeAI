import pytest

from insightforge.core.llm import FakeLLMClient
from insightforge.core.memory import ConversationMemory
from insightforge.core.planner import (
    Planner,
    PlanValidationError,
    PlotStep,
    SqlStep,
    SummaryStep,
    fallback_plan,
    validate_plan,
)

VALID = {
    "steps": [
        {
            "name": "by_department",
            "action": "sql",
            "query": "SELECT department, COUNT(*) AS count FROM employees GROUP BY department",
        },
        {
            "name": "chart",
            "action": "plot",
            "kind": "bar",
            "data_source": "by_department",
            "x": "department",
            "y": "count",
        },
        {"name": "summary", "action": "summary"},
    ]
}


def test_validate_plan_accepts_object_and_list_and_appends_summary(schema):
    object_plan = validate_plan({"steps": VALID["steps"][:1]}, schema)
    list_plan = validate_plan(VALID["steps"], schema)
    assert isinstance(object_plan.steps[-1], SummaryStep)
    assert len(list_plan.steps) == 3


def test_validate_plan_drops_unknown_plot_source(schema):
    raw = [
        {"name": "query", "action": "sql", "query": "SELECT 1"},
        {"name": "bad", "action": "plot", "kind": "bar", "data_source": "missing", "x": "x", "y": "y"},
    ]
    plan = validate_plan(raw, schema)
    assert not any(isinstance(step, PlotStep) for step in plan.steps)


def test_validate_plan_requires_sql(schema):
    with pytest.raises(PlanValidationError):
        validate_plan([{"name": "summary", "action": "summary"}], schema)


def test_planner_uses_valid_json_and_schema_prompt(schema):
    llm = FakeLLMClient([__import__("json").dumps(VALID)])
    plan = Planner(llm).plan("Compare departments", schema)
    assert isinstance(plan.steps[0], SqlStep)
    assert "TABLE employees" in llm.calls[0][0]["content"]


def test_planner_falls_back_on_garbage(schema):
    planner = Planner(FakeLLMClient(["not json"]))
    plan = planner.plan("Profile data", schema)
    assert planner.last_used_fallback is True
    assert planner.last_fallback_reason
    assert "LLMJSONError" in planner.last_fallback_reason
    assert any(isinstance(step, SqlStep) for step in plan.steps)


def test_fallback_uses_real_schema_and_valid_plot_source(schema):
    plan = fallback_plan("Profile", schema)
    sql_steps = {step.name: step for step in plan.steps if isinstance(step, SqlStep)}
    plots = [step for step in plan.steps if isinstance(step, PlotStep)]
    assert any("employees" in step.query for step in sql_steps.values())
    assert any("department" in step.query for step in sql_steps.values())
    assert plots and plots[0].data_source in sql_steps


def test_planner_includes_memory_for_follow_up(schema):
    memory = ConversationMemory()
    memory.add("Original goal", "Original summary", ["employees"])
    llm = FakeLLMClient([__import__("json").dumps(VALID)])
    Planner(llm).plan("Follow up", schema, memory)
    assert "Original goal" in llm.calls[0][0]["content"]
