from datetime import date

import pandas as pd
import pytest
from pydantic import TypeAdapter, ValidationError

from insightforge.core.catalog import DataCatalog
from insightforge.core.profiling import profile_catalog
from insightforge.core.validation import (
    AllowedValuesRule,
    FreshnessRule,
    NotNullRule,
    PatternRule,
    RangeRule,
    RowCountRule,
    RuleSet,
    UniqueRule,
    ValidationRule,
    check_rules,
    suggest_rules,
)

RULE = TypeAdapter(ValidationRule)


@pytest.fixture
def frame():
    return pd.DataFrame(
        {
            "order_id": ["A1", "A2", "A3", "A3", "A5", "A6"],
            "status": ["paid", "paid", "Refunded", "open", "paid", None],
            "amount": [10.0, -5.0, 30.0, 999.0, 50.0, 60.0],
            "ordered_on": pd.to_datetime(
                ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04", "2026-09-05", "2026-09-20"]
            ),
            "email": ["a@x.com", "b@x.com", "bad", "c@x.com", "d@x.com", "e@x.com"],
        }
    )


@pytest.fixture
def catalog(frame):
    value = DataCatalog()
    value.register_df("orders", frame)
    yield value
    value.close()


def result(report, rule_id):
    return next(item for item in report.results if item.rule_id == rule_id)


def test_rules_count_failing_rows_like_pandas(catalog, frame):
    rules = [
        NotNullRule(id="nn", table="orders", column="status"),
        UniqueRule(id="uq", table="orders", column="order_id", severity="blocking"),
        RangeRule(id="rg", table="orders", column="amount", min=0, max=500),
        AllowedValuesRule(id="av", table="orders", column="status", values=["paid", "refunded", "open"]),
        AllowedValuesRule(
            id="avi", table="orders", column="status", values=["paid", "refunded", "open"], ignore_case=True
        ),
        PatternRule(id="pt", table="orders", column="email", pattern=r"[^@]+@[^@]+\.[a-z]+"),
        RowCountRule(id="rc", table="orders", min=1, max=5),
    ]
    report = check_rules(catalog, rules, revision=3)
    assert report.rules_revision == 3
    assert result(report, "nn").failing_rows == int(frame["status"].isna().sum()) == 1
    duplicated = frame["order_id"].duplicated(keep=False)
    assert result(report, "uq").failing_rows == int(duplicated.sum()) == 2
    assert result(report, "uq").examples == ["A3"]
    outside = ~frame["amount"].between(0, 500)
    assert result(report, "rg").failing_rows == int(outside.sum()) == 2
    assert result(report, "rg").examples == ["-5.0", "999.0"]
    allowed = frame["status"].notna() & ~frame["status"].isin(["paid", "refunded", "open"])
    assert result(report, "av").failing_rows == int(allowed.sum()) == 1
    assert result(report, "avi").status == "passed"
    pattern = ~frame["email"].str.fullmatch(r"[^@]+@[^@]+\.[a-z]+")
    assert result(report, "pt").failing_rows == int(pattern.sum()) == 1
    assert result(report, "pt").examples == [], "sensitive columns never show example values"
    assert result(report, "rc").status == "failed" and "6 rows" in result(report, "rc").message
    assert report.failed_blocking == 1
    assert report.failed_warning == 5
    assert report.passed == 1
    assert report.blocked


def test_freshness_uses_the_data_dates(catalog):
    rule = FreshnessRule(id="fr", table="orders", column="ordered_on", max_age_days=7)
    fresh = check_rules(catalog, [rule], today=date(2026, 9, 25))
    stale = check_rules(catalog, [rule], today=date(2026, 9, 30))
    assert fresh.results[0].status == "passed"
    assert stale.results[0].status == "failed"
    assert "2026-09-20, 10 days" in stale.results[0].message
    assert "not at when the source was last updated" in stale.results[0].message


def test_missing_columns_and_wrong_types_are_errors_that_block(catalog):
    report = check_rules(
        catalog,
        [
            NotNullRule(id="missing", table="orders", column="nope", severity="blocking"),
            RangeRule(id="type", table="orders", column="status", min=0),
            NotNullRule(id="table", table="other", column="x"),
        ],
    )
    assert [item.status for item in report.results] == ["error", "error", "error"]
    assert "Column nope" in result(report, "missing").message
    assert report.failed_blocking == 1 and report.failed_warning == 2
    assert report.blocked


def test_rule_models_reject_bad_input():
    with pytest.raises(ValidationError):
        RULE.validate_python({"kind": "range", "table": "t", "column": "a"})
    with pytest.raises(ValidationError):
        RULE.validate_python({"kind": "pattern", "table": "t", "column": "a", "pattern": "("})
    with pytest.raises(ValidationError):
        RuleSet.model_validate(
            {
                "rules": [
                    {"id": "a", "kind": "not_null", "table": "t", "column": "x"},
                    {"id": "a", "kind": "unique", "table": "t", "column": "x"},
                ]
            }
        )
    assert RULE.validate_python({"kind": "row_count", "table": "t", "min": 1}).severity == "warning"


def test_suggested_rules_pass_on_the_data_they_came_from():
    catalog = DataCatalog()
    catalog.register_df(
        "staff",
        pd.DataFrame(
            {
                "employee_id": [f"E{i}" for i in range(12)],
                "team": ["a", "b", "c"] * 4,
                "salary": [float(100 + i) for i in range(12)],
            }
        ),
    )
    suggestions = suggest_rules(profile_catalog(catalog))
    kinds = {(item.rule.kind, getattr(item.rule, "column", None)) for item in suggestions}
    assert kinds == {
        ("row_count", None),
        ("not_null", "employee_id"),
        ("unique", "employee_id"),
        ("allowed_values", "team"),
        ("range", "salary"),
    }
    report = check_rules(catalog, [item.rule for item in suggestions])
    assert report.passed == len(suggestions)
    catalog.close()
