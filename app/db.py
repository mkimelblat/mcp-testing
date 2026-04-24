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
        "prompt":  "Update my Coffee Chat event type to 45 minutes and switch the location to Zoom",
        "expect":  (
            "Response confirms that the Coffee Chat event type is now 45 minutes long "
            "and that its location is now Zoom, such that a user would trust the change "
            "was actually made. Does not refuse, ask which Calendly account to use, "
            "or ask the user to provide the raw event-type structure."
        ),
        "must_call":     ["event_types-update_event_type"],
        "must_not_call": ["event_types-create_event_type"],
        "at_most_once":  ["event_types-update_event_type"],
        "max_seconds":   None,
        "mutates":       True,
    },
    {
        "id":      "update_event_type_availability",
        "prompt":  "Remove Thursdays as available days from my Coffee Chat event type",
        "expect":  (
            "Response confirms that Thursdays is no longer a bookable day on the Coffee Chat "
            "event type, such that a user would trust the change was actually made. "
            "Does not fail with a schema error, ask the user to paste the raw schedule "
            "structure, or ask which Calendly account to use."
        ),
        "must_call":     ["event_types-update_event_type_availability_schedule"],
        "must_not_call": [],
        "at_most_once":  ["event_types-update_event_type_availability_schedule"],
        "max_seconds":   None,
        "mutates":       True,
    },
    {
        "id":      "find_available_slots",
        "prompt":  "Find open slots for my Coffee Chat event type next week",
        "expect":  (
            "Response identifies the Coffee Chat event type and provides next-week "
            "availability grounded in real data — either as a time window + frequency "
            "or as enumerated slots — in a form a user could act on. States a timezone "
            "at least once. Does not refuse or say it cannot retrieve availability."
        ),
        "must_call":     ["event_types-list_event_type_available_times"],
        "must_not_call": [
            "meetings-list_events",
            "event_types-list_event_type_availability_schedule",
            "event_types-update_event_type_availability_schedule",
            "availability-list_user_availability_schedules",
            "availability-get_user_availability_schedule",
            "availability-list_user_busy_times",
        ],
        "at_most_once":  ["event_types-list_event_type_available_times"],
        "max_seconds":   30.0,
        "mutates":       False,
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
        "must_call":     ["event_types-list_event_types"],
        "must_not_call": ["scheduling_links-create_single_use_scheduling_link"],
        "at_most_once":  ["event_types-list_event_types"],
        "max_seconds":   30.0,
        "mutates":       False,
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
        "must_not_call": ["scheduling_links-create_single_use_scheduling_link", "meetings-cancel_event"],
        "at_most_once":  [],
        "max_seconds":   None,
        "mutates":       False,
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
        "must_call":     ["meetings-cancel_event"],
        "must_not_call": [],
        "at_most_once":  [],
        "max_seconds":   None,
        "mutates":       True,
    },
    {
        "id":      "create_single_use_link",
        "prompt":  "Create a single-use scheduling link for my Coffee Chat event type with a 15 min duration",
        "expect":  (
            "Response provides a single-use (one-time) scheduling link containing "
            "'calendly.com' tied to the Coffee Chat event type — the kind of link that "
            "expires after one booking. Does not return the reusable event-type "
            "scheduling page URL instead."
        ),
        "must_call":     ["shares-create_share"],
        "must_not_call": ["meetings-create_invitee", "scheduling_links-create_single_use_scheduling_link"],
        "at_most_once":  ["scheduling_links-create_single_use_scheduling_link"],
        "max_seconds":   None,
        "mutates":       True,
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
        "must_call":     ["meetings-create_invitee"],
        "must_not_call": [],
        "at_most_once":  ["meetings-create_invitee"],
        "max_seconds":   30.0,
        "mutates":       True,
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
    at_most_once  TEXT    NOT NULL DEFAULT '[]',
    max_seconds   REAL,
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
    runs_per_test  INTEGER NOT NULL,
    name           TEXT
);

