import math
import warnings
from typing import Any, Literal

import numpy as np
import pandas as pd

Grain = Literal["day", "week", "month"]

FREQUENCIES = {"day": "D", "week": "W-MON", "month": "MS"}
SEASONS = {"day": 7, "week": 52, "month": 12}
DEFAULT_HORIZON = {"day": 14, "week": 8, "month": 3}
MIN_POINTS = 8
ROBUST_Z = 3.5


class SeriesError(ValueError):
    pass


def _grain(dates: pd.Series, requested: Grain | None) -> Grain:
    if requested:
        return requested
    span = (dates.max() - dates.min()).days
    return "month" if span > 730 else "week" if span > 120 else "day"


def build_series(
    frame: pd.DataFrame, date_column: str, value_column: str, grain: Grain | None
) -> tuple[pd.Series, Grain, list[str]]:
    for column in (date_column, value_column):
        if column not in frame.columns:
            raise SeriesError(
                f"Column {column!r} is not in the result; available: {', '.join(map(str, frame.columns))}"
            )
    dates = pd.to_datetime(frame[date_column], errors="coerce")
    values = pd.to_numeric(frame[value_column], errors="coerce")
    data = pd.DataFrame({"date": dates, "value": values}).dropna()
    if data.empty:
        raise SeriesError(f"{date_column} needs dates and {value_column} needs numbers")
    chosen = _grain(data["date"], grain)
    notes: list[str] = []
    step = pd.tseries.frequencies.to_offset(FREQUENCIES[chosen])
    series = (
        data.set_index("date")["value"]
        .resample(FREQUENCIES[chosen], label="left", closed="left")
        .sum()
    )
    first_date, last_date = data["date"].min().normalize(), data["date"].max().normalize()
    slack = (series.index[0] + step - series.index[0]) * 0.2
    if len(series) > 2 and first_date - series.index[0] > slack:
        notes.append(f"The first {chosen} (from {series.index[0].date()}) was incomplete and left out.")
        series = series.iloc[1:]
    period_end = series.index[-1] + step - pd.Timedelta(days=1)
    if len(series) > 2 and period_end.normalize() - last_date > slack:
        notes.append(
            f"The last {chosen} (from {series.index[-1].date()}) was incomplete and left out."
        )
        series = series.iloc[:-1]
    empty = int((series == 0).sum())
    if empty:
        notes.append(f"{empty} {chosen}s had no rows and count as 0.")
    if len(series) < MIN_POINTS:
        raise SeriesError(
            f"Only {len(series)} complete {chosen}s of data; at least {MIN_POINTS} are needed"
        )
    return series.astype(float), chosen, notes


def _naive(history: np.ndarray, horizon: int, season: int) -> np.ndarray:
    return np.repeat(history[-1], horizon)


def _seasonal_naive(history: np.ndarray, horizon: int, season: int) -> np.ndarray:
    last = history[-season:]
    return np.array([last[step % season] for step in range(horizon)])


def _holt_winters(history: np.ndarray, horizon: int, season: int) -> np.ndarray:
    from statsmodels.tsa.holtwinters import ExponentialSmoothing

    seasonal = "add" if len(history) >= 2 * season else None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model = ExponentialSmoothing(
            history,
            trend="add",
            damped_trend=True,
            seasonal=seasonal,
            seasonal_periods=season if seasonal else None,
            initialization_method="estimated",
        ).fit()
    return np.asarray(model.forecast(horizon), dtype=float)


NAIVE = "naive (repeat the last value)"
MODELS = {
    NAIVE: _naive,
    "seasonal naive (repeat last season)": _seasonal_naive,
    "damped Holt-Winters": _holt_winters,
}


