from typing import Literal

import pandas as pd
from pydantic import BaseModel
from sqlglot import exp, parse_one
from sqlglot.errors import ParseError

from insightforge.core.catalog import DataCatalog, _qualified, _quote

FindingCode = Literal[
    "empty_result", "zero_result", "filter_matches_nothing", "many_to_many_join", "value_matched"
]
SERIOUS_FINDINGS = {"filter_matches_nothing", "many_to_many_join"}


class ResultFinding(BaseModel):
    step: str
    code: FindingCode
    message: str
    model_message: str


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _tables(expression: exp.Expression) -> tuple[dict[str, str], str | None]:
    ctes = {cte.alias_or_name for cte in expression.find_all(exp.CTE)}
    tables = [table for table in expression.find_all(exp.Table) if table.name not in ctes]
    aliases = {table.alias_or_name: table.name for table in tables}
    aliases.update({table.name: table.name for table in tables})
    names = {table.name for table in tables}
    return aliases, next(iter(names)) if len(names) == 1 else None


def _column_table(column: exp.Column, aliases: dict[str, str], single: str | None) -> str | None:
    return aliases.get(column.table) if column.table else single


def _filter_findings(
    step: str, expression: exp.Expression, catalog: DataCatalog, timeout: float | None
) -> list[ResultFinding]:
    aliases, single = _tables(expression)
    findings: list[ResultFinding] = []
    where = expression.find(exp.Where)
    if where is None:
        return findings
    for comparison in where.find_all(exp.EQ):
        left, right = comparison.this, comparison.expression
        if isinstance(right, exp.Column) and isinstance(left, exp.Literal):
            left, right = right, left
        if not (isinstance(left, exp.Column) and isinstance(right, exp.Literal) and right.is_string):
            continue
        table = _column_table(left, aliases, single)
        if table is None or table not in catalog.table_names():
            continue
        column, value = _quote(left.name), right.this
        source = _qualified(table)
        matches = catalog.query(
            f"SELECT COUNT(*) AS n FROM {source} WHERE CAST({column} AS VARCHAR) = {_literal(value)}",
            timeout_seconds=timeout,
        )["n"].iloc[0]
        if matches:
            continue
        text, target = f"CAST({column} AS VARCHAR)", f"lower({_literal(value)})"
        similar = catalog.query(
            f"SELECT DISTINCT {text} AS v FROM {source} WHERE {column} IS NOT NULL "
            f"AND (lower(trim({text})) = trim({target}) "
            f"OR jaro_winkler_similarity(lower({text}), {target}) >= 0.85) "
            f"ORDER BY jaro_winkler_similarity(lower(v), {target}) DESC LIMIT 3",
            timeout_seconds=timeout,
        )["v"].tolist()
        base = f"{step}: the filter {left.name} = '{value}' matches no rows in {table}"
        hint = f"; similar values: {', '.join(repr(item) for item in similar)}" if similar else ""
        vague = "; the column has values that differ only in spelling or capitalisation"
        findings.append(
            ResultFinding(
                step=step,
                code="filter_matches_nothing",
                message=f"{base}{hint}.",
                model_message=f"{base}{vague if similar else ''}.",
            )
        )
    return findings


def _join_findings(
    step: str, expression: exp.Expression, catalog: DataCatalog, timeout: float | None
) -> list[ResultFinding]:
    aliases, _ = _tables(expression)
    findings: list[ResultFinding] = []
    for join in expression.find_all(exp.Join):
        condition = join.args.get("on")
        if condition is None:
            continue
        for comparison in condition.find_all(exp.EQ):
            sides = [comparison.this, comparison.expression]
            if not all(isinstance(side, exp.Column) and side.table for side in sides):
                continue
            duplicated = []
            for side in sides:
                table = aliases.get(side.table)
                if table is None or table not in catalog.table_names():
                    break
                column = _quote(side.name)
                repeats = catalog.query(
                    f"SELECT COUNT({column}) - COUNT(DISTINCT {column}) AS n FROM {_qualified(table)}",
                    timeout_seconds=timeout,
                )["n"].iloc[0]
                if repeats:
                    duplicated.append(f"{table}.{side.name}")
            if len(duplicated) == 2:
                message = (
                    f"{step}: the join on {' = '.join(duplicated)} repeats keys on both sides, so rows are "
                    "multiplied and totals may be counted more than once."
                )
                findings.append(
                    ResultFinding(step=step, code="many_to_many_join", message=message, model_message=message)
                )
    return findings


def check_result(
    step: str, sql: str, frame: pd.DataFrame, catalog: DataCatalog, timeout: float | None = None
) -> list[ResultFinding]:
    findings: list[ResultFinding] = []
    suspicious = False
    if frame.empty:
        suspicious = True
        message = f"{step}: the query returned no rows."
        findings.append(ResultFinding(step=step, code="empty_result", message=message, model_message=message))
    elif len(frame) == 1:
        values = frame.iloc[0].tolist()
        numeric = [
            value for value in values if isinstance(value, int | float) and not isinstance(value, bool)
        ]
        if numeric and all(value is None or pd.isna(value) or value == 0 for value in values):
            suspicious = True
            message = f"{step}: the only result is 0 or empty."
            findings.append(
                ResultFinding(step=step, code="zero_result", message=message, model_message=message)
            )
    try:
        expression = parse_one(sql, read="duckdb")
    except (ParseError, ValueError):
        return findings
    try:
        if suspicious:
            findings.extend(_filter_findings(step, expression, catalog, timeout))
        findings.extend(_join_findings(step, expression, catalog, timeout))
    except Exception:
        return findings
    return findings
