from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from insightforge.core.catalog import DataCatalog, sanitize_identifier

SourceKind = Literal[
    "csv",
    "tsv",
    "parquet",
    "json",
    "excel",
    "url",
    "gsheet",
    "postgres",
    "mysql",
    "sqlite",
    "sqlalchemy",
    "mssql",
]


class DataSource(BaseModel):
    kind: SourceKind
    location: str
    options: dict[str, Any] = Field(default_factory=dict)
    name: str | None = None


class IngestError(ValueError):
    pass


class LoadResult(BaseModel):
    tables: list[str]
    notes: list[str] = Field(default_factory=list)


def table_name_for(path_or_name: str) -> str:
    parsed = urlparse(path_or_name)
    value = Path(parsed.path if parsed.scheme else path_or_name).stem
    return sanitize_identifier(value)


def _local_source(
    kind: SourceKind, location: str, name: str | None, options: dict[str, Any]
) -> DataSource:
    path = Path(location).expanduser()
    if not path.exists():
        raise IngestError(f"Local source does not exist: {location}")
    return DataSource(kind=kind, location=str(path), name=name, options=options)


def detect_source(
    location: str, name: str | None = None, options: dict[str, Any] | None = None
) -> DataSource:
    options = dict(options or {})
    lowered = location.lower()
    if "docs.google.com/spreadsheets/d/" in lowered:
        return DataSource(kind="gsheet", location=location, name=name, options=options)
    if lowered.startswith(("http://", "https://")):
        return DataSource(kind="url", location=location, name=name, options=options)
    if lowered.startswith(("postgres://", "postgresql://")):
        return DataSource(kind="postgres", location=location, name=name, options=options)
    if lowered.startswith(("mysql://", "mysql+pymysql://")):
        return DataSource(kind="mysql", location=location, name=name, options=options)
    if lowered.startswith("sqlite://"):
        path = location[len("sqlite:///") :] if lowered.startswith("sqlite:///") else location[9:]
        return _local_source("sqlite", path, name, options)
    if "://" in location:
        dialect = lowered.split("://", 1)[0]
        if dialect and all(character.isalnum() or character in "+_-" for character in dialect):
            return DataSource(kind="sqlalchemy", location=location, name=name, options=options)

    suffix = Path(location).suffix.lower()
    if suffix == ".duckdb":
        raise IngestError("DuckDB database files are not supported as ingestion sources")
    kinds: dict[str, SourceKind] = {
        ".csv": "csv",
        ".tsv": "tsv",
        ".tab": "tsv",
        ".parquet": "parquet",
        ".pq": "parquet",
        ".json": "json",
        ".jsonl": "json",
        ".ndjson": "json",
        ".xlsx": "excel",
        ".xlsm": "excel",
        ".xls": "excel",
        ".sqlite": "sqlite",
        ".sqlite3": "sqlite",
        ".db": "sqlite",
    }
    kind = kinds.get(suffix)
    if kind is None:
        supported = "csv, tsv, parquet, json, Excel, SQLite, HTTP URLs, and database URIs"
        raise IngestError(f"Unsupported source type for {location}. Supported types: {supported}")
    return _local_source(kind, location, name, options)


def public_source(location: str, **kwargs: Any) -> DataSource:
    if urlparse(location).scheme.lower() not in {"http", "https"}:
        raise IngestError("A public HTTP(S) URL is required")
    source = detect_source(location, **kwargs)
    if source.kind not in {"url", "gsheet"}:
        raise IngestError("A public HTTP(S) URL is required")
    return source


def load_source(source: DataSource, catalog: DataCatalog) -> LoadResult:
    if source.kind in {"csv", "tsv", "parquet", "json"}:
        from insightforge.ingest.files import load_file

        return load_file(source, catalog)
    if source.kind == "excel":
        from insightforge.ingest.excel import load_excel

        return load_excel(source, catalog)
    if source.kind == "url":
        from insightforge.ingest.url import load_url

        return load_url(source, catalog, source.options.get("dest_dir"))
    if source.kind == "gsheet":
        from insightforge.ingest.gsheets import load_gsheet

        return load_gsheet(source, catalog, source.options.get("dest_dir"))
    if source.kind in {"postgres", "mysql", "sqlite", "sqlalchemy", "mssql"}:
        from insightforge.ingest.database import load_database

        return load_database(source, catalog)
    raise IngestError(f"Unsupported source kind: {source.kind}")


def load_any(location: str, catalog: DataCatalog, **kwargs: Any) -> LoadResult:
    return load_source(detect_source(location, **kwargs), catalog)
