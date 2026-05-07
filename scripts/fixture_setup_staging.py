"""
Idempotent fixture setup for the v4 eval suite against staging.

Reads the harness's `CALENDLY_MCP_TOKEN` from .env and probes/creates
the resources each eval needs:

  1. Coffee Chat event type exists (one-on-one, 30 min, google_conference).
  2. Coffee Chat availability includes Thursday (so eval #9 is observable).
  3. An upcoming Coffee Chat with FIXTURE_INVITEE_EMAIL.
  4. A past Coffee Chat with the fixture invitee: MANUAL.
  5. A fixture removable team member: invited (manual accept in UI).
  6. A pending team invitation (target for eval #29).
  7. newhire@calendly.com NOT pre-invited (precondition for eval #30).
  8. Routing form submissions: MANUAL.

The fixture invitee is intentionally a self-owned test address
(cash0902@gmail.com / Michael Kimelblat) rather than the
aundreia.heisey@calendly.com from the OpenAI submission doc, so
re-running the suite doesn't flood a real Calendly user with booking
confirmation emails.

Run after switching the harness to staging and reconnecting OAuth.
"""
from __future__ import annotations

import datetime
import json
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

URL    = "https://mcp.staging.calendly-internal.com"
TOKEN  = os.environ.get("CALENDLY_MCP_TOKEN")
if not TOKEN:
    sys.exit("CALENDLY_MCP_TOKEN not set in .env")

# Canonical fixture invitee for staging eval work. Using cash0902@gmail.com
# (Mike's secondary test account, also a member of the staging org)
# instead of aundreia.heisey@calendly.com to avoid spamming a real
# Calendly user with booking confirmation emails on every suite run.
FIXTURE_INVITEE_NAME  = "Michael Kimelblat"
FIXTURE_INVITEE_EMAIL = "cash0902@gmail.com"

HDR = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type":  "application/json",
    "Accept":        "application/json, text/event-stream",
}

SID: str | None = None


def parse_resp(text: str, ct: str | None):
    if "event-stream" in (ct or ""):
        for line in text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
    return json.loads(text)


def init_session() -> None:
    global SID
    r = httpx.post(URL, headers=HDR, json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                   "clientInfo": {"name": "fixture-setup", "version": "0.1"}}
    }, timeout=15)
    SID = r.headers.get("Mcp-Session-Id")
    if r.status_code != 200:
        sys.exit(f"MCP init failed: {r.status_code} {r.text[:300]}")


class MCPToolError(RuntimeError):
    pass


def call(name: str, args: dict | None = None):
    h = dict(HDR)
    if SID:
        h["Mcp-Session-Id"] = SID
    r = httpx.post(URL, headers=h, json={
        "jsonrpc": "2.0", "id": 99, "method": "tools/call",
        "params": {"name": name, "arguments": args or {}}
    }, timeout=30)
    d = parse_resp(r.text, r.headers.get("content-type"))
    if "error" in d:
        raise MCPToolError(f"{name}: {d['error']}")
    result = d.get("result", {})
    if result.get("isError"):
        # Tool-level error (e.g. 403 from upstream Calendly API). Body in content[0].text.
        content = result.get("content", [])
        msg = content[0]["text"] if content and content[0].get("type") == "text" else str(result)
        raise MCPToolError(f"{name}: {msg}")
    sc = result.get("structuredContent")
    if sc is not None:
        return sc
    content = result.get("content", [])
    if content and content[0].get("type") == "text":
        try:
            return json.loads(content[0]["text"])
        except json.JSONDecodeError:
            return content[0]["text"]
    return result


def utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def iso_z(dt: datetime.datetime) -> str:
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def step1_coffee_chat(user_uri: str) -> str:
    print("Step 1: Coffee Chat event type")
    ets = call("event_types-list_event_types", {"user": user_uri})
    coffee = None
    for et in ets.get("collection", []):
        if (et.get("name") or "").strip().lower() == "coffee chat":
            coffee = et
            break
    if coffee:
        print(f"  ✓ exists: {coffee['uri']}")
        return coffee["uri"]
    print("  creating Coffee Chat...")
    result = call("event_types-create_event_type", {
        "create_event_type_request": {
            "name":     "Coffee Chat",
            "owner":    user_uri,
            "duration": 30,
            "active":   True,
            "locations": [{"kind": "google_conference"}],
        }
    })
    res = result.get("resource", result)
    uri = res.get("uri") or res.get("scheduling_url")
    if not uri:
        print(f"  ⚠ unexpected create response: {json.dumps(result)[:300]}")
        sys.exit(1)
    print(f"  ✓ created: {uri}")
    return uri


def step2_availability(coffee_uri: str) -> None:
    print("\nStep 2: Coffee Chat availability rules (Thursday must be present)")
    sched = call("event_types-list_event_type_availability_schedule",
                 {"event_type": coffee_uri})
    # Response shape: {"collection": [{"availability_rule": {"timezone", "rules": [...]}}]}
    coll = sched.get("collection", []) if isinstance(sched, dict) else []
    rule = coll[0].get("availability_rule") if coll else None
    rules = rule.get("rules", []) if rule else []
    has_thursday = any(
        r.get("wday") == "thursday" and r.get("intervals")
        for r in rules
    )
    if has_thursday:
        print("  ✓ Thursday already in availability rules")
        return
    print("  no Thursday availability — installing baseline Mon–Fri 9–5...")
    baseline_rule = {
        "timezone": "America/Los_Angeles",
        "rules": [
            {"type": "wday", "wday": d, "intervals": [{"from": "09:00", "to": "17:00"}]}
            for d in ("monday", "tuesday", "wednesday", "thursday", "friday")
        ] + [
            {"type": "wday", "wday": d, "intervals": []}
            for d in ("saturday", "sunday")
        ],
    }
    backups_dir = ROOT / "backups"
    backups_dir.mkdir(exist_ok=True)
    backup_path = backups_dir / "coffee_chat_baseline_rules.json"
    with backup_path.open("w") as f:
        json.dump(rule or {}, f, indent=2)
    call("event_types-update_event_type_availability_schedule", {
        "event_type": coffee_uri,
        "update_event_type_availability_request": {
            "availability_rule": baseline_rule,
        },
    })
    print(f"  ✓ baseline installed; previous rules saved to {backup_path}")


def find_meeting_with_invitee(user_uri: str, name: str, email: str,
                              start: datetime.datetime, end: datetime.datetime) -> bool:
    events = call("meetings-list_events", {
        "user":           user_uri,
        "min_start_time": iso_z(start),
        "max_start_time": iso_z(end),
        "status":         "active",
        "count":          50,
    })
    for e in events.get("collection", []):
        if e.get("name") != name:
            continue
        ev_uuid = e["uri"].split("/")[-1]
        invitees = call("meetings-list_event_invitees", {"uuid": ev_uuid})
        if any(inv.get("email") == email for inv in invitees.get("collection", [])):
            return True
    return False


