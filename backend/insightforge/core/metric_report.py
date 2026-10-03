"""Checked reports for any approved metric (Phase 4c).

A report calculates one approved metric for a reporting period and the preceding period of the same
length, optionally split by one of the metric's allowed groupings. Code writes every query (through
:func:`insightforge.core.metrics.compile_query`), runs data checks first, and writes the summary from
the results; no AI model is involved. Blocking checks stop the report; other findings are listed as
review notes and mark the report "needs review".

All texts are ASCII so the CLI and the Windows console can print them.
"""

from __future__ import annotations

import math
from datetime import timedelta
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel

from insightforge.core.catalog import DataCatalog, _qualified, _quote
from insightforge.core.metrics import (
    Metric,
    MetricError,
    MetricQuery,
    _canonical,
    _condition,
    _join_for,
    _resolve_dimension,
    compile_query,
)
from insightforge.core.relationships import Relationship
from insightforge.core.schema import SchemaInfo
from insightforge.core.sql_guard import guard_sql
from insightforge.core.verified_report import (
    CalculationCheck,
    EvidenceValue,
    ReportPeriod,
    ReportValidationError,
)

ENGINE_VERSION = "metric_report_v1"
ARTIFACT = "metric_report"


class MetricReportResult(BaseModel):
    columns: list[str]
    rows: list[dict[str, Any]]
    sql: str
    summary: str
    checks: list[CalculationCheck]
    warnings: list[str]
    evidence: list[EvidenceValue]
    comparison: dict[str, float | None]
    definition: str
    verification: Literal["checks_passed", "needs_review"]
    engine_version: str = ENGINE_VERSION


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _display(value: float | None, unit: str = "") -> str:
    if value is None:
        return "not available"
    text = f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"
    return f"{unit} {text}".strip()


def _percent(change: float | None, previous: float | None) -> float | None:
    if change is None or previous is None or previous <= 0:
        return None
    return round(change / previous * 100, 2)


