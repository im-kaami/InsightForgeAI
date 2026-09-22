import sqlite3

import duckdb
import pytest

from insightforge.core.catalog import DataCatalog, sanitize_identifier


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
