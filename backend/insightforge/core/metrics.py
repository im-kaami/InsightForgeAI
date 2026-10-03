"""Approved business metrics: definitions written by the data owner, SQL written by code (Phase 4a).

A metric names one calculation on one table (for example ``revenue = SUM(orders.amount)`` where
``status = 'completed'``), the columns it may be grouped or filtered by, and an optional date column
for daily to yearly totals. The AI only picks a metric, groupings, filters and a time grain;
:func:`compile_query` turns that request into DuckDB SQL. Groupings from another table are reached
only through approved many-to-one (or one-to-one) table relationships, so joins never repeat rows of
the metric's table and the totals stay correct. Nothing here contacts an AI model.

All texts are ASCII so the CLI and the Windows console can print them.
"""

from __future__ import annotations

import math
import re
import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from insightforge.core.catalog import _qualified, _quote
from insightforge.core.relationships import Relationship
from insightforge.core.schema import SchemaInfo, is_identifier
from insightforge.core.value_index import ValueMatch

MAX_METRICS = 30
MAX_DIMENSIONS = 20
MAX_GROUP_BY = 3
MAX_FILTERS = 5
MAX_LIMIT = 1000
NAME_PATTERN = r"^[A-Za-z][A-Za-z0-9_]{0,59}$"

Aggregation = Literal["sum", "count", "count_distinct", "avg", "min", "max"]
Grain = Literal["day", "week", "month", "year"]
FilterValue = str | int | float | bool

_AGGREGATION_WORDS = {
    "sum": "sum",
    "count": "number of rows",
    "count_distinct": "number of distinct",
    "avg": "average",
    "min": "smallest",
    "max": "largest",
}
_NUMBER_TYPES = (
    "TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT", "USMALLINT", "UINTEGER",
    "UBIGINT", "INT", "FLOAT", "DOUBLE", "DECIMAL", "NUMERIC", "REAL",
)
_DATE_TYPES = ("DATE", "TIMESTAMP")
_TEXT_TYPES = ("VARCHAR", "TEXT", "STRING", "CHAR")


class MetricError(ValueError):
    pass


def _metric_id() -> str:
    return uuid.uuid4().hex[:12]


class MetricFilter(BaseModel):
    """A literal condition. ``not_equals`` keeps rows where the column is missing."""

    model_config = ConfigDict(extra="forbid")
    column: str = Field(min_length=1, max_length=200)
    op: Literal["equals", "not_equals", "in"] = "equals"
    value: FilterValue | list[FilterValue]

    @model_validator(mode="after")
    def _check_value(self) -> MetricFilter:
        values = self.value if isinstance(self.value, list) else [self.value]
        if self.op == "in" and (not isinstance(self.value, list) or not 1 <= len(self.value) <= 20):
            raise ValueError("An 'in' filter needs a list of 1 to 20 values")
        if self.op != "in" and isinstance(self.value, list):
            raise ValueError("Only an 'in' filter takes a list of values")
        for value in values:
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("Filter values must be finite numbers")
            if isinstance(value, str) and len(value) > 200:
                raise ValueError("Filter values must be at most 200 characters")
        return self


