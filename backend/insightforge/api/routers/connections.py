from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from insightforge.api.deps import CurrentUser, Db
from insightforge.api.schemas import ConnectionCreate, ConnectionOut
from insightforge.core.catalog import DataCatalog
from insightforge.db.models import Connection, Dataset
from insightforge.ingest import DataSource, IngestError, load_source, parse_db_uri, redact_uri
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


@router.post("", response_model=ConnectionOut, status_code=201)
def create_connection(body: ConnectionCreate, db: Db, user: CurrentUser):
    kind, _ = parse_db_uri(body.uri)
    catalog = DataCatalog()
    try:
        load_source(DataSource(kind=kind, location=body.uri, name=body.name), catalog)
    except IngestError as error:
        raise HTTPException(400, str(error)) from error
    finally:
        catalog.close()
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
