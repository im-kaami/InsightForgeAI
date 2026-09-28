from pathlib import Path

import httpx
import pytest_asyncio

from insightforge.config import get_settings

FIXTURES = Path(__file__).parents[1] / "fixtures"


@pytest_asyncio.fixture
async def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'app.db').as_posix()}")
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("LOCAL_LLM_MODEL", "")
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-with-at-least-32-characters")
    monkeypatch.setenv("APP_SECRET", "test-app-secret-with-at-least-32-characters")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    get_settings.cache_clear()
    from insightforge.api.main import create_app

    application = create_app()
    async with application.router.lifespan_context(application):
        yield application
    get_settings.cache_clear()


@pytest_asyncio.fixture
async def client(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as value:
        yield value


@pytest_asyncio.fixture
async def auth_headers(client):
    credentials = {"email": "owner@example.com", "password": "correct horse battery staple"}
    assert (await client.post("/api/auth/register", json=credentials)).status_code == 201
    response = await client.post("/api/auth/login", json=credentials)
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest_asyncio.fixture
async def hr_dataset(client, auth_headers):
    content = (FIXTURES / "hr.csv").read_bytes()
    response = await client.post(
        "/api/datasets/upload",
        headers=auth_headers,
        files=[("files", ("hr.csv", content, "text/csv"))],
    )
    assert response.status_code == 201, response.text
    return response.json()
