"""Single sign-on with OpenID Connect (Phase 5f): Google, Microsoft Entra ID, Okta, Auth0 and others.

Authorization-code flow with PKCE (S256) and a nonce. ``start`` builds the provider URL and a signed,
ten-minute ``state`` that carries the nonce and the PKCE verifier; the same value is set as an
HttpOnly cookie, and ``finish`` requires both to match, which stops login cross-site request forgery.
The ID token is verified with the provider's published signing keys (JWKS): signature, issuer,
audience, expiry and nonce. The email must be verified by the provider and, when
``OIDC_ALLOWED_DOMAINS`` is set, belong to one of those domains. The person then gets the same
InsightForge session token as a password sign-in; an account is created on first sign-in, or the
existing account with that email is used.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWKClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from insightforge.config import Settings
from insightforge.db.models import User
from insightforge.services.auth import hash_password

STATE_SECONDS = 600
STATE_COOKIE = "if_oidc_state"
_discovery: dict[str, tuple[float, dict[str, Any]]] = {}
_jwks: dict[str, PyJWKClient] = {}


class OIDCError(ValueError):
    pass


@dataclass(frozen=True)
class Started:
    url: str
    state: str


def enabled(settings: Settings) -> bool:
    return bool(
        settings.oidc_issuer
        and settings.oidc_client_id
        and settings.oidc_client_secret
        and settings.oidc_redirect_uri
    )


def discover(settings: Settings, client: httpx.Client | None = None) -> dict[str, Any]:
    issuer = str(settings.oidc_issuer).rstrip("/")
    cached = _discovery.get(issuer)
    if cached and cached[0] > time.time():
        return cached[1]
    http = client or httpx.Client(timeout=10)
    try:
        response = http.get(f"{issuer}/.well-known/openid-configuration")
        response.raise_for_status()
        document = response.json()
    finally:
        if client is None:
            http.close()
    for key in ("issuer", "authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not document.get(key):
            raise OIDCError(f"The identity provider's configuration has no {key}")
    if str(document["issuer"]).rstrip("/") != issuer:
        raise OIDCError("The identity provider's issuer does not match OIDC_ISSUER")
    _discovery[issuer] = (time.time() + 3600, document)
    return document


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def start(settings: Settings, client: httpx.Client | None = None) -> Started:
    if not enabled(settings):
        raise OIDCError("Single sign-on is not configured")
    document = discover(settings, client)
    nonce = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    state = jwt.encode(
        {"n": nonce, "v": verifier, "exp": int(time.time()) + STATE_SECONDS, "purpose": "oidc"},
        settings.jwt_secret,
        algorithm="HS256",
    )
    query = urlencode(
        {
            "response_type": "code",
            "client_id": settings.oidc_client_id,
            "redirect_uri": settings.oidc_redirect_uri,
            "scope": settings.oidc_scopes,
            "state": state,
            "nonce": nonce,
            "code_challenge": _challenge(verifier),
            "code_challenge_method": "S256",
        }
    )
    return Started(url=f"{document['authorization_endpoint']}?{query}", state=state)


def _claims(settings: Settings, document: dict[str, Any], id_token: str, nonce: str) -> dict[str, Any]:
    jwks_uri = document["jwks_uri"]
    keys = _jwks.setdefault(jwks_uri, PyJWKClient(jwks_uri))
    try:
        key = keys.get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token,
            key.key,
            algorithms=["RS256", "ES256", "PS256"],
            audience=settings.oidc_client_id,
            issuer=document["issuer"],
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except jwt.PyJWTError as error:
        raise OIDCError(f"The sign-in token is not valid: {error}") from error
    if claims.get("nonce") != nonce:
        raise OIDCError("The sign-in token was not issued for this sign-in attempt")
    return claims


def finish(
    db: Session,
    settings: Settings,
    code: str,
    state: str,
    cookie_state: str | None,
    client: httpx.Client | None = None,
) -> User:
    """Exchange the code, check the ID token and return the account that signed in."""
    if not enabled(settings):
        raise OIDCError("Single sign-on is not configured")
    if not cookie_state or not secrets.compare_digest(cookie_state, state):
        raise OIDCError("This sign-in was not started in this browser; please try again")
    try:
        payload = jwt.decode(state, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as error:
        raise OIDCError("This sign-in attempt has expired; please try again") from error
    if payload.get("purpose") != "oidc":
        raise OIDCError("This sign-in attempt is not valid")
    document = discover(settings, client)
    http = client or httpx.Client(timeout=15)
    try:
        response = http.post(
            document["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.oidc_redirect_uri,
                "client_id": settings.oidc_client_id,
                "client_secret": settings.oidc_client_secret,
                "code_verifier": payload["v"],
            },
            headers={"Accept": "application/json"},
        )
    finally:
        if client is None:
            http.close()
    if response.status_code >= 400:
        raise OIDCError("The identity provider refused the sign-in code")
    id_token = response.json().get("id_token")
    if not id_token:
        raise OIDCError("The identity provider returned no ID token")
    claims = _claims(settings, document, id_token, payload["n"])
    email = str(claims.get("email") or "").strip().lower()
    if not email or claims.get("email_verified") is not True:
        raise OIDCError("Your identity provider did not confirm a verified email address")
    domains = [item.strip().lower().lstrip("@") for item in settings.oidc_allowed_domains if item.strip()]
    if domains and email.rsplit("@", 1)[-1] not in domains:
        raise OIDCError("This email domain is not allowed to sign in here")
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        # A random password nobody knows: this account signs in with single sign-on.
        user = User(email=email, password_hash=hash_password(secrets.token_urlsafe(32)))
        db.add(user)
        db.commit()
        db.refresh(user)
    return user
