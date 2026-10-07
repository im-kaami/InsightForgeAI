from datetime import UTC, datetime, timedelta

import httpx
import pytest

from insightforge.config import Settings, get_settings
from insightforge.db.models import RefreshToken
from insightforge.db.session import SessionLocal
from insightforge.services import refresh

PASSWORD = "correct horse battery staple"
MARK = {refresh.HEADER: "1"}


async def _register(client, email="amy@example.com"):
    response = await client.post("/api/auth/register", json={"email": email, "password": PASSWORD})
    assert response.status_code == 201, response.text


async def _login(client, email="amy@example.com"):
    response = await client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return response


def _cookie(client):
    return client.cookies.get(refresh.COOKIE)


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _rows():
    db = SessionLocal()
    try:
        return db.query(RefreshToken).all()
    finally:
        db.close()


async def test_login_sets_a_strict_http_only_cookie_and_no_secret_in_the_body(client):
    await _register(client)
    response = await _login(client)
    header = response.headers["set-cookie"]
    assert header.startswith(f"{refresh.COOKIE}=ifs_")
    lowered = header.lower()
    assert "httponly" in lowered and "samesite=strict" in lowered and "path=/api/auth" in lowered
    assert "secure" not in lowered.replace("samesite", "")
    assert "max-age=2592000" in lowered
    assert _cookie(client) not in response.text
    assert set(response.json()) == {"access_token", "token_type"}


async def test_only_hashes_are_stored(client):
    await _register(client)
    await _login(client)
    secret = _cookie(client)
    [row] = _rows()
    assert row.token_hash == refresh.hash_secret(secret) and secret not in row.token_hash
    assert len(row.token_hash) == 64 and row.token_version == 0


async def test_refresh_needs_the_app_header(client):
    await _register(client)
    await _login(client)
    assert (await client.post("/api/auth/refresh")).status_code == 403
    assert (await client.post("/api/auth/refresh", headers={refresh.HEADER: "0"})).status_code == 403
    assert (await client.post("/api/auth/logout")).status_code == 403


async def test_refresh_returns_a_working_token_and_rotates_the_cookie(client):
    await _register(client)
    await _login(client)
    first = _cookie(client)
    response = await client.post("/api/auth/refresh", headers=MARK)
    assert response.status_code == 200, response.text
    assert _cookie(client) != first and _cookie(client).startswith("ifs_")
    assert first not in response.text and _cookie(client) not in response.text
    me = await client.get("/api/auth/me", headers=_bearer(response.json()["access_token"]))
    assert me.status_code == 200 and me.json()["email"] == "amy@example.com"
    assert len(_rows()) == 2 and sum(row.revoked_at is not None for row in _rows()) == 1
    assert len({row.family_id for row in _rows()}) == 1


async def test_reusing_an_old_cookie_revokes_the_whole_family(client):
    await _register(client)
    await _login(client)
    old = _cookie(client)
    assert (await client.post("/api/auth/refresh", headers=MARK)).status_code == 200
    newest = _cookie(client)
    db = SessionLocal()
    try:
        stale = datetime.now(UTC) - timedelta(minutes=5)
        db.query(RefreshToken).filter(RefreshToken.token_hash == refresh.hash_secret(old)).update(
            {"revoked_at": stale}
        )
        db.commit()
    finally:
        db.close()
    client.cookies.set(refresh.COOKIE, old, path="/api/auth")
    thief = await client.post("/api/auth/refresh", headers=MARK)
    assert thief.status_code == 401
    assert thief.json()["detail"] == "Your session has ended; sign in again"
    assert all(row.revoked_at is not None for row in _rows())
    client.cookies.set(refresh.COOKIE, newest, path="/api/auth")
    assert (await client.post("/api/auth/refresh", headers=MARK)).status_code == 401


async def test_two_tabs_refreshing_together_are_both_served(client):
    await _register(client)
    await _login(client)
    old = _cookie(client)
    assert (await client.post("/api/auth/refresh", headers=MARK)).status_code == 200
    client.cookies.set(refresh.COOKIE, old, path="/api/auth")
    second = await client.post("/api/auth/refresh", headers=MARK)
    assert second.status_code == 200
    assert (await client.post("/api/auth/refresh", headers=MARK)).status_code == 200


async def test_expired_and_unknown_cookies_are_refused_and_cleared(client):
    await _register(client)
    await _login(client)
    db = SessionLocal()
    try:
        db.query(RefreshToken).update({"expires_at": datetime.now(UTC) - timedelta(seconds=1)})
        db.commit()
    finally:
        db.close()
    expired = await client.post("/api/auth/refresh", headers=MARK)
    assert expired.status_code == 401
    assert "max-age=0" in expired.headers["set-cookie"].lower()
    client.cookies.set(refresh.COOKIE, "ifs_not-a-real-secret", path="/api/auth")
    assert (await client.post("/api/auth/refresh", headers=MARK)).status_code == 401
    client.cookies.clear()
    assert (await client.post("/api/auth/refresh", headers=MARK)).status_code == 401


async def test_logout_revokes_and_clears(client):
    await _register(client)
    await _login(client)
    secret = _cookie(client)
    out = await client.post("/api/auth/logout", headers=MARK)
    assert out.status_code == 204 and "max-age=0" in out.headers["set-cookie"].lower()
    assert all(row.revoked_at is not None for row in _rows())
    client.cookies.set(refresh.COOKIE, secret, path="/api/auth")
    assert (await client.post("/api/auth/refresh", headers=MARK)).status_code == 401
    client.cookies.clear()
    assert (await client.post("/api/auth/logout", headers=MARK)).status_code == 204


