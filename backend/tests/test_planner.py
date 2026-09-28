import json

import pytest

from insightforge.core.llm import FakeLLMClient
from insightforge.core.memory import ConversationMemory
from insightforge.core.planner import (
    PLAN_JSON_SCHEMA,
    Planner,
    PlanValidationError,
    PlotStep,
    SqlStep,
    SummaryStep,
    fallback_plan,
    validate_plan,
)
from insightforge.core.schema import ColumnInfo, SchemaInfo, TableInfo

BROKEN = {
    "steps": [
        {"name": "by_department", "action": "sql"},
        {"name": "chart", "action": "plot", "kind": "bar", "data_source": "missing", "x": "department"},
        {"name": "summary", "action": "summary"},
    ]
}

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


def test_planner_accepts_single_step_dict(schema):
    llm = FakeLLMClient([json.dumps({"name": "q", "action": "sql", "query": "SELECT 1"})])
    planner = Planner(llm)
    plan = planner.plan("Run one query", schema)
    assert len(plan.steps) == 2
    assert isinstance(plan.steps[0], SqlStep)
    assert isinstance(plan.steps[1], SummaryStep)
    assert planner.last_used_fallback is False


def test_planner_accepts_nested_plan_wrapper(schema):
    raw = {"plan": {"steps": [{"name": "q", "action": "sql", "query": "SELECT 1"}]}}
    planner = Planner(FakeLLMClient([json.dumps(raw)]))
    plan = planner.plan("Run wrapped query", schema)
    assert isinstance(plan.steps[0], SqlStep)
    assert planner.last_used_fallback is False


def test_planner_normalizes_plot_aliases(schema):
    raw = {
        "steps": [
            {
                "name": "dept_avg_salaries",
                "action": "sql",
                "query": (
                    "SELECT department, AVG(salary) AS avg_salary FROM employees "
                    "GROUP BY department"
                ),
            },
            {
                "name": "salary_chart",
                "action": "plot",
                "chart_type": "BAR",
                "data_source": "dept_avg_salaries",
                "x": "department",
                "y": ["avg_salary"],
                "title": "Average salary by department",
            },
        ]
    }
    planner = Planner(FakeLLMClient([json.dumps(raw)]))
    plan = planner.plan("Compare salaries", schema)
    plot = next(step for step in plan.steps if isinstance(step, PlotStep))
    assert plot.kind == "bar"
    assert plot.y == "avg_salary"


def test_planner_uses_valid_json_and_schema_prompt(schema):
    llm = FakeLLMClient([json.dumps(VALID)])
    plan = Planner(llm).plan("Compare departments", schema)
    assert isinstance(plan.steps[0], SqlStep)
    prompt = llm.calls[0][0]["content"]
    assert "TABLE employees" in prompt
    assert '"kind"' in prompt
    assert '"data_source"' in prompt


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


def test_validate_plan_reports_why_steps_were_dropped(schema):
    problems: list[str] = []
    with pytest.raises(PlanValidationError):
        validate_plan(BROKEN, schema, problems)
    assert problems[0].startswith("Step 1 (by_department) is invalid: sql.query: Field required")
    assert "data_source 'missing', which is not the name of an earlier sql step" in problems[1]


def test_planner_asks_the_model_to_repair_a_broken_plan(schema):
    llm = FakeLLMClient([json.dumps(BROKEN), json.dumps(VALID)])
    planner = Planner(llm)
    plan = planner.plan("Compare departments", schema)
    assert [step.name for step in plan.steps] == ["by_department", "chart", "summary"]
    assert planner.last_used_fallback is False
    assert planner.last_plan_issues == []
    repair_request = llm.calls[1][-1]["content"]
    assert "Your plan had these problems:" in repair_request
    assert "sql.query: Field required" in repair_request
    assert llm.schemas == [PLAN_JSON_SCHEMA, PLAN_JSON_SCHEMA]


def test_planner_keeps_the_valid_steps_when_repair_fails(schema):
    partial = {"steps": [VALID["steps"][0], BROKEN["steps"][1]]}
    planner = Planner(FakeLLMClient([json.dumps(partial), "still not json"]))
    plan = planner.plan("Compare departments", schema)
    assert [step.name for step in plan.steps] == ["by_department", "summary"]
    assert planner.last_used_fallback is False
    assert "data_source 'missing'" in planner.last_plan_issues[0]
    assert planner.last_plan_issues[-1].startswith("Plan repair failed: LLMJSONError")


def test_planner_falls_back_when_the_repair_is_still_invalid(schema):
    planner = Planner(FakeLLMClient([json.dumps(BROKEN), json.dumps(BROKEN)]))
    plan = planner.plan("Compare departments", schema)
    assert planner.last_used_fallback is True
    assert "Plan must contain at least one SQL step" in planner.last_fallback_reason
    assert planner.last_plan_issues == []
    assert any(isinstance(step, SqlStep) for step in plan.steps)


def test_fallback_statistics_skip_identifier_columns():
    table = TableInfo(
        name="orders",
        row_count=10,
        columns=[
            ColumnInfo(name="order_id", dtype="BIGINT"),
            ColumnInfo(name="region", dtype="VARCHAR"),
            ColumnInfo(name="amount", dtype="DOUBLE"),
        ],
    )
    plan = fallback_plan("Profile", SchemaInfo(tables=[table]))
    stats = [step for step in plan.steps if isinstance(step, SqlStep) and step.name.startswith("stats")]
    assert [step.name for step in stats] == ["stats_amount"]


def test_planner_includes_memory_for_follow_up(schema):
    memory = ConversationMemory()
    memory.add("Original goal", "Original summary", ["employees"])
    llm = FakeLLMClient([json.dumps(VALID)])
    Planner(llm).plan("Follow up", schema, memory)
    assert "Original goal" in llm.calls[0][0]["content"]
