import json

from insightforge.core.agent import InsightForgeAgent
from insightforge.core.llm import FakeLLMClient
from insightforge.core.planner import AMBIGUITY_JSON_SCHEMA, PLAN_JSON_SCHEMA, Planner

ASK = {
    "ambiguous": True,
    "question": "Best by revenue or by orders?",
    "options": ["Revenue", "Orders"],
}
PLAN = {"steps": [{"name": "n", "action": "sql", "query": "SELECT COUNT(*) AS n FROM employees"}]}


def _model(ambiguity: dict):
    def reply(messages):
        if "too ambiguous" in messages[0]["content"]:
            return json.dumps(ambiguity)
        return json.dumps(PLAN)

    return FakeLLMClient(reply)


def test_planner_asks_when_the_ambiguity_check_says_so(schema):
    llm = _model(ASK)
    planner = Planner(llm)
    plan = planner.plan("Who are the best?", schema, allow_clarification=True)
    assert plan.steps == []
    assert planner.last_clarification.options == ["Revenue", "Orders"]
    assert llm.schemas == [AMBIGUITY_JSON_SCHEMA]
    assert "TABLE employees" in llm.calls[0][0]["content"]


def test_clear_questions_are_planned_after_the_check(schema):
    llm = _model({"ambiguous": False})
    planner = Planner(llm)
    plan = planner.plan("How many employees?", schema, allow_clarification=True)
    assert planner.last_clarification is None
    assert plan.steps[0].name == "n"
    assert llm.schemas == [AMBIGUITY_JSON_SCHEMA, PLAN_JSON_SCHEMA]


def test_no_ambiguity_check_when_asking_is_not_allowed(schema):
    llm = _model(ASK)
    planner = Planner(llm)
    planner.plan("Who are the best?", schema)
    assert planner.last_clarification is None
    assert llm.schemas == [PLAN_JSON_SCHEMA]


def test_malformed_or_failed_checks_fall_back_to_planning(schema):
    planner = Planner(_model({"ambiguous": True, "question": "Which?", "options": ["only one"]}))
    assert planner.plan("Best?", schema, allow_clarification=True).steps[0].name == "n"
    assert planner.last_clarification is None

    def broken(messages):
        if "too ambiguous" in messages[0]["content"]:
            return "not json"
        return json.dumps(PLAN)

    planner = Planner(FakeLLMClient(broken))
    assert planner.plan("Best?", schema, allow_clarification=True).steps[0].name == "n"
    assert [event.ok for event in planner.tracer.events] == [False, True]


def test_agent_stops_before_running_anything_when_it_asks(catalog):
    result = InsightForgeAgent(_model(ASK), privacy_mode="full").run(
        "Who are the best?", catalog, allow_clarification=True
    )
    assert result.clarification.question == "Best by revenue or by orders?"
    assert result.summary == "Best by revenue or by orders?"
    assert result.artifacts == []
    assert [event.step for event in result.trace] == ["value_index", "ambiguity"]
