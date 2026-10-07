from datetime import UTC, datetime, timedelta

import jwt
import pytest

from insightforge.cli import _run
from insightforge.config import get_settings
from insightforge.db.models import PasswordReset, User
from insightforge.db.session import SessionLocal
from insightforge.services.login_limits import LoginLimiter, login_limiter
from insightforge.services.password_reset import create_reset

PASSWORD = "correct horse battery staple"
RESET_ERROR = "This reset link is invalid or has expired"


async def _register(client, email="amy@example.com", password=PASSWORD):
    response = await client.post("/api/auth/register", json={"email": email, "password": password})
    assert response.status_code == 201, response.text
    return response.json()


async def _login(client, email="amy@example.com", password=PASSWORD):
    return await client.post("/api/auth/login", json={"email": email, "password": password})


async def _token(client, email="amy@example.com", password=PASSWORD):
    return (await _login(client, email, password)).json()["access_token"]


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def clock(monkeypatch):
    class Clock:
        now = 1000.0

        def __call__(self):
            return self.now

    value = Clock()
    monkeypatch.setattr(login_limiter, "clock", value)
    return value


def _link(email="amy@example.com", hours=1):
    db = SessionLocal()
    try:
        return create_reset(db, email, hours)
    finally:
        db.close()


async def test_register_validates_and_lowercases(client):
    for body in (
        {"email": "a@b.co", "password": "short"},
        {"email": "no-at-sign", "password": PASSWORD},
        {"email": "two@@example.com", "password": PASSWORD},
        {"email": "@example.com", "password": PASSWORD},
        {"email": "amy@", "password": PASSWORD},
        {"email": "amy@example.com", "password": "x" * 257},
    ):
        assert (await client.post("/api/auth/register", json=body)).status_code == 422, body
    user = await _register(client, "  Amy@Example.COM ")
    assert user["email"] == "amy@example.com"
    assert (await _login(client, "AMY@example.com")).status_code == 200


async def test_login_locks_after_repeated_failures_and_unlocks(client, clock):
    await _register(client)
    await _register(client, "bob@example.com")
    for _ in range(5):
        assert (await _login(client, password="wrong password")).status_code == 401
    locked = await _login(client)
    assert locked.status_code == 429
    assert locked.headers["retry-after"] == "900"
    assert "Try again in 15 minutes" in locked.json()["detail"]
    assert (await _login(client, "bob@example.com")).status_code == 200
    clock.now += 899
    assert (await _login(client)).status_code == 429
    clock.now += 2
    assert (await _login(client)).status_code == 200


async def test_success_clears_failures_and_unknown_emails_count(client, clock):
    await _register(client)
    for _ in range(4):
        await _login(client, password="wrong password")
    assert (await _login(client)).status_code == 200
    for _ in range(4):
        await _login(client, password="wrong password")
    assert (await _login(client)).status_code == 200
    for _ in range(5):
        assert (await _login(client, "ghost@example.com")).status_code == 401
    assert (await _login(client, "ghost@example.com")).status_code == 429


def test_limiter_keeps_a_bounded_number_of_keys(monkeypatch):
    monkeypatch.setattr("insightforge.services.login_limits.MAX_KEYS", 3)
    limiter = LoginLimiter(clock=iter(range(1, 100)).__next__)
    for number in range(6):
        limiter.record_failure(f"user{number}@example.com")
    assert len(limiter.failures) == 3
    assert "user5@example.com" in limiter.failures


async def test_change_password_ends_other_sessions(client):
    await _register(client)
    old = await _token(client)
    other = await _token(client)
    wrong = await client.post(
        "/api/auth/password",
        headers=_bearer(old),
        json={"current_password": "nope nope nope", "new_password": "brand new password"},
    )
    assert (wrong.status_code, wrong.json()["detail"]) == (400, "Current password is incorrect")
    short = await client.post(
        "/api/auth/password",
        headers=_bearer(old),
        json={"current_password": PASSWORD, "new_password": "short"},
    )
    assert short.status_code == 422
    changed = await client.post(
        "/api/auth/password",
        headers=_bearer(old),
        json={"current_password": PASSWORD, "new_password": "brand new password"},
    )
    assert changed.status_code == 200, changed.text
    fresh = changed.json()["access_token"]
    assert (await client.get("/api/auth/me", headers=_bearer(fresh))).status_code == 200
    for stale in (old, other):
        ended = await client.get("/api/auth/me", headers=_bearer(stale))
        assert (ended.status_code, ended.json()["detail"]) == (
            401,
            "Your session has ended; sign in again",
        )
    assert (await _login(client, password=PASSWORD)).status_code == 401
    assert (await _login(client, password="brand new password")).status_code == 200


async def test_wrong_current_password_counts_toward_the_lockout(client, clock):
    await _register(client)
    token = await _token(client)
    for _ in range(5):
        await client.post(
            "/api/auth/password",
            headers=_bearer(token),
            json={"current_password": "nope nope nope", "new_password": "brand new password"},
        )
    assert (await _login(client)).status_code == 429


async def test_api_tokens_cannot_change_passwords_or_sign_out_everywhere(client):
    await _register(client)
    session = await _token(client)
    created = await client.post(
        "/api/auth/tokens", headers=_bearer(session), json={"name": "ci", "scope": "ask"}
    )
    api_token = created.json()["token"]
    body = {"current_password": PASSWORD, "new_password": "brand new password"}
    changed = await client.post("/api/auth/password", headers=_bearer(api_token), json=body)
    assert changed.status_code in {401, 403}
    everywhere = await client.post("/api/auth/logout-all", headers=_bearer(api_token))
    assert everywhere.status_code in {401, 403}
    assert (await _login(client)).status_code == 200


