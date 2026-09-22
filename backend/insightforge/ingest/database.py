from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit

import pandas as pd

from insightforge.core.catalog import DataCatalog, sanitize_identifier
from insightforge.ingest.base import DataSource, IngestError, LoadResult


def _sqlite_path(uri: str) -> str:
    return uri[len("sqlite:///") :] if uri.lower().startswith("sqlite:///") else uri


def _mysql_connection(uri: str) -> str:
    parsed = urlsplit(uri)
    database = parsed.path.lstrip("/")
    values = {
        "host": parsed.hostname or "localhost",
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "port": str(parsed.port or 3306),
        "database": unquote(database),
    }
    return " ".join(f"{key}={value}" for key, value in values.items())


def parse_db_uri(uri: str) -> tuple[str, str]:
    lowered = uri.lower()
    if lowered.startswith("postgres://"):
        return "postgres", f"postgresql://{uri[len('postgres://') :]}"
    if lowered.startswith("postgresql://"):
        return "postgres", uri
    if lowered.startswith(("mysql://", "mysql+pymysql://")):
        return "mysql", _mysql_connection(uri)
    if lowered.startswith("sqlite:///"):
        return "sqlite", _sqlite_path(uri)
    if "://" in uri:
        return "sqlalchemy", uri
    if Path(uri).suffix.lower() in {".sqlite", ".sqlite3", ".db"}:
        return "sqlite", uri
    return "sqlalchemy", uri


def redact_uri(uri: str) -> str:
    if "://" not in uri:
        return uri
    parsed = urlsplit(uri)
    if parsed.password is None:
        return uri
    username = quote(unquote(parsed.username or ""), safe="")
    hostname = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    netloc = f"{username}:***@{hostname}{port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def _load_sqlalchemy(source: DataSource, catalog: DataCatalog, alias: str) -> LoadResult:
    try:
        from sqlalchemy import create_engine, inspect
        from sqlalchemy.exc import NoSuchModuleError

        engine = create_engine(source.location)
    except (ImportError, ModuleNotFoundError, NoSuchModuleError) as error:
        raise IngestError(
            f"Database driver unavailable for {redact_uri(source.location)}; "
            'pip install "insightforge[db]"'
        ) from error
    except Exception as error:
        raise IngestError(f"Could not open database {redact_uri(source.location)}") from error
    allow = set(source.options.get("tables") or [])
    max_rows = int(source.options.get("max_rows", 2_000_000))
    notes: list[str] = []
    loaded: list[str] = []
    try:
        names = inspect(engine).get_table_names()
        if allow:
            names = [name for name in names if name in allow]
        for table in names:
            chunks: list[pd.DataFrame] = []
            rows = 0
            capped = False
            for chunk in pd.read_sql_table(table, engine, chunksize=50_000):
                remaining = max_rows - rows
                if remaining <= 0:
                    capped = True
                    break
                chunks.append(chunk.head(remaining))
                rows += min(len(chunk), remaining)
                if len(chunk) > remaining or rows >= max_rows:
                    capped = True
                    break
            frame = (
                pd.concat(chunks, ignore_index=True)
                if chunks
                else pd.read_sql_table(table, engine).head(0)
            )
            name = f"{alias}__{sanitize_identifier(table)}"
            catalog.register_df(name, frame)
            loaded.append(name)
            if capped:
                notes.append(f"capped `{table}` at {max_rows} rows")
    except (ImportError, ModuleNotFoundError) as error:
        raise IngestError(
            f"Database driver unavailable for {redact_uri(source.location)}; "
            'pip install "insightforge[db]"'
        ) from error
    except Exception as error:
        raise IngestError(f"Could not load database {redact_uri(source.location)}") from error
    finally:
        engine.dispose()
    return LoadResult(tables=loaded, notes=notes)


def load_database(source: DataSource, catalog: DataCatalog) -> LoadResult:
    db_type, connection = parse_db_uri(source.location)
    default_alias = "sqlalchemy" if source.kind == "sqlalchemy" else db_type
    alias = sanitize_identifier(source.name or default_alias)
    if source.kind == "sqlalchemy" or db_type == "sqlalchemy":
        return _load_sqlalchemy(source, catalog, alias)
    if db_type == "sqlite" and not Path(connection).expanduser().exists():
        raise IngestError(f"SQLite database does not exist: {redact_uri(source.location)}")
    try:
        catalog.attach(alias, connection, db_type)
    except Exception as error:
        raise IngestError(f"Could not attach database {redact_uri(source.location)}") from error
    tables = [name for name in catalog.table_names() if name.startswith(f"{alias}.")]
    allow = set(source.options.get("tables") or [])
    display_uri = connection if db_type == "postgres" else source.location
    notes = [f"attached {db_type} as alias `{alias}` ({redact_uri(display_uri)})"]
    if allow:
        outside = [name for name in tables if name.split(".", 1)[1] not in allow]
        if outside:
            notes.append(f"attached tables outside allow-list: {', '.join(outside)}")
    return LoadResult(tables=tables, notes=notes)
