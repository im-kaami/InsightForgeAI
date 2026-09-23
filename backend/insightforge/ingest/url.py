import re
import tempfile
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import httpx

from insightforge.config import get_settings
from insightforge.core.catalog import DataCatalog
from insightforge.ingest.base import (
    DataSource,
    IngestError,
    LoadResult,
    detect_source,
    load_source,
    table_name_for,
)
from insightforge.ingest.netguard import validate_public_url

_KNOWN_SUFFIXES = {
    ".csv",
    ".tsv",
    ".tab",
    ".parquet",
    ".pq",
    ".json",
    ".jsonl",
    ".ndjson",
    ".xlsx",
    ".xlsm",
    ".xls",
}
_CONTENT_SUFFIXES = {
    "text/csv": ".csv",
    "application/json": ".json",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-excel": ".xls",
}
_FILENAME_RE = re.compile(r"filename\*?=(?:UTF-8''|\")?([^\";]+)", re.IGNORECASE)
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}


def _filename(headers: httpx.Headers, url: str) -> str:
    disposition = headers.get("content-disposition", "")
    match = _FILENAME_RE.search(disposition)
    if match:
        return Path(unquote(match.group(1).strip())).name
    return Path(unquote(urlparse(url).path)).name or "download"


def download(
    url: str,
    dest_dir: Path,
    max_bytes: int = 200_000_000,
    timeout: int = 60,
    *,
    client: httpx.Client | None = None,
    allow_html: bool = False,
    allow_private: bool | None = None,
) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    own_client = client is None
    http_client = client or httpx.Client(timeout=timeout, follow_redirects=False)
    private_allowed = get_settings().allow_private_urls if allow_private is None else allow_private
    temp_path: Path | None = None
    current = url
    try:
        for hop in range(6):
            validate_public_url(current, allow_private=private_allowed)
            with http_client.stream("GET", current, follow_redirects=False) as response:
                if response.status_code in _REDIRECT_STATUSES:
                    location = response.headers.get("location")
                    if not location:
                        raise IngestError(f"Download failed with HTTP {response.status_code}")
                    if hop == 5:
                        raise IngestError("Too many redirects")
                    current = urljoin(current, location)
                    continue
                if response.status_code < 200 or response.status_code >= 300:
                    raise IngestError(f"Download failed with HTTP {response.status_code}")
                if allow_html and urlparse(str(response.url)).hostname == "accounts.google.com":
                    raise IngestError("Download redirected to an authentication page")
                name = _filename(response.headers, str(response.url))
                content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                with tempfile.NamedTemporaryFile(dir=dest_dir, delete=False, suffix=".part") as output:
                    temp_path = Path(output.name)
                    size = 0
                    first_bytes = b""
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > max_bytes:
                            raise IngestError(f"Download exceeds the {max_bytes}-byte size limit")
                        if len(first_bytes) < 4:
                            first_bytes = (first_bytes + chunk)[:4]
                        output.write(chunk)
                break
        else:
            raise IngestError("Too many redirects")
        suffix = Path(name).suffix.lower()
        if suffix not in _KNOWN_SUFFIXES:
            inferred = _CONTENT_SUFFIXES.get(content_type)
            if content_type == "application/octet-stream" and first_bytes == b"PAR1":
                inferred = ".parquet"
            if allow_html and content_type == "text/html":
                inferred = ".html"
            if not inferred:
                raise IngestError(f"Could not determine downloaded file type ({content_type or 'unknown'})")
            name = f"{Path(name).stem or 'download'}{inferred}"
        destination = dest_dir / Path(name).name
        destination.unlink(missing_ok=True)
        temp_path.replace(destination)
        temp_path = None
        return destination
    except httpx.HTTPError as error:
        raise IngestError(f"Download failed: {type(error).__name__}") from error
    finally:
        if temp_path:
            temp_path.unlink(missing_ok=True)
        if own_client:
            http_client.close()


def load_url(
    source: DataSource, catalog: DataCatalog, dest_dir: Path | None = None
) -> LoadResult:
    destination = dest_dir or get_settings().storage_dir / "downloads"
    client = source.options.get("client")
    max_bytes = int(source.options.get("max_bytes", 200_000_000))
    path = download(
        source.location,
        destination,
        max_bytes=max_bytes,
        client=client,
        allow_private=source.options.get("allow_private"),
    )
    url_name = Path(urlparse(source.location).path).name or path.name
    name = source.name or table_name_for(url_name)
    local = detect_source(str(path), name=name, options=source.options)
    return load_source(local, catalog)
