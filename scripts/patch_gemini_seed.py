"""
Seed 23 `gemini`-tagged eval rows in the harness, in the exact
execution order Gemini specified on their Google Sheet.

The 23 rows form a self-contained chain:
- #1-6 (Event Types): list → create Coffee Chat → get link → update
  to 45 min → single-use links. Coffee Chat is created in #2, so no
  pre-seed needed.
- #7-9 (Availability): check busy, list schedules, remove Friday
  availability from Coffee Chat (Coffee Chat now exists per #2).
- #10-15 (Meetings): book Gemini User 4 → list → cancel → mark
  no-show → read no-show → clear no-show.
- #16 (Locations): list location kinds.
- #17-19 (Org Admin invite/revoke chain): invite +user5 → list
  pending → revoke +user5.
- #20-22 (Org Admin reads): org details, list members, get user role.
- #23 (Routing): list submissions to first form.

All rows tagged ["<category>", "gemini"]; positions assigned
sequentially starting at MAX(position)+1 so they sort in execution
order in the UI.

Exemplars are first-draft. Will iterate after the first end-to-end
run against the connected account.
"""
from __future__ import annotations

import datetime
import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "mcp_testing.db"

ROWS = [
    {
        "id": "gemini_list_event_types",
        "prompt": "List my event types",
        "criteria":
            "Returns a list of the host's active event types. Each item "
            "identifies an event type by name; durations are acceptable "
            "but not required.",
        "exemplar":
            "Here are your event types:\n"
            "- **15 Minute Meeting** — 15 minutes\n"
            "- **30 Minute Meeting** — 30 minutes\n"
            "- **60 Minute Meeting** — 60 minutes",
        "must_call": ["event_types-list_event_types"],
        "tags": ["event-types", "gemini"],
        "mutates": 0,
    },
    {
        "id": "gemini_create_event_type",
        "prompt": 'Create a new 30-minute event type called Coffee Chat to meet in person at "Cafe"',
        "criteria":
            "Confirms creation of a new event type named 'Coffee Chat', "
            "30 minutes long, with an in-person location set to 'Cafe'. "
            "Returns the scheduling URL for the new event type.",
        "exemplar":
            "Done — I created the **Coffee Chat** event type.\n"
            "- Name: Coffee Chat\n"
            "- Duration: 30 minutes\n"
            "- Location: In-person at Cafe\n"
            "- Status: Active\n"
            "- Scheduling link: https://calendly.com/calendlymcptest1/coffee-chat",
        "must_call": ["event_types-create_event_type"],
        "tags": ["event-types", "gemini"],
        "mutates": 1,
    },
    {
        "id": "gemini_get_scheduling_link",
        "prompt": "Get me the link for my Coffee Chat",
        "criteria":
            "Returns the reusable scheduling URL for the Coffee Chat "
            "event type. A direct, single link only.",
        "exemplar":
            "Here's the scheduling link for **Coffee Chat**: "
            "https://calendly.com/calendlymcptest1/coffee-chat — share "
            "it with anyone you want to book a meeting with.",
        "must_call": ["event_types-list_event_types"],
        "tags": ["event-types", "gemini"],
        "mutates": 0,
    },
    {
        "id": "gemini_update_event_type",
        "prompt": "Update my Coffee Chat event type to 45 minutes",
        "criteria":
            "Confirms the Coffee Chat duration was updated to 45 "
            "minutes; other settings (location, availability) unchanged.",
        "exemplar":
            "Updated **Coffee Chat** to 45 minutes. All other settings "
            "(location: In-person at Cafe, availability rules) remain "
            "unchanged.",
        "must_call": [
            "event_types-list_event_types",
            "event_types-get_event_type",
            "event_types-update_event_type",
        ],
        "tags": ["event-types", "gemini"],
        "mutates": 1,
    },
    {
        "id": "gemini_create_single_use_link",
        "prompt": "Generate a one-time scheduling link I can send a candidate for my Coffee Chat",
        "criteria":
            "Creates a single-use scheduling link for Coffee Chat and "
            "returns the link URL.",
        "exemplar":
            "Here's a single-use scheduling link for **Coffee Chat**: "
            "https://calendly.com/d/abc1-def2-ghi3 — share it with your "
            "candidate. Once it's used, it expires.",
        "must_call": [
            "event_types-list_event_types",
            "scheduling_links-create_single_use_scheduling_link",
        ],
        "tags": ["event-types", "gemini"],
        "mutates": 1,
    },
    {
        "id": "gemini_create_single_use_link_custom",
        "prompt": "Create a single-use link for my Coffee Chat with a 15-minute duration",
        "criteria":
            "Creates a single-use scheduling link for Coffee Chat that "
            "overrides the default duration to 15 minutes.",
        "exemplar":
            "Created a 15-minute single-use scheduling link for "
            "**Coffee Chat**: https://calendly.com/d/abc1-def2-ghi3 — "
            "when used, the booking will be 15 minutes (overriding the "
            "default 45). Share with your invitee.",
        "must_call": [
            "event_types-list_event_types",
            "shares-create_share",
        ],
        "tags": ["event-types", "gemini"],
        "mutates": 1,
    },
    {
        "id": "gemini_check_when_busy",
        "prompt": "Am I free next Thursday afternoon for a Coffee Chat?",
        "criteria":
            "Reports whether the host is free next Thursday afternoon in "
            "their timezone. Either a yes/no with conflicts noted, or a "
            "list of busy blocks.",
        "exemplar":
            "Next Thursday afternoon (in your timezone, America/Los_Angeles), "
            "you have no conflicts on your Calendly calendar. You're free "
            "from 12:00 PM through 5:00 PM.",
        "must_call": [
            "users-get_current_user",
            "availability-list_user_busy_times",
        ],
        "tags": ["availability", "gemini"],
        "mutates": 0,
    },
    {
        "id": "gemini_list_availability_schedules",
        "prompt": "Show me my availability schedules",
        "criteria":
            "Lists the user's named availability schedules with the "
            "default flagged.",
        "exemplar":
            "Here are your availability schedules:\n"
            "- **Working hours** (default)",
        "must_call": ["availability-list_user_availability_schedules"],
        "tags": ["availability", "gemini"],
        "mutates": 0,
    },
    {
        "id": "gemini_remove_friday_availability",
        "prompt": "Remove Fridays from my Coffee Chat event type's availability",
        "criteria":
            "Updates the Coffee Chat availability schedule to remove "
            "Friday availability; preserves other weekdays' existing "
            "rules.",
        "exemplar":
            "Removed Fridays from **Coffee Chat** availability. Mon, "
            "Tue, Wed, Thu keep their existing 9:00 AM – 5:00 PM rules; "
            "Saturday and Sunday remain unavailable.",
        "must_call": [
            "event_types-list_event_types",
            "event_types-list_event_type_availability_schedule",
            "event_types-update_event_type_availability_schedule",
        ],
        "tags": ["availability", "gemini"],
        "mutates": 1,
    },
    {
        "id": "gemini_book_meeting",
        "prompt": "Book a Coffee Chat with Gemini User 4 (calendlymcptest1+user4@gmail.com) for my first available slot next week",
        "criteria":
            "Books a Coffee Chat with Gemini User 4 at the first "
            "available slot in the next week. Returns a confirmation "
            "with event type, invitee, date/time, and location.",
        "exemplar":
            "Booked your Coffee Chat:\n"
            "- Event type: **Coffee Chat**\n"
            "- Invitee: **Gemini User 4** (calendlymcptest1+user4@gmail.com)\n"
            "- Date and time: Monday at 9:00 AM PT\n"
            "- Location: In-person at Cafe",
        "must_call": [
            "event_types-list_event_types",
            "event_types-list_event_type_available_times",
            "meetings-create_invitee",
        ],
        "tags": ["meetings", "gemini"],
        "mutates": 1,
    },
    {
        "id": "gemini_list_meetings",
        "prompt": "What Calendly meetings do I have next week?",
        "criteria":
            "Lists upcoming Calendly meetings in the next week. Each "
            "shows event type, date/time in host's timezone, and "
            "invitee name.",
        "exemplar":
            "Here are your Calendly meetings next week:\n"
            "- **Coffee Chat** with Gemini User 4 — Monday at 9:00–9:45 AM PT (In-person at Cafe)",
        "must_call": ["meetings-list_events"],
        "tags": ["meetings", "gemini"],
        "mutates": 0,
    },
    {
        "id": "gemini_cancel_meeting",
        "prompt": "Cancel my next Coffee Chat",
        "criteria":
            "Identifies the next Coffee Chat (event type, time, invitee) "
            "and pauses for confirmation before cancelling. Does NOT "
            "silently cancel without confirmation.",
        "exemplar":
            "Just to confirm — you want me to cancel your **Coffee Chat** "
            "with Gemini User 4 (calendlymcptest1+user4@gmail.com) on "
            "Monday at 9:00 AM PT? Cancellation will notify them. Reply "
            "'yes' (and optionally a reason) to confirm and I'll proceed.",
        "must_call": ["meetings-list_events"],
        "tags": ["meetings", "gemini"],
        "mutates": 0,  # single-turn — pauses for confirm
    },
    {
        "id": "gemini_mark_no_show",
        "prompt": "Mark Gemini User 4 as a no-show for the 9am Coffee Chat on 5/15/26",
        "criteria":
            "Finds the 9am Coffee Chat on 5/15/26 with Gemini User 4 as "
            "invitee and marks it as a no-show.",
        "exemplar":
            "Marked **Gemini User 4** (calendlymcptest1+user4@gmail.com) "
            "as a no-show for the 9:00 AM Coffee Chat on Friday, May 15, "
            "2026. The no-show is now on record for that meeting.",
        "must_call": [
            "meetings-list_events",
            "meetings-list_event_invitees",
            "meetings-create_invitee_no_show",
        ],
        "tags": ["meetings", "gemini"],
        "mutates": 1,
    },
    {
        "id": "gemini_read_no_show",
        "prompt": "Did Gemini User 4 no-show for any of his past meetings?",
        "criteria":
            "Reports whether Gemini User 4 has any past meetings marked "
            "as a no-show, based on actual Calendly records.",
        "exemplar":
            "Yes — Gemini User 4 (calendlymcptest1+user4@gmail.com) was "
            "marked as a no-show for the 9:00 AM Coffee Chat on Friday, "
            "May 15, 2026.",
        "must_call": [
            "meetings-list_events",
            "meetings-list_event_invitees",
        ],
        "tags": ["meetings", "gemini"],
        "mutates": 0,
    },
    {
        "id": "gemini_clear_no_show",
        "prompt": "Undo the no-show for Gemini User 4",
        "criteria":
            "Removes the no-show flag from Gemini User 4's past meeting.",
        "exemplar":
            "Cleared the no-show for **Gemini User 4** "
            "(calendlymcptest1+user4@gmail.com) on the May 15, 2026 "
            "Coffee Chat. The no-show record has been removed.",
        "must_call": [
            "meetings-list_events",
            "meetings-list_event_invitees",
            "meetings-delete_invitee_no_show",
        ],
        "tags": ["meetings", "gemini"],
        "mutates": 1,
    },
    {
        "id": "gemini_list_locations",
        "prompt": "What locations are configured for my calendly account",
        "criteria":
            "Lists the supported meeting location kinds available on the "
            "account, with connection status for the conference "
            "integrations (Zoom, Google Meet, etc.).",
        "exemplar":
            "Here are the meeting location kinds available on your account:\n\n"
            "Available without integration:\n"
            "- Physical location\n"
            "- Ask invitee\n"
            "- Custom location\n"
            "- Outbound call\n"
            "- Inbound call\n\n"
            "Conference integrations (not currently connected):\n"
            "- Zoom\n"
            "- GoToMeeting\n"
            "- Google Meet\n"
            "- Microsoft Teams\n"
            "- Webex",
        "must_call": ["locations-list_user_meeting_locations"],
        "tags": ["availability", "gemini"],
        "mutates": 0,
    },
    {
        "id": "gemini_invite_team_member",
        "prompt": "Invite calendlymcptest1+user5@gmail.com to my Calendly team",
        "criteria":
            "Sends a Calendly team invitation to the named email.",
        "exemplar":
            "Invitation sent to **calendlymcptest1+user5@gmail.com**. "
            "They'll receive an email and can accept it to join your "
            "Calendly team.",
        "must_call": [
            "organizations-list_organization_invitations",
            "organizations-create_organization_invitation",
        ],
        "tags": ["organizations", "gemini"],
        "mutates": 1,
    },
    {
        "id": "gemini_list_pending_invitations",
        "prompt": "Which calendly user invitations are pending",
        "criteria":
            "Lists pending team invitations on the user's Calendly "
            "organization, with at least the email per invitation.",
        "exemplar":
            "You have 1 pending invitation:\n"
            "- **calendlymcptest1+user5@gmail.com** — sent today",
        "must_call": ["organizations-list_organization_invitations"],
        "tags": ["organizations", "gemini"],
        "mutates": 0,
    },
    {
        "id": "gemini_revoke_invitation",
        "prompt": "Revoke calendlymcptest1+user5@gmail.com's Calendly invitation",
        "criteria":
            "Revokes the pending invitation for the named email; the "
            "recipient can no longer accept it.",
        "exemplar":
            "Revoked the pending invitation for "
            "**calendlymcptest1+user5@gmail.com**. They will no longer "
            "be able to accept it to join your team.",
        "must_call": [
            "organizations-list_organization_invitations",
            "organizations-revoke_organization_invitation",
        ],
        "tags": ["organizations", "gemini"],
        "mutates": 1,
    },
    {
        "id": "gemini_read_organization",
        "prompt": "Tell me about my Calendly organization",
        "criteria":
            "Returns key details about the user's Calendly organization "
            "(name and at minimum one identifying detail; additional "
            "high-level fields like plan, member count are acceptable).",
        "exemplar":
            "Your Calendly organization:\n"
            "- Name: Calendly MCP Test Team\n"
            "- Plan: Teams\n"
            "- Members: 5 active, 0 pending",
        "must_call": ["organizations-get_organization"],
        "tags": ["organizations", "gemini"],
        "mutates": 0,
    },
    {
        "id": "gemini_list_team_members",
        "prompt": "List all of the users in my Calendly organization",
        "criteria":
            "Lists all active members of the user's Calendly "
            "organization. Each item identifies a member by email "
            "and/or name; roles are good to include when present.",
        "exemplar":
            "Here are the users in your Calendly organization:\n"
            "- calendlymcptest1@gmail.com (Owner)\n"
            "- calendlymcptest1+user1@gmail.com (Member)\n"
            "- calendlymcptest1+user2@gmail.com (Member)\n"
            "- calendlymcptest1+user3@gmail.com (Member)\n"
            "- calendlymcptest1+user4@gmail.com (Member)",
        "must_call": ["organizations-list_organization_memberships"],
        "tags": ["organizations", "gemini"],
        "mutates": 0,
    },
    {
        "id": "gemini_read_user",
        "prompt": "What is user calendlymcptest1+user1@gmail.com's role?",
        "criteria":
            "Returns the role of the named user in the connected "
            "organization (Owner, Admin, Member, etc.).",
        "exemplar":
            "**calendlymcptest1+user1@gmail.com** is a **Member** of "
            "your Calendly organization.",
        "must_call": [
            "organizations-list_organization_memberships",
            "users-get_user",
        ],
        "tags": ["organizations", "gemini"],
        "mutates": 0,
    },
    {
        "id": "gemini_list_routing_form_submissions",
        "prompt": "Show me submissions to my first routing form",
        "criteria":
            "Lists submissions to the user's first routing form, or "
            "states plainly that the form has no submissions yet (the "
            "user spec accepts either as a valid success path).",
        "exemplar":
            "Your first routing form has no submissions yet. Once "
            "invitees submit the form, their responses will appear here.",
        "must_call": [
            "routing_forms-list_routing_forms",
            "routing_forms-list_routing_form_submissions",
        ],
        "tags": ["routing-forms", "gemini"],
        "mutates": 0,
    },
]


