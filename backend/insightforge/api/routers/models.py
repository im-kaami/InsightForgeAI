from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from insightforge.api.deps import CurrentUser, Db
from insightforge.api.routers.schedules import validate_cron
from insightforge.api.schemas import (
    ModelScheduleIn,
    ModelScheduleOut,
    SavedModelOut,
    SaveModelIn,
    ScoreModelIn,
    ScoreOut,
    ScoringOut,
)
from insightforge.db.models import ModelScoring, SavedModel
from insightforge.services import models as service
from insightforge.services.models import ModelServiceError
from insightforge.services.scheduler import model_job_id, next_run, scheduler

router = APIRouter(tags=["models"])


def _out(db: Db, model: SavedModel) -> SavedModelOut:
    metrics = dict(model.metrics_json or {})
    warning = metrics.pop("warning", None)
    schedule = service.get_schedule(db, model)
    return SavedModelOut(
        id=model.id,
        dataset_id=model.dataset_id,
        dataset_version_id=model.dataset_version_id,
        run_id=model.run_id,
        name=model.name,
        task=model.task,
        target=model.target,
        features=list(model.features_json or []),
        date_column=model.date_column,
        model_type=model.model_type,
        metrics=metrics,
        created_at=model.created_at.isoformat() if model.created_at else None,
        warning=warning,
        schedule=ModelScheduleOut.model_validate(schedule) if schedule else None,
        open_alerts=len(service.open_alerts(db, model.owner_id, model.id)),
    )


def _scoring_out(scoring: ModelScoring, model: SavedModel | None = None) -> ScoringOut:
    return ScoringOut(
        id=scoring.id,
        model_id=scoring.model_id,
        model_name=model.name if model else None,
        dataset_id=model.dataset_id if model else None,
        trigger=scoring.trigger,
        version_id=scoring.version_id,
        status=scoring.status,
        rows_scored=scoring.rows_scored,
        max_psi=scoring.max_psi,
        verdict=scoring.verdict,
        reasons=list(scoring.reasons_json or []),
        error=scoring.error,
        alert=scoring.alert,
        acknowledged_at=scoring.acknowledged_at,
        created_at=scoring.created_at,
    )


def _guard(error: ModelServiceError) -> HTTPException:
    return HTTPException(error.status, str(error))


@router.post("/runs/{run_id}/artifacts/{position}/model", response_model=SavedModelOut, status_code=201)
def save_model(run_id: str, position: int, body: SaveModelIn, db: Db, user: CurrentUser):
    try:
        model = service.create_saved_model(db, user.id, run_id, position, body.name or "")
    except ModelServiceError as error:
        raise _guard(error) from error
    return _out(db, model)


@router.get("/datasets/{dataset_id}/models", response_model=list[SavedModelOut])
def list_models(dataset_id: str, db: Db, user: CurrentUser):
    try:
        return [_out(db, model) for model in service.list_models(db, user.id, dataset_id)]
    except ModelServiceError as error:
        raise _guard(error) from error


@router.get("/models/{model_id}", response_model=SavedModelOut)
def get_model(model_id: str, db: Db, user: CurrentUser):
    try:
        return _out(db, service.owned_model(db, user.id, model_id))
    except ModelServiceError as error:
        raise _guard(error) from error


@router.delete("/models/{model_id}", status_code=204)
def delete_model(model_id: str, db: Db, user: CurrentUser):
    try:
        service.delete_model(db, user.id, model_id)
    except ModelServiceError as error:
        raise _guard(error) from error


@router.post("/models/{model_id}/score", response_model=ScoreOut)
def score_model(model_id: str, body: ScoreModelIn, db: Db, user: CurrentUser):
    try:
        return service.score_model(db, user.id, model_id, body.version_id)
    except ModelServiceError as error:
        raise _guard(error) from error


@router.get("/models/{model_id}/scores/latest.csv")
def latest_scores(model_id: str, db: Db, user: CurrentUser):
    try:
        path = service.latest_scores_path(db, user.id, model_id)
    except ModelServiceError as error:
        raise _guard(error) from error
    return FileResponse(path, media_type="text/csv", filename="scores-latest.csv")


@router.put("/models/{model_id}/schedule", response_model=ModelScheduleOut)
def put_schedule(model_id: str, body: ModelScheduleIn, db: Db, user: CurrentUser):
    validate_cron(body.cron, body.timezone)
    try:
        schedule = service.save_schedule(
            db,
            user.id,
            model_id,
            body.cron,
            body.timezone,
            body.enabled,
            next_run(body.cron, body.timezone),
        )
    except ModelServiceError as error:
        raise _guard(error) from error
    if scheduler.scheduler.running:
        scheduler.reschedule_model(schedule)
        db.commit()
    return schedule


@router.delete("/models/{model_id}/schedule", status_code=204)
def remove_schedule(model_id: str, db: Db, user: CurrentUser):
    try:
        schedule_id = service.delete_schedule(db, user.id, model_id)
    except ModelServiceError as error:
        raise _guard(error) from error
    scheduler.remove(model_job_id(schedule_id))


@router.post("/models/{model_id}/schedule/run-now", response_model=ScoringOut)
def run_schedule_now(model_id: str, db: Db, user: CurrentUser):
    """Run the scheduled check immediately: current version, stored with alerts."""
    try:
        model = service.owned_model(db, user.id, model_id)
        if service.get_schedule(db, model) is None:
            raise ModelServiceError("This model has no schedule", status=404)
        scoring = service.run_scheduled_scoring(db, model.id)
    except ModelServiceError as error:
        raise _guard(error) from error
    return _scoring_out(scoring, model)


@router.get("/models/{model_id}/scorings", response_model=list[ScoringOut])
def scorings(model_id: str, db: Db, user: CurrentUser):
    try:
        model = service.owned_model(db, user.id, model_id)
        return [_scoring_out(item, model) for item in service.list_scorings(db, user.id, model_id)]
    except ModelServiceError as error:
        raise _guard(error) from error


@router.post("/models/{model_id}/scorings/{scoring_id}/acknowledge", response_model=ScoringOut)
def acknowledge(model_id: str, scoring_id: str, db: Db, user: CurrentUser):
    try:
        model = service.owned_model(db, user.id, model_id)
        return _scoring_out(service.acknowledge(db, user.id, model_id, scoring_id), model)
    except ModelServiceError as error:
        raise _guard(error) from error


@router.get("/model-alerts", response_model=list[ScoringOut])
def model_alerts(db: Db, user: CurrentUser):
    items = service.open_alerts(db, user.id)
    models = {item.model_id: db.get(SavedModel, item.model_id) for item in items}
    return [_scoring_out(item, models.get(item.model_id)) for item in items]
