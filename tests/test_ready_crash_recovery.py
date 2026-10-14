"""Recovery of a READY run abandoned by an extinct execution process."""

from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from magazine import recovery_action, run_action, runtime_identity
from magazine.async_runner import run_magazine
from magazine.execution_meta import (
    ExecutionMetadata,
    load_execution_metadata,
    save_execution_metadata,
    utc_now_iso,
)
from magazine.recovery_action import recover_lost_runtime
from magazine.runtime_identity import get_runtime_identity, process_is_extinct
from magazine.state_store import load_run_state, save_run_state


MODEL_MAP = {"small": "simulated-model"}
TIMEOUT = 30
linux_only = pytest.mark.skipif(
    sys.platform != "linux", reason="requires distinct Linux processes and /proc"
)


def _document(path: Path) -> None:
    path.write_text(json.dumps({
        "schema_version": "1.0",
        "cartridges": [
            {"id": "c1", "job_type": "small", "prompt": "first"},
            {"id": "c2", "job_type": "small", "prompt": "second"},
        ],
    }), encoding="utf-8")


def _paths(storage: Path) -> tuple[Path, Path, Path, Path]:
    run_dir = storage / "run"
    return (
        run_dir / "state.json", run_dir / ".lock",
        run_dir / "execution.json", run_dir / "results" / "c1.txt",
    )


def _owner(document: str, storage: str, ready, release, hard_exit: bool) -> None:
    original_begin = run_action.persist_begin_next_cartridge

    def at_ready(*args, **kwargs):
        state = load_run_state(args[0])
        assert state["status"] == "READY"
        assert state["current"] is None
        assert state["done"] == ["c1"] and state["todo"] == ["c2"]
        ready.set()
        if hard_exit:
            os._exit(137)
        assert release.wait(TIMEOUT)
        return original_begin(*args, **kwargs)

    async def dispatch(prompt, model_id):
        return f"SIMULATED_RESULT:{prompt}"

    run_action.persist_begin_next_cartridge = at_ready
    asyncio.run(run_magazine(document, storage, "probe-owner", MODEL_MAP, dispatch))


def _recover_in_process(state_path: str, lock_path: str, responses) -> None:
    outcome = recover_lost_runtime(state_path, lock_path, "probe-owner")
    responses.put(outcome.outcome)


@linux_only
def test_hard_crash_ready_recovers_then_explicit_run_starts_at_c2(tmp_path):
    """The real owner dies with os._exit; a separate process retires its claim."""
    document, storage = tmp_path / "workflow.json", tmp_path / "storage"
    _document(document)
    ctx = multiprocessing.get_context("fork")
    ready, release, responses = ctx.Event(), ctx.Event(), ctx.Queue()
    owner = ctx.Process(
        target=_owner, args=(str(document), str(storage), ready, release, True)
    )
    owner.start()
    assert ready.wait(TIMEOUT)
    owner.join(TIMEOUT)
    assert owner.exitcode == 137

    state_path, lock_path, exec_path, result_path = _paths(storage)
    state_before = state_path.read_bytes()
    result_before = result_path.read_bytes()
    claim_before = load_execution_metadata(exec_path)
    assert load_run_state(state_path) == {
        "done": ["c1"], "current": None, "todo": ["c2"],
        "status": "READY", "revision": 2,
    }
    assert claim_before.halt_reason is None
    assert process_is_extinct(claim_before.pid, claim_before.start_ticks)

    recoverer = ctx.Process(
        target=_recover_in_process,
        args=(str(state_path), str(lock_path), responses),
    )
    recoverer.start()
    recoverer.join(TIMEOUT)
    assert recoverer.exitcode == 0
    assert responses.get(timeout=5) == "ready_recovered"
    assert state_path.read_bytes() == state_before
    assert result_path.read_bytes() == result_before
    assert load_execution_metadata(exec_path).halt_reason == "lost-owner-ready"

    dispatched = []

    async def resumed_dispatch(prompt, model_id):
        dispatched.append(prompt)
        return "SIMULATED_RESULT:second"

    # Recovery did not dispatch.  This is a separate, explicit admission.
    assert dispatched == []
    finished = asyncio.run(run_magazine(
        str(document), str(storage), "probe-owner", MODEL_MAP, resumed_dispatch,
    ))
    assert finished["status"] == "COMPLETED"
    assert finished["done"] == ["c1", "c2"]
    assert dispatched == ["second"]
    assert result_path.read_bytes() == result_before
    assert load_execution_metadata(exec_path).attempt == claim_before.attempt + 1


