import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from insightforge.api.routers import (
    auth,
    connections,
    dashboards,
    datasets,
    follows,
    models,
    runs,
    schedules,
    sessions,
    shares,
    usage,
    verified_reports,
    workspaces,
)
from insightforge.api.schemas import HealthOut, ImportOptions
from insightforge.config import get_settings, validate_settings
from insightforge.core.llm import build_llm, build_local_llm, llm_mode, resolved_model
from insightforge.db.models import Run
from insightforge.db.session import SessionLocal, configure, init_db
from insightforge.services.datasets import DatasetBusyError
from insightforge.services.events import RunEventBus
from insightforge.services.scheduler import scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    problems = validate_settings(settings)
    if settings.environment == "production" and problems:
        raise RuntimeError("Refusing to start: " + "; ".join(problems))
    for problem in problems:
        logging.getLogger("insightforge").warning("Configuration warning: %s", problem)
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
    app.state.local_llm = build_local_llm(settings)
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
    logger = logging.getLogger("insightforge")
    if not logger.handlers:
        logger.addHandler(logging.StreamHandler())
    logger.setLevel(logging.INFO)
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
        verified_reports.router,
        models.router,
        follows.router,
        dashboards.router,
        shares.router,
        usage.router,
        workspaces.router,
    ):
        application.include_router(router, prefix="/api")

    def openapi_schema():
        if application.openapi_schema:
            return application.openapi_schema
        schema = get_openapi(
            title=application.title,
            version=application.version,
            routes=application.routes,
        )
        schema["components"]["schemas"]["ImportOptions"] = ImportOptions.model_json_schema()
        application.openapi_schema = schema
        return schema

    application.openapi = openapi_schema

    @application.exception_handler(DatasetBusyError)
    async def dataset_busy(_request: Request, error: DatasetBusyError):
        return JSONResponse({"detail": str(error)}, status_code=409)

    @application.get("/api/health", response_model=HealthOut)
    def health():
        return {
            "status": "ok",
            "llm": llm_mode(application.state.llm),
            "provider": settings.llm_provider,
            "model": resolved_model(settings),
            "local_model": getattr(application.state.local_llm, "model", None),
        }

    return application


app = create_app()
