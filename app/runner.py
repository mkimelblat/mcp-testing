"""
Run orchestrator. Executes a list of tests sequentially, persisting each
iteration to SQLite and fanning out SSE events to any subscribers.

Up to `MAX_CONCURRENT_RUNS` runs may be active at once. Each run executes
its tests serially; parallelism is only at the run level. Subscribers to
a run's events receive live updates; if they join mid-run, they read
existing persisted results first, then subscribe to the live feed.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, AsyncIterator

from test_prompt import MCP_SERVER_URL, MODEL, make_client, run_test

from app import db

# ── State ─────────────────────────────────────────────────────────────────────

MAX_CONCURRENT_RUNS = 3

_active_runs: set[int] = set()
_subscribers: dict[int, list[asyncio.Queue]] = {}
_run_plans: dict[int, int] = {}


def current_run_ids() -> list[int]:
    return sorted(_active_runs)


def planned_total(run_id: int) -> int | None:
    return _run_plans.get(run_id)


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
    """Raised when at capacity. `args[0]` is the list of active run_ids."""


async def start_run(
    test_ids: list[str],
    runs_per_test: int,
    model: str | None = None,
) -> int:
    """
    Create a run record and kick off execution in the background. Returns
    the run_id immediately. Up to MAX_CONCURRENT_RUNS may execute at once.

    The capacity check → db insert → set mutation runs with no awaits in
    between, so the asyncio event loop cannot switch coroutines mid-check
    (no race even with simultaneous requests).
    """
    if len(_active_runs) >= MAX_CONCURRENT_RUNS:
        raise RunInProgressError(sorted(_active_runs))

    effective_model = model or MODEL
    run_id = db.create_run(effective_model, MCP_SERVER_URL, runs_per_test)
    _active_runs.add(run_id)

    tests_by_id = {t["id"]: t for t in db.list_tests()}
    _run_plans[run_id] = sum(
        1 if tests_by_id[tid]["mutates"] else runs_per_test
        for tid in test_ids if tid in tests_by_id
    )

    asyncio.create_task(_execute_run(run_id, test_ids, runs_per_test, effective_model))
    return run_id


async def _execute_run(
    run_id: int, test_ids: list[str], runs_per_test: int, model: str,
) -> None:
    try:
        await _execute_run_inner(run_id, test_ids, runs_per_test, model)
        db.mark_run_finished(run_id, "complete")
        await _broadcast(run_id, {"type": "complete", "status": "complete"})
    except Exception as e:
        db.mark_run_finished(run_id, "error")
        await _broadcast(run_id, {"type": "error", "message": str(e)})
        await _broadcast(run_id, {"type": "complete", "status": "error"})
    finally:
        _active_runs.discard(run_id)
        _run_plans.pop(run_id, None)


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
                at_most_once=test.get("at_most_once"),
                max_seconds=test.get("max_seconds"),
                model=model,
            )
            iter_result = result["runs"][0]
            result_id = db.save_run_result(run_id, test, i + 1, iter_result)
            usage = iter_result.get("usage") or {}

            await _broadcast(run_id, {
                "type":      "result",
                "result": {
                    "id":                  result_id,
                    "test_id":             test_id,
                    "test_prompt":         test["prompt"],
                    "test_expect":         test["expect"],
                    "test_must_call":      test.get("must_call") or [],
                    "test_must_not_call":  test.get("must_not_call") or [],
                    "test_at_most_once":   test.get("at_most_once") or [],
                    "test_max_seconds":    test.get("max_seconds"),
                    "test_mutates":        test["mutates"],
                    "iteration":           i + 1,
                    "total":               effective_runs,
                    "passed":              iter_result["passed"],
                    "tool_ok":             iter_result["tool_ok"],
                    "judge_ok":            iter_result["judge_ok"],
                    "at_most_once_ok":     iter_result["at_most_once_ok"],
                    "time_ok":             iter_result["time_ok"],
                    "tool_reason":         iter_result["tool_reason"],
                    "judge_reason":        iter_result["judge_reason"],
                    "at_most_once_reason": iter_result["at_most_once_reason"],
                    "time_reason":         iter_result["time_reason"],
                    "tools_called":        iter_result["tools"],
                    "response_text":       iter_result["text"],
                    "elapsed_seconds":     iter_result["elapsed"],
                    "input_tokens":        usage.get("input"),
                    "output_tokens":       usage.get("output"),
                },
            })

            await _broadcast(run_id, {"type": "summary"})
