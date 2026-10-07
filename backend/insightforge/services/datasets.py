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
from insightforge.core.catalog import DataCatalog, _quote, sanitize_identifier
from insightforge.core.profiling import profile_catalog
from insightforge.core.recipes import AppliedRecipe, RecipeError, SavedRecipe, applied, apply_recipe
from insightforge.core.validation import SavedRules, check_rules
from insightforge.db.models import Connection, Dataset, DatasetVersion, User
from insightforge.ingest import (
    DataSource,
    IngestError,
    detect_source,
    load_source,
    public_source,
    redact_uri,
)
from insightforge.ingest.database import check_database_target
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


RAW_CATALOG = "raw.duckdb"


def _validate(dataset: Dataset, catalog: DataCatalog) -> dict[str, Any]:
    rules = SavedRules.model_validate(dataset.rules_json or {})
    if not rules.rules:
        return {}
    report = check_rules(
        catalog, rules.rules, revision=rules.revision, timeout=get_settings().query_timeout_seconds
    )
    return report.model_dump(mode="json")


def _prepare_version(
    dataset: Dataset,
    version: DatasetVersion,
    version_dir: Path,
    *,
    recipe: SavedRecipe | None = None,
) -> None:
    """Apply the recipe to catalog.duckdb (keeping the import as raw.duckdb), then profile and validate.

    Call with every connection to the version catalog closed. An explicit recipe raises RecipeError on
    failure; the saved recipe applied automatically records the error and keeps the imported data.
    """
    path = version_dir / "catalog.duckdb"
    raw = version_dir / RAW_CATALOG
    saved = recipe or SavedRecipe.model_validate(dataset.recipe_json or {})
    version.recipe_json = {}
    if saved.steps and (recipe is not None or saved.auto_apply):
        if not raw.exists():
            shutil.copy2(path, raw)
        catalog: DataCatalog | None = DataCatalog(path)
        try:
            results = apply_recipe(catalog, saved.steps, timeout=get_settings().query_timeout_seconds)
            version.recipe_json = applied(saved.revision, saved.steps, results).model_dump(mode="json")
        except RecipeError as error:
            if recipe is not None:
                raise
            catalog.close()
            catalog = None
            shutil.copy2(raw, path)
            version.recipe_json = AppliedRecipe(
                revision=saved.revision,
                steps=saved.steps,
                error=str(error),
                failed_step=error.index,
                applied_at=datetime.now(UTC),
            ).model_dump(mode="json")
        finally:
            if catalog:
                catalog.close()
    catalog = DataCatalog(path, read_only=True)
    try:
        version.schema_json, version.profile_json = _profile_version(catalog)
        version.validation_json = _validate(dataset, catalog)
    finally:
        catalog.close()


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
        catalog.close()
        catalog = None
        _prepare_version(dataset, version, version_dir)
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
    from insightforge.services.metric_reports import on_new_version

    on_new_version(db, dataset.id)
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
        catalog.close()
        catalog = None
        _prepare_version(dataset, version, directory)
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
    db: Session,
    user: User,
    connection: Connection,
    name: str,
    tables: list[str] | None,
    schema: str | None = None,
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
                "options": {"tables": tables or [], **({"schema": schema} if schema else {})},
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
            current_raw = current_dir / RAW_CATALOG
            if current_raw.is_file():
                raw_copy = version_dir / RAW_CATALOG
                shutil.copy2(current_raw, raw_copy)
                escaped = raw_copy.as_posix().replace("'", "''")
                catalog.connection.execute(f"ATTACH '{escaped}' AS insightforge_raw")
                try:
                    for table in result.tables:
                        catalog.connection.execute(
                            f"CREATE TABLE insightforge_raw.{_quote(table)} AS SELECT * FROM {_quote(table)}"
                        )
                finally:
                    catalog.connection.execute("DETACH insightforge_raw")
            version.recipe_json = dict(current.recipe_json or {})
            fingerprint_path = Path(source.location) if Path(source.location).is_file() else None
            if fingerprint_path is None:
                added_downloads = set((version_dir / "downloads").iterdir()) - downloads_before
                fingerprint_path = next((path for path in added_downloads if path.is_file()), None)
            version.sources_json = [*current.sources_json, _source_metadata(source, fingerprint_path)]
            version.schema_json, version.profile_json = _profile_version(catalog)
            version.validation_json = _validate(dataset, catalog)
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


def _version_catalog_path(dataset: Dataset, version_id: str) -> Path:
    return (
        _storage().root
        / "users"
        / dataset.owner_id
        / "datasets"
        / dataset.id
        / "versions"
        / version_id
        / "catalog.duckdb"
    )


