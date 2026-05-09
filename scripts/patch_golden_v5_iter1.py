"""
Iter 1 patch on the golden v5 suite, applied after the first smoke
run (8/17 passed). Failure modes triaged into 4 buckets:

A. Exemplar-vs-real-state drift (3 cases): exemplar values were
   from a clean account; real test account has different state
   (`30 Minute Meeting` is 60 min not 30, 3 availability schedules
   not 1, and 1 upcoming Coffee Chat at Monday 9am not Friday).
   Fix: rewrite exemplars to match actual fixture state.

B. Prompt-vs-fixture mismatch (1 case): cancel_meeting exemplar
   referenced "Intro Call" event type, but the prompt asks about
   Coffee Chat. Fix: align exemplar to Coffee Chat at fixture time.

C. Specific-time prompts the fixture can't satisfy (1 case):
   book_meeting prompts asked for "next Thursday at 2:30pm" —
   that specific slot may or may not be free. Fix: rewrite prompts
   to "next available slot" framing so they don't depend on a
   specific clock time.

D. Model behavior mismatches the prescriptive exemplar (1 case):
   reschedule_meeting model legitimately asks the host for the new
   time (sensible UX) instead of pulling a reschedule URL. Fix:
   update exemplar + drop list_event_invitees from must_call.

Two known-failure cases NOT fixed here:
- invite_team_member: fixture-blocked (seat allotment exhausted on
  the test org). Documented in golden_v5_authoring.md.
- create_single_use_link_custom: model exhibits ECO-2123 (refuses
  duration override). Exemplar describes the desired behavior; the
  failure is meaningful signal for Gemini.
"""
from __future__ import annotations

import csv
import datetime
import json
import sqlite3
from pathlib import Path

ROOT     = Path(__file__).resolve().parent.parent
DB_PATH  = ROOT / "mcp_testing.db"
CSV_PATH = ROOT / "scripts" / "golden_v5.csv"

EXEMPLAR_PATCHES = [
    {
        "id": "list_event_types",
        "reason": "Bucket A — match actual active event types on the test account.",
        "exemplar":
            "Here are your event types:\n"
            "- **30 Minute Meeting** — 60 minutes\n"
            "- **A Managed Event** — 30 minutes\n"
            "- **Coffee Chat** — 30 minutes\n"
            "- **New Meeting** — 5 minutes\n"
            "- **Z Managed Event** — 30 minutes",
    },
    {
        "id": "list_meetings",
        "reason": "Bucket A — match actual single upcoming Coffee Chat (date drifts; rule handles).",
        "exemplar":
            "Here are your upcoming Calendly meetings this week:\n"
            "- **Coffee Chat** with Michael Kimelblat — Monday at 9:00–9:30 AM PT (Google Meet)",
    },
    {
        "id": "list_availability_schedules",
        "reason": "Bucket A — test account has 3 schedules, not 1.",
        "exemplar":
            "Here are your availability schedules:\n"
            "- **Working hours** (default)\n"
            "- **test**\n"
            "- **another**",
    },
    {
        "id": "list_routing_form_submissions",
        "reason": "Bucket A — test routing form has no submissions; user spec accepts this as a valid success path.",
        "exemplar":
            "Your **test** routing form has no submissions yet. Once invitees "
            "submit the form, their responses will appear here.",
    },
    {
        "id": "cancel_meeting",
        "reason": "Bucket B — match prompt (Coffee Chat) and fixture time (Monday 9am).",
        "exemplar":
            "Just to confirm — you want me to cancel your **Coffee Chat** with "
            "Michael Kimelblat (cash0902@gmail.com) on Monday at 9:00 AM PT? "
            "Cancellation will notify Michael. Reply 'yes' (and optionally a "
            "reason) to confirm and I'll proceed.",
    },
    {
        "id": "book_meeting",
        "reason": "Bucket C — exemplar shows the next-available-slot booking confirmation.",
        "exemplar":
            "Booked your Coffee Chat:\n"
            "- Event type: **Coffee Chat**\n"
            "- Invitee: **Michael Kimelblat** (cash0902@gmail.com)\n"
            "- Date and time: Monday at 9:30 AM PT\n"
            "- Location: Google Meet",
    },
    {
        "id": "reschedule_meeting",
        "reason": "Bucket D — model asks for new time (sensible UX) rather than dumping reschedule URL.",
        "exemplar":
            "Your next **Coffee Chat** is with Michael Kimelblat "
            "(cash0902@gmail.com) on Monday at 9:00 AM PT.\n\n"
            "What new date/time would you like to reschedule it to? I can "
            "show available Coffee Chat slots, or you can give me a specific "
            "time. If you'd prefer, I can also cancel and rebook directly.",
    },
]

