import ipaddress
import os
import re
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit, urlunsplit

import pandas as pd

from insightforge.config import get_settings, private_databases_allowed
from insightforge.core.catalog import DataCatalog, sanitize_identifier
from insightforge.ingest import netguard
from insightforge.ingest.base import DataSource, IngestError, LoadResult
from insightforge.ingest.netguard import check_public_host


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
    for key, value in values.items():
        # The connection string is "key=value key=value", so a value with whitespace could add settings.
        if any(char.isspace() or ord(char) < 32 for char in value):
            raise IngestError(f"The MySQL {key} may not contain spaces or control characters")
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
    if lowered.startswith(("mssql://", "sqlserver://", "mssql+pymssql://")):
        return "mssql", f"mssql+pymssql://{uri.split('://', 1)[1]}"
    if lowered.startswith("snowflake://"):
        return "snowflake", uri
    if "://" in uri:
        return "sqlalchemy", uri
    if Path(uri).suffix.lower() in {".sqlite", ".sqlite3", ".db"}:
        return "sqlite", uri
    return "sqlalchemy", uri


SQLITE_OFF = "SQLite file connections are turned off on this server (ALLOW_SQLITE_FILES)"
PRIVATE_OFF = (
    "Connections to private or local network addresses are turned off on this server "
    "(ALLOW_PRIVATE_DATABASES)"
)


def _is_sqlite(uri: str) -> bool:
    lowered = uri.lower()
    if lowered.startswith("sqlite"):
        return True
    return "://" not in uri and Path(uri).suffix.lower() in {".sqlite", ".sqlite3", ".db"}


def _sqlite_file(uri: str) -> str:
    if "://" not in uri:
        return uri
    rest = uri.split("://", 1)[1].split("?", 1)[0]
    return unquote(rest[1:]) if rest.startswith("/") else ""


def _inside(path: str, directory: str) -> bool:
    try:
        return os.path.commonpath([os.path.normcase(path), os.path.normcase(directory)]) == os.path.normcase(
            directory
        )
    except ValueError:
        return False


def _check_sqlite_file(uri: str) -> None:
    settings = get_settings()
    if not settings.allow_sqlite_files:
        raise IngestError(SQLITE_OFF)
    raw = _sqlite_file(uri)
    if not raw:
        raise IngestError("SQLite file does not exist")
    path = Path(raw).expanduser().resolve()
    if not path.is_file():
        raise IngestError("SQLite file does not exist")
    resolved = str(path)
    if settings.database_url.lower().startswith("sqlite"):
        app_database = Path(_sqlite_file(settings.database_url)).expanduser().resolve()
        if os.path.normcase(resolved) == os.path.normcase(str(app_database)):
            raise IngestError("That SQLite file is the application's own database")
    if _inside(resolved, str(Path(settings.storage_dir).expanduser().resolve())):
        raise IngestError("That SQLite file is inside the application's storage")


_ACCOUNT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,254}$")


def _check_snowflake(uri: str) -> None:
    account = urlsplit(uri).hostname or ""
    if not _ACCOUNT.match(account):
        raise IngestError("Enter the Snowflake account identifier, for example ft45233.eu-central-2.aws")
    if not private_databases_allowed(get_settings()):
        check_public_host(f"{account}.snowflakecomputing.com", PRIVATE_OFF)


def _check_database_hosts(uri: str) -> None:
    if private_databases_allowed(get_settings()):
        return
    if "://" not in uri:
        raise IngestError(PRIVATE_OFF)
    parsed = urlsplit(uri)
    hosts = [parsed.hostname or ""]
    for key in ("host", "hostaddr"):
        for value in parse_qs(parsed.query).get(key, []):
            hosts.extend(part for part in value.split(","))
    hosts = [host.strip() for host in hosts]
    if not hosts or not all(hosts):
        raise IngestError(PRIVATE_OFF)
    for host in hosts:
        check_public_host(host, PRIVATE_OFF)


def pin_postgres_host(connection: str) -> str:
    """Add libpq's ``hostaddr`` (the address that was just checked) so the name cannot be re-resolved."""
    if private_databases_allowed(get_settings()) or "://" not in connection:
        return connection
    parts = urlsplit(connection)
    host = parts.hostname or ""
    query = parse_qs(parts.query)
    if not host or "hostaddr" in query:
        return connection
    try:
        ipaddress.ip_address(host)
        return connection
    except ValueError:
        pass
    addresses = netguard.resolve_host(host.lower().rstrip("."))
    check_public_host(host, PRIVATE_OFF, lambda _name: addresses)
    separator = "&" if parts.query else ""
    return urlunsplit(parts._replace(query=f"{parts.query}{separator}hostaddr={addresses[0]}"))


def check_database_target(uri: str) -> None:
    """Refuse database connections this server is not set up to allow (files, private hosts)."""
    if _is_sqlite(uri):
        _check_sqlite_file(uri)
    elif uri.lower().startswith("snowflake://"):
        _check_snowflake(uri)
    else:
        _check_database_hosts(uri)


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


def _with_snowflake_schema(uri: str, schema: str | None) -> str:
    """Snowflake takes database and schema from the address path; ``schema`` replaces the schema part."""
    if not schema:
        return uri
    parts = urlsplit(uri)
    database = parts.path.strip("/").split("/")[0]
    if not database:
        raise IngestError("Name a database in the address to choose a schema")
    return urlunsplit((parts.scheme, parts.netloc, f"/{database}/{schema}", parts.query, parts.fragment))


