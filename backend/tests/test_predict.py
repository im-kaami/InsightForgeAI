import json

import numpy as np
import pandas as pd
import pytest

from insightforge.core.agent import InsightForgeAgent
from insightforge.core.artifacts import StatArtifact
from insightforge.core.catalog import DataCatalog
from insightforge.core.llm import FakeLLMClient
from insightforge.core.planner import PREDICT_CUES
from insightforge.core.predict import PredictError, predict


def _churn(n: int = 600) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    frame = pd.DataFrame(
        {
            "customer_id": range(n),
            "tenure": rng.integers(1, 60, n),
            "monthly": rng.normal(50, 15, n).round(2),
            "plan": rng.choice(["basic", "plus", "pro"], n),
            "signup": pd.date_range("2024-01-01", periods=n, freq="D"),
        }
    )
    logit = -2 + 0.06 * (60 - frame.tenure) + (frame.plan == "basic") * 1.2
    frame["churned"] = np.where(rng.random(n) < 1 / (1 + np.exp(-logit)), "yes", "no")
    frame["cancel_reason"] = np.where(frame.churned == "yes", "price", "none")
    return frame


def test_classification_beats_the_baseline_and_flags_ids_and_leaks():
    result = predict(_churn(), "churned", date_column="signup")
    board = {row["model"]: row for row in result["groups"]}
    assert set(board) == {
        "baseline (most common class)",
        "logistic regression",
        "random forest",
        "gradient boosting",
    }
    best = result["test"].removeprefix("Prediction model (").removesuffix(")")
    assert board[best]["holdout_score"] > board["baseline (most common class)"]["holdout_score"] + 0.05
    assert result["statistic"] == board[best]["holdout_score"]
    assert "time-based: trained on the earliest 80% by signup" in result["checks"][1]
    assert any("customer_id (looks like an ID)" in check for check in result["checks"])
    leak = "cancel_reason predicted churned almost perfectly"
    assert any(caution.startswith(leak) for caution in result["cautions"])
    assert result["importance"][0]["feature"] == "tenure"
    assert "cancel_reason" not in result["features"]
    assert "ROC AUC" in result["interpretation"]


def test_regression_reports_error_against_the_average(hr_df):
    result = predict(hr_df, "salary", ["department", "performance_score", "location", "job_title"])
    board = {row["model"]: row for row in result["groups"]}
    best = result["test"].removeprefix("Prediction model (").removesuffix(")")
    assert board[best]["holdout_score"] < board["baseline (average)"]["holdout_score"]
    assert "typical error of" in result["interpretation"] and "R-squared" in result["interpretation"]
    assert "Only 60 rows; scores may change noticeably with more data." in result["cautions"]


def test_prediction_questions_are_routed_and_run_as_a_tested_method():
    catalog = DataCatalog()
    catalog.register_df("subscribers", _churn())
    choice = {
        "predict": True,
        "target": "churned",
        "features": [],
        "date": "signup",
        "sql": "SELECT * FROM subscribers",
    }

    def reply(messages):
        if "predict an outcome per record" in messages[0]["content"]:
            return json.dumps(choice)
        return "Tenure matters most."

    result = InsightForgeAgent(FakeLLMClient(reply), privacy_mode="full").run(
        "Which factors predict whether a subscriber churns?", catalog
    )
    stat = next(item for item in result.artifacts if isinstance(item, StatArtifact))
    assert stat.method == "predict" and stat.y == "churned" and stat.x == "signup"
    assert stat.importance[0]["feature"] == "tenure"
    assert "cancel_reason" not in stat.features and stat.p_value is None
    catalog.close()


def test_predict_cues_leave_forecasts_to_the_series_route():
    assert PREDICT_CUES.search("Which factors predict whether a subscriber churns?")
    assert PREDICT_CUES.search("Which customers are likely to churn?")
    assert not PREDICT_CUES.search("Predict the number of sales per week for the next 8 weeks")
    assert not PREDICT_CUES.search("Forecast revenue next month")


@pytest.mark.parametrize(
    ("frame", "target", "message"),
    [
        (pd.DataFrame({"a": range(10), "y": range(10)}), "y", "at least 60 rows"),
        (_churn().assign(churned="no"), "churned", "only one value"),
        (_churn(), "missing", "not in the result"),
        (_churn()[["customer_id", "churned"]], "churned", "No usable predictor columns"),
    ],
)
def test_unusable_inputs_raise_clear_errors(frame, target, message):
    with pytest.raises(PredictError, match=message):
        predict(frame, target)
