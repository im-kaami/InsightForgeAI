from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

MIN_ROWS = 60
MAX_ROWS = 50_000
MAX_CATEGORIES = 20
LEAK_THRESHOLD = 0.98


class PredictError(ValueError):
    pass


def _fmt(value: float) -> str:
    return f"{value:,.0f}" if abs(value) >= 1000 else f"{value:,.3g}"


def _is_classification(target: pd.Series) -> bool:
    if not pd.api.types.is_numeric_dtype(target):
        return True
    values = target.dropna().unique()
    return len(values) <= 10 and np.all(np.equal(np.mod(values, 1), 0))


def _screen_features(
    data: pd.DataFrame, target: str, requested: list[str], date_column: str | None
) -> tuple[list[str], list[str], list[str]]:
    candidates = requested or [column for column in data.columns if column not in {target, date_column}]
    kept, dropped, leaks = [], [], []
    y = data[target]
    classify = _is_classification(y)
    for column in candidates:
        if column not in data.columns:
            raise PredictError(
                f"Column {column!r} is not in the result; available: {', '.join(map(str, data.columns))}"
            )
        values = data[column]
        unique = values.nunique(dropna=True)
        if unique <= 1:
            dropped.append(f"{column} (constant)")
            continue
        numeric = pd.api.types.is_numeric_dtype(values)
        if (not numeric or pd.api.types.is_integer_dtype(values)) and unique > 0.95 * len(values):
            dropped.append(f"{column} (looks like an ID)")
            continue
        if not numeric and unique > 50:
            dropped.append(f"{column} (too many distinct text values)")
            continue
        if pd.api.types.is_datetime64_any_dtype(values):
            dropped.append(f"{column} (a date; use it for a time-based check instead)")
            continue
        if classify:
            purity = data.groupby(values.astype(str))[target].agg(
                lambda part: part.value_counts(normalize=True).iloc[0]
            )
            weights = values.astype(str).value_counts(normalize=True)
            if (
                unique < len(values) * 0.5
                and float((purity * weights.reindex(purity.index)).sum()) > LEAK_THRESHOLD
            ):
                leaks.append(column)
                continue
        elif numeric:
            correlation = pd.concat([values, y], axis=1).dropna().corr().iloc[0, 1]
            if abs(correlation) > LEAK_THRESHOLD:
                leaks.append(column)
                continue
        kept.append(column)
    return kept, dropped, leaks


@dataclass
class TrainedModel:
    """Result of screening, splitting and choosing a model on a prepared frame.

    Holds the fitted best pipeline, the fitted baseline, the kept feature list and
    everything ``predict`` needs to build its report. ``model_store`` reuses the same
    computation to save and reload models.
    """

    classify: bool
    target: str
    date_column: str | None
    kept: list[str]
    numeric: list[str]
    categorical: list[str]
    dropped: list[str]
    leaks: list[str]
    best_name: str
    baseline_name: str
    best_model: Any
    baseline_model: Any
    board: list[dict[str, Any]]
    split: str
    folds: int
    metric: str
    scoring: str
    n: int
    x_test: pd.DataFrame
    y_test: pd.Series
    y: pd.Series


def prepare_frame(frame: pd.DataFrame, target: str) -> pd.DataFrame:
    """Drop rows without the target, cap the row count and check the minimum.

    Deterministic: the row cap uses a fixed random_state so training on the same
    frame always sees the same rows.
    """
    if target not in frame.columns:
        raise PredictError(
            f"Column {target!r} is not in the result; available: {', '.join(map(str, frame.columns))}"
        )
    data = frame.dropna(subset=[target]).copy()
    if len(data) > MAX_ROWS:
        data = data.sample(MAX_ROWS, random_state=42)
    if len(data) < MIN_ROWS:
        raise PredictError(
            f"A prediction model needs at least {MIN_ROWS} rows with the target; found {len(data)}"
        )
    classify = _is_classification(data[target])
    if classify and data[target].nunique() < 2:
        raise PredictError(f"{target} has only one value, so there is nothing to predict")
    return data


