import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import delete, func, select
from starlette.datastructures import UploadFile as StarletteUploadFile

from insightforge.api.deps import CurrentUser, Db, StorageDep
from insightforge.api.schemas import (
    ConnectionDatasetCreate,
    DatasetOut,
    ImportOptions,
    PrivacyUpdate,
    URLDatasetCreate,
    VersionConfirm,
    VersionOut,
)
from insightforge.config import get_settings
from insightforge.core.profiling import DataProfile
from insightforge.core.schema import DatasetNotes, SchemaInfo
from insightforge.core.sql_guard import guard_sql
from insightforge.db.models import (
    ChatSession,
    Connection,
    Dataset,
    DatasetVersion,
    ReportDefinition,
    Run,
    Schedule,
    User,
)
from insightforge.ingest import IngestError, detect_source, parse_db_uri, public_source
from insightforge.services.crypto import decrypt, encrypt
from insightforge.services.datasets import (
    activate_version,
    add_source,
    create_dataset_from_connection,
    create_dataset_from_url,
    ensure_current_version,
    list_versions,
    open_catalog,
    refresh_url_dataset,
    reprofile_version,
    stage_files,
)

router = APIRouter(prefix="/datasets", tags=["datasets"])
_OPTIONS = TypeAdapter(list[ImportOptions])


async def _stream_upload(file: UploadFile, storage: StorageDep) -> Path:
    max_bytes = get_settings().max_upload_bytes
    total = 0
    path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=storage.temp_dir(), delete=False) as output:
            path = Path(output.name)
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(413, "File too large")
                output.write(chunk)
        return path
    except Exception:
        if path:
            path.unlink(missing_ok=True)
        raise


def _options(value: str | None, count: int) -> list[ImportOptions] | None:
    if value is None:
        return None
    try:
        parsed = _OPTIONS.validate_json(value)
    except ValidationError as error:
        raise HTTPException(422, "Invalid import options") from error
    if len(parsed) != count:
        raise HTTPException(422, "Import options must match the uploaded files")
    return parsed


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def output(dataset: Dataset, review_version_id: str | None = None) -> DatasetOut:
    return DatasetOut(
        id=dataset.id,
        name=dataset.name,
        kind=dataset.kind,
        tables=dataset.tables_json,
        schema=SchemaInfo.model_validate(dataset.schema_json or {"tables": []}),
        sources=dataset.sources_json,
        current_version_id=dataset.current_version_id,
        llm_policy=dataset.llm_policy,
        notes=DatasetNotes.model_validate(dataset.notes_json or {}),
        profile=DataProfile.model_validate(dataset.profile_json) if dataset.profile_json else None,
        review_version_id=review_version_id,
        created_at=dataset.created_at,
    )


def version_output(version: DatasetVersion) -> VersionOut:
    return VersionOut(
        id=version.id,
        dataset_id=version.dataset_id,
        base_version_id=version.base_version_id,
        state=version.state,
        sources=version.sources_json,
        schema=SchemaInfo.model_validate(version.schema_json),
        profile=DataProfile.model_validate(version.profile_json),
        created_at=_utc(version.created_at),
        confirmed_at=_utc(version.confirmed_at),
    )


def owned(db: Db, user: User, dataset_id: str) -> Dataset:
    dataset = db.scalar(
        select(Dataset).where(Dataset.id == dataset_id, Dataset.owner_id == user.id)
    )
    if not dataset:
        raise HTTPException(404, "Dataset not found")
    return dataset


def owned_version(db: Db, user: User, dataset: Dataset, version_id: str) -> DatasetVersion:
    version = db.scalar(
        select(DatasetVersion).where(
            DatasetVersion.id == version_id,
            DatasetVersion.dataset_id == dataset.id,
            DatasetVersion.owner_id == user.id,
        )
    )
    if not version:
        raise HTTPException(404, "Dataset version not found")
    return version


def latest_draft_id(db: Db, dataset: Dataset) -> str | None:
    return db.scalar(
        select(DatasetVersion.id)
        .where(
            DatasetVersion.dataset_id == dataset.id,
            DatasetVersion.owner_id == dataset.owner_id,
            DatasetVersion.state == "draft",
        )
        .order_by(DatasetVersion.created_at.desc())
        .limit(1)
    )


def connection_uri(db: Db, dataset: Dataset) -> str | None:
    connection = db.get(Connection, dataset.connection_id) if dataset.connection_id else None
    return decrypt(connection.encrypted_uri) if connection else None


@router.get("", response_model=list[DatasetOut])
def list_datasets(db: Db, user: CurrentUser):
    drafts: dict[str, str] = {}
    for dataset_id, version_id in db.execute(
        select(DatasetVersion.dataset_id, DatasetVersion.id)
        .where(
            DatasetVersion.owner_id == user.id,
            DatasetVersion.state == "draft",
        )
        .order_by(DatasetVersion.created_at.desc())
    ):
        drafts.setdefault(dataset_id, version_id)
    return [
        output(item, review_version_id=drafts.get(item.id))
        for item in db.scalars(select(Dataset).where(Dataset.owner_id == user.id))
    ]


