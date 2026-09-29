import math
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

SRM_ALPHA = 0.001
ALPHA = 0.05


class ExperimentError(ValueError):
    pass


def _fmt(value: float) -> str:
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    return f"{value:,.3g}" if abs(value) < 1 else f"{value:,.2f}".rstrip("0").rstrip(".")


def _p(value: float) -> str:
    return "< 0.001" if value < 0.001 else f"= {value:.3f}"


def _control_first(labels: list[str]) -> list[str]:
    preferred = ("control", "a", "baseline", "old", "0", "false")
    control = next((label for label in labels if label.strip().lower() in preferred), sorted(labels)[0])
    return [control, next(label for label in labels if label != control)]


def ab_test(frame: pd.DataFrame, variant: str, outcome: str, covariate: str | None = None) -> dict[str, Any]:
    from statsmodels.stats.proportion import confint_proportions_2indep, proportions_ztest

    for column in [variant, outcome, *([covariate] if covariate else [])]:
        if column not in frame.columns:
            raise ExperimentError(
                f"Column {column!r} is not in the result; available: {', '.join(map(str, frame.columns))}"
            )
    data = pd.DataFrame(
        {
            "variant": frame[variant].astype("string"),
            "y": pd.to_numeric(frame[outcome], errors="coerce"),
            **({"x": pd.to_numeric(frame[covariate], errors="coerce")} if covariate else {}),
        }
    ).dropna()
    labels = sorted(data["variant"].unique().tolist())
    if len(labels) != 2:
        raise ExperimentError(f"An A/B test needs exactly two variants in {variant!r}; found {len(labels)}")
    control, treatment = _control_first(labels)
    a, b = data[data["variant"] == control], data[data["variant"] == treatment]
    if min(len(a), len(b)) < 10:
        raise ExperimentError("Each variant needs at least 10 rows")
    checks, cautions = [], []
    srm_p = float(stats.chisquare([len(a), len(b)]).pvalue)
    checks.append(
        f"Split: {len(a):,} {control} vs {len(b):,} {treatment} (sample-ratio check p {_p(srm_p)})."
    )
    if srm_p < SRM_ALPHA:
        cautions.append(
            "Sample ratio mismatch: the split is far from 50/50, which often means a bug in assignment or "
            "tracking; results may be biased."
        )
    binary = set(data["y"].unique()) <= {0.0, 1.0}
    reduction = None
    if covariate and not binary:
        pooled = data["x"] - data["x"].mean()
        theta = float(np.cov(data["y"], data["x"], ddof=1)[0, 1] / np.var(data["x"], ddof=1))
        adjusted = data["y"] - theta * pooled
        reduction = 1 - float(np.var(adjusted, ddof=1) / np.var(data["y"], ddof=1))
        a_values, b_values = adjusted[a.index], adjusted[b.index]
        checks.append(
            f"CUPED with {covariate} reduced the variance by {reduction:.0%} (theta = {_fmt(theta)})."
        )
    else:
        a_values, b_values = a["y"], b["y"]
        if covariate:
            checks.append("CUPED was skipped because the outcome is a yes/no conversion.")
    groups = [
        {"variant": control, "rows": int(len(a)), "mean": float(a["y"].mean())},
        {"variant": treatment, "rows": int(len(b)), "mean": float(b["y"].mean())},
    ]
    if binary:
        successes = np.array([int(a["y"].sum()), int(b["y"].sum())])
        totals = np.array([len(a), len(b)])
        p_value = float(proportions_ztest(successes[::-1], totals[::-1])[1])
        low, high = confint_proportions_2indep(
            successes[1], totals[1], successes[0], totals[0], method="wald"
        )
        difference = float(b["y"].mean() - a["y"].mean())
        test = "Two-proportion z-test"
        label = f"difference in conversion rate ({treatment} minus {control})"
        detail = f"Conversion is {a['y'].mean():.1%} for {control} and {b['y'].mean():.1%} for {treatment}"
    else:
        result = stats.ttest_ind(b_values, a_values, equal_var=False)
        interval = result.confidence_interval(0.95)
        low, high = float(interval.low), float(interval.high)
        p_value, difference = float(result.pvalue), float(b_values.mean() - a_values.mean())
        test = "Welch's t-test" + (" with CUPED" if reduction is not None else "")
        label = f"difference in mean {outcome} ({treatment} minus {control})"
        detail = (
            f"Mean {outcome} is {_fmt(float(a['y'].mean()))} for {control} and "
            f"{_fmt(float(b['y'].mean()))} for {treatment}"
        )
    base = float(a["y"].mean())
    lift = difference / base if base else 0.0
    magnitude = (
        "negligible"
        if abs(lift) < 0.01
        else "small"
        if abs(lift) < 0.05
        else "medium"
        if abs(lift) < 0.1
        else "large"
    )
    verdict = (
        f"p {_p(p_value)}, so the difference is unlikely to be chance alone."
        if p_value < ALPHA
        else f"p {_p(p_value)}, so the test cannot tell the variants apart; the true effect may be zero."
    )
    if p_value >= ALPHA and abs(lift) >= 0.05:
        cautions.append("The observed lift is sizeable but uncertain; the test may need more traffic.")
    return {
        "test": test,
        "statistic": difference,
        "p_value": p_value,
        "effect_size": {"name": "relative_lift", "value": lift, "magnitude": magnitude},
        "interval": {"label": label, "low": float(low), "high": float(high), "level": 0.95},
        "groups": groups,
        "checks": checks,
        "cautions": cautions,
        "interpretation": (
            f"{detail} ({lift:+.1%} relative lift; 95% CI for the difference {_fmt(float(low))} to "
            f"{_fmt(float(high))}). {test}: {verdict}"
        ),
        "n": int(len(data)),
    }


def conclusion_holds(
    original_p: float, original_sign: float, alternatives: list[tuple[str, float, float]]
) -> tuple[list[dict[str, Any]], str, bool]:
    significant = original_p < ALPHA
    rows = []
    for label, p_value, sign in alternatives:
        holds = (p_value < ALPHA) == significant and (
            not significant or math.copysign(1, sign) == math.copysign(1, original_sign)
        )
        rows.append({"analysis": label, "p_value": float(p_value), "holds": bool(holds)})
    held = sum(row["holds"] for row in rows)
    names = ", ".join(row["analysis"] for row in rows)
    message = f"Robustness: the conclusion holds in {held} of {len(rows)} alternative analyses ({names})."
    return rows, message, held == len(rows)
