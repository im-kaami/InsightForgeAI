import hashlib
import shutil
import threading
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import duckdb
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from insightforge.config import get_settings
from insightforge.core.catalog import DataCatalog, sanitize_identifier
from insightforge.core.profiling import profile_catalog
from insightforge.db.models import Connection, Dataset, DatasetVersion, User
from insightforge.ingest import (
    DataSource,
    IngestError,
    detect_source,
    load_source,
    public_source,
    redact_uri,
)
from insightforge.services.storage import Storage

if TYPE_CHECKING:
    from insightforge.api.schemas import ImportOptions


class DatasetBusyError(RuntimeError):
    pass


_LOCKS: defaultdict[str, threading.RLock] = defaultdict(threading.RLock)


def _storage() -> Storage:
    return Storage(get_settings().storage_dir)


def _fingerprint(path: Path) -> tuple[str, int]:
    with path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    return digest, path.stat().st_size


def _source_metadata(source: DataSource, path: Path | None = None) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "kind": source.kind,
        "location": (
            source.location
            if source.kind in {"url", "gsheet"}
            else source.options.get("original_filename", Path(source.location).name)
        ),
        "name": source.name,
        "options": {
            key: value
            for key, value in source.options.items()
            if key not in {"client", "dest_dir", "allow_private", "original_filename"}
        },
    }
    if path and path.is_file():
        metadata["sha256"], metadata["size_bytes"] = _fingerprint(path)
    return metadata


def refresh_schema(dataset: Dataset, catalog: DataCatalog) -> None:
    schema = catalog.introspect()
    dataset.schema_json = schema.model_dump(mode="json")
    dataset.tables_json = catalog.table_names()


def _profile_version(catalog: DataCatalog) -> tuple[dict[str, Any], dict[str, Any]]:
    schema = catalog.introspect().model_dump(mode="json")
    profile = profile_catalog(catalog, timeout_seconds=10, max_columns=100).model_dump(mode="json")
    return schema, profile


def _option_source(filename: str, path: Path, option: "ImportOptions") -> DataSource:
    if option.table_name is not None and not any(character.isalnum() for character in option.table_name):
        raise IngestError("Table name must contain a letter or number")
    source = detect_source(str(path), name=option.table_name)
    if source.kind not in {"csv", "tsv", "parquet", "json", "excel"}:
        raise IngestError("Only CSV, TSV, Parquet, JSON and Excel uploads are supported")
    if source.kind in {"parquet", "json"} and (
        option.header_row != 1 or option.text_columns
    ):
        raise IngestError(f"Custom header and text-column options are not supported for {source.kind}")
    source.options = {
        "header": option.header_row - 1,
        "text_columns": option.text_columns,
        "sheets": option.sheets,
        "preserve_rows": True,
    }
    source.location = str(path)
    return source


def ensure_current_version(db: Session, dataset: Dataset) -> DatasetVersion | None:
    if dataset.connection_id or dataset.kind == "connection":
        return None
    if dataset.current_version_id:
        return db.scalar(
            select(DatasetVersion).where(
                DatasetVersion.id == dataset.current_version_id,
                DatasetVersion.dataset_id == dataset.id,
                DatasetVersion.owner_id == dataset.owner_id,
            )
        )
    with _LOCKS[dataset.id]:
        db.refresh(dataset)
        if dataset.current_version_id:
            return db.get(DatasetVersion, dataset.current_version_id)
        storage = _storage()
        owner_id, dataset_id = dataset.owner_id, dataset.id
        legacy_dir = storage.dataset_dir(owner_id, dataset_id)
        legacy_catalog = legacy_dir / "catalog.duckdb"
        wal = Path(f"{legacy_catalog}.wal")
        if not legacy_catalog.is_file():
            return None
        if wal.exists() and wal.stat().st_size:
            raise DatasetBusyError("Dataset is being written; retry after the write completes")
        version = DatasetVersion(
            dataset_id=dataset_id,
            owner_id=owner_id,
            state="ready",
            sources_json=dataset.sources_json,
            schema_json=dataset.schema_json or {"tables": []},
            profile_json={},
            confirmed_at=datetime.now(UTC),
        )
        db.add(version)
        db.flush()
        version_id = version.id
        version_dir = storage.version_dir(owner_id, dataset_id, version_id)
        catalog: DataCatalog | None = None
        success = False
        try:
            shutil.copy2(legacy_catalog, version_dir / "catalog.duckdb")
            for folder in ("uploads", "downloads"):
                source_dir = legacy_dir / folder
                if source_dir.is_dir():
                    shutil.copytree(source_dir, version_dir / folder, dirs_exist_ok=True)
            legacy_sources = []
            for item in dataset.sources_json:
                location = str(item.get("location", ""))
                public_location = (
                    location
                    if urlparse(location).scheme.lower() in {"http", "https"}
                    else Path(location).name
                )
                source = DataSource(
                    kind=item["kind"],
                    location=public_location,
                    name=item.get("name"),
                    options=item.get("options") or {},
                )
                original = None
                if urlparse(location).scheme.lower() not in {"http", "https"}:
                    for folder in ("uploads", "downloads"):
                        candidate = version_dir / folder / Path(location).name
                        if candidate.is_file():
                            original = candidate
                            break
                legacy_sources.append(_source_metadata(source, original))
            version.sources_json = legacy_sources
            catalog = DataCatalog(version_dir / "catalog.duckdb", read_only=True)
            schema_json, profile_json = _profile_version(catalog)
            version.schema_json = schema_json
            version.profile_json = profile_json
            dataset.current_version_id = version_id
            dataset.schema_json = schema_json
            dataset.profile_json = profile_json
            dataset.sources_json = legacy_sources
            dataset.tables_json = [table["name"] for table in schema_json["tables"]]
            db.commit()
            db.refresh(dataset)
            success = True
            return version
        finally:
            if catalog:
                catalog.close()
            if not success:
                db.rollback()
                if version_dir.exists():
                    shutil.rmtree(version_dir)


