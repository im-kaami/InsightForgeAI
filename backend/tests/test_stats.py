import json
import math

import numpy as np
import pandas as pd
import pytest
from scipy import stats
from statsmodels.stats.oneway import anova_oneway

from insightforge.core.agent import InsightForgeAgent
from insightforge.core.artifacts import ErrorArtifact, StatArtifact
from insightforge.core.executor import Executor
from insightforge.core.llm import FakeLLMClient
from insightforge.core.planner import PLAN_JSON_SCHEMA, Planner, StatStep, validate_plan
from insightforge.core.stats import StatError, adjust_for_multiple_tests, run_test
from insightforge.core.summarizer import Summarizer

RNG = np.random.default_rng(7)


def _groups(**samples: np.ndarray) -> pd.DataFrame:
    return pd.DataFrame(
        [{"team": name, "score": float(value)} for name, values in samples.items() for value in values]
    )


def test_two_normal_groups_use_welch_with_interval_and_hedges_g():
    a, b = RNG.normal(100, 10, 40), RNG.normal(90, 15, 45)
    result = run_test("t", "compare_groups", "src", "team", "score", _groups(a=a, b=b))
    expected = stats.ttest_ind(a, b, equal_var=False)
    assert result.test == "Welch's t-test"
    assert result.p_value == pytest.approx(expected.pvalue)
    interval = expected.confidence_interval(0.95)
    assert (result.interval.low, result.interval.high) == pytest.approx((interval.low, interval.high))
    pooled = math.sqrt((39 * np.var(a, ddof=1) + 44 * np.var(b, ddof=1)) / 83)
    assert result.effect_size.value == pytest.approx((a.mean() - b.mean()) / pooled * (1 - 3 / (4 * 85 - 9)))
    assert result.n == 85 and result.trust == "tested method"
    assert "unlikely to be chance alone" in result.interpretation


def test_skewed_small_groups_use_mann_whitney_with_rank_biserial():
    a, b = RNG.exponential(1, 15), RNG.exponential(3, 15)
    result = run_test("t", "compare_groups", "src", "team", "score", _groups(a=a, b=b))
    expected = stats.mannwhitneyu(a, b, alternative="two-sided")
    assert result.test == "Mann-Whitney U test"
    assert result.p_value == pytest.approx(expected.pvalue)
    assert result.effect_size.value == pytest.approx(2 * expected.statistic / 225 - 1)
    assert "rank-based" in result.checks[0]


def test_many_groups_choose_anova_welch_anova_or_kruskal():
    equal = {name: RNG.normal(mean, 5, 40) for name, mean in (("a", 50), ("b", 55), ("c", 60))}
    anova = run_test("t", "compare_groups", "src", "team", "score", _groups(**equal))
    assert anova.test == "One-way ANOVA"
    assert anova.p_value == pytest.approx(stats.f_oneway(*equal.values()).pvalue)
    grand = np.concatenate(list(equal.values()))
    between = sum(len(v) * (v.mean() - grand.mean()) ** 2 for v in equal.values())
    assert anova.effect_size.value == pytest.approx(between / ((grand - grand.mean()) ** 2).sum())
    assert len(anova.pairwise) == 3 and all("p_adjusted" in pair for pair in anova.pairwise)

    unequal = {"a": RNG.normal(50, 1, 40), "b": RNG.normal(52, 10, 40), "c": RNG.normal(55, 20, 40)}
    welch = run_test("t", "compare_groups", "src", "team", "score", _groups(**unequal))
    assert welch.test == "Welch's ANOVA"
    assert welch.p_value == pytest.approx(anova_oneway(list(unequal.values()), use_var="unequal").pvalue)

    skewed = {name: RNG.exponential(scale, 12) for name, scale in (("a", 1), ("b", 2), ("c", 4))}
    kruskal = run_test("t", "compare_groups", "src", "team", "score", _groups(**skewed))
    assert kruskal.test == "Kruskal-Wallis test"
    assert kruskal.p_value == pytest.approx(stats.kruskal(*skewed.values()).pvalue)


def test_categories_use_fisher_for_sparse_two_by_two_and_chi_square_otherwise():
    sparse = pd.DataFrame({"a": ["x"] * 6 + ["y"] * 6, "b": ["p"] * 5 + ["q"] + ["q"] * 5 + ["p"]})
    fisher = run_test("t", "compare_categories", "src", "a", "b", sparse)
    assert fisher.test == "Fisher's exact test"
    assert fisher.p_value == pytest.approx(stats.fisher_exact([[5, 1], [1, 5]]).pvalue)

    rows = RNG.choice(["north", "south", "west"], 300), RNG.choice(["yes", "no"], 300)
    frame = pd.DataFrame({"region": rows[0], "answer": rows[1]})
    chi = run_test("t", "compare_categories", "src", "region", "answer", frame)
    table = pd.crosstab(frame["region"], frame["answer"]).to_numpy()
    assert chi.test == "Chi-square test of independence"
    assert chi.p_value == pytest.approx(stats.chi2_contingency(table).pvalue)
    assert chi.effect_size.value == pytest.approx(stats.contingency.association(table, method="cramer"))
    assert chi.n == 300 and len(chi.groups) == 3


