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

import asyncio
import base64
import json
import os
import secrets
import time
from urllib.parse import quote_plus

from dotenv import load_dotenv, set_key, unset_key
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sse_starlette.sse import EventSourceResponse

from app import calendly_oauth, db, provider_models, runner
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

# Display preference read from .env. Callable so toggling the setting takes
# effect on the next render without restarting uvicorn.
templates.env.globals["eval_groups_open"] = \
    lambda: os.environ.get("EVAL_GROUPS_DEFAULT_OPEN", "").lower() == "true"

# Cache-bust /static/style.css with the file's mtime so template soft
# refreshes pick up CSS edits without requiring a hard reload.
def _style_version() -> str:
    try:
        return str(int(os.path.getmtime(os.path.join(STATIC_DIR, "style.css"))))
    except OSError:
        return "0"
templates.env.globals["style_version"] = _style_version

# Exposed to templates so any page (topbar, settings, etc) can show which
# Calendly account the harness is currently authenticated as. Returns a dict
# with at least {"uuid": ...}, enriched with {"name","email",...} when
# CALENDLY_API_KEY is set and /users/me succeeds.
templates.env.globals["calendly_user"] = lambda: _current_calendly_user()


@app.on_event("startup")
def _startup() -> None:
    db.init_db()
    n = db.mark_abandoned_runs()
    if n:
        print(f"[startup] marked {n} orphaned run(s) as error")
    _refresh_available_models()


OPENAI_PRESET    = ["gpt-5.4", "gpt-5.3", "gpt-5.2", "gpt-5.1", "gpt-5",
                    "gpt-5-mini", "gpt-4.1", "gpt-4o", "gpt-4o-mini"]
ANTHROPIC_PRESET = ["claude-opus-4-7", "claude-sonnet-4-6",
                    "claude-haiku-4-5-20251001"]

# Populated on startup + after any key save. Empty list means "not probed"
# (key missing or fetch failed) — callers fall back to the preset.
_available_models: dict[str, list[str]] = {"openai": [], "anthropic": []}


def _refresh_available_models(provider: str | None = None) -> None:
    """Fetch /v1/models for the given provider (or both) using current env keys."""
    if provider in (None, "openai"):
        key = os.environ.get("OPENAI_API_KEY")
        _available_models["openai"] = provider_models.fetch_openai(key) if key else []
    if provider in (None, "anthropic"):
        key = os.environ.get("ANTHROPIC_API_KEY")
        _available_models["anthropic"] = provider_models.fetch_anthropic(key) if key else []


def _model_options() -> dict[str, list[str]]:
    """Preset models filtered to what the configured keys can actually access."""
    return {
        "openai": (
            [m for m in OPENAI_PRESET if m in _available_models["openai"]]
            if _available_models["openai"] else OPENAI_PRESET
        ),
        "anthropic": (
            [m for m in ANTHROPIC_PRESET if m in _available_models["anthropic"]]
            if _available_models["anthropic"] else ANTHROPIC_PRESET
        ),
    }


def _env_status() -> dict[str, bool]:
    return {
        "openai":    bool(os.environ.get("OPENAI_API_KEY")),
        "anthropic": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "calendly":  bool(os.environ.get("CALENDLY_MCP_TOKEN")),
    }


_PROVIDER_FOR_KEY = {
    "OPENAI_API_KEY":    "openai",
    "ANTHROPIC_API_KEY": "anthropic",
}


def _set_env(name: str, value: str) -> None:
    """Persist a credential to .env and update the running process env."""
    # python-dotenv requires the .env file to exist.
    if not os.path.exists(ENV_FILE):
        open(ENV_FILE, "a").close()
    set_key(ENV_FILE, name, value)
    os.environ[name] = value
    _reset_provider_clients()
    if name in _PROVIDER_FOR_KEY:
        _refresh_available_models(_PROVIDER_FOR_KEY[name])
    if name == "CALENDLY_API_KEY":
        _user_lookup_cache.clear()