CREATE TABLE IF NOT EXISTS run_results (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id              INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    test_id             TEXT    NOT NULL,
    test_prompt         TEXT    NOT NULL,
    test_expect         TEXT    NOT NULL,
    test_must_call      TEXT    NOT NULL DEFAULT '[]',
    test_must_not_call  TEXT    NOT NULL DEFAULT '[]',
    test_at_most_once   TEXT    NOT NULL DEFAULT '[]',
    test_max_seconds    REAL,
    test_mutates        INTEGER NOT NULL DEFAULT 0,
    iteration           INTEGER NOT NULL,
    passed              INTEGER NOT NULL,
    tool_ok             INTEGER NOT NULL,
    judge_ok            INTEGER NOT NULL,
    at_most_once_ok     INTEGER NOT NULL DEFAULT 1,
    time_ok             INTEGER NOT NULL DEFAULT 1,
    tool_reason         TEXT    NOT NULL,
    judge_reason        TEXT    NOT NULL,
    at_most_once_reason TEXT    NOT NULL DEFAULT '',
    time_reason         TEXT    NOT NULL DEFAULT '',
    tools_called        TEXT    NOT NULL,
    response_text       TEXT    NOT NULL,
    elapsed_seconds     REAL    NOT NULL,
    input_tokens        INTEGER,
    output_tokens       INTEGER,
    created_at          TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_run_results_run_id ON run_results(run_id);
"""


# Additive ALTER statements for upgrading pre-existing DBs.
# SQLite has no "ADD COLUMN IF NOT EXISTS", so we check PRAGMA first.
_MIGRATIONS: dict[str, list[tuple[str, str]]] = {
    "tests": [
        ("at_most_once", "ALTER TABLE tests ADD COLUMN at_most_once TEXT NOT NULL DEFAULT '[]'"),
        ("max_seconds",  "ALTER TABLE tests ADD COLUMN max_seconds REAL"),
    ],
    "runs": [
        ("name", "ALTER TABLE runs ADD COLUMN name TEXT"),
    ],
    "run_results": [
        ("test_must_call",      "ALTER TABLE run_results ADD COLUMN test_must_call TEXT NOT NULL DEFAULT '[]'"),
        ("test_must_not_call",  "ALTER TABLE run_results ADD COLUMN test_must_not_call TEXT NOT NULL DEFAULT '[]'"),
        ("test_at_most_once",   "ALTER TABLE run_results ADD COLUMN test_at_most_once TEXT NOT NULL DEFAULT '[]'"),
        ("test_max_seconds",    "ALTER TABLE run_results ADD COLUMN test_max_seconds REAL"),
        ("test_mutates",        "ALTER TABLE run_results ADD COLUMN test_mutates INTEGER NOT NULL DEFAULT 0"),
        ("at_most_once_ok",     "ALTER TABLE run_results ADD COLUMN at_most_once_ok INTEGER NOT NULL DEFAULT 1"),
        ("time_ok",             "ALTER TABLE run_results ADD COLUMN time_ok INTEGER NOT NULL DEFAULT 1"),
        ("at_most_once_reason", "ALTER TABLE run_results ADD COLUMN at_most_once_reason TEXT NOT NULL DEFAULT ''"),
        ("time_reason",         "ALTER TABLE run_results ADD COLUMN time_reason TEXT NOT NULL DEFAULT ''"),
        ("input_tokens",        "ALTER TABLE run_results ADD COLUMN input_tokens INTEGER"),
        ("output_tokens",       "ALTER TABLE run_results ADD COLUMN output_tokens INTEGER"),
    ],
}


def _migrate(conn: sqlite3.Connection) -> None:
    for table, alters in _MIGRATIONS.items():
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for col, stmt in alters:
            if col not in existing:
                conn.execute(stmt)


def init_db() -> None:
    """Create tables if missing; migrate existing DBs; seed on first boot."""
    with connect() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
        (count,) = conn.execute("SELECT COUNT(*) FROM tests").fetchone()
        if count == 0:
            _seed_defaults(conn)


def _seed_defaults(conn: sqlite3.Connection) -> None:
    now = now_iso()
    rows = [
        (
            t["id"], t["prompt"], t["expect"],
            json.dumps(t["must_call"]), json.dumps(t["must_not_call"]),
            json.dumps(t.get("at_most_once") or []),
            t.get("max_seconds"),
            1 if t["mutates"] else 0, i, now, now,
        )
        for i, t in enumerate(DEFAULT_TESTS)
    ]
    conn.executemany(
        """INSERT INTO tests
           (id, prompt, expect, must_call, must_not_call, at_most_once, max_seconds,
            mutates, position, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
        "at_most_once":  json.loads(row["at_most_once"]),
        "max_seconds":   row["max_seconds"],
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
               (id, prompt, expect, must_call, must_not_call, at_most_once, max_seconds,
                mutates, position, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                test["id"], test["prompt"], test["expect"],
                json.dumps(test.get("must_call") or []),
                json.dumps(test.get("must_not_call") or []),
                json.dumps(test.get("at_most_once") or []),
                test.get("max_seconds"),
                1 if test.get("mutates") else 0,
                max_pos + 1, now, now,
            ),
        )


def update_test(test_id: str, test: dict[str, Any]) -> None:
    with connect() as conn:
        conn.execute(
            """UPDATE tests
               SET prompt = ?, expect = ?, must_call = ?, must_not_call = ?,
                   at_most_once = ?, max_seconds = ?, mutates = ?, updated_at = ?
               WHERE id = ?""",
            (
                test["prompt"], test["expect"],
                json.dumps(test.get("must_call") or []),
                json.dumps(test.get("must_not_call") or []),
                json.dumps(test.get("at_most_once") or []),
                test.get("max_seconds"),
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


def mark_abandoned_runs() -> int:
    """Mark any run still flagged `running` as `error`. Returns the count.

    In-memory runner state (active tasks, queues) is lost on process restart
    — uvicorn `--reload`, crashes, redeploys — but the DB row stays
    `running`. Call this at startup: whatever the DB says is `running` at
    boot is by definition orphaned, because the runner that would advance
    it no longer exists.
    """
    with connect() as conn:
        cur = conn.execute(
            "UPDATE runs SET status = 'error', finished_at = ? WHERE status = 'running'",
            (now_iso(),),
        )
        return cur.rowcount


def get_run(run_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    return dict(row) if row else None


def set_run_name(run_id: int, name: str | None) -> None:
    """Set or clear a run's display name. Raises ValueError if run_id
    doesn't exist so callers can translate to 404."""
    if name is not None:
        name = name.strip()[:200] or None
    with connect() as conn:
        cur = conn.execute("UPDATE runs SET name = ? WHERE id = ?", (name, run_id))
        if cur.rowcount == 0:
            raise ValueError(f"run {run_id} not found")


def list_runs(limit: int = 50, query: str = "") -> list[dict[str, Any]]:
    """Return runs with aggregate and per-test pass/total breakdowns.

    When `query` is non-empty, restrict to runs that have at least one
    run_result whose response_text, tool_reason, judge_reason, or
    tools_called contains the substring (SQLite LIKE, case-insensitive).
    """
    q = query.strip()
    where_sql  = ""
    where_args: tuple = ()
    if q:
        like = f"%{q}%"
        where_sql = """
            WHERE EXISTS (
                SELECT 1 FROM run_results
                WHERE run_results.run_id = runs.id
                  AND (response_text LIKE ?
                       OR tool_reason LIKE ?
                       OR judge_reason LIKE ?
                       OR tools_called LIKE ?)
            )
        """
        where_args = (like, like, like, like)

    with connect() as conn:
        rows = conn.execute(
            f"""SELECT runs.*,
                       COUNT(rr.id)                AS results_total,
                       COALESCE(SUM(rr.passed), 0) AS results_passed
                FROM runs
                LEFT JOIN run_results rr ON rr.run_id = runs.id
                {where_sql}
                GROUP BY runs.id
                ORDER BY runs.id DESC
                LIMIT ?""",
            (*where_args, limit),
        ).fetchall()
        runs = [dict(r) for r in rows]

        run_ids = [r["id"] for r in runs]
        if run_ids:
            placeholders = ",".join("?" * len(run_ids))
            per_test = conn.execute(
                f"""SELECT run_id, test_id,
                           COUNT(*)                 AS total,
                           COALESCE(SUM(passed), 0) AS passed,
                           MIN(id)                  AS first_result_id
                    FROM run_results
                    WHERE run_id IN ({placeholders})
                    GROUP BY run_id, test_id
                    ORDER BY run_id DESC, first_result_id""",
                run_ids,
            ).fetchall()
        else:
            per_test = []

    buckets: dict[int, list[dict[str, Any]]] = {}
    for row in per_test:
        buckets.setdefault(row["run_id"], []).append({
            "test_id": row["test_id"],
            "passed":  row["passed"],
            "total":   row["total"],
        })
    for r in runs:
        r["tests"] = buckets.get(r["id"], [])
    return runs


def save_run_result(run_id: int, test: dict[str, Any], iteration: int, result: dict[str, Any]) -> int:
    """Persist one iteration of one test inside a run. Returns the inserted id.

    Snapshots the full eval rubric (prompt/expect + all assertions + mutates)
    so later edits to the `tests` row don't mutate what this run was scored
    against. The run detail page renders from these columns, never from the
    live `tests` row.
    """
    usage = result.get("usage") or {}
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO run_results
               (run_id, test_id, test_prompt, test_expect,
                test_must_call, test_must_not_call, test_at_most_once,
                test_max_seconds, test_mutates, iteration,
                passed, tool_ok, judge_ok, at_most_once_ok, time_ok,
                tool_reason, judge_reason, at_most_once_reason, time_reason,
                tools_called, response_text, elapsed_seconds,
                input_tokens, output_tokens, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id, test["id"], test["prompt"], test["expect"],
                json.dumps(test.get("must_call") or []),
                json.dumps(test.get("must_not_call") or []),
                json.dumps(test.get("at_most_once") or []),
                test.get("max_seconds"),
                1 if test.get("mutates") else 0,
                iteration,
                1 if result["passed"] else 0,
                1 if result["tool_ok"] else 0,
                1 if result["judge_ok"] else 0,
                1 if result["at_most_once_ok"] else 0,
                1 if result["time_ok"] else 0,
                result["tool_reason"], result["judge_reason"],
                result["at_most_once_reason"], result["time_reason"],
                json.dumps(result["tools"]), result["text"],
                result["elapsed"],
                usage.get("input"), usage.get("output"),
                now_iso(),
            ),
        )
        return cur.lastrowid


def list_run_results(run_id: int) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT * FROM run_results
               WHERE run_id = ?
               ORDER BY id ASC""",
            (run_id,),
        ).fetchall()
    return [
        {
            **dict(r),
            "tools_called":       json.loads(r["tools_called"]),
            "test_must_call":     json.loads(r["test_must_call"]),
            "test_must_not_call": json.loads(r["test_must_not_call"]),
            "test_at_most_once":  json.loads(r["test_at_most_once"]),
        }
        for r in rows
    ]
