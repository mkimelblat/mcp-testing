"""
Patch v4 evals based on remaining real failures from run #101 that
patch C didn't address.

Single change in this round: shift book_meeting + cancel_meeting from
2pm to 2:30pm.

Why: the staging Intro Call event type's available slots align on the
half-hour (2:30, 3:00, 3:30...). 2:00 PM isn't a returned slot, so the
model correctly refused to book at 2pm — but that breaks the eval. Two
options to fix:
  - Reconfigure the event type's slot increments so 2:00 is offered.
    Requires admin-only Calendly settings we don't expose.
  - Move the prompt time to a real slot.

Going with the prompt update. This diverges slightly from the OpenAI
submission doc's verbatim '2:00 PM' phrasing but keeps the suite
runnable on the actual staging fixture. If you ever need
submission-doc fidelity for a re-grade, run the chatgpt-tagged subset
against an account configured for hourly slots.
"""
from __future__ import annotations

import datetime
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "mcp_testing.db"

PATCHES = [
    {
        "id":     "book_meeting",
        "prompt": "Book an Intro Call with Michael Kimelblat (cash0902@gmail.com) for next Thursday at 2:30pm.",
        "expect": "Confirmation that the meeting was booked. The confirmation includes: Event type: Intro Call, Invitee: Michael Kimelblat (cash0902@gmail.com), and Date and time: Thursday at 2:30 PM.",
    },
    {
        "id":     "cancel_meeting",
        "prompt": "Cancel my 2:30pm Intro Call appointment next Thursday.",
    },
]


def main() -> None:
    now = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute("BEGIN")
        for p in PATCHES:
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
                raise RuntimeError(f"Expected 1 row for id={p['id']}, got {cur.rowcount}")
            print(f"  ✓ {p['id']}")
        con.commit()
        print(f"\nApplied {len(PATCHES)} patches.")
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


if __name__ == "__main__":
    main()