def step3_upcoming_meeting(user_uri: str, coffee_uri: str, user_tz: str) -> None:
    print(f"\nStep 3: Upcoming Coffee Chat with {FIXTURE_INVITEE_EMAIL}")
    now = utc_now()
    week = now + datetime.timedelta(days=7)
    if find_meeting_with_invitee(user_uri, "Coffee Chat",
                                 FIXTURE_INVITEE_EMAIL, now, week):
        print(f"  ✓ upcoming Coffee Chat with {FIXTURE_INVITEE_EMAIL} exists")
        return

    # Look up the Coffee Chat event type's actual configured locations.
    # Hardcoding google_conference fails on accounts where the event type
    # is configured with a different kind (zoom_conference, physical, etc.).
    location_payload: dict | None = None
    ets = call("event_types-list_event_types", {"user": user_uri})
    for et in ets.get("collection", []):
        if (et.get("name") or "").strip().lower() == "coffee chat":
            for loc in et.get("locations") or []:
                kind = loc.get("kind")
                if kind:
                    location_payload = {"kind": kind}
                    break
            break

    print(f"  no upcoming Coffee Chat with {FIXTURE_INVITEE_EMAIL} — finding a slot...")
    slots = call("event_types-list_event_type_available_times", {
        "event_type": coffee_uri,
        "start_time": iso_z(now + datetime.timedelta(hours=1)),
        "end_time":   iso_z(week),
    })
    slot_list = slots.get("collection", [])
    if not slot_list:
        print("  ⚠ no available slots in the next 7 days. Cannot book.")
        return
    slot = slot_list[0]
    loc_str = location_payload["kind"] if location_payload else "(none configured on event type)"
    print(f"  booking slot {slot['start_time']} (location: {loc_str})...")
    args: dict = {
        "post_invitee_request": {
            "event_type": coffee_uri,
            "start_time": slot["start_time"],
            "invitee": {
                "name":     FIXTURE_INVITEE_NAME,
                "email":    FIXTURE_INVITEE_EMAIL,
                "timezone": user_tz,
            },
        }
    }
    if location_payload:
        args["post_invitee_request"]["location"] = location_payload
    call("meetings-create_invitee", args)
    print("  ✓ booked")


def step4_past_meeting(user_uri: str) -> None:
    print(f"\nStep 4: Past Coffee Chat with {FIXTURE_INVITEE_EMAIL}")
    past_lo = utc_now() - datetime.timedelta(days=7)
    past_hi = utc_now()
    if find_meeting_with_invitee(user_uri, "Coffee Chat",
                                 FIXTURE_INVITEE_EMAIL, past_lo, past_hi):
        print(f"  ✓ past Coffee Chat with {FIXTURE_INVITEE_EMAIL} exists")
        return
    print("  ⚠ MANUAL SETUP NEEDED:")
    print(f"    Book a Coffee Chat with {FIXTURE_INVITEE_EMAIL} starting ~5")
    print("    minutes from now for 5-min duration. Wait at least 10 minutes")
    print("    after the start time so it counts as 'past' before running evals")
    print("    #16–#18 (mark_no_show / read_no_show / clear_no_show).")


def step5_removable_member(org_uuid: str, org_uri: str) -> None:
    print("\nStep 5: Fixture removable team member (target for eval #28)")
    try:
        pending = call("organizations-list_organization_invitations",
                       {"uuid": org_uuid, "status": "pending"})
        mems = call("organizations-list_organization_memberships",
                    {"organization": org_uri})
    except MCPToolError as e:
        print(f"  ⚠ list failed: {e}")
        return
    has_member = any(
        "eval-removable" in (m.get("user", {}).get("email") or "")
        for m in mems.get("collection", [])
    )
    pending_eval = [
        i for i in pending.get("collection", [])
        if "eval-removable" in (i.get("email") or "")
    ]
    if has_member:
        print("  ✓ fixture member already in org")
        return
    if pending_eval:
        print("  ⚠ pending invitation exists; accept it manually in the staging UI:")
        for i in pending_eval:
            print(f"    {i['email']}")
        return
    ts = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    email = f"eval-removable+{ts}@calendly.com"
    print(f"  inviting {email}...")
    try:
        call("organizations-create_organization_invitation", {
            "uuid": org_uuid,
            "create_organization_invitation_request": {"email": email},
        })
        print(f"  ✓ invited; ⚠ accept the invitation in the staging UI before running eval #28")
    except MCPToolError as e:
        print(f"  ⚠ invite failed: {e}")
        print("    The connected user likely lacks org-admin permission. Evals")
        print("    #28–#31 will not run cleanly without reconnecting as an owner.")