async def test_logout_all_ends_every_session(client):
    await _register(client)
    first, second = await _token(client), await _token(client)
    assert (await client.post("/api/auth/logout-all", headers=_bearer(first))).status_code == 204
    assert (await client.get("/api/auth/me", headers=_bearer(first))).status_code == 401
    assert (await client.get("/api/auth/me", headers=_bearer(second))).status_code == 401
    assert (await client.post("/api/auth/logout-all")).status_code == 401
    assert (await client.get("/api/auth/me", headers=_bearer(await _token(client)))).status_code == 200


async def test_tokens_without_a_version_claim_still_work_at_version_zero(client):
    user = await _register(client)
    legacy = jwt.encode({"sub": user["id"]}, get_settings().jwt_secret, algorithm="HS256")
    assert (await client.get("/api/auth/me", headers=_bearer(legacy))).status_code == 200
    await client.post("/api/auth/logout-all", headers=_bearer(legacy))
    assert (await client.get("/api/auth/me", headers=_bearer(legacy))).status_code == 401


async def test_reset_link_sets_a_new_password_once(client):
    await _register(client)
    session = await _token(client)
    secret, _ = _link()
    assert secret.startswith("ifr_")
    db = SessionLocal()
    try:
        stored = db.query(PasswordReset).one()
        assert len(stored.token_hash) == 64 and secret not in stored.token_hash
    finally:
        db.close()
    reset = await client.post(
        "/api/auth/password/reset", json={"token": secret, "new_password": "reset password 1"}
    )
    assert reset.status_code == 204, reset.text
    assert (await client.get("/api/auth/me", headers=_bearer(session))).status_code == 401
    assert (await _login(client, password=PASSWORD)).status_code == 401
    assert (await _login(client, password="reset password 1")).status_code == 200
    again = await client.post(
        "/api/auth/password/reset", json={"token": secret, "new_password": "reset password 2"}
    )
    assert (again.status_code, again.json()["detail"]) == (400, RESET_ERROR)
    assert (await _login(client, password="reset password 1")).status_code == 200


async def test_reset_clears_the_lockout(client, clock):
    await _register(client)
    for _ in range(5):
        await _login(client, password="wrong password")
    assert (await _login(client)).status_code == 429
    secret, _ = _link()
    done = await client.post(
        "/api/auth/password/reset", json={"token": secret, "new_password": "reset password 1"}
    )
    assert done.status_code == 204
    assert (await _login(client, password="reset password 1")).status_code == 200


async def test_expired_garbage_and_replaced_links_share_one_error(client):
    await _register(client)
    replaced, _ = _link()
    expired, _ = _link()
    db = SessionLocal()
    try:
        record = db.query(PasswordReset).one()
        record.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        db.commit()
    finally:
        db.close()
    messages = set()
    for token in (replaced, expired, "garbage", "ifr_" + "x" * 40, ""):
        response = await client.post(
            "/api/auth/password/reset", json={"token": token, "new_password": "reset password 1"}
        )
        assert response.status_code == 400, token
        messages.add(response.json()["detail"])
    assert messages == {RESET_ERROR}
    short = await client.post("/api/auth/password/reset", json={"token": "x", "new_password": "short"})
    assert short.status_code == 422


async def test_a_new_link_replaces_earlier_unused_ones(client):
    await _register(client)
    first, _ = _link()
    second, _ = _link()
    gone = await client.post(
        "/api/auth/password/reset", json={"token": first, "new_password": "reset password 1"}
    )
    assert gone.status_code == 400
    worked = await client.post(
        "/api/auth/password/reset", json={"token": second, "new_password": "reset password 1"}
    )
    assert worked.status_code == 204


async def test_reset_link_command(client, capsys):
    await _register(client)
    argv = ["reset-link", "AMY@example.com", "--base-url", "http://localhost:3999/", "--hours", "2"]
    assert _run(argv) == 0
    printed = capsys.readouterr().out
    link = printed.splitlines()[0]
    assert link.startswith("http://localhost:3999/reset#ifr_")
    assert "expires" in printed
    reset = await client.post(
        "/api/auth/password/reset",
        json={"token": link.split("#", 1)[1], "new_password": "reset password 1"},
    )
    assert reset.status_code == 204
    assert _run(["reset-link", "nobody@example.com"]) == 1
    assert "No account" in capsys.readouterr().err
    db = SessionLocal()
    try:
        assert db.query(User).count() == 1
    finally:
        db.close()


async def test_a_link_used_between_lookup_and_claim_is_refused(client, monkeypatch):
    from insightforge.services import password_reset

    await _register(client)
    secret, _ = _link()
    real = password_reset._utc

    def other_request_wins(value):
        other = SessionLocal()
        try:
            other.query(PasswordReset).update({"used_at": datetime.now(UTC)})
            other.commit()
        finally:
            other.close()
        return real(value)

    monkeypatch.setattr(password_reset, "_utc", other_request_wins)
    db = SessionLocal()
    try:
        assert password_reset.redeem_reset(db, secret, "reset password 1") is None
    finally:
        db.close()
    assert (await _login(client, password=PASSWORD)).status_code == 200
    assert (await _login(client, password="reset password 1")).status_code == 401
