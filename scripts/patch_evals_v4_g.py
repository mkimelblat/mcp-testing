"""
Patch G — clarify read_routing_form expect so the judge stops calling
fabrication when the model answers from list_routing_forms alone.

Run #104 incident: the model called list_routing_forms (which returns
each form with its `questions` array inline — name, type, required,
answer_choices) and correctly described the first form's question. The
judge, lacking knowledge that list_routing_forms includes questions
inline, concluded the model fabricated the question because
get_routing_form wasn't called.

The expect now explicitly states what list_routing_forms returns, so
the judge has no excuse to penalize a correct response.
"""
from __future__ import annotations

import datetime
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "mcp_testing.db"

NEW_EXPECT = (
    "Either a description of the first routing form's questions (each "
    "question's text and type), OR a clear statement that the form has "
    "no questions / is in draft state. NOTE for the judge: the "
    "`routing_forms-list_routing_forms` endpoint returns each form with "
    "its `questions` array inline (each with `name`, `type`, `required`, "
    "and `answer_choices`), so a response that describes the first "
    "form's questions after calling only `list_routing_forms` is NOT "
    "fabrication — the data is in the list response. Calling "
    "`get_routing_form` is optional. Penalize fabrication only if a "
    "response invents questions that don't appear in the API data."
)


def main() -> None:
    now = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute("BEGIN")
        cur = con.execute(
            "UPDATE tests SET expect = ?, updated_at = ? WHERE id = 'read_routing_form'",
            (NEW_EXPECT, now),
        )
        if cur.rowcount != 1:
            raise RuntimeError(f"Expected to patch 1 row, got {cur.rowcount}")
        con.commit()
        print("  ✓ read_routing_form expect updated")
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


if __name__ == "__main__":
    main()