def step6_pending_invitation(org_uuid: str) -> None:
    print("\nStep 6: A pending team invitation (target for eval #29)")
    try:
        pending = call("organizations-list_organization_invitations",
                       {"uuid": org_uuid, "status": "pending"})
    except MCPToolError as e:
        print(f"  ⚠ list failed: {e}")
        return
    coll = pending.get("collection", [])
    if coll:
        print(f"  ✓ {len(coll)} pending invitation(s) already exist")
        return
    ts = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    email = f"fixture-pending+{ts}@calendly.com"
    print(f"  inviting {email}...")
    try:
        call("organizations-create_organization_invitation", {
            "uuid": org_uuid,
            "create_organization_invitation_request": {"email": email},
        })
        print("  ✓ invited (will appear in pending invitations list)")
    except MCPToolError as e:
        print(f"  ⚠ invite failed: {e}")


def step7_no_newhire(org_uuid: str) -> None:
    print("\nStep 7: newhire@calendly.com NOT pre-invited (precondition for eval #30)")
    try:
        pending = call("organizations-list_organization_invitations",
                       {"uuid": org_uuid, "status": "pending", "email": "newhire@calendly.com"})
    except MCPToolError as e:
        print(f"  ⚠ list failed: {e}")
        return
    coll = pending.get("collection", [])
    if not coll:
        print("  ✓ no pre-existing newhire invitation")
        return
    for i in coll:
        inv_uuid = i["uri"].split("/")[-1]
        print(f"  revoking stale newhire invitation {inv_uuid}...")
        try:
            call("organizations-revoke_organization_invitation", {
                "org_uuid": org_uuid,
                "uuid":     inv_uuid,
            })
        except MCPToolError as e:
            print(f"  ⚠ revoke failed: {e}")
    print("  ✓ done")


def step8_routing_forms() -> None:
    print("\nStep 8: Routing form submissions (evals #34, #35)")
    print("  ⚠ MANUAL SETUP NEEDED:")
    print("    The MCP token lacks `routing_forms:write` scope, and direct REST")
    print("    POSTs from this token are rejected. To enable evals #34 / #35:")
    print("    1) Activate one of the 3 draft routing forms in the staging UI.")
    print("    2) Submit to it via its public URL.")
    print("    Without this, evals #34 and #35 will return empty / 404.")


def step9_summarize_org_admin() -> None:
    print("\nStep 9: Summary — org-admin gates (if invites/removes failed above)")
    print("  Two distinct gates can block the org-admin tier (#28, #30, #31):")
    print("    - role=user (not owner): the Calendly API rejects invitation")
    print("      create/revoke and member removal with Permission Denied.")
    print("    - seat allotment exhausted: even owners get Permission Denied")
    print("      with a 'purchase more seats' message when the org has no")
    print("      free seats to invite into.")
    print("  See per-step error messages above for the specific cause on this")
    print("  account. Eval #29 (list_pending_invitations) is read-only and")
    print("  succeeds regardless of role.")


def main() -> None:
    init_session()
    me = call("users-get_current_user")
    user_uri = me["resource"]["uri"]
    org_uri  = me["resource"]["current_organization"]
    org_uuid = org_uri.split("/")[-1]
    user_tz  = me["resource"]["timezone"]
    print(f"Identity: {me['resource']['name']} <{me['resource']['email']}>")
    print(f"  user_uri: {user_uri}")
    print(f"  org_uri:  {org_uri}")
    print()

    coffee_uri = step1_coffee_chat(user_uri)
    step2_availability(coffee_uri)
    step3_upcoming_meeting(user_uri, coffee_uri, user_tz)
    step4_past_meeting(user_uri)
    step5_removable_member(org_uuid, org_uri)
    step6_pending_invitation(org_uuid)
    step7_no_newhire(org_uuid)
    step8_routing_forms()
    step9_summarize_org_admin()

    print("\n=== Setup complete. ===")


if __name__ == "__main__":
    main()
