#!/usr/bin/env python3
"""
Run any prompt against the Calendly hosted MCP (mcp.calendly.com) N times
against a chosen LLM (OpenAI GPT family or Anthropic Claude family), and
report consistency.

Provider is inferred from the model name:
  - Starts with "claude" → Anthropic Messages API with native remote MCP
  - Otherwise            → OpenAI Responses API with native remote MCP

Both providers handle the full MCP tool-call loop internally — no connection
management needed here.

CLI usage:
  python test_prompt.py \\
    --prompt "Find open slots for my Coffee Chat next week" \\
    --expect "Lists available time slots with timezone" \\
    --runs 5 \\
    --model gpt-5.1

Import usage (from run_tests.py, app/runner.py):
  from test_prompt import run_once, judge, run_test
"""

import argparse
import asyncio
import json
import os
import sys
import time
from collections import Counter

from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

MCP_SERVER_URL = "https://mcp.calendly.com"
MODEL          = "gpt-5.1"


# ── Provider detection ────────────────────────────────────────────────────────

def is_anthropic(model: str) -> bool:
    return model.lower().startswith("claude")


# ── Clients ───────────────────────────────────────────────────────────────────

def make_client() -> AsyncOpenAI:
    """
    Legacy entry point kept for compatibility. Returns an OpenAI client.
    Provider-specific clients are constructed on demand in _openai_* / _anthropic_*.
    """
    return AsyncOpenAI(
        organization=os.environ.get("OPENAI_ORG_ID") or None,
    )


_openai_client: AsyncOpenAI | None = None
_anthropic_client = None  # late import


def _get_openai_client() -> AsyncOpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = AsyncOpenAI(
            organization=os.environ.get("OPENAI_ORG_ID") or None,
        )
    return _openai_client


def _get_anthropic_client():
    global _anthropic_client
    if _anthropic_client is None:
        from anthropic import AsyncAnthropic
        _anthropic_client = AsyncAnthropic()
    return _anthropic_client


# ── Judge prompt (shared across providers) ────────────────────────────────────

_JUDGE_SYSTEM_PROMPT = (
    "Evaluate whether an AI assistant's response meets a given criterion.\n\n"
    "You will be given:\n"
    "  - The criterion (what the response must satisfy).\n"
    "  - The list of tools the assistant called while producing the "
    "response (tool names only, not outputs).\n"
    "  - The response text.\n\n"
    "A response is GROUNDED if the facts it states are plausibly "
    "consistent with the tools that were called — this explicitly "
    "includes summarized or pattern forms (e.g. \"every 30 minutes from "
    "9:00 to 4:30\", ranges, counts, aggregates). Specific facts (dates, "
    "times, URLs, IDs, names) in the response should be treated as "
    "grounded if a listed tool is the kind that would return that type "
    "of data (e.g. `list_events` returning event dates/URIs, "
    "`list_event_invitees` returning attendee names). You do not see "
    "the raw tool output — trust that tools called returned real data.\n\n"
    "A response is FABRICATED when it asserts specific facts and no "
    "listed tool could plausibly have produced them — either the tools "
    "list is empty, or the tools called are unrelated to the facts "
    "claimed.\n\n"
    "Judge pass/fail strictly against the stated criterion. Do not "
    "penalize the response for things the criterion does not ask about.\n\n"
    'Reply with JSON only: {"pass": true|false, "reason": "one sentence"}'
)


def _format_judge_user_message(criteria: str, tools_called: list[str], response_text: str) -> str:
    tools_str = ", ".join(tools_called) if tools_called else "(none)"
    return (
        f"Criterion: {criteria}\n\n"
        f"Tools called: {tools_str}\n\n"
        f"Response:\n{response_text}"
    )


# ── OpenAI path ───────────────────────────────────────────────────────────────

def _openai_mcp_config(token: str) -> dict:
    return {
        "type":             "mcp",
        "server_label":     "calendly",
        "server_url":       MCP_SERVER_URL,
        "headers":          {"Authorization": f"Bearer {token}"},
        "require_approval": "never",
    }


async def _openai_run_once(prompt: str, token: str, model: str) -> tuple[str, list[str]]:
    client = _get_openai_client()
    # `reasoning` is only valid on reasoning models (o-series, gpt-5). Chat
    # models like gpt-4o reject it with 400 unsupported_parameter.
    kwargs: dict = {
        "model": model,
        "tools": [_openai_mcp_config(token)],
        "input": prompt,
    }
    if model.startswith(("o1", "o3", "o4", "gpt-5")):
        kwargs["reasoning"] = {"effort": "medium"}
    response = await client.responses.create(**kwargs)
    tools_called = [
        item.name
        for item in response.output
        if getattr(item, "type", None) == "mcp_call"
    ]
    return response.output_text, tools_called


