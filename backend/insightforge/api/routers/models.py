from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from insightforge.api.deps import CurrentUser, Db
from insightforge.api.schemas import SavedModelOut, SaveModelIn, ScoreModelIn, ScoreOut
from insightforge.db.models import SavedModel
from insightforge.services import models as service
from insightforge.services.models import ModelServiceError

router = APIRouter(tags=["models"])


def _out(model: SavedModel) -> SavedModelOut:
    metrics = dict(model.metrics_json or {})
    warning = metrics.pop("warning", None)
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
    )


def _guard(error: ModelServiceError) -> HTTPException:
    return HTTPException(error.status, str(error))


@router.post("/runs/{run_id}/artifacts/{position}/model", response_model=SavedModelOut, status_code=201)
def save_model(run_id: str, position: int, body: SaveModelIn, db: Db, user: CurrentUser):
    try:
        model = service.create_saved_model(db, user.id, run_id, position, body.name or "")
    except ModelServiceError as error:
        raise _guard(error) from error
    return _out(model)


@router.get("/datasets/{dataset_id}/models", response_model=list[SavedModelOut])
def list_models(dataset_id: str, db: Db, user: CurrentUser):
    try:
        return [_out(model) for model in service.list_models(db, user.id, dataset_id)]
    except ModelServiceError as error:
        raise _guard(error) from error


@router.get("/models/{model_id}", response_model=SavedModelOut)
def get_model(model_id: str, db: Db, user: CurrentUser):
    try:
        return _out(service.owned_model(db, user.id, model_id))
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
