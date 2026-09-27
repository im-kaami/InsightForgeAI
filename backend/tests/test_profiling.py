import pandas as pd

from insightforge.core.catalog import DataCatalog, QueryTimeoutError
from insightforge.core.profiling import profile_catalog


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
