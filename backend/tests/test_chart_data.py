import pandas as pd
import pytest

from insightforge.core.catalog import DataCatalog
from insightforge.core.chart_data import (
    MAX_BARS,
    MAX_POINTS,
    MAX_SLICES,
    OTHER_LABEL,
    prepare_chart_data,
    suggest_chart,
)
from insightforge.core.planner import PlotStep
from insightforge.core.plotter import PlotError, make_figure

ROWS = 25_000


@pytest.fixture
def events():
    catalog = DataCatalog()
    catalog.register_df(
        "events",
        pd.DataFrame(
            {
                "id": range(ROWS),
                "category": [f"c{index % 80}" for index in range(ROWS)],
                "region": ["north" if index % 2 else "south" for index in range(ROWS)],
                "value": [float(index % 97) for index in range(ROWS)],
                "day": pd.date_range("2024-01-01", periods=ROWS, freq="h"),
            }
        ),
    )
    yield catalog
    catalog.close()


def _prefix(catalog: DataCatalog, rows: int = 100) -> pd.DataFrame:
    return catalog.query(f"SELECT * FROM events ORDER BY id LIMIT {rows}")


def _full(catalog: DataCatalog):
    return lambda body: catalog.query(f"WITH src AS (SELECT * FROM events ORDER BY id) {body}")


def _step(kind: str, x: str, y: str | None = None, color: str | None = None) -> PlotStep:
    return PlotStep(name="chart", kind=kind, data_source="source", x=x, y=y, color=color)


def test_complete_bar_keeps_every_row_within_the_category_limit():
    frame = pd.DataFrame({"group": [f"g{index % 20}" for index in range(800)], "value": range(800)})
    chart = prepare_chart_data(_step("bar", "group", "value"), frame)
    assert len(chart.frame) == 800
    assert chart.note is None


def test_complete_bar_keeps_the_largest_categories():
    frame = pd.DataFrame({"group": [f"g{index}" for index in range(80)], "value": range(80)})
    chart = prepare_chart_data(_step("bar", "group", "value"), frame)
    assert chart.frame["group"].nunique() == MAX_BARS
    assert set(chart.frame["group"]) == {f"g{index}" for index in range(30, 80)}
    assert chart.note == f"Showing the top {MAX_BARS} of 80 group values by total value."


def test_complete_pie_combines_small_slices_as_other():
    frame = pd.DataFrame({"group": [f"g{index}" for index in range(15)], "value": range(1, 16)})
    chart = prepare_chart_data(_step("pie", "group", "value"), frame)
    assert len(chart.frame) == MAX_SLICES
    assert chart.frame["group"].iloc[-1] == OTHER_LABEL
    assert chart.frame["value"].sum() == frame["value"].sum()
    assert "combined as Other" in chart.note


def test_complete_line_and_histogram_use_all_rows():
    frame = pd.DataFrame({"x": range(9000), "y": range(9000)})
    assert len(prepare_chart_data(_step("line", "x", "y"), frame).frame) == 9000
    assert len(prepare_chart_data(_step("histogram", "x"), frame).frame) == 9000


def test_truncated_bar_sums_every_row(events):
    chart = prepare_chart_data(_step("bar", "category", "value"), _prefix(events), _full(events), ROWS)
    expected = events.query("SELECT category, SUM(value) AS value FROM events GROUP BY 1")
    expected = expected.set_index("category")["value"]
    assert chart.frame["category"].nunique() == MAX_BARS
    for category, value in zip(chart.frame["category"], chart.frame["value"], strict=True):
        assert value == pytest.approx(expected[category])
    assert chart.note == (
        f"Summed value over all {ROWS:,} rows for each category. Showing the top {MAX_BARS} of 80 "
        "category values."
    )


def test_truncated_line_is_thinned_evenly_in_x_order(events):
    chart = prepare_chart_data(_step("line", "id", "value"), _prefix(events), _full(events), ROWS)
    assert len(chart.frame) <= MAX_POINTS
    assert chart.frame["id"].tolist() == list(range(0, ROWS, 3))
    assert chart.note == f"Showing 1 of every 3 points ({len(chart.frame):,} of {ROWS:,}), in id order."


def test_truncated_scatter_uses_a_repeatable_sample(events):
    first = prepare_chart_data(_step("scatter", "id", "value"), _prefix(events), _full(events), ROWS)
    second = prepare_chart_data(_step("scatter", "id", "value"), _prefix(events), _full(events), ROWS)
    assert len(first.frame) == MAX_POINTS
    assert first.frame["id"].tolist() == second.frame["id"].tolist()
    assert first.note == f"Showing a random sample of {MAX_POINTS:,} of {ROWS:,} points."


def test_truncated_numeric_histogram_bins_every_row(events):
    chart = prepare_chart_data(_step("histogram", "value"), _prefix(events), _full(events), ROWS)
    assert chart.prebinned
    assert chart.frame["count"].sum() == ROWS
    assert chart.bin_width == pytest.approx(96 / 40)
    assert make_figure(chart.step, chart.frame, prebinned=True, bin_width=chart.bin_width)["data"]


