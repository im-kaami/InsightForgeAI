import math
from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd

from insightforge.core.planner import PlotStep
from insightforge.core.plotter import validate_plot_columns
from insightforge.core.schema import is_identifier

MAX_POINTS = 10_000
MAX_BARS = 50
MAX_SLICES = 10
HISTOGRAM_BINS = 40
OTHER_LABEL = "Other"

QueryRunner = Callable[[str], pd.DataFrame]


@dataclass
class ChartData:
    step: PlotStep
    frame: pd.DataFrame
    note: str | None = None
    prebinned: bool = False
    bin_width: float | None = None


def _q(name: str) -> str:
    return f'"{name.replace(chr(34), chr(34) * 2)}"'


def _rows(count: int | None) -> str:
    return f"all {count:,} rows" if count is not None else "all rows"


def _value_column(step: PlotStep) -> str:
    if step.y:
        return step.y
    return "count" if "count" not in {step.x, step.color} else "__count"


def _numeric_by(frame: pd.DataFrame, value: str, key: str) -> pd.Series:
    return pd.to_numeric(frame[value], errors="coerce").groupby(frame[key], sort=False, dropna=False).sum()


AUTO_CHART_NOTE = "Chart added automatically because the plan had none."


def suggest_chart(source: str, frame: pd.DataFrame) -> PlotStep | None:
    if len(frame) < 2:
        return None
    temporal, categorical, numeric = [], [], []
    for column in frame.columns:
        name, series = str(column), frame[column]
        if pd.api.types.is_bool_dtype(series):
            categorical.append(name)
        elif pd.api.types.is_datetime64_any_dtype(series) or pd.api.types.infer_dtype(
            series, skipna=True
        ) in {"date", "datetime"}:
            temporal.append(name)
        elif pd.api.types.is_numeric_dtype(series):
            if not is_identifier(name):
                numeric.append(name)
        elif pd.api.types.is_string_dtype(series) or pd.api.types.is_object_dtype(series):
            categorical.append(name)
    if not numeric:
        return None
    y = numeric[0]
    name = f"{source}_chart"
    if temporal:
        kind, x, title = "line", temporal[0], f"{y} over {temporal[0]}"
    elif categorical:
        x = categorical[0]
        if len(categorical) >= 2 and not frame.duplicated([x, categorical[1]]).any():
            second = categorical[1]
            return PlotStep(
                name=name,
                kind="heatmap",
                data_source=source,
                x=x,
                y=second,
                color=y,
                title=f"{y} by {x} and {second}",
            )
        if frame[x].is_unique:
            kind, title = "bar", f"{y} by {x}"
        else:
            kind, title = "box", f"Spread of {y} by {x}"
    elif len(numeric) >= 2 and len(frame) >= 10:
        kind, x, y, title = "scatter", numeric[0], numeric[1], f"{numeric[1]} against {numeric[0]}"
    elif len(frame) >= 10:
        return PlotStep(
            name=name, kind="histogram", data_source=source, x=y, title=f"Distribution of {y}"
        )
    else:
        return None
    return PlotStep(name=name, kind=kind, data_source=source, x=x, y=y, title=title)


def prepare_chart_data(
    step: PlotStep,
    frame: pd.DataFrame,
    full_query: QueryRunner | None = None,
    full_row_count: int | None = None,
) -> ChartData:
    validate_plot_columns(step, frame)
    if full_query is None:
        return _complete(step, frame)
    try:
        return _truncated(step, frame, full_query, full_row_count)
    except Exception as error:
        chart = _complete(step, frame)
        note = (
            f"Only the first {len(frame):,} rows are plotted because the full result could not be "
            f"summarized ({type(error).__name__})."
        )
        chart.note = f"{chart.note} {note}" if chart.note else note
        return chart


def _complete(step: PlotStep, frame: pd.DataFrame) -> ChartData:
    if step.kind not in {"bar", "pie"}:
        return ChartData(step, frame)
    categories = frame[step.x].nunique(dropna=False)
    if step.kind == "bar" and step.y and categories > MAX_BARS:
        totals = _numeric_by(frame, step.y, step.x)
        keep = totals.abs().sort_values(ascending=False).index[:MAX_BARS]
        note = f"Showing the top {MAX_BARS} of {categories:,} {step.x} values by total {step.y}."
        return ChartData(step, frame[frame[step.x].isin(keep)], note)
    if step.kind == "pie" and categories > MAX_SLICES:
        values = (
            _numeric_by(frame, step.y, step.x)
            if step.y
            else frame.groupby(step.x, sort=False, dropna=False).size()
        )
        return _pie_with_other(step, values.sort_values(ascending=False), categories)
    return ChartData(step, frame)


