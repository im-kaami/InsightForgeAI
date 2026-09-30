import pandas as pd
import pytest
from pydantic import TypeAdapter, ValidationError

from insightforge.core.catalog import DataCatalog
from insightforge.core.profiling import profile_catalog
from insightforge.core.recipes import (
    ChangeType,
    CleaningRecipe,
    CleanText,
    Condition,
    DeriveColumn,
    DropDuplicates,
    DropMissing,
    FillMissing,
    FilterRows,
    MapValues,
    RecipeError,
    RecipeStep,
    RenameColumn,
    ValueMapping,
    apply_recipe,
    describe_step,
    suggest_steps,
)

STEP = TypeAdapter(RecipeStep)


@pytest.fixture
def frame():
    return pd.DataFrame(
        {
            "id": ["1", "2", "3", "4", "5", "5"],
            "work_mode": [" remote", "Remote", "office ", None, "REMOTE", "REMOTE"],
            "amount": ["10.5", "20", "x", None, "40", "40"],
            "hired": ["01/02/2024", "15/03/2024", "30/04/2024", None, "01/05/2024", "01/05/2024"],
            "salary": [100.0, None, 300.0, 400.0, None, None],
        }
    )


@pytest.fixture
def catalog(frame):
    value = DataCatalog()
    value.register_df("people", frame)
    yield value
    value.close()


def rows(catalog):
    return catalog.query("SELECT * FROM people")


def clean(values):
    return [None if pd.isna(value) else value for value in values]


def test_text_cleaning_and_mapping_match_pandas(catalog, frame):
    results = apply_recipe(
        catalog,
        [
            CleanText(table="people", column="work_mode", case="lower"),
            MapValues(
                table="people",
                column="work_mode",
                mapping=[ValueMapping(from_value="REMOTE", to_value="Remote")],
                ignore_case=True,
            ),
        ],
    )
    expected = frame["work_mode"].str.strip().str.lower().replace({"remote": "Remote"})
    assert clean(rows(catalog)["work_mode"]) == clean(expected)
    assert results[0].changed_values == 5
    assert results[1].changed_values == 4
    assert results[0].rows_before == results[0].rows_after == 6


def test_change_type_counts_failures_and_can_leave_them_empty(catalog):
    with pytest.raises(RecipeError, match="1 values in people.amount could not be converted"):
        apply_recipe(catalog, [ChangeType(table="people", column="amount", to="number")])
    results = apply_recipe(
        catalog, [ChangeType(table="people", column="amount", to="number", on_error="empty")]
    )
    values = rows(catalog)["amount"]
    assert results[0].failed_values == 1
    expected = pd.to_numeric(pd.Series(["10.5", "20", "x", None, "40", "40"]), errors="coerce")
    assert values.isna().tolist() == expected.isna().tolist()
    assert values.dropna().tolist() == expected.dropna().tolist()


def test_change_type_with_a_date_format(catalog, frame):
    apply_recipe(catalog, [ChangeType(table="people", column="hired", to="date", date_format="%d/%m/%Y")])
    got = pd.to_datetime(rows(catalog)["hired"])
    expected = pd.to_datetime(frame["hired"], format="%d/%m/%Y")
    assert clean(got) == clean(expected)


def test_fill_missing_median_matches_pandas(catalog, frame):
    results = apply_recipe(catalog, [FillMissing(table="people", column="salary", method="median")])
    expected = frame["salary"].fillna(frame["salary"].median())
    assert rows(catalog)["salary"].tolist() == expected.tolist()
    assert results[0].changed_values == 3
    assert "hidden for a sensitive column" in results[0].description
    apply_recipe(catalog, [FillMissing(table="people", column="amount", method="most_common")])
    assert rows(catalog)["amount"].tolist()[3] == "40"


def test_fill_missing_value_must_fit_the_column(catalog):
    with pytest.raises(RecipeError, match="does not fit"):
        apply_recipe(catalog, [FillMissing(table="people", column="salary", value="abc")])


def test_drop_missing_and_duplicates_match_pandas(catalog, frame):
    apply_recipe(
        catalog,
        [DropMissing(table="people", columns=["work_mode"]), DropDuplicates(table="people")],
    )
    expected = frame.dropna(subset=["work_mode"]).drop_duplicates()
    assert rows(catalog)["id"].tolist() == expected["id"].tolist()


