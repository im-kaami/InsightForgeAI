from datetime import UTC, datetime

import duckdb
from pydantic import BaseModel, Field

from insightforge.core.catalog import DataCatalog, QueryTimeoutError, _qualified, _quote
from insightforge.core.sensitivity import classify_column


class ColumnQuality(BaseModel):
    name: str
    dtype: str
    sensitivity: str | None = None
    null_count: int | None = None
    null_fraction: float | None = None
    distinct_count: int | None = None
    repeated_non_null_count: int | None = None


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


def profile_catalog(
    catalog: DataCatalog, *, timeout_seconds: float = 10, max_columns: int = 100
) -> DataProfile:
    if timeout_seconds <= 0 or max_columns < 1:
        raise ValueError("Profiling limits must be positive")
    result = DataProfile()
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
                    name=column_name, dtype=dtype, sensitivity=classify_column(column_name)
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
            remaining = max(0, remaining - len(table.columns))
        except (duckdb.Error, QueryTimeoutError) as error:
            table.complete = False
            result.warnings.append(
                f"{name}: profiling incomplete ({type(error).__name__}); missing checks are unknown"
            )
        result.complete = result.complete and table.complete
    return result
