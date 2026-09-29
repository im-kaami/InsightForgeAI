import json

import numpy as np
import pandas as pd
import pytest
from scipy import stats
from statsmodels.stats.proportion import proportions_ztest

from insightforge.core.agent import InsightForgeAgent
from insightforge.core.artifacts import StatArtifact
from insightforge.core.catalog import DataCatalog
from insightforge.core.experiments import conclusion_holds
from insightforge.core.llm import FakeLLMClient
from insightforge.core.planner import SEGMENT_CUES, STAT_CUES
from insightforge.core.stats import StatError, run_test

RNG = np.random.default_rng(5)


def _customers() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "customer_id": range(100),
            "spend": np.r_[RNG.normal(100, 10, 60), RNG.normal(500, 40, 40)],
            "orders": np.r_[RNG.normal(2, 0.5, 60), RNG.normal(9, 1, 40)],
        }
    )


def test_segments_find_the_planted_groups_and_profile_them():
    result = run_test("s", "segments", "rows", "spend", "orders", _customers(), features=["spend", "orders"])
    assert result.statistic == 2 and result.n == 100
    assert result.effect_size.name == "silhouette" and result.effect_size.magnitude == "large"
    sizes = sorted(group["rows"] for group in result.groups)
    assert sizes == [40, 60]
    big = next(group for group in result.groups if group["rows"] == 60)
    assert big["profile"] == "low orders, low spend" or big["profile"] == "low spend, low orders"
    assert "Silhouette score by number of groups: 2:" in result.checks[1]
    assert result.features == ["spend", "orders"] and result.p_value is None


def test_segments_respect_a_requested_count_and_reject_bad_input():
    result = run_test("s", "segments", "rows", "", "", _customers(), features=["spend", "orders"], k=3)
    assert result.statistic == 3 and result.checks[1] == "The number of groups was set to 3."
    with pytest.raises(StatError, match="2 to 8 numeric columns"):
        run_test("s", "segments", "rows", "", "", _customers(), features=["spend"])
    with pytest.raises(StatError, match="at least 20 complete rows"):
        run_test("s", "segments", "rows", "", "", _customers().head(5), features=["spend", "orders"])


def _conversions() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "variant": ["control"] * 500 + ["new"] * 520,
            "converted": np.r_[RNG.binomial(1, 0.10, 500), RNG.binomial(1, 0.15, 520)],
        }
    )


def test_ab_tests_on_conversion_use_a_two_proportion_test():
    frame = _conversions()
    result = run_test("ab", "ab_test", "rows", "variant", "converted", frame)
    successes = [frame[frame.variant == name].converted.sum() for name in ("new", "control")]
    expected = proportions_ztest(successes, [520, 500])[1]
    assert result.test == "Two-proportion z-test"
    assert result.p_value == pytest.approx(expected)
    rates = frame.groupby("variant").converted.mean()
    assert result.statistic == pytest.approx(rates["new"] - rates["control"])
    assert result.interval.low < result.statistic < result.interval.high
    assert "sample-ratio check p = 0.531" in result.checks[0]
    assert result.effect_size.name == "relative_lift"


def test_ab_tests_flag_sample_ratio_mismatch_and_use_cuped():
    lopsided = pd.DataFrame({"variant": ["A"] * 700 + ["B"] * 300, "converted": RNG.binomial(1, 0.1, 1000)})
    result = run_test("ab", "ab_test", "rows", "variant", "converted", lopsided)
    assert any(caution.startswith("Sample ratio mismatch") for caution in result.cautions)

    pre = RNG.normal(50, 10, 600)
    frame = pd.DataFrame({"variant": ["A"] * 300 + ["B"] * 300, "pre": pre})
    frame["revenue"] = pre + np.where(frame.variant == "B", 2, 0) + RNG.normal(0, 3, 600)
    plain = run_test("ab", "ab_test", "rows", "variant", "revenue", frame)
    cuped = run_test("ab", "ab_test", "rows", "variant", "revenue", frame, controls=["pre"])
    assert plain.p_value == pytest.approx(
        stats.ttest_ind(frame.revenue[300:], frame.revenue[:300], equal_var=False).pvalue
    )
    assert cuped.test == "Welch's t-test with CUPED" and cuped.p_value < plain.p_value
    assert any("CUPED with pre reduced the variance by 9" in check for check in cuped.checks)
    assert [row["analysis"] for row in cuped.robustness] == [
        "with outcomes capped at the 1st and 99th percentiles",
        "without CUPED",
    ]


def test_robustness_reports_when_conclusions_hold_or_change():
    rows, message, holds = conclusion_holds(0.01, 2.0, [("a", 0.02, 1.0), ("b", 0.2, 1.0)])
    assert [row["holds"] for row in rows] == [True, False] and not holds
    assert message.startswith("Robustness: the conclusion holds in 1 of 2 alternative analyses (a, b)")
    assert conclusion_holds(0.01, 2.0, [("flip", 0.01, -1.0)])[2] is False
    assert conclusion_holds(0.4, 2.0, [("still null", 0.3, -1.0)])[2] is True


def test_every_significance_method_gets_a_robustness_check(hr_df):
    group = run_test("g", "compare_groups", "rows", "department", "salary", hr_df)
    assert [row["analysis"] for row in group.robustness] == [
        "Welch's ANOVA on means",
        "without the top and bottom 1% of values",
    ]
    assert group.checks[-1].startswith("Robustness: the conclusion holds in 2 of 2")
    fit = run_test("r", "regression", "rows", "performance_score", "salary", hr_df, controls=["department"])
    assert [row["analysis"] for row in fit.robustness][-1] == "without the controls"
    categories = run_test("c", "compare_categories", "rows", "department", "location", hr_df)
    assert categories.robustness == []


def test_segment_and_experiment_questions_are_routed():
    assert SEGMENT_CUES.search("Segment our customers by spend and frequency")
    assert SEGMENT_CUES.search("What types of customers do we have?")
    assert not SEGMENT_CUES.search("How many customers ordered in May?")
    assert not SEGMENT_CUES.search("Do completed order amounts differ between customer segments?")
    assert SEGMENT_CUES.search("Split customers into 3 groups")
    assert STAT_CUES.search("Did the new variant win the A/B test?")


def test_segment_questions_get_a_segments_plan():
    catalog = DataCatalog()
    catalog.register_df("customers", _customers())
    choice = {"segments": True, "features": ["spend", "orders"], "sql": "SELECT * FROM customers"}

    def reply(messages):
        if "split entities" in messages[0]["content"]:
            return json.dumps(choice)
        return "Two groups."

    result = InsightForgeAgent(FakeLLMClient(reply), privacy_mode="full").run(
        "Segment customers by spend and orders", catalog
    )
    stat = next(item for item in result.artifacts if isinstance(item, StatArtifact))
    assert stat.method == "segments" and stat.statistic == 2
    catalog.close()