def main() -> None:
    assert len(ROWS) == 23, f"Expected 23 rows, got {len(ROWS)}"
    now = (
        datetime.datetime.now(datetime.UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute("BEGIN")
        max_pos = con.execute("SELECT COALESCE(MAX(position), -1) FROM tests").fetchone()[0]
        for i, row in enumerate(ROWS, start=1):
            existing = con.execute("SELECT 1 FROM tests WHERE id = ?", (row["id"],)).fetchone()
            if existing:
                print(f"  · {row['id']}: already exists, skipping")
                continue
            con.execute(
                """INSERT INTO tests (
                    id, prompt, criteria, must_call, must_not_call, at_most_once,
                    mutates, position, created_at, updated_at, max_seconds,
                    tags, exemplar
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    row["id"],
                    row["prompt"],
                    row["criteria"],
                    json.dumps(row["must_call"]),
                    json.dumps([]),
                    json.dumps([]),
                    int(row["mutates"]),
                    max_pos + i,
                    now, now,
                    None,
                    json.dumps(row["tags"]),
                    row["exemplar"],
                ),
            )
            mark = "WRITE" if row["mutates"] else "READ "
            print(f"  ✓ #{i:>2}  [{mark}]  {row['id']}: {row['prompt'][:60]}")
        con.commit()
        print(f"\nInserted {len(ROWS)} gemini-tagged rows in execution order.")
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


if __name__ == "__main__":
    main()
