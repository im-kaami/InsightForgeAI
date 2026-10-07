import sqlite3

import pytest

from insightforge.config import Settings, get_settings, validate_settings
from insightforge.core.catalog import DataCatalog
from insightforge.ingest import IngestError, parse_db_uri, redact_uri
from insightforge.ingest.database import _with_snowflake_schema, check_database_target, list_tables


def test_sql_server_uris_are_normalized_to_pymssql():
    for uri in (
        "mssql://sa:pw@host:1433/db",
        "sqlserver://sa:pw@host:1433/db",
        "mssql+pymssql://sa:pw@host:1433/db",
        "MSSQL://sa:pw@host:1433/db",
    ):
        assert parse_db_uri(uri) == ("mssql", "mssql+pymssql://sa:pw@host:1433/db")


def test_other_sqlalchemy_uris_stay_generic():
    assert parse_db_uri("mssql+pyodbc://sa:pw@host/db?driver=x")[0] == "sqlalchemy"
    assert parse_db_uri("oracle+oracledb://u:p@h/db")[0] == "sqlalchemy"


def test_redaction_hides_sql_server_passwords_with_special_characters():
    password = "p%40ss%3Aw%2Ford%21"
    uri = f"mssql+pymssql://sa:{password}@db.example.com:1433/sales"
    hidden = redact_uri(uri)
    assert hidden == "mssql+pymssql://sa:***@db.example.com:1433/sales"
    assert not any(piece in hidden for piece in ("p%40", "%3A", "ord", "%21"))
    assert redact_uri("sqlserver://sa:s3cret@host/db").count("s3cret") == 0
    assert redact_uri("mssql://sa@host/db") == "mssql://sa@host/db"


def _sqlite(path):
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE users (id INTEGER, name TEXT)")
        connection.execute("CREATE TABLE events (id INTEGER)")


def test_list_tables_reads_names_without_copying(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLOW_SQLITE_FILES", "true")
    get_settings.cache_clear()
    path = tmp_path / "source.sqlite"
    _sqlite(path)
    assert list_tables(f"sqlite:///{path.as_posix()}") == ["events", "users"]
    assert list_tables(f"sqlite+pysqlite:///{path.as_posix()}") == ["events", "users"]


def test_list_tables_errors_hide_the_password(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLOW_SQLITE_FILES", "true")
    get_settings.cache_clear()
    with pytest.raises(IngestError) as caught:
        list_tables(f"sqlite:///{(tmp_path / 'missing.sqlite').as_posix()}")
    assert "does not exist" in str(caught.value)
    with pytest.raises(IngestError) as caught:
        list_tables("mssql://sa:topsecret@127.0.0.1:1/db")
    assert "topsecret" not in str(caught.value)


def test_catalog_only_lists_the_default_schema_of_an_attached_postgres(tmp_path):
    catalog = DataCatalog()
    try:
        catalog.attachments["pg"] = "postgres"
        catalog.attachment_schemas["pg"] = "public"
        assert catalog.table_names() == []
    finally:
        catalog.close()


def test_allow_sqlite_files_is_a_problem_in_production():
    base = {"jwt_secret": "x" * 40, "app_secret": "y" * 40}
    production = Settings(environment="production", allow_sqlite_files=True, **base)
    assert any("ALLOW_SQLITE_FILES" in problem for problem in validate_settings(production))
    development = Settings(environment="development", allow_sqlite_files=True, **base)
    assert not any("ALLOW_SQLITE_FILES" in problem for problem in validate_settings(development))
    safe = validate_settings(Settings(environment="production", **base))
    assert not any("ALLOW_SQLITE_FILES" in problem for problem in safe)


def test_list_tables_refuses_sqlite_files_by_default(tmp_path):
    path = tmp_path / "source.sqlite"
    _sqlite(path)
    with pytest.raises(IngestError, match="ALLOW_SQLITE_FILES"):
        list_tables(f"sqlite:///{path.as_posix()}")


def test_snowflake_uris_keep_their_shape_and_redact_the_password():
    uri = "snowflake://kaami:p%40ss%3Aw%2Frd%3F%23@ft45233.eu-central-2.aws/DB/PUBLIC?warehouse=COMPUTE_WH"
    assert parse_db_uri(uri) == ("snowflake", uri)
    assert parse_db_uri(uri.upper().replace("KAAMI", "kaami"))[0] == "snowflake"
    hidden = redact_uri(uri)
    assert hidden == "snowflake://kaami:***@ft45233.eu-central-2.aws/DB/PUBLIC?warehouse=COMPUTE_WH"
    assert not any(piece in hidden for piece in ("p%40", "%3A", "ord", "%3F"))


def test_the_snowflake_schema_option_replaces_the_schema_in_the_address():
    base = "snowflake://u:p@acct.eu/DB/OLD?warehouse=W&role=R"
    assert _with_snowflake_schema(base, "NEW") == "snowflake://u:p@acct.eu/DB/NEW?warehouse=W&role=R"
    assert _with_snowflake_schema("snowflake://u:p@acct.eu/DB?warehouse=W", "S").endswith("/DB/S?warehouse=W")
    assert _with_snowflake_schema(base, None) == base
    with pytest.raises(IngestError, match="database"):
        _with_snowflake_schema("snowflake://u:p@acct.eu?warehouse=W", "S")


def test_snowflake_account_identifiers_are_checked(monkeypatch):
    for bad in ("snowflake://u:p@/DB", "snowflake://u:p@-bad/DB", "snowflake://u:p@a%20b/DB"):
        with pytest.raises(IngestError, match="account identifier"):
            check_database_target(bad)
    check_database_target("snowflake://u:p@ft45233.eu-central-2.aws/DB/S?warehouse=W")


def test_in_production_the_snowflake_host_is_what_gets_resolved(monkeypatch):
    from insightforge.ingest import netguard

    monkeypatch.setenv("ENVIRONMENT", "production")
    get_settings.cache_clear()
    seen = []

    def resolver(hostname):
        seen.append(hostname)
        return ["93.184.216.34"]

    monkeypatch.setattr(netguard, "resolve_host", resolver)
    check_database_target("snowflake://u:p@ft45233.eu-central-2.aws/DB/S")
    assert seen == ["ft45233.eu-central-2.aws.snowflakecomputing.com"]
    monkeypatch.setattr(netguard, "resolve_host", lambda _host: ["10.0.0.5"])
    with pytest.raises(IngestError, match="ALLOW_PRIVATE_DATABASES"):
        check_database_target("snowflake://u:p@ft45233.eu-central-2.aws/DB/S")
