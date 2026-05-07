"""
Patch F — rework `share_calendar` into `create_single_use_link_custom`.

The previous eval used a name-prefill prompt to force the model down
the customizable path (`shares-create_share`), but model behavior in
practice was unreliable (run #101 model hallucinated that email
prefill wasn't supported). A duration-override prompt is a cleaner
way to force the same path: `shares-create_share` accepts a
`duration` field, while `scheduling_links-create_single_use_scheduling_link`
does NOT — so a 15-minute Coffee Chat link can only be satisfied by
the share tool.

Changes:
  - eval id: 'share_calendar' → 'create_single_use_link_custom'
  - tier tag: 'shares' → 'links' (groups it with create_single_use_link)
  - prompt: now requests a 15-minute duration override
  - expect: rewritten to match new scenario

The historical 2 run_results rows for 'share_calendar' keep their
test_id label (no FK, snapshot-based). New runs will record the
new id.
"""
from __future__ import annotations

import datetime
import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "mcp_testing.db"

OLD_ID = "share_calendar"
NEW_ID = "create_single_use_link_custom"


def main() -> None:
    now = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    new_prompt = "Create a single link for my Coffee Chat event type with a 15 min duration."
    new_expect = (
        "Confirmation that a single-use scheduling link was created for the "
        "Coffee Chat event type with a 15-minute duration override. The "
        "response includes the booking URL on calendly.com or "
        "staging.calendly.com."
    )
    new_tags = ["links"]

    con = sqlite3.connect(DB_PATH)
    try:
        con.execute("BEGIN")
        cur = con.execute(
            """
            UPDATE tests
               SET id = ?, prompt = ?, expect = ?, tags = ?, updated_at = ?
             WHERE id = ?
            """,
            (NEW_ID, new_prompt, new_expect, json.dumps(new_tags), now, OLD_ID),
        )
        if cur.rowcount != 1:
            raise RuntimeError(
                f"Expected to rename 1 row from {OLD_ID!r}, got {cur.rowcount}. "
                "Has it already been patched?"
            )
        con.commit()
        print(f"  ✓ {OLD_ID!r} → {NEW_ID!r}")
        print(f"    prompt: {new_prompt}")
        print(f"    tags:   {new_tags}")
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


if __name__ == "__main__":
    main()
