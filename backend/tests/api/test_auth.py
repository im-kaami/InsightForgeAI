async def test_auth_flow(client):
    body = {"email": "person@example.com", "password": "long-enough-password"}
    response = await client.post("/api/auth/register", json=body)
    assert response.status_code == 201
    assert (await client.post("/api/auth/register", json=body)).status_code == 409
    assert (
        await client.post(
            "/api/auth/login", json={"email": body["email"], "password": "wrong"}
        )
    ).status_code == 401
    login = await client.post("/api/auth/login", json=body)
    token = login.json()["access_token"]
    me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json()["email"] == body["email"]
    assert (await client.get("/api/auth/me")).status_code == 401
