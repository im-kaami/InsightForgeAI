"""Small, visible corrections that code makes to the AI's SQL (each one is reported as an assumption)."""

import re

import pandas as pd
from sqlglot import exp, parse_one
from sqlglot.errors import ParseError

from insightforge.core.catalog import DataCatalog, _qualified, _quote
from insightforge.core.checks import _column_table, _literal, _tables

DESCENDING = ("highest", "most", "largest", "biggest", "top", "best", "maximum", "max")
ASCENDING = ("lowest", "least", "smallest", "fewest", "worst", "minimum", "min")


def _words(goal: str, options: tuple[str, ...]) -> list[str]:
    return [word for word in options if re.search(rf"\b{word}\b", goal, re.IGNORECASE)]


def add_superlative_order(query: str, goal: str) -> tuple[str, str] | None:
    """Sort an aggregated result when the question asks for the highest/lowest, and say what was done.

    Only a single SELECT with GROUP BY, no ORDER BY or LIMIT and exactly one aggregate column is touched.
    Returns the new SQL and the assumption text, or None to leave the query alone.
    """
    high, low = _words(goal, DESCENDING), _words(goal, ASCENDING)
    if bool(high) == bool(low):
        return None
    try:
        expression = parse_one(query, read="duckdb")
    except (ParseError, ValueError):
        return None
    if not isinstance(expression, exp.Select) or not expression.args.get("group"):
        return None
    if expression.args.get("order") or expression.args.get("limit"):
        return None
    aggregates = [
        (position, item)
        for position, item in enumerate(expression.expressions, start=1)
        if item.find(exp.AggFunc) is not None and item.find(exp.Window) is None
    ]
    if len(aggregates) != 1:
        return None
    position, item = aggregates[0]
    descending = bool(high)
    if isinstance(item, exp.Alias):
        target: exp.Expression = exp.column(exp.to_identifier(item.alias))
        label = item.alias
    else:
        target = exp.Literal.number(position)
        label = f"column {position}"
    expression.set("order", exp.Order(expressions=[exp.Ordered(this=target, desc=descending)]))
    word = (high or low)[0]
    direction = "descending" if descending else "ascending"
    return (
        expression.sql(dialect="duckdb"),
        f"Sorted the result by {label} {direction} because the question asks for the {word}",
    )


def match_stored_values(
    query: str, catalog: DataCatalog, timeout: float | None
) -> tuple[str, list[str]] | None:
    """Fix a filter whose text differs from the one stored value only in case or surrounding spaces.

    A comparison such as ``location = 'remote'`` that matches nothing is rewritten when exactly one distinct
    stored value equals the text ignoring case and spaces. Returns the new SQL and one note per fix.
    """
    try:
        expression = parse_one(query, read="duckdb")
    except (ParseError, ValueError):
        return None
    where = expression.find(exp.Where)
    if where is None:
        return None
    aliases, single = _tables(expression)
    notes: list[str] = []
    for comparison in list(where.find_all(exp.EQ)):
        left, right = comparison.this, comparison.expression
        if isinstance(right, exp.Column) and isinstance(left, exp.Literal):
            left, right = right, left
        if not (isinstance(left, exp.Column) and isinstance(right, exp.Literal) and right.is_string):
            continue
        table = _column_table(left, aliases, single)
        if table is None or table not in catalog.table_names():
            continue
        column, value, source = _quote(left.name), right.this, _qualified(table)
        text = f"CAST({column} AS VARCHAR)"
        exact = catalog.query(
            f"SELECT COUNT(*) AS n FROM {source} WHERE {text} = {_literal(value)}", timeout_seconds=timeout
        )["n"].iloc[0]
        if exact:
            continue
        found = catalog.query(
            f"SELECT DISTINCT {text} AS v FROM {source} WHERE {column} IS NOT NULL "
            f"AND lower(trim({text})) = lower(trim({_literal(value)})) LIMIT 3",
            timeout_seconds=timeout,
        )["v"].tolist()
        if len(found) != 1 or pd.isna(found[0]):
            continue
        right.set("this", str(found[0]))
        notes.append(
            f"Matched '{value}' to the stored value '{found[0]}' in {table}.{left.name} "
            "(case and spaces differ)"
        )
    if not notes:
        return None
    return expression.sql(dialect="duckdb"), notes