def test_truncated_date_histogram_keeps_dates(events):
    chart = prepare_chart_data(_step("histogram", "day"), _prefix(events), _full(events), ROWS)
    assert chart.frame["count"].sum() == ROWS
    assert pd.api.types.is_datetime64_any_dtype(chart.frame["day"])


def test_truncated_categorical_histogram_counts_the_largest_categories(events):
    chart = prepare_chart_data(
        _step("histogram", "category", color="region"), _prefix(events), _full(events), ROWS
    )
    assert chart.frame["category"].nunique() <= MAX_BARS
    assert set(chart.frame["region"]) == {"north", "south"}
    assert "top 50 of 80" in chart.note


def test_truncated_pie_combines_other_from_every_row(events):
    chart = prepare_chart_data(_step("pie", "category", "value"), _prefix(events), _full(events), ROWS)
    total = events.query("SELECT SUM(value) AS total FROM events")["total"].iloc[0]
    assert len(chart.frame) == MAX_SLICES
    assert chart.frame["value"].sum() == pytest.approx(total)
    assert chart.note.startswith(f"Calculated from all {ROWS:,} rows.")


def test_truncated_heatmap_sums_every_row_per_cell(events):
    chart = prepare_chart_data(
        _step("heatmap", "category", "region", "value"), _prefix(events), _full(events), ROWS
    )
    total = events.query("SELECT SUM(value) AS total FROM events")["total"].iloc[0]
    assert chart.frame["value"].sum() == pytest.approx(total)
    assert chart.note == f"Summed all {ROWS:,} rows into 80 cells."
    assert make_figure(chart.step, chart.frame)["data"]


def test_truncated_box_plot_uses_a_repeatable_sample(events):
    chart = prepare_chart_data(_step("box", "region", "value"), _prefix(events), _full(events), ROWS)
    assert len(chart.frame) == MAX_POINTS
    assert chart.note == f"Showing a random sample of {MAX_POINTS:,} of {ROWS:,} points."


@pytest.mark.parametrize(
    "step",
    [
        PlotStep(name="box", kind="box", data_source="source", x="group", y="value"),
        PlotStep(name="single_box", kind="box", data_source="source", x="value"),
        PlotStep(name="heatmap", kind="heatmap", data_source="source", x="group", y="band"),
        PlotStep(name="area", kind="area", data_source="source", x="step", y="value", color="group"),
    ],
)
def test_new_chart_kinds_render(step):
    frame = pd.DataFrame(
        {"group": ["a", "b"] * 5, "band": ["x"] * 5 + ["y"] * 5, "step": range(10), "value": range(10)}
    )
    figure = make_figure(step, prepare_chart_data(step, frame).frame)
    assert figure["data"]


def test_truncated_chart_falls_back_to_retrieved_rows_when_summary_fails():
    frame = pd.DataFrame({"x": range(10), "y": range(10)})

    def broken(_sql):
        raise RuntimeError("database unavailable")

    chart = prepare_chart_data(_step("line", "x", "y"), frame, broken, 50)
    assert len(chart.frame) == 10
    assert chart.note == (
        "Only the first 10 rows are plotted because the full result could not be summarized (RuntimeError)."
    )


@pytest.mark.parametrize(
    ("frame", "expected"),
    [
        (pd.DataFrame({"region": ["a", "b"], "sales": [1, 2]}), ("bar", "region", "sales")),
        (
            pd.DataFrame({"day": pd.date_range("2024-01-01", periods=3), "sales": [1, 2, 3]}),
            ("line", "day", "sales"),
        ),
        (pd.DataFrame({"price": range(12), "units": range(12)}), ("scatter", "price", "units")),
        (pd.DataFrame({"team": ["a", "a", "b"], "salary": [1, 2, 3]}), ("box", "team", "salary")),
        (
            pd.DataFrame({"team": ["a", "a", "b"], "role": ["x", "y", "x"], "people": [1, 2, 3]}),
            ("heatmap", "team", "role"),
        ),
        (pd.DataFrame({"order_id": range(12), "amount": range(12)}), ("histogram", "amount", None)),
    ],
)
def test_suggest_chart_picks_a_chart_from_the_result_shape(frame, expected):
    step = suggest_chart("result", frame)
    assert (step.kind, step.x, step.y) == expected
    assert step.data_source == "result"


@pytest.mark.parametrize(
    "frame",
    [
        pd.DataFrame({"total": [5]}),
        pd.DataFrame({"region": ["a", "b"], "label": ["x", "y"]}),
        pd.DataFrame({"order_id": [1, 2], "region": ["a", "b"]}),
    ],
)
def test_suggest_chart_skips_results_without_a_useful_chart(frame):
    assert suggest_chart("result", frame) is None


def test_missing_chart_columns_raise_plot_error():
    with pytest.raises(PlotError, match="Missing columns: missing"):
        prepare_chart_data(_step("bar", "missing", "value"), pd.DataFrame({"value": [1]}))
