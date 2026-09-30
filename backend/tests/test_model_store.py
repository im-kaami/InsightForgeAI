import numpy as np
import pandas as pd
import pytest
from sklearn.metrics import balanced_accuracy_score, mean_absolute_error

from insightforge.core import model_store
from insightforge.core.model_store import (
    ModelStoreError,
    _psi,
    drift,
    evaluate_new,
    load,
    recommendation,
    save,
    score,
    train_final,
)


def _churn(n: int = 600, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    frame = pd.DataFrame(
        {
            "tenure": rng.integers(1, 60, n),
            "monthly": rng.normal(50, 15, n).round(2),
            "plan": rng.choice(["basic", "plus", "pro"], n),
        }
    )
    logit = -2 + 0.06 * (60 - frame.tenure) + (frame.plan == "basic") * 1.2
    frame["churned"] = np.where(rng.random(n) < 1 / (1 + np.exp(-logit)), "yes", "no")
    return frame


def _houses(n: int = 600, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    size = rng.normal(1500, 400, n)
    rooms = rng.integers(2, 7, n)
    frame = pd.DataFrame({"size": size.round(1), "rooms": rooms})
    frame["price"] = (size * 120 + rooms * 5000 + rng.normal(0, 10000, n)).round(2)
    return frame


def test_psi_matches_a_hand_computed_value():
    # expected shares e, actual shares a; PSI = sum((a-e)*ln(a/e))
    expected = np.array([0.5, 0.3, 0.2])
    actual = np.array([0.4, 0.4, 0.2])
    manual = sum((a - e) * np.log(a / e) for e, a in zip(expected, actual, strict=True))
    assert _psi(expected, actual) == pytest.approx(manual, abs=1e-9)


def test_identical_data_gives_psi_zero():
    frame = _churn()
    model = train_final(frame, "churned")
    report = drift(model.profile, model_store.prepare_frame(frame, "churned"))
    assert report["max_psi"] == pytest.approx(0.0, abs=1e-6)
    assert all(row["band"] == "stable" for row in report["features"])
    assert report["unseen_row_share"] == 0.0


def test_a_shifted_distribution_crosses_the_thresholds():
    frame = _houses()
    model = train_final(frame, "price")
    shifted = _houses(seed=1).copy()
    shifted["size"] = shifted["size"] + 1800  # push far beyond the training deciles
    report = drift(model.profile, shifted)
    size_row = next(row for row in report["features"] if row["feature"] == "size")
    assert size_row["psi"] > 0.25
    assert size_row["band"] == "major shift"


def test_new_categories_land_in_the_other_bucket():
    frame = _churn()
    model = train_final(frame, "churned")
    fresh = _churn(seed=2).copy()
    fresh.loc[fresh.index[:120], "plan"] = "enterprise"  # unseen category
    report = drift(model.profile, fresh)
    plan_row = next(row for row in report["features"] if row["feature"] == "plan")
    assert plan_row["unseen_share"] > 0.15
    assert report["unseen_row_share"] > 0.15


def test_a_reloaded_model_predicts_the_same_as_in_memory(tmp_path):
    frame = _churn()
    model = train_final(frame, "churned")
    path = tmp_path / "model.joblib"
    save(model, str(path))
    reloaded = load(str(path))
    fresh = _churn(seed=3)
    in_memory = score(model.estimator, model, fresh)["prediction"].tolist()
    from_disk = score(reloaded, model, fresh)["prediction"].tolist()
    assert in_memory == from_disk


def test_metrics_match_sklearn_on_a_holdout():
    frame = _churn()
    model = train_final(frame, "churned")
    # evaluate_new on the training frame reproduces balanced accuracy from sklearn directly
    scored = evaluate_new(model.estimator, model, frame)
    data = frame.dropna(subset=["churned"])
    expected = balanced_accuracy_score(
        data["churned"].astype(str), model.estimator.predict(data[model.features])
    )
    assert scored["balanced_accuracy"] == pytest.approx(expected, abs=1e-9)


def test_regression_metrics_match_sklearn():
    frame = _houses()
    model = train_final(frame, "price")
    scored = evaluate_new(model.estimator, model, frame)
    expected = mean_absolute_error(frame["price"], model.estimator.predict(frame[model.features]))
    assert scored["mae"] == pytest.approx(expected, abs=1e-6)


def test_score_blocks_when_a_feature_is_missing():
    frame = _churn()
    model = train_final(frame, "churned")
    fresh = _churn(seed=4).drop(columns=["monthly"])
    with pytest.raises(ModelStoreError) as error:
        score(model.estimator, model, fresh)
    assert "monthly" in str(error.value)


def test_recommendation_flags_major_shift_and_is_quiet_otherwise():
    frame = _houses()
    model = train_final(frame, "price")
    stable = drift(model.profile, model_store.prepare_frame(frame, "price"))
    quiet = recommendation(model, stable, evaluate_new(model.estimator, model, frame))
    assert quiet["verdict"] == "no retraining signal"

    shifted = _houses(seed=5).copy()
    shifted["size"] = shifted["size"] + 1800
    loud_drift = drift(model.profile, shifted)
    loud = recommendation(model, loud_drift, None)
    assert loud["verdict"] == "retrain recommended"
    assert loud["reasons"]


def test_binary_classification_adds_a_probability_column():
    frame = _churn()
    model = train_final(frame, "churned")
    scored = score(model.estimator, model, _churn(seed=6))
    assert "probability" in scored.columns
    assert scored["probability"].between(0, 1).all()
