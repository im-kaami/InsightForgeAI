from pathlib import Path

import pandas as pd
import pytest

from insightforge.core.catalog import DataCatalog
from insightforge.ingest import netguard
from insightforge.services.login_limits import login_limiter


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    monkeypatch.setattr(netguard, "resolve_host", lambda _hostname: ["93.184.216.34"])


@pytest.fixture(autouse=True)
def fresh_login_limiter():
    login_limiter.reset()
    yield
    login_limiter.reset()


@pytest.fixture
def hr_df() -> pd.DataFrame:
    return pd.read_csv(Path(__file__).parent / "fixtures" / "hr.csv")


@pytest.fixture
def catalog(hr_df: pd.DataFrame):
    instance = DataCatalog()
    instance.register_df("employees", hr_df)
    instance.register_df(
        "departments",
        pd.DataFrame(
            {
                "department": ["Engineering", "Sales", "Marketing", "Finance", "Human Resources"],
                "budget": [1_500_000, 1_100_000, 900_000, 1_200_000, 800_000],
                "head_count": [12, 12, 12, 12, 12],
            }
        ),
    )
    yield instance
    instance.close()


@pytest.fixture
def schema(catalog: DataCatalog):
    return catalog.introspect()
