from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from insightforge.db.models import ApiToken
from insightforge.db.session import SessionLocal

from .test_table_relationships_api import _upload_shop


async def _token(client, headers, scope="read", **body):
    response = await client.post(
        "/api/auth/tokens", headers=headers, json={"name": f"{scope} script", "scope": scope, **body}
    )
    assert response.status_code == 201, response.text
    created = response.json()
    return created, {"Authorization": f"Bearer {created['token']}"}


async def test_a_token_is_shown_once_and_only_its_hash_is_stored(client, auth_headers):
    created, token_headers = await _token(client, auth_headers, expires_in_days=30)
    assert created["token"].startswith("ifk_") and created["prefix"] == created["token"][:10]
    assert created["scope"] == "read" and created["revoked_at"] is None
    listed = (await client.get("/api/auth/tokens", headers=auth_headers)).json()
    assert [item["id"] for item in listed] == [created["id"]]
    assert "token" not in listed[0] and "token_hash" not in listed[0]
    db = SessionLocal()
    try:
        record = db.get(ApiToken, created["id"])
        assert record.token_hash != created["token"] and len(record.token_hash) == 64
        assert created["token"] not in str(record.__dict__)
    finally:
        db.close()
    me = await client.get("/api/auth/me", headers=token_headers)
    assert me.status_code == 200
    assert (await client.get("/api/auth/tokens", headers=auth_headers)).json()[0]["last_used_at"]


async def test_read_tokens_can_only_read(client, auth_headers):
    dataset = await _upload_shop(client, auth_headers)
    _, token = await _token(client, auth_headers, scope="read")
    assert (await client.get("/api/datasets", headers=token)).status_code == 200
    assert (await client.get(f"/api/datasets/{dataset['id']}", headers=token)).status_code == 200
    session = await client.post("/api/sessions", headers=token, json={"dataset_id": dataset["id"]})
    assert session.status_code == 403 and "read" in session.json()["detail"]
    assert (await client.delete(f"/api/datasets/{dataset['id']}", headers=token)).status_code == 403
    metrics = await client.put(f"/api/datasets/{dataset['id']}/metrics", headers=token, json={"metrics": []})
    assert metrics.status_code == 403


async def test_ask_tokens_can_also_ask_questions_but_nothing_else(client, auth_headers):
    dataset = await _upload_shop(client, auth_headers)
    _, token = await _token(client, auth_headers, scope="ask")
    session = await client.post("/api/sessions", headers=token, json={"dataset_id": dataset["id"]})
    assert session.status_code == 201, session.text
    run = await client.post(
        f"/api/sessions/{session.json()['id']}/runs", headers=token, json={"goal": "How many orders?"}
    )
    assert run.status_code == 202, run.text
    assert (await client.get(f"/api/runs/{run.json()['id']}", headers=token)).status_code == 200
    assert (await client.delete(f"/api/sessions/{session.json()['id']}", headers=token)).status_code == 403
    rename = await client.patch(
        f"/api/datasets/{dataset['id']}/privacy", headers=token, json={"llm_policy": "full"}
    )
    assert rename.status_code == 403


async def test_tokens_cannot_manage_tokens(client, auth_headers):
    created, token = await _token(client, auth_headers, scope="ask")
    assert (await client.get("/api/auth/tokens", headers=token)).status_code == 403
    more = await client.post("/api/auth/tokens", headers=token, json={"name": "escalate", "scope": "ask"})
    assert more.status_code == 403
    assert (await client.delete(f"/api/auth/tokens/{created['id']}", headers=token)).status_code == 403


async def test_revoked_expired_and_unknown_tokens_are_refused(client, auth_headers):
    created, token = await _token(client, auth_headers)
    assert (await client.delete(f"/api/auth/tokens/{created['id']}", headers=auth_headers)).status_code == 204
    assert (await client.get("/api/datasets", headers=token)).status_code == 401
    expired, expired_token = await _token(client, auth_headers)
    db = SessionLocal()
    try:
        record = db.scalar(select(ApiToken).where(ApiToken.id == expired["id"]))
        record.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    finally:
        db.close()
    assert (await client.get("/api/datasets", headers=expired_token)).status_code == 401
    unknown = {"Authorization": "Bearer ifk_not-a-real-token"}
    assert (await client.get("/api/datasets", headers=unknown)).status_code == 401
    too_long = await client.post(
        "/api/auth/tokens", headers=auth_headers, json={"name": "x", "expires_in_days": 400}
    )
    assert too_long.status_code == 422


async def test_tokens_are_owner_scoped(client, auth_headers):
    created, token = await _token(client, auth_headers)
    credentials = {"email": "token-intruder@example.com", "password": "correct horse battery staple"}
    await client.post("/api/auth/register", json=credentials)
    login = (await client.post("/api/auth/login", json=credentials)).json()["access_token"]
    other = {"Authorization": f"Bearer {login}"}
    assert (await client.get("/api/auth/tokens", headers=other)).json() == []
    assert (await client.delete(f"/api/auth/tokens/{created['id']}", headers=other)).status_code == 404
    dataset = await _upload_shop(client, other)
    # The first user's token only reaches the first user's data.
    assert (await client.get(f"/api/datasets/{dataset['id']}", headers=token)).status_code == 404
