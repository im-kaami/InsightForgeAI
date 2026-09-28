from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import delete, func, select

from insightforge.api.deps import BusDep, CurrentUser, Db, LLMDep
from insightforge.api.schemas import RunCreate, RunOut, SessionCreate, SessionOut
from insightforge.config import get_settings
from insightforge.db.models import (
    Artifact,
    ChatSession,
    Dataset,
    ReportDefinition,
    Run,
    Schedule,
    User,
)
from insightforge.db.session import SessionLocal
from insightforge.services.datasets import ensure_current_version
from insightforge.services.runs import start_run

router = APIRouter(prefix="/sessions", tags=["sessions"])


def run_output(db: Db, run: Run) -> RunOut:
    artifacts = db.scalars(
        select(Artifact).where(Artifact.run_id == run.id).order_by(Artifact.position)
    ).all()
    return RunOut(
        id=run.id,
        session_id=run.session_id,
        goal=run.goal,
        status=run.status,
        plan=run.plan_json,
        summary=run.summary,
        artifacts=[item.payload_json for item in artifacts],
        timings=run.timings_json or {},
        token_usage=run.token_usage_json or {},
        used_fallback_plan=run.used_fallback_plan,
        dataset_version_id=run.dataset_version_id,
        definition_id=run.definition_id,
        provenance=run.provenance_json or {},
        verification_status=run.verification_status,
        warnings=run.warnings_json or [],
        fallback_reason=run.fallback_reason,
        error=run.error,
        created_at=run.created_at,
        finished_at=run.finished_at,
    )


def session_output(
    db: Db,
    session: ChatSession,
    include_runs: bool = True,
    run_count: int = 0,
    last_activity_at: datetime | None = None,
) -> SessionOut:
    runs = []
    if include_runs:
        values = db.scalars(
            select(Run).where(Run.session_id == session.id).order_by(Run.created_at)
        ).all()
        runs = [run_output(db, item) for item in values]
        run_count = len(runs)
        activities = [item.finished_at or item.created_at for item in values]
        last_activity_at = max(activities, default=None)
    return SessionOut(
        id=session.id,
        dataset_id=session.dataset_id,
        title=session.title,
        created_at=session.created_at,
        runs=runs,
        run_count=run_count,
        last_activity_at=last_activity_at,
    )


def owned(db: Db, user: User, session_id: str) -> ChatSession:
    session = db.scalar(
        select(ChatSession).where(ChatSession.id == session_id, ChatSession.owner_id == user.id)
    )
    if not session:
        raise HTTPException(404, "Session not found")
    return session


@router.post("", response_model=SessionOut, status_code=201)
def create_session(body: SessionCreate, db: Db, user: CurrentUser):
    dataset = db.scalar(
        select(Dataset).where(Dataset.id == body.dataset_id, Dataset.owner_id == user.id)
    )
    if not dataset:
        raise HTTPException(404, "Dataset not found")
    session = ChatSession(
        owner_id=user.id,
        dataset_id=dataset.id,
        title=body.title or "New analysis",
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session_output(db, session)


@router.get("", response_model=list[SessionOut])
def list_sessions(db: Db, user: CurrentUser):
    values = db.scalars(select(ChatSession).where(ChatSession.owner_id == user.id)).all()
    stats = {
        session_id: (count, last_activity)
        for session_id, count, last_activity in db.execute(
            select(
                Run.session_id,
                func.count(),
                func.max(func.coalesce(Run.finished_at, Run.created_at)),
            )
            .where(Run.owner_id == user.id)
            .group_by(Run.session_id)
        )
    }
    return [
        session_output(
            db,
            item,
            include_runs=False,
            run_count=stats.get(item.id, (0, None))[0],
            last_activity_at=stats.get(item.id, (0, None))[1],
        )
        for item in values
    ]


@router.get("/{session_id}", response_model=SessionOut)
def get_session(session_id: str, db: Db, user: CurrentUser):
    return session_output(db, owned(db, user, session_id))


@router.get("/{session_id}/runs", response_model=list[RunOut])
def list_session_runs(session_id: str, db: Db, user: CurrentUser):
    session = owned(db, user, session_id)
    values = db.scalars(
        select(Run).where(Run.session_id == session.id).order_by(Run.created_at)
    ).all()
    return [run_output(db, run) for run in values]


@router.delete("/{session_id}", status_code=204)
def delete_session(session_id: str, db: Db, user: CurrentUser):
    session = owned(db, user, session_id)
    scheduled = db.scalar(
        select(func.count()).select_from(Schedule).where(Schedule.session_id == session.id)
    )
    if scheduled:
        raise HTTPException(409, "Remove session schedules before deleting it")
    active = db.scalar(
        select(func.count())
        .select_from(Run)
        .where(Run.session_id == session.id, Run.status.in_(("pending", "running")))
    )
    if active:
        raise HTTPException(409, "Wait for active analyses before deleting the session")
    run_ids = list(db.scalars(select(Run.id).where(Run.session_id == session.id)))
    if run_ids:
        db.execute(delete(Artifact).where(Artifact.run_id.in_(run_ids)))
        db.execute(delete(Run).where(Run.id.in_(run_ids)))
    db.execute(delete(ReportDefinition).where(ReportDefinition.session_id == session.id))
    db.delete(session)
    db.commit()


@router.post(
    "/{session_id}/runs", response_model=RunOut, status_code=status.HTTP_202_ACCEPTED
)
async def create_run(
    session_id: str,
    body: RunCreate,
    request: Request,
    db: Db,
    user: CurrentUser,
    bus: BusDep,
    llm: LLMDep,
):
    session = owned(db, user, session_id)
    dataset = db.get(Dataset, session.dataset_id)
    version = ensure_current_version(db, dataset)
    if not dataset.connection_id and version is None:
        raise HTTPException(409, "Review and confirm the imported version first")
    active = db.scalar(
        select(func.count())
        .select_from(Run)
        .where(Run.owner_id == user.id, Run.status.in_(("pending", "running")))
    )
    if active >= get_settings().max_concurrent_runs_per_user:
        raise HTTPException(429, "Too many analyses running; wait for one to finish")
    run = Run(
        session_id=session.id,
        owner_id=user.id,
        goal=body.goal,
        status="pending",
        dataset_version_id=version.id if version else None,
        request_json={
            "kind": "exploratory",
            "privacy_mode": dataset.llm_policy,
            "mode": body.mode,
            "allow_clarification": not body.clarified,
        },
        verification_status="exploratory",
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    start_run(SessionLocal, bus, run.id, request.app.state.tasks, llm)
    return run_output(db, run)
