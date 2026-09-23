import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response, StreamingResponse
from sqlalchemy import select

from insightforge.api.deps import BusDep, CurrentUser, Db
from insightforge.api.routers.sessions import run_output
from insightforge.api.schemas import RunOut
from insightforge.db.models import Artifact, Run, User
from insightforge.services.reports import (
    ReportFormatUnavailable,
    render_html,
    render_markdown,
    render_pdf,
)

router = APIRouter(prefix="/runs", tags=["runs"])


def owned(db: Db, user: User, run_id: str) -> Run:
    run = db.scalar(select(Run).where(Run.id == run_id, Run.owner_id == user.id))
    if not run:
        raise HTTPException(404, "Run not found")
    return run


@router.get("/{run_id}", response_model=RunOut)
def get_run(run_id: str, db: Db, user: CurrentUser):
    return run_output(db, owned(db, user, run_id))


@router.get("/{run_id}/events")
def events(
    run_id: str,
    db: Db,
    user: CurrentUser,
    bus: BusDep,
):
    run = owned(db, user, run_id)

    async def stream():
        if run.status in {"completed", "failed"}:
            event = {"type": "done", "run_id": run.id}
            if run.status == "failed":
                event = {"type": "error", "message": run.error}
            yield f"data: {json.dumps(event)}\n\n"
            return
        async for event in bus.subscribe(run.id):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.get("/{run_id}/report")
def report(
    run_id: str,
    db: Db,
    user: CurrentUser,
    format: str = "md",
):
    run = owned(db, user, run_id)
    artifacts = db.scalars(
        select(Artifact).where(Artifact.run_id == run.id).order_by(Artifact.position)
    ).all()
    text = render_markdown(run, artifacts)
    if format == "md":
        return Response(
            text,
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="run-{run.id}.md"'},
        )
    html = render_html(run, artifacts)
    if format == "html":
        return Response(html, media_type="text/html")
    if format == "pdf":
        try:
            content = render_pdf(html)
        except ReportFormatUnavailable as error:
            raise HTTPException(501, str(error)) from error
        return Response(content, media_type="application/pdf")
    raise HTTPException(422, "format must be md, html, or pdf")


@router.get("/{run_id}/artifacts/{position}/csv")
def table_csv(run_id: str, position: int, db: Db, user: CurrentUser):
    run = owned(db, user, run_id)
    artifact = db.scalar(
        select(Artifact).where(
            Artifact.run_id == run.id,
            Artifact.position == position,
            Artifact.type == "table",
        )
    )
    path = Path(artifact.file_path) if artifact and artifact.file_path else None
    if not path or not path.is_file():
        raise HTTPException(404, "Table CSV not found")
    return FileResponse(path, media_type="text/csv", filename=f"{artifact.name}.csv")


@router.get("/{run_id}/artifacts/{name}.png")
def plot_file(run_id: str, name: str, db: Db, user: CurrentUser):
    run = owned(db, user, run_id)
    artifact = db.scalar(
        select(Artifact).where(
            Artifact.run_id == run.id,
            Artifact.name == name,
            Artifact.type == "plot",
        )
    )
    path = Path(artifact.file_path) if artifact and artifact.file_path else None
    if not path or not path.is_file():
        raise HTTPException(404, "Plot not found")
    return FileResponse(path, media_type="image/png")
