import re
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import duckdb
import pandas as pd

from insightforge.core.schema import ColumnInfo, SchemaInfo, TableInfo
from insightforge.core.sensitivity import classify_column

if TYPE_CHECKING:
    from insightforge.ingest.base import LoadResult


class QueryTimeoutError(RuntimeError):
    pass


def sanitize_identifier(name: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    value = re.sub(r"_+", "_", value)
    if not value or value[0].isdigit():
        value = f"t_{value}"
    return value


def _quote(name: str) -> str:
    return f'"{name.replace(chr(34), chr(34) * 2)}"'


def _qualified(name: str) -> str:
    return ".".join(_quote(part) for part in name.split("."))


class DataCatalog:
    def __init__(
        self,
        db_path: str | Path | None = None,
        memory_limit: str | None = None,
        threads: int | None = None,
        read_only: bool = False,
    ):
        self.connection = duckdb.connect(
            str(db_path) if db_path is not None else ":memory:", read_only=read_only
        )
        if memory_limit is not None:
            escaped_limit = memory_limit.replace("'", "''")
            self.connection.execute(f"SET memory_limit='{escaped_limit}'")
        if threads is not None:
            self.connection.execute(f"SET threads={int(threads)}")
        self.attachments: dict[str, str] = {}

    def register_df(self, name: str, df: pd.DataFrame) -> None:
        table_name = "__".join(sanitize_identifier(part) for part in name.split("__"))
        self.connection.register("_insightforge_df", df)
        try:
            self.connection.execute(
                f"CREATE OR REPLACE TABLE {_quote(table_name)} AS SELECT * FROM _insightforge_df"
            )
        finally:
            self.connection.unregister("_insightforge_df")

    def create_table_from_query(self, name: str, select_sql: str) -> None:
        self.connection.execute(
            f"CREATE OR REPLACE TABLE {_quote(sanitize_identifier(name))} AS {select_sql}"
        )

    def attach(
        self, alias: str, uri: str, db_type: Literal["postgres", "mysql", "sqlite"]
    ) -> None:
        clean_alias = sanitize_identifier(alias)
        escaped_uri = uri.replace("'", "''")
        self.connection.execute(f"INSTALL {db_type}")
        self.connection.execute(f"LOAD {db_type}")
        self.connection.execute(
            f"ATTACH '{escaped_uri}' AS {_quote(clean_alias)} (TYPE {db_type}, READ_ONLY)"
        )
        self.attachments[clean_alias] = db_type

    def table_names(self) -> list[str]:
        rows = self.connection.execute(
            "SELECT database_name, table_name FROM duckdb_tables() "
            "WHERE NOT internal ORDER BY database_name, table_name"
        ).fetchall()
        return [
            f"{database}.{table}" if database in self.attachments else str(table)
            for database, table in rows
        ]

    def introspect(
        self, sample_rows: int = 3, redact_sensitive_samples: bool = True
    ) -> SchemaInfo:
        tables: list[TableInfo] = []
        for name in self.table_names():
            qualified = _qualified(name)
            description = self.connection.execute(f"DESCRIBE SELECT * FROM {qualified}").fetchall()
            row_count = self.connection.execute(f"SELECT COUNT(*) FROM {qualified}").fetchone()[0]
            attached = "." in name and name.split(".", 1)[0] in self.attachments
            columns: list[ColumnInfo] = []
            for row in description:
                column_name, dtype = str(row[0]), str(row[1])
                column_sql = _quote(column_name)
                sensitivity = classify_column(column_name)
                samples: list[tuple[Any, ...]] = []
                if sample_rows > 0 and not (sensitivity and redact_sensitive_samples):
                    samples = self.connection.execute(
                        f"SELECT CAST(v AS VARCHAR) FROM (SELECT DISTINCT {column_sql} AS v "
                        f"FROM {qualified} WHERE {column_sql} IS NOT NULL "
                        f"ORDER BY v LIMIT {int(sample_rows)}) t"
                    ).fetchall()
                should_redact = sample_rows > 0 and sensitivity and redact_sensitive_samples
                sample_values = ["<redacted>"] if should_redact else [
                    str(value[0])[:40] for value in samples
                ]
                null_fraction = None
                if not attached:
                    null_fraction = self.connection.execute(
                        f"SELECT COUNT(*) FILTER (WHERE {column_sql} IS NULL)::DOUBLE "
                        f"/ NULLIF(COUNT(*), 0) FROM {qualified}"
                    ).fetchone()[0]
                columns.append(
                    ColumnInfo(
                        name=column_name,
                        dtype=dtype,
                        sample_values=sample_values,
                        null_fraction=null_fraction,
                        sensitivity=sensitivity,
                    )
                )
            tables.append(TableInfo(name=name, row_count=int(row_count), columns=columns))
        return SchemaInfo(tables=tables)

    def query(self, sql: str, timeout_seconds: float | None = None) -> pd.DataFrame:
        timer = None
        if timeout_seconds is not None:
            timer = threading.Timer(timeout_seconds, self.connection.interrupt)
            timer.start()
        try:
            return self.connection.execute(sql).fetchdf()
        except duckdb.InterruptException as error:
            if timeout_seconds is None:
                raise
            raise QueryTimeoutError(f"Query exceeded {timeout_seconds} seconds") from error
        finally:
            if timer:
                timer.cancel()

    def load(self, location: str, **kwargs: Any) -> "LoadResult":
        from insightforge.ingest import load_any

        return load_any(location, self, **kwargs)

    def set_external_access(self, enabled: bool) -> None:
        value = "true" if enabled else "false"
        self.connection.execute(f"SET enable_external_access={value}")

    def lock(self) -> None:
        self.set_external_access(False)
        self.connection.execute("SET lock_configuration=true")

    def close(self) -> None:
        self.connection.close()
