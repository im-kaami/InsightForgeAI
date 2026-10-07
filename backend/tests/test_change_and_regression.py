import json

import numpy as np
import pandas as pd
import pytest
import statsmodels.api as sm

from insightforge.core.agent import InsightForgeAgent
from insightforge.core.artifacts import StatArtifact
from insightforge.core.llm import FakeLLMClient
from insightforge.core.planner import CHANGE_CUES, STAT_CUES, Planner
from insightforge.core.stats import StatError, run_test

RNG = np.random.default_rng(11)


def _sales() -> pd.DataFrame:
    rows = []
    for period, north, south in (("before", (10, 100.0), (10, 50.0)), ("after", (5, 110.0), (15, 50.0))):
        rows += [{"period": period, "region": "North", "amount": north[1]}] * north[0]
        rows += [{"period": period, "region": "South", "amount": south[1]}] * south[0]
    return pd.DataFrame(rows)


def test_change_breakdown_adds_up_and_separates_mix_from_rate():
    frame = _sales()
    result = run_test("c", "explain_change", "rows", "period", "amount", frame, by=["region"])
    assert result.statistic == pytest.approx(1300 - 1500)
    rows = {row["segment"]: row for row in result.groups}
    assert rows["North"]["change"] == pytest.approx(550 - 1000)
    assert rows["South"]["change"] == pytest.approx(750 - 500)
    assert sum(row["change"] for row in rows.values()) == pytest.approx(result.statistic)
    mean_change = 65.0 - 75.0
    mix = sum(row["mix_effect"] for row in rows.values())
    rate = sum(row["rate_effect"] for row in rows.values())
    assert mix + rate == pytest.approx(mean_change)
    assert rate == pytest.approx((110 - 100) * (0.5 + 0.25) / 2)
    assert mix == pytest.approx((0.25 - 0.5) * (100 + 110) / 2 + (0.75 - 0.5) * (50 + 50) / 2)
    assert result.p_value is None and result.trust == "tested method"
    assert "fell from 1,500 (before) to 1,300 (after)" in result.interpretation
    assert "North (-450, 225% of the change)" in result.interpretation
    assert "This shows where the change happened, not why it happened." in result.cautions


def test_change_breakdown_orders_dates_counts_rows_and_flags_new_segments():
    frame = pd.DataFrame(
        {
            "month": ["2025-02"] * 3 + ["2025-01"] * 2,
            "orders": [1] * 5,
            "region": ["North", "North", "West", "North", "South"],
        }
    )
    result = run_test("c", "explain_change", "rows", "month", "orders", frame, by=["region"])
    assert result.statistic == 1
    assert "rose from 2 (2025-01) to 3 (2025-02)" in result.interpretation
    assert "average" not in result.interpretation
    assert any("new in 2025-02: West; gone in 2025-02: South" in caution for caution in result.cautions)
    counts = frame.drop(columns="orders")
    literal = run_test("c", "explain_change", "rows", "month", "1", counts, by=["region"])
    assert literal.statistic == 1 and "Total rows rose from 2" in literal.interpretation


@pytest.mark.parametrize(
    ("frame", "by", "message"),
    [
        (_sales(), [], "at least one column"),
        (_sales().assign(period="before"), ["region"], "exactly two periods"),
        (_sales(), ["missing"], "not in the result"),
    ],
)
def test_change_breakdown_rejects_unusable_input(frame, by, message):
    with pytest.raises(StatError, match=message):
        run_test("c", "explain_change", "rows", "period", "amount", frame, by=by)


def test_regression_matches_statsmodels_and_holds_controls_fixed():
    n = 120
    team = RNG.choice(["a", "b", "c"], n)
    deals = RNG.normal(10, 3, n)
    amount = 5 * deals + np.select([team == "b", team == "c"], [20, -10], 0) + RNG.normal(0, 4, n)
    frame = pd.DataFrame({"deals": deals, "amount": amount, "team": team})
    result = run_test("r", "regression", "rows", "deals", "amount", frame, controls=["team"])
    design = sm.add_constant(
        pd.DataFrame({"deals": deals}).join(pd.get_dummies(frame["team"], drop_first=True).astype(float))
    )
    expected = sm.OLS(amount, design).fit()
    assert result.statistic == pytest.approx(expected.params["deals"])
    assert result.p_value == pytest.approx(expected.pvalues["deals"])
    assert result.effect_size.name == "r_squared"
    assert result.effect_size.value == pytest.approx(expected.rsquared)
    assert [row["term"] for row in result.groups] == ["const", "deals", "team=b", "team=c"]
    assert "holding team fixed" in result.interpretation
    assert "Regression shows association, not proof that one causes the other." in result.cautions


