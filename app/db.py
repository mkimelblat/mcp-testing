"""
SQLite-backed store for test definitions and run history.

Tables:
  tests         — editable test definitions (replaces the hardcoded TESTS list)
  runs          — one row per suite execution
  run_results   — one row per (run, test, iteration)

On first boot, if `tests` is empty, seeds with the 8 default Calendly workflow
tests (same content as the original hardcoded TESTS constant).
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mcp_testing.db")


# ── Default tests (seeded on first boot only) ─────────────────────────────────

DEFAULT_TESTS: list[dict[str, Any]] = [
    {
        "id":      "update_event_type_details",
        "prompt":  "Update my Coffee Chat event type from 30 minutes to 60 minutes and switch the location to Zoom",
        "expect":  (
            "Response confirms that the Coffee Chat event type is now 60 minutes long "
            "and that its location is now Zoom, such that a user would trust the change "
            "was actually made. Does not refuse, ask which Calendly account to use, "
            "or ask the user to provide the raw event-type structure."
        ),
        "must_call":     ["event_types-update_event_type"],
        "must_not_call": ["event_types-create_event_type"],
        "mutates": True,
    },
    {
        "id":      "update_event_type_availability",
        "prompt":  "Remove Fridays as available days from my Coffee Chat event type",
        "expect":  (
            "Response confirms that Friday is no longer a bookable day on the Coffee Chat "
            "event type, such that a user would trust the change was actually made. "
            "Does not fail with a schema error, ask the user to paste the raw schedule "
            "structure, or ask which Calendly account to use."
        ),
        "must_call":     ["event_types-update_event_type_availability_schedule"],
        "must_not_call": [],
        "mutates": True,
    },
    {
        "id":      "find_available_slots",
        "prompt":  "Find open slots for my Coffee Chat event type next week",
        "expect":  (
            "Response provides specific bookable time slots for the Coffee Chat event "
            "type for specific dates next week (not just a generic weekly range like "
            "'9am-5pm weekdays'). A user could reply 'book me for <one of those times>' "
            "and have enough information to commit. The response states a timezone at "
            "least once. Does not refuse or say it cannot retrieve availability."
        ),
        "must_call":     ["event_types-list_event_type_available_times"],
        "must_not_call": ["meetings-list_events"],
        "mutates": False,
    },
    {
        "id":      "get_scheduling_link",
        "prompt":  "Get me the scheduling link for my Coffee Chat event type",
        "expect":  (
            "Response provides exactly one direct scheduling URL (containing "
            "'calendly.com') for the Coffee Chat event type — a link the user could "
            "paste into an email and have a recipient book with. Does not return "
            "multiple event-type links, and does not create a single-use / one-time "
            "link when the user asked for the regular scheduling link."
        ),
        "must_call":     [],
        "must_not_call": ["scheduling_links-create_single_use_scheduling_link"],
        "mutates": False,
    },
    {
        "id":      "get_rescheduling_link",
        "prompt":  "What is the rescheduling link for my next upcoming Coffee Chat meeting?",
        "expect":  (
            "Response either provides a working rescheduling link for the identified "
            "upcoming Coffee Chat meeting (with enough context — meeting time, invitee — "
            "that the user knows which meeting it is for), OR clearly states there are "
            "no upcoming Coffee Chat meetings. Does not fabricate a link or claim it "
            "cannot access the calendar."
        ),
        "must_call":     ["meetings-list_events", "meetings-list_event_invitees"],
        "must_not_call": ["scheduling_links-create_single_use_scheduling_link"],
        "mutates": False,
    },
    {
        "id":      "cancel_meeting",
        "prompt":  "Cancel my next upcoming Coffee Chat meeting",
        "expect":  (
            "Response identifies a specific upcoming Coffee Chat meeting by name, time, "
            "and/or invitee before cancelling — so the user knows which meeting was "
            "cancelled. Acceptable variants: confirms the cancellation with those "
            "details, asks for disambiguation if multiple Coffee Chat meetings match, "
            "or states clearly there are no upcoming Coffee Chat meetings. Does not "
            "cancel blindly without identifying the meeting."
        ),
        "must_call":     ["meetings-list_events"],
        "must_not_call": [],
        "mutates": True,
    },
    {
        "id":      "create_single_use_link",
        "prompt":  "Create a single-use scheduling link for my Coffee Chat event type",
        "expect":  (
            "Response provides a single-use (one-time) scheduling link containing "
            "'calendly.com' tied to the Coffee Chat event type — the kind of link that "
            "expires after one booking. Does not return the reusable event-type "
            "scheduling page URL instead."
        ),
        "must_call":     ["scheduling_links-create_single_use_scheduling_link"],
        "must_not_call": [],
        "mutates": True,
    },
    {
        "id":      "book_meeting",
        "prompt":  "Book a meeting with test-automation@example.com using my Coffee Chat event type for the next available slot",
        "expect":  (
            "Response does ONE of the following cleanly: "
            "(a) confirms a Coffee Chat booking was created on the calendar for "
            "test-automation@example.com at a specific time, treating the connected "
            "Calendly account as the host and the email as the invitee; OR "
            "(b) returns a single-use scheduling link that test-automation@example.com "
            "can use to self-book (the natural Calendly flow — also a valid outcome). "
            "Does not invert host/invitee, refuse, or claim it cannot access the account."
        ),
        "must_call":     [],
        "must_not_call": [],
        "mutates": True,
    },
]


# ── Connection helper ─────────────────────────────────────────────────────────

def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── Schema + seeding ──────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS tests (
    id            TEXT    PRIMARY KEY,
    prompt        TEXT    NOT NULL,
    expect        TEXT    NOT NULL,
    must_call     TEXT    NOT NULL,
    must_not_call TEXT    NOT NULL,
    mutates       INTEGER NOT NULL,
    position      INTEGER NOT NULL,
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at     TEXT    NOT NULL,
    finished_at    TEXT,
    status         TEXT    NOT NULL,
    model          TEXT    NOT NULL,
    mcp_url        TEXT    NOT NULL,
    runs_per_test  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS run_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    test_id         TEXT    NOT NULL,
    test_prompt     TEXT    NOT NULL,
    test_expect     TEXT    NOT NULL,
    iteration       INTEGER NOT NULL,
    passed          INTEGER NOT NULL,
    tool_ok         INTEGER NOT NULL,
    judge_ok        INTEGER NOT NULL,
    tool_reason     TEXT    NOT NULL,
    judge_reason    TEXT    NOT NULL,
    tools_called    TEXT    NOT NULL,
    response_text   TEXT    NOT NULL,
    elapsed_seconds REAL    NOT NULL,
    created_at      TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_run_results_run_id ON run_results(run_id);
"""


