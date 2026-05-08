"""
Curate the harness for the Gemini-bound 16-case golden suite.

Three kinds of changes, all in one transaction:

1. Add `golden` tag to 5 existing evals so they join the canonical
   golden subset.
2. Insert 2 brand-new harness rows: `get_scheduling_link` and
   `reschedule_meeting` — both correspond to user-authored cases
   that don't map onto any existing eval.
3. Rewrite 9 exemplars to clean literal samples (no parentheticals,
   no `Either … OR …`, no rubric notes). With the deterministic
   fixture infrastructure being added in Phase 4a, exemplars no
   longer need to fudge for variance — they describe one specific
   acceptable answer under the fixture state.

Pattern mirrors `patch_exemplars_v4_a.py` / `patch_exemplars_v4_b.py`.
"""
from __future__ import annotations

import datetime
import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "mcp_testing.db"

# ── 5 evals to add `golden` tag to ────────────────────────────────────────
TAG_ADDS = [
    "create_single_use_link_custom",
    "invite_team_member",
    "list_routing_form_submissions",
    "list_availability_schedules",
    "mark_no_show",
]

# ── 2 brand-new harness rows ──────────────────────────────────────────────
NEW_ROWS = [
    {
        "id":     "get_scheduling_link",
        "prompt": "Get me the link for my Coffee Chat.",
        "criteria":
            "Returns the reusable event-type scheduling URL for the named "
            "event type. The host can paste the link into a message and the "
            "recipient sees the standard Calendly booking page. Single-use "
            "links and URLs unrelated to the named event type are not "
            "acceptable substitutes.",
        "exemplar":
            "Here's the scheduling link for **Coffee Chat**: "
            "https://calendly.com/mkimelblat/coffee-chat — share it with "
            "anyone you want to book a meeting with.",
        "must_call":     ["users-get_current_user", "event_types-list_event_types"],
        "must_not_call": [],
        "at_most_once":  [],
        "mutates":       0,
        "max_seconds":   None,
        "tags":          ["event-types", "golden"],
    },
    {
        "id":     "reschedule_meeting",
        "prompt": "Reschedule my next Coffee Chat.",
        "criteria":
            "Identifies the next Coffee Chat (event type, time, invitee), "
            "then returns the reschedule link inline so the host can use it "
            "or forward it. Offers cancel-and-rebook as an alternative path. "
            "Does not silently cancel and rebook without surfacing the link "
            "option.",
        "exemplar":
            "Your next **Coffee Chat** is with Michael Kimelblat "
            "(cash0902@gmail.com) on Friday at 9:00 AM PT.\n\n"
            "Here's the reschedule link you can share with Michael — they'll "
            "pick a new time and the booking will update automatically: "
            "https://calendly.com/reschedulings/abc123\n\n"
            "Want me to cancel-and-rebook directly instead? Just tell me the "
            "new time.",
        "must_call":     [
            "users-get_current_user",
            "meetings-list_events",
            "meetings-list_event_invitees",
        ],
        "must_not_call": [],
        "at_most_once":  [],
        "mutates":       0,
        "max_seconds":   None,
        "tags":          ["meetings", "golden"],
    },
]

