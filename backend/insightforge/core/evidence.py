import math
import re
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field
from sqlglot import exp, parse_one
from sqlglot.errors import ParseError

from insightforge.core.schema import SchemaInfo, is_identifier

_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)?\b")
_LIST_MARKER = re.compile(r"(?m)^(\s*)\d+[.)](?=\s)")
_NUMBER = re.compile(r"(?<![\w.,])(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?")
_SUFFIX = re.compile(r"(k|K|M|B|bn)(?![A-Za-z])|\s(thousand|million|billion)\b", re.IGNORECASE)
_MULTIPLIERS = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "b": 1e9, "bn": 1e9, "billion": 1e9}


class EvidenceItem(BaseModel):
    id: str
    text: str
    value: Any
    artifact: str
    row: int | None = None
    column: str | None = None
    kind: Literal["cell", "column_total", "column_average", "row_count"] = "cell"


class NumberCheck(BaseModel):
    checked: int = 0
    matched: int = 0
    unmatched: list[str] = Field(default_factory=list)

    @property
    def message(self) -> str:
        return f"{self.matched} of {self.checked} numbers in the summary were found in the results"


@dataclass(frozen=True)
class NumberClaim:
    text: str
    value: float
    tolerance: float
    percent: bool


def _blank(match: re.Match[str]) -> str:
    return " " * len(match.group(0))


def _blank_marker(match: re.Match[str]) -> str:
    indent = match.group(1)
    return indent + " " * (len(match.group(0)) - len(indent))


def extract_numbers(text: str, ignore: set[float] | None = None) -> list[NumberClaim]:
    ignore = ignore or set()
    cleaned = _DATE.sub(_blank, _LIST_MARKER.sub(_blank_marker, text))
    claims: dict[str, NumberClaim] = {}
    for match in _NUMBER.finditer(cleaned):
        whole, fraction = match.group(1), match.group(2) or ""
        rest = cleaned[match.end() : match.end() + 12]
        percent = bool(re.match(r"\s?%", rest))
        suffix = None if percent else _SUFFIX.match(rest)
        if not percent and not suffix and rest[:1].isalpha():
            continue
        multiplier = _MULTIPLIERS[(suffix.group(1) or suffix.group(2)).lower()] if suffix else 1.0
        decimals = len(fraction) - 1 if fraction else 0
        start = match.start()
        negative = start > 0 and cleaned[start - 1] in "-\u2212" and (
            start < 2 or not cleaned[start - 2].isalnum()
        )
        number = float(whole.replace(",", "") + fraction)
        value = (-number if negative else number) * multiplier
        plain_integer = not fraction and not percent and not suffix
        if plain_integer and (abs(value) <= 10 or ("," not in whole and 1900 <= value <= 2100)):
            continue
        if value in ignore:
            continue
        written = text[start - (1 if negative else 0) : match.end()] + (
            "%" if percent else (suffix.group(0) if suffix else "")
        )
        written = written.strip()
        claims.setdefault(
            written,
            NumberClaim(written, value, 0.5 * 10 ** (-decimals) * multiplier, percent),
        )
    return list(claims.values())


def _native(value: Any) -> Any:
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _numeric_columns(frame: pd.DataFrame) -> list[tuple[str, np.ndarray]]:
    columns = []
    for column in frame.columns:
        series = frame[column]
        if pd.api.types.is_bool_dtype(series):
            continue
        if not pd.api.types.is_numeric_dtype(series):
            if pd.api.types.infer_dtype(series, skipna=True) != "decimal":
                continue
            series = pd.to_numeric(series, errors="coerce")
        columns.append((str(column), series.to_numpy(dtype=float, na_value=np.nan)))
    return columns


