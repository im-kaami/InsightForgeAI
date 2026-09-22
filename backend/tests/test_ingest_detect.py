import pytest

from insightforge.ingest import IngestError, detect_source, table_name_for


@pytest.mark.parametrize(
    ("filename", "kind"),
    [
        ("x.csv", "csv"),
        ("x.tsv", "tsv"),
        ("x.parquet", "parquet"),
        ("x.jsonl", "json"),
        ("x.xlsx", "excel"),
        ("data.db", "sqlite"),
    ],
)
def test_detects_local_sources(tmp_path, filename, kind):
    path = tmp_path / filename
    path.touch()
    assert detect_source(str(path)).kind == kind


@pytest.mark.parametrize(
    ("location", "kind"),
    [
        ("https://a.com/x.csv", "url"),
        ("https://docs.google.com/spreadsheets/d/abc/edit#gid=5", "gsheet"),
        ("postgresql://u:p@h/db", "postgres"),
        ("postgres://u:p@h/db", "postgres"),
        ("mysql://u:p@h/db", "mysql"),
        ("mssql+pyodbc://u:p@h/db", "sqlalchemy"),
    ],
)
def test_detects_remote_and_database_sources(location, kind):
    assert detect_source(location).kind == kind


def test_detects_sqlite_uri(tmp_path):
    path = tmp_path / "x.db"
    path.touch()
    assert detect_source(f"sqlite:///{path.as_posix()}").kind == "sqlite"


def test_unknown_and_missing_local_sources_raise(tmp_path):
    unknown = tmp_path / "x.txt"
    unknown.touch()
    with pytest.raises(IngestError, match="Supported types"):
        detect_source(str(unknown))
    with pytest.raises(IngestError, match="does not exist"):
        detect_source(str(tmp_path / "missing.csv"))


def test_table_name_uses_sanitized_stem():
    assert table_name_for("My Data (2024).csv") == "my_data_2024"
