"""
Reset script — undoes mutations that v4 evals performed against staging,
returning the account to a known fixture baseline so a re-run starts
clean.

What it does:
  1. Restore Coffee Chat availability rules from
     backups/coffee_chat_baseline_rules.json (eval #9 mutates these).
  2. Restore Coffee Chat duration to 30 minutes (eval #6 changes to 45).
  3. Deactivate any "Intro Call" event type (eval #5 created it).
  4. Cancel any active future meetings on Intro Call (eval #14).
  5. Revoke any pending invitation to newhire@calendly.com (eval #30) —
     requires org-admin permission, will skip with a warning otherwise.
  6. Clear the no-show flag from past Coffee Chats with Aundreia (eval
     #16) — depends on whether the no-show evals were actually run.

What it does NOT do (deliberately):
  - Cancel the upcoming or past Coffee Chats with Aundreia. Those are
    fixture meetings — leave them so the next run can reuse them.
  - Delete fixture members or pending invitations created by setup
    script — they're persistent fixtures.
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

HDR = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type":  "application/json",
    "Accept":        "application/json, text/event-stream",
}

SID: str | None = None


class MCPToolError(RuntimeError):
    pass


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
                   "clientInfo": {"name": "fixture-reset", "version": "0.1"}}
    }, timeout=15)
    SID = r.headers.get("Mcp-Session-Id")
    if r.status_code != 200:
        sys.exit(f"MCP init failed: {r.status_code} {r.text[:300]}")


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


def find_event_type(user_uri: str, name: str) -> dict | None:
    ets = call("event_types-list_event_types", {"user": user_uri})
    target = name.strip().lower()
    for et in ets.get("collection", []):
        if (et.get("name") or "").strip().lower() == target:
            return et
    return None


def step1_restore_coffee_rules(user_uri: str) -> None:
    print("Step 1: Restore Coffee Chat availability rules from backup")
    backup_path = ROOT / "backups" / "coffee_chat_baseline_rules.json"
    if not backup_path.exists():
        print(f"  ⚠ no backup at {backup_path} — skipping")
        return
    coffee = find_event_type(user_uri, "Coffee Chat")
    if not coffee:
        print("  ⚠ Coffee Chat not found — skipping")
        return
    with backup_path.open() as f:
        rule = json.load(f)
    if not rule or not rule.get("rules"):
        print("  ⚠ backup is empty (account had no rules originally) — skipping")
        return
    payload_rule = {
        "timezone": rule.get("timezone", "America/Los_Angeles"),
        "rules": rule["rules"],
    }
    try:
        call("event_types-update_event_type_availability_schedule", {
            "event_type": coffee["uri"],
            "update_event_type_availability_request": {"availability_rule": payload_rule},
        })
        print(f"  ✓ restored from {backup_path.name}")
    except MCPToolError as e:
        print(f"  ⚠ restore failed: {e}")


def step2_restore_coffee_duration(user_uri: str) -> None:
    print("\nStep 2: Restore Coffee Chat duration to 30 min")
    coffee = find_event_type(user_uri, "Coffee Chat")
    if not coffee:
        print("  ⚠ Coffee Chat not found — skipping")
        return
    if coffee.get("duration") == 30:
        print("  ✓ already 30 min")
        return
    uuid = coffee["uri"].split("/")[-1]
    try:
        call("event_types-update_event_type", {
            "uuid": uuid,
            "update_event_type_request": {"duration": 30},
        })
        print(f"  ✓ duration restored from {coffee.get('duration')} to 30 min")
    except MCPToolError as e:
        print(f"  ⚠ duration update failed: {e}")


def step3_deactivate_intro_call(user_uri: str) -> None:
    print("\nStep 3: Deactivate 'Intro Call' event type (created by eval #5)")
    intro = find_event_type(user_uri, "Intro Call")
    if not intro:
        print("  ✓ no Intro Call to deactivate")
        return
    if not intro.get("active", True):
        print("  ✓ already inactive")
        return
    uuid = intro["uri"].split("/")[-1]
    try:
        call("event_types-update_event_type", {
            "uuid": uuid,
            "update_event_type_request": {"active": False},
        })
        print(f"  ✓ deactivated {intro['uri']}")
    except MCPToolError as e:
        print(f"  ⚠ deactivate failed: {e}")


def step4_cancel_intro_call_meetings(user_uri: str) -> None:
    print("\nStep 4: Cancel any future Intro Call meetings (created by eval #14)")
    now = datetime.datetime.now(datetime.UTC)
    week = now + datetime.timedelta(days=14)
    events = call("meetings-list_events", {
        "user":           user_uri,
        "min_start_time": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "max_start_time": week.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "status":         "active",
        "count":          50,
    })
    intros = [e for e in events.get("collection", []) if e.get("name") == "Intro Call"]
    if not intros:
        print("  ✓ no active Intro Call meetings")
        return
    for e in intros:
        ev_uuid = e["uri"].split("/")[-1]
        try:
            call("meetings-cancel_event", {"uuid": ev_uuid})
            print(f"  ✓ canceled {e['uri']}")
        except MCPToolError as err:
            print(f"  ⚠ cancel failed for {e['uri']}: {err}")


def step5_revoke_newhire(org_uuid: str) -> None:
    print("\nStep 5: Revoke any pending newhire@calendly.com invitation")
    try:
        pending = call("organizations-list_organization_invitations", {
            "uuid": org_uuid, "status": "pending", "email": "newhire@calendly.com",
        })
    except MCPToolError as e:
        print(f"  ⚠ list failed: {e}")
        return
    coll = pending.get("collection", [])
    if not coll:
        print("  ✓ no pending newhire invitation")
        return
    for i in coll:
        inv_uuid = i["uri"].split("/")[-1]
        try:
            call("organizations-revoke_organization_invitation", {
                "org_uuid": org_uuid, "uuid": inv_uuid,
            })
            print(f"  ✓ revoked {inv_uuid}")
        except MCPToolError as e:
            print(f"  ⚠ revoke failed: {e}")


def step6_clear_aundreia_no_show(user_uri: str) -> None:
    print("\nStep 6: Clear no-show flags on past Aundreia Coffee Chats")
    past_lo = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=14)
    past_hi = datetime.datetime.now(datetime.UTC)
    events = call("meetings-list_events", {
        "user":           user_uri,
        "min_start_time": past_lo.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "max_start_time": past_hi.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "status":         "active",
        "count":          50,
    })
    cleared = 0
    for e in events.get("collection", []):
        if e.get("name") != "Coffee Chat":
            continue
        ev_uuid = e["uri"].split("/")[-1]
        invitees = call("meetings-list_event_invitees", {"uuid": ev_uuid})
        for inv in invitees.get("collection", []):
            if inv.get("email") != "aundreia.heisey@calendly.com":
                continue
            inv_uuid = inv["uri"].split("/")[-1]
            try:
                # Only delete if a no-show record exists.
                call("meetings-get_invitee_no_show", {"uuid": inv_uuid})
            except MCPToolError:
                continue  # 404 → no record, nothing to clear
            try:
                call("meetings-delete_invitee_no_show", {"uuid": inv_uuid})
                cleared += 1
                print(f"  ✓ cleared no-show on invitee {inv_uuid}")
            except MCPToolError as err:
                print(f"  ⚠ clear failed: {err}")
    if cleared == 0:
        print("  ✓ no no-show records to clear")


def main() -> None:
    init_session()
    me = call("users-get_current_user")
    user_uri = me["resource"]["uri"]
    org_uri  = me["resource"]["current_organization"]
    org_uuid = org_uri.split("/")[-1]
    print(f"Identity: {me['resource']['name']} <{me['resource']['email']}>")
    print()

    step1_restore_coffee_rules(user_uri)
    step2_restore_coffee_duration(user_uri)
    step3_deactivate_intro_call(user_uri)
    step4_cancel_intro_call_meetings(user_uri)
    step5_revoke_newhire(org_uuid)
    step6_clear_aundreia_no_show(user_uri)

    print("\n=== Reset complete. ===")


if __name__ == "__main__":
    main()