async def _openai_judge(
    response_text: str, criteria: str, tools_called: list[str], model: str,
) -> tuple[bool, str]:
    client = _get_openai_client()
    result = await client.chat.completions.create(
        model=model,
        temperature=0,
        messages=[
            {"role": "system", "content": _JUDGE_SYSTEM_PROMPT},
            {"role": "user",   "content": _format_judge_user_message(criteria, tools_called, response_text)},
        ],
        response_format={"type": "json_object"},
    )
    data = json.loads(result.choices[0].message.content)
    return data["pass"], data["reason"]


# ── Anthropic path ────────────────────────────────────────────────────────────

def _anthropic_mcp_config(token: str) -> dict:
    return {
        "type":                "url",
        "url":                 MCP_SERVER_URL,
        "name":                "calendly",
        "authorization_token": token,
    }


async def _anthropic_run_once(prompt: str, token: str, model: str) -> tuple[str, list[str]]:
    client = _get_anthropic_client()
    response = await client.beta.messages.create(
        model=model,
        max_tokens=4096,
        mcp_servers=[_anthropic_mcp_config(token)],
        tools=[{
            "type":            "mcp_toolset",
            "mcp_server_name": "calendly",
            "default_config":  {"enabled": True, "defer_loading": False},
        }],
        messages=[{"role": "user", "content": prompt}],
        betas=["mcp-client-2025-11-20"],
    )

    text_parts   = []
    tools_called = []
    for block in response.content:
        btype = getattr(block, "type", None)
        if btype == "text":
            text_parts.append(block.text)
        elif btype == "mcp_tool_use":
            tools_called.append(block.name)

    return "".join(text_parts), tools_called


async def _anthropic_judge(
    response_text: str, criteria: str, tools_called: list[str], model: str,
) -> tuple[bool, str]:
    client = _get_anthropic_client()
    # Some newer Anthropic models (e.g. claude-opus-4-7) deprecate the
    # `temperature` parameter entirely. We rely on the sharpened system
    # prompt + tools_called grounding for stable verdicts instead.
    result = await client.messages.create(
        model=model,
        max_tokens=512,
        system=_JUDGE_SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": _format_judge_user_message(criteria, tools_called, response_text)},
        ],
    )
    raw = "".join(b.text for b in result.content if getattr(b, "type", None) == "text")
    # Claude sometimes wraps JSON in prose — extract the first {...} block.
    start = raw.find("{")
    end   = raw.rfind("}")
    if start >= 0 and end > start:
        raw = raw[start:end + 1]
    data = json.loads(raw)
    return data["pass"], data["reason"]


# ── Unified entry points (provider dispatch) ──────────────────────────────────

async def run_once(
    prompt: str,
    token:  str,
    client = None,  # deprecated; kept so callers that pass it don't break
    model:  str | None = None,
) -> tuple[str, list[str]]:
    m = model or MODEL
    if is_anthropic(m):
        return await _anthropic_run_once(prompt, token, m)
    return await _openai_run_once(prompt, token, m)


async def judge(
    response_text: str,
    criteria:      str,
    tools_called:  list[str] | None = None,
    client = None,  # deprecated
    model:  str | None = None,
) -> tuple[bool, str]:
    m = model or MODEL
    tools = tools_called or []
    if is_anthropic(m):
        return await _anthropic_judge(response_text, criteria, tools, m)
    return await _openai_judge(response_text, criteria, tools, m)


# ── Tool-trace check (provider-agnostic) ──────────────────────────────────────

def check_tools(
    tools_called:  list[str],
    must_call:     list[str] | None,
    must_not_call: list[str] | None,
) -> tuple[bool, str]:
    called    = set(tools_called)
    missing   = [t for t in (must_call     or []) if t not in called]
    forbidden = [t for t in (must_not_call or []) if t in called]

    problems = []
    if missing:   problems.append(f"missing required: {', '.join(missing)}")
    if forbidden: problems.append(f"called forbidden: {', '.join(forbidden)}")
    return (not problems, "; ".join(problems))


def check_at_most_once(
    tools_called: list[str],
    at_most_once: list[str] | None,
) -> tuple[bool, str]:
    if not at_most_once:
        return True, ""
    counts    = Counter(tools_called)
    offenders = [f"{t} ({counts[t]}x)" for t in at_most_once if counts[t] > 1]
    if offenders:
        return False, f"called more than once: {', '.join(offenders)}"
    return True, ""


def check_time(elapsed: float, max_seconds: float | None) -> tuple[bool, str]:
    if max_seconds is None:
        return True, ""
    if elapsed > max_seconds:
        return False, f"{elapsed:.1f}s exceeds budget of {max_seconds:.1f}s"
    return True, ""


# ── Suite runner (N iterations of one test) ───────────────────────────────────

