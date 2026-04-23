#!/usr/bin/env python3
"""
Run all Calendly MCP workflow tests (or a single one by ID) from the SQLite
store. The same store is shared with the web UI in app/.

Usage:
  python run_tests.py                         # run all tests, 5 runs each
  python run_tests.py find_available_slots    # run one test by ID
  python run_tests.py --runs 3               # run all tests, 3 runs each
  python run_tests.py --readonly             # skip tests that write/mutate data
  python run_tests.py --list                 # list all test IDs and exit

Requires .env:
  OPENAI_API_KEY=...
  CALENDLY_MCP_TOKEN=...   (run setup_auth.py first if missing)
"""

import argparse
import asyncio
import os
import sys
import time

from dotenv import load_dotenv

from test_prompt import make_client, run_test, MODEL, MCP_SERVER_URL
from app import db

load_dotenv()


# ── Helpers ───────────────────────────────────────────────────────────────────

def separator(char="─", width=64):
    print(char * width)


# ── Runner ────────────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="Run Calendly MCP workflow tests")
    parser.add_argument("test_id",   nargs="?", help="Run a single test by ID")
    parser.add_argument("--runs",    type=int, default=5)
    parser.add_argument("--readonly", action="store_true", help="Skip tests that mutate data")
    parser.add_argument("--list",    action="store_true",  help="List test IDs and exit")
    parser.add_argument("--model",   default=MODEL, help=f"OpenAI model (default: {MODEL})")
    args = parser.parse_args()

    db.init_db()
    tests = db.list_tests()

    if args.list:
        print("\nAvailable tests:\n")
        for t in tests:
            flag = "  [writes data]" if t["mutates"] else ""
            print(f"  {t['id']}{flag}")
        print()
        sys.exit(0)

    for var in ("OPENAI_API_KEY", "CALENDLY_MCP_TOKEN"):
        if not os.environ.get(var):
            print(f"Error: {var} not set.", file=sys.stderr)
            if var == "CALENDLY_MCP_TOKEN":
                print("  Run: .venv/bin/python setup_auth.py", file=sys.stderr)
            sys.exit(1)

    if args.test_id:
        selected = [t for t in tests if t["id"] == args.test_id]
        if not selected:
            ids = ", ".join(t["id"] for t in tests)
            print(f"Error: unknown test '{args.test_id}'. Available: {ids}", file=sys.stderr)
            sys.exit(1)
    else:
        selected = tests

    if args.readonly:
        skipped  = [t["id"] for t in selected if t["mutates"]]
        selected = [t for t in selected if not t["mutates"]]
        if skipped:
            print(f"Skipping (--readonly): {', '.join(skipped)}\n")

    token  = os.environ["CALENDLY_MCP_TOKEN"]
    client = make_client()

    separator("═")
    print(f"  Calendly MCP Workflow Tests")
    print(f"  Model : {args.model}")
    print(f"  MCP   : {MCP_SERVER_URL}")
    print(f"  {len(selected)} test(s) × up to {args.runs} run(s) each")
    if any(t["mutates"] for t in selected) and args.runs > 1:
        print(f"  Note  : mutation tests capped at 1 run to avoid account pollution")
    separator("═")
    print()

    suite_start = time.monotonic()
    all_results = {}

    for test in selected:
        separator()
        flag = "  ⚠ writes data" if test["mutates"] else ""
        print(f"  {test['id']}{flag}")
        print(f"  Prompt: {test['prompt']}")
        separator()

        effective_runs = 1 if test["mutates"] else args.runs
        result = await run_test(
            prompt=test["prompt"],
            expect=test["expect"],
            runs=effective_runs,
            token=token,
            client=client,
            label=test["id"],
            must_call=test.get("must_call"),
            must_not_call=test.get("must_not_call"),
            model=args.model,
        )
        all_results[test["id"]] = result

        n, total = result["passed"], result["total"]
        status   = "✓ PASS" if n == total else f"✗ FAIL ({n}/{total})"
        print(f"\n  {status}\n")

    # ── Summary ───────────────────────────────────────────────────────────────
    elapsed = time.monotonic() - suite_start
    separator("═")
    print(f"  Summary  ({elapsed:.0f}s total)\n")

    all_passed = True
    for test_id, result in all_results.items():
        n, total = result["passed"], result["total"]
        bar    = ("█" * n) + ("░" * (total - n))
        status = "PASS" if n == total else "FAIL"
        print(f"  {status}  {bar}  {n}/{total}  {test_id}")
        if n < total:
            all_passed = False

    separator("═")
    print()
    if not all_passed:
        print("  Some tests failed — see details above.\n")
        sys.exit(1)
    else:
        print("  All tests passed.\n")


if __name__ == "__main__":
    asyncio.run(main())
