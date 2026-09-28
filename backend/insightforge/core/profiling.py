import math
from datetime import UTC, datetime
from typing import Any, Literal

import duckdb
from pydantic import BaseModel, Field

from insightforge.core.catalog import DataCatalog, QueryTimeoutError, _qualified, _quote
from insightforge.core.schema import is_identifier
from insightforge.core.sensitivity import classify_column

PROFILE_VERSION = 2
HISTOGRAM_BINS = 10
TOP_VALUES = 5
OUTLIER_FACTOR = 1.5

ColumnKind = Literal["number", "date", "text", "boolean", "other"]
_NUMBER_TYPES = (
    "TINYINT",
    "SMALLINT",
    "INTEGER",
    "BIGINT",
    "HUGEINT",
    "UTINYINT",
    "USMALLINT",
    "UINTEGER",
    "UBIGINT",
    "UHUGEINT",
    "FLOAT",
    "DOUBLE",
    "REAL",
    "DECIMAL",
    "NUMERIC",
)


class ValueCount(BaseModel):
    value: str
    count: int


class ColumnQuality(BaseModel):
    name: str
    dtype: str
    sensitivity: str | None = None
    null_count: int | None = None
    null_fraction: float | None = None
    distinct_count: int | None = None
    repeated_non_null_count: int | None = None
    kind: ColumnKind | None = None
    min_value: str | None = None
    max_value: str | None = None
    mean: float | None = None
    median: float | None = None
    p25: float | None = None
    p75: float | None = None
    std: float | None = None
    skewness: float | None = None
    outlier_count: int | None = None
    histogram: list[int] = Field(default_factory=list)
    top_values: list[ValueCount] = Field(default_factory=list)
    numeric_text_count: int | None = None
    date_text_count: int | None = None
    distinct_days: int | None = None
    span_days: int | None = None
    alerts: list[str] = Field(default_factory=list)


class TableQuality(BaseModel):
    name: str
    row_count: int | None = None
    duplicate_rows: int | None = None
    columns: list[ColumnQuality] = Field(default_factory=list)
    complete: bool = True


class DataProfile(BaseModel):
    tables: list[TableQuality] = Field(default_factory=list)
    complete: bool = True
    warnings: list[str] = Field(default_factory=list)
    profiled_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source_freshness: str = "unknown"
    method: str = "exact counts; no source rows are modified"
    profile_version: int = 1


def column_kind(dtype: str) -> ColumnKind:
    upper = dtype.upper()
    if upper.startswith(_NUMBER_TYPES):
        return "number"
    if upper.startswith(("DATE", "TIMESTAMP")):
        return "date"
    if upper.startswith(("VARCHAR", "TEXT", "STRING", "CHAR")):
        return "text"
    if upper.startswith("BOOL"):
        return "boolean"
    return "other"


def _number(value: Any) -> float | None:
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _display(value: float) -> str:
    return str(int(value)) if float(value).is_integer() and abs(value) < 1e15 else f"{value:.6g}"


