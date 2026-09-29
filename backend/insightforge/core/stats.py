import math
import re
from typing import Any, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field
from scipy import stats
from statsmodels.stats.multitest import multipletests
from statsmodels.stats.oneway import anova_oneway

from insightforge.core.timeseries import SeriesError, anomalies, forecast

TestMethod = Literal[
    "compare_groups",
    "compare_categories",
    "correlation",
    "explain_change",
    "regression",
    "forecast",
    "anomalies",
]

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
    by: list[str] = Field(default_factory=list)
    controls: list[str] = Field(default_factory=list)
    grain: str | None = None
    horizon: int | None = None
    history: list[dict[str, Any]] = Field(default_factory=list)
    statistic: float | None = None
    p_value: float | None = None
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
        row: dict[str, Any] = {"test": self.test, "n": self.n}
        if self.p_value is not None:
            row["p_value"] = self.p_value
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



def _period_order(labels: list[Any]) -> tuple[Any, Any, str | None]:
    lowered = {str(label).strip().lower(): label for label in labels}
    if set(lowered) == {"before", "after"}:
        return lowered["before"], lowered["after"], None
    for convert in (pd.to_datetime, pd.to_numeric):
        try:
            values = list(convert(pd.Series([str(label) for label in labels])))
        except (ValueError, TypeError):
            continue
        return (labels[0], labels[1], None) if values[0] <= values[1] else (labels[1], labels[0], None)
    first, second = sorted(labels, key=str)
    return first, second, f"Period order was assumed alphabetical: {first} before {second}."


def _change_table(old: pd.DataFrame, new: pd.DataFrame, column: str) -> pd.DataFrame:
    table = pd.DataFrame(
        {
            "before": old.groupby(column)["value"].sum(),
            "after": new.groupby(column)["value"].sum(),
            "rows_before": old.groupby(column)["value"].size(),
            "rows_after": new.groupby(column)["value"].size(),
        }
    ).fillna(0.0)
    if len(table) > MAX_CATEGORIES:
        raise StatError(f"{column} has {len(table)} values; break changes down by at most {MAX_CATEGORIES}")
    table["change"] = table["after"] - table["before"]
    mean_before = (table["before"] / table["rows_before"]).where(table["rows_before"] > 0)
    mean_after = (table["after"] / table["rows_after"]).where(table["rows_after"] > 0)
    mean_before, mean_after = mean_before.fillna(mean_after), mean_after.fillna(mean_before)
    share_before, share_after = table["rows_before"] / len(old), table["rows_after"] / len(new)
    table["mix_effect"] = (share_after - share_before) * (mean_before + mean_after) / 2
    table["rate_effect"] = (mean_after - mean_before) * (share_before + share_after) / 2
    return table.reindex(table["change"].abs().sort_values(ascending=False).index)