def reprofile_version(db: Session, dataset: Dataset, version: DatasetVersion) -> DatasetVersion:
    path = _version_catalog_path(dataset, version.id)
    if not path.is_file():
        raise IngestError("Dataset version files are missing")
    try:
        catalog = DataCatalog(path, read_only=True)
    except duckdb.Error as error:
        if "different configuration" in str(error).lower():
            raise DatasetBusyError("Dataset is currently busy") from error
        raise
    try:
        profile = profile_catalog(catalog, timeout_seconds=10, max_columns=100)
    finally:
        catalog.close()
    version.profile_json = profile.model_dump(mode="json")
    if dataset.current_version_id == version.id:
        dataset.profile_json = version.profile_json
    db.commit()
    db.refresh(version)
    return version


def save_recipe(db: Session, dataset: Dataset, steps: list[Any], auto_apply: bool) -> SavedRecipe:
    previous = SavedRecipe.model_validate(dataset.recipe_json or {})
    saved = SavedRecipe(
        steps=steps, auto_apply=auto_apply, revision=previous.revision + 1, updated_at=datetime.now(UTC)
    )
    dataset.recipe_json = saved.model_dump(mode="json")
    db.commit()
    db.refresh(dataset)
    return saved


def stage_recipe_version(db: Session, dataset: Dataset) -> DatasetVersion:
    """Create a draft from the current version's imported data with the saved recipe applied."""
    if dataset.connection_id:
        raise IngestError("Cleaning recipes need a dataset with versions")
    recipe = SavedRecipe.model_validate(dataset.recipe_json or {})
    if not recipe.steps:
        raise IngestError("Save at least one cleaning step first")
    current = ensure_current_version(db, dataset)
    if current is None:
        raise IngestError("Review and confirm the imported version first")
    storage = _storage()
    owner_id, dataset_id, current_id = dataset.owner_id, dataset.id, current.id
    current_dir = storage.version_dir(owner_id, dataset_id, current_id)
    base = current_dir / RAW_CATALOG
    if not base.is_file():
        if (current.recipe_json or {}).get("results"):
            raise IngestError("The imported data of the current version is missing")
        base = current_dir / "catalog.duckdb"
    if not base.is_file():
        raise IngestError("Dataset version files are missing")
    with _LOCKS[dataset.id]:
        version = DatasetVersion(
            dataset_id=dataset_id,
            owner_id=owner_id,
            base_version_id=current_id,
            state="draft",
            sources_json=list(current.sources_json),
        )
        db.add(version)
        db.flush()
        version_dir = storage.version_dir(owner_id, dataset_id, version.id)
        success = False
        try:
            shutil.copy2(base, version_dir / RAW_CATALOG)
            shutil.copy2(base, version_dir / "catalog.duckdb")
            for folder in ("uploads", "downloads"):
                if (current_dir / folder).is_dir():
                    shutil.copytree(current_dir / folder, version_dir / folder, dirs_exist_ok=True)
            try:
                _prepare_version(dataset, version, version_dir, recipe=recipe)
            except RecipeError as error:
                raise IngestError(str(error)) from error
            db.commit()
            db.refresh(version)
            success = True
            return version
        finally:
            if not success:
                db.rollback()
                if version_dir.exists():
                    shutil.rmtree(version_dir)


def save_rules(db: Session, dataset: Dataset, rules: list[Any]) -> SavedRules:
    previous = SavedRules.model_validate(dataset.rules_json or {})
    saved = SavedRules(rules=rules, revision=previous.revision + 1, updated_at=datetime.now(UTC))
    dataset.rules_json = saved.model_dump(mode="json")
    db.commit()
    db.refresh(dataset)
    return saved


def validate_version(db: Session, dataset: Dataset, version: DatasetVersion) -> DatasetVersion:
    path = _version_catalog_path(dataset, version.id)
    if not path.is_file():
        raise IngestError("Dataset version files are missing")
    try:
        catalog = DataCatalog(path, read_only=True)
    except duckdb.Error as error:
        if "different configuration" in str(error).lower():
            raise DatasetBusyError("Dataset is currently busy") from error
        raise
    try:
        version.validation_json = _validate(dataset, catalog)
    finally:
        catalog.close()
    db.commit()
    db.refresh(version)
    return version


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
        path = _version_catalog_path(dataset, selected)
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
        try:
            check_database_target(connection_uri)
        except IngestError:
            catalog.close()
            raise
        source_info = dataset.sources_json[0]
        source = DataSource(
            kind=source_info["kind"],
            location=connection_uri,
            name=sanitize_identifier(source_info.get("name") or source_info["kind"]),
            options=source_info.get("options") or {},
        )
        try:
            load_source(source, catalog)
            # Attached databases keep working; files, new attachments and extension installs do not.
            catalog.lock()
        except Exception:
            catalog.close()
            raise
    # DuckDB 1.4.5 cannot re-enable external access on a running file-backed database.
    return catalog