def profile_catalog(
    catalog: DataCatalog, *, timeout_seconds: float = 10, max_columns: int = 100
) -> DataProfile:
    if timeout_seconds <= 0 or max_columns < 1:
        raise ValueError("Profiling limits must be positive")
    result = DataProfile(
        method="exact counts and statistics; no source rows are modified",
        profile_version=PROFILE_VERSION,
    )
    remaining = max_columns
    for name in catalog.table_names():
        table = TableQuality(name=name)
        result.tables.append(table)
        qualified = _qualified(name)
        try:
            shape = catalog.query(f"SELECT COUNT(*) AS n FROM {qualified}", timeout_seconds=timeout_seconds)
            table.row_count = int(shape.iloc[0, 0])
            distinct = catalog.query(
                f"SELECT COUNT(*) AS n FROM (SELECT DISTINCT * FROM {qualified}) AS distinct_rows",
                timeout_seconds=timeout_seconds,
            )
            table.duplicate_rows = table.row_count - int(distinct.iloc[0, 0])
            if table.duplicate_rows:
                result.warnings.append(
                    f"{name}: {table.duplicate_rows} exact duplicate rows; review before reporting"
                )
            description = catalog.connection.execute(f"DESCRIBE SELECT * FROM {qualified}").fetchall()
            if len(description) > remaining:
                table.complete = False
                result.warnings.append(
                    f"{name}: column profiling budget reached; unprofiled columns are unknown"
                )
            for row in description[:remaining]:
                column_name, dtype = str(row[0]), str(row[1])
                quoted = _quote(column_name)
                column = ColumnQuality(
                    name=column_name,
                    dtype=dtype,
                    sensitivity=classify_column(column_name),
                    kind=column_kind(dtype),
                )
                table.columns.append(column)
                counts = catalog.query(
                    f"SELECT COUNT({quoted}) AS present, COUNT(DISTINCT {quoted}) AS distinct_values "
                    f"FROM {qualified}",
                    timeout_seconds=timeout_seconds,
                ).iloc[0]
                present, distinct_count = int(counts["present"]), int(counts["distinct_values"])
                column.null_count = table.row_count - present
                column.null_fraction = column.null_count / table.row_count if table.row_count else None
                column.distinct_count = distinct_count
                column.repeated_non_null_count = present - distinct_count
                try:
                    _describe_column(catalog, qualified, column, present, timeout_seconds)
                except (duckdb.Error, QueryTimeoutError) as error:
                    table.complete = False
                    result.warnings.append(
                        f"{name}.{column_name}: statistics incomplete ({type(error).__name__})"
                    )
                column.alerts = _alerts(column, present)
            remaining = max(0, remaining - len(table.columns))
        except (duckdb.Error, QueryTimeoutError) as error:
            table.complete = False
            result.warnings.append(
                f"{name}: profiling incomplete ({type(error).__name__}); missing checks are unknown"
            )
        result.complete = result.complete and table.complete
    return result


def _describe_column(
    catalog: DataCatalog, qualified: str, column: ColumnQuality, present: int, timeout: float
) -> None:
    if present == 0:
        return
    quoted = _quote(column.name)

    def query(sql: str):
        return catalog.query(sql, timeout_seconds=timeout)

    if column.kind == "number":
        value = f"CAST({quoted} AS DOUBLE)"
        stats = query(
            f"SELECT MIN({value}) AS lo, MAX({value}) AS hi, AVG({value}) AS mean, "
            f"STDDEV_SAMP({value}) AS std, quantile_cont({value}, 0.25) AS p25, "
            f"quantile_cont({value}, 0.5) AS p50, quantile_cont({value}, 0.75) AS p75, "
            f"skewness({value}) AS skew FROM {qualified} WHERE isfinite({value})"
        ).iloc[0]
        lo, hi = _number(stats["lo"]), _number(stats["hi"])
        column.mean, column.std = _number(stats["mean"]), _number(stats["std"])
        column.p25, column.median, column.p75 = (
            _number(stats["p25"]),
            _number(stats["p50"]),
            _number(stats["p75"]),
        )
        column.skewness = _number(stats["skew"])
        if lo is None or hi is None:
            return
        column.min_value, column.max_value = _display(lo), _display(hi)
        if column.p25 is not None and column.p75 is not None:
            spread = column.p75 - column.p25
            low, high = column.p25 - OUTLIER_FACTOR * spread, column.p75 + OUTLIER_FACTOR * spread
            column.outlier_count = int(
                query(
                    f"SELECT COUNT(*) AS n FROM {qualified} WHERE isfinite({value}) "
                    f"AND ({value} < {low!r} OR {value} > {high!r})"
                )["n"].iloc[0]
            )
        if hi > lo:
            width = (hi - lo) / HISTOGRAM_BINS
            bins = query(
                f"SELECT LEAST(CAST(floor(({value} - {lo!r}) / {width!r}) AS BIGINT), "
                f"{HISTOGRAM_BINS - 1}) AS b, COUNT(*) AS n FROM {qualified} "
                f"WHERE isfinite({value}) GROUP BY 1"
            )
            counts = [0] * HISTOGRAM_BINS
            for index, count in zip(bins["b"], bins["n"], strict=True):
                counts[int(index)] = int(count)
            column.histogram = counts
    elif column.kind == "date":
        day = f"CAST({quoted} AS DATE)"
        stats = query(
            f"SELECT CAST(MIN({quoted}) AS VARCHAR) AS lo, CAST(MAX({quoted}) AS VARCHAR) AS hi, "
            f"COUNT(DISTINCT {day}) AS days, date_diff('day', MIN({day}), MAX({day})) + 1 AS span "
            f"FROM {qualified}"
        ).iloc[0]
        column.min_value = str(stats["lo"]).removesuffix(" 00:00:00")
        column.max_value = str(stats["hi"]).removesuffix(" 00:00:00")
        column.distinct_days, column.span_days = int(stats["days"]), int(stats["span"])
    elif column.kind == "text":
        if not column.sensitivity:
            common = query(
                f"SELECT CAST({quoted} AS VARCHAR) AS value, COUNT(*) AS n FROM {qualified} "
                f"WHERE {quoted} IS NOT NULL GROUP BY 1 ORDER BY n DESC, value LIMIT {TOP_VALUES}"
            )
            column.top_values = [
                ValueCount(value=str(value)[:80], count=int(count))
                for value, count in zip(common["value"], common["n"], strict=True)
            ]
        trimmed = f"TRIM({quoted})"
        parsed = query(
            f"SELECT COUNT(*) FILTER (WHERE TRY_CAST({trimmed} AS DOUBLE) IS NOT NULL) AS numeric_text, "
            f"COUNT(*) FILTER (WHERE TRY_CAST({trimmed} AS DATE) IS NOT NULL "
            f"OR TRY_CAST({trimmed} AS TIMESTAMP) IS NOT NULL) AS date_text "
            f"FROM {qualified} WHERE {quoted} IS NOT NULL"
        ).iloc[0]
        column.numeric_text_count = int(parsed["numeric_text"])
        column.date_text_count = int(parsed["date_text"])