def _reason(error: Exception, uri: str, db_type: str) -> str:
    """A short reason from the database driver, for Snowflake only, with the password removed."""
    if db_type != "snowflake":
        return ""
    text = " ".join(str(error).split())[:240]
    password = urlsplit(uri).password or ""
    for secret in {password, unquote(password)} - {""}:
        text = text.replace(secret, "***")
    return f": {text}" if text else ""


def _driver_missing(location: str) -> IngestError:
    return IngestError(
        f"Database driver unavailable for {redact_uri(location)}; " 'pip install "insightforge[db]"'
    )


def _engine(location: str):
    try:
        from sqlalchemy import create_engine
        from sqlalchemy.exc import NoSuchModuleError

        return create_engine(location)
    except (ImportError, ModuleNotFoundError, NoSuchModuleError) as error:
        raise _driver_missing(location) from error
    except Exception as error:
        raise IngestError(f"Could not open database {redact_uri(location)}") from error


def list_tables(uri: str, schema: str | None = None) -> list[str]:
    """Names of the tables a connection can see (in ``schema`` if given), without copying any data."""
    check_database_target(uri)
    db_type, connection = parse_db_uri(uri)
    if db_type == "postgres":
        connection = pin_postgres_host(connection)
    if db_type in {"mssql", "sqlalchemy", "snowflake"}:
        from sqlalchemy import inspect

        if db_type == "snowflake":
            connection, schema = _with_snowflake_schema(connection, schema), None
        engine = _engine(connection)
        try:
            return sorted(inspect(engine).get_table_names(schema=schema))
        except (ImportError, ModuleNotFoundError) as error:
            raise _driver_missing(uri) from error
        except Exception as error:
            raise IngestError(
                f"Could not read tables from {redact_uri(uri)}{_reason(error, uri, db_type)}"
            ) from error
        finally:
            engine.dispose()
    catalog = DataCatalog()
    try:
        if db_type == "sqlite" and not Path(connection).expanduser().exists():
            raise IngestError(f"SQLite database does not exist: {redact_uri(uri)}")
        try:
            catalog.attach("probe", connection, db_type, schema=schema)
        except Exception as error:
            raise IngestError(f"Could not attach database {redact_uri(uri)}") from error
        return sorted(name.split(".", 1)[1] for name in catalog.table_names() if name.startswith("probe."))
    finally:
        catalog.close()


def _load_sqlalchemy(
    source: DataSource, catalog: DataCatalog, alias: str, location: str | None = None
) -> LoadResult:
    location = location or source.location
    schema = source.options.get("schema") or None
    if source.kind == "snowflake" or location.lower().startswith("snowflake://"):
        location, schema = _with_snowflake_schema(location, schema), None
    try:
        from sqlalchemy import inspect

        engine = _engine(location)
    except ImportError as error:
        raise _driver_missing(location) from error
    allow = set(source.options.get("tables") or [])
    max_rows = int(source.options.get("max_rows", 2_000_000))
    notes: list[str] = []
    loaded: list[str] = []
    try:
        names = inspect(engine).get_table_names(schema=schema)
        if allow:
            names = [name for name in names if name in allow]
        for table in names:
            chunks: list[pd.DataFrame] = []
            rows = 0
            capped = False
            for chunk in pd.read_sql_table(table, engine, schema=schema, chunksize=50_000):
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
                else pd.read_sql_table(table, engine, schema=schema).head(0)
            )
            name = f"{alias}__{sanitize_identifier(table)}"
            catalog.register_df(name, frame)
            loaded.append(name)
            if capped:
                notes.append(f"capped `{table}` at {max_rows} rows")
    except (ImportError, ModuleNotFoundError) as error:
        raise _driver_missing(location) from error
    except Exception as error:
        raise IngestError(f"Could not load database {redact_uri(location)}") from error
    finally:
        engine.dispose()
    return LoadResult(tables=loaded, notes=notes)


def load_database(source: DataSource, catalog: DataCatalog) -> LoadResult:
    db_type, connection = parse_db_uri(source.location)
    if db_type == "postgres":
        connection = pin_postgres_host(connection)
    default_alias = "sqlalchemy" if source.kind == "sqlalchemy" else db_type
    alias = sanitize_identifier(source.name or default_alias)
    if source.kind in {"sqlalchemy", "mssql", "snowflake"} or db_type in {
        "sqlalchemy",
        "mssql",
        "snowflake",
    }:
        return _load_sqlalchemy(source, catalog, alias, connection)
    if db_type == "sqlite" and not Path(connection).expanduser().exists():
        raise IngestError(f"SQLite database does not exist: {redact_uri(source.location)}")
    try:
        catalog.attach(
            alias,
            connection,
            db_type,
            schema=source.options.get("schema") or None,
            allow=set(source.options.get("tables") or []),
        )
    except Exception as error:
        raise IngestError(f"Could not attach database {redact_uri(source.location)}") from error
    tables = [name for name in catalog.table_names() if name.startswith(f"{alias}.")]
    allow = set(source.options.get("tables") or [])
    display_uri = connection if db_type == "postgres" else source.location
    notes = [f"attached {db_type} as alias `{alias}` ({redact_uri(display_uri)})"]
    if allow:
        notes.append("only the tables in the allow-list are shown; the database itself stays attached")
    return LoadResult(tables=tables, notes=notes)