@router.post("/upload", response_model=DatasetOut, status_code=201)
async def upload_dataset(
    db: Db,
    files: Annotated[list[UploadFile], File()],
    user: CurrentUser,
    storage: StorageDep,
    name: Annotated[str | None, Form()] = None,
    review: Annotated[bool, Form()] = False,
    options_json: Annotated[str | None, Form()] = None,
):
    values: list[tuple[str, Path]] = []
    try:
        for file in files:
            values.append((file.filename or "upload", await _stream_upload(file, storage)))
        dataset, version = stage_files(
            db, user, values, name, options=_options(options_json, len(values))
        )
        if review:
            return output(dataset, review_version_id=version.id)
        return output(activate_version(db, dataset, version, None))
    except IngestError as error:
        raise HTTPException(400, str(error)) from error
    finally:
        for _, path in values:
            path.unlink(missing_ok=True)


@router.post("/from-url", response_model=DatasetOut, status_code=201)
def from_url(body: URLDatasetCreate, db: Db, user: CurrentUser):
    try:
        return output(
            create_dataset_from_url(db, user, body.url, body.name, {"sheets": body.sheets or []})
        )
    except IngestError as error:
        raise HTTPException(400, str(error)) from error


@router.post("/from-connection", response_model=DatasetOut, status_code=201)
def from_connection(body: ConnectionDatasetCreate, db: Db, user: CurrentUser):
    connection = None
    if body.connection_id:
        connection = db.scalar(
            select(Connection).where(
                Connection.id == body.connection_id, Connection.owner_id == user.id
            )
        )
    elif body.uri:
        kind, _ = parse_db_uri(body.uri)
        connection = Connection(
            owner_id=user.id,
            name=body.name,
            kind=kind,
            encrypted_uri=encrypt(body.uri),
        )
        db.add(connection)
        db.flush()
    if not connection:
        raise HTTPException(404, "Connection not found")
    try:
        return output(create_dataset_from_connection(db, user, connection, body.name, body.tables))
    except IngestError as error:
        raise HTTPException(400, str(error)) from error


@router.get("/{dataset_id}/versions", response_model=list[VersionOut])
def versions(dataset_id: str, db: Db, user: CurrentUser):
    dataset = owned(db, user, dataset_id)
    if dataset.connection_id:
        raise HTTPException(422, "Live connection datasets do not have immutable versions")
    return [version_output(item) for item in list_versions(db, dataset)]


@router.post("/{dataset_id}/versions", response_model=VersionOut, status_code=201)
async def replace_version(
    dataset_id: str,
    db: Db,
    files: Annotated[list[UploadFile], File()],
    user: CurrentUser,
    storage: StorageDep,
    options_json: Annotated[str | None, Form()] = None,
):
    dataset = owned(db, user, dataset_id)
    if dataset.connection_id:
        raise HTTPException(422, "Live connection datasets do not have immutable versions")
    values: list[tuple[str, Path]] = []
    try:
        for file in files:
            values.append((file.filename or "upload", await _stream_upload(file, storage)))
        _, version = stage_files(
            db, user, values, None, dataset=dataset, options=_options(options_json, len(values))
        )
        return version_output(version)
    except IngestError as error:
        raise HTTPException(400, str(error)) from error
    finally:
        for _, path in values:
            path.unlink(missing_ok=True)


@router.post("/{dataset_id}/versions/{version_id}/confirm", response_model=DatasetOut)
def confirm_version(
    dataset_id: str,
    version_id: str,
    body: VersionConfirm,
    db: Db,
    user: CurrentUser,
):
    dataset = owned(db, user, dataset_id)
    version = owned_version(db, user, dataset, version_id)
    return output(activate_version(db, dataset, version, body.expected_current_version_id))


@router.post("/{dataset_id}/versions/{version_id}/profile", response_model=VersionOut)
def refresh_version_profile(dataset_id: str, version_id: str, db: Db, user: CurrentUser):
    dataset = owned(db, user, dataset_id)
    version = owned_version(db, user, dataset, version_id)
    try:
        return version_output(reprofile_version(db, dataset, version))
    except IngestError as error:
        raise HTTPException(400, str(error)) from error


@router.post("/{dataset_id}/refresh", response_model=DatasetOut)
def refresh_dataset(dataset_id: str, db: Db, user: CurrentUser):
    dataset = owned(db, user, dataset_id)
    try:
        version = refresh_url_dataset(db, user, dataset)
    except IngestError as error:
        raise HTTPException(422, str(error)) from error
    return output(dataset, review_version_id=version.id)


@router.put("/{dataset_id}/notes", response_model=DatasetOut)
def update_notes(dataset_id: str, body: DatasetNotes, db: Db, user: CurrentUser):
    dataset = owned(db, user, dataset_id)
    dataset.notes_json = body.model_dump(mode="json")
    db.commit()
    db.refresh(dataset)
    return output(dataset)