def stage_files(
    db: Session,
    user: User,
    files: list[tuple[str, Path]],
    name: str | None,
    dataset: Dataset | None = None,
    options: list["ImportOptions"] | None = None,
) -> tuple[Dataset, DatasetVersion]:
    from insightforge.api.schemas import ImportOptions

    if not files or len(files) > 10:
        raise IngestError("Choose between 1 and 10 files")
    basenames = [Path(filename).name for filename, _ in files]
    if len({item.casefold() for item in basenames}) != len(basenames):
        raise IngestError("Duplicate filenames are not allowed")
    if options is not None and len(options) != len(files):
        raise IngestError("Import options must match the uploaded files")
    options = options or [ImportOptions() for _ in files]
    if sum(path.stat().st_size for _, path in files) > get_settings().max_upload_bytes:
        raise IngestError("Combined upload is too large")
    created_dataset = dataset is None
    if dataset is None:
        dataset = Dataset(owner_id=user.id, name=name or Path(files[0][0]).stem, kind="files")
        db.add(dataset)
        db.flush()
    elif dataset.owner_id != user.id or dataset.connection_id:
        raise IngestError("Only owned materialized datasets can be versioned")
    ensure_current_version(db, dataset)
    version = DatasetVersion(
        dataset_id=dataset.id,
        owner_id=user.id,
        base_version_id=dataset.current_version_id,
        state="draft",
    )
    db.add(version)
    db.flush()
    storage = _storage()
    owner_id, dataset_id, version_id = user.id, dataset.id, version.id
    dataset_dir = storage.root / "users" / owner_id / "datasets" / dataset_id
    version_dir = storage.version_dir(owner_id, dataset_id, version_id)
    catalog: DataCatalog | None = None
    sources: list[dict[str, Any]] = []
    loaded_tables: set[str] = set()
    success = False
    try:
        catalog = DataCatalog(version_dir / "catalog.duckdb")
        for (filename, temp_path), option in zip(files, options, strict=True):
            safe_name = Path(filename).name
            upload = storage.save_upload_path(owner_id, dataset_id, safe_name, temp_path, version_id)
            source = _option_source(safe_name, upload, option)
            before = set(catalog.table_names())
            result = load_source(source, catalog)
            if before.intersection(result.tables) or loaded_tables.intersection(result.tables):
                raise IngestError("Uploaded files would create duplicate table names")
            loaded_tables.update(result.tables)
            sources.append(_source_metadata(source, upload))
        version.schema_json, version.profile_json = _profile_version(catalog)
        version.sources_json = sources
        db.commit()
        db.refresh(version)
        success = True
        return dataset, version
    finally:
        if catalog:
            catalog.close()
        if not success:
            db.rollback()
            if version_dir.exists():
                shutil.rmtree(version_dir)
            if created_dataset and dataset_dir.exists():
                shutil.rmtree(dataset_dir)