def test_drop_duplicates_by_key_keeps_the_first(catalog):
    catalog.connection.execute("UPDATE people SET salary = 1 WHERE id = '5' AND salary IS NULL")
    apply_recipe(catalog, [DropDuplicates(table="people", columns=["id"])])
    assert rows(catalog)["id"].tolist() == ["1", "2", "3", "4", "5"]


def test_filter_rows_keeps_missing_values_when_removing(catalog, frame):
    apply_recipe(
        catalog,
        [
            FilterRows(
                table="people",
                action="remove",
                conditions=[Condition(column="work_mode", op="equals", value="REMOTE")],
            )
        ],
    )
    expected = frame[frame["work_mode"].ne("REMOTE") | frame["work_mode"].isna()]
    assert rows(catalog)["id"].tolist() == expected["id"].tolist()


def test_filter_rows_numeric_comparison(catalog, frame):
    apply_recipe(
        catalog,
        [
            FilterRows(
                table="people",
                action="keep",
                conditions=[Condition(column="salary", op="at_least", value=200)],
            )
        ],
    )
    assert rows(catalog)["id"].tolist() == frame[frame["salary"] >= 200]["id"].tolist()


def test_rename_and_derive(catalog, frame):
    apply_recipe(
        catalog,
        [
            RenameColumn(table="people", column="salary", new_name="pay"),
            DeriveColumn(table="people", new_name="pay_k", expression="pay / 1000"),
        ],
    )
    result = rows(catalog)
    assert "salary" not in result.columns
    assert clean(result["pay_k"]) == clean(frame["salary"] / 1000)


@pytest.mark.parametrize(
    "expression",
    [
        "(SELECT MAX(salary) FROM people)",
        "SUM(salary)",
        "read_csv('x.csv')",
        "1) AS v FROM people; DROP TABLE people; --",
        "getenv('HOME')",
        "salary OVER ()",
    ],
)
def test_derive_rejects_unsafe_expressions(catalog, expression):
    with pytest.raises(RecipeError):
        apply_recipe(catalog, [DeriveColumn(table="people", new_name="x", expression=expression)])
    assert len(rows(catalog)) == 6


def test_errors_name_the_step_and_missing_columns(catalog):
    with pytest.raises(RecipeError) as error:
        apply_recipe(
            catalog,
            [
                CleanText(table="people", column="work_mode"),
                RenameColumn(table="people", column="missing", new_name="other"),
            ],
        )
    assert error.value.index == 1
    assert str(error.value).startswith("Step 2: Column missing")
    with pytest.raises(RecipeError, match="Table nope"):
        apply_recipe(catalog, [DropDuplicates(table="nope")])


def test_step_models_reject_bad_input():
    with pytest.raises(ValidationError):
        STEP.validate_python({"kind": "rename_column", "table": "t", "column": "a", "new_name": "b; drop"})
    with pytest.raises(ValidationError):
        STEP.validate_python(
            {"kind": "change_type", "table": "t", "column": "a", "to": "text", "date_format": "%d"}
        )
    with pytest.raises(ValidationError):
        STEP.validate_python({"kind": "fill_missing", "table": "t", "column": "a"})
    with pytest.raises(ValidationError):
        STEP.validate_python(
            {
                "kind": "map_values",
                "table": "t",
                "column": "a",
                "ignore_case": True,
                "mapping": [{"from_value": "A", "to_value": "x"}, {"from_value": "a", "to_value": "y"}],
            }
        )
    recipe = CleaningRecipe.model_validate({"steps": [{"kind": "drop_duplicates", "table": "t"}]})
    assert describe_step(recipe.steps[0]) == "Remove rows of t that repeat exactly, keeping the first"


def test_suggestions_come_from_the_health_check(catalog):
    suggestions = suggest_steps(profile_catalog(catalog))
    kinds = {(item.step.kind, getattr(item.step, "column", None)) for item in suggestions}
    assert ("drop_duplicates", None) in kinds
    assert ("change_type", "id") in kinds
    mapping = next(item.step for item in suggestions if item.step.kind == "map_values")
    assert mapping.column == "work_mode"
    assert {(item.from_value, item.to_value) for item in mapping.mapping} == {
        (" remote", "REMOTE"),
        ("Remote", "REMOTE"),
    }
    for item in suggestions:
        STEP.validate_python(item.step.model_dump())
