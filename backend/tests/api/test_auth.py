async def test_localhost_cors_preflight(client):
    response = await client.options(
        "/api/auth/register",
        headers={
            "Origin": "http://127.0.0.1:5555",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:5555"


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
