import json

import pytest

from insightforge.cli import _run
from insightforge.core.agent import InsightForgeAgent
from insightforge.core.llm import FakeLLMClient
from insightforge.evals import (
    CaseResult,
    EvalReport,
    compare,
    load_suite,
    open_dataset,
    run_case,
    run_suite,
)

SUITE = load_suite()


def _agent_answering(answers: dict[str, object]) -> InsightForgeAgent:
    def reply(messages):
        question = messages[-1]["content"]
        answer = answers.get(question)
        if "too ambiguous" in messages[0]["content"]:
            if answer == "CLARIFY":
                return json.dumps(
                    {"ambiguous": True, "question": "Which measure?", "options": ["Revenue", "Orders"]}
                )
            return json.dumps({"ambiguous": False})
        if "data-analysis planner" not in messages[0]["content"]:
            return "Done."
        test = answer if isinstance(answer, dict) else None
        steps = [{"name": "answer", "action": "sql", "query": test["sql"] if test else answer}]
        if test:
            steps.append(
                {"name": "test", "action": "test", "data_source": "answer"}
                | {
                    key: test[key]
                    for key in ("method", "x", "y", "by", "controls", "grain", "horizon", "features", "k")
                }
            )
        return json.dumps({"steps": [*steps, {"name": "summary", "action": "summary"}]})

    return InsightForgeAgent(FakeLLMClient(reply), privacy_mode="full")


def _reference(case) -> object:
    if case.expect.type == "clarify":
        return "CLARIFY"
    if case.expect.type == "stat":
        return case.expect.model_dump()
    return case.expect.sql


def test_every_reference_answer_passes_its_own_check():
    answers = {case.question: _reference(case) for case in SUITE.cases}
    report = run_suite(SUITE, lambda: _agent_answering(answers), "oracle")
    failures = [(case.id, case.reason) for case in report.cases if not case.passed]
    assert failures == []
    assert report.summary["accuracy"] == 1.0
    assert report.summary["clarifying_questions"] == 3
    assert len(report.cases) >= 30


def test_stat_cases_need_the_right_method_columns_and_data():
    case = next(case for case in SUITE.cases if case.id == "stat-segment-completed-amount")
    right = case.expect.model_dump()
    assert run_case(SUITE, case, _agent_answering({case.question: right})).passed
    unfiltered = right | {"sql": right["sql"].replace(" WHERE o.status = 'completed'", "")}
    wrong_data = run_case(SUITE, case, _agent_answering({case.question: unfiltered}))
    assert not wrong_data.passed and "ran on the right columns but p =" in wrong_data.reason
    with_region = right["sql"].replace("SELECT c.segment,", "SELECT c.segment, o.region,")
    wrong_column = run_case(
        SUITE, case, _agent_answering({case.question: right | {"sql": with_region, "x": "region"}})
    )
    assert wrong_column.reason == (
        "expected compare_groups(segment, amount); ran compare_groups(region, amount)"
    )
    failed = run_case(SUITE, case, _agent_answering({case.question: right | {"method": "correlation"}}))
    assert failed.reason == "the test step failed: Column 'segment' has no numeric values"
    no_test = run_case(SUITE, case, _agent_answering({case.question: right["sql"]}))
    assert not no_test.passed and no_test.reason == "no statistical test was run"


def test_needless_and_missing_clarifying_questions_fail():
    clear = next(case for case in SUITE.cases if case.id == "hr-headcount")
    asked = run_case(SUITE, clear, _agent_answering({clear.question: "CLARIFY"}))
    assert not asked.passed and asked.reason.startswith("asked a needless clarifying question")
    vague = next(case for case in SUITE.cases if case.id == "ambiguous-best-customers")
    answered = run_case(SUITE, vague, _agent_answering({vague.question: "SELECT 1 AS n"}))
    assert not answered.passed and answered.reason == "answered without asking a clarifying question"


def test_compare_measures_accuracy_on_shared_questions_only():
    comparison = compare(_report({"a": True, "b": True, "new": False}), _report({"a": True, "b": False}))
    assert comparison.accuracy_delta == 0.5


@pytest.mark.parametrize(
    ("case_id", "wrong_sql", "reason"),
    [
        ("hr-avg-salary", "SELECT AVG(salary) + 1 AS a FROM employees", "not in any result table"),
        (
            "hr-top-department-salary",
            "SELECT department, AVG(salary) AS s FROM employees GROUP BY 1 ORDER BY s",
            "no result table lists Engineering first",
        ),
        (
            "shop-revenue-by-category",
            "SELECT category, SUM(amount) AS s FROM orders GROUP BY 1",
            "no result table contains all 5 expected rows",
        ),
        (
            "shop-avg-completed-order",
            "SELECT AVG(amount) AS a FROM orders",
            "not in any result table",
        ),
    ],
)
def test_wrong_answers_fail(case_id, wrong_sql, reason):
    case = next(item for item in SUITE.cases if item.id == case_id)
    result = run_case(SUITE, case, _agent_answering({case.question: wrong_sql}))
    assert not result.passed
    assert reason in result.reason


def test_answers_rounded_to_whole_numbers_still_pass():
    case = next(item for item in SUITE.cases if item.id == "hr-avg-salary")
    result = run_case(
        SUITE, case, _agent_answering({case.question: "SELECT ROUND(AVG(salary)) AS a FROM employees"})
    )
    assert result.passed


def test_datasets_load_with_typed_columns():
    catalog = open_dataset(SUITE, "shop")
    try:
        types = dict(catalog.connection.execute("DESCRIBE orders").fetchall()[i][:2] for i in range(7))
    finally:
        catalog.close()
    assert types["order_date"] == "DATE"
    assert types["amount"] == "DOUBLE"


def _report(passed: dict[str, bool]) -> EvalReport:
    return EvalReport(
        suite="s",
        suite_version=1,
        model="m",
        started_at="2026-09-28T00:00:00Z",
        cases=[
            CaseResult(id=key, dataset="d", question="q", passed=value, reason="")
            for key, value in passed.items()
        ],
    )


def test_compare_lists_regressions_and_fixes():
    comparison = compare(_report({"a": False, "b": True}), _report({"a": True, "b": False}))
    assert (comparison.regressions, comparison.fixes, comparison.accuracy_delta) == (["a"], ["b"], 0.0)


def test_cli_eval_writes_a_report_and_fails_on_a_large_drop(tmp_path, capsys):
    baseline = tmp_path / "baseline.json"
    baseline.write_text(_report({"hr-headcount": True, "hr-avg-salary-by-department": True}).to_json())
    out = tmp_path / "report.json"
    code = _run(
        [
            "eval",
            "--model",
            "offline",
            "--case",
            "hr-headcount",
            "--case",
            "hr-avg-salary-by-department",
            "--out",
            str(out),
            "--baseline",
            str(baseline),
        ]
    )
    printed = capsys.readouterr().out
    assert code == 1
    assert "PASS hr-headcount" in printed
    assert "Now failing: hr-avg-salary-by-department" in printed
    assert json.loads(out.read_text())["summary"]["cases"] == 2
