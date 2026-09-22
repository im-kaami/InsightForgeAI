import json
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.express as px
import plotly.io as pio

from insightforge.core.planner import PlotStep


class PlotError(ValueError):
    pass


def make_figure(step: PlotStep, df: pd.DataFrame) -> dict[str, Any]:
    required = [step.x]
    if step.kind in {"line", "bar", "scatter"} and not step.y:
        raise PlotError(f"{step.kind} plots require y; available columns: {', '.join(map(str, df.columns))}")
    if step.y:
        required.append(step.y)
    if step.color:
        required.append(step.color)
    missing = [column for column in required if column not in df.columns]
    if missing:
        available = ", ".join(map(str, df.columns))
        raise PlotError(f"Missing columns: {', '.join(missing)}; available columns: {available}")

    data = df.head(500) if step.kind in {"bar", "pie", "line"} else df
    if step.kind == "pie":
        figure = px.pie(data, names=step.x, values=step.y, color=step.color, title=step.title)
    elif step.kind == "histogram":
        figure = px.histogram(data, x=step.x, y=step.y, color=step.color, title=step.title)
    else:
        plot = getattr(px, step.kind)
        figure = plot(data, x=step.x, y=step.y, color=step.color, title=step.title)
    return json.loads(figure.to_json())


def figure_to_png(figure: dict[str, Any], path: Path) -> Path | None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        pio.write_image(figure, path)
        return path
    except Exception:
        return None
