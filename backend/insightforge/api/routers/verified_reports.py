from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import func, select

from insightforge.api.deps import BusDep, CurrentUser, Db, LLMDep
from insightforge.api.routers.datasets import owned, owned_version
from insightforge.api.routers.sessions import run_output
from insightforge.api.schemas import (
    DefinitionCreate,
    DefinitionOut,
    MetricReportCreate,
    ReportRunCreate,
    RunOut,
)
from insightforge.config import get_settings
from insightforge.core.metrics import MetricError
from insightforge.core.schema import SchemaInfo
from insightforge.core.verified_report import (
    ReportPeriod,
    ReportValidationError,
    SalesDefinition,
    validate_definition,
)
from insightforge.db.models import ChatSession, ReportDefinition, Run
from insightforge.db.session import SessionLocal
from insightforge.services.datasets import ensure_current_version
from insightforge.services.metric_reports import FollowError, approved_metric, check_request, new_report_run
from insightforge.services.runs import start_run

router = APIRouter(prefix="/datasets/{dataset_id}/reports", tags=["verified-reports"])


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def definition_output(value: ReportDefinition) -> DefinitionOut:
    return DefinitionOut(
        id=value.id,
        dataset_id=value.dataset_id,
        session_id=value.session_id,
        name=value.name,
        version=value.version,
        previous_id=value.previous_id,
        definition=SalesDefinition.model_validate(value.definition_json),
        approved_at=_utc(value.approved_at),
    )


def owned_definition(
    db: Db, owner_id: str, dataset_id: str, definition_id: str
) -> ReportDefinition:
    value = db.scalar(
        select(ReportDefinition).where(
            ReportDefinition.id == definition_id,
            ReportDefinition.dataset_id == dataset_id,
            ReportDefinition.owner_id == owner_id,
        )
    )
    if not value:
        raise HTTPException(404, "Report definition not found")
    return value


@router.get("", response_model=list[DefinitionOut])
def list_definitions(dataset_id: str, db: Db, user: CurrentUser):
    owned(db, user, dataset_id)
    values = db.scalars(
        select(ReportDefinition)
        .where(
            ReportDefinition.dataset_id == dataset_id,
            ReportDefinition.owner_id == user.id,
        )
        .order_by(ReportDefinition.approved_at.desc())
    ).all()
    return [definition_output(value) for value in values]


@router.post("", response_model=DefinitionOut, status_code=201)
def create_definition(
    dataset_id: str,
    body: DefinitionCreate,
    db: Db,
    user: CurrentUser,
):
    dataset = owned(db, user, dataset_id)
    version = ensure_current_version(db, dataset)
    if not version or version.state != "ready" or dataset.current_version_id != version.id:
        raise HTTPException(409, "Review and confirm the imported version first")
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "Name cannot be blank")
    try:
        validate_definition(body.definition, SchemaInfo.model_validate(version.schema_json))
    except ReportValidationError as error:
        raise HTTPException(422, str(error)) from error
    previous = None
    if body.previous_id:
        previous = owned_definition(db, user.id, dataset.id, body.previous_id)
        session_id = previous.session_id
        definition_version = previous.version + 1
    else:
        session = ChatSession(
            owner_id=user.id,
            dataset_id=dataset.id,
            title=name,
        )
        db.add(session)
        db.flush()
        session_id = session.id
        definition_version = 1
    value = ReportDefinition(
        dataset_id=dataset.id,
        owner_id=user.id,
        session_id=session_id,
        name=name,
        version=definition_version,
        previous_id=previous.id if previous else None,
        definition_json=body.definition.model_dump(mode="json"),
    )
    db.add(value)
    db.commit()
    db.refresh(value)
    return definition_output(value)


@router.post("/metric", response_model=RunOut, status_code=status.HTTP_202_ACCEPTED)
async def create_metric_report(
    dataset_id: str,
    body: MetricReportCreate,
    request: Request,
    db: Db,
    user: CurrentUser,
    bus: BusDep,
    llm: LLMDep,
):
    """Start a checked report for one approved metric. Code calculates everything; no AI is used."""
    dataset = owned(db, user, dataset_id)
    version = (
        owned_version(db, user, dataset, body.version_id)
        if body.version_id
        else ensure_current_version(db, dataset)
    )
    if version is None or version.state != "ready":
        raise HTTPException(409, "Confirm the dataset version before running a report")
    try:
        metric, revision = approved_metric(dataset, body.metric)
        check_request(dataset, version, metric, body.group_by)
    except FollowError as error:
        raise HTTPException(error.status, str(error)) from error
    except MetricError as error:
        raise HTTPException(422, str(error)) from error
    period = ReportPeriod(start_date=body.start_date, end_date=body.end_date)
    active = db.scalar(
        select(func.count())
        .select_from(Run)
        .where(Run.owner_id == user.id, Run.status.in_(("pending", "running")))
    )
    if active >= get_settings().max_concurrent_runs_per_user:
        raise HTTPException(429, "Too many analyses running; wait for one to finish")
    run = new_report_run(db, user.id, dataset, version, metric, revision, body.group_by, period)
    start_run(SessionLocal, bus, run.id, request.app.state.tasks, llm)
    return run_output(db, run)


@router.post(
    "/{definition_id}/runs",
    response_model=RunOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_report_run(
    dataset_id: str,
    definition_id: str,
    body: ReportRunCreate,
    request: Request,
    db: Db,
    user: CurrentUser,
    bus: BusDep,
    llm: LLMDep,
):
    dataset = owned(db, user, dataset_id)
    definition = owned_definition(db, user.id, dataset.id, definition_id)
    version = owned_version(db, user, dataset, body.version_id)
    if version.state != "ready":
        raise HTTPException(409, "Confirm the dataset version before running a report")
    try:
        sales_definition = SalesDefinition.model_validate(definition.definition_json)
        validate_definition(sales_definition, SchemaInfo.model_validate(version.schema_json))
    except ReportValidationError as error:
        raise HTTPException(422, str(error)) from error
    active = db.scalar(
        select(func.count())
        .select_from(Run)
        .where(Run.owner_id == user.id, Run.status.in_(("pending", "running")))
    )
    if active >= get_settings().max_concurrent_runs_per_user:
        raise HTTPException(429, "Too many analyses running; wait for one to finish")
    period = {"start_date": body.start_date.isoformat(), "end_date": body.end_date.isoformat()}
    run = Run(
        session_id=definition.session_id,
        owner_id=user.id,
        goal=f"{definition.name}: {body.start_date} to {body.end_date}",
        status="pending",
        dataset_version_id=version.id,
        definition_id=definition.id,
        request_json={
            "kind": "sales_margin_v1",
            "definition": definition.definition_json,
            "definition_version": definition.version,
            "period": period,
            "privacy_mode": "local",
        },
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    start_run(SessionLocal, bus, run.id, request.app.state.tasks, llm)
    return run_output(db, run)
