"""Explain one prediction of a saved model with Shapley values (SHAP).

A tested method: the AI never computes any number here. For one row of input
values, each feature gets a contribution so that

    reference + sum(contributions) == explained output

holds exactly (SHAP's local accuracy). The reference is the average output over a
fixed sample of the model's training rows. Features are explained in their
original form (a text column is one feature, not one column per category), by
encoding text values as codes that are decoded back before the model is called.

With at most ``EXACT_LIMIT`` features the exact Shapley values are computed
(``shap.explainers.Exact``); above that a seeded permutation estimate is used
(``shap.explainers.Permutation``), which keeps local accuracy but approximates the
split between features. Contributions describe how this model reacts to the
inputs; they do not show that a feature causes the outcome.

All texts are ASCII so the CLI and the Windows console can print them.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

# Exact Shapley values up to this many features (2**8 coalitions).
EXACT_LIMIT = 8
# Training rows used as the reference ("background") sample.
BACKGROUND_ROWS = 50
SEED = 42
MISSING_CODE = -1.0

CAUTIONS = [
    "Contributions explain how this model reached its output for this row; they do not show that "
    "a feature causes the outcome.",
    "The reference is the model's average output over a sample of its training rows; a different "
    "reference gives different contributions.",
]


class ExplainError(ValueError):
    pass


def background_sample(frame: pd.DataFrame, features: list[str], target: str | None = None) -> pd.DataFrame:
    """A fixed, seeded sample of training rows used as the reference."""
    missing = [column for column in features if column not in frame.columns]
    if missing:
        raise ExplainError("The training data is missing feature columns: " + ", ".join(missing))
    data = frame.dropna(subset=[target]) if target and target in frame.columns else frame
    data = data[features]
    if data.empty:
        raise ExplainError("There are no training rows to use as a reference")
    if len(data) > BACKGROUND_ROWS:
        data = data.sample(BACKGROUND_ROWS, random_state=SEED)
    return data.reset_index(drop=True)


def _coerce_row(values: dict[str, Any], features: list[str], numeric: set[str]) -> dict[str, Any]:
    missing = [column for column in features if column not in values]
    if missing:
        raise ExplainError("Values are missing for: " + ", ".join(missing))
    row: dict[str, Any] = {}
    for column in features:
        value = values[column]
        if value is None or (isinstance(value, float) and math.isnan(value)) or value == "":
            row[column] = np.nan if column in numeric else None
        elif column in numeric:
            try:
                row[column] = float(value)
            except (TypeError, ValueError) as error:
                raise ExplainError(f"{column} needs a number, got {value!r}") from error
        else:
            row[column] = str(value)
    return row


class _Codec:
    """Turns a mixed frame into a float matrix and back (text columns as codes)."""

    def __init__(self, features: list[str], numeric: set[str], frames: list[pd.DataFrame]):
        self.features = features
        self.numeric = numeric
        self.categories: dict[str, list[str]] = {}
        for column in features:
            if column in numeric:
                continue
            seen: set[str] = set()
            for frame in frames:
                seen.update(str(v) for v in frame[column].dropna().tolist())
            self.categories[column] = sorted(seen)

    def encode(self, frame: pd.DataFrame) -> np.ndarray:
        columns = []
        for column in self.features:
            if column in self.numeric:
                columns.append(pd.to_numeric(frame[column], errors="coerce").to_numpy(dtype=float))
            else:
                lookup = {value: float(i) for i, value in enumerate(self.categories[column])}
                columns.append(
                    np.array(
                        [MISSING_CODE if pd.isna(v) else lookup[str(v)] for v in frame[column]],
                        dtype=float,
                    )
                )
        return np.column_stack(columns)

    def decode(self, matrix: np.ndarray) -> pd.DataFrame:
        data: dict[str, Any] = {}
        for index, column in enumerate(self.features):
            values = matrix[:, index]
            if column in self.numeric:
                data[column] = values.astype(float)
            else:
                names = self.categories[column]
                data[column] = pd.Series(
                    [None if code < 0 else names[int(round(code))] for code in values], dtype="object"
                )
        return pd.DataFrame(data, columns=self.features)


def _output(estimator: Any, task: str, row: pd.DataFrame) -> tuple[str, int | None]:
    """Choose what is explained: a probability for classifiers, the value for regression."""
    if task != "classification":
        return "predicted value", None
    if not hasattr(estimator, "predict_proba"):
        raise ExplainError("This model does not give probabilities, so it cannot be explained")
    model_classes = [str(c) for c in estimator.classes_]
    if len(model_classes) == 2:
        return f"probability of '{model_classes[1]}'", 1
    predicted = str(estimator.predict(row)[0])
    return f"probability of '{predicted}' (the predicted class)", model_classes.index(predicted)


def explain_row(
    estimator: Any,
    features: list[str],
    numeric: list[str],
    task: str,
    values: dict[str, Any],
    background: pd.DataFrame,
) -> dict[str, Any]:
    """Shapley contributions of each feature to one prediction.

    ``values`` maps every feature to its value for the row. ``background`` holds
    training rows (see :func:`background_sample`). Returns the reference, the
    explained output, the prediction, per-feature contributions sorted by size, and
    the gap between reference + contributions and the output (a check that should
    be zero up to rounding).
    """
    import shap

    numeric_set = set(numeric)
    row_values = _coerce_row(values, features, numeric_set)
    row = pd.DataFrame([row_values], columns=features)
    for column in features:
        if column not in numeric_set:
            row[column] = row[column].astype("object")
    codec = _Codec(features, numeric_set, [background, row])
    background_matrix = codec.encode(background)
    row_matrix = codec.encode(row)
    explained, class_index = _output(estimator, task, row)

    def model_fn(matrix: np.ndarray) -> np.ndarray:
        frame = codec.decode(np.atleast_2d(matrix))
        if class_index is None:
            return np.asarray(estimator.predict(frame), dtype=float)
        return np.asarray(estimator.predict_proba(frame)[:, class_index], dtype=float)

    masker = shap.maskers.Independent(background_matrix, max_samples=len(background_matrix))
    if len(features) <= EXACT_LIMIT:
        explainer = shap.explainers.Exact(model_fn, masker)
        result = explainer(row_matrix, silent=True)
        algorithm = "exact Shapley values"
    else:
        explainer = shap.explainers.Permutation(model_fn, masker, seed=SEED)
        result = explainer(row_matrix, max_evals=max(500, 20 * len(features) + 1), silent=True)
        algorithm = "permutation estimate of Shapley values (seeded)"

    contributions = np.asarray(result.values, dtype=float)[0]
    reference = float(np.asarray(result.base_values, dtype=float).reshape(-1)[0])
    output = float(model_fn(row_matrix)[0])
    gap = output - (reference + float(contributions.sum()))
    prediction = estimator.predict(row)[0]
    rows = [
        {
            "feature": column,
            "value": None if pd.isna(row_values[column]) else row_values[column],
            "contribution": float(contributions[index]),
        }
        for index, column in enumerate(features)
    ]
    rows.sort(key=lambda item: abs(item["contribution"]), reverse=True)
    return {
        "explained": explained,
        "reference": reference,
        "output": output,
        "prediction": prediction.item() if hasattr(prediction, "item") else prediction,
        "contributions": rows,
        "additivity_gap": gap,
        "algorithm": algorithm,
        "background_rows": int(len(background)),
        "interpretation": _interpretation(rows, explained, reference, output),
        "cautions": list(CAUTIONS),
    }


def _fmt(value: float) -> str:
    return f"{value:,.0f}" if abs(value) >= 1000 else f"{value:.3g}"


def _interpretation(rows: list[dict[str, Any]], explained: str, reference: float, output: float) -> str:
    text = f"The model's {explained} is {_fmt(output)} for this row, against {_fmt(reference)} on average."
    top = [item for item in rows if abs(item["contribution"]) > 0][:3]
    if top:
        parts = []
        for item in top:
            direction = "raises" if item["contribution"] > 0 else "lowers"
            size = _fmt(abs(item["contribution"]))
            parts.append(f"{item['feature']} = {item['value']} {direction} it by {size}")
        text += " Largest effects: " + "; ".join(parts) + "."
    return text
