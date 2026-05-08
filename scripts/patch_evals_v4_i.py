"""
Patch I — two more false-fabrication / over-prescriptive expect fixes.

1. read_team_member_profile: judge complained the model used
   "organization membership tools instead of a team-members listing
   tool". There IS no separate team-members tool — Calendly's MCP
   exposes team membership through `organizations-list_organization_memberships`,
   which returns the full user record inline (role, user.name,
   user.email, user.timezone, user.scheduling_url, user.slug). A
   response derived from that tool is NOT fabrication. Update the
   expect to tell the judge.

2. read_event_type_details: judge required "describing where or how
   the meeting occurs" but Mike's Coffee Chat has no location
   configured on the event type — the API returns an empty `locations`
   array. The model accurately said "no locations configured". The
   expect needs to accept that case as valid, since location is an
   optional field on event types.
"""
from __future__ import annotations

import datetime
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "mcp_testing.db"

PATCHES = [
    {
        "id":     "read_team_member_profile",
        "expect": (
            "The response describes a specific team member's profile (name, "
            "email, scheduling URL, role, timezone, etc.) based on the second "
            "entry returned when listing team members. NOTE for the judge: "
            "Calendly's MCP exposes team-member listing exclusively through "
            "`organizations-list_organization_memberships`. There is no "
            "separate 'team members' tool. The list response includes a full "
            "embedded `user` object per membership (with `name`, `email`, "
            "`timezone`, `scheduling_url`, `slug`, etc.) plus the membership's "
            "`role` and timestamps. A response that describes the second "
            "member after calling only that tool is NOT fabrication — those "
            "values come from the inline user object in the API response. "
            "Penalize fabrication only if the response invents a member who "
            "doesn't appear in the API list, or invents fields not returned."
        ),
    },
    {
        "id":     "read_event_type_details",
        "expect": (
            "The response describes the Coffee Chat event type's "
            "configuration, including its name and duration. If the event "
            "type has a location/meeting kind configured, the response "
            "should include it; if no location is configured (the "
            "`locations` array on the event type is empty), it's acceptable "
            "for the response to say so plainly — the model should reflect "
            "actual API state, not invent a location. Additional fields "
            "(scheduling URL, color, slug, status, etc.) are fine. The "
            "tool-trace assertion (must_call: event_types-list_event_types) "
            "is what guarantees the values come from the API."
        ),
    },
]


def main() -> None:
    now = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute("BEGIN")
        for p in PATCHES:
            cur = con.execute(
                "UPDATE tests SET expect = ?, updated_at = ? WHERE id = ?",
                (p["expect"], now, p["id"]),
            )
            if cur.rowcount != 1:
                raise RuntimeError(f"{p['id']}: expected 1 row, got {cur.rowcount}")
            print(f"  ✓ {p['id']}")
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


if __name__ == "__main__":
    main()
