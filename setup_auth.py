#!/usr/bin/env python3
"""
One-time OAuth setup for mcp.calendly.com.

Registers a dynamic client, opens a browser for you to log in,
captures the callback, and saves the token to .env.

Usage: .venv/bin/python setup_auth.py
"""

import base64
import hashlib
import http.server
import os
import secrets
import urllib.parse
import webbrowser

import httpx
from dotenv import set_key

MCP_SERVER_URL    = "https://mcp.calendly.com"
REDIRECT_URI      = "http://localhost:8080/callback"
ENV_FILE          = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


# ── PKCE helpers ──────────────────────────────────────────────────────────────

def pkce_pair() -> tuple[str, str]:
    verifier  = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


# ── Discovery ─────────────────────────────────────────────────────────────────

def discover() -> dict:
    print("  Fetching protected resource metadata...")
    resource = httpx.get(f"{MCP_SERVER_URL}/.well-known/oauth-protected-resource", timeout=10)
    resource.raise_for_status()
    meta = resource.json()

    auth_server = (meta.get("authorization_servers") or ["https://calendly.com"])[0].rstrip("/")
    print(f"  Fetching auth server metadata from {auth_server}...")
    server = httpx.get(f"{auth_server}/.well-known/oauth-authorization-server", timeout=10)
    server.raise_for_status()
    return server.json()


# ── Dynamic Client Registration ───────────────────────────────────────────────

def register(registration_endpoint: str) -> str:
    print("  Registering OAuth client...")
    r = httpx.post(registration_endpoint, json={
        "client_name":                 "Calendly MCP Test Harness",
        "redirect_uris":               [REDIRECT_URI],
        "grant_types":                 ["authorization_code"],
        "response_types":              ["code"],
        "token_endpoint_auth_method":  "none",
    }, timeout=10)
    r.raise_for_status()
    client_id = r.json()["client_id"]
    print(f"  Client registered: {client_id[:12]}...")
    return client_id


# ── Local callback server ─────────────────────────────────────────────────────

def wait_for_code() -> str | None:
    auth_code = None

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            nonlocal auth_code
            params    = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            auth_code = params.get("code", [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<html><body style='font-family:sans-serif;padding:2rem'>"
                b"<h2>Authorized &#10003;</h2>"
                b"<p>You can close this tab and return to the terminal.</p>"
                b"</body></html>"
            )

        def log_message(self, *_):
            pass

    srv = http.server.HTTPServer(("localhost", 8080), Handler)
    srv.handle_request()
    return auth_code


# ── Token exchange ────────────────────────────────────────────────────────────

def exchange(token_endpoint: str, client_id: str, code: str, verifier: str) -> dict:
    print("  Exchanging code for token...")
    r = httpx.post(token_endpoint, data={
        "grant_type":    "authorization_code",
        "client_id":     client_id,
        "code":          code,
        "redirect_uri":  REDIRECT_URI,
        "code_verifier": verifier,
    }, timeout=10)
    r.raise_for_status()
    return r.json()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("\nCalendly MCP — OAuth Setup\n")

    endpoints  = discover()
    client_id  = register(endpoints["registration_endpoint"])
    verifier, challenge = pkce_pair()
    state      = secrets.token_urlsafe(16)

    auth_url = (
        f"{endpoints['authorization_endpoint']}"
        f"?response_type=code"
        f"&client_id={urllib.parse.quote(client_id)}"
        f"&redirect_uri={urllib.parse.quote(REDIRECT_URI)}"
        f"&code_challenge={challenge}"
        f"&code_challenge_method=S256"
        f"&state={state}"
        f"&scope=mcp:scheduling:read%20mcp:scheduling:write"
    )

    print("\n  Opening browser — log in with your TEST Calendly account.")
    print("  If the browser doesn't open, paste this URL manually:\n")
    print(f"  {auth_url}\n")
    webbrowser.open(auth_url)

    print("  Waiting for callback on http://localhost:8080 ...")
    code = wait_for_code()

    if not code:
        print("\n  Error: no auth code received.")
        return

    tokens = exchange(endpoints["token_endpoint"], client_id, code, verifier)

    set_key(ENV_FILE, "CALENDLY_MCP_TOKEN", tokens["access_token"])
    if "refresh_token" in tokens:
        set_key(ENV_FILE, "CALENDLY_MCP_REFRESH_TOKEN", tokens["refresh_token"])

    print("\n  Token saved to .env")
    print("  Run tests with: .venv/bin/python run_tests.py\n")


if __name__ == "__main__":
    main()
