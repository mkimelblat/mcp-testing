# mcp-testing

Local test harness for the Calendly MCP server. Runs natural-language prompts
against `mcp.calendly.com` via the OpenAI Responses API, scores each run on
two dimensions (tool-trace correctness + LLM-judge text quality), and measures
consistency across repeated iterations.

Two interfaces share a single SQLite-backed store:

- **Web UI** — view, add, edit tests; trigger runs; watch results stream in live; browse run history
- **CLI** — scripted runs for batch / CI use

## Requirements

- Python 3.12+
- An OpenAI API key with access to GPT-5.x models (`gpt-5.1` is the default)
- A Calendly account to authorize against

## Setup

```bash
git clone https://github.com/mkimelblat/mcp-testing.git
cd mcp-testing

python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp .env.example .env
# → open .env, paste your OPENAI_API_KEY

.venv/bin/python setup_auth.py
# → opens browser, you log into Calendly, token is saved to .env
```

## Using the web UI

```bash
.venv/bin/python server.py
```

Visit [http://localhost:8000](http://localhost:8000).

- **Tests** — shows the 8 default workflow tests. Click a test id to edit the
  prompt, expectation, or tool assertions. Click **+ New test** to add one.
- Check one or more tests, set "runs per test", click **Run selected**. You're
  redirected to the run detail page where results stream in live via SSE.
  Mutating tests (e.g. cancel, book) are auto-capped at 1 iteration.
- **Runs** in the nav shows history — click any run to see its full detail.

## Using the CLI

```bash
.venv/bin/python run_tests.py --list                          # list tests
.venv/bin/python run_tests.py                                 # run all, 5 iterations each
.venv/bin/python run_tests.py --runs 3                        # 3 iterations each
.venv/bin/python run_tests.py --readonly                      # skip mutating tests
.venv/bin/python run_tests.py find_available_slots --runs 5   # one test, 5 iterations
```

CLI and web UI share the same SQLite file (`mcp_testing.db`), so edits in
either place are visible to the other.

## How scoring works

Each iteration is scored on two independent checks:

1. **Tool trace** — did the model call the tools you listed in `must_call`?
   Did it avoid the ones in `must_not_call`?
2. **Judge** — a separate LLM reads the user prompt, your expectation, and the
   model's response, and returns pass/fail with a reason.

A run passes only if **both** pass. The UI shows each dimension independently
so you can tell whether a failure is a grounding problem (wrong tools) or an
output-shape problem (right tools, bad answer).

## Project layout

```
app/
  main.py            FastAPI routes
  runner.py          async run orchestrator + SSE fanout
  db.py              SQLite schema, test + run CRUD, default seed data
  templates/         Jinja2 templates (HTMX for live updates)
  static/style.css
test_prompt.py       core runner — MCP client, tool checking, LLM judge
run_tests.py         CLI entry point
setup_auth.py        one-time OAuth flow (DCR + PKCE)
server.py            uvicorn entry point for the web UI
```
