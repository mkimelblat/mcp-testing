"""
Patch exemplars + one must_call entry to fix the 6 failures seen in
run #110 (29/35 in exemplar mode). Failure forensics broke the
remainder into:

- Phrasing equivalence (3 evals): exemplar required a specific
  phrase ("single-use" / "overriding the default 30" / "Reply 'yes' /
  irreversible") but the model conveyed the same fact with different
  vocabulary. The judge prompt now has a general "equivalent
  phrasings" rule (Layer A here); these per-exemplar relaxations are
  the belt-and-suspenders backup.

- Fixture-fragile (1 eval): read_meeting was locked to specific
  date/time/location values that drift. Same Layer-B treatment as
  the run-109 patches — describe shape instead of values.

- Singular vs plural (1 eval): check_when_busy's exemplar said
  "A list of specific time intervals", but a single busy block is
  a valid response. Loosen to "one or more".

- must_call drift (1 eval): read_no_show required
  meetings-get_invitee_no_show, but list_event_invitees returns
  no-show status inline. Same pattern as the read_routing_form /
  read_availability_schedule fixes — drop the redundant get_*.

Run after editing test_prompt.py's _JUDGE_SYSTEM_PROMPT_EXEMPLAR
(Layer A — adds the "Equivalent phrasings" rule).
"""
from __future__ import annotations

import datetime
import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "mcp_testing.db"

EXEMPLAR_PATCHES = [
    # ── Phrasing-equivalence relaxations (3 evals) ──────────────────────
    {
        "id":     "create_single_use_link",
        "reason": "Accept any phrasing that conveys single-use semantics; the link itself + a brief equivalent phrasing is enough.",
        "exemplar":
            "Here's a single-use scheduling link for **Coffee Chat**: "
            "`https://calendly.com/d/<unique-id>` — share it with your "
            "candidate. It can only be used once.\n\n"
            "(The exact URL after `/d/` is unique per call; any "
            "calendly.com or staging.calendly.com link of that shape is "
            "acceptable. The response should convey single-use semantics "
            "in any wording — 'one-time', 'single-use', 'can only be "
            "used once', 'one-time link', etc. — but no specific phrase "
            "is required.)",
    },
    {
        "id":     "create_single_use_link_custom",
        "reason": "Accept '15-minute link' as sufficient duration callout; explicit 'overrides the default' is optional.",
        "exemplar":
            "Created a customized 15-minute scheduling link for **Coffee "
            "Chat**: `https://calendly.com/d/<unique-id>` — when used, "
            "the booking will be 15 minutes (overriding the default 30). "
            "Share with your invitee.\n\n"
            "(The exact URL after `/d/` is unique per call; any "
            "calendly.com or staging.calendly.com link of that shape is "
            "acceptable. Saying '15-minute link' or otherwise surfacing "
            "the 15-minute duration is sufficient; an explicit 'overrides "
            "the default duration' callout is acceptable but not "
            "required.)",
    },
    {
        "id":     "remove_team_member",
        "reason": "Pause-for-confirmation is the substantive requirement; explicit 'irreversible / Reply yes' wording is one acceptable form, not the only one.",
        "exemplar":
            "Either:\n"
            "(a) **Explicit-warning confirmation.** Just to confirm — "
            "you want me to remove **mk** (cash0902@gmail.com) from your "
            "Calendly team? This is immediate and can't be undone. "
            "Reply 'yes' to confirm and I'll proceed.\n\n"
            "OR\n\n"
            "(b) **Softer confirmation.** The most recently added team "
            "member is **mk** (cash0902@gmail.com). Please confirm: do "
            "you want me to remove this member from your Calendly "
            "organization?\n\n"
            "(The key requirement is that the model identifies the "
            "candidate by name/email and pauses for confirmation before "
            "the destructive action — exact wording varies. "
            "Fire-and-forget removal without confirmation is a "
            "fail.)",
    },

    # ── Fixture-fragile (1 eval) ─────────────────────────────────────────
    {
        "id":     "read_meeting",
        "reason": "Specific date/time/location lock the exemplar to a moment in time; describe shape instead.",
        "exemplar":
            "Your next **Coffee Chat** is on a specific date and time "
            "in the user's timezone (e.g. \"Thursday, May 15 at "
            "10:00 AM PT (ending at 10:30 AM)\"), with the configured "
            "location (e.g. Google Meet, Zoom, a custom location, or "
            "another configured kind).\n\n"
            "(The exact date, time, and location reflect the actual "
            "next Coffee Chat from the API. If there is no upcoming "
            "Coffee Chat, the response says so plainly without "
            "inventing one.)",
    },

    # ── Singular vs plural (1 eval) ──────────────────────────────────────
    {
        "id":     "check_when_busy",
        "reason": "A single busy interval is a valid response; loosen 'a list' to 'one or more'.",
        "exemplar":
            "On the requested Thursday, in your timezone "
            "(America/Los_Angeles), you are busy at:\n\n"
            "One or more specific time intervals reflecting the "
            "calendar's actual busy times (e.g. \"10:00 AM – 10:30 AM "
            "PT — Coffee Chat\"). A single interval is fine if there's "
            "only one busy block; the model does not need to add "
            "explicit \"and these are all\" framing.\n\n"
            "If you have no busy intervals on that day, the response "
            "says so plainly without inventing blocks.",
    },
]

# read_no_show: drop meetings-get_invitee_no_show from must_call.
# list_event_invitees returns no-show status inline, so the model
# correctly answers without the separate get call.
MUST_CALL_PATCHES = [
    {
        "id":     "read_no_show",
        "reason": "list_event_invitees returns no-show status inline; the get_* call is redundant (same pattern as read_routing_form / read_availability_schedule).",
        "drop":   ["meetings-get_invitee_no_show"],
    },
]


def main() -> None:
    now = (
        datetime.datetime.now(datetime.UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute("BEGIN")

        # Exemplar rewrites
        for p in EXEMPLAR_PATCHES:
            cur = con.execute(
                "UPDATE tests SET exemplar = ?, updated_at = ? WHERE id = ?",
                (p["exemplar"], now, p["id"]),
            )
            if cur.rowcount != 1:
                raise RuntimeError(f"Expected 1 row for id={p['id']}, got {cur.rowcount}")
            print(f"  ✓ exemplar  {p['id']}: {p['reason']}")

        # must_call drops
        for p in MUST_CALL_PATCHES:
            row = con.execute(
                "SELECT must_call FROM tests WHERE id = ?", (p["id"],)
            ).fetchone()
            if row is None:
                raise RuntimeError(f"No row for id={p['id']}")
            current = json.loads(row[0]) if row[0] else []
            new = [t for t in current if t not in p["drop"]]
            if new == current:
                raise RuntimeError(
                    f"Expected to drop {p['drop']} from {p['id']}, "
                    f"but none of those were in must_call={current}"
                )
            cur = con.execute(
                "UPDATE tests SET must_call = ?, updated_at = ? WHERE id = ?",
                (json.dumps(new), now, p["id"]),
            )
            if cur.rowcount != 1:
                raise RuntimeError(f"Expected 1 row for id={p['id']}, got {cur.rowcount}")
            print(f"  ✓ must_call {p['id']}: dropped {p['drop']} → {new}")

        con.commit()
        print(
            f"\nApplied {len(EXEMPLAR_PATCHES)} exemplar patch(es) "
            f"+ {len(MUST_CALL_PATCHES)} must_call patch(es)."
        )
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


if __name__ == "__main__":
    main()
