import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from sqlalchemy import select
from starlette.datastructures import UploadFile as StarletteUploadFile

from insightforge.api.deps import CurrentUser, Db, StorageDep
from insightforge.api.schemas import ConnectionDatasetCreate, DatasetOut, URLDatasetCreate
from insightforge.config import get_settings
from insightforge.core.sql_guard import guard_sql
from insightforge.db.models import Connection, Dataset, User
from insightforge.ingest import IngestError, detect_source, parse_db_uri
from insightforge.services.crypto import decrypt, encrypt
from insightforge.services.datasets import (
    add_source,
    create_dataset_from_connection,
    create_dataset_from_files,
    create_dataset_from_url,
    open_catalog,
)

router = APIRouter(prefix="/datasets", tags=["datasets"])


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


def output(dataset: Dataset) -> DatasetOut:
    return DatasetOut(
        id=dataset.id,
        name=dataset.name,
        kind=dataset.kind,
        tables=dataset.tables_json,
        schema=dataset.schema_json,
        sources=dataset.sources_json,
        created_at=dataset.created_at,
    )


def owned(db: Db, user: User, dataset_id: str) -> Dataset:
    dataset = db.scalar(
        select(Dataset).where(Dataset.id == dataset_id, Dataset.owner_id == user.id)
    )
    if not dataset:
        raise HTTPException(404, "Dataset not found")
    return dataset


def connection_uri(db: Db, dataset: Dataset) -> str | None:
    connection = db.get(Connection, dataset.connection_id) if dataset.connection_id else None
    return decrypt(connection.encrypted_uri) if connection else None


@router.get("", response_model=list[DatasetOut])
def list_datasets(db: Db, user: CurrentUser):
    return [output(item) for item in db.scalars(select(Dataset).where(Dataset.owner_id == user.id))]


@router.post("/upload", response_model=DatasetOut, status_code=201)
async def upload_dataset(
    db: Db,
    files: Annotated[list[UploadFile], File()],
    user: CurrentUser,
    storage: StorageDep,
    name: Annotated[str | None, Form()] = None,
):
    values: list[tuple[str, Path]] = []
    try:
        for file in files:
            values.append((file.filename or "upload", await _stream_upload(file, storage)))
        return output(create_dataset_from_files(db, user, values, name))
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
def from_connection(
    body: ConnectionDatasetCreate, db: Db, user: CurrentUser
):
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


@router.post("/{dataset_id}/sources", response_model=DatasetOut)
async def add_dataset_source(
    dataset_id: str,
    request: Request,
    db: Db,
    user: CurrentUser,
    storage: StorageDep,
):
    dataset = owned(db, user, dataset_id)
    try:
        if request.headers.get("content-type", "").startswith("multipart/"):
            form = await request.form()
            upload = next((value for value in form.values() if isinstance(value, StarletteUploadFile)), None)
            if not upload:
                raise HTTPException(400, "A file is required")
            temp_path = await _stream_upload(upload, storage)
            path = storage.save_upload_path(
                user.id, dataset.id, upload.filename or "upload", temp_path
            )
            source = detect_source(str(path))
        else:
            body = await request.json()
            source = detect_source(body["url"])
        return output(add_source(db, dataset, source, connection_uri(db, dataset)))
    except IngestError as error:
        raise HTTPException(400, str(error)) from error


@router.get("/{dataset_id}", response_model=DatasetOut)
def get_dataset(dataset_id: str, db: Db, user: CurrentUser):
    return output(owned(db, user, dataset_id))


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
):
    dataset = owned(db, user, dataset_id)
    if table not in dataset.tables_json:
        raise HTTPException(404, "Table not found")
    quoted = ".".join(f'"{part.replace(chr(34), chr(34) * 2)}"' for part in table.split("."))
    catalog = open_catalog(dataset, connection_uri(db, dataset), for_run=True)
    try:
        frame = catalog.query(guard_sql(f"SELECT * FROM {quoted} LIMIT {min(limit, 200)}"))
        return {"columns": list(frame.columns), "rows": frame.to_dict(orient="records")}
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
    db.delete(dataset)
    db.commit()
    storage.delete_dataset(user.id, dataset.id)
