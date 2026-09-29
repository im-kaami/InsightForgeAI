import math
from typing import Any, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field
from scipy import stats
from statsmodels.stats.multitest import multipletests
from statsmodels.stats.oneway import anova_oneway

TestMethod = Literal["compare_groups", "compare_categories", "correlation"]

ALPHA = 0.05
NORMAL_ENOUGH_N = 30
SMALL_GROUP_N = 10
MAX_GROUPS = 20
MAX_CATEGORIES = 30
MAX_PAIRWISE_GROUPS = 10


class StatError(ValueError):
    pass


class EffectSize(BaseModel):
    name: str
    value: float
    magnitude: str


class Interval(BaseModel):
    label: str
    low: float
    high: float
    level: float = 0.95


class StatArtifact(BaseModel):
    name: str
    type: Literal["stat"] = "stat"
    trust: Literal["tested method"] = "tested method"
    method: TestMethod
    test: str
    data_source: str
    x: str
    y: str
    n: int
    statistic: float | None = None
    p_value: float
    p_adjusted: float | None = None
    effect_size: EffectSize | None = None
    interval: Interval | None = None
    groups: list[dict[str, Any]] = Field(default_factory=list)
    pairwise: list[dict[str, Any]] = Field(default_factory=list)
    checks: list[str] = Field(default_factory=list)
    cautions: list[str] = Field(default_factory=list)
    interpretation: str
    note: str | None = None

    def key_table(self) -> pd.DataFrame:
        row: dict[str, Any] = {"test": self.test, "n": self.n, "p_value": self.p_value}
        if self.statistic is not None:
            row["statistic"] = self.statistic
        if self.p_adjusted is not None:
            row["p_adjusted"] = self.p_adjusted
        if self.effect_size:
            row[self.effect_size.name] = self.effect_size.value
        if self.interval:
            row["ci_low"], row["ci_high"] = self.interval.low, self.interval.high
        return pd.DataFrame([row])


def format_p(value: float) -> str:
    return "< 0.001" if value < 0.001 else f"= {value:.3f}"


def _num(value: float) -> str:
    if not math.isfinite(value):
        return str(value)
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    return f"{value:,.3g}" if abs(value) < 1 else f"{value:,.2f}".rstrip("0").rstrip(".")


def _magnitude(value: float, cuts: tuple[float, float, float]) -> str:
    size = abs(value)
    return (
        "negligible"
        if size < cuts[0]
        else "small"
        if size < cuts[1]
        else "medium"
        if size < cuts[2]
        else "large"
    )


def _verdict(p_value: float) -> str:
    if p_value < ALPHA:
        return (
            f"p {format_p(p_value)}, so a result this strong is unlikely to be chance alone "
            f"(at the {ALPHA} level)."
        )
    return (
        f"p {format_p(p_value)}, so the data do not show a real effect; it may be chance. "
        "This does not prove there is no effect."
    )


def _column(frame: pd.DataFrame, name: str) -> pd.Series:
    if name not in frame.columns:
        raise StatError(
            f"Column {name!r} is not in the result; available: {', '.join(map(str, frame.columns))}"
        )
    return frame[name]


def _numeric(frame: pd.DataFrame, name: str) -> pd.Series:
    values = pd.to_numeric(_column(frame, name), errors="coerce")
    if values.notna().sum() == 0:
        raise StatError(f"Column {name!r} has no numeric values")
    return values.astype(float)


def _describe(values: np.ndarray) -> dict[str, float]:
    return {
        "n": int(len(values)),
        "mean": float(np.mean(values)),
        "median": float(np.median(values)),
        "sd": float(np.std(values, ddof=1)) if len(values) > 1 else 0.0,
    }


