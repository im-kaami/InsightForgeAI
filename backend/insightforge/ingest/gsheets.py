import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

from insightforge.config import get_settings
from insightforge.core.catalog import DataCatalog
from insightforge.ingest.base import DataSource, IngestError, LoadResult, detect_source, load_source
from insightforge.ingest.url import download

_ID_RE = re.compile(r"docs\.google\.com/spreadsheets/d/([a-zA-Z0-9-_]+)")


def parse_gsheet(url: str) -> tuple[str, str | None]:
    match = _ID_RE.search(url)
    if not match:
        raise IngestError("Invalid Google Sheets URL")
    parsed = urlparse(url)
    values = parse_qs(parsed.query)
    fragment_values = parse_qs(parsed.fragment)
    gid = (values.get("gid") or fragment_values.get("gid") or [None])[0]
    return match.group(1), gid


def export_url(spreadsheet_id: str, gid: str | None = None) -> str:
    base = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/export"
    return f"{base}?format=csv&gid={gid}" if gid is not None else f"{base}?format=xlsx"


def load_gsheet(
    source: DataSource, catalog: DataCatalog, dest_dir: Path | None = None
) -> LoadResult:
    spreadsheet_id, gid = parse_gsheet(source.location)
    destination = dest_dir or get_settings().storage_dir / "downloads"
    client = source.options.get("client")
    try:
        path = download(
            export_url(spreadsheet_id, gid),
            destination,
            max_bytes=int(source.options.get("max_bytes", 200_000_000)),
            client=client,
            allow_html=True,
        )
    except IngestError as error:
        if any(value in str(error) for value in ("HTTP 401", "HTTP 403", "authentication page")):
            raise IngestError(
                "Google Sheet is not publicly accessible. Share it as "
                "'Anyone with the link → Viewer' and try again."
            ) from error
        raise
    content_type_html = path.suffix.lower() == ".html"
    starts_html = path.read_bytes()[:512].lstrip().lower().startswith((b"<!doctype html", b"<html"))
    if content_type_html or starts_html:
        path.unlink(missing_ok=True)
        raise IngestError(
            "Google Sheet is not publicly accessible. Share it as "
            "'Anyone with the link → Viewer' and try again."
        )
    name = source.name or f"gsheet_{spreadsheet_id[:8]}"
    local = detect_source(str(path), name=name, options=source.options)
    try:
        return load_source(local, catalog)
    except httpx.HTTPError as error:
        raise IngestError("Could not download Google Sheet") from error
