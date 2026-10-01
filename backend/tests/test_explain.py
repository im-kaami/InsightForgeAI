from itertools import combinations
from math import factorial

import numpy as np
import pandas as pd
import pytest
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from insightforge.core.explain import (
    BACKGROUND_ROWS,
    ExplainError,
    background_sample,
    explain_row,
)


def _frame(n=300, seed=0):
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        {
            "tenure": rng.integers(1, 60, n).astype(float),
            "monthly": rng.normal(50, 15, n).round(2),
            "plan": rng.choice(["basic", "plus", "pro"], n),
        }
    )


def _pipeline(model, numeric, categorical, scale=False):
    numeric_steps = [SimpleImputer(strategy="median")] + ([StandardScaler()] if scale else [])
    return make_pipeline(
        ColumnTransformer(
            [
                ("number", make_pipeline(*numeric_steps), numeric),
                (
                    "category",
                    make_pipeline(
                        SimpleImputer(strategy="constant", fill_value="missing"),
                        OneHotEncoder(handle_unknown="ignore", sparse_output=False),
                    ),
                    categorical,
                ),
            ]
        ),
        model,
    )


def _brute_force_shapley(f, x: pd.DataFrame, background: pd.DataFrame) -> np.ndarray:
    """Interventional Shapley values by enumerating every coalition (reference)."""
    features = list(x.columns)
    p = len(features)

    def value(subset):
        mixed = background.copy()
        for column in subset:
            mixed[column] = x.iloc[0][column]
        return float(np.mean(f(mixed)))

    phi = np.zeros(p)
    for i, feature in enumerate(features):
        others = [c for c in features if c != feature]
        for size in range(p):
            weight = factorial(size) * factorial(p - size - 1) / factorial(p)
            for subset in combinations(others, size):
                phi[i] += weight * (value((*subset, feature)) - value(subset))
    return phi


def test_linear_model_contributions_match_the_closed_form():
    # For a linear model with an independent reference, the Shapley value of feature
    # i is w_i * (x_i - mean of the reference), with w_i the effective coefficient.
    data = _frame()
    y = 3.0 * data["tenure"] - 2.0 * data["monthly"] + 10
    features = ["tenure", "monthly"]
    model = _pipeline(Ridge(alpha=1e-6), features, [], scale=True).fit(data[features], y)
    background = background_sample(data, features)
    values = {"tenure": 40.0, "monthly": 20.0}
    result = explain_row(model, features, features, "regression", values, background)
    scaler = model[0].named_transformers_["number"][-1]
    weights = model[-1].coef_ / scaler.scale_
    by_feature = {item["feature"]: item["contribution"] for item in result["contributions"]}
    for index, column in enumerate(features):
        expected = weights[index] * (values[column] - background[column].mean())
        assert by_feature[column] == pytest.approx(expected, rel=1e-6, abs=1e-6)
    assert result["reference"] == pytest.approx(float(model.predict(background).mean()), rel=1e-9)
    assert result["algorithm"] == "exact Shapley values"


def test_exact_values_match_a_brute_force_enumeration_with_a_text_feature():
    data = _frame()
    target = np.where(data["tenure"] < 20, "yes", "no")
    features = ["tenure", "monthly", "plan"]
    model = _pipeline(
        RandomForestClassifier(n_estimators=30, random_state=0), ["tenure", "monthly"], ["plan"]
    ).fit(data[features], target)
    background = background_sample(data, features)
    values = {"tenure": 12, "monthly": 70.5, "plan": "pro"}
    result = explain_row(model, features, ["tenure", "monthly"], "classification", values, background)
    positive = list(model.classes_).index("yes")
    reference = _brute_force_shapley(
        lambda frame: model.predict_proba(frame)[:, positive],
        pd.DataFrame([values])[features],
        background,
    )
    by_feature = {item["feature"]: item["contribution"] for item in result["contributions"]}
    for index, column in enumerate(features):
        assert by_feature[column] == pytest.approx(reference[index], abs=1e-9)
    assert result["explained"] == "probability of 'yes'"
    assert result["output"] == pytest.approx(model.predict_proba(pd.DataFrame([values]))[0, positive])


def test_contributions_add_up_to_the_output_with_many_features():
    rng = np.random.default_rng(3)
    n = 200
    data = pd.DataFrame({f"x{i}": rng.normal(size=n) for i in range(10)})
    data["group"] = rng.choice(["a", "b"], n)
    y = data["x0"] * 4 + data["x1"] - (data["group"] == "a") * 2
    features = [*[f"x{i}" for i in range(10)], "group"]
    numeric = [f"x{i}" for i in range(10)]
    model = _pipeline(RandomForestRegressor(n_estimators=30, random_state=0), numeric, ["group"])
    model.fit(data[features], y)
    background = background_sample(data, features)
    values = {**{c: float(data.iloc[0][c]) for c in numeric}, "group": "a"}
    first = explain_row(model, features, numeric, "regression", values, background)
    second = explain_row(model, features, numeric, "regression", values, background)
    assert first["algorithm"].startswith("permutation")
    assert abs(first["additivity_gap"]) < 1e-6
    total = first["reference"] + sum(item["contribution"] for item in first["contributions"])
    assert total == pytest.approx(float(model.predict(pd.DataFrame([values])[features])[0]), abs=1e-6)
    assert first["contributions"] == second["contributions"]  # seeded, so repeatable
    assert first["contributions"][0]["feature"] == "x0"


def test_missing_values_unseen_categories_and_bad_input():
    data = _frame()
    target = np.where(data["plan"] == "pro", "yes", "no")
    features = ["tenure", "monthly", "plan"]
    numeric = ["tenure", "monthly"]
    model = _pipeline(RandomForestClassifier(n_estimators=20, random_state=0), numeric, ["plan"])
    model.fit(data[features], target)
    background = background_sample(data, features)
    assert len(background) == BACKGROUND_ROWS
    result = explain_row(
        model, features, numeric, "classification",
        {"tenure": None, "monthly": 40, "plan": "enterprise"}, background,
    )
    assert abs(result["additivity_gap"]) < 1e-9
    assert next(i for i in result["contributions"] if i["feature"] == "tenure")["value"] is None
    with pytest.raises(ExplainError, match="missing"):
        explain_row(model, features, numeric, "classification", {"tenure": 1}, background)
    with pytest.raises(ExplainError, match="needs a number"):
        explain_row(
            model, features, numeric, "classification",
            {"tenure": "abc", "monthly": 1, "plan": "pro"}, background,
        )
    assert result["interpretation"].isascii()