def _pie_with_other(step: PlotStep, ranked: pd.Series, categories: int) -> ChartData:
    value_column = _value_column(step)
    kept = ranked.iloc[: MAX_SLICES - 1]
    result = pd.DataFrame(
        {
            step.x: [str(value) for value in kept.index] + [OTHER_LABEL],
            value_column: [*kept.to_numpy(), ranked.iloc[MAX_SLICES - 1 :].sum()],
        }
    )
    note = (
        f"Showing the {MAX_SLICES - 1} largest of {categories:,} {step.x} values; "
        f"the other {categories - (MAX_SLICES - 1):,} are combined as {OTHER_LABEL}."
    )
    return ChartData(step.model_copy(update={"y": value_column, "color": None}), result, note)


def _truncated(
    step: PlotStep, frame: pd.DataFrame, query: QueryRunner, total_rows: int | None
) -> ChartData:
    if step.kind == "bar":
        return _truncated_bar(step, frame, query, total_rows)
    if step.kind == "pie":
        return _truncated_pie(step, query, total_rows)
    if step.kind == "histogram":
        return _truncated_histogram(step, frame, query, total_rows)
    if step.kind == "heatmap":
        return _truncated_heatmap(step, query, total_rows)
    count = total_rows
    if count is None:
        count = int(query("SELECT COUNT(*) AS n FROM src")["n"].iloc[0])
    names = dict.fromkeys(column for column in (step.x, step.y, step.color) if column)
    columns = ", ".join(_q(column) for column in names)
    if count <= MAX_POINTS:
        return ChartData(step, query(f"SELECT {columns} FROM src"), f"Plotted {_rows(count)}.")
    if step.kind in {"scatter", "box"}:
        sampled = query(
            f"SELECT {columns} FROM src USING SAMPLE reservoir({MAX_POINTS} ROWS) REPEATABLE (42)"
        )
        return ChartData(step, sampled, f"Showing a random sample of {len(sampled):,} of {count:,} points.")
    x = _q(step.x)
    every = math.ceil(count / MAX_POINTS)
    partition = f"PARTITION BY {_q(step.color)} " if step.color else ""
    thinned = query(
        f"SELECT {columns} FROM (SELECT {columns}, row_number() OVER ({partition}ORDER BY {x}) AS __n "
        f"FROM src) WHERE (__n - 1) % {every} = 0 ORDER BY {x}"
    )
    note = f"Showing 1 of every {every:,} points ({len(thinned):,} of {count:,}), in {step.x} order."
    return ChartData(step, thinned, note)


def _truncated_heatmap(step: PlotStep, query: QueryRunner, total_rows: int | None) -> ChartData:
    x, y = _q(step.x), _q(str(step.y))
    value = f"SUM({_q(step.color)})" if step.color else "COUNT(*)"
    value_column = step.color or ("count" if "count" not in {step.x, step.y} else "__count")
    cells = query(
        f"SELECT {x} AS __x, {y} AS __y, {value} AS __v FROM src GROUP BY ALL "
        f"ORDER BY __v DESC NULLS LAST LIMIT {MAX_POINTS}"
    ).rename(columns={"__x": step.x, "__y": str(step.y), "__v": value_column})
    total_cells = int(
        query(f"SELECT COUNT(*) AS n FROM (SELECT {x}, {y} FROM src GROUP BY ALL)")["n"].iloc[0]
    )
    note = f"Summed {_rows(total_rows)} into {total_cells:,} cells."
    if total_cells > MAX_POINTS:
        note += f" Showing the {MAX_POINTS:,} largest cells."
    return ChartData(step.model_copy(update={"color": value_column}), cells, note)


def _category_count(query: QueryRunner, column: str) -> int:
    return int(query(f"SELECT COUNT(*) AS n FROM (SELECT {_q(column)} FROM src GROUP BY 1)")["n"].iloc[0])


def _truncated_bar(
    step: PlotStep, frame: pd.DataFrame, query: QueryRunner, total_rows: int | None
) -> ChartData:
    x, y = _q(step.x), _q(str(step.y))
    color = f", {_q(step.color)} AS __c" if step.color else ""
    aggregated = query(
        f"SELECT a.* FROM (SELECT {x} AS __x{color}, SUM({y}) AS __y FROM src GROUP BY ALL) a "
        f"JOIN (SELECT {x} AS __x FROM src GROUP BY 1 ORDER BY abs(SUM({y})) DESC NULLS LAST "
        f"LIMIT {MAX_BARS}) k ON a.__x IS NOT DISTINCT FROM k.__x"
    )
    categories = _category_count(query, step.x)
    first_seen = {value: index for index, value in enumerate(frame[step.x].drop_duplicates())}
    totals = aggregated.groupby("__x", dropna=False)["__y"].sum().abs().sort_values(ascending=False)
    ranked = {value: rank for rank, value in enumerate(totals.index)}
    aggregated["__order"] = [
        first_seen.get(value, len(first_seen) + ranked.get(value, 0)) for value in aggregated["__x"]
    ]
    result = aggregated.sort_values("__order", kind="stable").drop(columns="__order")
    result = result.rename(columns={"__x": step.x, "__y": str(step.y), "__c": step.color or "__c"})
    note = f"Summed {step.y} over {_rows(total_rows)} for each {step.x}."
    if categories > MAX_BARS:
        note += f" Showing the top {MAX_BARS} of {categories:,} {step.x} values."
    return ChartData(step, result.reset_index(drop=True), note)


