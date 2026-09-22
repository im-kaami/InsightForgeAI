from pathlib import Path

from insightforge.core.catalog import DataCatalog, sanitize_identifier
from insightforge.ingest.base import DataSource, IngestError, LoadResult, table_name_for


def _sql_path(path: str) -> str:
    return Path(path).resolve().as_posix().replace("'", "''")


def load_file(source: DataSource, catalog: DataCatalog) -> LoadResult:
    path = Path(source.location)
    if not path.exists():
        raise IngestError(f"Local source does not exist: {source.location}")
    escaped = _sql_path(source.location)
    readers = {
        "csv": f"read_csv_auto('{escaped}')",
        "tsv": f"read_csv('{escaped}', delim='\\t', header=true)",
        "parquet": f"read_parquet('{escaped}')",
        "json": f"read_json_auto('{escaped}')",
    }
    reader = readers.get(source.kind)
    if reader is None:
        raise IngestError(f"Unsupported file source kind: {source.kind}")
    name = sanitize_identifier(source.name) if source.name else table_name_for(source.location)
    try:
        catalog.create_table_from_query(name, f"SELECT * FROM {reader}")
    except Exception as error:
        raise IngestError(f"Could not load {path.name}: {error}") from error
    return LoadResult(tables=[name])