def forecast(
    frame: pd.DataFrame,
    date_column: str,
    value_column: str,
    grain: Grain | None = None,
    horizon: int | None = None,
) -> dict[str, Any]:
    series, chosen, notes = build_series(frame, date_column, value_column, grain)
    season = SEASONS[chosen]
    steps = max(1, min(int(horizon or DEFAULT_HORIZON[chosen]), 36))
    holdout = max(3, min(steps, len(series) // 4))
    train, test = series.to_numpy()[:-holdout], series.to_numpy()[-holdout:]
    scores: dict[str, float] = {}
    for name, model in MODELS.items():
        if name.startswith("seasonal") and len(train) < season:
            continue
        try:
            predicted = model(train, holdout, season)
        except Exception:
            continue
        if np.all(np.isfinite(predicted)):
            scores[name] = float(np.mean(np.abs(predicted - test)))
    if not scores:
        raise SeriesError("No forecasting model could be fitted to this series")
    best = min(scores, key=scores.__getitem__)
    naive_error = scores[NAIVE]
    fitted = MODELS[best](train, holdout, season)
    spread = float(np.std(fitted - test, ddof=0)) or float(np.mean(np.abs(fitted - test)))
    future = MODELS[best](series.to_numpy(), steps, season)
    index = pd.date_range(
        series.index[-1], periods=steps + 1, freq=FREQUENCIES[chosen]
    )[1:]
    rows = [
        {
            "period": date.date().isoformat(),
            "forecast": float(value),
            "low": float(value - 1.96 * spread * math.sqrt(step + 1)),
            "high": float(value + 1.96 * spread * math.sqrt(step + 1)),
        }
        for step, (date, value) in enumerate(zip(index, future, strict=True))
    ]
    skill = 1 - scores[best] / naive_error if naive_error else 0.0
    ranked = sorted(scores.items(), key=lambda item: item[1])
    beat = f", {skill:.0%} lower than the naive forecast." if best != NAIVE else "."
    checks = [
        f"Backtest: each model forecast the last {holdout} {chosen}s from the earlier data; average error "
        + "; ".join(f"{name} {_fmt(error)}" for name, error in ranked)
        + ".",
        f"{best} had the smallest backtest error{beat}",
        *notes,
    ]
    cautions = [
        "The range is approximate: it is built from the backtest errors and widens with each step ahead.",
        "Forecasts assume the past pattern continues; they cannot foresee new events.",
    ]
    if len(series) < 2 * season:
        cautions.append(
            f"Less than two full seasons ({2 * season} {chosen}s) of history, so seasonality is not modelled."
        )
    if best == NAIVE:
        cautions.append("No model beat simply repeating the last value; treat the forecast as a baseline.")
    total = float(sum(row["forecast"] for row in rows))
    recent = float(series.iloc[-min(len(series), steps) :].mean())
    return {
        "test": f"Forecast ({best})",
        "grain": chosen,
        "horizon": steps,
        "statistic": float(future[0]),
        "p_value": None,
        "groups": rows,
        "history": [
            {"period": date.date().isoformat(), "value": float(value)} for date, value in series.items()
        ],
        "checks": checks,
        "cautions": cautions,
        "interpretation": (
            f"Forecast of total {value_column} per {chosen} for the next {steps} {chosen}s: "
            f"{_fmt(rows[0]['forecast'])} for {rows[0]['period']} (approximate 95% range "
            f"{_fmt(rows[0]['low'])} to {_fmt(rows[0]['high'])}), {_fmt(total)} in total over the {steps} "
            f"{chosen}s. The recent average was {_fmt(recent)} per {chosen}."
        ),
        "n": len(series),
    }


def anomalies(
    frame: pd.DataFrame, date_column: str, value_column: str, grain: Grain | None = None
) -> dict[str, Any]:
    series, chosen, notes = build_series(frame, date_column, value_column, grain)
    season = SEASONS[chosen]
    values = series.to_numpy()
    if len(series) >= 2 * season:
        from statsmodels.tsa.seasonal import STL

        fit = STL(series, period=season, robust=True).fit()
        expected = (fit.trend + fit.seasonal).to_numpy()
        method = "seasonal-trend decomposition (STL) with a robust score"
    else:
        window = min(7, max(3, len(series) // 4)) | 1
        expected = series.rolling(window, center=True, min_periods=1).median().to_numpy()
        method = f"a {window}-{chosen} rolling median with a robust score"
    residual = values - expected
    mad = float(np.median(np.abs(residual - np.median(residual))))
    scale = 1.4826 * mad
    if scale == 0:
        scale = float(np.std(residual)) or 1.0
    scores = (residual - np.median(residual)) / scale
    flagged = [
        {
            "period": date.date().isoformat(),
            "value": float(value),
            "expected": float(guess),
            "difference": float(value - guess),
            "score": float(score),
        }
        for date, value, guess, score in zip(series.index, values, expected, scores, strict=True)
        if abs(score) > ROBUST_Z
    ]
    flagged.sort(key=lambda row: abs(row["score"]), reverse=True)
    if flagged:
        top = flagged[0]
        detail = (
            f" The most unusual was {top['period']}: {_fmt(top['value'])} against about "
            f"{_fmt(top['expected'])} expected."
        )
    else:
        detail = ""
    return {
        "test": "Unusual values (robust score)",
        "grain": chosen,
        "statistic": float(len(flagged)),
        "p_value": None,
        "groups": flagged[:20],
        "history": [
            {"period": date.date().isoformat(), "value": float(value), "expected": float(guess)}
            for date, value, guess in zip(series.index, values, expected, strict=True)
        ],
        "checks": [
            f"Expected values come from {method}; a {chosen} is flagged when its robust score "
            f"(distance from expected in MAD units) exceeds {ROBUST_Z}.",
            *notes,
        ],
        "cautions": [
            "Unusual does not mean wrong: check flagged periods against known events before acting.",
        ],
        "interpretation": (
            f"{len(flagged)} of {len(series)} {chosen}s of total {value_column} look unusual.{detail}"
        ),
        "n": len(series),
    }


def _fmt(value: float) -> str:
    return f"{value:,.0f}" if abs(value) >= 1000 else f"{value:,.2f}".rstrip("0").rstrip(".")