@linux_only
def test_live_owner_at_same_ready_boundary_cannot_be_retired(tmp_path):
    document, storage = tmp_path / "workflow.json", tmp_path / "storage"
    _document(document)
    ctx = multiprocessing.get_context("fork")
    ready, release = ctx.Event(), ctx.Event()
    owner = ctx.Process(
        target=_owner, args=(str(document), str(storage), ready, release, False)
    )
    owner.start()
    try:
        assert ready.wait(TIMEOUT)
        state_path, lock_path, exec_path, result_path = _paths(storage)
        state_before = state_path.read_bytes()
        result_before = result_path.read_bytes()
        claim_before = exec_path.read_bytes()
        outcome = recover_lost_runtime(
            str(state_path), str(lock_path), "probe-owner"
        )
        assert outcome.outcome == "owner_live"
        assert state_path.read_bytes() == state_before
        assert exec_path.read_bytes() == claim_before
        assert result_path.read_bytes() == result_before
    finally:
        release.set()
        owner.join(TIMEOUT)
        if owner.is_alive():
            owner.terminate()
            owner.join(TIMEOUT)
    assert owner.exitcode == 0


def _ready_claim(tmp_path: Path) -> tuple[Path, Path, Path, ExecutionMetadata]:
    state_path, lock_path = tmp_path / "state.json", tmp_path / ".lock"
    exec_path = tmp_path / "execution.json"
    save_run_state(state_path, {
        "done": ["c1"], "current": None, "todo": ["c2"],
        "status": "READY", "revision": 2,
    })
    claim = ExecutionMetadata(
        attempt=1, runtime_id="1" * 32, pid=99999999, start_ticks=1,
        created_at=utc_now_iso(), halted_by=None, halted_at=None,
        halt_reason=None,
    )
    save_execution_metadata(exec_path, claim)
    return state_path, lock_path, exec_path, claim


def test_unknown_owner_liveness_refuses_ready_recovery(tmp_path, monkeypatch):
    state_path, lock_path, exec_path, claim = _ready_claim(tmp_path)
    state_before, claim_before = state_path.read_bytes(), exec_path.read_bytes()
    get_runtime_identity()  # Resolve the recovery writer before hiding /proc.
    monkeypatch.setattr(runtime_identity, "_PROC_ROOT", tmp_path / "missing-proc")

    outcome = recover_lost_runtime(str(state_path), str(lock_path), "probe-owner")

    assert outcome.outcome == "owner_live"
    assert state_path.read_bytes() == state_before
    assert exec_path.read_bytes() == claim_before
    assert load_execution_metadata(exec_path) == claim


def test_changed_claim_is_fenced_before_ready_retirement(tmp_path, monkeypatch):
    state_path, lock_path, exec_path, claim = _ready_claim(tmp_path)
    successor = replace(claim, attempt=2, runtime_id="2" * 32)
    real_acquire = recovery_action.acquire_run_lock

    def replace_before_lock(*args):
        save_execution_metadata(exec_path, successor)
        return real_acquire(*args)

    monkeypatch.setattr(recovery_action, "acquire_run_lock", replace_before_lock)
    outcome = recover_lost_runtime(str(state_path), str(lock_path), "probe-owner")
    assert outcome.outcome == "claim_changed"
    assert load_execution_metadata(exec_path) == successor
    assert load_run_state(state_path)["status"] == "READY"