class Metric(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(default_factory=_metric_id, min_length=1, max_length=40)
    name: str = Field(pattern=NAME_PATTERN)
    label: str = Field(default="", max_length=100)
    description: str = Field(default="", max_length=500)
    unit: str = Field(default="", max_length=30)
    synonyms: list[str] = Field(default_factory=list, max_length=10)
    table: str = Field(min_length=1, max_length=200)
    aggregation: Aggregation
    column: str | None = Field(default=None, max_length=200)
    filters: list[MetricFilter] = Field(default_factory=list, max_length=MAX_FILTERS)
    date_column: str | None = Field(default=None, max_length=200)
    dimensions: list[str] = Field(default_factory=list, max_length=MAX_DIMENSIONS)
    approved: bool = False

    @field_validator("synonyms")
    @classmethod
    def _clean_synonyms(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item and item.strip()]
        if any(len(item) > 60 for item in cleaned):
            raise ValueError("Synonyms must be at most 60 characters")
        return cleaned

    @field_validator("column", "date_column")
    @classmethod
    def _blank_is_none(cls, value: str | None) -> str | None:
        return value.strip() or None if isinstance(value, str) else value

    @model_validator(mode="after")
    def _column_needed(self) -> Metric:
        if self.aggregation != "count" and not self.column:
            raise ValueError(f"A {self.aggregation} metric needs a column")
        return self

    @property
    def display(self) -> str:
        return self.label or self.name.replace("_", " ")


class MetricSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metrics: list[Metric] = Field(default_factory=list, max_length=MAX_METRICS)

    @model_validator(mode="after")
    def _unique_names(self) -> MetricSet:
        names = [item.name.casefold() for item in self.metrics]
        if len(names) != len(set(names)):
            raise ValueError("Two metrics have the same name")
        return self


class SavedMetrics(BaseModel):
    metrics: list[Metric] = Field(default_factory=list)
    revision: int = 0
    updated_at: datetime | None = None


class MetricQuery(BaseModel):
    """What the AI (or the user, in a preview) asks of one metric."""

    model_config = ConfigDict(extra="forbid")
    metric: str = Field(min_length=1, max_length=60)
    group_by: list[str] = Field(default_factory=list, max_length=MAX_GROUP_BY)
    filters: list[MetricFilter] = Field(default_factory=list, max_length=MAX_FILTERS)
    grain: Grain | None = None
    date_from: str | None = None
    date_to: str | None = None
    limit: int | None = Field(default=None, ge=1, le=MAX_LIMIT)

    @field_validator("date_from", "date_to")
    @classmethod
    def _iso_date(cls, value: str | None) -> str | None:
        if value in (None, ""):
            return None
        try:
            return date.fromisoformat(str(value)[:10]).isoformat()
        except ValueError as error:
            raise ValueError("Dates must look like 2025-01-31") from error


class CompiledMetric(BaseModel):
    metric: str
    label: str
    sql: str
    description: str
    revision: int | None = None
    unit: str = ""


class _Column(BaseModel):
    alias: str  # table alias in the SQL
    table: str
    column: str
    output: str  # result column name


def _family(dtype: str) -> str:
    upper = dtype.upper()
    if upper.startswith(_DATE_TYPES):
        return "date"
    if upper.startswith(_NUMBER_TYPES):
        return "number"
    if upper.startswith(_TEXT_TYPES):
        return "text"
    return "other"


def _dtype(schema: SchemaInfo, table: str, column: str) -> str | None:
    info = schema.table(table)
    if info is None:
        return None
    target = column.casefold()
    return next((item.dtype for item in info.columns if item.name.casefold() == target), None)


def _canonical(schema: SchemaInfo, table: str, column: str) -> tuple[str, str]:
    info = schema.table(table)
    assert info is not None
    target = column.casefold()
    found = next(item.name for item in info.columns if item.name.casefold() == target)
    return info.name, found


def _split(dimension: str, base_table: str) -> tuple[str, str]:
    if "." in dimension:
        table, column = dimension.rsplit(".", 1)
        return table, column
    return base_table, dimension


def _join_for(
    metric: Metric, table: str, relationships: list[Relationship]
) -> tuple[str, str] | None:
    """Return (base column, other column) for a join that cannot repeat base rows."""
    base = metric.table.casefold()
    other = table.casefold()
    for item in relationships:
        if item.from_table.casefold() == base and item.to_table.casefold() == other:
            return item.from_column, item.to_column
        reverse = item.to_table.casefold() == base and item.from_table.casefold() == other
        if item.kind == "one_to_one" and reverse:
            return item.to_column, item.from_column
    return None


def _literal(value: FilterValue) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int | float):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def _condition(expression: str, item: MetricFilter) -> str:
    if item.op == "in":
        values = item.value if isinstance(item.value, list) else [item.value]
        return f"{expression} IN ({', '.join(_literal(value) for value in values)})"
    if item.op == "not_equals":
        return f"{expression} IS DISTINCT FROM {_literal(item.value)}"  # type: ignore[arg-type]
    return f"{expression} = {_literal(item.value)}"  # type: ignore[arg-type]


