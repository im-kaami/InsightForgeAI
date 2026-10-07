import pytest

from insightforge.config import get_settings
from insightforge.ingest import netguard
from insightforge.ingest.base import IngestError
from insightforge.ingest.database import (
    pin_connection,
    pin_mysql_host,
    pin_postgres_host,
    pin_url_host,
)
from insightforge.ingest.netguard import pin_public_url


@pytest.fixture(autouse=True)
def public_only(monkeypatch):
    monkeypatch.setenv("ALLOW_PRIVATE_DATABASES", "false")
    get_settings.cache_clear()


def test_url_is_pinned_to_the_checked_address():
    url, headers, extensions = pin_public_url("https://files.example.org:8443/a/b.csv?x=1")
    assert url == "https://93.184.216.34:8443/a/b.csv?x=1"
    assert headers == {"Host": "files.example.org:8443"}
    assert extensions == {"sni_hostname": "files.example.org"}


def test_default_port_is_left_out_of_the_host_header():
    _, headers, _ = pin_public_url("https://files.example.org/data.csv")
    assert headers == {"Host": "files.example.org"}


def test_a_name_that_resolves_to_a_private_address_is_refused(monkeypatch):
    monkeypatch.setattr(netguard, "resolve_host", lambda _name: ["10.0.0.5"])
    with pytest.raises(IngestError, match="Private or internal"):
        pin_public_url("https://rebind.example.org/data.csv")


def test_postgres_host_gets_the_checked_hostaddr():
    pinned = pin_postgres_host("postgresql://u:p@db.example.org:5432/app?sslmode=require")
    assert pinned.endswith("sslmode=require&hostaddr=93.184.216.34")
    assert pin_postgres_host(pinned) == pinned


def test_mysql_host_is_replaced_by_the_checked_address():
    pinned = pin_mysql_host("host=db.example.org user=u password=p port=3306 database=app")
    assert pinned == "host=93.184.216.34 user=u password=p port=3306 database=app"
    assert pin_mysql_host(pinned) == pinned


def test_sqlalchemy_url_host_is_replaced_and_credentials_kept():
    pinned = pin_url_host("mssql+pymssql://sa:p%40ss@db.example.org:1433/master")
    assert pinned == "mssql+pymssql://sa:p%40ss@93.184.216.34:1433/master"
    assert pin_url_host("mssql+pymssql://sa:p@10.0.0.4/master") == "mssql+pymssql://sa:p@10.0.0.4/master"


def test_snowflake_is_left_alone():
    uri = "snowflake://u:p@ft45233.eu-central-2.aws/db"
    assert pin_connection("snowflake", uri) == uri


def test_postgres_pinning_refuses_a_private_resolution(monkeypatch):
    monkeypatch.setattr(netguard, "resolve_host", lambda _name: ["192.168.1.9"])
    with pytest.raises(IngestError):
        pin_postgres_host("postgresql://u:p@db.example.org/app")
