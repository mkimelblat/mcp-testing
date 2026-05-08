"""
Patch exemplars to fix two failure modes seen in run #109:

Layer B (5 evals) — exemplars locked in fixture-specific values that
drift over time (specific dates, slot counts, schedule counts, etc.).
Rewrite to describe the SHAPE of an acceptable answer instead of
specific values.

Layer C (3 evals) — exemplars assumed one outcome path; reality has
another path that's also valid. Rewrite to accept either.

Run after editing test_prompt.py's _JUDGE_SYSTEM_PROMPT_EXEMPLAR
(Layer A) so the exemplar judge stops flagging grounded extras as
fabrication.
"""
from __future__ import annotations

import datetime
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "mcp_testing.db"

PATCHES = [
    # ── Layer B: fixture-fragile exemplars rewritten as shape descriptions ──
    {
        "id":     "list_event_types",
        "reason": "Specific durations + names drift as evals mutate them. Describe shape.",
        "exemplar":
            "Here are your event types:\n"
            "- **Coffee Chat**\n"
            "- **30 Minute Meeting**\n"
            "- **A Managed Event**\n"
            "- **test**\n"
            "- **Z Managed Event**\n\n"
            "(Each item identifies an event type by name. Duration and "
            "additional fields are acceptable but not required.)",
    },
    {
        "id":     "find_event_type_available_times",
        "reason": "Specific slot ranges drift with availability rule edits.",
        "exemplar":
            "Here are open slots for **Coffee Chat** in the requested date "
            "range (in your timezone, America/Los_Angeles):\n\n"
            "A non-empty list of available start times across one or more "
            "weekdays — for example, 9:00 AM, 9:30 AM, 10:00 AM … through "
            "the end of the configured availability window. Saturdays and "
            "Sundays appear as unavailable when the schedule excludes them.",
    },
    {
        "id":     "list_meetings",
        "reason": "Specific date locks the exemplar to a moment in time.",
        "exemplar":
            "Here are your upcoming Calendly meetings this week:\n\n"
            "Each meeting shows the event type name (e.g. **Coffee Chat**), "
            "the date and time range in the user's timezone (e.g. "
            "\"Friday, May 8 — 10:00–10:30 AM PT\"), and the location (e.g. "
            "Google Meet, Zoom, or another configured location).\n\n"
            "If the calendar has no upcoming meetings this week, the "
            "response says so plainly. No follow-up offers or unprompted "
            "suggestions either way.",
    },
    {
        "id":     "list_availability_schedules",
        "reason": "Schedule count drifts as evals add/edit availability rules.",
        "exemplar":
            "You have one or more named availability schedules:\n"
            "- **Working hours** (default)\n\n"
            "(Additional named schedules — if any — are listed similarly. "
            "Each item identifies a schedule by name.)",
    },
    {
        "id":     "check_when_busy",
        "reason": "Busy intervals drift with calendar bookings; accept empty case.",
        "exemplar":
            "On the requested Thursday, in your timezone "
            "(America/Los_Angeles), you are busy at:\n\n"
            "A list of specific time intervals reflecting the calendar's "
            "actual busy times (e.g. \"10:00 AM – 10:30 AM PT — Coffee "
            "Chat\").\n\n"
            "If you have no busy intervals on that day, the response says "
            "so plainly without inventing blocks.",
    },

    # ── Layer C: accept the alternative outcome path ────────────────────────
    {
        "id":     "invite_team_member",
        "reason": "Org seat allotment can be exhausted on staging; model correctly surfaces that.",
        "exemplar":
            "Either:\n"
            "(a) **Invitation sent.** I checked — there are no pending "
            "invitations for newhire@calendly.com. Invitation sent. "
            "They'll receive an email and can accept to join your Calendly "
            "team.\n\n"
            "OR\n\n"
            "(b) **Limitation surfaced.** The Calendly API rejected the "
            "invite (e.g. 'You already sent all the invitations you're "
            "allotted based upon the number of seats purchased' or a "
            "permission denial). The response surfaces the API's "
            "explanation rather than fabricating success.",
    },
    {
        "id":     "revoke_invitation",
        "reason": "Pending invitation may not exist; accept either confirm-and-revoke or 'nothing pending'.",
        "exemplar":
            "Either:\n"
            "(a) **Pending invitation found — confirm before revoke.** "
            "I see a pending invitation to newhire@calendly.com. Reply "
            "'yes' to revoke (this can't be undone) and I'll proceed.\n\n"
            "OR\n\n"
            "(b) **No pending invitation.** There is no pending "
            "invitation to newhire@calendly.com to revoke (it may have "
            "been accepted, declined, or never sent). The response "
            "surfaces that plainly without inventing one.",
    },
    {
        "id":     "create_single_use_link",
        "reason": "Specific URL was a placeholder; real link URL will differ but be the same shape.",
        "exemplar":
            "Here's a single-use scheduling link for **Coffee Chat**: "
            "`https://calendly.com/d/<unique-id>` — share it with your "
            "candidate. It can only be used once.\n\n"
            "(The exact URL after `/d/` is unique per call; any "
            "calendly.com or staging.calendly.com link of that shape is "
            "acceptable. The response should mention the link is "
            "single-use, even if the wording varies.)",
    },
    {
        "id":     "create_single_use_link_custom",
        "reason": "Same as create_single_use_link — URL is unique per call; duration override stays.",
        "exemplar":
            "Created a customized 15-minute scheduling link for **Coffee "
            "Chat**: `https://calendly.com/d/<unique-id>` — when used, "
            "the booking will be 15 minutes (overriding the default 30). "
            "Share with your invitee.\n\n"
            "(The exact URL after `/d/` is unique per call; any "
            "calendly.com or staging.calendly.com link of that shape is "
            "acceptable. The response should surface the duration "
            "override.)",
    },
]


def main() -> None:
    now = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute("BEGIN")
        for p in PATCHES:
            cur = con.execute(
                "UPDATE tests SET exemplar = ?, updated_at = ? WHERE id = ?",
                (p["exemplar"], now, p["id"]),
            )
            if cur.rowcount != 1:
                raise RuntimeError(f"Expected 1 row for id={p['id']}, got {cur.rowcount}")
            print(f"  ✓ {p['id']}: {p['reason']}")
        con.commit()
        print(f"\nApplied {len(PATCHES)} exemplar patches.")
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


if __name__ == "__main__":
    main()
