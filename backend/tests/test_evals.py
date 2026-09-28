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


def _agent_answering(answers: dict[str, str]) -> InsightForgeAgent:
    def reply(messages):
        if "data-analysis planner" not in messages[0]["content"]:
            return "Done."
        question = messages[-1]["content"]
        return json.dumps(
            {
                "steps": [
                    {"name": "answer", "action": "sql", "query": answers[question]},
                    {"name": "summary", "action": "summary"},
                ]
            }
        )

    return InsightForgeAgent(FakeLLMClient(reply), privacy_mode="full")


def test_every_reference_answer_passes_its_own_check():
    answers = {case.question: case.expect.sql for case in SUITE.cases}
    report = run_suite(SUITE, lambda: _agent_answering(answers), "oracle")
    failures = [(case.id, case.reason) for case in report.cases if not case.passed]
    assert failures == []
    assert report.summary["accuracy"] == 1.0
    assert len(report.cases) >= 25


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
