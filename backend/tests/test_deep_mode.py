import json

import pandas as pd
import pytest

from insightforge.core.agent import InsightForgeAgent
from insightforge.core.catalog import DataCatalog
from insightforge.core.checks import check_result
from insightforge.core.llm import FakeLLMClient, offline_fake_llm
from insightforge.evals import load_suite, open_dataset

REMOTE_WRONG = "SELECT COUNT(*) AS n FROM employees WHERE location = 'remot'"
REMOTE_RIGHT = "SELECT COUNT(*) AS n FROM employees WHERE location = 'Remote'"


@pytest.fixture
def hr():
    catalog = open_dataset(load_suite(), "hr")
    yield catalog
    catalog.close()


def _model(reviews: list[dict], summary: str = "There are 13 remote employees.", seen: list | None = None):
    remaining = list(reviews)

    def reply(messages):
        system = messages[0]["content"]
        if "data-analysis planner" in system:
            plan = {
                "steps": [
                    {"name": "remote", "action": "sql", "query": REMOTE_WRONG},
                    {"name": "summary", "action": "summary"},
                ]
            }
            return json.dumps(plan)
        if "You review" in system:
            if seen is not None:
                seen.append(messages[1]["content"])
            return json.dumps(remaining.pop(0))
        return summary

    return FakeLLMClient(reply)


def test_deep_mode_revises_a_wrong_filter_and_replaces_the_result(hr):
    seen: list[str] = []
    llm = _model(
        [
            {
                "verdict": "revise",
                "reason": "The filter used the wrong capitalisation",
                "steps": [{"name": "remote", "action": "sql", "query": REMOTE_RIGHT}],
            },
            {"verdict": "answer", "reason": "The count answers the question"},
        ],
        seen=seen,
    )
    result = InsightForgeAgent(llm, privacy_mode="full").run("How many work remotely?", hr, mode="deep")
    assert result.rounds == 2
    assert [review["verdict"] for review in result.reviews] == ["revise", "answer"]
    assert [(item.name, item.rows) for item in result.artifacts if item.type == "table"] == [
        ("remote", [{"n": 13}])
    ]
    assert result.findings == []
    assert result.number_check.unmatched == []
    assert "similar values: 'Remote'" in seen[0]
    assert "| 0 |" in seen[0]
    steps = [(event.kind, event.step) for event in result.trace]
    assert ("decision", "review:1") in steps and steps.count(("sql", "remote")) == 2


def test_quick_mode_keeps_the_wrong_result_but_flags_it(hr):
    result = InsightForgeAgent(_model([]), privacy_mode="full").run("How many work remotely?", hr)
    assert result.rounds == 1 and result.reviews == []
    assert [finding.code for finding in result.findings] == ["zero_result", "filter_matches_nothing"]


def test_schema_only_reviews_see_no_values(hr):
    seen: list[str] = []
    llm = _model([{"verdict": "answer", "reason": "ok"}], seen=seen)
    InsightForgeAgent(llm, privacy_mode="schema_only").run("How many work remotely?", hr, mode="deep")
    assert "1 rows; columns: n" in seen[0]
    assert "| 0 |" not in seen[0]
    assert "'Remote'" not in seen[0]
    assert "differ only in spelling or capitalisation" in seen[0]


def test_deep_mode_respects_the_round_limit(hr):
    revise = {
        "verdict": "revise",
        "reason": "try again",
        "steps": [{"name": "remote", "action": "sql", "query": REMOTE_WRONG}],
    }
    llm = _model([revise, revise, revise])
    result = InsightForgeAgent(llm, privacy_mode="full", deep_max_rounds=2).run("Remote?", hr, mode="deep")
    assert result.rounds == 2
    assert len(result.reviews) == 1


def test_deep_mode_stops_at_the_time_limit(hr):
    llm = _model([])
    result = InsightForgeAgent(llm, privacy_mode="full", deep_max_seconds=0).run("Remote?", hr, mode="deep")
    assert result.rounds == 1
    assert result.deep_notes == ["Deep mode stopped after 1 round(s) at its 0-second limit."]


def test_a_failed_review_keeps_the_results(hr):
    def reply(messages):
        system = messages[0]["content"]
        if "data-analysis planner" in system:
            return json.dumps({"steps": [{"name": "n", "action": "sql", "query": "SELECT 1 AS n"}]})
        if "You review" in system:
            return "not json"
        return "Done."

    result = InsightForgeAgent(FakeLLMClient(reply), privacy_mode="full").run("Count", hr, mode="deep")
    assert result.rounds == 1
    assert result.deep_notes[0].startswith("Review 1 failed (LLMJSONError")
    assert [item.name for item in result.artifacts if item.type == "table"] == ["n"]


def test_review_steps_with_unknown_sources_are_reported(hr):
    llm = _model(
        [
            {
                "verdict": "revise",
                "reason": "add a chart",
                "steps": [
                    {"name": "chart", "action": "plot", "kind": "bar", "data_source": "missing", "x": "n"}
                ],
            }
        ]
    )
    result = InsightForgeAgent(llm, privacy_mode="full").run("Remote?", hr, mode="deep")
    assert result.rounds == 1
    assert "plots 'missing', which is not a result step" in result.plan_issues[0]


def test_offline_deep_mode_runs_once(hr):
    result = InsightForgeAgent(offline_fake_llm(), privacy_mode="full").run("Profile", hr, mode="deep")
    assert result.rounds == 1
    assert result.deep_notes == ["Deep mode needs an AI model, so the analysis ran once without a review."]


def test_many_to_many_joins_are_reported():
    catalog = DataCatalog()
    try:
        catalog.register_df("a", pd.DataFrame({"k": [1, 1, 2], "v": [10, 20, 5]}))
        catalog.register_df("b", pd.DataFrame({"k": [1, 1, 2], "w": ["x", "y", "z"]}))
        sql = "SELECT SUM(a.v) AS s FROM a JOIN b ON a.k = b.k"
        findings = check_result("total", sql, catalog.query(sql), catalog)
        one_to_many = "SELECT SUM(a.v) AS s FROM a JOIN (SELECT DISTINCT k FROM b) c ON a.k = c.k"
        assert check_result("total", one_to_many, catalog.query(one_to_many), catalog) == []
    finally:
        catalog.close()
    assert [finding.code for finding in findings] == ["many_to_many_join"]
    assert "a.k = b.k repeats keys on both sides" in findings[0].message