def explain_change(frame: pd.DataFrame, period: str, measure: str, by: list[str]) -> dict[str, Any]:
    if not by:
        raise StatError("Explaining a change needs at least one column to break it down by")
    if len(by) > 3:
        raise StatError("Break a change down by at most 3 columns")
    counted = measure not in frame.columns and re.fullmatch(r"\d+(\.\d+)?", measure.strip()) is not None
    values = pd.Series(float(measure), index=frame.index) if counted else _numeric(frame, measure)
    measure = "rows" if counted else measure
    data = pd.DataFrame({"period": _column(frame, period), "value": values})
    for column in by:
        data[column] = _column(frame, column).astype("string").fillna("(missing)")
    data = data.dropna(subset=["period", "value"])
    labels = list(pd.unique(data["period"]))
    if len(labels) != 2:
        shown = ", ".join(map(str, labels[:5]))
        raise StatError(
            f"Explaining a change needs exactly two periods in {period!r}; found {len(labels)}: {shown}"
        )
    before, after, order_caution = _period_order(labels)
    old, new = data[data["period"] == before], data[data["period"] == after]
    total_old, total_new = float(old["value"].sum()), float(new["value"].sum())
    change = total_new - total_old
    mean_old, mean_new = float(old["value"].mean()), float(new["value"].mean())
    groups: list[dict[str, Any]] = []
    cautions = ["This shows where the change happened, not why it happened."]
    if order_caution:
        cautions.append(order_caution)
    summaries = []
    for column in by:
        table = _change_table(old, new, column)
        spread = float(table["change"].abs().sum())
        concentration = float(table["change"].abs().iloc[0]) / spread if spread else 0.0
        summaries.append((concentration, column, table))
        for segment, row in table.head(10).iterrows():
            groups.append(
                {
                    "dimension": column,
                    "segment": str(segment),
                    "before": float(row["before"]),
                    "after": float(row["after"]),
                    "change": float(row["change"]),
                    "share_of_change": float(row["change"] / change) if change else None,
                    "mix_effect": float(row["mix_effect"]),
                    "rate_effect": float(row["rate_effect"]),
                }
            )
        thin = [
            str(key) for key, row in table.iterrows() if 0 < min(row["rows_before"], row["rows_after"]) < 5
        ]
        appeared = [str(key) for key, row in table.iterrows() if row["rows_before"] == 0]
        vanished = [str(key) for key, row in table.iterrows() if row["rows_after"] == 0]
        if thin:
            cautions.append(f"{column}: fewer than 5 rows in a period for {', '.join(thin[:5])}.")
        if appeared or vanished:
            cautions.append(
                f"{column}: new in {after}: {', '.join(appeared[:5]) or 'none'}; "
                f"gone in {after}: {', '.join(vanished[:5]) or 'none'}."
            )
    concentration, primary, table = max(summaries, key=lambda item: item[0])
    top = [
        f"{segment} ({_num(float(row['change']))}"
        + (f", {row['change'] / change:.0%} of the change)" if change else ")")
        for segment, row in table.head(2).iterrows()
    ]
    mix, rate = float(table["mix_effect"].sum()), float(table["rate_effect"].sum())
    direction = "rose" if change > 0 else "fell" if change < 0 else "did not change"
    percent = f" ({change / total_old:+.1%})" if total_old else ""
    average = (
        ""
        if data["value"].nunique() <= 1
        else f" The average {measure} per row moved from {_num(mean_old)} to {_num(mean_new)}: "
        f"{_num(mix)} of that came from a shift in the mix of {primary} and {_num(rate)} from changes "
        f"within each {primary}."
    )
    return {
        "test": "Change breakdown (mix and rate)",
        "statistic": change,
        "p_value": None,
        "groups": groups,
        "checks": [
            f"{len(old):,} rows in {before} and {len(new):,} rows in {after}.",
            "Segment changes add up to the total change; mix and rate effects add up exactly to the change "
            "in the average (symmetric split).",
            f"{primary} concentrates the change most: its largest segment accounts for {concentration:.0%} "
            "of the movement.",
        ],
        "cautions": cautions,
        "interpretation": (
            f"Total {measure} {direction} from {_num(total_old)} ({before}) to {_num(total_new)} ({after}), "
            f"a change of {_num(change)}{percent}. By {primary}, the largest contributors were "
            f"{' and '.join(top)}.{average}"
        ),
        "n": len(data),
    }


def _design(data: pd.DataFrame, predictor: str, controls: list[str]) -> pd.DataFrame:
    import statsmodels.api as sm

    design = pd.DataFrame({predictor: pd.to_numeric(data[predictor], errors="coerce").astype(float)})
    for column in controls:
        numeric = pd.to_numeric(data[column], errors="coerce")
        if numeric.notna().all():
            design[column] = numeric.astype(float)
        else:
            dummies = pd.get_dummies(
                data[column].astype(str), prefix=column, prefix_sep="=", drop_first=True
            )
            design = design.join(dummies.astype(float))
    return sm.add_constant(design, has_constant="add")


