"""Regressions for bounded execution lifecycle corrections.

These tests exercise the persisted state and ownership claim rather than only
mocking transition internals.  The lost-owner regression additionally uses two
real Linux processes so the recorded ``pid`` / ``start_ticks`` evidence is
authoritative.
"""

from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from magazine import async_runner, run_loop
from magazine.async_runner import run_magazine
from magazine.execution_meta import (
    ExecutionMetadata,
    load_execution_metadata,
    save_execution_metadata,
    utc_now_iso,
)
from magazine.paths import execution_file_path
from magazine.recovery_action import (
    persist_error_halt,
    persist_recover_halted,
    recover_lost_runtime,
)
from magazine.runtime_identity import RuntimeIdentity, get_runtime_identity
from magazine.schema import ExecutionOwnershipError
from magazine.state_store import load_run_state, save_run_state


MODEL_MAP = {"research": "model-a"}
TIMEOUT = 30
linux_only = pytest.mark.skipif(
    sys.platform != "linux", reason="uses fork and /proc (Linux-only by design)"
)


def _write_magazine(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "cartridges": [
                    {"id": "a", "job_type": "research", "prompt": "A"},
                    {"id": "b", "job_type": "research", "prompt": "B"},
                ],
            }
        ),
        encoding="utf-8",
    )


def _run_paths(state_dir: Path) -> tuple[Path, Path, Path]:
    run_dir = state_dir / "run"
    return run_dir / "state.json", run_dir / ".lock", run_dir / "execution.json"


def _run_blocked_owner(magazine_path: str, state_dir: str, ready, stop) -> None:
    """Process A: own a RUNNING run and remain alive in model dispatch."""

    async def dispatch(prompt, model_id):
        ready.set()
        while not stop.is_set():
            await asyncio.sleep(0.01)
        return "unexpected release"

    asyncio.run(
        run_magazine(magazine_path, state_dir, "owner", MODEL_MAP, dispatch)
    )


@linux_only
def test_live_owner_process_cannot_be_retired_but_extinct_owner_can(tmp_path):
    """A free run lock is not evidence that the dispatching owner died."""

    magazine_path = tmp_path / "mag.json"
    state_dir = tmp_path / "state"
    _write_magazine(magazine_path)
    ctx = multiprocessing.get_context("fork")
    ready = ctx.Event()
    stop = ctx.Event()
    owner = ctx.Process(
        target=_run_blocked_owner,
        args=(str(magazine_path), str(state_dir), ready, stop),
    )
    owner.start()
    try:
        assert ready.wait(TIMEOUT), "owner never entered blocked dispatch"
        state_path, lock_path, exec_path = _run_paths(state_dir)
        claim_before = load_execution_metadata(exec_path)
        assert claim_before is not None
        assert claim_before.pid == owner.pid

        live = recover_lost_runtime(
            str(state_path), str(lock_path), "owner"
        )
        assert live.outcome == "owner_live"
        assert load_run_state(state_path)["status"] == "RUNNING"
        assert load_execution_metadata(exec_path) == claim_before
    finally:
        if owner.is_alive():
            owner.terminate()
        owner.join(TIMEOUT)
    assert not owner.is_alive()

    extinct = recover_lost_runtime(str(state_path), str(lock_path), "owner")
    assert extinct.outcome == "halted"
    assert load_run_state(state_path)["status"] == "HALTED"
    assert load_execution_metadata(exec_path).halt_reason == "lost-owner"


def test_failure_between_cartridges_retires_ready_claim_and_resumes_once(tmp_path):
    magazine_path = tmp_path / "mag.json"
    state_dir = tmp_path / "state"
    _write_magazine(magazine_path)
    first_dispatch = AsyncMock(return_value="RA")

    with patch(
        "magazine.run_loop.run_action.persist_begin_next_cartridge",
        side_effect=RuntimeError("between cartridges"),
    ):
        with pytest.raises(RuntimeError, match="between cartridges"):
            asyncio.run(
                run_magazine(
                    str(magazine_path), str(state_dir), "owner", MODEL_MAP,
                    first_dispatch,
                )
            )

    state_path, _, exec_path = _run_paths(state_dir)
    stranded = load_run_state(state_path)
    assert stranded == {
        "done": ["a"],
        "current": None,
        "todo": ["b"],
        "status": "READY",
        "revision": 2,
    }
    assert (state_dir / "run/results/a.txt").read_text() == "RA"
    assert load_execution_metadata(exec_path).halt_reason == (
        "task-failed-between-cartridges"
    )
    first_dispatch.assert_awaited_once()

    second_dispatch = AsyncMock(return_value="RB")
    completed = asyncio.run(
        run_magazine(
            str(magazine_path), str(state_dir), "owner", MODEL_MAP,
            second_dispatch,
        )
    )
    assert completed["status"] == "COMPLETED"
    assert completed["done"] == ["a", "b"]
    assert (state_dir / "run/results/a.txt").read_text() == "RA"
    assert (state_dir / "run/results/b.txt").read_text() == "RB"
    second_dispatch.assert_awaited_once_with("B", "model-a")


