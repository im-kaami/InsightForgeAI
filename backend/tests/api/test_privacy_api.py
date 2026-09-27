import asyncio


async def _wait(client, headers, run_id):
    for _ in range(100):
        response = await client.get(f"/api/runs/{run_id}", headers=headers)
        if response.json()["status"] in {"completed", "failed"}:
            return response.json()
        await asyncio.sleep(0.1)
    raise AssertionError("run did not finish")


async def test_default_local_privacy_avoids_configured_llm(client, auth_headers, hr_dataset, app):
    session = (
        await client.post(
            "/api/sessions",
            headers=auth_headers,
            json={"dataset_id": hr_dataset["id"], "title": "Local"},
        )
    ).json()
    created = await client.post(
        f"/api/sessions/{session['id']}/runs",
        headers=auth_headers,
        json={"goal": "profile locally"},
    )
    result = await _wait(client, auth_headers, created.json()["id"])
    assert result["status"] == "completed"
    assert result["provenance"]["privacy_mode"] == "local"
    assert app.state.llm.calls == []
    missing_ack = await client.patch(
        f"/api/datasets/{hr_dataset['id']}/privacy",
        headers=auth_headers,
        json={"mode": "full", "acknowledged": False},
    )
    assert missing_ack.status_code == 422

    other = {"email": "privacy-other@example.com", "password": "long-enough-password"}
    await client.post("/api/auth/register", json=other)
    token = (await client.post("/api/auth/login", json=other)).json()["access_token"]
    alien = await client.patch(
        f"/api/datasets/{hr_dataset['id']}/privacy",
        headers={"Authorization": f"Bearer {token}"},
        json={"mode": "full", "acknowledged": True},
    )
    assert alien.status_code == 404
