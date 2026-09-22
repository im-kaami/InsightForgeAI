import pandas as pd
import pytest

from insightforge.core.planner import PlotStep
from insightforge.core.plotter import PlotError, make_figure


def test_bar_figure_contains_plotly_data_and_layout():
    figure = make_figure(
        PlotStep(name="bar", kind="bar", data_source="source", x="group", y="value"),
        pd.DataFrame({"group": ["a", "b"], "value": [1, 2]}),
    )
    assert figure["data"]
    assert "layout" in figure


def test_missing_column_lists_available_columns():
    with pytest.raises(PlotError, match="available columns: group, value"):
        make_figure(
            PlotStep(name="bar", kind="bar", data_source="source", x="missing", y="value"),
            pd.DataFrame({"group": ["a"], "value": [1]}),
        )


@pytest.mark.parametrize(
    "step",
    [
        PlotStep(name="pie", kind="pie", data_source="source", x="group", y="value"),
        PlotStep(name="hist", kind="histogram", data_source="source", x="value"),
    ],
)
def test_pie_and_histogram(step):
    figure = make_figure(step, pd.DataFrame({"group": ["a", "b"], "value": [1, 2]}))
    assert figure["data"]
