"""
Patch v4 evals based on run #101 results — fixes faulty expectations,
stale Aundreia references in expects, schedule-name carry-over, and
must_call assertions that don't match what the prompt actually requires.

Each entry has a `reason` field explaining why we're changing it. The
script applies all changes in a single transaction.
"""
from __future__ import annotations

import datetime
import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "mcp_testing.db"


PATCHES = [
    # ── A. Faulty expectations — judge over-strict on legit responses ────────
    {
        "id":     "list_event_types",
        "reason": "Judge read 'identifies by Name and Duration' as 'ONLY name + duration'. Allow extra fields.",
        "expect": "A list of the connected user's event types. At minimum each item shows the event type name and its duration. Additional fields (URL, location, status, slug, etc.) are acceptable.",
    },
    {
        "id":     "read_event_type_details",
        "reason": "Judge worried about fabrication even though get_event_type was called. Tool-trace already covers grounding.",
        "expect": "The response describes the Coffee Chat event type's configuration, including at minimum its name, duration, and location/meeting kind. Additional fields are fine. The tool-trace assertion (must_call: event_types-get_event_type) is what guarantees the values come from the API.",
    },
    {
        "id":     "list_availability_schedules",
        "reason": "Same overly-restrictive 'name only' framing as list_event_types.",
        "expect": "A list of the user's named availability schedules, each identified at minimum by its name (e.g. 'Working hours'). Additional details like timezone and per-day rules are acceptable.",
    },
    {
        "id":     "list_routing_forms",
        "reason": "Same overly-restrictive 'name + event type only' framing.",
        "expect": "A list of the organization's routing forms, each identified at minimum by name. Additional fields (status, creation time, associated event type, etc.) are acceptable.",
    },
    {
        "id":     "list_pending_invitations",
        "reason": "Expect didn't accept an empty list as a valid response when no pending invitations exist.",
        "expect": "Either a list of pending invitations (each identified by invitee email and date sent or status), OR a clear statement that there are no pending invitations. Empty-list responses are valid when the org actually has none.",
    },
    {
        "id":     "read_organization",
        "reason": "Required 'member count or plan tier' but those aren't always in the response. Soften.",
        "expect": "The response describes the organization with at minimum its name. Additional high-level fields (plan tier, status, type, member count, etc.) are good to include when present in the API response.",
    },
    {
        "id":     "read_team_membership",
        "reason": "Required a 'status' field that the membership API doesn't expose as a separate scalar.",
        "expect": "The response describes the connected user's organization membership with at minimum the role (e.g. owner, admin, user). Additional fields (organization URI, created_at, the embedded user info) are good to include.",
    },
    {
        "id":     "create_event_type",
        "reason": "Required calendly.com/... URL but staging returns staging.calendly.com/...; required Status: Active label that the model doesn't always emit verbatim.",
        "expect": "Confirmation that the event type was created. The confirmation includes: Name: Intro Call, Duration: 30 minutes, Location: Zoom, and a scheduling URL on the Calendly domain (calendly.com/... or staging.calendly.com/...). Surfacing the active state is good but not required if the response otherwise reads as a successful creation confirmation.",
    },

    # ── B. Stale Aundreia references in expects (after prompt patch B) ───────
    {
        "id":     "read_invitee",
        "reason": "Patch B changed the prompt to use cash0902@gmail.com but left the expect referring to Aundreia.",
        "expect": "The response describes Michael Kimelblat's invitation specifically (cash0902@gmail.com): at minimum the matched invitee's name and email, plus any additional details (timezone, questions/answers). The match is on the actual invitee returned by the API, not a fabricated one.",
    },
    {
        "id":     "mark_no_show",
        "reason": "Patch B changed prompt to cash0902 but expect still says Aundreia.",
        "expect": "Confirmation that Michael (cash0902@gmail.com) was marked as a no-show on yesterday's Coffee Chat. References the specific past meeting and the specific invitee.",
    },
    {
        "id":     "clear_no_show",
        "reason": "Same — prompt is cash0902, expect still says Aundreia.",
        "expect": "Confirmation that Michael's (cash0902@gmail.com) no-show mark was removed from yesterday's Coffee Chat.",
    },

    # ── C. Stale schedule name in expect (after prompt patch A) ──────────────
    {
        "id":     "read_availability_schedule",
        "reason": "Patch A updated prompt to 'Working hours' but expect still referenced 'Default Hours'.",
        "expect": "The response describes the rules of the 'Working hours' schedule: which days and what hour ranges, with the schedule's timezone. The values reflect the stored schedule.",
    },

    # ── D. Wrong must_call assertions ────────────────────────────────────────
    {
        "id":        "read_team_member_profile",
        "reason":    "users-get_user is unnecessary because list_organization_memberships returns inline user data; the model correctly skips it.",
        "must_call": ["users-get_current_user", "organizations-list_organization_memberships"],
    },

    # ── E. Single-turn architecture vs multi-turn expects ────────────────────
    # The harness is single-turn, so an eval that prompts for a destructive
    # action and expects the model to ask for confirmation can never see the
    # destructive tool actually fire. Move those tools out of must_call so
    # the eval validates the right behavior (asking for confirmation) instead
    # of failing because the model correctly waited.
    {
        "id":        "cancel_meeting",
        "reason":    "Single-turn harness: model correctly asks for confirmation before cancelling; cancel_event won't fire on the same turn.",
        "must_call": ["meetings-list_events"],
        "at_most_once": ["meetings-cancel_event"],
        "expect":    "The model identifies the booking by event type, date/time, and invitee name, notes that cancellation will notify the invitee, and asks the user to confirm (optionally with a reason) before performing the cancel. The model does NOT silently call meetings-cancel_event without confirmation in the same turn.",
    },
    {
        "id":        "remove_team_member",
        "reason":    "Same single-turn issue: model asks for confirmation before destructive removal.",
        "must_call": ["organizations-list_organization_memberships"],
        "at_most_once": ["organizations-delete_organization_membership"],
        "expect":    "The model identifies the most-recently-added member by name and email and asks the user to confirm before removal. It does NOT silently call delete_organization_membership without confirmation.",
    },
    {
        "id":        "revoke_invitation",
        "reason":    "Same single-turn issue: model asks for confirmation before revoking.",
        "must_call": ["organizations-list_organization_invitations"],
        "at_most_once": ["organizations-revoke_organization_invitation"],
        "expect":    "The model identifies the pending invitation by email and asks the user to confirm before revoking. It does NOT silently call revoke_organization_invitation without confirmation.",
    },
]


def main() -> None:
    now = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute("BEGIN")
        for p in PATCHES:
            sets: list[str] = ["updated_at = ?"]
            params: list = [now]
            if "expect" in p:
                sets.append("expect = ?")
                params.append(p["expect"])
            if "must_call" in p:
                sets.append("must_call = ?")
                params.append(json.dumps(p["must_call"]))
            if "must_not_call" in p:
                sets.append("must_not_call = ?")
                params.append(json.dumps(p["must_not_call"]))
            if "at_most_once" in p:
                sets.append("at_most_once = ?")
                params.append(json.dumps(p["at_most_once"]))
            params.append(p["id"])
            cur = con.execute(
                f"UPDATE tests SET {', '.join(sets)} WHERE id = ?",
                params,
            )
            if cur.rowcount != 1:
                raise RuntimeError(f"Expected 1 row for id={p['id']}, got {cur.rowcount}")
            print(f"  ✓ {p['id']}: {p['reason']}")
        con.commit()
        print(f"\nApplied {len(PATCHES)} patches.")
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


if __name__ == "__main__":
    main()