def regression(frame: pd.DataFrame, predictor: str, outcome: str, controls: list[str]) -> dict[str, Any]:
    import statsmodels.api as sm
    from statsmodels.stats.diagnostic import het_breuschpagan
    from statsmodels.stats.outliers_influence import variance_inflation_factor
    from statsmodels.stats.stattools import jarque_bera

    if len({outcome, predictor, *controls}) != 2 + len(controls):
        raise StatError("The outcome, predictor and controls must be different columns")
    data = pd.DataFrame({column: _column(frame, column) for column in [outcome, predictor, *controls]})
    data = data.dropna()
    y = pd.to_numeric(data[outcome], errors="coerce")
    if y.isna().any() or pd.to_numeric(data[predictor], errors="coerce").isna().any():
        raise StatError(
            f"Regression needs numeric {outcome} and {predictor}; compare groups instead for a category"
        )
    design = _design(data, predictor, controls)
    if len(data) < design.shape[1] + 3:
        raise StatError(f"Regression needs more rows than predictors; only {len(data)} usable rows")
    if design[predictor].nunique() < 2:
        raise StatError(f"{predictor} does not vary, so its effect cannot be estimated")
    y = y.astype(float)
    model = sm.OLS(y, design).fit()
    checks = []
    cautions = ["Regression shows association, not proof that one causes the other."]
    breusch_p = float(het_breuschpagan(model.resid, design)[1])
    if breusch_p < ALPHA:
        model = sm.OLS(y, design).fit(cov_type="HC3")
        checks.append(
            f"Uneven spread of errors (Breusch-Pagan p {format_p(breusch_p)}); robust HC3 errors are used."
        )
    else:
        checks.append(f"Even spread of errors (Breusch-Pagan p {format_p(breusch_p)}).")
    jarque_p = float(jarque_bera(model.resid)[1])
    checks.append(f"Residual normality: Jarque-Bera p {format_p(jarque_p)}.")
    if jarque_p < ALPHA and len(data) < 50:
        cautions.append("Errors are not bell-shaped and the sample is small; p-values may be off.")
    terms = [column for column in design.columns if column != "const"]
    if len(terms) > 1:
        inflation = {
            column: float(variance_inflation_factor(design.to_numpy(), list(design.columns).index(column)))
            for column in terms
        }
        checks.append(f"Largest variance inflation factor: {max(inflation.values()):.1f}.")
        if high := [column for column, value in inflation.items() if value > 5]:
            cautions.append(
                f"{', '.join(high)} overlap strongly with other predictors; effects are unstable."
            )
    if len(data) < 10 * len(terms):
        cautions.append(f"Only {len(data)} rows for {len(terms)} predictors; estimates are imprecise.")
    bounds = model.conf_int()
    coefficient, p_value = float(model.params[predictor]), float(model.pvalues[predictor])
    low, high = float(bounds.loc[predictor, 0]), float(bounds.loc[predictor, 1])
    r2 = float(model.rsquared)
    held = f", holding {', '.join(controls)} fixed" if controls else ""
    amount = f"{_num(abs(coefficient))} {'more' if coefficient >= 0 else 'less'}"
    return {
        "test": "Linear regression (OLS)",
        "statistic": coefficient,
        "p_value": p_value,
        "effect_size": EffectSize(name="r_squared", value=r2, magnitude=_magnitude(r2, (0.02, 0.13, 0.26))),
        "interval": Interval(label=f"change in {outcome} per 1 {predictor}", low=low, high=high),
        "groups": [
            {
                "term": term,
                "coefficient": float(model.params[term]),
                "ci_low": float(bounds.loc[term, 0]),
                "ci_high": float(bounds.loc[term, 1]),
                "p_value": float(model.pvalues[term]),
            }
            for term in list(design.columns)[:20]
        ],
        "checks": checks,
        "cautions": cautions,
        "interpretation": (
            f"Each additional 1 {predictor} is associated with {amount} {outcome} "
            f"(95% CI {_num(low)} to {_num(high)}){held}. The model explains {r2:.0%} of the variation in "
            f"{outcome} (R-squared = {_num(r2)}, n = {len(data):,}). {predictor}: {_verdict(p_value)}"
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
    by: list[str] | None = None,
    controls: list[str] | None = None,
    grain: str | None = None,
    horizon: int | None = None,
) -> StatArtifact:
    by, controls = list(by or []), list(controls or [])
    try:
        if method == "explain_change":
            result = explain_change(frame, x, y, by)
        elif method == "regression":
            result = regression(frame, x, y, controls)
        elif method == "forecast":
            result = forecast(frame, x, y, grain, horizon)
        elif method == "anomalies":
            result = anomalies(frame, x, y, grain)
        else:
            result = METHODS[method](frame, x, y)
    except SeriesError as error:
        raise StatError(str(error)) from error
    n = result.pop("n")
    effect, p_value = result.get("effect_size"), result.get("p_value")
    if p_value is not None and p_value >= ALPHA and effect and effect.magnitude in {"medium", "large"}:
        result["cautions"].append(
            "The observed effect is sizeable, but there is not enough data to rule out chance; "
            "more data would settle it."
        )
    return StatArtifact(
        name=name,
        method=method,
        data_source=data_source,
        x=x,
        y=y,
        by=by if method == "explain_change" else [],
        controls=controls if method == "regression" else [],
        n=n,
        note=note,
        **result,
    )


def adjust_for_multiple_tests(artifacts: list[StatArtifact]) -> None:
    tested = [artifact for artifact in artifacts if artifact.p_value is not None]
    if len(tested) < 2:
        return
    adjusted = multipletests([artifact.p_value for artifact in tested], method="holm")[1]
    for artifact, p_adjusted in zip(tested, adjusted, strict=True):
        artifact.p_adjusted = float(p_adjusted)
        artifact.checks.append(
            f"{len(tested)} tests in this analysis; Holm-adjusted p {format_p(p_adjusted)}."
        )
        if artifact.p_value is not None and artifact.p_value < ALPHA <= p_adjusted:
            artifact.cautions.append(
                "Not significant after adjusting for the number of tests in this analysis."
            )
