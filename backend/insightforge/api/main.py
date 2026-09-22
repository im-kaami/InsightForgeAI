import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from insightforge.api.routers import auth, connections, datasets, runs, schedules, sessions
from insightforge.config import get_settings
from insightforge.core.llm import build_llm
from insightforge.db.models import Run
from insightforge.db.session import SessionLocal, configure, init_db
from insightforge.services.datasets import DatasetBusyError
from insightforge.services.events import RunEventBus
from insightforge.services.scheduler import scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    init_db()
    configure()
    db = SessionLocal()
    try:
        interrupted = db.query(Run).filter(Run.status.in_({"pending", "running"})).all()
        for run in interrupted:
            run.status = "failed"
            run.error = "Interrupted by server restart"
        db.commit()
    finally:
        db.close()
    app.state.llm = build_llm(settings)
    app.state.bus = RunEventBus(asyncio.get_running_loop())
    app.state.tasks = set()
    if settings.scheduler_enabled:
        scheduler.start(app)
        configure()
        db = SessionLocal()
        try:
            scheduler.sync_jobs(db)
        finally:
            db.close()
    yield
    if settings.scheduler_enabled:
        scheduler.shutdown()


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(title="InsightForge", lifespan=lifespan)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    for router in (
        auth.router,
        connections.router,
        datasets.router,
        sessions.router,
        runs.router,
        schedules.router,
    ):
        application.include_router(router, prefix="/api")

    @application.exception_handler(DatasetBusyError)
    async def dataset_busy(_request: Request, error: DatasetBusyError):
        return JSONResponse({"detail": str(error)}, status_code=409)

    @application.get("/api/health")
    def health():
        return {"status": "ok"}

    return application


app = create_app()