def _clear_env(name: str) -> None:
    if os.path.exists(ENV_FILE):
        unset_key(ENV_FILE, name)
    os.environ.pop(name, None)
    _reset_provider_clients()
    if name in _PROVIDER_FOR_KEY:
        _refresh_available_models(_PROVIDER_FOR_KEY[name])
    if name == "CALENDLY_API_KEY":
        _user_lookup_cache.clear()


def _reset_provider_clients() -> None:
    """Drop cached LLM clients so they pick up refreshed env vars."""
    import test_prompt as tp
    tp._openai_client    = None
    tp._anthropic_client = None


# ── Calendly token lifecycle ──────────────────────────────────────────────────

def _store_calendly_tokens(
    tokens:         dict,
    client_id:      str | None = None,
    token_endpoint: str | None = None,
) -> None:
    """Persist an access-token response. `client_id` and `token_endpoint` are
    only passed on the initial OAuth exchange — they're also needed for later
    refresh calls, so we keep them in .env alongside the token."""
    _set_env("CALENDLY_MCP_TOKEN", tokens["access_token"])
    if "refresh_token" in tokens:
        _set_env("CALENDLY_MCP_REFRESH_TOKEN", tokens["refresh_token"])
    if "expires_in" in tokens:
        _set_env(
            "CALENDLY_MCP_TOKEN_EXPIRES_AT",
            str(int(time.time()) + int(tokens["expires_in"])),
        )
    if client_id:
        _set_env("CALENDLY_MCP_CLIENT_ID", client_id)
    if token_endpoint:
        _set_env("CALENDLY_MCP_TOKEN_ENDPOINT", token_endpoint)


def _ensure_calendly_auth() -> str | None:
    """Preflight the Calendly access token before starting a run. Returns
    `None` when the token is valid (refreshing it silently if it's near
    expiry), or a human-readable error string when the caller should redirect
    the user to /settings to reconnect."""
    token = os.environ.get("CALENDLY_MCP_TOKEN")
    if not token:
        return "Calendly not connected — connect on Settings"

    expires_at = os.environ.get("CALENDLY_MCP_TOKEN_EXPIRES_AT")
    if not expires_at:
        # Legacy token with no expiry info; let the run try it. If it 401s,
        # the user will hit the same "expired" flow after reconnecting once.
        return None
    try:
        expires_at_ts = int(expires_at)
    except ValueError:
        return None

    # 5-min buffer so a refresh mid-run doesn't race with token expiry.
    if time.time() < expires_at_ts - 300:
        return None

    refresh_tok    = os.environ.get("CALENDLY_MCP_REFRESH_TOKEN")
    client_id      = os.environ.get("CALENDLY_MCP_CLIENT_ID")
    token_endpoint = os.environ.get("CALENDLY_MCP_TOKEN_ENDPOINT")
    if not (refresh_tok and client_id and token_endpoint):
        return "Calendly session expired — reconnect on Settings"

    try:
        new_tokens = calendly_oauth.refresh(token_endpoint, client_id, refresh_tok)
    except Exception:
        return "Calendly session expired — reconnect on Settings"

    _store_calendly_tokens(new_tokens)
    return None


def _current_calendly_user_uuid() -> str | None:
    """Return the Calendly user_uuid embedded in the MCP access token's JWT
    payload, or None if no token is set / the token isn't a decodable JWT.
    Signature isn't verified — we already trust the token to drive requests."""
    token = os.environ.get("CALENDLY_MCP_TOKEN")
    if not token:
        return None
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        pad = "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + pad))
    except Exception:
        return None
    uuid = payload.get("user_uuid")
    return uuid if isinstance(uuid, str) else None


# Cache for /users/{uuid} lookups. Keyed by (api_key, uuid) so rotating
# either the PAT or reconnecting to a different Calendly account triggers
# a fresh fetch.
_user_lookup_cache: dict[tuple[str, str], dict] = {}