@router.patch("/{dataset_id}/privacy", response_model=DatasetOut)
def update_privacy(
    dataset_id: str,
    body: PrivacyUpdate,
    db: Db,
    user: CurrentUser,
):
    dataset = owned(db, user, dataset_id)
    active = db.scalar(
        select(func.count())
        .select_from(Run)
        .join(ChatSession, ChatSession.id == Run.session_id)
        .where(
            ChatSession.dataset_id == dataset.id,
            Run.status.in_(("pending", "running")),
        )
    )
    if active:
        raise HTTPException(409, "Wait for active analyses to finish before changing privacy")
    dataset.llm_policy = body.mode
    db.commit()
    db.refresh(dataset)
    return output(dataset)


@router.post("/{dataset_id}/sources", response_model=DatasetOut)
async def add_dataset_source(
    dataset_id: str,
    request: Request,
    db: Db,
    user: CurrentUser,
    storage: StorageDep,
):
    dataset = owned(db, user, dataset_id)
    temp_path: Path | None = None
    try:
        if request.headers.get("content-type", "").startswith("multipart/"):
            form = await request.form()
            upload = next(
                (value for value in form.values() if isinstance(value, StarletteUploadFile)), None
            )
            if not upload:
                raise HTTPException(400, "A file is required")
            temp_path = await _stream_upload(upload, storage)
            suffix = Path(upload.filename or "upload").suffix
            if suffix:
                renamed = temp_path.with_suffix(suffix)
                temp_path.replace(renamed)
                temp_path = renamed
            source = detect_source(str(temp_path), name=Path(upload.filename or "upload").stem)
            if source.kind not in {"csv", "tsv", "parquet", "json", "excel"}:
                raise IngestError("Only CSV, TSV, Parquet, JSON and Excel uploads are supported")
            source.options["original_filename"] = Path(upload.filename or "upload").name
        else:
            body = await request.json()
            source = public_source(body["url"])
        return output(add_source(db, dataset, source, connection_uri(db, dataset)))
    except IngestError as error:
        raise HTTPException(400, str(error)) from error
    finally:
        if temp_path:
            temp_path.unlink(missing_ok=True)


@router.get("/{dataset_id}", response_model=DatasetOut)
def get_dataset(dataset_id: str, db: Db, user: CurrentUser):
    dataset = owned(db, user, dataset_id)
    if not dataset.connection_id:
        ensure_current_version(db, dataset)
    return output(dataset, review_version_id=latest_draft_id(db, dataset))


@router.get("/{dataset_id}/schema")
def schema(dataset_id: str, db: Db, user: CurrentUser):
    return owned(db, user, dataset_id).schema_json


@router.get("/{dataset_id}/preview")
def preview(
    dataset_id: str,
    table: str,
    db: Db,
    user: CurrentUser,
    limit: int = 50,
    version_id: str | None = None,
):
    dataset = owned(db, user, dataset_id)
    if version_id is None and not dataset.connection_id:
        ensure_current_version(db, dataset)
    if version_id:
        version = owned_version(db, user, dataset, version_id)
        tables = [item["name"] for item in version.schema_json["tables"]]
    else:
        tables = dataset.tables_json
    if table not in tables:
        raise HTTPException(404, "Table not found")
    quoted = ".".join(f'"{part.replace(chr(34), chr(34) * 2)}"' for part in table.split("."))
    catalog = open_catalog(
        dataset,
        connection_uri(db, dataset),
        for_run=True,
        version_id=version_id,
    )
    try:
        frame = catalog.query(guard_sql(f"SELECT * FROM {quoted} LIMIT {min(limit, 200)}"))
        return {
            "columns": list(frame.columns),
            "rows": json.loads(frame.to_json(orient="records", date_format="iso")),
        }
    finally:
        catalog.close()


@router.delete("/{dataset_id}", status_code=204)
def delete_dataset(
    dataset_id: str,
    db: Db,
    user: CurrentUser,
    storage: StorageDep,
):
    dataset = owned(db, user, dataset_id)
    scheduled = db.scalar(
        select(func.count()).select_from(Schedule).where(Schedule.dataset_id == dataset.id)
    )
    if scheduled:
        raise HTTPException(409, "Remove dataset schedules before deleting it")
    active = db.scalar(
        select(func.count())
        .select_from(Run)
        .join(ChatSession, Run.session_id == ChatSession.id)
        .where(
            ChatSession.dataset_id == dataset.id,
            Run.status.in_(("pending", "running")),
        )
    )
    if active:
        raise HTTPException(409, "Wait for active analyses before deleting the dataset")
    session_ids = list(db.scalars(select(ChatSession.id).where(ChatSession.dataset_id == dataset.id)))
    db.execute(delete(ReportDefinition).where(ReportDefinition.dataset_id == dataset.id))
    db.execute(delete(DatasetVersion).where(DatasetVersion.dataset_id == dataset.id))
    if session_ids:
        run_ids = list(db.scalars(select(Run.id).where(Run.session_id.in_(session_ids))))
        if run_ids:
            from insightforge.db.models import Artifact

            db.execute(delete(Artifact).where(Artifact.run_id.in_(run_ids)))
            db.execute(delete(Run).where(Run.id.in_(run_ids)))
        db.execute(delete(ChatSession).where(ChatSession.id.in_(session_ids)))
    db.delete(dataset)
    db.commit()
    storage.delete_dataset(user.id, dataset.id)