def train_model(
    frame: pd.DataFrame, target: str, features: list[str] | None = None, date_column: str | None = None
) -> TrainedModel:
    """Screen features, split, cross-validate model choices and fit the best one.

    This is the shared core of ``predict`` and ``model_store.train_final``. It is
    deterministic (fixed seeds) so a reloaded model reproduces training predictions.
    """
    from sklearn.compose import ColumnTransformer
    from sklearn.dummy import DummyClassifier, DummyRegressor
    from sklearn.ensemble import (
        HistGradientBoostingClassifier,
        HistGradientBoostingRegressor,
        RandomForestClassifier,
        RandomForestRegressor,
    )
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression, Ridge
    from sklearn.metrics import balanced_accuracy_score, mean_absolute_error
    from sklearn.model_selection import cross_val_score, train_test_split
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import OneHotEncoder, StandardScaler

    data = prepare_frame(frame, target)
    classify = _is_classification(data[target])
    kept, dropped, leaks = _screen_features(data, target, list(features or []), date_column)
    if not kept:
        raise PredictError("No usable predictor columns remain after removing IDs, constants and leaks")
    numeric = [column for column in kept if pd.api.types.is_numeric_dtype(data[column])]
    categorical = [column for column in kept if column not in numeric]
    x, y = data[kept], data[target].astype(str) if classify else data[target].astype(float)
    if date_column and date_column in data.columns:
        order = pd.to_datetime(data[date_column], errors="coerce").sort_values(kind="stable").index
        cut = int(len(order) * 0.8)
        train_index, test_index = order[:cut], order[cut:]
        x_train, x_test, y_train, y_test = (
            x.loc[train_index],
            x.loc[test_index],
            y.loc[train_index],
            y.loc[test_index],
        )
        split = f"time-based: trained on the earliest 80% by {date_column}, tested on the latest 20%"
    else:
        stratify = y if classify and y.value_counts().min() >= 2 else None
        x_train, x_test, y_train, y_test = train_test_split(
            x, y, test_size=0.2, random_state=42, stratify=stratify
        )
        split = "random 80/20 split with a fixed seed" + (" (stratified)" if stratify is not None else "")

    def prepare(scale: bool) -> ColumnTransformer:
        numeric_steps = [SimpleImputer(strategy="median")] + ([StandardScaler()] if scale else [])
        return ColumnTransformer(
            [
                ("number", make_pipeline(*numeric_steps), numeric),
                (
                    "category",
                    make_pipeline(
                        SimpleImputer(strategy="constant", fill_value="missing"),
                        OneHotEncoder(
                            handle_unknown="infrequent_if_exist",
                            max_categories=MAX_CATEGORIES,
                            sparse_output=False,
                        ),
                    ),
                    categorical,
                ),
            ]
        )

    if classify:
        candidates = {
            "baseline (most common class)": make_pipeline(
                prepare(False), DummyClassifier(strategy="most_frequent")
            ),
            "logistic regression": make_pipeline(prepare(True), LogisticRegression(max_iter=2000)),
            "random forest": make_pipeline(
                prepare(False), RandomForestClassifier(n_estimators=200, random_state=42)
            ),
            "gradient boosting": make_pipeline(
                prepare(False), HistGradientBoostingClassifier(random_state=42)
            ),
        }
        scoring, metric = "balanced_accuracy", "balanced accuracy"
    else:
        candidates = {
            "baseline (average)": make_pipeline(prepare(False), DummyRegressor()),
            "ridge regression": make_pipeline(prepare(True), Ridge()),
            "random forest": make_pipeline(
                prepare(False), RandomForestRegressor(n_estimators=200, random_state=42)
            ),
            "gradient boosting": make_pipeline(
                prepare(False), HistGradientBoostingRegressor(random_state=42)
            ),
        }
        scoring, metric = "neg_mean_absolute_error", "mean absolute error"
    folds = 3 if len(x_train) >= 300 else min(5, max(2, int(y_train.value_counts().min()) if classify else 5))
    board = []
    for name, model in candidates.items():
        scores = cross_val_score(model, x_train, y_train, cv=folds, scoring=scoring)
        board.append({"model": name, "cv_score": float(np.mean(scores) * (-1 if not classify else 1))})
    better = max if classify else min
    best = better(
        (row for row in board if not row["model"].startswith("baseline")), key=lambda row: row["cv_score"]
    )
    baseline_name = next(row["model"] for row in board if row["model"].startswith("baseline"))
    fitted = {name: candidates[name].fit(x_train, y_train) for name in (best["model"], baseline_name)}
    for row in board:
        if row["model"] in fitted:
            predicted = fitted[row["model"]].predict(x_test)
            row["holdout_score"] = float(
                balanced_accuracy_score(y_test, predicted)
                if classify
                else mean_absolute_error(y_test, predicted)
            )
    return TrainedModel(
        classify=classify,
        target=target,
        date_column=date_column,
        kept=kept,
        numeric=numeric,
        categorical=categorical,
        dropped=dropped,
        leaks=leaks,
        best_name=best["model"],
        baseline_name=baseline_name,
        best_model=fitted[best["model"]],
        baseline_model=fitted[baseline_name],
        board=board,
        split=split,
        folds=folds,
        metric=metric,
        scoring=scoring,
        n=len(data),
        x_test=x_test,
        y_test=y_test,
        y=y,
    )