# ── 9 exemplar rewrites to clean literal samples ──────────────────────────
EXEMPLAR_PATCHES = [
    {
        "id": "list_event_types",
        "reason": "Strip parenthetical rubric. Literal sample with names + durations per user spec.",
        "exemplar":
            "Here are your event types:\n"
            "- **Coffee Chat** — 30 minutes\n"
            "- **30 Minute Meeting** — 30 minutes\n"
            "- **A Managed Event** — 30 minutes\n"
            "- **test** — 30 minutes\n"
            "- **Z Managed Event** — 30 minutes",
    },
    {
        "id": "list_meetings",
        "reason": "Strip rubric prose. Literal sample of one upcoming meeting in user-spec format.",
        "exemplar":
            "Here are your upcoming Calendly meetings this week:\n"
            "- **Coffee Chat** with Michael Kimelblat — Friday at 9:00–9:30 AM PT (Google Meet)",
    },
    {
        "id": "check_when_busy",
        "reason": "Strip rubric. Literal sample with one busy block from fixture state.",
        "exemplar":
            "On Thursday, in your timezone (America/Los_Angeles), you are busy at:\n"
            "- 9:00 AM – 9:30 AM PT — **Coffee Chat** with Michael Kimelblat",
    },
    {
        "id": "find_event_type_available_times",
        "reason": "Strip rubric. Literal sample slot list from default availability + fixture state.",
        "exemplar":
            "Here are open slots for **Coffee Chat** next week (in your timezone, America/Los_Angeles):\n"
            "- Monday: 9:00 AM, 9:30 AM, 10:00 AM, 10:30 AM, 11:00 AM, 11:30 AM, 12:00 PM, …\n"
            "- Tuesday: 9:00 AM, 9:30 AM, 10:00 AM, 10:30 AM, 11:00 AM, 11:30 AM, 12:00 PM, …\n"
            "- Wednesday: 9:00 AM, 9:30 AM, 10:00 AM, 10:30 AM, 11:00 AM, 11:30 AM, 12:00 PM, …\n"
            "- Thursday: 9:30 AM, 10:00 AM, 10:30 AM, 11:00 AM, 11:30 AM, 12:00 PM, …\n"
            "- Friday: 9:00 AM, 9:30 AM, 10:00 AM, 10:30 AM, 11:00 AM, 11:30 AM, 12:00 PM, …\n\n"
            "Slots run through 5:00 PM each day. Saturday and Sunday are unavailable.",
    },
    {
        "id": "create_single_use_link",
        "reason": "Strip parenthetical URL/phrasing rubric. Literal one-time link sample.",
        "exemplar":
            "Here's a single-use scheduling link for **Coffee Chat**: "
            "https://calendly.com/d/abc1-def2-ghi3 — share it with your candidate. "
            "Once it's used, it expires.",
    },
    {
        "id": "create_single_use_link_custom",
        "reason": "Strip parenthetical rubric. Literal 15-min override sample.",
        "exemplar":
            "Created a 15-minute single-use scheduling link for **Coffee Chat**: "
            "https://calendly.com/d/abc1-def2-ghi3 — when used, the booking will be "
            "15 minutes (overriding the default 30). Share with your invitee.",
    },
    {
        "id": "invite_team_member",
        "reason": "Drop dual-outcome Either/OR. Fixtures guarantee success path; literal sample.",
        "exemplar":
            "Invitation sent to **newhire@calendly.com**. They'll receive an email "
            "and can accept it to join your Calendly team.",
    },
    {
        "id": "list_routing_form_submissions",
        "reason": "Was authored for the empty-submissions case. Fixtures will establish at least one submission; literal sample reflects that.",
        "exemplar":
            "Here are submissions to your **test** routing form:\n"
            "1. Submitted 2026-05-07 — name: Test User, email: test@example.com",
    },
    {
        "id": "list_availability_schedules",
        "reason": "Strip parenthetical rubric. Literal sample of the named schedule.",
        "exemplar":
            "Here are your availability schedules:\n"
            "- **Working hours** (default)",
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

        # ── 1. Add `golden` tag to 5 existing rows ────────────────────────
        for tid in TAG_ADDS:
            row = con.execute("SELECT tags FROM tests WHERE id = ?", (tid,)).fetchone()
            if row is None:
                raise RuntimeError(f"No row for id={tid}")
            tags = json.loads(row[0]) if row[0] else []
            if "golden" in tags:
                print(f"  · {tid}: already golden, skipping")
                continue
            new_tags = tags + ["golden"]
            cur = con.execute(
                "UPDATE tests SET tags = ?, updated_at = ? WHERE id = ?",
                (json.dumps(new_tags), now, tid),
            )
            if cur.rowcount != 1:
                raise RuntimeError(f"Expected 1 row for id={tid}, got {cur.rowcount}")
            print(f"  ✓ tag      {tid}: +golden → {new_tags}")

        # ── 2. Insert 2 new rows ───────────────────────────────────────────
        max_pos = con.execute("SELECT COALESCE(MAX(position), -1) FROM tests").fetchone()[0]
        for offset, row in enumerate(NEW_ROWS, start=1):
            cur = con.execute("SELECT 1 FROM tests WHERE id = ?", (row["id"],))
            if cur.fetchone() is not None:
                print(f"  · {row['id']}: already exists, skipping insert")
                continue
            con.execute(
                """INSERT INTO tests (
                    id, prompt, criteria, must_call, must_not_call, at_most_once,
                    mutates, position, created_at, updated_at, max_seconds,
                    tags, exemplar
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    row["id"], row["prompt"], row["criteria"],
                    json.dumps(row["must_call"]),
                    json.dumps(row["must_not_call"]),
                    json.dumps(row["at_most_once"]),
                    int(row["mutates"]),
                    max_pos + offset,
                    now, now,
                    row["max_seconds"],
                    json.dumps(row["tags"]),
                    row["exemplar"],
                ),
            )
            print(f"  ✓ insert   {row['id']}: tags={row['tags']}, must_call={row['must_call']}")

        # ── 3. Rewrite 9 exemplars to clean literal samples ──────────────
        for p in EXEMPLAR_PATCHES:
            cur = con.execute(
                "UPDATE tests SET exemplar = ?, updated_at = ? WHERE id = ?",
                (p["exemplar"], now, p["id"]),
            )
            if cur.rowcount != 1:
                raise RuntimeError(f"Expected 1 row for id={p['id']}, got {cur.rowcount}")
            print(f"  ✓ exemplar {p['id']}: {p['reason']}")

        con.commit()
        print(
            f"\nApplied {len(TAG_ADDS)} tag-add(s), {len(NEW_ROWS)} insert(s), "
            f"{len(EXEMPLAR_PATCHES)} exemplar rewrite(s)."
        )
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


if __name__ == "__main__":
    main()
