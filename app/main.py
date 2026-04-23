"""
FastAPI app for the Calendly MCP test harness UI.

Routes:
  GET    /                         — test list
  GET    /tests/new                — form: create test
  POST   /tests                    — create test
  GET    /tests/{id}/edit          — form: edit test
  POST   /tests/{id}               — update test
  POST   /tests/{id}/delete        — delete test
  POST   /runs                     — start a new run
  GET    /runs                     — run history
  GET    /runs/{id}                — run detail (live or static)
  GET    /runs/{id}/stream         — SSE event stream for a running run
  GET    /settings                 — manage API keys + Calendly OAuth
  POST   /settings/api-key         — save an OpenAI or Anthropic key
  POST   /settings/clear/{name}    — clear a stored credential
  GET    /auth/calendly/start      — begin Calendly OAuth flow
  GET    /auth/calendly/callback   — OAuth callback, stores token in .env
"""

from __future__ import annotations

import json
import os
import secrets

from dotenv import load_dotenv, set_key, unset_key
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sse_starlette.sse import EventSourceResponse

from app import calendly_oauth, db, runner
from test_prompt import MCP_SERVER_URL, MODEL

load_dotenv()

APP_DIR      = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR     = os.path.dirname(APP_DIR)
ENV_FILE     = os.path.join(ROOT_DIR, ".env")
TEMPLATE_DIR = os.path.join(APP_DIR, "templates")
STATIC_DIR   = os.path.join(APP_DIR, "static")

app       = FastAPI(title="Calendly MCP Test Harness")
templates = Jinja2Templates(directory=TEMPLATE_DIR)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.on_event("startup")
def _startup() -> None:
    db.init_db()


def _env_status() -> dict[str, bool]:
    return {
        "openai":    bool(os.environ.get("OPENAI_API_KEY")),
        "anthropic": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "calendly":  bool(os.environ.get("CALENDLY_MCP_TOKEN")),
    }


def _set_env(name: str, value: str) -> None:
    """Persist a credential to .env and update the running process env."""
    # python-dotenv requires the .env file to exist.
    if not os.path.exists(ENV_FILE):
        open(ENV_FILE, "a").close()
    set_key(ENV_FILE, name, value)
    os.environ[name] = value
    _reset_provider_clients()


def _clear_env(name: str) -> None:
    if os.path.exists(ENV_FILE):
        unset_key(ENV_FILE, name)
    os.environ.pop(name, None)
    _reset_provider_clients()


def _reset_provider_clients() -> None:
    """Drop cached LLM clients so they pick up refreshed env vars."""
    import test_prompt as tp
    tp._openai_client    = None
    tp._anthropic_client = None


# ── Test list ─────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "tests":       db.list_tests(),
            "model":       MODEL,
            "mcp_url":     MCP_SERVER_URL,
            "env_status":  _env_status(),
        },
    )


# ── Test CRUD ─────────────────────────────────────────────────────────────────

@app.get("/tests/new", response_class=HTMLResponse)
def test_new(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "test_form.html",
        {"test": None, "mode": "new"},
    )


def _parse_tool_list(raw: str) -> list[str]:
    """Accept a newline- or comma-separated list of tool names."""
    items = [p.strip() for chunk in raw.splitlines() for p in chunk.split(",")]
    return [i for i in items if i]


@app.post("/tests")
def test_create(
    id:            str  = Form(...),
    prompt:        str  = Form(...),
    expect:        str  = Form(...),
    must_call:     str  = Form(""),
    must_not_call: str  = Form(""),
    mutates:       bool = Form(False),
) -> RedirectResponse:
    if db.get_test(id) is not None:
        raise HTTPException(status_code=409, detail=f"Test id '{id}' already exists")
    db.create_test({
        "id":            id.strip(),
        "prompt":        prompt.strip(),
        "expect":        expect.strip(),
        "must_call":     _parse_tool_list(must_call),
        "must_not_call": _parse_tool_list(must_not_call),
        "mutates":       mutates,
    })
    return RedirectResponse("/", status_code=303)