def activate_version(
    db: Session,
    dataset: Dataset,
    version: DatasetVersion,
    expected_current_version_id: str | None,
) -> Dataset:
    if version.dataset_id != dataset.id or version.owner_id != dataset.owner_id:
        raise DatasetBusyError("Version does not belong to this dataset")
    with _LOCKS[dataset.id]:
        db.refresh(dataset)
        if version.state == "ready" and dataset.current_version_id == version.id:
            return dataset
        if version.state != "draft":
            raise DatasetBusyError("Historical versions cannot replace the active version")
        if (
            version.base_version_id != expected_current_version_id
            or dataset.current_version_id != expected_current_version_id
        ):
            raise DatasetBusyError("Dataset changed since preview; review the latest version")
        statement = (
            update(Dataset)
            .where(
                Dataset.id == dataset.id,
                Dataset.current_version_id == expected_current_version_id,
            )
            .values(
                current_version_id=version.id,
                schema_json=version.schema_json,
                profile_json=version.profile_json,
                sources_json=version.sources_json,
                tables_json=[table["name"] for table in version.schema_json["tables"]],
            )
        )
        if db.execute(statement).rowcount != 1:
            raise DatasetBusyError("Dataset changed since preview; review the latest version")
        version.state = "ready"
        version.confirmed_at = datetime.now(UTC)
        db.commit()
        db.refresh(dataset)
        return dataset


def list_versions(db: Session, dataset: Dataset) -> list[DatasetVersion]:
    ensure_current_version(db, dataset)
    return list(
        db.scalars(
            select(DatasetVersion)
            .where(
                DatasetVersion.dataset_id == dataset.id,
                DatasetVersion.owner_id == dataset.owner_id,
            )
            .order_by(DatasetVersion.created_at.desc())
        )
    )


def create_dataset_from_files(
    db: Session,
    user: User,
    files: list[tuple[str, Path]],
    name: str | None,
) -> Dataset:
    dataset, version = stage_files(db, user, files, name)
    return activate_version(db, dataset, version, None)


def _stage_url(
    db: Session,
    user: User,
    source: DataSource,
    dataset: Dataset,
    *,
    cleanup_parent: bool = False,
) -> DatasetVersion:
    version = DatasetVersion(
        dataset_id=dataset.id,
        owner_id=user.id,
        base_version_id=dataset.current_version_id,
        state="draft",
    )
    db.add(version)
    db.flush()
    owner_id, dataset_id, version_id = user.id, dataset.id, version.id
    storage = _storage()
    dataset_dir = storage.root / "users" / owner_id / "datasets" / dataset_id
    directory = storage.version_dir(owner_id, dataset_id, version_id)
    source.options["dest_dir"] = directory / "downloads"
    source.options.pop("allow_private", None)
    catalog: DataCatalog | None = None
    success = False
    try:
        catalog = DataCatalog(directory / "catalog.duckdb")
        load_source(source, catalog)
        downloaded = next((path for path in (directory / "downloads").iterdir() if path.is_file()), None)
        version.sources_json = [_source_metadata(source, downloaded)]
        version.schema_json, version.profile_json = _profile_version(catalog)
        db.commit()
        db.refresh(version)
        success = True
        return version
    finally:
        if catalog:
            catalog.close()
        if not success:
            db.rollback()
            if directory.exists():
                shutil.rmtree(directory)
            if cleanup_parent and dataset_dir.exists():
                shutil.rmtree(dataset_dir)


def create_dataset_from_url(
    db: Session, user: User, url: str, name: str | None, options: dict[str, Any]
) -> Dataset:
    source = public_source(url, options=options)
    dataset = Dataset(owner_id=user.id, name=name or "Downloaded data", kind="url")
    db.add(dataset)
    db.flush()
    version = _stage_url(db, user, source, dataset, cleanup_parent=True)
    return activate_version(db, dataset, version, None)


