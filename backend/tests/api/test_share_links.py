import json
from datetime import UTC, datetime, timedelta

from insightforge.db.models import ShareLink
from insightforge.db.session import SessionLocal

from .test_dashboards_api import _run, _setup


async def _share(client, headers, kind, target_id, **body):
    response = await client.post(
        "/api/shares",
        headers=headers,
        json={"kind": kind, "target_id": target_id, "acknowledged": True, **body},
    )
    return response


async def _view(client, secret):
    return await client.post("/api/public/shares/view", json={"secret": secret})


async def test_a_shared_answer_shows_results_but_no_internals(client, auth_headers):
    dataset, _ = await _setup(client, auth_headers)
    run = await _run(client, auth_headers, dataset, "What are sales by region?")
    created = await _share(client, auth_headers, "run", run["id"], expires_in_days=3)
    assert created.status_code == 201, created.text
    secret = created.json()["secret"]
    assert secret.startswith("ifs_") and created.json()["title"] == "What are sales by region?"
    viewed = await _view(client, secret)  # no Authorization header
    assert viewed.status_code == 200, viewed.text
    content = viewed.json()["content"]
    assert content["type"] == "run" and content["summary"]
    table = next(item for item in content["artifacts"] if item["type"] == "table")
    assert table["metric"]["name"] == "revenue" and table["rows"]
    text = json.dumps(viewed.json())
    for hidden in ("sql", "csv_path", "png_path", "trace", "owner@example.com", "sources", "plan"):
        assert f'"{hidden}"' not in text, hidden
    listed = (await client.get("/api/shares", headers=auth_headers)).json()
    assert listed[0]["view_count"] == 1 and "secret" not in listed[0] and "token_hash" not in listed[0]


async def test_a_shared_dashboard_and_revocation(client, auth_headers):
    dataset, board = await _setup(client, auth_headers)
    await client.post(
        f"/api/dashboards/{board['id']}/items",
        headers=auth_headers,
        json={"kind": "question", "dataset_id": dataset["id"], "query_id": "q-region"},
    )
    created = (await _share(client, auth_headers, "dashboard", board["id"])).json()
    viewed = (await _view(client, created["secret"])).json()
    assert viewed["title"] == "Weekly numbers"
    tile = viewed["content"]["items"][0]
    assert tile["kind"] == "question" and tile["snapshot"]["rows"] and "sql" not in tile["snapshot"]
    assert tile["dataset_id"] is None and tile["config"] == {}
    assert (await client.delete(f"/api/shares/{created['id']}", headers=auth_headers)).status_code == 204
    assert (await _view(client, created["secret"])).status_code == 404


async def test_links_need_consent_expire_and_are_owner_scoped(client, auth_headers):
    dataset, board = await _setup(client, auth_headers)
    no_consent = await client.post(
        "/api/shares", headers=auth_headers, json={"kind": "dashboard", "target_id": board["id"]}
    )
    assert no_consent.status_code == 422
    too_long = await _share(client, auth_headers, "dashboard", board["id"], expires_in_days=91)
    assert too_long.status_code == 422
    created = (await _share(client, auth_headers, "dashboard", board["id"])).json()
    db = SessionLocal()
    try:
        link = db.get(ShareLink, created["id"])
        assert link.token_hash != created["secret"]
        link.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    finally:
        db.close()
    assert (await _view(client, created["secret"])).status_code == 404
    assert (await _view(client, "ifs_not-a-real-secret")).status_code == 404
    credentials = {"email": "share-intruder@example.com", "password": "correct horse battery staple"}
    await client.post("/api/auth/register", json=credentials)
    login = (await client.post("/api/auth/login", json=credentials)).json()["access_token"]
    other = {"Authorization": f"Bearer {login}"}
    assert (await _share(client, other, "dashboard", board["id"])).status_code == 404
    assert (await client.get("/api/shares", headers=other)).json() == []
    assert (await client.delete(f"/api/shares/{created['id']}", headers=other)).status_code == 404
    # API tokens cannot create links (a link exposes data to anyone).
    token = (
        await client.post("/api/auth/tokens", headers=auth_headers, json={"name": "t", "scope": "ask"})
    ).json()["token"]
    tokened = await _share(client, {"Authorization": f"Bearer {token}"}, "dashboard", board["id"])
    assert tokened.status_code == 403
    assert dataset["id"]
