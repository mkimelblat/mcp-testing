"""
Bulk-replace the `tests` table with the v4 eval set defined in
/tmp/evals_v4.csv. Idempotent: a backup snapshot already lives in
backups/tests_pre_v4_*.json.

CSV columns: num, eval_id, tier, tags, prompt, expect, must_call,
must_not_call, at_most_once, mutates. List-valued fields use ';' as
separator.
"""
from __future__ import annotations

import csv
import datetime
import json
import sqlite3
import sys
from pathlib import Path

CSV_PATH = Path(__file__).resolve().parent / "evals_v4.csv"
DB_PATH  = Path(__file__).resolve().parent.parent / "mcp_testing.db"


def split_list(cell: str) -> list[str]:
    return [p.strip() for p in cell.split(";") if p.strip()]


def main() -> None:
    rows = []
    with CSV_PATH.open() as f:
        for row in csv.DictReader(f):
            rows.append({
                "id":            row["eval_id"],
                "prompt":        row["prompt"],
                "expect":        row["expect"],
                "must_call":     json.dumps(split_list(row["must_call"])),
                "must_not_call": json.dumps(split_list(row["must_not_call"])),
                "at_most_once":  json.dumps(split_list(row["at_most_once"])),
                "tags":          json.dumps(split_list(row["tags"])),
                "mutates":       int(row["mutates"]),
                "position":      int(row["num"]),
            })

    if len(rows) != 39:
        sys.exit(f"Expected 39 rows in CSV, got {len(rows)}")

    now = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute("BEGIN")
        con.execute("DELETE FROM tests")
        con.executemany(
            """
            INSERT INTO tests (
                id, prompt, expect, must_call, must_not_call,
                mutates, position, created_at, updated_at,
                at_most_once, max_seconds, tags
            ) VALUES (
                :id, :prompt, :expect, :must_call, :must_not_call,
                :mutates, :position, :created_at, :updated_at,
                :at_most_once, NULL, :tags
            )
            """,
            [{**r, "created_at": now, "updated_at": now} for r in rows],
        )
        con.commit()
        print(f"Migrated {len(rows)} evals")
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


if __name__ == "__main__":
    main()