def calculate_metric_report(
    catalog: DataCatalog,
    metric: Metric,
    period: ReportPeriod,
    schema: SchemaInfo,
    relationships: list[Relationship] | None = None,
    group_by: str | None = None,
    revision: int | None = None,
    timeout_seconds: float = 30,
) -> MetricReportResult:
    rels = list(relationships or [])
    if not metric.approved:
        raise MetricError(f"{metric.name} is a draft; approve it before reporting on it")
    if not metric.date_column:
        raise MetricError(f"{metric.name} has no date column, so it cannot be reported per period")
    checks: list[CalculationCheck] = []
    warnings: list[str] = []

    def count(sql: str) -> int:
        return int(catalog.query(sql, timeout_seconds=timeout_seconds).iloc[0, 0])

    def blocking(code: str, sql: str, message: str) -> None:
        affected = count(sql)
        checks.append(
            CalculationCheck(code=code, passed=affected == 0, affected_rows=affected, message=message)
        )
        if affected:
            raise ReportValidationError(checks)

    def review(code: str, sql: str, message: str) -> None:
        affected = count(sql)
        checks.append(
            CalculationCheck(code=code, passed=affected == 0, affected_rows=affected, message=message)
        )
        if affected:
            warnings.append(f"{message} ({affected:,} rows)")

    # Same validation compile_query does, before any query touches the data.
    compile_query(metric, MetricQuery(metric=metric.name), schema, rels, revision)
    base_info = schema.table(metric.table)
    assert base_info is not None
    base = f"{_qualified(base_info.name)} AS {_quote('t0')}"
    _, date_col = _canonical(schema, base_info.name, metric.date_column)
    raw_date = f"{_quote('t0')}.{_quote(date_col)}"
    fixed = (
        " AND ".join(
            _condition(f"{_quote('t0')}.{_quote(_canonical(schema, base_info.name, item.column)[1])}", item)
            for item in metric.filters
        )
        or "TRUE"
    )
    blocking(
        "invalid_dates",
        f"SELECT COUNT(*) FROM {base} WHERE ({fixed}) AND {raw_date} IS NOT NULL "
        f"AND TRY_CAST({raw_date} AS DATE) IS NULL",
        f"Dates in {date_col} that cannot be read as dates must be corrected before reporting",
    )
    review(
        "missing_dates",
        f"SELECT COUNT(*) FROM {base} WHERE ({fixed}) AND {raw_date} IS NULL",
        f"Rows without a date in {date_col} are left out of every period",
    )
    window = (
        f"({fixed}) AND CAST({raw_date} AS DATE) BETWEEN DATE '{period.previous_start.isoformat()}' "
        f"AND DATE '{period.end_date.isoformat()}'"
    )
    if metric.column and metric.aggregation != "count":
        _, value_col = _canonical(schema, base_info.name, metric.column)
        review(
            "missing_values",
            f"SELECT COUNT(*) FROM {base} WHERE {window} AND {_quote('t0')}.{_quote(value_col)} IS NULL",
            f"Rows with no {value_col} are skipped by the calculation",
        )
    grouping: tuple[str, str] | None = None
    if group_by:
        table, column, _ = _resolve_dimension(metric, group_by, schema, rels)
        grouping = (table, column)
        if table.casefold() != base_info.name.casefold():
            link = _join_for(metric, table, rels)
            assert link is not None  # _resolve_dimension and compile_query check the relationship
            base_key, other_key = link
            other = _qualified(table)
            blocking(
                "join_lookup_unique",
                f"SELECT COUNT(*) - COUNT(DISTINCT {_quote(other_key)}) FROM {other}",
                f"Keys in {table}.{other_key} must be unique and present, or rows would be counted twice",
            )
            review(
                "join_matches",
                f"SELECT COUNT(*) FROM {base} LEFT JOIN {other} AS {_quote('t1')} ON "
                f"{_quote('t0')}.{_quote(base_key)} = {_quote('t1')}.{_quote(other_key)} "
                f"WHERE {window} AND {_quote('t1')}.{_quote(other_key)} IS NULL",
                f"Rows with no matching {table} row are grouped as missing",
            )

    periods = {
        "current": (period.start_date, period.end_date),
        "previous": (period.previous_start, period.previous_end),
    }
    totals: dict[str, float | None] = {}
    grouped: dict[str, pd.DataFrame] = {}
    sqls: list[str] = []
    for label, (start, end) in periods.items():
        dates = {"date_from": start.isoformat(), "date_to": (end + timedelta(days=1)).isoformat()}
        total = compile_query(metric, MetricQuery(metric=metric.name, **dates), schema, rels, revision)
        sql = guard_sql(total.sql)
        sqls.append(f"-- {label} total\n{sql}")
        frame = catalog.query(sql, timeout_seconds=timeout_seconds)
        totals[label] = _number(frame.iloc[0, 0]) if len(frame) else None
        if group_by:
            split = compile_query(
                metric, MetricQuery(metric=metric.name, group_by=[group_by], **dates), schema, rels, revision
            )
            sql = guard_sql(split.sql)
            sqls.append(f"-- {label} by {group_by}\n{sql}")
            grouped[label] = catalog.query(sql, timeout_seconds=timeout_seconds)
    rows_in_period = count(
        f"SELECT COUNT(*) FROM {base} WHERE ({fixed}) AND CAST({raw_date} AS DATE) BETWEEN "
        f"DATE '{period.start_date.isoformat()}' AND DATE '{period.end_date.isoformat()}'"
    )
    if rows_in_period == 0:
        checks.append(
            CalculationCheck(
                code="no_current_rows", passed=False, message="No rows match this reporting period"
            )
        )
        raise ReportValidationError(checks)
    previous_rows = count(
        f"SELECT COUNT(*) FROM {base} WHERE ({fixed}) AND CAST({raw_date} AS DATE) BETWEEN "
        f"DATE '{period.previous_start.isoformat()}' AND DATE '{period.previous_end.isoformat()}'"
    )
    if previous_rows == 0:
        warnings.append("No rows in the previous period; the change is unavailable, not zero")
        totals["previous"] = None
    elif totals["previous"] is not None and totals["previous"] <= 0:
        warnings.append("The previous value is zero or negative; a percentage change is unavailable")

    def row(group: Any, current: float | None, previous: float | None) -> dict[str, Any]:
        change = current - previous if current is not None and previous is not None else None
        return {
            "group": group,
            "current": current,
            "previous": previous,
            "change": change,
            "change_percent": _percent(change, previous),
        }

    rows = [row("Total", totals["current"], totals["previous"])]
    if grouping:
        key = next(column for column in grouped["current"].columns if column != metric.name)
        previous_map = (
            {item[key]: _number(item[metric.name]) for item in grouped["previous"].to_dict(orient="records")}
            if previous_rows
            else {}
        )
        current_map = {
            item[key]: _number(item[metric.name]) for item in grouped["current"].to_dict(orient="records")
        }
        keys = list(current_map) + [item for item in previous_map if item not in current_map]
        for value in keys:
            rows.append(
                row(
                    "(missing)"
                    if value is None or (isinstance(value, float) and math.isnan(value))
                    else value,
                    current_map.get(value),
                    previous_map.get(value) if previous_rows else None,
                )
            )
    group_label = group_by or "group"
    columns = [group_label, "current", "previous", "change", "change_percent"]
    rows = [{group_label: item.pop("group"), **item} for item in rows]
    evidence = [
        EvidenceValue(id=f"{index}.{column}", artifact=ARTIFACT, row=index, column=column, value=None)
        for index, item in enumerate(rows)
        for column in ("current", "previous", "change", "change_percent")
    ]
    for item in evidence:
        value = rows[item.row][item.column]
        item.value = None if value is None else str(value)

    total = rows[0]
    unit = metric.unit
    current_text = f"{period.start_date.isoformat()} to {period.end_date.isoformat()}"
    previous_text = f"{period.previous_start.isoformat()} to {period.previous_end.isoformat()}"
    definition = compile_query(metric, MetricQuery(metric=metric.name), schema, rels, revision).description
    lines = [
        f"## {metric.display}: checked report",
        f"Period: {current_text} (inclusive), compared with {previous_text}.",
        f"- {metric.display}: {_display(total['current'], unit)}. Evidence: `0.current`.",
    ]
    if total["previous"] is not None:
        lines.append(f"- Previous period: {_display(total['previous'], unit)}. Evidence: `0.previous`.")
    if total["change_percent"] is not None:
        direction = "up" if total["change"] > 0 else "down" if total["change"] < 0 else "unchanged"
        lines.append(
            f"- Change: {_display(total['change'], unit)} ({direction} {abs(total['change_percent'])}%). "
            "Evidence: `0.change`, `0.change_percent`."
        )
    if grouping and len(rows) > 1:
        top = max(rows[1:], key=lambda item: item["current"] if item["current"] is not None else -math.inf)
        if top["current"] is not None:
            index = rows.index(top)
            lines.append(
                f"- Largest {group_label}: {top[group_label]} with {_display(top['current'], unit)}. "
                f"Evidence: `{index}.current`."
            )
    if warnings:
        lines.extend(["### Review notes", *[f"- {item}" for item in warnings]])
    lines.append(
        f"Definition (revision {revision}): {definition}" if revision else f"Definition: {definition}"
    )
    lines.append("Calculated by tested code from the approved definition. It does not establish causes.")
    return MetricReportResult(
        columns=columns,
        rows=rows,
        sql="\n\n".join(sqls),
        summary="\n\n".join(lines),
        checks=checks,
        warnings=warnings,
        evidence=evidence,
        comparison={"change": total["change"], "change_percent": total["change_percent"]},
        definition=definition,
        verification="needs_review" if warnings else "checks_passed",
    )