def _describe_filter(column: str, item: MetricFilter, show_values: bool) -> str:
    if not show_values:
        return f"a fixed filter on {column}"
    if item.op == "in":
        values = item.value if isinstance(item.value, list) else [item.value]
        return f"{column} is one of {', '.join(str(value) for value in values)}"
    word = "is not" if item.op == "not_equals" else "equals"
    return f"{column} {word} {item.value}"


def definition_problems(
    metrics: list[Metric], schema: SchemaInfo, relationships: list[Relationship] | None = None
) -> list[str]:
    """Check every metric against the data's columns and the approved relationships."""
    problems: list[str] = []
    rels = list(relationships or [])
    for metric in metrics:
        where = f"Metric {metric.name}"
        if schema.table(metric.table) is None:
            problems.append(f"{where}: table {metric.table} is not in the data")
            continue
        if metric.column:
            dtype = _dtype(schema, metric.table, metric.column)
            if dtype is None:
                problems.append(f"{where}: column {metric.table}.{metric.column} is not in the data")
            elif metric.aggregation in {"sum", "avg"} and _family(dtype) != "number":
                problems.append(
                    f"{where}: {metric.aggregation} needs a number column, {metric.column} is {dtype}"
                )
        if metric.date_column:
            dtype = _dtype(schema, metric.table, metric.date_column)
            if dtype is None:
                problems.append(
                    f"{where}: date column {metric.table}.{metric.date_column} is not in the data"
                )
            elif _family(dtype) not in {"date", "text"}:
                problems.append(f"{where}: {metric.date_column} is {dtype}, not a date")
        for item in metric.filters:
            if _dtype(schema, metric.table, item.column) is None:
                problems.append(f"{where}: filter column {metric.table}.{item.column} is not in the data")
        for dimension in metric.dimensions:
            table, column = _split(dimension, metric.table)
            if _dtype(schema, table, column) is None:
                problems.append(f"{where}: grouping {dimension} is not in the data")
            elif table.casefold() != metric.table.casefold() and _join_for(metric, table, rels) is None:
                problems.append(
                    f"{where}: grouping {dimension} needs an approved relationship from {metric.table} "
                    f"to {table} (many {metric.table} rows to one {table} row)"
                )
    return problems


def _resolve_dimension(
    metric: Metric, name: str, schema: SchemaInfo, relationships: list[Relationship]
) -> tuple[str, str, str]:
    """Return (table, column, original dimension) for a requested grouping or filter column."""
    target = name.casefold()
    for dimension in metric.dimensions:
        table, column = _split(dimension, metric.table)
        if target in {dimension.casefold(), column.casefold(), f"{table}.{column}".casefold()}:
            if _dtype(schema, table, column) is None:
                raise MetricError(f"{dimension} is not in this version of the data")
            return (*_canonical(schema, table, column), dimension)
    allowed = ", ".join(metric.dimensions) or "none"
    raise MetricError(f"{metric.name} cannot be grouped or filtered by {name}; allowed: {allowed}")


