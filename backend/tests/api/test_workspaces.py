from .test_metrics_api import REVENUE
from .test_table_relationships_api import _upload_shop

PASSWORD = "correct horse battery staple"


async def _user(client, email):
    await client.post("/api/auth/register", json={"email": email, "password": PASSWORD})
    token = (await client.post("/api/auth/login", json={"email": email, "password": PASSWORD})).json()
    return {"Authorization": f"Bearer {token['access_token']}"}


async def _team(client, owner_headers):
    """A workspace with an editor and a viewer, and the owner's shop dataset shared with it."""
    workspace = (await client.post("/api/workspaces", headers=owner_headers, json={"name": "Finance"})).json()
    people = {}
    for role in ("editor", "viewer"):
        headers = await _user(client, f"{role}@example.com")
        invite = await client.post(
            f"/api/workspaces/{workspace['id']}/invites",
            headers=owner_headers,
            json={"email": f"{role.upper()}@example.com", "role": role},
        )
        assert invite.status_code == 201, invite.text
        accepted = await client.post(
            "/api/workspaces/invites/accept", headers=headers, json={"secret": invite.json()["secret"]}
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["role"] == role
        people[role] = headers
    dataset = await _upload_shop(client, owner_headers)
    shared = await client.put(
        f"/api/datasets/{dataset['id']}/workspace",
        headers=owner_headers,
        json={"workspace_id": workspace["id"]},
    )
    assert shared.status_code == 200, shared.text
    return workspace, dataset, people


async def test_roles_decide_what_members_can_do_with_a_shared_dataset(client, auth_headers):
    workspace, dataset, people = await _team(client, auth_headers)
    base = f"/api/datasets/{dataset['id']}"
    outsider = await _user(client, "outsider@example.com")
    for headers, access in ((people["viewer"], "read"), (people["editor"], "edit"), (auth_headers, "own")):
        assert (await client.get(base, headers=headers)).json()["access"] == access
        listed = (await client.get("/api/datasets", headers=headers)).json()
        assert [item["id"] for item in listed] == [dataset["id"]]
        assert (
            await client.get(f"{base}/preview", headers=headers, params={"table": "orders"})
        ).status_code == 200
    assert (await client.get(base, headers=outsider)).status_code == 404
    assert (await client.get("/api/datasets", headers=outsider)).json() == []

    metrics = {"metrics": [REVENUE | {"dimensions": ["region"]}]}
    viewer_edit = await client.put(f"{base}/metrics", headers=people["viewer"], json=metrics)
    assert viewer_edit.status_code == 403 and "ask an editor" in viewer_edit.json()["detail"]
    assert (await client.put(f"{base}/metrics", headers=people["editor"], json=metrics)).status_code == 200
    for headers in (people["viewer"], people["editor"]):
        privacy = await client.patch(
            f"{base}/privacy", headers=headers, json={"mode": "full", "acknowledged": True}
        )
        assert privacy.status_code == 403
        assert (await client.delete(base, headers=headers)).status_code == 403
        unshare = await client.put(f"{base}/workspace", headers=headers, json={"workspace_id": None})
        assert unshare.status_code == 403

    # Viewers ask questions in their own session; the owner does not see it.
    session = await client.post("/api/sessions", headers=people["viewer"], json={"dataset_id": dataset["id"]})
    assert session.status_code == 201, session.text
    assert (
        await client.get(f"/api/sessions/{session.json()['id']}", headers=auth_headers)
    ).status_code == 404
    assert (
        await client.post("/api/sessions", headers=outsider, json={"dataset_id": dataset["id"]})
    ).status_code == 404
    report = await client.post(
        f"{base}/reports/metric",
        headers=people["viewer"],
        json={"metric": "revenue", "start_date": "2025-07-01", "end_date": "2025-12-31"},
    )
    assert report.status_code == 202, report.text


async def test_invites_are_for_one_address_and_owners_manage_members(client, auth_headers):
    workspace = (await client.post("/api/workspaces", headers=auth_headers, json={"name": "Ops"})).json()
    base = f"/api/workspaces/{workspace['id']}"
    invite = (
        await client.post(f"{base}/invites", headers=auth_headers, json={"email": "amy@example.com"})
    ).json()
    assert invite["role"] == "viewer" and invite["secret"].startswith("ifi_")
    wrong = await _user(client, "bob@example.com")
    refused = await client.post(
        "/api/workspaces/invites/accept", headers=wrong, json={"secret": invite["secret"]}
    )
    assert refused.status_code == 403 and "amy@example.com" in refused.json()["detail"]
    amy = await _user(client, "amy@example.com")
    assert (
        await client.post("/api/workspaces/invites/accept", headers=amy, json={"secret": invite["secret"]})
    ).status_code == 200
    again = await client.post(
        "/api/workspaces/invites/accept", headers=amy, json={"secret": invite["secret"]}
    )
    assert again.status_code == 404
    seen_by_amy = (await client.get(base, headers=amy)).json()
    assert seen_by_amy["role"] == "viewer" and seen_by_amy["invites"] == []
    assert {item["email"] for item in seen_by_amy["members"]} == {"owner@example.com", "amy@example.com"}
    assert (
        await client.post(f"{base}/invites", headers=amy, json={"email": "x@example.com"})
    ).status_code == 403
    amy_id = next(item["user_id"] for item in seen_by_amy["members"] if item["email"] == "amy@example.com")
    owner_id = next(
        item["user_id"] for item in seen_by_amy["members"] if item["email"] == "owner@example.com"
    )
    assert (
        await client.patch(f"{base}/members/{owner_id}", headers=amy, json={"role": "viewer"})
    ).status_code == 403
    last_owner = await client.patch(
        f"{base}/members/{owner_id}", headers=auth_headers, json={"role": "viewer"}
    )
    assert last_owner.status_code == 409
    assert (await client.delete(f"{base}/members/{owner_id}", headers=auth_headers)).status_code == 409
    promoted = await client.patch(f"{base}/members/{amy_id}", headers=auth_headers, json={"role": "owner"})
    assert promoted.status_code == 200
    assert (await client.get(base, headers=wrong)).status_code == 404
    revoked = (
        await client.post(f"{base}/invites", headers=auth_headers, json={"email": "cy@example.com"})
    ).json()
    assert (await client.delete(f"{base}/invites/{revoked['id']}", headers=auth_headers)).status_code == 204
    cy = await _user(client, "cy@example.com")
    assert (
        await client.post("/api/workspaces/invites/accept", headers=cy, json={"secret": revoked["secret"]})
    ).status_code == 404


async def test_leaving_unshares_what_you_shared_and_dashboards_are_view_only(client, auth_headers):
    workspace, dataset, people = await _team(client, auth_headers)
    board = (await client.post("/api/dashboards", headers=auth_headers, json={"name": "Team board"})).json()
    shared = await client.put(
        f"/api/dashboards/{board['id']}/workspace",
        headers=auth_headers,
        json={"workspace_id": workspace["id"]},
    )
    assert shared.json()["workspace_id"] == workspace["id"]
    viewer_board = await client.get(f"/api/dashboards/{board['id']}", headers=people["viewer"])
    assert viewer_board.status_code == 200 and viewer_board.json()["access"] == "read"
    assert [
        item["id"] for item in (await client.get("/api/dashboards", headers=people["editor"])).json()
    ] == [board["id"]]
    for attempt in (
        client.post(f"/api/dashboards/{board['id']}/refresh", headers=people["editor"]),
        client.delete(f"/api/dashboards/{board['id']}", headers=people["editor"]),
        client.patch(f"/api/dashboards/{board['id']}", headers=people["editor"], json={"name": "x"}),
    ):
        assert (await attempt).status_code == 404
    # An outsider cannot share into a workspace they do not belong to.
    outsider = await _user(client, "outsider2@example.com")
    own = (await client.post("/api/dashboards", headers=outsider, json={"name": "Mine"})).json()
    sneaky = await client.put(
        f"/api/dashboards/{own['id']}/workspace", headers=outsider, json={"workspace_id": workspace["id"]}
    )
    assert sneaky.status_code == 404

    members = (await client.get(f"/api/workspaces/{workspace['id']}", headers=auth_headers)).json()["members"]
    owner_id = next(item["user_id"] for item in members if item["email"] == "owner@example.com")
    editor_id = next(item["user_id"] for item in members if item["email"] == "editor@example.com")
    await client.patch(
        f"/api/workspaces/{workspace['id']}/members/{editor_id}", headers=auth_headers, json={"role": "owner"}
    )
    assert (
        await client.delete(f"/api/workspaces/{workspace['id']}/members/{owner_id}", headers=auth_headers)
    ).status_code == 204
    for headers in (people["viewer"], people["editor"]):
        assert (await client.get(f"/api/datasets/{dataset['id']}", headers=headers)).status_code == 404
        assert (await client.get(f"/api/dashboards/{board['id']}", headers=headers)).status_code == 404
    assert (await client.get(f"/api/datasets/{dataset['id']}", headers=auth_headers)).json()[
        "workspace_id"
    ] is None


async def test_deleting_a_workspace_unshares_everything_and_tokens_cannot_manage_it(client, auth_headers):
    workspace, dataset, people = await _team(client, auth_headers)
    token = (
        await client.post("/api/auth/tokens", headers=auth_headers, json={"name": "t", "scope": "ask"})
    ).json()["token"]
    tokened = {"Authorization": f"Bearer {token}"}
    assert (await client.post("/api/workspaces", headers=tokened, json={"name": "x"})).status_code == 403
    assert (await client.get("/api/workspaces", headers=tokened)).status_code == 200
    assert (
        await client.delete(f"/api/workspaces/{workspace['id']}", headers=people["editor"])
    ).status_code == 403
    assert (
        await client.delete(f"/api/workspaces/{workspace['id']}", headers=auth_headers)
    ).status_code == 204
    assert (await client.get(f"/api/datasets/{dataset['id']}", headers=people["viewer"])).status_code == 404
    assert (await client.get("/api/workspaces", headers=people["viewer"])).json() == []
