"""
Export the golden v5 deliverable for Gemini.

Reads `scripts/golden_v5.csv` (case_id, prompt_index, prompt) and joins
each row against the canonical `tests.exemplar` and `tests.must_call`
from `mcp_testing.db`. Emits `scripts/golden_v5_deliverable.csv` with
columns:

  case_id, prompt_index, prompt, exemplar, tools_in_scope

`tools_in_scope` is a comma-separated list of `must_call` tool names
for the case — informational, in case Gemini wants to know which MCP
tools each case exercises. If Gemini's spec ends up not wanting that
column, just drop it.
"""
from __future__ import annotations

import csv
import json
import sqlite3
import sys
from pathlib import Path

ROOT     = Path(__file__).resolve().parent.parent
DB_PATH  = ROOT / "mcp_testing.db"
SRC_CSV  = ROOT / "scripts" / "golden_v5.csv"
DST_CSV  = ROOT / "scripts" / "golden_v5_deliverable.csv"


def main() -> None:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cases: dict[str, dict] = {}
    for r in con.execute("SELECT id, exemplar, must_call FROM tests"):
        cases[r["id"]] = {
            "exemplar":  r["exemplar"] or "",
            "must_call": json.loads(r["must_call"]) if r["must_call"] else [],
        }
    con.close()

    src_rows = list(csv.DictReader(SRC_CSV.open()))
    missing = sorted({r["case_id"] for r in src_rows} - set(cases))
    if missing:
        sys.exit(f"CSV references unknown case_ids: {missing}")

    written = 0
    with DST_CSV.open("w", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["case_id", "prompt_index", "prompt", "exemplar", "tools_in_scope"],
        )
        w.writeheader()
        for r in src_rows:
            case = cases[r["case_id"]]
            if not case["exemplar"]:
                sys.exit(f"Case {r['case_id']} has no exemplar set in DB")
            w.writerow({
                "case_id":        r["case_id"],
                "prompt_index":   r["prompt_index"],
                "prompt":         r["prompt"],
                "exemplar":       case["exemplar"],
                "tools_in_scope": ",".join(case["must_call"]),
            })
            written += 1

    by_case: dict[str, int] = {}
    for r in src_rows:
        by_case[r["case_id"]] = by_case.get(r["case_id"], 0) + 1

    print(f"Wrote {written} rows to {DST_CSV}")
    print(f"  cases: {len(by_case)}")
    print(f"  prompts/case: {min(by_case.values())}-{max(by_case.values())}")
    print(f"\nBy case:")
    for cid in sorted(by_case):
        print(f"  {cid}: {by_case[cid]} prompts")


if __name__ == "__main__":
    main()
