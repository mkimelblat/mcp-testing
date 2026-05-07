"""
Patch v4 eval prompts for #21 (read_availability_schedule) and #28
(remove_team_member) to match the staging "2FA Test" account state and
to remove fictional fixture references. Updates `tests` rows in place.
"""
from __future__ import annotations

import datetime
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "mcp_testing.db"

PATCHES = [
    {
        "id":     "read_availability_schedule",
        "prompt": "Show me the rules for my Working hours availability schedule.",
    },
    {
        "id":     "remove_team_member",
        "prompt": "Remove the most recently added team member from my Calendly team.",
    },
]


def main() -> None:
    now = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute("BEGIN")
        for p in PATCHES:
            cur = con.execute(
                "UPDATE tests SET prompt = ?, updated_at = ? WHERE id = ?",
                (p["prompt"], now, p["id"]),
            )
            if cur.rowcount != 1:
                raise RuntimeError(f"Expected to patch 1 row for id={p['id']}, got {cur.rowcount}")
            print(f"Patched {p['id']!r} prompt")
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


if __name__ == "__main__":
    main()