# Drop list_event_invitees from reschedule_meeting must_call —
# the "ask for new time" path doesn't require pulling invitee data.
MUST_CALL_PATCHES = [
    {
        "id": "reschedule_meeting",
        "drop": ["meetings-list_event_invitees"],
        "reason": "Bucket D — model's ask-for-new-time path doesn't need invitee details.",
    },
]

# Rewrite a few prompt rows in golden_v5.csv to align with fixture
# state. Other prompts stay as authored.
PROMPT_REWRITES = {
    # cancel_meeting: drop specific-time prompts that can't match fixture.
    ("cancel_meeting", "1"): "Cancel my next Coffee Chat",
    ("cancel_meeting", "2"): "Cancel my upcoming Coffee Chat",
    ("cancel_meeting", "3"): "Drop my Coffee Chat with cash0902",
    ("cancel_meeting", "4"): "cancel my next coffee chat",
    ("cancel_meeting", "5"): "I need to cancel the Coffee Chat with cash0902",
    ("cancel_meeting", "6"): "Pull the plug on my next Coffee Chat",
    ("cancel_meeting", "7"): "Cancel my next upcoming meeting",

    # book_meeting: switch all to "next available slot" framing.
    ("book_meeting", "1"): "Book a Coffee Chat with cash0902@gmail.com for the next available slot",
    ("book_meeting", "2"): "Set up a 30-min Coffee Chat with cash0902@gmail.com — next available slot",
    ("book_meeting", "3"): "Schedule cash0902@gmail.com into my Coffee Chat at the next available time",
    ("book_meeting", "4"): "book cash0902@gmail.com into next available coffee chat slot",
    ("book_meeting", "5"): "Book the next available Coffee Chat with cash0902@gmail.com",
    ("book_meeting", "6"): "Get cash0902@gmail.com on my calendar for a Coffee Chat at the next open slot",
    ("book_meeting", "7"): "Schedule a Coffee Chat with cash0902@gmail.com at the next available time",

    # find_event_type_available_times prompt #1 was singular ("next slot"),
    # other 6 are plural; flip #1 to plural so all 7 match the multi-slot exemplar.
    ("find_event_type_available_times", "1"):
        "What slots are open for Coffee Chats next week?",
}


def apply_db_patches() -> None:
    now = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute("BEGIN")
        for p in EXEMPLAR_PATCHES:
            cur = con.execute(
                "UPDATE tests SET exemplar = ?, updated_at = ? WHERE id = ?",
                (p["exemplar"], now, p["id"]),
            )
            if cur.rowcount != 1:
                raise RuntimeError(f"Expected 1 row for id={p['id']}, got {cur.rowcount}")
            print(f"  ✓ exemplar  {p['id']}: {p['reason']}")
        for p in MUST_CALL_PATCHES:
            row = con.execute("SELECT must_call FROM tests WHERE id = ?", (p["id"],)).fetchone()
            current = json.loads(row[0]) if row[0] else []
            new = [t for t in current if t not in p["drop"]]
            if new == current:
                raise RuntimeError(f"Expected to drop {p['drop']} from {p['id']}; not in {current}")
            cur = con.execute(
                "UPDATE tests SET must_call = ?, updated_at = ? WHERE id = ?",
                (json.dumps(new), now, p["id"]),
            )
            if cur.rowcount != 1:
                raise RuntimeError(f"Expected 1 row for id={p['id']}, got {cur.rowcount}")
            print(f"  ✓ must_call {p['id']}: {p['reason']}; → {new}")
        con.commit()
        print(f"\nDB: {len(EXEMPLAR_PATCHES)} exemplar(s), {len(MUST_CALL_PATCHES)} must_call(s).")
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def apply_csv_patches() -> None:
    rows = list(csv.DictReader(CSV_PATH.open()))
    changed = 0
    for r in rows:
        key = (r["case_id"], r["prompt_index"])
        if key in PROMPT_REWRITES:
            old = r["prompt"]
            r["prompt"] = PROMPT_REWRITES[key]
            print(f"  ✓ prompt    {key[0]}#{key[1]}: was {old!r}")
            changed += 1
    with CSV_PATH.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["case_id", "prompt_index", "prompt"])
        w.writeheader()
        w.writerows(rows)
    print(f"\nCSV: {changed} prompt(s) rewritten in {CSV_PATH.name}.")


if __name__ == "__main__":
    apply_db_patches()
    apply_csv_patches()