def predict(
    frame: pd.DataFrame, target: str, features: list[str] | None = None, date_column: str | None = None
) -> dict[str, Any]:
    from sklearn.inspection import permutation_importance
    from sklearn.metrics import r2_score, roc_auc_score

    trained = train_model(frame, target, features, date_column)
    classify = trained.classify
    kept = trained.kept
    board = trained.board
    best_name = trained.best_name
    best_model = trained.best_model

    extra = {}
    if classify and trained.y.nunique() == 2 and hasattr(best_model, "predict_proba"):
        probabilities = best_model.predict_proba(trained.x_test)[:, 1]
        positive = best_model.classes_[1]
        extra["roc_auc"] = float(roc_auc_score((trained.y_test == positive).astype(int), probabilities))
    if not classify:
        extra["r_squared"] = float(r2_score(trained.y_test, best_model.predict(trained.x_test)))
    importance = permutation_importance(
        best_model, trained.x_test, trained.y_test, n_repeats=5, random_state=42, scoring=trained.scoring
    )
    ranked = sorted(
        (
            {"feature": column, "importance": float(mean), "spread": float(std)}
            for column, mean, std in zip(
                kept, importance.importances_mean, importance.importances_std, strict=True
            )
        ),
        key=lambda row: row["importance"],
        reverse=True,
    )
    best_score = next(row["holdout_score"] for row in board if row["model"] == best_name)
    base_score = next(row["holdout_score"] for row in board if row["model"] == trained.baseline_name)
    gain = (
        (best_score - base_score)
        if classify
        else (base_score - best_score) / base_score
        if base_score
        else 0.0
    )
    checks = [
        f"Problem type: {'classification' if classify else 'regression'} of {target}; "
        f"{len(kept)} predictors.",
        f"Check: {trained.split}. Model choice used {trained.folds}-fold cross-validation on the "
        "training part only.",
        "Leaderboard ("
        + trained.metric
        + "): "
        + "; ".join(f"{row['model']} {_fmt(row['cv_score'])}" for row in board)
        + ".",
    ]
    if trained.dropped:
        checks.append(f"Left out: {', '.join(trained.dropped[:8])}.")
    cautions = ["Importance shows what the model relies on, not what causes the outcome."]
    if trained.leaks:
        cautions.append(
            f"{', '.join(trained.leaks)} predicted {target} almost perfectly and were left out; they may "
            "only be known after the outcome (leakage)."
        )
    if trained.n < 500:
        cautions.append(f"Only {trained.n} rows; scores may change noticeably with more data.")
    if (classify and gain < 0.05) or (not classify and gain < 0.05):
        cautions.append("The best model is barely better than the baseline; its predictions add little.")
    top = ", ".join(row["feature"] for row in ranked[:3] if row["importance"] > 0) or "no single column"
    comparison = (
        f"balanced accuracy {best_score:.0%} against {base_score:.0%} for always guessing the most "
        "common class"
        if classify
        else f"a typical error of {_fmt(best_score)} against {_fmt(base_score)} for always guessing "
        "the average"
    )
    return {
        "test": f"Prediction model ({best_name})",
        "statistic": best_score,
        "p_value": None,
        "groups": board,
        "importance": ranked[:10],
        "checks": checks,
        "cautions": cautions,
        "interpretation": (
            f"The {best_name} model predicts {target} on held-out rows with {comparison}. "
            f"It relies most on {top}."
            + (f" ROC AUC {extra['roc_auc']:.2f}." if "roc_auc" in extra else "")
            + (f" R-squared {extra['r_squared']:.2f}." if "r_squared" in extra else "")
        ),
        "n": trained.n,
        "features": kept,
    }
