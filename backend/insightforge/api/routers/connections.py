from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from insightforge.api.deps import CurrentUser, Db
from insightforge.api.schemas import (
    SCHEMA_NAME,
    ConnectionCreate,
    ConnectionOut,
    ConnectionTables,
    ConnectionTest,
)
from insightforge.config import get_settings
from insightforge.db.models import Connection, Dataset
from insightforge.ingest import IngestError, parse_db_uri, redact_uri
from insightforge.ingest.database import list_tables
from insightforge.services.crypto import decrypt, encrypt

router = APIRouter(prefix="/connections", tags=["connections"])


def output(connection: Connection) -> ConnectionOut:
    return ConnectionOut(
        id=connection.id,
        name=connection.name,
        kind=connection.kind,
        redacted_uri=redact_uri(decrypt(connection.encrypted_uri)),
    )


@router.get("", response_model=list[ConnectionOut])
def list_connections(db: Db, user: CurrentUser):
    return [output(item) for item in db.scalars(select(Connection).where(Connection.owner_id == user.id))]


@router.get("/options")
def connection_options(user: CurrentUser):
    """What the Database tab may offer on this server."""
    return {"sqlite_files": get_settings().allow_sqlite_files}


@router.post("/test", response_model=ConnectionTables)
def test_connection(body: ConnectionTest, user: CurrentUser):
    """Connect and list the tables without saving anything."""
    kind, _ = parse_db_uri(body.uri)
    try:
        tables = list_tables(body.uri, body.schema_name)
    except IngestError as error:
        raise HTTPException(422, str(error)) from error
    return ConnectionTables(kind=kind, tables=tables, redacted_uri=redact_uri(body.uri))


@router.get("/{connection_id}/tables", response_model=ConnectionTables)
def connection_tables(
    connection_id: str,
    db: Db,
    user: CurrentUser,
    schema: Annotated[str | None, Query(min_length=1, max_length=128, pattern=SCHEMA_NAME)] = None,
):
    connection = db.scalar(
        select(Connection).where(Connection.id == connection_id, Connection.owner_id == user.id)
    )
    if not connection:
        raise HTTPException(404, "Connection not found")
    uri = decrypt(connection.encrypted_uri)
    try:
        tables = list_tables(uri, schema)
    except IngestError as error:
        raise HTTPException(422, str(error)) from error
    return ConnectionTables(kind=connection.kind, tables=tables, redacted_uri=redact_uri(uri))


@router.post("", response_model=ConnectionOut, status_code=201)
def create_connection(body: ConnectionCreate, db: Db, user: CurrentUser):
    kind, _ = parse_db_uri(body.uri)
    try:
        list_tables(body.uri)
    except IngestError as error:
        raise HTTPException(400, str(error)) from error
    connection = Connection(
        owner_id=user.id,
        name=body.name,
        kind=kind,
        encrypted_uri=encrypt(body.uri),
    )
    db.add(connection)
    db.commit()
    db.refresh(connection)
    return output(connection)


@router.delete("/{connection_id}", status_code=204)
def delete_connection(connection_id: str, db: Db, user: CurrentUser):
    connection = db.scalar(
        select(Connection).where(Connection.id == connection_id, Connection.owner_id == user.id)
    )
    if not connection:
        raise HTTPException(404, "Connection not found")
    if db.scalar(select(Dataset).where(Dataset.connection_id == connection.id)):
        raise HTTPException(409, "Connection is used by a dataset")
    db.delete(connection)
    db.commit()
