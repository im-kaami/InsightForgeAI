from pathlib import Path

import pytest

from insightforge.core.catalog import DataCatalog
from insightforge.ingest import DataSource, load_any, load_source

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    ("filename", "table", "rows"),
    [("hr.csv", "hr", 60), ("sales.parquet", "sales", 12), ("sales.json", "sales", 12)],
)
def test_loads_supported_files(filename, table, rows):
    catalog = DataCatalog()
    try:
        result = load_any(str(FIXTURES / filename), catalog)
        assert result.tables == [table]
        assert catalog.introspect().table(table).row_count == rows
    finally:
        catalog.close()


def test_name_override_is_honored():
    catalog = DataCatalog()
    try:
        result = load_any(str(FIXTURES / "hr.csv"), catalog, name="Employees 2024")
        assert result.tables == ["employees_2024"]
        assert "employees_2024" in catalog.table_names()
    finally:
        catalog.close()


def test_custom_header_and_text_column_preserve_leading_zeroes(tmp_path):
    path = tmp_path / "ids.csv"
    path.write_text("Export title\nid,value\n001,10\n002,20\n", encoding="utf-8")
    catalog = DataCatalog()
    try:
        source = DataSource(
            kind="csv",
            location=str(path),
            options={"header": 1, "text_columns": ["id"], "preserve_rows": True},
        )
        load_source(source, catalog)
        frame = catalog.query("SELECT * FROM ids ORDER BY id")
        assert frame["id"].tolist() == ["001", "002"]
    finally:
        catalog.close()


def test_loads_tsv(tmp_path):
    path = tmp_path / "items.tsv"
    path.write_text("id\tname\n1\tone\n2\ttwo\n", encoding="utf-8")
    catalog = DataCatalog()
    try:
        result = catalog.load(str(path))
        assert result.tables == ["items"]
        assert catalog.query("SELECT COUNT(*) AS count FROM items").iloc[0, 0] == 2
    finally:
        catalog.close()