async def _browsers(client, count=2):
    """Sign in `count` times and return each one's (cookie, access token)."""
    sessions = []
    for _ in range(count):
        client.cookies.clear()
        body = (await _login(client)).json()
        sessions.append((_cookie(client), body["access_token"]))
    return sessions


async def test_sign_out_everywhere_revokes_every_refresh_token(client):
    await _register(client)
    (_, token), (other, _) = await _browsers(client)
    out = await client.post("/api/auth/logout-all", headers=_bearer(token))
    assert out.status_code == 204
    assert all(row.revoked_at is not None for row in _rows())
    client.cookies.set(refresh.COOKIE, other, path="/api/auth")
    assert (await client.post("/api/auth/refresh", headers=MARK)).status_code == 401


async def test_changing_the_password_ends_other_browsers_but_not_this_one(client):
    await _register(client)
    (_, token), (other, _) = await _browsers(client)
    changed = await client.post(
        "/api/auth/password",
        headers=_bearer(token),
        json={"current_password": PASSWORD, "new_password": "brand new password"},
    )
    assert changed.status_code == 200, changed.text
    assert "set-cookie" in changed.headers
    mine = _cookie(client)
    assert mine != other
    client.cookies.set(refresh.COOKIE, other, path="/api/auth")
    assert (await client.post("/api/auth/refresh", headers=MARK)).status_code == 401
    client.cookies.set(refresh.COOKIE, mine, path="/api/auth")
    ok = await client.post("/api/auth/refresh", headers=MARK)
    assert ok.status_code == 200
    me = await client.get("/api/auth/me", headers=_bearer(ok.json()["access_token"]))
    assert me.status_code == 200


async def test_a_password_reset_ends_every_session(client):
    from insightforge.services.password_reset import create_reset

    await _register(client)
    (cookie, _), = await _browsers(client, 1)
    db = SessionLocal()
    try:
        secret, _ = create_reset(db, "amy@example.com")
    finally:
        db.close()
    done = await client.post(
        "/api/auth/password/reset", json={"token": secret, "new_password": "reset password 1"}
    )
    assert done.status_code == 204
    client.cookies.set(refresh.COOKIE, cookie, path="/api/auth")
    assert (await client.post("/api/auth/refresh", headers=MARK)).status_code == 401


async def test_a_changed_token_version_rejects_the_refresh_token(client):
    await _register(client)
    await _login(client)
    db = SessionLocal()
    try:
        from insightforge.db.models import User

        db.query(User).update({"token_version": 5})
        db.commit()
    finally:
        db.close()
    assert (await client.post("/api/auth/refresh", headers=MARK)).status_code == 401


async def test_the_secure_flag_is_set_in_production(client, monkeypatch):
    await _register(client)
    monkeypatch.setenv("ENVIRONMENT", "production")
    get_settings.cache_clear()
    response = await _login(client)
    assert "secure" in response.headers["set-cookie"].lower().replace("samesite", "")


async def test_old_rows_are_cleaned_up_when_a_token_is_issued(client):
    await _register(client)
    await _login(client)
    db = SessionLocal()
    try:
        user_id = db.query(RefreshToken).one().user_id
        old = datetime.now(UTC) - timedelta(days=9)
        db.add_all(
            [
                RefreshToken(
                    user_id=user_id,
                    family_id="f1",
                    token_hash="a" * 64,
                    expires_at=old,
                ),
                RefreshToken(
                    user_id=user_id,
                    family_id="f2",
                    token_hash="b" * 64,
                    expires_at=datetime.now(UTC) + timedelta(days=5),
                    revoked_at=old,
                ),
                RefreshToken(
                    user_id=user_id,
                    family_id="f3",
                    token_hash="c" * 64,
                    expires_at=datetime.now(UTC) + timedelta(days=5),
                    revoked_at=datetime.now(UTC) - timedelta(days=1),
                ),
            ]
        )
        db.commit()
    finally:
        db.close()
    await _login(client)
    families = {row.family_id for row in _rows()}
    assert "f1" not in families and "f2" not in families and "f3" in families


async def test_api_tokens_cannot_refresh(client):
    await _register(client)
    token = (await _login(client)).json()["access_token"]
    created = await client.post(
        "/api/auth/tokens", headers=_bearer(token), json={"name": "ci", "scope": "ask"}
    )
    client.cookies.clear()
    refused = await client.post(
        "/api/auth/refresh", headers={**MARK, **_bearer(created.json()["token"])}
    )
    assert refused.status_code == 401


def test_the_default_lifetimes():
    settings = Settings(_env_file=None)
    assert settings.access_token_minutes == 30 and settings.refresh_token_days == 30
    with pytest.raises(ValueError):
        Settings(_env_file=None, refresh_token_days=0)
    with pytest.raises(ValueError):
        Settings(_env_file=None, refresh_token_days=366)


async def test_an_access_token_past_its_lifetime_is_rejected(client):
    import jwt

    await _register(client)
    me = (await _login(client)).json()["access_token"]
    claims = jwt.decode(me, get_settings().jwt_secret, algorithms=["HS256"])
    expired = jwt.encode(
        {**claims, "exp": datetime.now(UTC) - timedelta(minutes=1)},
        get_settings().jwt_secret,
        algorithm="HS256",
    )
    assert (await client.get("/api/auth/me", headers=_bearer(expired))).status_code == 401
    assert isinstance(client, httpx.AsyncClient)
