from pathlib import Path

import httpx
import pytest
import respx

from insightforge.core.catalog import DataCatalog
from insightforge.ingest import DataSource, IngestError
from insightforge.ingest.gsheets import export_url, load_gsheet, parse_gsheet

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://docs.google.com/spreadsheets/d/abc/edit#gid=123", ("abc", "123")),
        ("https://docs.google.com/spreadsheets/d/abc/edit?usp=sharing", ("abc", None)),
        ("https://docs.google.com/spreadsheets/d/abc/edit?gid=7", ("abc", "7")),
    ],
)
def test_parse_gsheet(url, expected):
    assert parse_gsheet(url) == expected


def test_export_urls():
    assert export_url("abc") == "https://docs.google.com/spreadsheets/d/abc/export?format=xlsx"
    assert export_url("abc", "7").endswith("export?format=csv&gid=7")


def test_loads_public_xlsx_export(tmp_path):
    url = "https://docs.google.com/spreadsheets/d/abcdefghijk/edit"
    with respx.mock:
        respx.get(export_url("abcdefghijk")).mock(
            return_value=httpx.Response(
                200,
                content=(FIXTURES / "workbook.xlsx").read_bytes(),
                headers={"Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
            )
        )
        catalog = DataCatalog()
        try:
            result = load_gsheet(DataSource(kind="gsheet", location=url), catalog, tmp_path)
            assert "gsheet_abcdefgh__orders" in result.tables
            assert "gsheet_abcdefgh__customers" in result.tables
        finally:
            catalog.close()


def test_private_sheet_redirect_raises_public_message(tmp_path):
    source_url = "https://docs.google.com/spreadsheets/d/private/edit"
    export = export_url("private")
    login = "https://accounts.google.com/login"
    with respx.mock:
        respx.get(export).mock(return_value=httpx.Response(302, headers={"Location": login}))
        respx.get(login).mock(
            return_value=httpx.Response(200, text="<html>Login</html>", headers={"Content-Type": "text/html"})
        )
        catalog = DataCatalog()
        try:
            with pytest.raises(IngestError, match="publicly"):
                load_gsheet(DataSource(kind="gsheet", location=source_url), catalog, tmp_path)
        finally:
            catalog.close()
