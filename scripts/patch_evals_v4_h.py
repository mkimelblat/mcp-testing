"""
Patch H — two fixes informed by run-history analysis.

1. list_locations expect: clarify for the judge that
   `locations-list_user_meeting_locations` returns 10 well-known
   location kinds with `connected` flags. Run-flagged failure where
   the judge called fabrication on a model response that exactly
   matched the API output, because the judge didn't know what the API
   returns. Same pattern as the run #104 read_routing_form fix.

2. read_availability_schedule must_call: drop
   `availability-get_user_availability_schedule`. The list endpoint
   returns `rules, timezone, default, name` inline (probed and
   verified), so get is redundant. Pattern continuation of the user's
   recent updates dropping get on read_event_type_details, read_meeting,
   read_invitee, read_routing_form, read_routing_form_submission.

NOT included: read_no_show. The list_event_invitees response includes
a `no_show` field, but its structure (vs the get_invitee_no_show
detail) couldn't be verified without a real no-show record on staging.
Address separately once the past-Coffee-Chat fixture exists.
"""
from __future__ import annotations

import datetime
import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "mcp_testing.db"

NEW_LIST_LOCATIONS_EXPECT = (
    "A list of the user's available meeting location kinds with their "
    "connection status. NOTE for the judge: the "
    "`locations-list_user_meeting_locations` endpoint returns a "
    "fixed-shape collection of 10 location kinds (`physical`, "
    "`ask_invitee`, `custom`, `outbound_call`, `inbound_call`, "
    "`zoom_conference`, `gotomeeting_conference`, `google_conference`, "
    "`microsoft_teams_conference`, `webex_conference`) with a "
    "`connected` flag per item (null for non-integration kinds, "
    "true/false for the conference integrations). A response that "
    "names these specific kinds is NOT fabrication — they are exactly "
    "what the API returns. Adding human-readable descriptions of each "
    "kind (e.g. 'physical = in-person meetings') is acceptable. "
    "Penalize fabrication only if the response invents kinds not in "
    "the API list, or misreports the `connected` flag for a kind."
)

NEW_READ_AVAILABILITY_SCHEDULE_MUST_CALL = ["availability-list_user_availability_schedules"]


def main() -> None:
    now = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute("BEGIN")
        cur = con.execute(
            "UPDATE tests SET expect = ?, updated_at = ? WHERE id = 'list_locations'",
            (NEW_LIST_LOCATIONS_EXPECT, now),
        )
        if cur.rowcount != 1:
            raise RuntimeError(f"list_locations: expected 1 row, got {cur.rowcount}")
        print("  ✓ list_locations expect updated (judge informed of API shape)")

        cur = con.execute(
            "UPDATE tests SET must_call = ?, updated_at = ? WHERE id = 'read_availability_schedule'",
            (json.dumps(NEW_READ_AVAILABILITY_SCHEDULE_MUST_CALL), now),
        )
        if cur.rowcount != 1:
            raise RuntimeError(f"read_availability_schedule: expected 1 row, got {cur.rowcount}")
        print("  ✓ read_availability_schedule must_call: dropped get_user_availability_schedule")

        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


if __name__ == "__main__":
    main()