def test_stale_owner_cannot_retire_ready_claim(tmp_path):
    state_path = tmp_path / "state.json"
    lock_path = tmp_path / ".lock"
    exec_path = execution_file_path(tmp_path)
    ready = {
        "done": ["a"],
        "current": None,
        "todo": ["b"],
        "status": "READY",
        "revision": 2,
    }
    save_run_state(state_path, ready)
    current = ExecutionMetadata(
        attempt=2,
        runtime_id="2" * 32,
        pid=22,
        start_ticks=220,
        created_at=utc_now_iso(),
        halted_by=None,
        halted_at=None,
        halt_reason=None,
    )
    save_execution_metadata(exec_path, current)
    stale = RuntimeIdentity(runtime_id="1" * 32, pid=11, start_ticks=110)

    with pytest.raises(ExecutionOwnershipError):
        persist_error_halt(
            str(state_path), str(lock_path), "owner", 1, stale,
            RuntimeError("stale"),
            ready_retire_reason="task-failed-between-cartridges",
        )
    assert load_run_state(state_path) == ready
    assert load_execution_metadata(exec_path) == current


def test_original_exception_remains_primary_when_error_persistence_fails():
    class ExecutionFailure(RuntimeError):
        pass

    original = ExecutionFailure("model failed")
    persistence = OSError("state disk failed")
    with patch.object(
        run_loop, "run_all_cartridges", new=AsyncMock(side_effect=original)
    ), patch.object(
        async_runner.recovery_action, "persist_error_halt",
        side_effect=persistence,
    ):
        with pytest.raises(ExecutionFailure) as caught:
            asyncio.run(
                async_runner._run_cartridges_or_halt(
                    "mag", "state", "lock", "owner", 1,
                    RuntimeIdentity("1" * 32, 1, 1), MODEL_MAP, object(),
                )
            )

    assert caught.value is original
    assert caught.value.__traceback__ is not None
    assert any(
        "state disk failed" in note for note in getattr(caught.value, "__notes__", [])
    )


def test_cancellation_during_dispatch_halts_uncertain_run_and_propagates(tmp_path):
    magazine_path = tmp_path / "mag.json"
    state_dir = tmp_path / "state"
    _write_magazine(magazine_path)

    async def scenario():
        entered = asyncio.Event()

        async def dispatch(prompt, model_id):
            entered.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(
            run_magazine(
                str(magazine_path), str(state_dir), "owner", MODEL_MAP,
                dispatch,
            )
        )
        await asyncio.wait_for(entered.wait(), TIMEOUT)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    state_path, lock_path, exec_path = _run_paths(state_dir)
    halted = load_run_state(state_path)
    assert halted["status"] == "HALTED"
    assert halted["current"] == "a"
    assert halted["done"] == []
    assert halted["todo"] == ["b"]
    assert load_execution_metadata(exec_path).halt_reason == "CancelledError()"

    recovered = persist_recover_halted(
        str(state_path), str(lock_path), "owner"
    )
    assert recovered["status"] == "READY"
    assert recovered["todo"] == ["a", "b"]


def test_stale_cancelled_task_cannot_halt_successor(tmp_path):
    magazine_path = tmp_path / "mag.json"
    state_dir = tmp_path / "state"
    _write_magazine(magazine_path)

    async def scenario():
        entered = asyncio.Event()

        async def dispatch(prompt, model_id):
            entered.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(
            run_magazine(
                str(magazine_path), str(state_dir), "owner", MODEL_MAP,
                dispatch,
            )
        )
        await asyncio.wait_for(entered.wait(), TIMEOUT)
        state_path, _, exec_path = _run_paths(state_dir)
        old = load_execution_metadata(exec_path)
        successor = ExecutionMetadata(
            attempt=old.attempt + 1,
            runtime_id="f" * 32,
            pid=999,
            start_ticks=999,
            created_at=utc_now_iso(),
            halted_by=None,
            halted_at=None,
            halt_reason=None,
        )
        save_execution_metadata(exec_path, successor)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert load_run_state(state_path)["status"] == "RUNNING"
        assert load_execution_metadata(exec_path) == successor

    asyncio.run(scenario())


def test_cancellation_between_cartridges_retires_ready_claim(tmp_path):
    magazine_path = tmp_path / "mag.json"
    state_dir = tmp_path / "state"
    _write_magazine(magazine_path)
    dispatch = AsyncMock(return_value="RA")

    with patch(
        "magazine.run_loop.run_action.persist_begin_next_cartridge",
        side_effect=asyncio.CancelledError,
    ):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(
                run_magazine(
                    str(magazine_path), str(state_dir), "owner", MODEL_MAP,
                    dispatch,
                )
            )

    state_path, _, exec_path = _run_paths(state_dir)
    assert load_run_state(state_path) == {
        "done": ["a"],
        "current": None,
        "todo": ["b"],
        "status": "READY",
        "revision": 2,
    }
    assert (state_dir / "run/results/a.txt").read_text() == "RA"
    assert load_execution_metadata(exec_path).halt_reason == (
        "task-cancelled-between-cartridges"
    )
    dispatch.assert_awaited_once()


def test_cancellation_persistence_failure_is_diagnostic_and_still_cancels():
    cancellation = asyncio.CancelledError()
    persistence = OSError("cannot persist cancellation")
    with patch.object(
        run_loop, "run_all_cartridges", new=AsyncMock(side_effect=cancellation)
    ), patch.object(
        async_runner.recovery_action, "persist_error_halt",
        side_effect=persistence,
    ):
        with pytest.raises(asyncio.CancelledError) as caught:
            asyncio.run(
                async_runner._run_cartridges_or_halt(
                    "mag", "state", "lock", "owner", 1,
                    RuntimeIdentity("1" * 32, 1, 1), MODEL_MAP, object(),
                )
            )

    assert caught.value is cancellation
    assert any(
        "cannot persist cancellation" in note
        for note in getattr(caught.value, "__notes__", [])
    )
