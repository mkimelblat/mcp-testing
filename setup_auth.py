#!/usr/bin/env python3
"""
One-time OAuth setup for mcp.calendly.com (CLI flow).

Registers a dynamic client, opens a browser for you to log in,
captures the callback on localhost:8080, and saves the token to .env.

Usage: .venv/bin/python setup_auth.py

For a fully-in-browser alternative, use the Settings page in the web UI.
"""

import http.server
import os
import secrets
import urllib.parse
import webbrowser

from dotenv import set_key

from app.calendly_oauth import (
    authorize_url, discover, exchange, pkce_pair, register,
)

REDIRECT_URI = "http://localhost:8080/callback"
ENV_FILE     = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


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


def main():
    print("\nCalendly MCP — OAuth Setup\n")

    print("  Fetching OAuth metadata...")
    endpoints = discover()

    print("  Registering OAuth client...")
    client_id = register(endpoints["registration_endpoint"], REDIRECT_URI)
    print(f"  Client registered: {client_id[:12]}...")

    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(16)

    url = authorize_url(
        endpoints["authorization_endpoint"],
        client_id, REDIRECT_URI, challenge, state,
    )

    print("\n  Opening browser — log in with your TEST Calendly account.")
    print("  If the browser doesn't open, paste this URL manually:\n")
    print(f"  {url}\n")
    webbrowser.open(url)

    print("  Waiting for callback on http://localhost:8080 ...")
    code = wait_for_code()

    if not code:
        print("\n  Error: no auth code received.")
        return

    print("  Exchanging code for token...")
    tokens = exchange(
        endpoints["token_endpoint"], client_id, code, verifier, REDIRECT_URI,
    )

    set_key(ENV_FILE, "CALENDLY_MCP_TOKEN", tokens["access_token"])
    if "refresh_token" in tokens:
        set_key(ENV_FILE, "CALENDLY_MCP_REFRESH_TOKEN", tokens["refresh_token"])

    print("\n  Token saved to .env")
    print("  Run tests with: .venv/bin/python run_tests.py\n")


if __name__ == "__main__":
    main()
