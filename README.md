# mcp-testing

Local eval harness for the Calendly MCP server. Runs natural-language prompts
against `mcp.calendly.com` via either the OpenAI Responses API or the
Anthropic Messages API — both have native remote-MCP support — scores each
run on four dimensions (tool trace, call-count limits, LLM-judge text
quality, runtime budget), and measures consistency across repeated iterations.

Two interfaces share a single SQLite-backed store:

- **Web UI** — view, add, edit evals; trigger runs; watch results stream in live; browse run history; manage credentials
- **CLI** — scripted runs for batch / CI use

## Requirements

- Python 3.12+
- A Calendly account to authorize against
- At least one LLM provider key:
  - OpenAI (for `gpt-5.x`, `gpt-4.1`, `gpt-4o`) — [platform.openai.com/api-keys](https://platform.openai.com/api-keys)
  - Anthropic (for `claude-*`) — [console.anthropic.com](https://console.anthropic.com/settings/keys)

## Setup

```bash
git clone https://github.com/mkimelblat/mcp-testing.git
cd mcp-testing

python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt

.venv/bin/python server.py      # → http://localhost:8000
```

Then open [http://localhost:8000](http://localhost:8000) in the browser and click **Settings**:

1. **Connect Calendly** — runs the OAuth flow end-to-end in the browser, stores the token in `.env`
2. Paste your **OpenAI** and/or **Anthropic** API key — also saved to `.env`

You're ready to run evals. No manual `.env` editing required.

> **Note on Calendly token**: `mcp.calendly.com` is a separate service from
> `api.calendly.com`, and it requires OAuth-issued tokens with `mcp:scheduling:*`
> scopes — Calendly PATs from the developer portal don't work here. That's
> why Settings has an explicit OAuth button rather than a paste field.

## Using the web UI

Visit [http://localhost:8000](http://localhost:8000).

- **Evals** — shows the 8 default workflow evals. Click an eval id to edit the
  prompt, expectation, or tool assertions. Click **+ New eval** to add one.
- Pick a **Model** from the dropdown (OpenAI and Anthropic models are
  grouped). The dropdown is auto-filtered to models your saved API keys can
  actually access — a call to `/v1/models` on each provider runs on startup
  and after any key save. Check one or more evals, set "runs per eval",
  click **Run selected**. You're redirected to the run detail page where
  results stream in live via SSE. Mutating evals (e.g. cancel, book) are
  auto-capped at 1 iteration.
- **Runs** in the nav shows history — each row lists which evals were
  included, with a per-eval pass/total ratio (green = all passed, orange =
  partial, red = all failed) alongside the aggregate Results pill. Click
  any run to see its full detail, including which model it was run with.
- **Settings** manages the three credentials at any time, and shows a
  collapsible "N models accessible" list under each saved key so you can
  verify what your OpenAI / Anthropic project has access to.

## Using the CLI

```bash
.venv/bin/python run_tests.py --list                          # list evals
.venv/bin/python run_tests.py                                 # run all, 5 iterations each
.venv/bin/python run_tests.py --runs 3                        # 3 iterations each
.venv/bin/python run_tests.py --readonly                      # skip mutating evals
.venv/bin/python run_tests.py find_available_slots --runs 5   # one eval, 5 iterations
.venv/bin/python run_tests.py --model claude-sonnet-4-6       # A/B against Claude
```

If you prefer to run the Calendly OAuth flow from the terminal rather than
the web UI, `setup_auth.py` does the same thing:

```bash
.venv/bin/python setup_auth.py
```

CLI and web UI share the same SQLite file (`mcp_testing.db`), so edits in
either place are visible to the other.

## How scoring works

Each iteration is scored on four independent checks:

1. **Tool trace** — did the model call the tools you listed in `must_call`?
   Did it avoid the ones in `must_not_call`?
2. **At-most-once** — no tool in `at_most_once` was called more than once
   (useful when you want "list once, then act" behavior and see a model
   redundantly re-list).
3. **Judge** — a separate LLM reads the user prompt, your expectation, and the
   model's response, and returns pass/fail with a reason.
4. **Runtime** — elapsed time ≤ `max_seconds` (only enforced if set).

A run passes only if **all** pass. The UI shows each dimension independently
so you can tell whether a failure is a grounding problem (wrong tools) or an
output-shape problem (right tools, bad answer). At-most-once and runtime
pills only render when the eval has an assertion for that dimension.

**Snapshot semantics.** Each `run_results` row stores a snapshot of the full
rubric (prompt, expectation, tool assertions, runtime budget) at the moment
the run executed. Editing an eval later does **not** rewrite what historical
runs appear to have been scored against — the run detail page shows the
rubric as it was, not as it is now.

## Parallel runs

Up to 3 runs can execute concurrently (`MAX_CONCURRENT_RUNS` in
`app/runner.py`). Kick off multiple runs — e.g. to A/B two models side by
side — and each streams results independently to its own detail page.
Starting a 4th while 3 are active returns HTTP 429 with the active run
ids; wait for one to finish.

## Project layout

```
app/
  main.py              FastAPI routes (evals, runs, settings, OAuth)
  runner.py            async run orchestrator + SSE fanout
  db.py                SQLite schema, eval + run CRUD, default seed data
  calendly_oauth.py    shared OAuth 2.1 DCR + PKCE helpers
  templates/           Jinja2 templates (HTMX for live updates)
  static/style.css
test_prompt.py         provider-dispatch core — OpenAI Responses or Anthropic Messages
run_tests.py           CLI entry point
setup_auth.py          terminal-based Calendly OAuth flow (same logic as web UI)
server.py              uvicorn entry point for the web UI
```
