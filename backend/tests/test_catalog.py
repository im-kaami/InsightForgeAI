import sqlite3

import duckdb
import pytest

from insightforge.core.catalog import DataCatalog, QueryTimeoutError, sanitize_identifier


def test_introspection_reports_tables_rows_and_types(schema):
    assert len(schema.tables) == 2
    assert schema.table("employees").row_count == 60
    assert schema.table("departments").row_count == 5
    assert "INT" in next(c.dtype for c in schema.table("employees").columns if c.name == "employee_id")
    assert "VARCHAR" in next(c.dtype for c in schema.table("employees").columns if c.name == "department")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("My Sheet 1", "my_sheet_1"), ("2024", "t_2024"), ("---", "t_")],
)
def test_sanitize_identifier(raw, expected):
    assert sanitize_identifier(raw) == expected


def test_lock_blocks_external_file_access(catalog):
    catalog.lock()
    with pytest.raises(duckdb.Error):
        catalog.query("SELECT * FROM read_csv_auto('x.csv')")


def test_lock_blocks_file_access_on_a_writable_file_catalog(tmp_path):
    """Live-connection datasets keep a writable catalog file; locking must still stop file reads."""
    note = tmp_path / "note.txt"
    note.write_text("synthetic", encoding="utf-8")
    catalog = DataCatalog(tmp_path / "catalog.duckdb")
    try:
        assert catalog.query(f"SELECT content FROM read_text('{note.as_posix()}')").iloc[0, 0] == "synthetic"
        catalog.lock()
        with pytest.raises(duckdb.Error, match="disabled"):
            catalog.query(f"SELECT content FROM read_text('{note.as_posix()}')")
        assert catalog.query("SELECT 1 AS x").iloc[0, 0] == 1
    finally:
        catalog.close()


def test_query_timeout_interrupts_long_query():
    catalog = DataCatalog()
    try:
        with pytest.raises(QueryTimeoutError, match="Query exceeded 0.5 seconds"):
            catalog.query(
                "SELECT count(*) FROM range(3000000000) a, range(1000) b",
                timeout_seconds=0.5,
            )
    finally:
        catalog.close()


def test_attached_sqlite_table_is_listed_and_queryable(tmp_path):
    path = tmp_path / "source.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE widgets (id INTEGER, name TEXT)")
        connection.executemany("INSERT INTO widgets VALUES (?, ?)", [(1, "one"), (2, "two")])
    catalog = DataCatalog()
    try:
        catalog.attach("external", str(path), "sqlite")
        assert "external.widgets" in catalog.table_names()
        catalog.lock()
        assert catalog.query('SELECT * FROM "external"."widgets"').shape == (2, 2)
    finally:
        catalog.close()