def test_correlation_uses_pearson_unless_ranks_disagree():
    x = RNG.normal(0, 1, 80)
    linear = pd.DataFrame({"x": x, "y": 2 * x + RNG.normal(0, 1, 80)})
    pearson = run_test("t", "correlation", "src", "x", "y", linear)
    expected = stats.pearsonr(linear["x"], linear["y"])
    assert pearson.test == "Pearson correlation"
    assert pearson.statistic == pytest.approx(expected.statistic)
    interval = expected.confidence_interval(0.95)
    assert (pearson.interval.low, pearson.interval.high) == pytest.approx((interval.low, interval.high))
    assert "Correlation does not show that one causes the other." in pearson.cautions

    skewed = pd.DataFrame({"x": list(range(30)) + [100], "y": list(range(30)) + [-500]})
    spearman = run_test("t", "correlation", "src", "x", "y", skewed)
    assert spearman.test == "Spearman rank correlation"
    assert spearman.statistic == pytest.approx(stats.spearmanr(skewed["x"], skewed["y"]).statistic)


def test_cautions_for_small_groups_dropped_groups_and_underpowered_effects():
    frame = _groups(a=np.array([1.0, 2, 3, 4, 5]), b=np.array([2.0, 3, 4, 5, 7]), c=np.array([9.0]))
    result = run_test("t", "compare_groups", "src", "team", "score", frame)
    assert "c was left out because it has fewer than 2 values." in result.cautions
    assert any(caution.startswith("Small groups (a, b") for caution in result.cautions)
    assert result.p_value >= 0.05
    assert "does not prove there is no effect" in result.interpretation


@pytest.mark.parametrize(
    ("method", "frame", "x", "y", "message"),
    [
        ("compare_groups", pd.DataFrame({"g": ["a", "b"], "v": [1, 2]}), "g", "missing", "not in the result"),
        ("compare_groups", pd.DataFrame({"g": ["a", "b"], "v": ["x", "y"]}), "g", "v", "no numeric values"),
        ("compare_groups", pd.DataFrame({"g": ["a"] * 4, "v": [1, 2, 3, 4]}), "g", "v", "at least two"),
        ("correlation", pd.DataFrame({"x": [1, 2, 3], "y": [1, 2, 4]}), "x", "y", "at least 4 rows"),
        ("correlation", pd.DataFrame({"x": [1, 1, 1, 1], "y": [1, 2, 3, 4]}), "x", "y", "to vary"),
        ("compare_categories", pd.DataFrame({"a": ["x", "x"], "b": ["p", "q"]}), "a", "b", "two categories"),
    ],
)
def test_unusable_inputs_raise_clear_errors(method, frame, x, y, message):
    with pytest.raises(StatError, match=message):
        run_test("t", method, "src", x, y, frame)


def test_several_tests_are_holm_adjusted():
    frame = _groups(a=RNG.normal(0, 1, 40), b=RNG.normal(0.5, 1, 40))
    first = run_test("one", "compare_groups", "src", "team", "score", frame)
    shuffled = frame.assign(score=RNG.normal(0, 1, 80))
    second = run_test("two", "compare_groups", "src", "team", "score", shuffled)
    adjust_for_multiple_tests([first, second])
    smaller, larger = sorted((first, second), key=lambda artifact: artifact.p_value)
    assert smaller.p_adjusted == pytest.approx(min(1.0, 2 * smaller.p_value))
    assert larger.p_adjusted == pytest.approx(max(smaller.p_adjusted, larger.p_value))
    assert any("2 tests in this analysis" in check for check in first.checks)


ROWS_SQL = "SELECT department, salary FROM employees"
TEST_PLAN = {
    "steps": [
        {"name": "rows", "action": "sql", "query": ROWS_SQL},
        {"name": "salary_test", "action": "test", "method": "compare_groups", "data_source": "rows",
         "x": "department", "y": "salary"},
        {"name": "summary", "action": "summary"},
    ]
}


def test_plans_accept_test_steps_only_on_earlier_sql_steps(schema):
    plan = validate_plan(TEST_PLAN, schema)
    assert isinstance(plan.steps[1], StatStep)
    problems: list[str] = []
    bad = {"steps": [TEST_PLAN["steps"][0], {**TEST_PLAN["steps"][1], "data_source": "nope"}]}
    validate_plan(bad, schema, problems)
    assert "not the name of an earlier sql step" in problems[0]
    test_schema = PLAN_JSON_SCHEMA["properties"]["steps"]["items"]["anyOf"][-1]
    methods = test_schema["properties"]["method"]["enum"]
    assert methods[:3] == ["compare_groups", "compare_categories", "correlation"]
    assert {"explain_change", "regression", "forecast", "anomalies"} <= set(methods)