def compare_groups(frame: pd.DataFrame, group: str, value: str) -> dict[str, Any]:
    data = pd.DataFrame({"group": _column(frame, group), "value": _numeric(frame, value)}).dropna()
    samples = {str(key): part["value"].to_numpy() for key, part in data.groupby("group", sort=True)}
    cautions = [
        f"{key} was left out because it has fewer than 2 values."
        for key, values in samples.items()
        if len(values) < 2
    ]
    samples = {key: values for key, values in samples.items() if len(values) >= 2}
    if len(samples) < 2:
        raise StatError(f"Comparing groups needs at least two {group} groups with 2 or more values each")
    if len(samples) > MAX_GROUPS:
        raise StatError(f"{group} has {len(samples)} groups; compare at most {MAX_GROUPS}")
    if all(np.ptp(values) == 0 for values in samples.values()):
        raise StatError(f"{value} does not vary within any group, so there is nothing to test")
    groups = [{group: key, **_describe(values)} for key, values in samples.items()]
    checks: list[str] = []
    non_normal = []
    for key, values in samples.items():
        if len(values) >= NORMAL_ENOUGH_N:
            continue
        if len(values) < 3 or np.ptp(values) == 0:
            non_normal.append(f"{key} (too few distinct values to check)")
            continue
        shapiro_p = float(stats.shapiro(values).pvalue)
        if shapiro_p < ALPHA:
            non_normal.append(f"{key} (Shapiro-Wilk p {format_p(shapiro_p)})")
    parametric = not non_normal
    checks.append(
        "Normality: every group has 30+ values or passes a Shapiro-Wilk check, so means are compared."
        if parametric
        else f"Normality: not met for {', '.join(non_normal)}, so a rank-based test compares typical values."
    )
    small = [key for key, values in samples.items() if len(values) < SMALL_GROUP_N]
    if small:
        cautions.append(
            f"Small groups ({', '.join(small)} have fewer than {SMALL_GROUP_N} values); "
            "treat the result as fragile."
        )
    keys = list(samples)
    arrays = [samples[key] for key in keys]
    result: dict[str, Any] = {
        "groups": groups,
        "checks": checks,
        "cautions": cautions,
        "n": sum(len(values) for values in arrays),
    }
    if len(arrays) == 2:
        a, b = arrays
        if parametric:
            test = stats.ttest_ind(a, b, equal_var=False)
            interval = test.confidence_interval(0.95)
            pooled = math.sqrt(
                ((len(a) - 1) * np.var(a, ddof=1) + (len(b) - 1) * np.var(b, ddof=1)) / (len(a) + len(b) - 2)
            )
            difference = float(np.mean(a) - np.mean(b))
            hedges = (difference / pooled if pooled else 0.0) * (1 - 3 / (4 * (len(a) + len(b)) - 9))
            result |= {
                "test": "Welch's t-test",
                "statistic": float(test.statistic),
                "p_value": float(test.pvalue),
                "effect_size": EffectSize(
                    name="hedges_g", value=hedges, magnitude=_magnitude(hedges, (0.2, 0.5, 0.8))
                ),
                "interval": Interval(
                    label=f"difference in mean {value} ({keys[0]} minus {keys[1]})",
                    low=float(interval.low),
                    high=float(interval.high),
                ),
            }
            summary = (
                f"Mean {value} is {_num(float(np.mean(a)))} for {keys[0]} and {_num(float(np.mean(b)))} "
                f"for {keys[1]}, a difference of {_num(difference)} "
                f"(95% CI {_num(float(interval.low))} to {_num(float(interval.high))})."
            )
        else:
            test = stats.mannwhitneyu(a, b, alternative="two-sided")
            rank_biserial = 2 * float(test.statistic) / (len(a) * len(b)) - 1
            result |= {
                "test": "Mann-Whitney U test",
                "statistic": float(test.statistic),
                "p_value": float(test.pvalue),
                "effect_size": EffectSize(
                    name="rank_biserial",
                    value=rank_biserial,
                    magnitude=_magnitude(rank_biserial, (0.1, 0.3, 0.5)),
                ),
            }
            summary = (
                f"Median {value} is {_num(float(np.median(a)))} for {keys[0]} and "
                f"{_num(float(np.median(b)))} for {keys[1]}."
            )
    else:
        n_total = sum(len(values) for values in arrays)
        grand = np.concatenate(arrays)
        if parametric:
            levene_p = float(stats.levene(*arrays, center="median").pvalue)
            between = sum(len(values) * (np.mean(values) - grand.mean()) ** 2 for values in arrays)
            total = float(((grand - grand.mean()) ** 2).sum())
            eta = float(between / total) if total else 0.0
            if levene_p < ALPHA:
                test = anova_oneway(arrays, use_var="unequal")
                name = "Welch's ANOVA"
                checks.append(
                    f"Equal spread: not met (Levene p {format_p(levene_p)}), so Welch's ANOVA is used."
                )
            else:
                test = stats.f_oneway(*arrays)
                name = "One-way ANOVA"
                checks.append(f"Equal spread: met (Levene p {format_p(levene_p)}).")
            result |= {
                "test": name,
                "statistic": float(test.statistic),
                "p_value": float(test.pvalue),
                "effect_size": EffectSize(
                    name="eta_squared", value=eta, magnitude=_magnitude(eta, (0.01, 0.06, 0.14))
                ),
            }
            highest = max(groups, key=lambda item: item["mean"])
            lowest = min(groups, key=lambda item: item["mean"])
            summary = (
                f"Mean {value} ranges from {_num(lowest['mean'])} ({lowest[group]}) to "
                f"{_num(highest['mean'])} ({highest[group]}) across {len(arrays)} groups."
            )
        else:
            test = stats.kruskal(*arrays)
            epsilon = float(test.statistic) / (n_total - 1)
            result |= {
                "test": "Kruskal-Wallis test",
                "statistic": float(test.statistic),
                "p_value": float(test.pvalue),
                "effect_size": EffectSize(
                    name="epsilon_squared", value=epsilon, magnitude=_magnitude(epsilon, (0.01, 0.06, 0.14))
                ),
            }
            highest = max(groups, key=lambda item: item["median"])
            lowest = min(groups, key=lambda item: item["median"])
            summary = (
                f"Median {value} ranges from {_num(lowest['median'])} ({lowest[group]}) to "
                f"{_num(highest['median'])} ({highest[group]}) across {len(arrays)} groups."
            )
        if result["p_value"] < ALPHA and len(arrays) <= MAX_PAIRWISE_GROUPS:
            result["pairwise"] = _pairwise(samples, parametric)
            checks.append("Pairwise follow-up tests are Holm-adjusted for the number of pairs.")
    effect = result["effect_size"]
    result["interpretation"] = (
        f"{summary} {result['test']}: {_verdict(result['p_value'])} "
        f"Effect size: {effect.magnitude} ({effect.name} = {_num(effect.value)})."
    )
    return result


