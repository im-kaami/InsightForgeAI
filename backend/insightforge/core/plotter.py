import json
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.express as px
import plotly.io as pio

from insightforge.core.planner import PlotStep


class PlotError(ValueError):
    pass


def validate_plot_columns(step: PlotStep, df: pd.DataFrame) -> None:
    required = [step.x]
    if step.kind in {"line", "bar", "scatter", "area", "heatmap"} and not step.y:
        raise PlotError(f"{step.kind} plots require y; available columns: {', '.join(map(str, df.columns))}")
    if step.y:
        required.append(step.y)
    if step.color:
        required.append(step.color)
    missing = [column for column in required if column not in df.columns]
    if missing:
        available = ", ".join(map(str, df.columns))
        raise PlotError(f"Missing columns: {', '.join(missing)}; available columns: {available}")


def make_figure(
    step: PlotStep, df: pd.DataFrame, *, prebinned: bool = False, bin_width: float | None = None
) -> dict[str, Any]:
    validate_plot_columns(step, df)
    data = df
    if step.kind == "histogram" and prebinned:
        value = step.y or next(column for column in data.columns if column not in {step.x, step.color})
        figure = px.bar(data, x=step.x, y=value, color=step.color, title=step.title)
        if bin_width is not None:
            figure.update_traces(width=bin_width)
        figure.update_layout(bargap=0)
    elif step.kind == "box":
        figure = (
            px.box(data, x=step.x, y=step.y, color=step.color, title=step.title)
            if step.y
            else px.box(data, y=step.x, color=step.color, title=step.title)
        )
    elif step.kind == "heatmap":
        figure = px.density_heatmap(
            data,
            x=step.x,
            y=step.y,
            z=step.color,
            histfunc="sum" if step.color else "count",
            title=step.title,
        )
    elif step.kind == "pie":
        figure = px.pie(data, names=step.x, values=step.y, color=step.color, title=step.title)
    elif step.kind == "histogram":
        figure = px.histogram(data, x=step.x, y=step.y, color=step.color, title=step.title)
    else:
        plot = getattr(px, step.kind)
        figure = plot(data, x=step.x, y=step.y, color=step.color, title=step.title)
    return json.loads(figure.to_json())


def series_figure(
    title: str,
    history: list[dict[str, Any]],
    forecast: list[dict[str, Any]] | None = None,
    flagged: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    import plotly.graph_objects as go

    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=[row["period"] for row in history],
            y=[row["value"] for row in history],
            mode="lines",
            name="Actual",
        )
    )
    if history and "expected" in history[0]:
        figure.add_trace(
            go.Scatter(
                x=[row["period"] for row in history],
                y=[row["expected"] for row in history],
                mode="lines",
                name="Expected",
                line={"dash": "dot"},
            )
        )
    if forecast:
        periods = [row["period"] for row in forecast]
        figure.add_trace(
            go.Scatter(
                x=periods + periods[::-1],
                y=[row["high"] for row in forecast] + [row["low"] for row in forecast][::-1],
                fill="toself",
                line={"width": 0},
                opacity=0.25,
                name="Approximate 95% range",
                hoverinfo="skip",
            )
        )
        figure.add_trace(
            go.Scatter(
                x=periods, y=[row["forecast"] for row in forecast], mode="lines+markers", name="Forecast"
            )
        )
    if flagged:
        figure.add_trace(
            go.Scatter(
                x=[row["period"] for row in flagged],
                y=[row["value"] for row in flagged],
                mode="markers",
                marker={"size": 11, "symbol": "x"},
                name="Unusual",
            )
        )
    figure.update_layout(title=title)
    return json.loads(figure.to_json())


def figure_to_png(figure: dict[str, Any], path: Path) -> Path | None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        pio.write_image(figure, path)
        return path
    except Exception:
        return None
