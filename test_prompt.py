#!/usr/bin/env python3
"""
Run any prompt against the Calendly hosted MCP (mcp.calendly.com) N times
using GPT-5.4 (Thinking) via the OpenAI Responses API, and report consistency.

The Responses API handles the full MCP tool-call loop internally —
no connection management needed here.

CLI usage:
  python test_prompt.py \\
    --prompt "Find open slots for my Coffee Chat next week" \\
    --expect "Lists available time slots with timezone" \\
    --runs 5

Import usage (from run_tests.py):
  from test_prompt import run_once, judge, run_test
"""

import argparse
import asyncio
import json
import os
import sys
import time

from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

MCP_SERVER_URL = "https://mcp.calendly.com"
MODEL          = "gpt-5.1"


def make_client() -> AsyncOpenAI:
    return AsyncOpenAI(
        organization=os.environ.get("OPENAI_ORG_ID") or None,
    )


def mcp_tool_config(token: str) -> dict:
    return {
        "type":             "mcp",
        "server_label":     "calendly",
        "server_url":       MCP_SERVER_URL,
        "headers":          {"Authorization": f"Bearer {token}"},
        "require_approval": "never",
    }


async def run_once(
    prompt: str,
    token: str,
    client: AsyncOpenAI,
    model: str | None = None,
) -> tuple[str, list[str]]:
    """
    Send one prompt to the chosen model with the Calendly MCP attached.
    Returns (response_text, tools_called).
    OpenAI handles the full tool-call loop internally.
    """
    response = await client.responses.create(
        model=model or MODEL,
        reasoning={"effort": "medium"},
        tools=[mcp_tool_config(token)],
        input=prompt,
    )

    tools_called = [
        item.name
        for item in response.output
        if getattr(item, "type", None) == "mcp_call"
    ]

    return response.output_text, tools_called


async def judge(
    response_text: str,
    criteria: str,
    client: AsyncOpenAI,
    model: str | None = None,
) -> tuple[bool, str]:
    """Use an LLM to evaluate whether the response meets the criteria."""
    result = await client.chat.completions.create(
        model=model or MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "Evaluate whether an AI assistant's response meets a given criterion. "
                    'Reply with JSON only: {"pass": true|false, "reason": "one sentence"}'
                ),
            },
            {
                "role": "user",
                "content": f"Criterion: {criteria}\n\nResponse:\n{response_text}",
            },
        ],
        response_format={"type": "json_object"},
    )
    data = json.loads(result.choices[0].message.content)
    return data["pass"], data["reason"]


def check_tools(
    tools_called: list[str],
    must_call: list[str] | None,
    must_not_call: list[str] | None,
) -> tuple[bool, str]:
    """
    Check tool trace against required / forbidden tools.
    Returns (passed, reason). reason is empty on pass.
    """
    called = set(tools_called)
    missing   = [t for t in (must_call or [])     if t not in called]
    forbidden = [t for t in (must_not_call or []) if t in called]

    problems = []
    if missing:
        problems.append(f"missing required: {', '.join(missing)}")
    if forbidden:
        problems.append(f"called forbidden: {', '.join(forbidden)}")
    return (not problems, "; ".join(problems))


async def run_test(
    prompt: str,
    expect: str,
    runs: int,
    token: str,
    client: AsyncOpenAI,
    label: str = "",
    must_call: list[str] | None = None,
    must_not_call: list[str] | None = None,
    model: str | None = None,
) -> dict:
    """
    Run a test case N times. Each run scored on two dimensions:
      - tool_ok:  did required tools fire? did forbidden tools NOT fire?
      - judge_ok: does the text meet the user-goal criterion?
    Overall pass = tool_ok AND judge_ok.
    """
    results = []
    prefix  = f"  [{label}] " if label else "  "

    for i in range(runs):
        print(f"{prefix}Run {i + 1}/{runs} ... ", end="", flush=True)
        t0 = time.monotonic()

        tool_ok,  tool_reason  = True, ""
        judge_ok, judge_reason = False, ""
        text, tools_called     = "", []

        try:
            text, tools_called     = await run_once(prompt, token, client, model=model)
            tool_ok,  tool_reason  = check_tools(tools_called, must_call, must_not_call)
            judge_ok, judge_reason = await judge(text, expect, client, model=model)
        except Exception as e:
            judge_reason = f"Exception: {e}"

        passed  = tool_ok and judge_ok
        elapsed = time.monotonic() - t0
        results.append({
            "passed":       passed,
            "tool_ok":      tool_ok,
            "tool_reason":  tool_reason,
            "judge_ok":     judge_ok,
            "judge_reason": judge_reason,
            "tools":        tools_called,
            "text":         text,
            "elapsed":      elapsed,
        })

        status    = "PASS" if passed else "FAIL"
        tools_str = ", ".join(tools_called) if tools_called else "none"
        print(f"{status}  [{tools_str}]  ({elapsed:.1f}s)")
        if not tool_ok:
            print(f"{prefix}       → tool: {tool_reason}")
        if not judge_ok:
            print(f"{prefix}       → judge: {judge_reason}")

    n_passed = sum(r["passed"] for r in results)
    return {"passed": n_passed, "total": runs, "runs": results}


# ── CLI entry point ───────────────────────────────────────────────────────────

async def _cli_main():
    parser = argparse.ArgumentParser(
        description="Test a single prompt against the Calendly MCP via GPT-5.4"
    )
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--expect", required=True, help="Plain-English success criterion")
    parser.add_argument("--runs",   type=int, default=5)
    parser.add_argument("--model",  default=MODEL, help=f"OpenAI model (default: {MODEL})")
    args = parser.parse_args()

    for var in ("OPENAI_API_KEY", "CALENDLY_MCP_TOKEN"):
        if not os.environ.get(var):
            print(f"Error: {var} not set.", file=sys.stderr)
            if var == "CALENDLY_MCP_TOKEN":
                print("  Run: .venv/bin/python setup_auth.py", file=sys.stderr)
            sys.exit(1)

    token  = os.environ["CALENDLY_MCP_TOKEN"]
    client = make_client()

    print(f"\nModel  : {args.model}")
    print(f"MCP    : {MCP_SERVER_URL}")
    print(f"Prompt : {args.prompt}")
    print(f"Expect : {args.expect}")
    print(f"Runs   : {args.runs}\n")

    result = await run_test(
        args.prompt, args.expect, args.runs, token, client, model=args.model,
    )

    n, total = result["passed"], result["total"]
    print(f"\n  Result: {n}/{total} passed")

    if n < total:
        print("\n  Failed runs:")
        for i, r in enumerate(result["runs"]):
            if not r["passed"]:
                reasons = []
                if not r["tool_ok"]:  reasons.append(f"tool: {r['tool_reason']}")
                if not r["judge_ok"]: reasons.append(f"judge: {r['judge_reason']}")
                print(f"\n    Run {i + 1}: {' | '.join(reasons)}")
                print(f"    Response: {r['text'][:400]}...")
        sys.exit(1)
    else:
        print("  All runs passed.\n")


if __name__ == "__main__":
    asyncio.run(_cli_main())