def _pairwise(samples: dict[str, np.ndarray], parametric: bool) -> list[dict[str, Any]]:
    keys = list(samples)
    pairs, p_values = [], []
    for index, first in enumerate(keys):
        for second in keys[index + 1 :]:
            a, b = samples[first], samples[second]
            if parametric:
                p = float(stats.ttest_ind(a, b, equal_var=False).pvalue)
                difference = float(np.mean(a) - np.mean(b))
            else:
                p = float(stats.mannwhitneyu(a, b, alternative="two-sided").pvalue)
                difference = float(np.median(a) - np.median(b))
            pairs.append({"a": first, "b": second, "difference": difference, "p_value": p})
            p_values.append(p)
    adjusted = multipletests(p_values, method="holm")[1]
    for pair, p_adjusted in zip(pairs, adjusted, strict=True):
        pair["p_adjusted"] = float(p_adjusted)
        pair["significant"] = bool(p_adjusted < ALPHA)
    return sorted(pairs, key=lambda pair: pair["p_adjusted"])


def compare_categories(frame: pd.DataFrame, first: str, second: str) -> dict[str, Any]:
    data = pd.DataFrame({"a": _column(frame, first), "b": _column(frame, second)}).dropna()
    table = pd.crosstab(data["a"].astype(str), data["b"].astype(str))
    if min(table.shape) < 2:
        raise StatError(f"{first} and {second} each need at least two categories")
    if max(table.shape) > MAX_CATEGORIES:
        raise StatError(f"Too many categories to compare (at most {MAX_CATEGORIES} per column)")
    counts = table.to_numpy()
    expected = stats.contingency.expected_freq(counts)
    sparse_share = float((expected < 5).mean())
    cramers_v = float(stats.contingency.association(counts, method="cramer"))
    checks, cautions = [], []
    if counts.shape == (2, 2) and sparse_share > 0:
        odds, p_value = stats.fisher_exact(counts)
        test, statistic = "Fisher's exact test", float(odds)
        checks.append("Expected counts: some are below 5, so Fisher's exact test is used.")
    else:
        chi2, p_value, _, _ = stats.chi2_contingency(counts)
        test, statistic = "Chi-square test of independence", float(chi2)
        checks.append(f"Expected counts: {sparse_share:.0%} of cells expect fewer than 5.")
        if sparse_share > 0.2:
            cautions.append(
                "Over 20% of cells expect fewer than 5 counts; combine rare categories for a reliable test."
            )
    effect = EffectSize(name="cramers_v", value=cramers_v, magnitude=_magnitude(cramers_v, (0.1, 0.3, 0.5)))
    related = "are associated" if p_value < ALPHA else "show no clear association"
    groups = [
        {first: index, **{str(column): int(value) for column, value in row.items()}}
        for index, row in table.iterrows()
    ]
    return {
        "test": test,
        "statistic": statistic,
        "p_value": float(p_value),
        "effect_size": effect,
        "groups": groups,
        "checks": checks,
        "cautions": cautions,
        "n": int(counts.sum()),
        "interpretation": (
            f"{first} and {second} {related} across {int(counts.sum()):,} rows. "
            f"{test}: {_verdict(float(p_value))} "
            f"Effect size: {effect.magnitude} (Cramer's V = {_num(cramers_v)})."
        ),
    }


