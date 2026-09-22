import os
import sqlite3

import pytest

from insightforge.core.catalog import DataCatalog
from insightforge.ingest import DataSource, load_any, load_source, parse_db_uri, redact_uri


def _sqlite_database(path):
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE users (id INTEGER, name TEXT)")
        connection.execute("INSERT INTO users VALUES (1, 'Ada')")
        connection.execute("CREATE TABLE events (id INTEGER, user_id INTEGER)")
        connection.execute("INSERT INTO events VALUES (10, 1)")


def test_attaches_sqlite_database_and_introspects(tmp_path):
    path = tmp_path / "source.sqlite"
    _sqlite_database(path)
    catalog = DataCatalog()
    try:
        result = load_any(f"sqlite:///{path.as_posix()}", catalog)
        assert result.tables == ["sqlite.events", "sqlite.users"]
        assert catalog.introspect().table("sqlite.users").row_count == 1
    finally:
        catalog.close()


def test_parse_database_uris_and_redaction():
    assert parse_db_uri("postgres://u:p@h/db") == ("postgres", "postgresql://u:p@h/db")
    kind, connection = parse_db_uri("mysql://u:secret@host:3307/app")
    assert kind == "mysql"
    assert "host=host" in connection
    assert "user=u" in connection
    assert "password=secret" in connection
    assert "secret" not in redact_uri("mysql://u:secret@host:3307/app")
    assert "***" in redact_uri("mysql://u:secret@host:3307/app")


def test_sqlalchemy_fallback_materializes_tables(tmp_path):
    path = tmp_path / "fallback.sqlite"
    _sqlite_database(path)
    source = DataSource(kind="sqlalchemy", location=f"sqlite+pysqlite:///{path.as_posix()}")
    catalog = DataCatalog()
    try:
        result = load_source(source, catalog)
        assert result.tables == ["sqlalchemy__events", "sqlalchemy__users"]
        assert catalog.introspect().table("sqlalchemy__users").row_count == 1
    finally:
        catalog.close()


@pytest.mark.integration
def test_postgres_attach():
    uri = os.getenv("INSIGHTFORGE_TEST_PG_URI")
    if not uri:
        pytest.skip("INSIGHTFORGE_TEST_PG_URI is not set")
    catalog = DataCatalog()
    try:
        result = load_any(uri, catalog)
        assert result.tables
    finally:
        catalog.close()