async def run_test(
    prompt:        str,
    expect:        str,
    runs:          int,
    token:         str,
    client = None,  # deprecated; ignored
    label:         str = "",
    must_call:     list[str]   | None = None,
    must_not_call: list[str]   | None = None,
    at_most_once:  list[str]   | None = None,
    max_seconds:   float       | None = None,
    model:         str         | None = None,
) -> dict:
    """
    Run a test case N times. Each run scored on four dimensions:
      - tool_ok:         did required tools fire? did forbidden tools NOT fire?
      - at_most_once_ok: no tool in `at_most_once` was called more than once
      - judge_ok:        does the text meet the user-goal criterion?
      - time_ok:         elapsed <= max_seconds (if set)
    Overall pass = all four.
    """
    results = []
    prefix  = f"  [{label}] " if label else "  "
    m       = model or MODEL

    for i in range(runs):
        print(f"{prefix}Run {i + 1}/{runs} ... ", end="", flush=True)
        t0 = time.monotonic()

        tool_ok,          tool_reason          = True,  ""
        at_most_once_ok,  at_most_once_reason  = True,  ""
        judge_ok,         judge_reason         = False, ""
        text, tools_called                     = "", []

        try:
            text, tools_called = await run_once(prompt, token, model=m)
            tool_ok,         tool_reason         = check_tools(tools_called, must_call, must_not_call)
            at_most_once_ok, at_most_once_reason = check_at_most_once(tools_called, at_most_once)
            judge_ok,        judge_reason        = await judge(text, expect, tools_called=tools_called, model=m)
        except Exception as e:
            judge_reason = f"Exception: {e}"

        elapsed            = time.monotonic() - t0
        time_ok, time_reason = check_time(elapsed, max_seconds)
        passed = tool_ok and judge_ok and at_most_once_ok and time_ok

        results.append({
            "passed":              passed,
            "tool_ok":             tool_ok,
            "tool_reason":         tool_reason,
            "at_most_once_ok":     at_most_once_ok,
            "at_most_once_reason": at_most_once_reason,
            "judge_ok":            judge_ok,
            "judge_reason":        judge_reason,
            "time_ok":             time_ok,
            "time_reason":         time_reason,
            "tools":               tools_called,
            "text":                text,
            "elapsed":             elapsed,
        })

        status    = "PASS" if passed else "FAIL"
        tools_str = ", ".join(tools_called) if tools_called else "none"
        print(f"{status}  [{tools_str}]  ({elapsed:.1f}s)")
        if not tool_ok:
            print(f"{prefix}       → tool: {tool_reason}")
        if not at_most_once_ok:
            print(f"{prefix}       → at_most_once: {at_most_once_reason}")
        if not judge_ok:
            print(f"{prefix}       → judge: {judge_reason}")
        if not time_ok:
            print(f"{prefix}       → time: {time_reason}")

    n_passed = sum(r["passed"] for r in results)
    return {"passed": n_passed, "total": runs, "runs": results}


# ── CLI entry point ───────────────────────────────────────────────────────────

async def _cli_main():
    parser = argparse.ArgumentParser(
        description="Test a single prompt against the Calendly MCP via a chosen LLM"
    )
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--expect", required=True, help="Plain-English success criterion")
    parser.add_argument("--runs",   type=int, default=5)
    parser.add_argument("--model",  default=MODEL, help=f"Model name (default: {MODEL})")
    args = parser.parse_args()

    token = os.environ.get("CALENDLY_MCP_TOKEN")
    if not token:
        print("Error: CALENDLY_MCP_TOKEN not set.", file=sys.stderr)
        print("  Run: .venv/bin/python setup_auth.py", file=sys.stderr)
        sys.exit(1)

    provider_key = "ANTHROPIC_API_KEY" if is_anthropic(args.model) else "OPENAI_API_KEY"
    if not os.environ.get(provider_key):
        print(f"Error: {provider_key} not set (required for model '{args.model}').", file=sys.stderr)
        sys.exit(1)

    print(f"\nModel  : {args.model}")
    print(f"MCP    : {MCP_SERVER_URL}")
    print(f"Prompt : {args.prompt}")
    print(f"Expect : {args.expect}")
    print(f"Runs   : {args.runs}\n")

    result = await run_test(
        args.prompt, args.expect, args.runs, token, model=args.model,
    )

    n, total = result["passed"], result["total"]
    print(f"\n  Result: {n}/{total} passed")

    if n < total:
        print("\n  Failed runs:")
        for i, r in enumerate(result["runs"]):
            if not r["passed"]:
                reasons = []
                if not r["tool_ok"]:         reasons.append(f"tool: {r['tool_reason']}")
                if not r["at_most_once_ok"]: reasons.append(f"at_most_once: {r['at_most_once_reason']}")
                if not r["judge_ok"]:        reasons.append(f"judge: {r['judge_reason']}")
                if not r["time_ok"]:         reasons.append(f"time: {r['time_reason']}")
                print(f"\n    Run {i + 1}: {' | '.join(reasons)}")
                print(f"    Response: {r['text'][:400]}...")
        sys.exit(1)
    else:
        print("  All runs passed.\n")


if __name__ == "__main__":
    asyncio.run(_cli_main())