@app.get("/tests/{test_id}/edit", response_class=HTMLResponse)
def test_edit_form(request: Request, test_id: str) -> HTMLResponse:
    test = db.get_test(test_id)
    if not test:
        raise HTTPException(status_code=404, detail=f"Test '{test_id}' not found")
    return templates.TemplateResponse(
        request, "test_form.html",
        {"test": test, "mode": "edit"},
    )


@app.post("/tests/{test_id}")
def test_update(
    test_id:       str,
    prompt:        str  = Form(...),
    expect:        str  = Form(...),
    must_call:     str  = Form(""),
    must_not_call: str  = Form(""),
    mutates:       bool = Form(False),
) -> RedirectResponse:
    if db.get_test(test_id) is None:
        raise HTTPException(status_code=404, detail=f"Test '{test_id}' not found")
    db.update_test(test_id, {
        "prompt":        prompt.strip(),
        "expect":        expect.strip(),
        "must_call":     _parse_tool_list(must_call),
        "must_not_call": _parse_tool_list(must_not_call),
        "mutates":       mutates,
    })
    return RedirectResponse("/", status_code=303)


@app.post("/tests/{test_id}/delete")
def test_delete(test_id: str) -> RedirectResponse:
    db.delete_test(test_id)
    return RedirectResponse("/", status_code=303)


# ── Runs ──────────────────────────────────────────────────────────────────────

@app.post("/runs")
async def run_create(request: Request) -> RedirectResponse:
    form = await request.form()
    test_ids = form.getlist("test_ids")
    if not test_ids:
        raise HTTPException(status_code=400, detail="Select at least one test")
    try:
        runs_per_test = int(form.get("runs_per_test") or 1)
    except ValueError:
        runs_per_test = 1
    runs_per_test = max(1, min(runs_per_test, 50))
    model = (form.get("model") or "").strip() or None

    try:
        run_id = await runner.start_run(test_ids, runs_per_test, model=model)
    except runner.RunInProgressError as e:
        active = int(str(e)) if str(e).isdigit() else 0
        raise HTTPException(
            status_code=409,
            detail=f"A run is already in progress — see /runs/{active}",
        )
    return RedirectResponse(f"/runs/{run_id}", status_code=303)


@app.get("/runs", response_class=HTMLResponse)
def runs_list(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "runs_list.html",
        {"runs": db.list_runs(), "current_run_id": runner.current_run_id()},
    )


@app.get("/runs/{run_id}", response_class=HTMLResponse)
def run_detail(request: Request, run_id: int) -> HTMLResponse:
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return templates.TemplateResponse(
        request, "run_detail.html",
        {
            "run":     run,
            "results": db.list_run_results(run_id),
            "is_live": run["status"] == "running",
        },
    )


@app.get("/runs/{run_id}/stream")
async def run_stream(request: Request, run_id: int) -> EventSourceResponse:
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    async def event_generator():
        async for event in runner.stream(run_id):
            if await request.is_disconnected():
                break
            etype = event.get("type", "message")
            if etype == "result":
                html = templates.get_template("_run_result.html").render(
                    {"r": event["result"]}
                )
                yield {"event": "result", "data": html}
            elif etype == "test_start":
                html = templates.get_template("_test_start.html").render(
                    {"t": event}
                )
                yield {"event": "test_start", "data": html}
            elif etype == "complete":
                yield {"event": "complete", "data": event.get("status", "")}
            elif etype == "error":
                yield {"event": "error", "data": event.get("message", "")}

    return EventSourceResponse(event_generator())


# ── Settings ──────────────────────────────────────────────────────────────────

@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, error: str = "", ok: str = "") -> HTMLResponse:
    return templates.TemplateResponse(
        request, "settings.html",
        {
            "env_status": _env_status(),
            "error":      error,
            "ok":         ok,
        },
    )


