import httpx
import pytest
import respx

from insightforge.ingest.base import IngestError
from insightforge.ingest.netguard import validate_public_url
from insightforge.ingest.url import download


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/x",
        "http://localhost/x",
        "http://169.254.169.254/latest/meta-data",
        "http://metadata.google.internal/",
        "file:///etc/passwd",
        "http://10.0.0.5/",
        "http://100.64.0.1/",
        "http://224.0.0.1/",
    ],
)
def test_rejects_private_and_non_http_urls(url):
    with pytest.raises(IngestError):
        validate_public_url(url)


def test_rejects_hostname_resolving_private_address():
    with pytest.raises(IngestError):
        validate_public_url("https://private.example/data", resolver=lambda _host: ["10.1.2.3"])


def test_accepts_public_address():
    validate_public_url("https://public.example/data", resolver=lambda _host: ["93.184.216.34"])
    validate_public_url("http://93.184.216.34/")


def test_download_follows_public_redirect(tmp_path):
    with respx.mock:
        respx.get("https://files.example.org/start").mock(
            return_value=httpx.Response(302, headers={"Location": "/data.csv"})
        )
        respx.get("https://files.example.org/data.csv").mock(
            return_value=httpx.Response(
                200,
                content=b"id,name\n1,one\n",
                headers={"Content-Type": "text/csv"},
            )
        )
        path = download("https://files.example.org/start", tmp_path)
    assert path.name == "data.csv"
    assert path.read_text() == "id,name\n1,one\n"


def test_download_rejects_redirect_to_private_address(tmp_path):
    with respx.mock:
        respx.get("https://files.example.org/start").mock(
            return_value=httpx.Response(302, headers={"Location": "http://127.0.0.1/data.csv"})
        )
        with pytest.raises(IngestError, match="Private or internal"):
            download("https://files.example.org/start", tmp_path)