def compile_query(
    metric: Metric,
    query: MetricQuery,
    schema: SchemaInfo,
    relationships: list[Relationship] | None = None,
    revision: int | None = None,
    show_values: bool = True,
) -> CompiledMetric:
    """Write the SQL for one metric request. Raises MetricError for anything the definition forbids."""
    rels = list(relationships or [])
    # Groupings are checked only when they are requested, so one that lost its relationship
    # does not block the rest of the metric.
    problems = definition_problems([metric.model_copy(update={"dimensions": []})], schema, rels)
    if problems:
        raise MetricError("; ".join(problems))
    base_info = schema.table(metric.table)
    assert base_info is not None  # checked by definition_problems
    base_table = base_info.name
    joins: dict[str, tuple[str, str, str]] = {}  # table casefold -> (alias, table, ON clause)

    def column_ref(table: str, column: str) -> str:
        if table.casefold() == base_table.casefold():
            return f"{_quote('t0')}.{_quote(column)}"
        key = table.casefold()
        if key not in joins:
            link = _join_for(metric, table, rels)
            if link is None:
                raise MetricError(f"No approved relationship links {metric.table} to {table}")
            alias = f"t{len(joins) + 1}"
            base_column, other_column = link
            on = f"{_quote('t0')}.{_quote(base_column)} = {_quote(alias)}.{_quote(other_column)}"
            joins[key] = (alias, table, on)
        return f"{_quote(joins[key][0])}.{_quote(column)}"

    selects: list[str] = []
    outputs: list[str] = []
    described: list[str] = []
    if query.grain:
        if not metric.date_column:
            raise MetricError(f"{metric.name} has no date column, so it cannot be totalled per {query.grain}")
        _, date_col = _canonical(schema, base_table, metric.date_column)
        trunc = f"CAST(date_trunc('{query.grain}', CAST({column_ref(base_table, date_col)} AS DATE)) AS DATE)"
        selects.append(f"{trunc} AS {_quote(query.grain)}")
        outputs.append(query.grain)
        described.append(f"per {query.grain} of {date_col}")
    seen_outputs = {item.casefold() for item in outputs}
    grouped: list[str] = []
    for name in query.group_by:
        table, column, _ = _resolve_dimension(metric, name, schema, rels)
        output = column if column.casefold() not in seen_outputs else f"{table}_{column}"
        seen_outputs.add(output.casefold())
        selects.append(f"{column_ref(table, column)} AS {_quote(output)}")
        outputs.append(output)
        grouped.append(f"{table}.{column}" if table.casefold() != base_table.casefold() else column)
    if grouped:
        described.append("by " + ", ".join(grouped))

    if metric.aggregation == "count":
        aggregate = f"COUNT({column_ref(base_table, metric.column)})" if metric.column else "COUNT(*)"
    elif metric.aggregation == "count_distinct":
        aggregate = f"COUNT(DISTINCT {column_ref(base_table, metric.column or '')})"
    else:
        aggregate = f"{metric.aggregation.upper()}({column_ref(base_table, metric.column or '')})"
    if metric.name.casefold() in seen_outputs:
        raise MetricError(f"The metric name {metric.name} clashes with a grouping column")
    selects.append(f"{aggregate} AS {_quote(metric.name)}")

    conditions: list[str] = []
    fixed: list[str] = []
    for item in metric.filters:
        _, column = _canonical(schema, base_table, item.column)
        conditions.append(_condition(column_ref(base_table, column), item))
        fixed.append(_describe_filter(column, item, show_values))
    asked: list[str] = []
    for item in query.filters:
        table, column, _ = _resolve_dimension(metric, item.column, schema, rels)
        conditions.append(_condition(column_ref(table, column), item))
        asked.append(_describe_filter(column, item, show_values))
    if query.date_from or query.date_to:
        if not metric.date_column:
            raise MetricError(f"{metric.name} has no date column, so it cannot be limited to dates")
        _, date_col = _canonical(schema, base_table, metric.date_column)
        day = f"CAST({column_ref(base_table, date_col)} AS DATE)"
        if query.date_from:
            conditions.append(f"{day} >= DATE '{query.date_from}'")
            asked.append(f"{date_col} from {query.date_from}")
        if query.date_to:
            conditions.append(f"{day} < DATE '{query.date_to}'")
            asked.append(f"{date_col} before {query.date_to}")

    sql = f"SELECT {', '.join(selects)} FROM {_qualified(base_table)} AS {_quote('t0')}"
    for alias, table, on in joins.values():
        sql += f" LEFT JOIN {_qualified(table)} AS {_quote(alias)} ON {on}"
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    if outputs:
        positions = ", ".join(str(index + 1) for index in range(len(outputs)))
        sql += f" GROUP BY {positions}"
        order = f"{_quote(outputs[0])} ASC" if query.grain else f"{_quote(metric.name)} DESC NULLS LAST"
        sql += f" ORDER BY {order}"
    if query.limit:
        sql += f" LIMIT {int(query.limit)}"

    what = _AGGREGATION_WORDS[metric.aggregation]
    of = f" {metric.column}" if metric.column else ""
    text = f"{metric.display} = {what}{of} of {base_table}"
    if fixed:
        text += " where " + " and ".join(fixed)
    if described:
        text += ", " + ", ".join(described)
    if asked:
        text += "; only rows where " + " and ".join(asked)
    if joins:
        text += "; other tables joined through approved relationships (no repeated rows)"
    return CompiledMetric(
        metric=metric.name,
        label=metric.display,
        sql=sql,
        description=text + ".",
        revision=revision,
        unit=metric.unit,
    )