def _fetch_calendly_user_by_uuid(api_key: str, uuid: str) -> dict | None:
    """Resolve an MCP JWT user_uuid to name/email via Calendly's REST API.
    The PAT must have scope to view the user (typically same org or self).
    Returns None on any failure — callers fall back to showing just the uuid."""
    cache_key = (api_key, uuid)
    if cache_key in _user_lookup_cache:
        return _user_lookup_cache[cache_key]
    import httpx
    try:
        r = httpx.get(
            f"https://api.calendly.com/users/{uuid}",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=5,
        )
        if r.status_code >= 400:
            return None
        resource = r.json().get("resource") or {}
    except Exception:
        return None
    info = {
        "uri":             resource.get("uri"),
        "name":            resource.get("name"),
        "email":           resource.get("email"),
        "slug":            resource.get("slug"),
        "scheduling_url":  resource.get("scheduling_url"),
    }
    _user_lookup_cache[cache_key] = info
    return info


def _current_calendly_user() -> dict | None:
    """Return display info for the currently authenticated Calendly user.
    Always includes {"uuid": ...} when an MCP token is set; additionally
    includes {"name","email","slug","scheduling_url"} when CALENDLY_API_KEY
    is set and the UUID can be resolved via /users/{uuid}."""
    uuid = _current_calendly_user_uuid()
    if not uuid:
        return None
    info: dict = {"uuid": uuid}
    api_key = os.environ.get("CALENDLY_API_KEY")
    if api_key:
        resolved = _fetch_calendly_user_by_uuid(api_key, uuid)
        if resolved:
            info.update(resolved)
    return info


def _clear_calendly_session() -> None:
    """Clear every env var tied to a Calendly token. Called from the
    Disconnect button and any other teardown."""
    for name in (
        "CALENDLY_MCP_TOKEN",
        "CALENDLY_MCP_REFRESH_TOKEN",
        "CALENDLY_MCP_TOKEN_EXPIRES_AT",
        "CALENDLY_MCP_CLIENT_ID",
        "CALENDLY_MCP_TOKEN_ENDPOINT",
    ):
        _clear_env(name)


# ── Test list ─────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "tests":          db.list_tests(),
            "model":          MODEL,
            "mcp_url":        MCP_SERVER_URL,
            "env_status":     _env_status(),
            "model_options":  _model_options(),
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


def _parse_max_seconds(raw: str) -> float | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        v = float(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid max_seconds: '{raw}'")
    if v < 0:
        raise HTTPException(status_code=400, detail="max_seconds must be >= 0")
    return v


@app.post("/tests")
def test_create(
    id:            str  = Form(...),
    prompt:        str  = Form(...),
    expect:        str  = Form(...),
    must_call:     str  = Form(""),
    must_not_call: str  = Form(""),
    at_most_once:  str  = Form(""),
    max_seconds:   str  = Form(""),
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
        "at_most_once":  _parse_tool_list(at_most_once),
        "max_seconds":   _parse_max_seconds(max_seconds),
        "mutates":       mutates,
    })
    return RedirectResponse("/", status_code=303)


@app.get("/tests/{test_id}/edit", response_class=HTMLResponse)
def test_edit_form(request: Request, test_id: str, tab: str = "edit") -> HTMLResponse:
    test = db.get_test(test_id)
    if not test:
        raise HTTPException(status_code=404, detail=f"Test '{test_id}' not found")
    ctx: dict = {"test": test, "mode": "edit", "tab": tab}
    if tab == "runs":
        ctx["runs"] = db.list_runs_for_test(test_id)
    return templates.TemplateResponse(request, "test_form.html", ctx)


