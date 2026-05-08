"""
Golden v5 validation runner.

Reads `scripts/golden_v5.csv` (case_id, prompt_index, prompt) and runs
each prompt through the harness's exemplar-mode judge. For each row,
the canonical `exemplar` and `must_call` come from the matching
`tests` row in `mcp_testing.db`; the prompt comes from the CSV.

Persists results to `scripts/golden_v5_results.json` (one JSON object
per row) and prints per-case aggregate pass rates so we can see which
prompts/exemplars need iteration.

Usage:
  CALENDLY_MCP_TOKEN=... .venv/bin/python scripts/run_golden_suite.py
  # or limit to specific cases:
  ... scripts/run_golden_suite.py --case list_event_types --case book_meeting
  # or one prompt-index per case for a quick smoke:
  ... scripts/run_golden_suite.py --first-only

Re-run `scripts/fixture_reset_staging.py` + `scripts/fixture_setup
_staging.py` before this for a deterministic starting state.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, UTC
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))

import test_prompt  # noqa: E402

DB_PATH      = ROOT / "mcp_testing.db"
CSV_PATH     = ROOT / "scripts" / "golden_v5.csv"
RESULTS_PATH = ROOT / "scripts" / "golden_v5_results.json"


def load_cases() -> dict[str, dict]:
    """Pull canonical exemplar / criteria / must_call for each case_id."""
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    out = {}
    for r in con.execute(
        "SELECT id, criteria, exemplar, must_call, must_not_call, "
        "at_most_once, max_seconds FROM tests"
    ):
        out[r["id"]] = {
            "criteria":      r["criteria"],
            "exemplar":      r["exemplar"],
            "must_call":     json.loads(r["must_call"])     if r["must_call"]     else [],
            "must_not_call": json.loads(r["must_not_call"]) if r["must_not_call"] else [],
            "at_most_once":  json.loads(r["at_most_once"])  if r["at_most_once"]  else [],
            "max_seconds":   r["max_seconds"],
        }
    con.close()
    return out


def load_prompts(only_cases: list[str] | None, first_only: bool) -> list[dict]:
    rows = []
    seen = set()
    with CSV_PATH.open() as f:
        for row in csv.DictReader(f):
            cid = row["case_id"]
            if only_cases and cid not in only_cases:
                continue
            if first_only and cid in seen:
                continue
            seen.add(cid)
            rows.append({
                "case_id":      cid,
                "prompt_index": int(row["prompt_index"]),
                "prompt":       row["prompt"],
            })
    return rows


async def main(only_cases: list[str] | None, first_only: bool, model: str) -> None:
    token = os.environ.get("CALENDLY_MCP_TOKEN")
    if not token:
        sys.exit("CALENDLY_MCP_TOKEN not set in .env")

    cases  = load_cases()
    prompts = load_prompts(only_cases, first_only)
    if not prompts:
        sys.exit("No prompts matched filter")

    # Verify every CSV case_id exists in the DB
    missing = sorted({p["case_id"] for p in prompts} - set(cases))
    if missing:
        sys.exit(f"CSV references unknown case_ids: {missing}")

    print(f"Running {len(prompts)} prompts across "
          f"{len({p['case_id'] for p in prompts})} cases against {model}\n")

    results = []
    for i, p in enumerate(prompts, 1):
        case = cases[p["case_id"]]
        print(f"[{i}/{len(prompts)}] {p['case_id']}#{p['prompt_index']}: "
              f"{p['prompt'][:80]}")
        out = await test_prompt.run_test(
            prompt        = p["prompt"],
            runs          = 1,
            token         = token,
            criteria      = case["criteria"],
            exemplar      = case["exemplar"],
            judge_mode    = "exemplar",
            must_call     = case["must_call"],
            must_not_call = case["must_not_call"],
            at_most_once  = case["at_most_once"],
            max_seconds   = case["max_seconds"],
            model         = model,
            label         = f"{p['case_id']}#{p['prompt_index']}",
        )
        run = out["runs"][0]
        results.append({
            "case_id":      p["case_id"],
            "prompt_index": p["prompt_index"],
            "prompt":       p["prompt"],
            "passed":       run["passed"],
            "tool_ok":      run["tool_ok"],
            "judge_ok":     run["judge_ok"],
            "tool_reason":  run["tool_reason"],
            "judge_reason": run["judge_reason"],
            "tools_called": run["tools"],
            "text":         run["text"],
            "elapsed":      run["elapsed"],
        })

    # Per-case aggregate
    by_case: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        by_case[r["case_id"]].append(r)

    print("\n" + "=" * 70)
    print(f"PER-CASE PASS RATE  (target ≥ 85%)")
    print("=" * 70)
    overall_pass = 0
    overall_total = 0
    for case_id in sorted(by_case):
        rs = by_case[case_id]
        passed = sum(1 for r in rs if r["passed"])
        total  = len(rs)
        overall_pass  += passed
        overall_total += total
        bar = "█" * passed + "░" * (total - passed)
        print(f"  {case_id:<35s} {passed:>2d}/{total:<2d}  {bar}")
    pct = 100 * overall_pass / overall_total if overall_total else 0
    print("-" * 70)
    print(f"  {'OVERALL':<35s} {overall_pass:>2d}/{overall_total:<2d}  {pct:.1f}%")
    print("=" * 70)

    # Persist results
    payload = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "model":        model,
        "judge_mode":   "exemplar",
        "judge_model":  test_prompt.JUDGE_MODEL,
        "totals": {
            "passed": overall_pass,
            "total":  overall_total,
            "pct":    round(pct, 1),
        },
        "by_case": {
            cid: {
                "passed": sum(1 for r in rs if r["passed"]),
                "total":  len(rs),
            }
            for cid, rs in by_case.items()
        },
        "rows": results,
    }
    RESULTS_PATH.write_text(json.dumps(payload, indent=2))
    print(f"\nResults persisted to {RESULTS_PATH}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--case", action="append", default=[],
                   help="Filter to specific case_id(s); repeat for multiple")
    p.add_argument("--first-only", action="store_true",
                   help="Run only prompt_index=1 per case (smoke check)")
    p.add_argument("--model", default=test_prompt.MODEL,
                   help=f"Override main model (default: {test_prompt.MODEL})")
    args = p.parse_args()
    asyncio.run(main(args.case or None, args.first_only, args.model))