def test_regression_uses_robust_errors_when_spread_is_uneven_and_rejects_categories():
    x = np.linspace(1, 10, 200)
    frame = pd.DataFrame({"x": x, "y": 2 * x + RNG.normal(0, 1, 200) * x**2})
    result = run_test("r", "regression", "rows", "x", "y", frame)
    assert "robust HC3 errors are used" in result.checks[0]
    with pytest.raises(StatError, match="compare groups instead"):
        run_test("r", "regression", "rows", "team", "amount", pd.DataFrame({"team": ["a", "b"] * 5,
                                                                            "amount": range(10)}))


def test_cues_route_change_and_regression_questions():
    assert CHANGE_CUES.search("Why did total amount drop from May to June?")
    assert CHANGE_CUES.search("What drove the change in orders between H1 and H2?")
    assert not CHANGE_CUES.search("What is the total amount in June?")
    assert STAT_CUES.search("How much does performance score affect salary, controlling for department?")
    assert STAT_CUES.search("By how much does salary rise for each additional point of score?")


CHANGE = {
    "change": True,
    "y": "salary",
    "by": ["department"],
    "sql": "SELECT CASE WHEN hire_date < '2020-01-01' THEN 'before' ELSE 'after' END AS period, "
    "salary, department FROM employees",
}


def test_change_questions_get_a_change_breakdown_plan(catalog):
    def reply(messages):
        system = messages[0]["content"]
        if "asks WHY a number changed" in system:
            return json.dumps(CHANGE)
        return "Payroll moved."

    llm = FakeLLMClient(reply)
    result = InsightForgeAgent(llm, privacy_mode="full").run(
        "Why did salaries change for people hired after 2020?", catalog
    )
    assert [step.action for step in result.plan.steps] == ["sql", "test", "summary"]
    artifact = next(item for item in result.artifacts if isinstance(item, StatArtifact))
    assert artifact.method == "explain_change" and artifact.by == ["department"]
    assert artifact.test == "Change breakdown (mix and rate)"


def test_rejected_test_data_gets_one_query_rewrite(catalog):
    one_period = {**CHANGE, "sql": "SELECT 'before' AS period, salary, department FROM employees"}
    repairs: list[str] = []

    def reply(messages):
        system = messages[0]["content"]
        if "asks WHY a number changed" in system:
            return json.dumps(one_period)
        if "Correct the DuckDB SQL" in system:
            repairs.append(messages[-1]["content"])
            return json.dumps({"query": CHANGE["sql"]})
        return "Done."

    result = InsightForgeAgent(FakeLLMClient(reply), privacy_mode="schema_only").run(
        "Why did salaries change for people hired after 2020?", catalog
    )
    artifact = next(item for item in result.artifacts if isinstance(item, StatArtifact))
    assert artifact.method == "explain_change" and artifact.n == 60
    assert len(repairs) == 1
    assert "must contain exactly two periods, but the result had only 1" in repairs[0]
    assert "Remove WHERE conditions that keep only one period" in repairs[0]
    assert "found 1: before" not in repairs[0]
    table = next(item for item in result.artifacts if getattr(item, "name", "") == "rows")
    assert "CASE WHEN" in table.sql


def test_regression_choices_pass_controls_through(schema):
    choice = {
        "test": True,
        "method": "regression",
        "x": "performance_score",
        "y": "salary",
        "controls": ["department"],
        "sql": "SELECT performance_score, salary, department FROM employees",
    }
    llm = FakeLLMClient([json.dumps(choice)])
    plan = Planner(llm).plan("How much does score affect salary, controlling for department?", schema)
    assert plan.steps[1].name == "regression"
    assert plan.steps[1].controls == ["department"]
