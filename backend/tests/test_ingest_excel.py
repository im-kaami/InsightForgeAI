from pathlib import Path

from insightforge.core.catalog import DataCatalog
from insightforge.ingest import load_any

WORKBOOK = Path(__file__).parent / "fixtures" / "workbook.xlsx"


def test_loads_nonempty_workbook_sheets_and_renames_columns():
    catalog = DataCatalog()
    try:
        result = load_any(str(WORKBOOK), catalog)
        assert "workbook__orders" in result.tables
        assert "workbook__customers" in result.tables
        assert catalog.introspect().table("workbook__orders").row_count == 10
        assert catalog.introspect().table("workbook__customers").row_count == 5
        assert any("Empty" in note for note in result.notes)
        columns = catalog.query("SELECT * FROM workbook__orders LIMIT 1").columns
        assert "col_0" in columns
    finally:
        catalog.close()


def test_sheet_filter_keeps_sheet_suffix():
    catalog = DataCatalog()
    try:
        result = load_any(str(WORKBOOK), catalog, options={"sheets": ["Orders"]})
        assert result.tables == ["workbook__orders"]
        assert catalog.introspect().table("workbook__orders").row_count == 10
    finally:
        catalog.close()