def correlation(frame: pd.DataFrame, first: str, second: str) -> dict[str, Any]:
    data = pd.DataFrame({"x": _numeric(frame, first), "y": _numeric(frame, second)}).dropna()
    if len(data) < 4:
        raise StatError("A correlation needs at least 4 rows with both values")
    if data["x"].nunique() < 2 or data["y"].nunique() < 2:
        raise StatError("A correlation needs both columns to vary")
    pearson = stats.pearsonr(data["x"], data["y"])
    spearman = stats.spearmanr(data["x"], data["y"])
    pearson_ci = pearson.confidence_interval(0.95)
    rho = float(spearman.statistic)
    checks = [f"Pearson r = {_num(float(pearson.statistic))}; Spearman rho = {_num(rho)}."]
    cautions = ["Correlation does not show that one causes the other."]
    if len(data) < NORMAL_ENOUGH_N:
        cautions.append(f"Only {len(data)} rows; the estimate is imprecise.")
    rank_based = abs(float(pearson.statistic) - rho) > 0.2
    if rank_based:
        checks.append(
            "Pearson and Spearman disagree by more than 0.2, so the rank-based Spearman result is used."
        )
        cautions.append("The relationship may be non-linear or driven by a few extreme values.")
        z, spread = math.atanh(max(min(rho, 0.999999), -0.999999)), 1.96 / math.sqrt(len(data) - 3)
        test, value, p_value = "Spearman rank correlation", rho, float(spearman.pvalue)
        interval = Interval(
            label="Spearman rho (approximate)", low=math.tanh(z - spread), high=math.tanh(z + spread)
        )
        name = "spearman_rho"
    else:
        test, value, p_value = "Pearson correlation", float(pearson.statistic), float(pearson.pvalue)
        interval = Interval(label="Pearson r", low=float(pearson_ci.low), high=float(pearson_ci.high))
        name = "pearson_r"
    effect = EffectSize(name=name, value=value, magnitude=_magnitude(value, (0.1, 0.3, 0.5)))
    direction = "positive" if value > 0 else "negative"
    tendency = (
        f"Higher {first} tends to go with {'higher' if value > 0 else 'lower'} {second}. "
        if p_value < ALPHA
        else ""
    )
    return {
        "test": test,
        "statistic": value,
        "p_value": p_value,
        "effect_size": effect,
        "interval": interval,
        "groups": [
            {"measure": "pearson_r", "value": float(pearson.statistic), "p_value": float(pearson.pvalue)},
            {"measure": "spearman_rho", "value": rho, "p_value": float(spearman.pvalue)},
        ],
        "checks": checks,
        "cautions": cautions,
        "interpretation": (
            f"{first} and {second} have a {effect.magnitude} {direction} correlation "
            f"({name} = {_num(value)}, 95% CI {_num(interval.low)} to {_num(interval.high)}, "
            f"n = {len(data):,}). "
            f"{tendency}{test}: {_verdict(p_value)}"
        ),
        "n": len(data),
    }


METHODS = {
    "compare_groups": compare_groups,
    "compare_categories": compare_categories,
    "correlation": correlation,
}


def run_test(
    name: str,
    method: TestMethod,
    data_source: str,
    x: str,
    y: str,
    frame: pd.DataFrame,
    note: str | None = None,
) -> StatArtifact:
    result = METHODS[method](frame, x, y)
    n = result.pop("n")
    effect = result.get("effect_size")
    if result["p_value"] >= ALPHA and effect and effect.magnitude in {"medium", "large"}:
        result["cautions"].append(
            "The observed effect is sizeable, but there is not enough data to rule out chance; "
            "more data would settle it."
        )
    return StatArtifact(name=name, method=method, data_source=data_source, x=x, y=y, n=n, note=note, **result)


def adjust_for_multiple_tests(artifacts: list[StatArtifact]) -> None:
    if len(artifacts) < 2:
        return
    adjusted = multipletests([artifact.p_value for artifact in artifacts], method="holm")[1]
    for artifact, p_adjusted in zip(artifacts, adjusted, strict=True):
        artifact.p_adjusted = float(p_adjusted)
        artifact.checks.append(
            f"{len(artifacts)} tests in this analysis; Holm-adjusted p {format_p(p_adjusted)}."
        )
        if artifact.p_value < ALPHA <= p_adjusted:
            artifact.cautions.append(
                "Not significant after adjusting for the number of tests in this analysis."
            )