def _alerts(column: ColumnQuality, present: int) -> list[str]:
    alerts: list[str] = []
    if present == 0:
        return ["All values are missing"]
    if column.null_fraction is not None and column.null_fraction >= 0.5:
        alerts.append(f"{column.null_fraction:.0%} of values are missing")
    if present >= 2 and column.distinct_count == 1:
        alerts.append("Every value is the same")
    if column.kind == "text":
        distinct = column.distinct_count or 0
        if present >= 5 and (column.numeric_text_count or 0) >= 0.95 * present:
            alerts.append("Values look like numbers but are stored as text")
        elif present >= 5 and (column.date_text_count or 0) >= 0.95 * present:
            alerts.append("Values look like dates but are stored as text")
        elif present >= 20 and distinct == present:
            alerts.append("Every value is different, so this may be an identifier")
        elif distinct >= 50 and distinct >= 0.5 * present:
            alerts.append(f"{distinct:,} different values; charts will show only the most common")
    if column.kind == "number" and not is_identifier(column.name):
        if column.outlier_count and column.p25 is not None and column.p75 is not None:
            spread = column.p75 - column.p25
            low = _display(column.p25 - OUTLIER_FACTOR * spread)
            high = _display(column.p75 + OUTLIER_FACTOR * spread)
            alerts.append(f"{column.outlier_count:,} values fall outside the usual range ({low} to {high})")
        if present >= 20 and column.skewness is not None and abs(column.skewness) >= 2:
            alerts.append("Values are highly skewed; the median may describe them better than the average")
    if column.kind == "date" and column.distinct_days and column.span_days:
        missing = column.span_days - column.distinct_days
        if column.span_days <= 3660 and column.distinct_days >= 7 and missing > 0:
            if column.distinct_days >= 0.5 * column.span_days:
                alerts.append(
                    f"{missing:,} days between {column.min_value} and {column.max_value} have no rows"
                )
    return alerts
