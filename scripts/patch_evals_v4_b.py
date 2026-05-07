"""
Patch v4 evals to swap aundreia.heisey@calendly.com for
cash0902@gmail.com so re-running the suite doesn't repeatedly spam a
real Calendly user. Affects evals #13, #14, #16, #17, #18 (prompts) and
#14 (expect text).

The substitute identity is "Michael Kimelblat <cash0902@gmail.com>",
which is an existing member of the staging test org so bookings stay
self-contained.
"""
from __future__ import annotations

import datetime
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "mcp_testing.db"

PROMPT_PATCHES = [
    {
        "id":     "read_invitee",
        "prompt": "Show me the invitee details for cash0902@gmail.com on my next Coffee Chat.",
    },
    {
        "id":     "book_meeting",
        "prompt": "Book an Intro Call with Michael Kimelblat (cash0902@gmail.com) for next Thursday at 2pm.",
        "expect": "Confirmation that the meeting was booked. The confirmation includes: Event type: Intro Call, Invitee: Michael Kimelblat (cash0902@gmail.com), and Date and time: Thursday at 2:00 PM.",
    },
    {
        "id":     "mark_no_show",
        "prompt": "Mark Michael (cash0902@gmail.com) as a no-show for yesterday's Coffee Chat.",
    },
    {
        "id":     "read_no_show",
        "prompt": "Was Michael (cash0902@gmail.com) marked as a no-show for yesterday's Coffee Chat?",
    },
    {
        "id":     "clear_no_show",
        "prompt": "Remove Michael's (cash0902@gmail.com) no-show flag from yesterday's Coffee Chat.",
    },
]


def main() -> None:
    now = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute("BEGIN")
        for p in PROMPT_PATCHES:
            sets = ["prompt = ?", "updated_at = ?"]
            params: list = [p["prompt"], now]
            if "expect" in p:
                sets.insert(1, "expect = ?")
                params.insert(1, p["expect"])
            params.append(p["id"])
            cur = con.execute(
                f"UPDATE tests SET {', '.join(sets)} WHERE id = ?",
                params,
            )
            if cur.rowcount != 1:
                raise RuntimeError(f"Expected to patch 1 row for id={p['id']}, got {cur.rowcount}")
            print(f"Patched {p['id']!r}")
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


if __name__ == "__main__":
    main()