@app.post("/tests/{test_id}")
def test_update(
    test_id:       str,
    prompt:        str  = Form(...),
    expect:        str  = Form(...),
    must_call:     str  = Form(""),
    must_not_call: str  = Form(""),
    at_most_once:  str  = Form(""),
    max_seconds:   str  = Form(""),
    mutates:       bool = Form(False),
) -> RedirectResponse:
    if db.get_test(test_id) is None:
        raise HTTPException(status_code=404, detail=f"Test '{test_id}' not found")
    db.update_test(test_id, {
        "prompt":        prompt.strip(),
        "expect":        expect.strip(),
        "must_call":     _parse_tool_list(must_call),
        "must_not_call": _parse_tool_list(must_not_call),
        "at_most_once":  _parse_tool_list(at_most_once),
        "max_seconds":   _parse_max_seconds(max_seconds),
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

    auth_err = await asyncio.to_thread(_ensure_calendly_auth)
    if auth_err:
        return RedirectResponse(
            f"/settings?error={quote_plus(auth_err)}", status_code=303,
        )

    try:
        run_id = await runner.start_run(test_ids, runs_per_test, model=model)
    except runner.RunInProgressError as e:
        active_ids = e.args[0] if e.args else []
        pretty = ", ".join(f"#{i}" for i in active_ids) or "unknown"
        raise HTTPException(
            status_code=429,
            detail=(
                f"At capacity — {runner.MAX_CONCURRENT_RUNS} runs already "
                f"active ({pretty}). Wait for one to finish."
            ),
        )
    return RedirectResponse(f"/runs/{run_id}", status_code=303)


@app.get("/runs", response_class=HTMLResponse)
def runs_list(request: Request, q: str = "") -> HTMLResponse:
    query = q.strip()
    return templates.TemplateResponse(
        request, "runs_list.html",
        {
            "runs":            db.list_runs(query=query),
            "current_run_ids": runner.current_run_ids(),
            "query":           query,
        },
    )


def _group_by_test(results: list[dict]) -> list[tuple[str, list[dict]]]:
    """Group results by test_id preserving first-appearance order."""
    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for r in results:
        tid = r["test_id"]
        if tid not in groups:
            groups[tid] = []
            order.append(tid)
        groups[tid].append(r)
    return [(tid, groups[tid]) for tid in order]


def _matches(result: dict, needle_lower: str) -> bool:
    """Case-insensitive substring match across the same fields the runs
    list search covers."""
    fields = (
        result.get("response_text", ""),
        result.get("tool_reason",   ""),
        result.get("judge_reason",  ""),
        " ".join(result.get("tools_called") or []),
    )
    return any(needle_lower in (f or "").lower() for f in fields)


@app.get("/runs/{run_id}", response_class=HTMLResponse)
def run_detail(request: Request, run_id: int, q: str = "") -> HTMLResponse:
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    results = db.list_run_results(run_id)

    # Search is only offered for completed runs (see template), but if a
    # live page somehow posts a query, treat it as no filter.
    query = q.strip() if run["status"] != "running" else ""
    if query:
        needle = query.lower()
        results = [r for r in results if _matches(r, needle)]

    return templates.TemplateResponse(
        request, "run_detail.html",
        {
            "run":           run,
            "results":       results,
            "groups":        _group_by_test(results),
            "is_live":       run["status"] == "running",
            "planned_total": runner.planned_total(run_id) or len(results),
            "query":         query,
        },
    )


@app.post("/runs/{run_id}/name")
async def run_rename(run_id: int, request: Request) -> Response:
    form = await request.form()
    name = (form.get("name") or "").strip() or None
    try:
        db.set_run_name(run_id, name)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return Response(status_code=204)


@app.get("/runs/{run_id}/stream")
async def run_stream(request: Request, run_id: int) -> EventSourceResponse:
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    # Honor the browser's Last-Event-ID on reconnect so we don't replay
    # iterations the client already rendered (which would beforeend-append
    # duplicate groups/articles). run_results.id is our monotonic event id.
    try:
        last_id = int(request.headers.get("Last-Event-ID", "0"))
    except ValueError:
        last_id = 0

    async def event_generator():
        known_tids:   set[str] = set()   # client already has the group shell
        emitted_tids: set[str] = set()   # we've emitted test_start this connection

        async for event in runner.stream(run_id):
            if await request.is_disconnected():
                break
            etype = event.get("type", "message")

            if etype == "result":
                r = event["result"]
                tid = r["test_id"]
                rid = r.get("id") or 0

                # Client already has this iteration from before the drop —
                # remember the tid so we don't re-synth the group, but emit
                # nothing to the wire.
                if rid and rid <= last_id:
                    known_tids.add(tid)
                    continue

                # Synthesize a group shell only if the client doesn't already
                # have one. Replayed results reach here without their original
                # test_start event, so we fabricate one.
                if tid not in known_tids and tid not in emitted_tids:
                    emitted_tids.add(tid)
                    shell_html = templates.get_template("_test_start.html").render({
                        "t": {
                            "test_id": tid,
                            "runs":    r.get("total", 1),
                            "mutates": r.get("test_mutates", False),
                        }
                    })
                    # No `id` on test_start: the SSE spec preserves the prior
                    # Last-Event-ID when a field is omitted, so the integer id
                    # from the last `result` event stays live for reconnects.
                    yield {"event": "test_start", "data": shell_html}

                article_html = templates.get_template("_run_result.html").render({"r": r})
                # Wrap in a div with hx-swap-oob. htmx unwraps the tagged
                # element and inserts its children into the target — so the
                # <article> lands inside .test-group-body with .result intact.
                article_oob = (
                    f'<div hx-swap-oob="beforeend:#test-group-body-{tid}">'
                    f"{article_html}</div>"
                )
                yield {"event": "result", "data": article_oob, "id": str(rid)}

            elif etype == "test_start":
                tid = event["test_id"]
                # Skip if the client already has this group from a previous
                # connection, or we've already emitted it this connection.
                if tid in known_tids or tid in emitted_tids:
                    continue
                emitted_tids.add(tid)
                html = templates.get_template("_test_start.html").render({"t": event})
                yield {"event": "test_start", "data": html}

            elif etype == "summary":
                results = db.list_run_results(run_id)
                planned = runner.planned_total(run_id) or len(results)
                html = templates.get_template("_run_summary.html").render(
                    {"results": results, "planned_total": planned}
                )
                yield {"event": "summary", "data": html}
            elif etype == "complete":
                status = event.get("status", "")
                # OOB-swap the header status pill so it flips from "running"
                # to its final state in place. Remaining payload is empty, so
                # the #run-footer (sse-swap="complete", hx-swap="innerHTML")
                # just clears its "Running..." message.
                pill_oob = (
                    f'<span id="run-status" hx-swap-oob="true" '
                    f'class="pill pill-{status}">{status}</span>'
                )
                yield {"event": "complete", "data": pill_oob}
            elif etype == "error":
                yield {"event": "error", "data": event.get("message", "")}

    return EventSourceResponse(event_generator())


# ── Settings ──────────────────────────────────────────────────────────────────

@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, error: str = "", ok: str = "") -> HTMLResponse:
    return templates.TemplateResponse(
        request, "settings.html",
        {
            "env_status":        _env_status(),
            "available_models":  _available_models,
            "error":             error,
            "ok":                ok,
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
    if name == "CALENDLY_MCP_TOKEN":
        _clear_calendly_session()
    else:
        _clear_env(name)
    return RedirectResponse("/settings?ok=Credential+cleared", status_code=303)


@app.post("/settings/eval-groups-default-open")
def settings_eval_groups_default_open(
    default_state: str = Form(...),
) -> RedirectResponse:
    if default_state not in ("expanded", "collapsed"):
        raise HTTPException(status_code=400, detail="Invalid value")
    _set_env("EVAL_GROUPS_DEFAULT_OPEN",
             "true" if default_state == "expanded" else "false")
    return RedirectResponse("/settings?ok=Preference+saved", status_code=303)


# ── Calendly OAuth (web-based flow, callback on this same server) ─────────────

# state → {"verifier": ..., "client_id": ..., "endpoints": {...}}
_pending_oauth: dict[str, dict] = {}


def _oauth_redirect_uri(request: Request) -> str:
    # Use the request URL base so the port matches wherever uvicorn is bound.
    # Force `localhost` over `127.0.0.1` — Calendly's DCR endpoint rejects
    # raw IP redirect URIs (verified against CLI flow, which used localhost).
    base = str(request.base_url).rstrip("/").replace("127.0.0.1", "localhost")
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

    _store_calendly_tokens(
        tokens,
        client_id=pending["client_id"],
        token_endpoint=pending["endpoints"]["token_endpoint"],
    )
    return RedirectResponse("/settings?ok=Calendly+connected", status_code=303)