_VALID_KEY_NAMES = {
    "OPENAI_API_KEY":    "OpenAI API key",
    "ANTHROPIC_API_KEY": "Anthropic API key",
}


@app.post("/settings/api-key")
def settings_save_key(
    name:  str = Form(...),
    value: str = Form(...),
) -> RedirectResponse:
    if name not in _VALID_KEY_NAMES:
        raise HTTPException(status_code=400, detail="Unknown credential name")
    value = value.strip()
    if not value:
        return RedirectResponse("/settings?error=Empty+value+ignored", status_code=303)
    _set_env(name, value)
    return RedirectResponse(f"/settings?ok={_VALID_KEY_NAMES[name]}+saved", status_code=303)


@app.post("/settings/clear/{name}")
def settings_clear(name: str) -> RedirectResponse:
    clearable = {"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "CALENDLY_MCP_TOKEN", "CALENDLY_MCP_REFRESH_TOKEN"}
    if name not in clearable:
        raise HTTPException(status_code=400, detail="Not a clearable credential")
    _clear_env(name)
    # Also clear refresh token alongside the main Calendly token.
    if name == "CALENDLY_MCP_TOKEN":
        _clear_env("CALENDLY_MCP_REFRESH_TOKEN")
    return RedirectResponse("/settings?ok=Credential+cleared", status_code=303)


# ── Calendly OAuth (web-based flow, callback on this same server) ─────────────

# state → {"verifier": ..., "client_id": ..., "endpoints": {...}}
_pending_oauth: dict[str, dict] = {}


def _oauth_redirect_uri(request: Request) -> str:
    # Use the request URL base so the redirect URI matches the port the user is
    # actually on (useful if someone runs uvicorn on a non-default port).
    base = str(request.base_url).rstrip("/")
    return f"{base}/auth/calendly/callback"


@app.get("/auth/calendly/start")
def calendly_oauth_start(request: Request) -> RedirectResponse:
    try:
        endpoints = calendly_oauth.discover()
    except Exception as e:
        return RedirectResponse(
            f"/settings?error=Calendly+discovery+failed:+{e}", status_code=303,
        )

    redirect_uri = _oauth_redirect_uri(request)
    try:
        client_id = calendly_oauth.register(
            endpoints["registration_endpoint"], redirect_uri,
        )
    except Exception as e:
        return RedirectResponse(
            f"/settings?error=Calendly+client+registration+failed:+{e}",
            status_code=303,
        )

    verifier, challenge = calendly_oauth.pkce_pair()
    state = secrets.token_urlsafe(16)
    _pending_oauth[state] = {
        "verifier":     verifier,
        "client_id":    client_id,
        "endpoints":    endpoints,
        "redirect_uri": redirect_uri,
    }

    url = calendly_oauth.authorize_url(
        endpoints["authorization_endpoint"],
        client_id, redirect_uri, challenge, state,
    )
    return RedirectResponse(url, status_code=303)


@app.get("/auth/calendly/callback")
def calendly_oauth_callback(
    code:  str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    if error:
        _pending_oauth.pop(state or "", None)
        return RedirectResponse(f"/settings?error=Calendly+OAuth:+{error}", status_code=303)
    if not code or not state or state not in _pending_oauth:
        return RedirectResponse("/settings?error=Invalid+OAuth+state", status_code=303)

    pending = _pending_oauth.pop(state)
    try:
        tokens = calendly_oauth.exchange(
            pending["endpoints"]["token_endpoint"],
            pending["client_id"],
            code,
            pending["verifier"],
            pending["redirect_uri"],
        )
    except Exception as e:
        return RedirectResponse(
            f"/settings?error=Calendly+token+exchange+failed:+{e}", status_code=303,
        )

    _set_env("CALENDLY_MCP_TOKEN", tokens["access_token"])
    if "refresh_token" in tokens:
        _set_env("CALENDLY_MCP_REFRESH_TOKEN", tokens["refresh_token"])

    return RedirectResponse("/settings?ok=Calendly+connected", status_code=303)
