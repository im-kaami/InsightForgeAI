import numpy as np
import pandas as pd

from insightforge.core.catalog import DataCatalog, QueryTimeoutError
from insightforge.core.profiling import PROFILE_VERSION, DataProfile, profile_catalog


def _columns(frame: pd.DataFrame) -> dict:
    catalog = DataCatalog()
    try:
        catalog.register_df("items", frame)
        profile = profile_catalog(catalog)
    finally:
        catalog.close()
    assert profile.profile_version == PROFILE_VERSION
    return {column.name: column for column in profile.tables[0].columns}


def test_numbers_get_ranges_quartiles_outliers_and_a_distribution():
    values = [*[100.0] * 50, *[110.0] * 50, 1000.0]
    column = _columns(pd.DataFrame({"amount": values}))["amount"]
    assert (column.kind, column.min_value, column.max_value) == ("number", "100", "1000")
    assert (column.p25, column.median, column.p75) == (100.0, 110.0, 110.0)
    assert column.outlier_count == 1
    assert sum(column.histogram) == len(values) and len(column.histogram) == 10
    assert column.alerts == [
        "1 values fall outside the usual range (85 to 125)",
        "Values are highly skewed; the median may describe them better than the average",
    ]


def test_text_columns_show_common_values_and_values_stored_as_text():
    frame = pd.DataFrame(
        {
            "region": ["north", "north", "south", "east", "west", "north"],
            "price": ["1.50", "2", " 3 ", "4.25", "5", "6"],
            "email": [f"person{index}@example.com" for index in range(6)],
        }
    )
    columns = _columns(frame)
    assert [(item.value, item.count) for item in columns["region"].top_values[:2]] == [
        ("north", 3),
        ("east", 1),
    ]
    assert columns["price"].alerts == ["Values look like numbers but are stored as text"]
    assert columns["email"].sensitivity and columns["email"].top_values == []


def test_dates_report_their_range_and_missing_days():
    days = [*pd.date_range("2024-01-01", periods=10), *pd.date_range("2024-01-15", periods=10)]
    column = _columns(pd.DataFrame({"day": days}))["day"]
    assert (column.min_value, column.max_value) == ("2024-01-01", "2024-01-24")
    assert (column.distinct_days, column.span_days) == (20, 24)
    assert column.alerts == ["4 days between 2024-01-01 and 2024-01-24 have no rows"]


def test_constant_identifier_and_empty_columns_are_flagged():
    frame = pd.DataFrame(
        {
            "status": ["open"] * 30,
            "code": [f"c{index}" for index in range(30)],
            "empty": [None] * 30,
            "order_id": np.arange(30),
        }
    )
    columns = _columns(frame)
    assert columns["status"].alerts == ["Every value is the same"]
    assert columns["code"].alerts == ["Every value is different, so this may be an identifier"]
    assert columns["empty"].alerts == ["All values are missing"]
    assert columns["order_id"].alerts == []


def test_profiles_saved_before_version_two_are_marked_as_older():
    assert DataProfile.model_validate({"tables": []}).profile_version == 1


def test_profile_counts_and_no_data_mutation():
    catalog = DataCatalog()
    try:
        catalog.register_df("items", pd.DataFrame({"id": ["01", "01", "02", None], "value": [2, 2, 3, None]}))
        result = profile_catalog(catalog)
        table = result.tables[0]
        assert table.row_count == 4
        assert table.duplicate_rows == 1
        column = next(item for item in table.columns if item.name == "id")
        assert column.null_count == 1
        assert column.distinct_count == 2
        assert column.repeated_non_null_count == 1
        assert column.null_fraction == 0.25
        assert result.complete is True
        assert catalog.query('SELECT "id" FROM items').iloc[0, 0] == "01"
    finally:
        catalog.close()


def test_incomplete_profile_is_explicit(monkeypatch):
    catalog = DataCatalog()
    try:
        catalog.register_df("items", pd.DataFrame({"id": [1]}))

        def fail(*_args, **_kwargs):
            raise QueryTimeoutError("timed out")

        monkeypatch.setattr(catalog, "query", fail)
        result = profile_catalog(catalog)
        assert result.complete is False
        assert result.warnings
        assert result.tables[0].row_count is None
    finally:
        catalog.close()


def test_column_budget_is_not_reported_as_complete():
    catalog = DataCatalog()
    try:
        catalog.register_df("items", pd.DataFrame({"id": [1], "extra": [2]}))
        result = profile_catalog(catalog, max_columns=1)
        assert result.complete is False
        assert len(result.tables[0].columns) == 1
    finally:
        catalog.close()
