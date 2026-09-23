from pathlib import Path
from typing import Any

import duckdb
from sqlalchemy.orm import Session

from insightforge.config import get_settings
from insightforge.core.catalog import DataCatalog, sanitize_identifier
from insightforge.db.models import Connection, Dataset, User
from insightforge.ingest import DataSource, detect_source, load_source, redact_uri
from insightforge.services.storage import Storage


class DatasetBusyError(RuntimeError):
    pass


def _storage() -> Storage:
    return Storage(get_settings().storage_dir)


def refresh_schema(dataset: Dataset, catalog: DataCatalog) -> None:
    schema = catalog.introspect()
    dataset.schema_json = schema.model_dump(mode="json")
    dataset.tables_json = catalog.table_names()


def create_dataset_from_files(
    db: Session, user: User, files: list[tuple[str, Path]], name: str | None
) -> Dataset:
    dataset = Dataset(owner_id=user.id, name=name or Path(files[0][0]).stem, kind="files")
    db.add(dataset)
    db.flush()
    storage = _storage()
    directory = storage.dataset_dir(user.id, dataset.id)
    catalog = DataCatalog(directory / "catalog.duckdb")
    sources = []
    try:
        for filename, temp_path in files:
            safe_name = Path(filename).name
            path = storage.save_upload_path(user.id, dataset.id, safe_name, temp_path)
            source = detect_source(str(path))
            load_source(source, catalog)
            sources.append({"kind": source.kind, "location": safe_name, "name": source.name, "options": {}})
        dataset.sources_json = sources
        refresh_schema(dataset, catalog)
        db.commit()
        db.refresh(dataset)
        return dataset
    finally:
        catalog.close()


def create_dataset_from_url(
    db: Session, user: User, url: str, name: str | None, options: dict[str, Any]
) -> Dataset:
    source = detect_source(url, options=options)
    dataset = Dataset(owner_id=user.id, name=name or "Downloaded data", kind="url")
    db.add(dataset)
    db.flush()
    directory = _storage().dataset_dir(user.id, dataset.id)
    source.options["dest_dir"] = directory / "downloads"
    catalog = DataCatalog(directory / "catalog.duckdb")
    try:
        load_source(source, catalog)
        display_options = {key: value for key, value in options.items() if key != "client"}
        dataset.sources_json = [
            {"kind": source.kind, "location": url, "name": source.name, "options": display_options}
        ]
        refresh_schema(dataset, catalog)
        db.commit()
        db.refresh(dataset)
        return dataset
    finally:
        catalog.close()


def create_dataset_from_connection(
    db: Session, user: User, connection: Connection, name: str, tables: list[str] | None
) -> Dataset:
    from insightforge.services.crypto import decrypt

    dataset = Dataset(
        owner_id=user.id,
        name=name,
        kind="connection",
        connection_id=connection.id,
        sources_json=[
            {
                "kind": connection.kind,
                "location": redact_uri(decrypt(connection.encrypted_uri)),
                "name": connection.name,
                "options": {"tables": tables or []},
            }
        ],
    )
    db.add(dataset)
    db.flush()
    catalog = open_catalog(dataset, decrypt(connection.encrypted_uri), for_run=False)
    try:
        refresh_schema(dataset, catalog)
        db.commit()
        db.refresh(dataset)
        return dataset
    finally:
        catalog.close()


def add_source(
    db: Session,
    dataset: Dataset,
    source: DataSource,
    connection_uri: str | None = None,
) -> Dataset:
    directory = _storage().dataset_dir(dataset.owner_id, dataset.id)
    source.options.setdefault("dest_dir", directory / "downloads")
    catalog = open_catalog(dataset, connection_uri, for_run=False)
    try:
        load_source(source, catalog)
        dataset.sources_json = [
            *dataset.sources_json,
            {
                "kind": source.kind,
                "location": source.location,
                "name": source.name,
                "options": {key: value for key, value in source.options.items() if key != "dest_dir"},
            },
        ]
        refresh_schema(dataset, catalog)
        db.commit()
        return dataset
    finally:
        catalog.close()


def open_catalog(
    dataset: Dataset, connection_uri: str | None = None, for_run: bool = False
) -> DataCatalog:
    del for_run
    path = _storage().dataset_dir(dataset.owner_id, dataset.id) / "catalog.duckdb"
    settings = get_settings()
    try:
        catalog = DataCatalog(
            path,
            memory_limit=settings.duckdb_memory_limit,
            threads=settings.duckdb_threads,
        )
    except duckdb.Error as error:
        if "different configuration" in str(error).lower():
            raise DatasetBusyError("Dataset is currently busy") from error
        raise
    if connection_uri:
        source_info = dataset.sources_json[0]
        source = DataSource(
            kind=source_info["kind"],
            location=connection_uri,
            name=sanitize_identifier(source_info.get("name") or source_info["kind"]),
            options=source_info.get("options") or {},
        )
        load_source(source, catalog)
    # DuckDB 1.4.5 cannot re-enable external access on a running file-backed database.
    return catalog
