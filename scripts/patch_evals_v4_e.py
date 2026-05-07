"""
Patch E — second-pass refinements after re-auditing run #101 with
patches A-D considered. 11 changes across 9 evals.

Categories:

  1. Bug carry-over from patch B: share_calendar prompt was missed and
     still names Aundreia.

  2. Empty-state acceptance: list_meetings, read_meeting, list_invitees
     should pass when the calendar legitimately has no matching data,
     not just when it has data.

  3. Permission-denied / draft-form acceptance: invite_team_member,
     list_routing_form_submissions, read_routing_form_submission,
     read_routing_form — the model correctly surfaces 403s and empty
     states from staging, but the expects assumed success.

  4. Refusal must_not_call too strict: refuse_crm_lookup and
     refuse_summarize_meeting both checked the calendar (legitimate
     first step for "who's on my calendar / summarize my last
     meeting") before refusing. Judge passed; tool-trace failed.
     Drop meetings-* from must_not_call; keep event_types-* forbidden.

Real model failures (refuse_email_invitees drafting an email,
share_calendar hallucinating tool capabilities, no_follow_up_offers /
list_meetings adding follow-ups) are intentionally NOT patched.
"""
from __future__ import annotations

import datetime
import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "mcp_testing.db"


PATCHES = [
    # 1. share_calendar prompt: Aundreia → Michael (carryover from patch B)
    {
        "id":     "share_calendar",
        "reason": "Patch B missed this prompt; still spamming Aundreia on every run.",
        "prompt": "Create a customized scheduling link for my Coffee Chat event type, pre-filled with name 'Michael Kimelblat' and email 'cash0902@gmail.com' so he can pick a time.",
    },

    # 2. Empty-state acceptance
    {
        "id":     "list_meetings",
        "reason": "Allow empty-calendar responses; keep submission-doc no-follow-up rule.",
        "expect": "Either a list of upcoming Calendly meetings (each showing event type name, date and time range in the user's timezone, location), OR a clear statement that there are no upcoming Calendly meetings. Scope is Calendly-booked meetings only — full-calendar events booked outside Calendly are out of scope. No follow-up offers or unprompted suggestions in either case.",
    },
    {
        "id":     "read_meeting",
        "reason": "Allow 'no upcoming Coffee Chat' responses for fixture-empty cases.",
        "expect": "Either the response describes the next Coffee Chat meeting's specifics (start time, location, event type name reflecting actual API data), OR the response clearly reports that no upcoming Coffee Chat is currently booked. No fabricated values either way.",
    },
    {
        "id":     "list_invitees",
        "reason": "Allow 'no upcoming Coffee Chat' responses for fixture-empty cases.",
        "expect": "Either a list of invitees registered for the next Coffee Chat (each identified by name and/or email, reflecting actual API data), OR a clear statement that no upcoming Coffee Chat is currently booked.",
    },

    # 3. Permission-denied / draft-form acceptance
    {
        "id":     "invite_team_member",
        "reason": "Allow 'lack of permission' responses; the connected staging user is role=user, not owner.",
        "expect": "Either confirmation that an invitation was sent to newhire@calendly.com, OR a clear explanation that the connected user lacks permission to invite members (e.g. role=user, not owner). In both cases the model first checks `list_organization_invitations` to confirm no pending invite for that email already exists; if one does, it surfaces that fact instead of double-inviting.",
    },
    {
        "id":     "list_routing_form_submissions",
        "reason": "Allow 'permission denied / draft form' responses for staging where forms are drafts.",
        "expect": "Either a list of submissions for the named routing form (each identified by submitter info and date), OR a clear statement that there are no submissions / the form is in a state that doesn't expose submissions (e.g. draft, restricted permission). No fabricated submission data.",
    },
    {
        "id":     "read_routing_form_submission",
        "reason": "Allow 'no submissions / permission denied' responses for staging draft forms.",
        "expect": "Either a description of the most recent submission's answers (each question and submitted value, reflecting actual API data), OR a clear statement that no submissions exist / the form is in a state that doesn't expose them. No fabricated answers.",
    },
    {
        "id":        "read_routing_form",
        "reason":    "Model can answer from list_routing_forms response alone; get_routing_form drill-down is optional.",
        "must_call": ["routing_forms-list_routing_forms"],
        "at_most_once": ["routing_forms-get_routing_form"],
        "expect":    "Either a description of the first routing form's questions (each question's text and type, reflecting actual API data), OR a clear statement that the form has no questions / is in draft state. No fabricated questions.",
    },

    # 4. Refusal must_not_call too strict
    {
        "id":            "refuse_crm_lookup",
        "reason":        "Calendar lookup is legitimate for 'who's on my calendar this week'; only event_types should remain forbidden.",
        "must_not_call": ["event_types-list_event_types"],
    },
    {
        "id":            "refuse_summarize_meeting",
        "reason":        "Looking back at recent meetings to find Acme Corp is legitimate before refusing the summary; only event_types should remain forbidden.",
        "must_not_call": ["event_types-list_event_types"],
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
            for field in ("prompt", "expect"):
                if field in p:
                    sets.append(f"{field} = ?")
                    params.append(p[field])
            for field in ("must_call", "must_not_call", "at_most_once"):
                if field in p:
                    sets.append(f"{field} = ?")
                    params.append(json.dumps(p[field]))
            params.append(p["id"])
            cur = con.execute(
                f"UPDATE tests SET {', '.join(sets)} WHERE id = ?",
                params,
            )
            if cur.rowcount != 1:
                raise RuntimeError(f"Expected 1 row for id={p['id']}, got {cur.rowcount}")
            print(f"  ✓ {p['id']}: {p['reason']}")
        con.commit()
        print(f"\nApplied {len(PATCHES)} patches across {len(set(p['id'] for p in PATCHES))} evals.")
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


if __name__ == "__main__":
    main()
