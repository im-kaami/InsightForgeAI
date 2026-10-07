import re

import jwt
import pytest

from insightforge.api.routers import auth as auth_router
from insightforge.config import get_settings
from insightforge.services import mailer

PASSWORD = "correct horse battery staple"


@pytest.fixture
def outbox(monkeypatch):
    sent = []
    monkeypatch.setenv("SMTP_HOST", "mail.example.com")
    monkeypatch.setenv("SMTP_FROM", "insightforge@example.com")
    get_settings.cache_clear()
    monkeypatch.setattr(mailer, "start_background", lambda to, subject, text: sent.append((to, text)))
    return sent


def _token(text):
    return re.search(r"/verify#(\S+)", text).group(1)


async def test_accounts_need_a_confirmed_email_when_email_is_set_up(client, outbox):
    body = {"email": "new@example.com", "password": PASSWORD}
    assert (await client.post("/api/auth/register", json=body)).status_code == 201
    blocked = await client.post("/api/auth/login", json=body)
    assert blocked.status_code == 403 and "Confirm your email" in blocked.json()["detail"]
    assert len(outbox) == 1 and outbox[0][0] == "new@example.com"
    token = _token(outbox[0][1])
    assert (await client.post("/api/auth/verify", json={"token": token})).status_code == 204
    assert (await client.post("/api/auth/login", json=body)).status_code == 200


async def test_bad_confirmation_links_share_one_error(client, outbox):
    forged = jwt.encode(
        {"sub": "x", "purpose": "verify-email"}, "another-secret-another-secret-123", "HS256"
    )
    wrong_purpose = jwt.encode({"sub": "x"}, get_settings().jwt_secret, "HS256")
    for token in ("garbage", forged, wrong_purpose):
        response = await client.post("/api/auth/verify", json={"token": token})
        assert response.status_code == 400


async def test_resend_is_silent_and_limited(client, outbox):
    body = {"email": "again@example.com", "password": PASSWORD}
    await client.post("/api/auth/register", json=body)
    outbox.clear()
    for address in (body["email"], "nobody@example.com"):
        response = await client.post("/api/auth/verify/resend", json={"email": address})
        assert response.status_code == 204
    assert outbox == []  # the registration email was just sent
    auth_router._last_verification.clear()
    for _ in range(2):
        await client.post("/api/auth/verify/resend", json={"email": body["email"]})
    assert len(outbox) == 1


async def test_without_email_set_up_accounts_work_at_once(client):
    body = {"email": "plain@example.com", "password": PASSWORD}
    await client.post("/api/auth/register", json=body)
    assert (await client.post("/api/auth/login", json=body)).status_code == 200