def init_db() -> None:
    """Create tables if missing; seed `tests` from DEFAULT_TESTS on first boot."""
    with connect() as conn:
        conn.executescript(SCHEMA)
        (count,) = conn.execute("SELECT COUNT(*) FROM tests").fetchone()
        if count == 0:
            _seed_defaults(conn)


def _seed_defaults(conn: sqlite3.Connection) -> None:
    now = now_iso()
    rows = [
        (
            t["id"], t["prompt"], t["expect"],
            json.dumps(t["must_call"]), json.dumps(t["must_not_call"]),
            1 if t["mutates"] else 0, i, now, now,
        )
        for i, t in enumerate(DEFAULT_TESTS)
    ]
    conn.executemany(
        """INSERT INTO tests
           (id, prompt, expect, must_call, must_not_call, mutates, position, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )


# ── Test CRUD ─────────────────────────────────────────────────────────────────

def _row_to_test(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id":            row["id"],
        "prompt":        row["prompt"],
        "expect":        row["expect"],
        "must_call":     json.loads(row["must_call"]),
        "must_not_call": json.loads(row["must_not_call"]),
        "mutates":       bool(row["mutates"]),
        "position":      row["position"],
        "created_at":    row["created_at"],
        "updated_at":    row["updated_at"],
    }


def list_tests() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM tests ORDER BY position ASC, id ASC"
        ).fetchall()
    return [_row_to_test(r) for r in rows]


def get_test(test_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM tests WHERE id = ?", (test_id,)).fetchone()
    return _row_to_test(row) if row else None


def create_test(test: dict[str, Any]) -> None:
    now = now_iso()
    with connect() as conn:
        (max_pos,) = conn.execute(
            "SELECT COALESCE(MAX(position), -1) FROM tests"
        ).fetchone()
        conn.execute(
            """INSERT INTO tests
               (id, prompt, expect, must_call, must_not_call, mutates, position, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                test["id"], test["prompt"], test["expect"],
                json.dumps(test.get("must_call") or []),
                json.dumps(test.get("must_not_call") or []),
                1 if test.get("mutates") else 0,
                max_pos + 1, now, now,
            ),
        )