def check_numbers(
    summary: str,
    tables: dict[str, pd.DataFrame],
    row_counts: dict[str, list[int]] | None = None,
    ignore: set[float] | None = None,
) -> tuple[NumberCheck, list[EvidenceItem]]:
    row_counts = row_counts or {}
    columns = {name: _numeric_columns(frame) for name, frame in tables.items()}
    evidence: list[EvidenceItem] = []
    unmatched: list[str] = []
    claims = extract_numbers(summary, ignore)
    for claim in claims:
        targets = [(claim.value, claim.tolerance)]
        if claim.percent:
            targets.append((claim.value / 100, claim.tolerance / 100))
        found: tuple[str, int | None, str | None, Any, str] | None = None
        for name, frame in tables.items():
            for column, values in columns[name]:
                for target, tolerance in targets:
                    slack = tolerance + 1e-9 * max(1.0, abs(target))
                    hits = np.flatnonzero(np.abs(values - target) <= slack)
                    if hits.size:
                        row = int(hits[0])
                        found = (name, row + 1, column, _native(frame[column].iloc[row]), "cell")
                        break
                if found:
                    break
            if found:
                break
            if claim.percent:
                continue
            for column, values in columns[name]:
                if len(values) < 2 or is_identifier(column) or np.isnan(values).all():
                    continue
                for kind, statistic in (
                    ("column_total", float(np.nansum(values))),
                    ("column_average", float(np.nanmean(values))),
                ):
                    if abs(statistic - claim.value) <= claim.tolerance:
                        found = (name, None, column, _native(statistic), kind)
                        break
                if found:
                    break
            count = next(
                (item for item in row_counts.get(name, []) if abs(item - claim.value) <= claim.tolerance),
                None,
            )
            if not found and count is not None:
                found = (name, None, None, count, "row_count")
            if found:
                break
        if found:
            name, row, column, value, kind = found
            evidence.append(
                EvidenceItem(
                    id=f"N{len(evidence) + 1}",
                    text=claim.text,
                    value=value,
                    artifact=name,
                    row=row,
                    column=column,
                    kind=kind,
                )
            )
        else:
            unmatched.append(claim.text)
    return NumberCheck(checked=len(claims), matched=len(evidence), unmatched=unmatched), evidence


def sql_literals(sql: str) -> set[float]:
    try:
        expression = parse_one(sql, read="duckdb")
    except (ParseError, ValueError):
        return set()
    values: set[float] = set()
    for literal in expression.find_all(exp.Literal):
        if not literal.is_string:
            try:
                values.add(float(literal.this))
            except ValueError:
                continue
    return values


def _sql(node: exp.Expression) -> str:
    return node.sql(dialect="duckdb")


def describe_query(
    name: str, sql: str, schema: SchemaInfo | None = None, result_limit: int = 10000
) -> str:
    try:
        expression = parse_one(sql, read="duckdb")
    except (ParseError, ValueError):
        return f"{name}: the query could not be described automatically."
    ctes = {cte.alias_or_name for cte in expression.find_all(exp.CTE)}
    tables = list(
        dict.fromkeys(table.name for table in expression.find_all(exp.Table) if table.name not in ctes)
    )
    parts = [f"reads {', '.join(tables)}" if tables else "reads no tables"]
    for join in expression.find_all(exp.Join):
        condition = join.args.get("on")
        target = join.this.name if isinstance(join.this, exp.Table) else join.this.alias_or_name
        parts.append(f"joins {target}" + (f" on {_sql(condition)}" if condition else ""))
    where = expression.args.get("where")
    parts.append(f"keeps only rows where {_sql(where.this)}" if where else "uses every row (no filter)")
    group = expression.args.get("group")
    if group:
        parts.append(f"groups by {', '.join(_sql(item) for item in group.expressions)}")
    aggregates = list(dict.fromkeys(_sql(node) for node in expression.find_all(exp.AggFunc)))
    if aggregates:
        parts.append(f"calculates {', '.join(aggregates[:6])}")
    having = expression.args.get("having")
    if having:
        parts.append(f"keeps only groups where {_sql(having.this)}")
    limit = expression.args.get("limit")
    if limit is not None:
        try:
            count = int(_sql(limit.expression))
        except (TypeError, ValueError, AttributeError):
            count = None
        if count is not None and count < result_limit:
            order = expression.args.get("order")
            by = f" by {', '.join(_sql(item) for item in order.expressions)}" if order else ""
            parts.append(f"keeps only the first {count:,} rows{by}")
    if schema is not None and aggregates:
        referenced = {
            column.name.casefold()
            for node in expression.find_all(exp.AggFunc)
            for column in node.find_all(exp.Column)
        }
        for table in schema.tables:
            if table.name not in tables:
                continue
            for column in table.columns:
                if column.name.casefold() in referenced and column.null_fraction:
                    parts.append(
                        f"{column.name} is missing in {column.null_fraction:.0%} of {table.name} rows, "
                        "and those rows are skipped by the calculation"
                    )
    return f"{name}: " + "; ".join(parts) + "."
