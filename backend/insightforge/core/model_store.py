"""Save prediction models, score new data and measure drift.

This is a tested method in the sense of the project's trust rules: the AI never
computes any number here. Training reuses ``predict.train_model`` (the same
screening, split and model choice as the "predict this column" feature), so a
saved model matches the run that produced it. Drift uses the Population Stability
Index (PSI) with fixed thresholds. Nothing in this module contacts an LLM.

All texts are ASCII so the CLI and the Windows console can print them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from insightforge.core.predict import (
    PredictError,
    _is_classification,
    prepare_frame,
    train_model,
)

# PSI bands (fixed).
PSI_STABLE = 0.1
PSI_MAJOR = 0.25
# Empty-bin epsilon so PSI stays finite.
EPSILON = 1e-4
# Retraining margins.
CLASSIFICATION_DROP = 0.05
REGRESSION_MAE_RATIO = 1.2

RETRAIN = "retrain recommended"
NO_RETRAIN = "no retraining signal"


class ModelStoreError(ValueError):
    pass


@dataclass
class FinalModel:
    """A fitted pipeline plus the metadata needed to score and explain it.

    ``estimator`` is the scikit-learn pipeline chosen by ``train_model``. It is what
    joblib writes to disk; the rest is stored as JSON in the database.
    """

    estimator: Any
    task: str  # 'classification' or 'regression'
    target: str
    features: list[str]
    numeric: list[str]
    categorical: list[str]
    date_column: str | None
    model_type: str
    metrics: dict[str, float]
    profile: dict[str, Any]
    classes: list[str] = field(default_factory=list)


def _metrics_from_trained(trained: Any) -> dict[str, float]:
    """Holdout metrics for the chosen model, matching predict's leaderboard.

    Uses the same scikit-learn metrics predict reports so the numbers a saved model
    stores equal the run's reported holdout metrics.
    """
    from sklearn.metrics import r2_score, roc_auc_score

    board = {row["model"]: row for row in trained.board}
    best = board[trained.best_name]
    baseline = board[trained.baseline_name]
    metrics: dict[str, float] = {
        "holdout_score": float(best["holdout_score"]),
        "baseline_score": float(baseline["holdout_score"]),
    }
    if trained.classify:
        metrics["balanced_accuracy"] = float(best["holdout_score"])
        if trained.y.nunique() == 2 and hasattr(trained.best_model, "predict_proba"):
            probabilities = trained.best_model.predict_proba(trained.x_test)[:, 1]
            positive = trained.best_model.classes_[1]
            metrics["roc_auc"] = float(
                roc_auc_score((trained.y_test == positive).astype(int), probabilities)
            )
    else:
        metrics["mae"] = float(best["holdout_score"])
        metrics["r_squared"] = float(r2_score(trained.y_test, trained.best_model.predict(trained.x_test)))
    return metrics


def training_profile(frame: pd.DataFrame, features: list[str]) -> dict[str, Any]:
    """Capture the training distribution used later to measure drift.

    For each numeric feature: decile edges (open-ended). For each categorical
    feature: category frequencies. For every feature: the missing share. Deciles use
    the training data so the same bins apply to new data.
    """
    profile: dict[str, Any] = {"n": int(len(frame)), "numeric": {}, "categorical": {}, "missing": {}}
    for column in features:
        if column not in frame.columns:
            continue
        values = frame[column]
        profile["missing"][column] = float(values.isna().mean())
        if pd.api.types.is_numeric_dtype(values):
            clean = pd.to_numeric(values, errors="coerce").dropna()
            if clean.empty:
                profile["numeric"][column] = {"edges": [], "weights": []}
                continue
            quantiles = np.unique(np.quantile(clean, np.linspace(0.1, 0.9, 9)))
            edges = [float(edge) for edge in quantiles]
            counts, _ = np.histogram(clean, bins=[-np.inf, *edges, np.inf])
            weights = (counts / counts.sum()).tolist()
            profile["numeric"][column] = {"edges": edges, "weights": [float(w) for w in weights]}
        else:
            freq = values.astype("object").where(values.notna(), other="__missing__")
            counts = freq.value_counts(normalize=True)
            profile["categorical"][column] = {
                str(category): float(share) for category, share in counts.items()
            }
    return profile


def _numeric_bins(edges: list[float], values: pd.Series) -> np.ndarray:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if clean.empty:
        return np.array([])
    counts, _ = np.histogram(clean, bins=[-np.inf, *edges, np.inf])
    return counts / counts.sum()


def _psi(expected: np.ndarray, actual: np.ndarray) -> float:
    """Population Stability Index between two share vectors.

    PSI = sum( (actual - expected) * ln(actual / expected) ), with an epsilon on
    empty bins so the logarithm stays finite. Identical distributions give 0.
    """
    expected = np.where(expected <= 0, EPSILON, expected)
    actual = np.where(actual <= 0, EPSILON, actual)
    return float(np.sum((actual - expected) * np.log(actual / expected)))


def _band(psi: float) -> str:
    if psi < PSI_STABLE:
        return "stable"
    if psi <= PSI_MAJOR:
        return "moderate shift"
    return "major shift"


def drift(profile: dict[str, Any], frame: pd.DataFrame) -> dict[str, Any]:
    """Per-feature PSI and bands, plus unseen-category and missing-value change.

    Numeric features are binned at the training deciles (open ends); categorical
    features are compared by category with an "other/new" bucket. Returns a report
    with a per-feature list and overall shares.
    """
    features_report: list[dict[str, Any]] = []
    unseen_rows_mask = pd.Series(False, index=frame.index)
    numeric_profiles = profile.get("numeric", {})
    categorical_profiles = profile.get("categorical", {})
    missing_profiles = profile.get("missing", {})

    for column, spec in numeric_profiles.items():
        edges = spec.get("edges", [])
        expected = np.array(spec.get("weights", []))
        if column not in frame.columns or expected.size == 0:
            continue
        actual = _numeric_bins(edges, frame[column])
        if actual.size == 0:
            continue
        psi = _psi(expected, actual)
        missing_now = float(frame[column].isna().mean())
        features_report.append(
            {
                "feature": column,
                "kind": "numeric",
                "psi": round(psi, 4),
                "band": _band(psi),
                "unseen_share": 0.0,
                "missing_change": round(missing_now - float(missing_profiles.get(column, 0.0)), 4),
            }
        )

    for column, expected_map in categorical_profiles.items():
        if column not in frame.columns:
            continue
        values = frame[column].astype("object").where(frame[column].notna(), other="__missing__")
        actual_counts = values.astype(str).value_counts(normalize=True)
        known = set(expected_map)
        categories = sorted(known | set(actual_counts.index) - {"__other__"})
        expected_vector, actual_vector = [], []
        other_expected, other_actual = 0.0, 0.0
        for category in categories:
            if category in expected_map:
                expected_vector.append(expected_map[category])
                actual_vector.append(float(actual_counts.get(category, 0.0)))
            else:
                other_actual += float(actual_counts.get(category, 0.0))
        expected_vector.append(max(other_expected, 0.0))
        actual_vector.append(other_actual)
        psi = _psi(np.array(expected_vector), np.array(actual_vector))
        seen = known | {"__missing__"}
        unseen = ~values.astype(str).isin(seen)
        unseen_rows_mask = unseen_rows_mask | unseen.reindex(frame.index, fill_value=False)
        missing_now = float(frame[column].isna().mean())
        features_report.append(
            {
                "feature": column,
                "kind": "categorical",
                "psi": round(psi, 4),
                "band": _band(psi),
                "unseen_share": round(float(unseen.mean()), 4),
                "missing_change": round(missing_now - float(missing_profiles.get(column, 0.0)), 4),
            }
        )

    features_report.sort(key=lambda row: row["psi"], reverse=True)
    return {
        "features": features_report,
        "rows": int(len(frame)),
        "unseen_row_share": round(float(unseen_rows_mask.mean()) if len(frame) else 0.0, 4),
        "max_psi": max((row["psi"] for row in features_report), default=0.0),
    }


def train_final(
    frame: pd.DataFrame,
    target: str,
    features: list[str] | None = None,
    date_column: str | None = None,
) -> FinalModel:
    """Train the deterministic final model and capture its training profile."""
    trained = train_model(frame, target, features, date_column)
    metrics = _metrics_from_trained(trained)
    profile = training_profile(prepare_frame(frame, target), trained.kept)
    classes = [str(c) for c in getattr(trained.best_model, "classes_", [])] if trained.classify else []
    return FinalModel(
        estimator=trained.best_model,
        task="classification" if trained.classify else "regression",
        target=target,
        features=trained.kept,
        numeric=trained.numeric,
        categorical=trained.categorical,
        date_column=date_column,
        model_type=trained.best_name,
        metrics=metrics,
        profile=profile,
        classes=classes,
    )


def save(model: FinalModel, path: str) -> None:
    """Write the fitted estimator to ``path`` with joblib (ships with scikit-learn)."""
    import joblib

    joblib.dump(model.estimator, path)


def load(path: str) -> Any:
    """Load a fitted estimator written by :func:`save`."""
    import joblib

    return joblib.load(path)


def _require_features(model: FinalModel, frame: pd.DataFrame) -> list[str]:
    missing = [column for column in model.features if column not in frame.columns]
    return missing


def score(estimator: Any, model: FinalModel, frame: pd.DataFrame) -> pd.DataFrame:
    """Predict for every row and, for binary classification, add a probability.

    Returns a copy of the feature columns with a ``prediction`` column (and
    ``probability`` when the model is a binary classifier).
    """
    missing = _require_features(model, frame)
    if missing:
        raise ModelStoreError(
            "The new data is missing required feature columns: " + ", ".join(missing)
        )
    features = frame[model.features].copy()
    predictions = estimator.predict(features)
    result = features.copy()
    result["prediction"] = predictions
    if model.task == "classification" and len(model.classes) == 2 and hasattr(estimator, "predict_proba"):
        positive = estimator.classes_[1]
        result["probability"] = estimator.predict_proba(features)[:, 1]
        result.attrs["positive_class"] = str(positive)
    return result


def evaluate_new(estimator: Any, model: FinalModel, frame: pd.DataFrame) -> dict[str, float] | None:
    """Score accuracy on new data when the target column is present.

    Reports the same holdout metrics as training (balanced accuracy / ROC AUC for
    classification; MAE / R2 for regression) so they compare directly.
    """
    from sklearn.metrics import (
        balanced_accuracy_score,
        mean_absolute_error,
        r2_score,
        roc_auc_score,
    )

    if model.target not in frame.columns:
        return None
    data = frame.dropna(subset=[model.target])
    missing = _require_features(model, data)
    if missing or data.empty:
        return None
    features = data[model.features]
    if model.task == "classification":
        y_true = data[model.target].astype(str)
        y_pred = estimator.predict(features)
        result = {"balanced_accuracy": float(balanced_accuracy_score(y_true, y_pred))}
        if len(model.classes) == 2 and hasattr(estimator, "predict_proba"):
            positive = estimator.classes_[1]
            probabilities = estimator.predict_proba(features)[:, 1]
            if y_true.nunique() == 2:
                result["roc_auc"] = float(roc_auc_score((y_true == str(positive)).astype(int), probabilities))
        return result
    y_true = data[model.target].astype(float)
    y_pred = estimator.predict(features)
    return {
        "mae": float(mean_absolute_error(y_true, y_pred)),
        "r_squared": float(r2_score(y_true, y_pred)),
    }


def recommendation(
    model: FinalModel, drift_report: dict[str, Any], new_metrics: dict[str, float] | None
) -> dict[str, Any]:
    """Decide, in code, whether retraining is worth suggesting.

    Retraining is recommended when any feature has PSI above the major-shift
    threshold, or the new-data score is worse than the training holdout by more than
    a fixed margin. Never claims the model is right or that anything causes anything.
    """
    reasons: list[str] = []
    max_psi = drift_report.get("max_psi", 0.0)
    if max_psi > PSI_MAJOR:
        worst = max(drift_report["features"], key=lambda row: row["psi"])
        reasons.append(
            f"feature '{worst['feature']}' shows a major distribution shift (PSI {worst['psi']:.2f})"
        )
    # An accuracy drop is only trusted as a retraining signal when the data has
    # actually shifted; on unchanged data a small gap is holdout-estimate noise.
    if new_metrics and max_psi >= PSI_STABLE:
        if model.task == "classification":
            trained = model.metrics.get("balanced_accuracy")
            now = new_metrics.get("balanced_accuracy")
            if trained is not None and now is not None and (trained - now) > CLASSIFICATION_DROP:
                reasons.append(
                    f"balanced accuracy fell from {trained:.2f} to {now:.2f} on the new data"
                )
        else:
            trained = model.metrics.get("mae")
            now = new_metrics.get("mae")
            if trained and now is not None and now > trained * REGRESSION_MAE_RATIO:
                reasons.append(f"the typical error rose from {trained:.3g} to {now:.3g} on the new data")
    verdict = RETRAIN if reasons else NO_RETRAIN
    if reasons:
        detail = "Retraining is worth considering because " + "; ".join(reasons) + "."
    else:
        detail = "No strong drift or accuracy drop was detected; there is no retraining signal."
    return {"verdict": verdict, "detail": detail, "reasons": reasons}


__all__ = [
    "FinalModel",
    "ModelStoreError",
    "PredictError",
    "drift",
    "evaluate_new",
    "load",
    "recommendation",
    "save",
    "score",
    "train_final",
    "training_profile",
    "_is_classification",
]
