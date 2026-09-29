import json

import numpy as np
import pandas as pd
import pytest

from insightforge.core.agent import InsightForgeAgent
from insightforge.core.artifacts import PlotArtifact, StatArtifact
from insightforge.core.catalog import DataCatalog
from insightforge.core.llm import FakeLLMClient
from insightforge.core.planner import SERIES_CUES, Planner
from insightforge.core.stats import StatError, run_test
from insightforge.core.timeseries import build_series


def _daily(values: list[float], start: str = "2025-01-01") -> pd.DataFrame:
    return pd.DataFrame({"day": pd.date_range(start, periods=len(values), freq="D"), "amount": values})


def test_series_are_totalled_per_grain_and_drop_incomplete_edges():
    frame = _daily([1.0] * 70, start="2025-01-02")
    series, grain, notes = build_series(frame, "day", "amount", None)
    assert grain == "day" and len(series) == 70
    weekly, grain, notes = build_series(frame, "day", "amount", "week")
    assert grain == "week"
    assert weekly.index[0] == pd.Timestamp("2025-01-06") and set(weekly) == {7.0}
    assert any(note.startswith("The first week (from 2024-12-30)") for note in notes)
    assert any(note.startswith("The last week") for note in notes)
    with pytest.raises(ValueError, match="at least 8 are needed"):
        build_series(frame, "day", "amount", "month")


def test_forecasts_are_backtested_and_ranges_widen():
    frame = _daily([100 + 2.0 * step for step in range(120)])
    result = run_test("f", "forecast", "rows", "day", "amount", frame, grain="day", horizon=5)
    assert result.method == "forecast" and result.grain == "day" and result.horizon == 5
    assert len(result.groups) == 5 and result.statistic == result.groups[0]["forecast"]
    assert result.groups[0]["period"] == "2025-05-01"
    assert result.statistic == pytest.approx(340, rel=0.02)
    widths = [row["high"] - row["low"] for row in result.groups]
    assert widths == sorted(widths)
    assert result.checks[0].startswith("Backtest: each model forecast the last 5 days")
    assert "damped Holt-Winters had the smallest backtest error" in result.checks[1]
    assert len(result.history) == 120 and result.p_value is None


def test_noise_that_no_model_beats_is_flagged_as_a_baseline():
    frame = _daily(list(np.cumsum(np.random.default_rng(3).normal(0, 5, 60)) + 500))
    result = run_test("f", "forecast", "rows", "day", "amount", frame, grain="day", horizon=3)
    names = " ".join(result.checks)
    assert "naive (repeat the last value)" in names
    if result.test == "Forecast (naive (repeat the last value))":
        assert any("treat the forecast as a baseline" in caution for caution in result.cautions)


def test_anomalies_flag_the_spike_only():
    values = [100.0 + (step % 3) for step in range(60)]
    values[40] = 400.0
    result = run_test("a", "anomalies", "rows", "day", "amount", _daily(values), grain="day")
    assert result.statistic == 1
    assert result.groups[0]["period"] == "2025-02-10" and result.groups[0]["value"] == 400
    assert "1 of 60 days of total amount look unusual" in result.interpretation
    assert all("expected" in row for row in result.history)


def test_series_errors_become_stat_errors():
    with pytest.raises(StatError, match="not in the result"):
        run_test("f", "forecast", "rows", "missing", "amount", _daily([1.0] * 20))
    with pytest.raises(StatError, match="at least 8"):
        run_test("f", "forecast", "rows", "day", "amount", _daily([1.0] * 5), grain="day")


def test_series_cues():
    assert SERIES_CUES.search("Forecast total amount for the next 3 months")
    assert SERIES_CUES.search("Were there any unusual weeks?")
    assert not SERIES_CUES.search("What is the total amount by region?")


def test_forecast_questions_get_a_forecast_step_and_chart():
    catalog = DataCatalog()
    sales = _daily([100 + step for step in range(90)]).rename(columns={"day": "sold_on"})
    catalog.register_df("sales", sales)
    choice = {
        "series": True,
        "method": "forecast",
        "x": "sold_on",
        "y": "amount",
        "grain": "day",
        "horizon": 7,
        "sql": "SELECT sold_on, amount FROM sales",
    }

    def reply(messages):
        system = messages[0]["content"]
        if "forecast a number over time" in system:
            return json.dumps(choice)
        return "Sales keep rising."

    result = InsightForgeAgent(FakeLLMClient(reply), privacy_mode="full").run(
        "Forecast daily sales amount for the next week", catalog
    )
    stat = next(item for item in result.artifacts if isinstance(item, StatArtifact))
    assert stat.method == "forecast" and stat.horizon == 7
    chart = next(item for item in result.artifacts if isinstance(item, PlotArtifact))
    assert chart.name == "forecast_chart" and chart.kind == "forecast"
    names = [trace["name"] for trace in chart.figure["data"]]
    assert names == ["Actual", "Approximate 95% range", "Forecast"]
    assert [item.name for item in result.artifacts if isinstance(item, PlotArtifact)] == ["forecast_chart"]
    catalog.close()


def test_series_choices_clamp_the_horizon(schema):
    choice = {"series": True, "method": "forecast", "x": "hire_date", "y": "salary", "grain": "month",
              "horizon": 500, "sql": "SELECT hire_date, salary FROM employees"}
    plan = Planner(FakeLLMClient([json.dumps(choice)])).plan("Forecast salary for the next months", schema)
    assert plan.steps[1].horizon == 36 and plan.steps[1].grain == "month"
