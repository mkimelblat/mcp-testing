"""
Shared OAuth 2.1 (DCR + PKCE) helpers for mcp.calendly.com.

Used by:
  - setup_auth.py     — CLI flow (local http.server on :8080)
  - app/main.py       — web flow (FastAPI callback on the same port as the UI)
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from typing import TypedDict

import httpx

MCP_SERVER_URL = "https://mcp.calendly.com"


class OAuthEndpoints(TypedDict):
    authorization_endpoint: str
    token_endpoint:         str
    registration_endpoint:  str


# ── PKCE ──────────────────────────────────────────────────────────────────────

def pkce_pair() -> tuple[str, str]:
    """Return (verifier, challenge). Store verifier, send challenge."""
    verifier  = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


# ── Discovery ─────────────────────────────────────────────────────────────────

def discover() -> OAuthEndpoints:
    """Discover Calendly's OAuth endpoints via MCP protected-resource metadata."""
    resource = httpx.get(
        f"{MCP_SERVER_URL}/.well-known/oauth-protected-resource", timeout=10
    )
    resource.raise_for_status()
    meta = resource.json()

    auth_server = (
        meta.get("authorization_servers") or ["https://calendly.com"]
    )[0].rstrip("/")

    server = httpx.get(
        f"{auth_server}/.well-known/oauth-authorization-server", timeout=10
    )
    server.raise_for_status()
    data = server.json()
    return {
        "authorization_endpoint": data["authorization_endpoint"],
        "token_endpoint":         data["token_endpoint"],
        "registration_endpoint":  data["registration_endpoint"],
    }


# ── Dynamic Client Registration ───────────────────────────────────────────────

def register(registration_endpoint: str, redirect_uri: str) -> str:
    """Register a public OAuth client with the given redirect URI."""
    r = httpx.post(
        registration_endpoint,
        json={
            "client_name":                "Calendly MCP Test Harness",
            "redirect_uris":              [redirect_uri],
            "grant_types":                ["authorization_code"],
            "response_types":             ["code"],
            "token_endpoint_auth_method": "none",
        },
        timeout=10,
    )
    if r.status_code >= 400:
        raise RuntimeError(
            f"DCR {r.status_code} from {registration_endpoint} "
            f"(redirect_uri={redirect_uri}): {r.text}"
        )
    return r.json()["client_id"]


# ── Token exchange ────────────────────────────────────────────────────────────

def exchange(
    token_endpoint: str,
    client_id:      str,
    code:           str,
    verifier:       str,
    redirect_uri:   str,
) -> dict:
    """Exchange an authorization code for an access token."""
    r = httpx.post(
        token_endpoint,
        data={
            "grant_type":    "authorization_code",
            "client_id":     client_id,
            "code":          code,
            "redirect_uri":  redirect_uri,
            "code_verifier": verifier,
        },
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def refresh(token_endpoint: str, client_id: str, refresh_token: str) -> dict:
    """Exchange a refresh token for a fresh access token (and possibly a
    rotated refresh token)."""
    r = httpx.post(
        token_endpoint,
        data={
            "grant_type":    "refresh_token",
            "client_id":     client_id,
            "refresh_token": refresh_token,
        },
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


# ── URL helper ────────────────────────────────────────────────────────────────

def authorize_url(
    authorization_endpoint: str,
    client_id:              str,
    redirect_uri:           str,
    challenge:              str,
    state:                  str,
    scope: str = "mcp:scheduling:read mcp:scheduling:write",
) -> str:
    """Build the full browser-visit URL for the authorization step."""
    import urllib.parse
    params = {
        "response_type":         "code",
        "client_id":             client_id,
        "redirect_uri":          redirect_uri,
        "code_challenge":        challenge,
        "code_challenge_method": "S256",
        "state":                 state,
        "scope":                 scope,
    }
    return f"{authorization_endpoint}?{urllib.parse.urlencode(params)}"