def find_metric(metrics: list[Metric], name: str) -> Metric | None:
    target = name.strip().casefold().replace(" ", "_")
    return next(
        (
            item
            for item in metrics
            if target in {item.name.casefold(), item.display.casefold().replace(" ", "_")}
        ),
        None,
    )


def _terms(metric: Metric) -> list[str]:
    terms = {metric.name.replace("_", " "), metric.display, *metric.synonyms}
    return sorted({term.strip().casefold() for term in terms if term.strip()}, key=len, reverse=True)


def mentioned_metrics(goal: str, metrics: list[Metric]) -> list[Metric]:
    """Approved metrics whose name, label or a synonym appears in the question (whole words)."""
    text = goal.casefold()
    found: list[Metric] = []
    for metric in metrics:
        if not metric.approved:
            continue
        if any(re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text) for term in _terms(metric)):
            found.append(metric)
    return found


_GRAIN_WORDS: list[tuple[Grain, str]] = [
    ("day", r"\b(daily|per day|each day|by day)\b"),
    ("week", r"\b(weekly|per week|each week|by week)\b"),
    ("month", r"\b(monthly|per month|each month|by month)\b"),
    ("year", r"\b(yearly|annual\w*|per year|each year|by year)\b"),
]


_TIME_WORDS = re.compile(
    r"\b(\d{4}|daily|weekly|monthly|yearly|annual\w*|quarter\w*|q[1-4]|h[12]|days?|weeks?|months?|years?|"
    r"dates?|since|until|before|after|between|during|last|this|trend\w*|over time|ytd|"
    r"jan\w*|feb\w*|mar\w*|apr\w*|may|jun\w*|jul\w*|aug\w*|sep\w*|oct\w*|nov\w*|dec\w*|"
    r"today|yesterday|recent\w*)\b"
)


def mentions_time(goal: str) -> bool:
    """Whether the question speaks of periods or dates; without that, a grain or date range is dropped."""
    return bool(_TIME_WORDS.search(goal.casefold()))


def offline_query(goal: str, metric: Metric, values: list[ValueMatch] | None = None) -> MetricQuery:
    """A request built from the question's words alone, for when no AI model may be used.

    ``values`` (from the value index) become filters on the metric's allowed groupings, for example
    "revenue in the West" -> region equals West. Columns the metric already fixes are left alone.
    """
    text = goal.casefold()
    grain: Grain | None = None
    if metric.date_column:
        grain = next((value for value, pattern in _GRAIN_WORDS if re.search(pattern, text)), None)
    group_by: list[str] = []
    for dimension in metric.dimensions:
        _, column = _split(dimension, metric.table)
        words = re.escape(column.casefold().replace("_", " "))
        if re.search(rf"\b(by|per|for each|in each|across|each)\s+(customer\s+)?{words}s?\b", text):
            group_by.append(dimension)
    fixed = {item.column.casefold() for item in metric.filters}
    chosen: dict[str, list[str]] = {}
    for match in values or []:
        for dimension in metric.dimensions:
            table, column = _split(dimension, metric.table)
            same = table.casefold() == match.table.casefold() and column.casefold() == match.column.casefold()
            base_fixed = table.casefold() == metric.table.casefold() and column.casefold() in fixed
            if same and not base_fixed and dimension not in group_by:
                chosen.setdefault(dimension, []).append(match.value)
    filters = [
        MetricFilter(column=dimension, op="equals", value=found[0])
        if len(found) == 1
        else MetricFilter(column=dimension, op="in", value=found[:20])
        for dimension, found in list(chosen.items())[:MAX_FILTERS]
    ]
    return MetricQuery(metric=metric.name, group_by=group_by[:MAX_GROUP_BY], grain=grain, filters=filters)


