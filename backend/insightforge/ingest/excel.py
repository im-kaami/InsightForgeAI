import re
import zipfile
from pathlib import Path

import pandas as pd

from insightforge.core.catalog import DataCatalog, sanitize_identifier
from insightforge.ingest.base import DataSource, IngestError, LoadResult, table_name_for

_MAX_EXCEL_BYTES = 100 * 1024 * 1024
_MAX_EXCEL_UNPACKED_BYTES = 1024 * 1024 * 1024
_UNNAMED_RE = re.compile(r"^Unnamed:\s*(\d+)(?:_level_\d+)?$", re.IGNORECASE)


def _clean_columns(frame: pd.DataFrame, preserve_rows: bool = False) -> pd.DataFrame:
    if not preserve_rows:
        frame = frame.dropna(how="all").dropna(axis=1, how="all")
    columns: list[str] = []
    for index, column in enumerate(frame.columns):
        value = str(column)
        match = _UNNAMED_RE.match(value)
        columns.append(f"col_{match.group(1) if match else index}" if match else value)
    frame.columns = columns
    return frame


def load_excel(source: DataSource, catalog: DataCatalog) -> LoadResult:
    """Load workbook sheets; a sheet filter always keeps sheet names in table names."""
    path = Path(source.location)
    if not path.exists():
        raise IngestError(f"Local source does not exist: {source.location}")
    if path.stat().st_size > _MAX_EXCEL_BYTES:
        raise IngestError("Excel source exceeds the 100 MB size limit")
    if zipfile.is_zipfile(path):
        try:
            with zipfile.ZipFile(path) as archive:
                unpacked = sum(item.file_size for item in archive.infolist())
        except zipfile.BadZipFile as error:
            raise IngestError("The Excel file is damaged") from error
        if unpacked > _MAX_EXCEL_UNPACKED_BYTES:
            raise IngestError("The Excel file expands to more than 1 GB; split it or save it as CSV")
    selected = source.options.get("sheets")
    sheet_name: list[str] | None = list(selected) if selected else None
    dtype = {name: "string" for name in source.options.get("text_columns", [])} or None
    try:
        sheets = pd.read_excel(
            path,
            sheet_name=sheet_name,
            header=source.options.get("header", 0),
            skiprows=source.options.get("skiprows"),
            dtype=dtype,
            engine="openpyxl" if path.suffix.lower() != ".xls" else None,
        )
    except ImportError as error:
        if path.suffix.lower() == ".xls":
            raise IngestError("install xlrd for .xls; or save as .xlsx") from error
        raise IngestError(f"Could not load Excel workbook {path.name}") from error
    except (OSError, ValueError) as error:
        raise IngestError(f"Could not load Excel workbook {path.name}: {error}") from error
    if isinstance(sheets, pd.DataFrame):
        sheets = {sheet_name[0] if sheet_name else path.stem: sheets}

    base = sanitize_identifier(source.name) if source.name else table_name_for(source.location)
    use_sheet_suffix = len(sheets) > 1 or bool(selected)
    tables: list[str] = []
    notes: list[str] = []
    for sheet, raw_frame in sheets.items():
        frame = _clean_columns(
            raw_frame, preserve_rows=bool(source.options.get("preserve_rows", False))
        )
        if frame.empty:
            notes.append(f"skipped empty sheet `{sheet}`")
            continue
        name = f"{base}__{sanitize_identifier(str(sheet))}" if use_sheet_suffix else base
        catalog.register_df(name, frame)
        tables.append(name)
    return LoadResult(tables=tables, notes=notes)
