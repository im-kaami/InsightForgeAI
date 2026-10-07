import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
import respx
from cryptography.hazmat.primitives.asymmetric import rsa

from insightforge.config import get_settings
from insightforge.services import oidc

ISSUER = "https://idp.example.com"
CLIENT = "insightforge-client"
FRONTEND = "http://localhost:3000"


def _key():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
    return private, {**public, "kid": "k1", "alg": "RS256", "use": "sig"}


PRIVATE, PUBLIC_JWK = _key()
OTHER_PRIVATE, _ = _key()


class _Keys:
    """Stands in for PyJWKClient, which fetches the key set with urllib rather than httpx."""

    def get_signing_key_from_jwt(self, _token):
        return jwt.PyJWK(PUBLIC_JWK)


@pytest.fixture
def sso(monkeypatch, app):
    for name, value in {
        "OIDC_ISSUER": ISSUER,
        "OIDC_CLIENT_ID": CLIENT,
        "OIDC_CLIENT_SECRET": "client-secret-value",
        "OIDC_REDIRECT_URI": "http://localhost:3000/api/auth/oidc/callback",
        "OIDC_PROVIDER_NAME": "Example SSO",
        "OIDC_FRONTEND_URL": FRONTEND,
    }.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    oidc._discovery.clear()
    oidc._jwks.clear()
    oidc._jwks[f"{ISSUER}/jwks"] = _Keys()
    yield
    get_settings.cache_clear()


def _discovery(router):
    router.get(f"{ISSUER}/.well-known/openid-configuration").mock(
        return_value=httpx.Response(
            200,
            json={
                "issuer": ISSUER,
                "authorization_endpoint": f"{ISSUER}/authorize",
                "token_endpoint": f"{ISSUER}/token",
                "jwks_uri": f"{ISSUER}/jwks",
            },
        )
    )


def _id_token(nonce, key=PRIVATE, **claims):
    now = int(time.time())
    body = {
        "iss": ISSUER,
        "aud": CLIENT,
        "sub": "user-123",
        "email": "Ada@Company.com",
        "email_verified": True,
        "nonce": nonce,
        "iat": now,
        "exp": now + 300,
        **claims,
    }
    return jwt.encode(body, key, algorithm="RS256", headers={"kid": "k1"})


async def _sign_in(client, token_for, cookie=True):
    with respx.mock(assert_all_called=False) as router:
        _discovery(router)
        started = await client.get("/api/auth/oidc/start", follow_redirects=False)
        assert started.status_code == 302
        location = urlparse(started.headers["location"])
        params = {key: values[0] for key, values in parse_qs(location.query).items()}
        assert location.netloc == "idp.example.com" and params["code_challenge_method"] == "S256"
        seen = {}

        def token(request):
            form = {key: values[0] for key, values in parse_qs(request.content.decode()).items()}
            digest = hashlib.sha256(form["code_verifier"].encode()).digest()
            seen["pkce"] = base64.urlsafe_b64encode(digest).rstrip(b"=").decode() == params["code_challenge"]
            return httpx.Response(200, json={"id_token": token_for(params["nonce"])})

        router.post(f"{ISSUER}/token").mock(side_effect=token)
        cookies = {oidc.STATE_COOKIE: started.cookies[oidc.STATE_COOKIE]} if cookie else {}
        client.cookies.clear()
        finished = await client.get(
            "/api/auth/oidc/callback",
            params={"code": "the-code", "state": params["state"]},
            cookies=cookies,
            follow_redirects=False,
        )
    assert finished.status_code == 302
    target = urlparse(finished.headers["location"])
    assert f"{target.scheme}://{target.netloc}{target.path}" == f"{FRONTEND}/login"
    return {key: values[0] for key, values in parse_qs(target.fragment).items()}, seen


async def test_single_sign_on_creates_an_account_and_returns_a_session(client, sso):
    config = (await client.get("/api/auth/oidc/config")).json()
    assert config == {"enabled": True, "name": "Example SSO"}
    result, seen = await _sign_in(client, lambda nonce: _id_token(nonce))
    assert seen["pkce"] is True and "oidc_error" not in result
    me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {result['oidc_token']}"})
    assert me.status_code == 200 and me.json()["email"] == "ada@company.com"
    assert (client.cookies.get("if_refresh") or "").startswith("ifs_")
    refreshed = await client.post("/api/auth/refresh", headers={"X-InsightForge-Refresh": "1"})
    assert refreshed.status_code == 200
    # The same person signing in again gets the same account.
    again, _ = await _sign_in(client, lambda nonce: _id_token(nonce))
    second = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {again['oidc_token']}"})
    assert second.json()["id"] == me.json()["id"]


@pytest.mark.parametrize(
    ("token_for", "message"),
    [
        (lambda nonce: _id_token("someone-else"), "not issued for this sign-in"),
        (lambda nonce: _id_token(nonce, email_verified=False), "verified email"),
        (lambda nonce: _id_token(nonce, aud="another-app"), "not valid"),
        (lambda nonce: _id_token(nonce, iss="https://evil.example.com"), "not valid"),
        (lambda nonce: _id_token(nonce, exp=int(time.time()) - 10), "not valid"),
        (lambda nonce: _id_token(nonce, key=OTHER_PRIVATE), "not valid"),
    ],
)
async def test_bad_sign_in_tokens_are_refused(client, sso, token_for, message):
    result, _ = await _sign_in(client, token_for)
    assert "oidc_token" not in result and message in result["oidc_error"]


async def test_the_state_cookie_and_allowed_domains_are_enforced(client, sso, monkeypatch):
    result, _ = await _sign_in(client, lambda nonce: _id_token(nonce), cookie=False)
    assert "not started in this browser" in result["oidc_error"]
    monkeypatch.setenv("OIDC_ALLOWED_DOMAINS", '["partner.com"]')
    get_settings.cache_clear()
    result, _ = await _sign_in(client, lambda nonce: _id_token(nonce))
    assert "domain is not allowed" in result["oidc_error"]
    missing = await client.get(
        "/api/auth/oidc/callback", params={"error": "access_denied"}, follow_redirects=False
    )
    assert "did not complete" in parse_qs(urlparse(missing.headers["location"]).fragment)["oidc_error"][0]


async def test_single_sign_on_is_off_until_configured(client):
    get_settings.cache_clear()
    assert (await client.get("/api/auth/oidc/config")).json()["enabled"] is False
    assert (await client.get("/api/auth/oidc/start", follow_redirects=False)).status_code == 503