def test_test_steps_are_offered_to_deep_mode_reviews_but_not_the_plan_prompt(schema):
    llm = FakeLLMClient([json.dumps(TEST_PLAN)])
    Planner(llm).plan("What does each department pay?", schema)
    assert '"action": "test"' not in llm.calls[0][0]["content"]
    reviewer = FakeLLMClient([json.dumps({"verdict": "answer", "reason": "fine"})])
    Planner(reviewer).review("Is it significant?", schema, {}, [], 1)
    assert '"action": "test"' in reviewer.calls[0][0]["content"]


def test_agent_runs_tests_and_links_quoted_statistics_to_evidence(catalog, hr_df):
    expected = run_test("x", "compare_groups", "rows", "department", "salary", hr_df)
    statistic = f"{expected.statistic:.2f}"
    llm = FakeLLMClient([json.dumps(TEST_PLAN), f"Departments differ (test statistic {statistic})."])
    result = InsightForgeAgent(llm, privacy_mode="full").run("Do salaries differ by department?", catalog)
    artifact = next(item for item in result.artifacts if isinstance(item, StatArtifact))
    assert artifact.p_value == pytest.approx(expected.p_value)
    assert artifact.note is None
    assert [event.kind for event in result.trace if event.step == "salary_test"] == ["test"]
    assert result.number_check.unmatched == []
    assert [item.artifact for item in result.evidence] == ["salary_test"]


def test_tests_on_cut_off_results_use_every_row(catalog, hr_df):
    planner, summarizer = Planner(FakeLLMClient(["unused"])), Summarizer(FakeLLMClient(["Done."]))
    executor = Executor(catalog, planner, summarizer, result_limit=5)
    schema = catalog.introspect()
    artifacts, *_ = executor.execute("q", validate_plan(TEST_PLAN, schema), schema)
    stat = next(item for item in artifacts if isinstance(item, StatArtifact))
    expected = run_test("x", "compare_groups", "rows", "department", "salary", hr_df)
    assert stat.n == len(hr_df)
    assert stat.note == f"Computed on all {len(hr_df):,} rows, not only the 5 shown."
    assert stat.p_value == pytest.approx(expected.p_value)


CHOICE = {
    "test": True,
    "method": "compare_groups",
    "x": "department",
    "y": "salary",
    "sql": "SELECT department, salary FROM employees",
}


def _replies(choice: dict, plan: dict | None = None):
    def reply(messages):
        system = messages[0]["content"]
        if "asks for a statistical test" in system:
            return json.dumps(choice)
        if "data-analysis planner" in system:
            return json.dumps(plan or {"steps": [TEST_PLAN["steps"][0]]})
        return "Done."

    return FakeLLMClient(reply)


def test_significance_questions_get_a_dedicated_test_choice(schema):
    llm = _replies(CHOICE)
    planner = Planner(llm, privacy_mode="schema_only")
    plan = planner.plan("Is the salary difference between departments significant?", schema)
    assert [step.action for step in plan.steps] == ["sql", "test", "summary"]
    step = plan.steps[1].model_dump()
    assert {key: step[key] for key in ("name", "action", "method", "data_source", "x", "y")} == {
        "name": "significance_test",
        "action": "test",
        "method": "compare_groups",
        "data_source": "rows",
        "x": "department",
        "y": "salary",
    }
    assert len(llm.calls) == 1 and "Engineering" not in llm.calls[0][0]["content"]


def test_descriptive_questions_skip_the_test_choice_or_fall_back_to_planning(schema):
    llm = _replies(CHOICE)
    Planner(llm).plan("What is the average salary by department?", schema)
    assert all("asks for a statistical test" not in call[0]["content"] for call in llm.calls)

    declined = _replies({"test": False})
    plan = Planner(declined).plan("Could this be chance?", schema)
    assert [step.action for step in plan.steps] == ["sql", "summary"]
    broken = _replies({"test": True, "method": "anova", "x": "a", "y": "b", "sql": "SELECT 1"})
    assert [step.action for step in Planner(broken).plan("Is it significant?", schema).steps] == [
        "sql",
        "summary",
    ]


def test_failed_data_steps_explain_why_the_test_did_not_run(catalog):
    broken_rows = {**TEST_PLAN["steps"][0], "query": "SELECT segment FROM employees"}
    plan = {"steps": [broken_rows, TEST_PLAN["steps"][1]]}
    llm = FakeLLMClient([json.dumps(plan), json.dumps({"query": "SELECT segment FROM employees"}), "Done."])
    result = InsightForgeAgent(llm, privacy_mode="full").run("Show pay by department", catalog)
    errors = {item.name: item.message for item in result.artifacts if isinstance(item, ErrorArtifact)}
    assert errors["salary_test"] == "The data step 'rows' failed, so the test could not run"


def test_bad_test_columns_become_error_artifacts(catalog):
    plan = {"steps": [TEST_PLAN["steps"][0], {**TEST_PLAN["steps"][1], "y": "bonus"}]}
    agent = InsightForgeAgent(FakeLLMClient([json.dumps(plan), "Done."]), privacy_mode="full")
    result = agent.run("Do bonuses differ?", catalog)
    error = next(item for item in result.artifacts if isinstance(item, ErrorArtifact))
    assert error.name == "salary_test" and "'bonus' is not in the result" in error.message