def _truncated_pie(step: PlotStep, query: QueryRunner, total_rows: int | None) -> ChartData:
    x = _q(step.x)
    value = f"SUM({_q(step.y)})" if step.y else "COUNT(*)"
    grouped = f"SELECT {x} AS __x, {value} AS __v FROM src GROUP BY 1"
    ranked = query(f"{grouped} ORDER BY __v DESC NULLS LAST LIMIT {MAX_SLICES - 1}")
    stats = query(f"SELECT COUNT(*) AS categories, SUM(__v) AS total FROM ({grouped})")
    categories = int(stats["categories"].iloc[0])
    values = pd.Series(ranked["__v"].to_numpy(), index=ranked["__x"].to_numpy())
    prefix = f"Calculated from {_rows(total_rows)}."
    if categories > MAX_SLICES:
        other = pd.Series([stats["total"].iloc[0] - values.sum()], index=["__other__"])
        chart = _pie_with_other(step, pd.concat([values, other]), categories)
        chart.note = f"{prefix} {chart.note}"
        return chart
    value_column = _value_column(step)
    result = pd.DataFrame({step.x: values.index, value_column: values.to_numpy()})
    return ChartData(step.model_copy(update={"y": value_column, "color": None}), result, prefix)


def _truncated_histogram(
    step: PlotStep, frame: pd.DataFrame, query: QueryRunner, total_rows: int | None
) -> ChartData:
    value_column = _value_column(step)
    value = f"SUM({_q(step.y)})" if step.y else "COUNT(*)"
    color = f", {_q(step.color)} AS __c" if step.color else ""
    x = _q(step.x)
    series = frame[step.x]
    inferred = pd.api.types.infer_dtype(series, skipna=True)
    temporal = pd.api.types.is_datetime64_any_dtype(series) or inferred in {"date", "datetime"}
    numeric = inferred == "decimal" or (
        pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series)
    )
    renames = {"__x": step.x, "__v": value_column, "__c": step.color or "__c"}
    if not (temporal or numeric):
        counted = query(
            f"SELECT {x} AS __x{color}, {value} AS __v FROM src GROUP BY ALL "
            f"ORDER BY __v DESC NULLS LAST LIMIT {MAX_BARS * 4}"
        ).rename(columns=renames)
        categories = _category_count(query, step.x)
        note = f"Counted {_rows(total_rows)}."
        if categories > MAX_BARS:
            top = counted.groupby(step.x, sort=False)[value_column].sum().nlargest(MAX_BARS).index
            counted = counted[counted[step.x].isin(top)]
            note += f" Showing the top {MAX_BARS} of {categories:,} {step.x} values."
        return ChartData(step, counted.reset_index(drop=True), note, prebinned=True)
    number = f"epoch_ms(CAST({x} AS TIMESTAMP))" if temporal else f"CAST({x} AS DOUBLE)"
    bounds = query(f"SELECT MIN({number}) AS lo, MAX({number}) AS hi FROM src")
    lo, hi = bounds["lo"].iloc[0], bounds["hi"].iloc[0]
    if pd.isna(lo) or pd.isna(hi):
        return ChartData(step, frame.head(0), "No values to plot.", prebinned=True)
    lo, hi = float(lo), float(hi)
    bins = HISTOGRAM_BINS if hi > lo else 1
    width = (hi - lo) / bins if hi > lo else 1.0
    binned = query(
        f"SELECT LEAST(CAST(floor(({number} - {lo!r}) / {width!r}) AS BIGINT), {bins - 1}) AS __bin{color}, "
        f"{value} AS __v FROM src WHERE {x} IS NOT NULL GROUP BY ALL ORDER BY __bin"
    )
    centers = lo + (binned["__bin"].astype(float) + 0.5) * width
    result = pd.DataFrame(
        {step.x: pd.to_datetime(centers, unit="ms") if temporal else centers, value_column: binned["__v"]}
    )
    if step.color:
        result[step.color] = binned["__c"]
    note = f"Binned {_rows(total_rows)} into {bins} ranges of {step.x}."
    return ChartData(step, result, note, prebinned=True, bin_width=width)