def update_test(test_id: str, test: dict[str, Any]) -> None:
    with connect() as conn:
        conn.execute(
            """UPDATE tests
               SET prompt = ?, expect = ?, must_call = ?, must_not_call = ?,
                   mutates = ?, updated_at = ?
               WHERE id = ?""",
            (
                test["prompt"], test["expect"],
                json.dumps(test.get("must_call") or []),
                json.dumps(test.get("must_not_call") or []),
                1 if test.get("mutates") else 0,
                now_iso(), test_id,
            ),
        )


def delete_test(test_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM tests WHERE id = ?", (test_id,))


# ── Run CRUD ──────────────────────────────────────────────────────────────────

def create_run(model: str, mcp_url: str, runs_per_test: int) -> int:
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO runs (started_at, status, model, mcp_url, runs_per_test)
               VALUES (?, 'running', ?, ?, ?)""",
            (now_iso(), model, mcp_url, runs_per_test),
        )
        return cur.lastrowid


def mark_run_finished(run_id: int, status: str = "complete") -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE runs SET status = ?, finished_at = ? WHERE id = ?",
            (status, now_iso(), run_id),
        )


def get_run(run_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    return dict(row) if row else None


def list_runs(limit: int = 50) -> list[dict[str, Any]]:
    """Return runs with aggregated passed/total counts joined from run_results."""
    with connect() as conn:
        rows = conn.execute(
            """SELECT runs.*,
                      COUNT(rr.id)                 AS results_total,
                      COALESCE(SUM(rr.passed), 0)  AS results_passed
               FROM runs
               LEFT JOIN run_results rr ON rr.run_id = runs.id
               GROUP BY runs.id
               ORDER BY runs.id DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def save_run_result(run_id: int, test: dict[str, Any], iteration: int, result: dict[str, Any]) -> None:
    """Persist one iteration of one test inside a run."""
    with connect() as conn:
        conn.execute(
            """INSERT INTO run_results
               (run_id, test_id, test_prompt, test_expect, iteration,
                passed, tool_ok, judge_ok, tool_reason, judge_reason,
                tools_called, response_text, elapsed_seconds, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id, test["id"], test["prompt"], test["expect"], iteration,
                1 if result["passed"] else 0,
                1 if result["tool_ok"] else 0,
                1 if result["judge_ok"] else 0,
                result["tool_reason"], result["judge_reason"],
                json.dumps(result["tools"]), result["text"],
                result["elapsed"], now_iso(),
            ),
        )


def list_run_results(run_id: int) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT * FROM run_results
               WHERE run_id = ?
               ORDER BY id ASC""",
            (run_id,),
        ).fetchall()
    return [
        {**dict(r), "tools_called": json.loads(r["tools_called"])}
        for r in rows
    ]
