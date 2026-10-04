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
    MetricPreviewIn,
    MetricPreviewOut,
    MetricSuggestions,
    PrivacyUpdate,
    SaveQueryIn,
    Suggestions,
    URLDatasetCreate,
    VersionConfirm,
    VersionOut,
)
from insightforge.config import get_settings
from insightforge.core.metrics import (
    MetricError,
    MetricQuery,
    MetricSet,
    SavedMetrics,
    compile_query,
    definition_problems,
    suggest_metrics,
)
from insightforge.core.profiling import DataProfile
from insightforge.core.queries import ApprovedQuery, QuerySet, SavedQueries, query_problems
from insightforge.core.recipes import AppliedRecipe, CleaningRecipe, SavedRecipe, suggest_steps
from insightforge.core.relationships import (
    RelationshipSet,
    RelationshipSuggestions,
    SavedRelationships,
    suggest_relationships,
    unknown_columns,
)
from insightforge.core.schema import DatasetNotes, SchemaInfo
from insightforge.core.sql_guard import guard_sql
from insightforge.core.validation import RuleSet, SavedRules, ValidationReport, suggest_rules
from insightforge.db.models import (
    Artifact,
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
    save_recipe,
    save_rules,
    stage_files,
    stage_recipe_version,
    validate_version,
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
        recipe=SavedRecipe.model_validate(dataset.recipe_json or {}),
        rules=SavedRules.model_validate(dataset.rules_json or {}),
        relationships=SavedRelationships.model_validate(dataset.relationships_json or {}),
        metrics=SavedMetrics.model_validate(dataset.metrics_json or {}),
        queries=SavedQueries.model_validate(dataset.queries_json or {}),
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
        recipe=AppliedRecipe.model_validate(version.recipe_json) if version.recipe_json else None,
        validation=(
            ValidationReport.model_validate(version.validation_json) if version.validation_json else None
        ),
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


def _versioned(dataset: Dataset) -> None:
    if dataset.connection_id:
        raise HTTPException(422, "Live connection datasets do not have immutable versions")


@router.get("/{dataset_id}/suggestions", response_model=Suggestions)
def suggestions(dataset_id: str, db: Db, user: CurrentUser):
    dataset = owned(db, user, dataset_id)
    _versioned(dataset)
    ensure_current_version(db, dataset)
    if not dataset.profile_json:
        return Suggestions()
    profile = DataProfile.model_validate(dataset.profile_json)
    return Suggestions(recipe_steps=suggest_steps(profile), rules=suggest_rules(profile))


@router.get("/{dataset_id}/relationships/suggestions", response_model=RelationshipSuggestions)
def relationship_suggestions(dataset_id: str, db: Db, user: CurrentUser):
    dataset = owned(db, user, dataset_id)
    _versioned(dataset)
    ensure_current_version(db, dataset)
    schema = SchemaInfo.model_validate(dataset.schema_json or {"tables": []})
    if len(schema.tables) < 2:
        return RelationshipSuggestions()
    existing = SavedRelationships.model_validate(dataset.relationships_json or {}).relationships
    try:
        catalog = open_catalog(dataset)
    except IngestError as error:
        raise HTTPException(422, str(error)) from error
    try:
        found = suggest_relationships(
            catalog, schema, existing, timeout=get_settings().query_timeout_seconds
        )
    finally:
        catalog.close()
    return RelationshipSuggestions(suggestions=found)


@router.put("/{dataset_id}/relationships", response_model=DatasetOut)
def update_relationships(dataset_id: str, body: RelationshipSet, db: Db, user: CurrentUser):
    dataset = owned(db, user, dataset_id)
    schema = SchemaInfo.model_validate(dataset.schema_json or {"tables": []})
    missing = unknown_columns(body.relationships, schema)
    if missing:
        raise HTTPException(422, "These columns are not in the current data: " + ", ".join(missing))
    previous = SavedRelationships.model_validate(dataset.relationships_json or {})
    saved = SavedRelationships(
        relationships=body.relationships,
        revision=previous.revision + 1,
        updated_at=datetime.now(UTC),
    )
    dataset.relationships_json = saved.model_dump(mode="json")
    db.commit()
    db.refresh(dataset)
    return output(dataset, review_version_id=latest_draft_id(db, dataset))


def _metric_context(dataset: Dataset) -> tuple[SchemaInfo, list]:
    schema = SchemaInfo.model_validate(dataset.schema_json or {"tables": []})
    relationships = SavedRelationships.model_validate(dataset.relationships_json or {}).relationships
    return schema, relationships


@router.get("/{dataset_id}/metrics/suggestions", response_model=MetricSuggestions)
def metric_suggestions(dataset_id: str, db: Db, user: CurrentUser):
    dataset = owned(db, user, dataset_id)
    schema, _ = _metric_context(dataset)
    existing = SavedMetrics.model_validate(dataset.metrics_json or {}).metrics
    return MetricSuggestions(suggestions=suggest_metrics(schema, existing))


@router.put("/{dataset_id}/metrics", response_model=DatasetOut)
def update_metrics(dataset_id: str, body: MetricSet, db: Db, user: CurrentUser):
    dataset = owned(db, user, dataset_id)
    schema, relationships = _metric_context(dataset)
    problems = definition_problems(body.metrics, schema, relationships)
    if problems:
        raise HTTPException(422, "; ".join(problems))
    previous = SavedMetrics.model_validate(dataset.metrics_json or {})
    saved = SavedMetrics(
        metrics=body.metrics, revision=previous.revision + 1, updated_at=datetime.now(UTC)
    )
    dataset.metrics_json = saved.model_dump(mode="json")
    db.commit()
    db.refresh(dataset)
    return output(dataset, review_version_id=latest_draft_id(db, dataset))


@router.post("/{dataset_id}/metrics/preview", response_model=MetricPreviewOut)
def preview_metric(dataset_id: str, body: MetricPreviewIn, db: Db, user: CurrentUser):
    """Calculate a metric (saved or still being edited) on the current version, in code."""
    dataset = owned(db, user, dataset_id)
    schema, relationships = _metric_context(dataset)
    query = body.query or MetricQuery(metric=body.metric.name)
    request = query.model_copy(update={"metric": body.metric.name})
    try:
        compiled = compile_query(body.metric, request, schema, relationships)
    except MetricError as error:
        raise HTTPException(422, str(error)) from error
    if dataset.connection_id:
        raise HTTPException(422, "Previews are available for uploaded and URL data, not live connections")
    ensure_current_version(db, dataset)
    try:
        catalog = open_catalog(dataset)
    except IngestError as error:
        raise HTTPException(422, str(error)) from error
    try:
        guarded = guard_sql(compiled.sql)
        frame = catalog.query(guarded, timeout_seconds=get_settings().query_timeout_seconds)
    except Exception as error:  # noqa: BLE001 - reported to the user as a failed preview
        raise HTTPException(422, f"The metric could not be calculated: {error}") from error
    finally:
        catalog.close()
    preview = frame.head(200)
    rows = json.loads(preview.to_json(orient="records", date_format="iso"))
    return MetricPreviewOut(
        metric=compiled.metric,
        label=compiled.label,
        sql=guarded,
        description=compiled.description,
        columns=[str(column) for column in frame.columns],
        rows=rows,
        total_rows=len(frame),
    )


def _check_queries_run(db: Db, dataset: Dataset, queries: list[ApprovedQuery]) -> None:
    """Run new or changed SQL once on the current version so broken queries are never approved."""
    if not queries or dataset.connection_id:
        return
    ensure_current_version(db, dataset)
    try:
        catalog = open_catalog(dataset)
    except IngestError as error:
        raise HTTPException(422, str(error)) from error
    try:
        for item in queries:
            try:
                catalog.query(guard_sql(item.sql, 1), timeout_seconds=get_settings().query_timeout_seconds)
            except Exception as error:  # noqa: BLE001 - reported to the user as a failed check
                raise HTTPException(422, f"Query {item.question!r} could not run: {error}") from error
    finally:
        catalog.close()


def _save_queries(db: Db, dataset: Dataset, body: QuerySet) -> DatasetOut:
    problems = query_problems(body.queries)
    if problems:
        raise HTTPException(422, "; ".join(problems))
    previous = SavedQueries.model_validate(dataset.queries_json or {})
    known = {item.sql for item in previous.queries}
    _check_queries_run(db, dataset, [item for item in body.queries if item.sql not in known])
    saved = SavedQueries(
        queries=body.queries,
        approved_only=body.approved_only,
        revision=previous.revision + 1,
        updated_at=datetime.now(UTC),
    )
    dataset.queries_json = saved.model_dump(mode="json")
    db.commit()
    db.refresh(dataset)
    return output(dataset, review_version_id=latest_draft_id(db, dataset))


@router.put("/{dataset_id}/queries", response_model=DatasetOut)
def update_queries(dataset_id: str, body: QuerySet, db: Db, user: CurrentUser):
    return _save_queries(db, owned(db, user, dataset_id), body)


@router.post("/{dataset_id}/queries/from-run", response_model=DatasetOut)
def save_query_from_run(dataset_id: str, body: SaveQueryIn, db: Db, user: CurrentUser):
    """Approve a result table's SQL from one of this dataset's runs as an answer to its question."""
    dataset = owned(db, user, dataset_id)
    run = db.scalar(
        select(Run)
        .join(ChatSession, ChatSession.id == Run.session_id)
        .where(Run.id == body.run_id, Run.owner_id == user.id, ChatSession.dataset_id == dataset.id)
    )
    if run is None:
        raise HTTPException(404, "Run not found")
    artifact = db.scalar(
        select(Artifact).where(Artifact.run_id == run.id, Artifact.position == body.position)
    )
    sql = (artifact.payload_json or {}).get("sql") if artifact and artifact.type == "table" else None
    if not sql:
        raise HTTPException(404, "That result has no SQL table")
    question = (body.question or run.goal.split("\n\nClarification:")[0]).strip()[:300]
    previous = SavedQueries.model_validate(dataset.queries_json or {})
    if any(item.question.casefold() == question.casefold() for item in previous.queries):
        raise HTTPException(409, "An approved question with this wording already exists")
    try:
        query = ApprovedQuery(
            question=question, sql=sql, description=body.description, approved=True, source_run_id=run.id
        )
        body_set = QuerySet(queries=[*previous.queries, query], approved_only=previous.approved_only)
    except ValidationError as error:
        raise HTTPException(422, str(error)) from error
    return _save_queries(db, dataset, body_set)


@router.put("/{dataset_id}/recipe", response_model=DatasetOut)
def update_recipe(dataset_id: str, body: CleaningRecipe, db: Db, user: CurrentUser):
    dataset = owned(db, user, dataset_id)
    _versioned(dataset)
    save_recipe(db, dataset, body.steps, body.auto_apply)
    return output(dataset, review_version_id=latest_draft_id(db, dataset))


@router.post("/{dataset_id}/recipe/apply", response_model=VersionOut, status_code=201)
def apply_saved_recipe(dataset_id: str, db: Db, user: CurrentUser):
    dataset = owned(db, user, dataset_id)
    try:
        return version_output(stage_recipe_version(db, dataset))
    except IngestError as error:
        raise HTTPException(422, str(error)) from error


@router.put("/{dataset_id}/rules", response_model=DatasetOut)
def update_rules(dataset_id: str, body: RuleSet, db: Db, user: CurrentUser):
    dataset = owned(db, user, dataset_id)
    _versioned(dataset)
    save_rules(db, dataset, body.rules)
    current = ensure_current_version(db, dataset)
    if current is not None:
        try:
            validate_version(db, dataset, current)
        except IngestError as error:
            raise HTTPException(400, str(error)) from error
    return output(dataset, review_version_id=latest_draft_id(db, dataset))


@router.post("/{dataset_id}/versions/{version_id}/validate", response_model=VersionOut)
def recheck_version(dataset_id: str, version_id: str, db: Db, user: CurrentUser):
    dataset = owned(db, user, dataset_id)
    version = owned_version(db, user, dataset, version_id)
    try:
        return version_output(validate_version(db, dataset, version))
    except IngestError as error:
        raise HTTPException(400, str(error)) from error


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
    from insightforge.services.dashboards import forget_dataset
    from insightforge.services.metric_reports import delete_follows_for_dataset
    from insightforge.services.models import delete_models_for_dataset
    from insightforge.services.scheduler import follow_job_id, scheduler

    for follow_id in delete_follows_for_dataset(db, user.id, dataset.id):
        scheduler.remove(follow_job_id(follow_id))
    forget_dataset(db, user.id, dataset.id)
    delete_models_for_dataset(db, user.id, dataset.id)
    db.delete(dataset)
    db.commit()
    storage.delete_dataset(user.id, dataset.id)
