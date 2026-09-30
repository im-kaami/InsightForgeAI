"""Save prediction models, score new data and measure drift.

Owner-scoped throughout. Model files are written by the app under
``STORAGE_DIR/users/<owner>/models/<model_id>/`` and only ever loaded from the path
stored on the database row (never from user input). Nothing here contacts an LLM.
"""

from __future__ import annotations

import csv
from typing import Any

import duckdb
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from insightforge.config import get_settings
from insightforge.core import model_store
from insightforge.core.model_store import ModelStoreError
from insightforge.core.predict import PredictError
from insightforge.core.sql_guard import guard_query
from insightforge.db.models import Artifact, Dataset, DatasetVersion, Run, SavedModel
from insightforge.services.datasets import open_catalog
from insightforge.services.storage import Storage

# Metrics that must match the run's reported holdout (within this tolerance).
METRIC_TOLERANCE = 1e-6


class ModelServiceError(ValueError):
    """Raised for expected, user-facing problems; the router maps these to 4xx."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def _storage() -> Storage:
    return Storage(get_settings().storage_dir)


def _owned_dataset(db: Session, owner_id: str, dataset_id: str) -> Dataset:
    dataset = db.scalar(
        select(Dataset).where(Dataset.id == dataset_id, Dataset.owner_id == owner_id)
    )
    if dataset is None:
        raise ModelServiceError("Dataset not found", status=404)
    return dataset


def owned_model(db: Session, owner_id: str, model_id: str) -> SavedModel:
    model = db.scalar(
        select(SavedModel).where(SavedModel.id == model_id, SavedModel.owner_id == owner_id)
    )
    if model is None:
        raise ModelServiceError("Model not found", status=404)
    return model


def _prediction_artifact(db: Session, run: Run, position: int) -> dict[str, Any]:
    artifact = db.scalar(
        select(Artifact).where(Artifact.run_id == run.id, Artifact.position == position)
    )
    if artifact is None:
        raise ModelServiceError("Artifact not found", status=404)
    payload = artifact.payload_json or {}
    if artifact.type != "stat" or payload.get("method") != "predict":
        raise ModelServiceError("That result is not a prediction model", status=422)
    return payload


def _source_sql(db: Session, run: Run, data_source: str) -> str:
    # A SQL step is persisted as a TableArtifact whose name is the step name; the
    # prediction StatArtifact points at that name through its data_source field.
    for artifact in db.scalars(select(Artifact).where(Artifact.run_id == run.id)):
        payload = artifact.payload_json or {}
        if artifact.type == "table" and artifact.name == data_source:
            return payload.get("sql") or ""
    raise ModelServiceError("The prediction's data query could not be found", status=422)


def _run_source(catalog: Any, sql: str) -> pd.DataFrame:
    guarded = guard_query(sql)
    return catalog.query(guarded.sql, timeout_seconds=get_settings().query_timeout_seconds)


def _blocking_failures(version: DatasetVersion) -> list[str]:
    validation = version.validation_json or {}
    results = validation.get("results") or []
    messages: list[str] = []
    for item in results if isinstance(results, list) else []:
        if item.get("severity") == "blocking" and item.get("status") in {"failed", "error"}:
            messages.append(item.get("message") or item.get("name") or "a blocking rule failed")
    return messages


def create_saved_model(
    db: Session, owner_id: str, run_id: str, position: int, name: str
) -> SavedModel:
    """Retrain and store the model behind a completed prediction result card.

    Re-runs the prediction's row-level SQL on the run's pinned, ready version through
    the guard, trains the final model deterministically, and stores the model file
    plus metadata. If the retrained holdout metric differs from the run's reported
    metric, the model is still saved but a warning is recorded.
    """
    run = db.scalar(select(Run).where(Run.id == run_id, Run.owner_id == owner_id))
    if run is None:
        raise ModelServiceError("Run not found", status=404)
    if run.status != "completed":
        raise ModelServiceError("The run is not complete", status=422)
    if not run.dataset_version_id:
        raise ModelServiceError("The run is not pinned to a dataset version", status=422)
    version = db.get(DatasetVersion, run.dataset_version_id)
    if version is None or version.state != "ready" or version.owner_id != owner_id:
        raise ModelServiceError("The pinned dataset version is not ready", status=422)
    dataset = _owned_dataset(db, owner_id, version.dataset_id)

    payload = _prediction_artifact(db, run, position)
    target = payload.get("y")
    features = list(payload.get("features") or [])
    date_column = payload.get("x") or None
    data_source = payload.get("data_source")
    sql = _source_sql(db, run, data_source)

    catalog = open_catalog(dataset, version_id=version.id)
    try:
        frame = _run_source(catalog, sql)
    finally:
        catalog.close()

    try:
        final = model_store.train_final(frame, target, features, date_column)
    except (PredictError, ModelStoreError) as error:
        raise ModelServiceError(str(error), status=422) from error

    warning = None
    reported = payload.get("statistic")
    trained_score = final.metrics.get("holdout_score")
    if reported is not None and trained_score is not None and abs(reported - trained_score) > 1e-4:
        warning = (
            "The retrained model's holdout score "
            f"({trained_score:.4g}) differs from the run's reported score ({reported:.4g}); "
            "the data behind the run may have changed."
        )

    model = SavedModel(
        owner_id=owner_id,
        dataset_id=dataset.id,
        dataset_version_id=version.id,
        run_id=run.id,
        artifact_position=position,
        name=name or f"{target} model",
        task=final.task,
        target=target,
        features_json=final.features,
        date_column=date_column,
        source_sql=sql,
        metrics_json={**final.metrics, **({"warning": warning} if warning else {})},
        profile_json={
            "task": final.task,
            "features": final.features,
            "numeric": final.numeric,
            "categorical": final.categorical,
            "classes": final.classes,
            "date_column": final.date_column,
            "profile": final.profile,
        },
        model_type=final.model_type,
        file_path="",
    )
    db.add(model)
    db.flush()

    directory = _storage().model_dir(owner_id, model.id)
    file_path = directory / "model.joblib"
    model_store.save(final, str(file_path))
    model.file_path = str(file_path.relative_to(_storage().root))
    db.commit()
    db.refresh(model)
    return model


def _load_final(model: SavedModel) -> tuple[Any, model_store.FinalModel]:
    profile = model.profile_json or {}
    estimator = model_store.load(str(_storage().root / model.file_path))
    final = model_store.FinalModel(
        estimator=estimator,
        task=model.task,
        target=model.target,
        features=list(model.features_json or []),
        numeric=list(profile.get("numeric") or []),
        categorical=list(profile.get("categorical") or []),
        date_column=model.date_column,
        model_type=model.model_type,
        metrics=dict(model.metrics_json or {}),
        profile=profile.get("profile") or {},
        classes=list(profile.get("classes") or []),
    )
    return estimator, final


def _available_columns(catalog: Any) -> set[str]:
    columns: set[str] = set()
    for table in catalog.table_names():
        try:
            frame = catalog.query(f'SELECT * FROM {table} LIMIT 0')
            columns.update(frame.columns)
        except Exception:  # noqa: BLE001 - best-effort column discovery
            continue
    return columns


def _needed_columns(model: SavedModel) -> list[str]:
    needed = list(model.features_json or [])
    if model.date_column:
        needed.append(model.date_column)
    needed.append(model.target)
    return needed

def score_model(db: Session, owner_id: str, model_id: str, version_id: str | None) -> dict[str, Any]:
    """Score a chosen ready version of the model's dataset and report drift.

    Blocked (422) when required features are missing; 409 when the version has
    blocking validation failures.
    """
    model = owned_model(db, owner_id, model_id)
    dataset = _owned_dataset(db, owner_id, model.dataset_id)
    chosen = version_id or dataset.current_version_id
    version = db.get(DatasetVersion, chosen) if chosen else None
    if version is None or version.owner_id != owner_id or version.dataset_id != dataset.id:
        raise ModelServiceError("Version not found", status=404)
    if version.state != "ready":
        raise ModelServiceError("Choose a ready dataset version to score", status=422)
    blocking = _blocking_failures(version)
    if blocking:
        raise ModelServiceError(
            "This version has blocking validation failures: " + "; ".join(blocking), status=409
        )

    estimator, final = _load_final(model)
    catalog = open_catalog(dataset, version_id=version.id)
    try:
        try:
            frame = _run_source(catalog, model.source_sql)
        except duckdb.Error as error:
            available = _available_columns(catalog)
            missing = [column for column in _needed_columns(model) if column not in available]
            if missing:
                raise ModelServiceError(
                    "The chosen version is missing required columns: " + ", ".join(missing),
                    status=422,
                ) from error
            raise ModelServiceError(f"The model's data query failed: {error}", status=422) from error
    finally:
        catalog.close()

    missing = [column for column in final.features if column not in frame.columns]
    if missing:
        raise ModelServiceError(
            "The chosen version is missing required feature columns: " + ", ".join(missing),
            status=422,
        )

    scored = model_store.score(estimator, final, frame)
    drift_report = model_store.drift(final.profile, frame)
    new_metrics = model_store.evaluate_new(estimator, final, frame)
    advice = model_store.recommendation(final, drift_report, new_metrics)

    directory = _storage().model_dir(owner_id, model.id)
    csv_path = directory / "scores-latest.csv"
    scored.to_csv(csv_path, index=False, quoting=csv.QUOTE_MINIMAL)

    preview = scored.head(200)
    return {
        "model_id": model.id,
        "version_id": version.id,
        "rows_scored": int(len(scored)),
        "preview_columns": list(preview.columns),
        "preview_rows": preview.astype(object).where(preview.notna(), None).to_dict("records"),
        "drift": drift_report,
        "new_data_metrics": new_metrics,
        "holdout_metrics": {
            key: value for key, value in final.metrics.items() if key != "warning"
        },
        "recommendation": advice,
    }


def latest_scores_path(db: Session, owner_id: str, model_id: str) -> str:
    model = owned_model(db, owner_id, model_id)
    path = _storage().model_dir(owner_id, model.id) / "scores-latest.csv"
    if not path.is_file():
        raise ModelServiceError("No scoring has been run for this model yet", status=404)
    return str(path)


def list_models(db: Session, owner_id: str, dataset_id: str) -> list[SavedModel]:
    _owned_dataset(db, owner_id, dataset_id)
    return list(
        db.scalars(
            select(SavedModel)
            .where(SavedModel.owner_id == owner_id, SavedModel.dataset_id == dataset_id)
            .order_by(SavedModel.created_at.desc())
        )
    )


def delete_model(db: Session, owner_id: str, model_id: str) -> None:
    model = owned_model(db, owner_id, model_id)
    _storage().delete_model(owner_id, model.id)
    db.delete(model)
    db.commit()


def delete_models_for_dataset(db: Session, owner_id: str, dataset_id: str) -> None:
    """Remove a dataset's models and their folders (used by dataset deletion)."""
    for model in db.scalars(
        select(SavedModel).where(SavedModel.owner_id == owner_id, SavedModel.dataset_id == dataset_id)
    ):
        _storage().delete_model(owner_id, model.id)
        db.delete(model)