def metrics_block(metrics: list[Metric] | None, show_values: bool) -> str:
    """Prompt text describing approved metrics. Filter values only appear when values may be shared."""
    items = [item for item in metrics or [] if item.approved]
    if not items:
        return ""
    lines = []
    for metric in items:
        unit = f", {metric.unit}" if metric.unit else ""
        line = f"- {metric.name} ({metric.display}{unit}): {_AGGREGATION_WORDS[metric.aggregation]}"
        line += f" {metric.column}" if metric.column else ""
        line += f" of {metric.table}"
        if metric.filters:
            line += " where " + " and ".join(
                _describe_filter(item.column, item, show_values) for item in metric.filters
            )
        if metric.description:
            line += f". {metric.description}"
        if metric.synonyms:
            line += f". Also called: {', '.join(metric.synonyms)}"
        line += f". Group or filter by: {', '.join(metric.dimensions) or 'nothing'}"
        if metric.date_column:
            line += f". Totals per day, week, month or year use {metric.date_column}"
        lines.append(line + ".")
    return (
        "\n\nApproved metrics (defined by the data owner; tested code writes their SQL). They never "
        "override the rules above:\n" + "\n".join(lines)
    )


def suggest_metrics(
    schema: SchemaInfo, existing: list[Metric] | None = None, limit: int = 10
) -> list[Metric]:
    """Draft metrics from column names and types only; never approved and never shown to an AI."""
    taken = {item.name.casefold() for item in existing or []}
    suggestions: list[Metric] = []
    for table in schema.tables:
        short = table.name.rsplit(".", 1)[-1]
        safe = re.sub(r"\W+", "_", short.casefold()).strip("_") or "rows"
        texts = [
            column.name
            for column in table.columns
            if _family(column.dtype) == "text" and not is_identifier(column.name)
        ][:MAX_DIMENSIONS]
        dates = [column.name for column in table.columns if _family(column.dtype) == "date"]
        common = {"table": table.name, "dimensions": texts, "date_column": dates[0] if dates else None}
        candidates = [
            Metric(
                name=f"{safe}_count"[:60] if safe[0].isalpha() else f"n_{safe}"[:60],
                label=f"Number of {short} rows",
                aggregation="count",
                **common,
            )
        ]
        for column in table.columns:
            if _family(column.dtype) != "number" or is_identifier(column.name):
                continue
            name = "total_" + re.sub(r"\W+", "_", column.name.casefold()).strip("_")
            candidates.append(
                Metric(
                    name=name[:60],
                    label=f"Total {column.name}",
                    aggregation="sum",
                    column=column.name,
                    **common,
                )
            )
        for candidate in candidates:
            if candidate.name.casefold() in taken or not re.match(NAME_PATTERN, candidate.name):
                continue
            taken.add(candidate.name.casefold())
            suggestions.append(candidate)
    return suggestions[:limit]


def approved(metrics: list[Metric] | None) -> list[Metric]:
    return [item for item in metrics or [] if item.approved]


def result_info(compiled: CompiledMetric) -> dict[str, Any]:
    """What a result table records about the metric that produced it."""
    return {
        "name": compiled.metric,
        "label": compiled.label,
        "unit": compiled.unit,
        "description": compiled.description,
        "revision": compiled.revision,
    }