def refresh_url_dataset(db: Session, user: User, dataset: Dataset) -> DatasetVersion:
    if dataset.kind != "url" or dataset.connection_id:
        raise IngestError("Refresh is available only for URL and Google Sheets datasets")
    if not dataset.sources_json or any(
        source.get("kind") not in {"url", "gsheet"} for source in dataset.sources_json
    ):
        raise IngestError("Mixed uploaded and URL sources cannot be refreshed automatically")
    ensure_current_version(db, dataset)
    if len(dataset.sources_json) != 1:
        raise IngestError("Refresh currently requires one approved public URL source")
    stored = dataset.sources_json[0]
    source = public_source(
        stored["location"],
        name=stored.get("name"),
        options={
            key: value
            for key, value in (stored.get("options") or {}).items()
            if key != "allow_private"
        },
    )
    return _stage_url(db, user, source, dataset)


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
    if source.kind not in {"csv", "tsv", "parquet", "json", "excel", "url", "gsheet"}:
        raise IngestError("Only file uploads and public HTTP(S) URLs can be added")
    if dataset.connection_id:
        directory = _storage().dataset_dir(dataset.owner_id, dataset.id)
        source.options.setdefault("dest_dir", directory / "downloads")
        catalog = open_catalog(dataset, connection_uri, for_run=False)
        try:
            load_source(source, catalog)
            dataset.sources_json = [*dataset.sources_json, _source_metadata(source)]
            refresh_schema(dataset, catalog)
            db.commit()
            return dataset
        finally:
            catalog.close()
    current = ensure_current_version(db, dataset)
    if current is None:
        raise IngestError("Review and confirm the imported version first")
    with _LOCKS[dataset.id]:
        owner_id, dataset_id, current_id = dataset.owner_id, dataset.id, current.id
        version = DatasetVersion(
            dataset_id=dataset_id,
            owner_id=owner_id,
            base_version_id=current_id,
            state="draft",
            sources_json=list(current.sources_json),
        )
        db.add(version)
        db.flush()
        version_id = version.id
        storage = _storage()
        current_dir = storage.version_dir(owner_id, dataset_id, current_id)
        version_dir = storage.version_dir(owner_id, dataset_id, version_id)
        catalog: DataCatalog | None = None
        success = False
        try:
            shutil.copy2(current_dir / "catalog.duckdb", version_dir / "catalog.duckdb")
            shutil.copytree(current_dir / "uploads", version_dir / "uploads", dirs_exist_ok=True)
            shutil.copytree(current_dir / "downloads", version_dir / "downloads", dirs_exist_ok=True)
            catalog = DataCatalog(version_dir / "catalog.duckdb")
            before = set(catalog.table_names())
            if source.kind in {"csv", "tsv", "parquet", "json", "excel"}:
                original = Path(source.location)
                target = version_dir / "uploads" / source.options.get(
                    "original_filename", original.name
                )
                shutil.copy2(original, target)
                source.location = str(target)
            else:
                source.options["dest_dir"] = version_dir / "downloads"
                source.options.pop("allow_private", None)
            downloads_before = set((version_dir / "downloads").iterdir())
            result = load_source(source, catalog)
            if before.intersection(result.tables):
                raise IngestError("Source would overwrite an existing table")
            fingerprint_path = Path(source.location) if Path(source.location).is_file() else None
            if fingerprint_path is None:
                added_downloads = set((version_dir / "downloads").iterdir()) - downloads_before
                fingerprint_path = next((path for path in added_downloads if path.is_file()), None)
            version.sources_json = [*current.sources_json, _source_metadata(source, fingerprint_path)]
            version.schema_json, version.profile_json = _profile_version(catalog)
            db.flush()
            if catalog:
                catalog.close()
                catalog = None
            result_dataset = activate_version(db, dataset, version, current_id)
            success = True
            return result_dataset
        finally:
            if catalog:
                catalog.close()
            if not success:
                db.rollback()
                if version_dir.exists():
                    shutil.rmtree(version_dir)


def open_catalog(
    dataset: Dataset,
    connection_uri: str | None = None,
    for_run: bool = False,
    version_id: str | None = None,
) -> DataCatalog:
    settings = get_settings()
    if dataset.connection_id:
        path = _storage().dataset_dir(dataset.owner_id, dataset.id) / "catalog.duckdb"
        read_only = False
    else:
        selected = version_id or dataset.current_version_id
        if not selected:
            raise IngestError("Review and confirm the imported version first")
        path = (
            _storage().root
            / "users"
            / dataset.owner_id
            / "datasets"
            / dataset.id
            / "versions"
            / selected
            / "catalog.duckdb"
        )
        if not path.is_file():
            raise IngestError("Dataset version files are missing")
        read_only = True
    try:
        catalog = DataCatalog(
            path,
            memory_limit=settings.duckdb_memory_limit,
            threads=settings.duckdb_threads,
            read_only=read_only,
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