def test_changed_claim_metadata_is_fenced_before_ready_retirement(
        tmp_path, monkeypatch):
    state_path, lock_path, exec_path, claim = _ready_claim(tmp_path)
    retired_elsewhere = replace(
        claim, halted_by="2" * 32, halted_at=utc_now_iso(),
        halt_reason="task-failed-between-cartridges",
    )
    real_acquire = recovery_action.acquire_run_lock

    def replace_before_lock(*args):
        save_execution_metadata(exec_path, retired_elsewhere)
        return real_acquire(*args)

    monkeypatch.setattr(recovery_action, "acquire_run_lock", replace_before_lock)
    outcome = recover_lost_runtime(str(state_path), str(lock_path), "probe-owner")
    assert outcome.outcome == "claim_changed"
    assert load_execution_metadata(exec_path) == retired_elsewhere
    assert load_run_state(state_path)["status"] == "READY"


def test_ready_route_status_fence_does_not_recover_new_running_state(
        tmp_path, monkeypatch):
    state_path, lock_path, exec_path, claim = _ready_claim(tmp_path)
    running = {
        "done": ["c1"], "current": "c2", "todo": [],
        "status": "RUNNING", "revision": 3,
    }
    real_acquire = recovery_action.acquire_run_lock

    def begin_before_lock(*args):
        save_run_state(state_path, running)
        return real_acquire(*args)

    monkeypatch.setattr(recovery_action, "acquire_run_lock", begin_before_lock)
    outcome = recover_lost_runtime(
        str(state_path), str(lock_path), "probe-owner",
        expected_status="READY",
    )
    assert outcome.outcome == "state_changed"
    assert load_run_state(state_path) == running
    assert load_execution_metadata(exec_path) == claim


def test_ready_retirement_failure_is_retryable(tmp_path, monkeypatch):
    state_path, lock_path, exec_path, claim = _ready_claim(tmp_path)
    state_before, claim_before = state_path.read_bytes(), exec_path.read_bytes()
    real_save = recovery_action.save_execution_metadata

    def fail_before_write(*args):
        raise OSError("claim write failed")

    monkeypatch.setattr(recovery_action, "save_execution_metadata", fail_before_write)
    with pytest.raises(OSError, match="claim write failed"):
        recover_lost_runtime(str(state_path), str(lock_path), "probe-owner")
    assert state_path.read_bytes() == state_before
    assert exec_path.read_bytes() == claim_before
    monkeypatch.setattr(recovery_action, "save_execution_metadata", real_save)
    assert recover_lost_runtime(
        str(state_path), str(lock_path), "probe-owner"
    ).outcome == "ready_recovered"
    assert state_path.read_bytes() == state_before
    assert load_execution_metadata(exec_path).halt_reason == "lost-owner-ready"


def test_ready_retirement_retry_after_durable_write_is_idempotent(
        tmp_path, monkeypatch):
    state_path, lock_path, exec_path, claim = _ready_claim(tmp_path)
    real_save = recovery_action.save_execution_metadata

    def fail_after_write(*args):
        real_save(*args)
        raise OSError("confirmation failed after write")

    monkeypatch.setattr(recovery_action, "save_execution_metadata", fail_after_write)
    with pytest.raises(OSError, match="confirmation failed after write"):
        recover_lost_runtime(str(state_path), str(lock_path), "probe-owner")
    retired = load_execution_metadata(exec_path)
    assert retired.halt_reason == "lost-owner-ready"
    monkeypatch.setattr(recovery_action, "save_execution_metadata", real_save)
    assert recover_lost_runtime(
        str(state_path), str(lock_path), "probe-owner"
    ).outcome == "ready_recovered"
    assert load_execution_metadata(exec_path) == retired
