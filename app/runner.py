"""
Run orchestrator. Executes a list of tests sequentially, persisting each
iteration to SQLite and fanning out SSE events to any subscribers.

Only one run can be active at a time (protected by `_run_lock`).
Subscribers to a run's events receive live updates; if they join mid-run,
they read existing persisted results first, then subscribe to the live feed.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, AsyncIterator

from test_prompt import MCP_SERVER_URL, MODEL, make_client, run_test

from app import db

# ── State ─────────────────────────────────────────────────────────────────────

_run_lock      = asyncio.Lock()
_current_run_id: int | None = None
_subscribers:  dict[int, list[asyncio.Queue]] = {}


def current_run_id() -> int | None:
    return _current_run_id


# ── Event fanout ──────────────────────────────────────────────────────────────

def _subscribe(run_id: int) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue()
    _subscribers.setdefault(run_id, []).append(q)
    return q


def _unsubscribe(run_id: int, q: asyncio.Queue) -> None:
    if run_id in _subscribers:
        try:
            _subscribers[run_id].remove(q)
        except ValueError:
            pass
        if not _subscribers[run_id]:
            _subscribers.pop(run_id, None)


async def _broadcast(run_id: int, event: dict[str, Any]) -> None:
    for q in list(_subscribers.get(run_id, [])):
        await q.put(event)


async def stream(run_id: int) -> AsyncIterator[dict[str, Any]]:
    """
    Yield events for a run. Emits any already-persisted results first
    (for clients joining mid-run or viewing a completed run), then live
    events if the run is still active.
    """
    for r in db.list_run_results(run_id):
        yield {"type": "result", "result": r}

    run = db.get_run(run_id)
    if not run or run["status"] != "running":
        yield {"type": "complete", "status": run["status"] if run else "unknown"}
        return

    q = _subscribe(run_id)
    try:
        while True:
            event = await q.get()
            yield event
            if event.get("type") == "complete":
                return
    finally:
        _unsubscribe(run_id, q)


# ── Execution ─────────────────────────────────────────────────────────────────

class RunInProgressError(Exception):
    pass


async def start_run(
    test_ids: list[str],
    runs_per_test: int,
    model: str | None = None,
) -> int:
    """
    Create a run record, acquire the lock, and kick off execution in the
    background. Returns the run_id immediately so the caller can redirect.
    """
    global _current_run_id

    if _run_lock.locked():
        raise RunInProgressError(_current_run_id or 0)

    effective_model = model or MODEL
    run_id = db.create_run(effective_model, MCP_SERVER_URL, runs_per_test)
    asyncio.create_task(_execute_run(run_id, test_ids, runs_per_test, effective_model))
    return run_id


async def _execute_run(
    run_id: int, test_ids: list[str], runs_per_test: int, model: str,
) -> None:
    global _current_run_id
    async with _run_lock:
        _current_run_id = run_id
        try:
            await _execute_run_inner(run_id, test_ids, runs_per_test, model)
            db.mark_run_finished(run_id, "complete")
            await _broadcast(run_id, {"type": "complete", "status": "complete"})
        except Exception as e:
            db.mark_run_finished(run_id, "error")
            await _broadcast(run_id, {"type": "error", "message": str(e)})
            await _broadcast(run_id, {"type": "complete", "status": "error"})
        finally:
            _current_run_id = None


async def _execute_run_inner(
    run_id: int, test_ids: list[str], runs_per_test: int, model: str,
) -> None:
    token = os.environ.get("CALENDLY_MCP_TOKEN")
    if not token:
        raise RuntimeError("CALENDLY_MCP_TOKEN not set — run setup_auth.py")

    client = make_client()
    all_tests = {t["id"]: t for t in db.list_tests()}

    for test_id in test_ids:
        test = all_tests.get(test_id)
        if not test:
            continue

        effective_runs = 1 if test["mutates"] else runs_per_test
        await _broadcast(run_id, {
            "type":    "test_start",
            "test_id": test_id,
            "prompt":  test["prompt"],
            "mutates": test["mutates"],
            "runs":    effective_runs,
        })

        for i in range(effective_runs):
            # Use run_test for a single iteration so each result streams independently.
            result = await run_test(
                prompt=test["prompt"],
                expect=test["expect"],
                runs=1,
                token=token,
                client=client,
                label=test_id,
                must_call=test.get("must_call"),
                must_not_call=test.get("must_not_call"),
                model=model,
            )
            iter_result = result["runs"][0]
            db.save_run_result(run_id, test, i + 1, iter_result)

            await _broadcast(run_id, {
                "type":      "result",
                "result": {
                    "test_id":         test_id,
                    "test_prompt":     test["prompt"],
                    "test_expect":     test["expect"],
                    "iteration":       i + 1,
                    "total":           effective_runs,
                    "passed":          iter_result["passed"],
                    "tool_ok":         iter_result["tool_ok"],
                    "judge_ok":        iter_result["judge_ok"],
                    "tool_reason":     iter_result["tool_reason"],
                    "judge_reason":    iter_result["judge_reason"],
                    "tools_called":    iter_result["tools"],
                    "response_text":   iter_result["text"],
                    "elapsed_seconds": iter_result["elapsed"],
                },
            })
