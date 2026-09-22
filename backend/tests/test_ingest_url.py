from pathlib import Path

import httpx
import pytest
import respx

from insightforge.core.catalog import DataCatalog
from insightforge.ingest import DataSource, IngestError
from insightforge.ingest.url import download, load_url

FIXTURES = Path(__file__).parent / "fixtures"


def _load(url, tmp_path, response):
    with respx.mock:
        respx.get(url).mock(return_value=response)
        catalog = DataCatalog()
        try:
            return load_url(DataSource(kind="url", location=url), catalog, tmp_path), catalog.table_names()
        finally:
            catalog.close()


def test_csv_url_loads_using_url_stem(tmp_path):
    body = (FIXTURES / "hr.csv").read_bytes()
    result, tables = _load(
        "https://example.com/data.csv",
        tmp_path,
        httpx.Response(200, content=body, headers={"Content-Type": "text/csv"}),
    )
    assert result.tables == ["data"]
    assert "data" in tables


def test_content_type_infers_csv_without_extension(tmp_path):
    body = (FIXTURES / "hr.csv").read_bytes()
    result, _ = _load(
        "https://example.com/download",
        tmp_path,
        httpx.Response(200, content=body, headers={"Content-Type": "text/csv"}),
    )
    assert result.tables == ["download"]


def test_http_error_and_size_cap_raise(tmp_path):
    with respx.mock:
        respx.get("https://example.com/missing.csv").mock(return_value=httpx.Response(404))
        with pytest.raises(IngestError, match="HTTP 404"):
            download("https://example.com/missing.csv", tmp_path)
        respx.get("https://example.com/large.csv").mock(
            return_value=httpx.Response(200, content=b"a" * 11, headers={"Content-Type": "text/csv"})
        )
        with pytest.raises(IngestError, match="size limit"):
            download("https://example.com/large.csv", tmp_path, max_bytes=10)


def test_content_disposition_excel_loads_sheets(tmp_path):
    body = (FIXTURES / "workbook.xlsx").read_bytes()
    response = httpx.Response(
        200,
        content=body,
        headers={
            "Content-Type": "application/octet-stream",
            "Content-Disposition": 'attachment; filename="report.xlsx"',
        },
    )
    result, _ = _load("https://example.com/export", tmp_path, response)
    assert "export__orders" in result.tables
    assert "export__customers" in result.tables
