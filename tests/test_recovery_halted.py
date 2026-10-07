"""Operator-journey tests for the HALTED recovery path (Slice 2).

These tests exercise the *operator-initiated* recovery sequence
(recover + public re-run) and the lock-serialization guarantee:
recovery transitions are the only path that can move a run
HALTED -> READY, and they do so under the run lock.

Slice 2 adds exact execution-ownership to the *execution* operations
(:func:`run_loop.execute_one_cartridge` and :func:`run_action.persist_begin_next_cartridge`).
Operator recovery operations (:func:`persist_recover_halted`,
:func:`persist_uncertain_halt`) are not gated on the claim, so the operator
journey still works.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from magazine import (
    orchestration,
    recovery_action,
    run_action,
    run_loop,
    run_preparation,
    state_store,
)
from magazine import schema
from magazine.execution_meta import (
    ExecutionMetadata,
    load_execution_metadata,
    save_execution_metadata,
    utc_now_iso,
)
from magazine.runtime_identity import RuntimeIdentity
from magazine.state import (
    STATUS_COMPLETED,
    STATUS_HALTED,
    STATUS_READY,
    STATUS_RUNNING,
)

OWNER = "operator"
MAGAZINE = """{
  "schema_version": "1.0",
  "cartridges": [
    {"id": "cart-1", "job_type": "research", "prompt": "prompt-1"},
    {"id": "cart-2", "job_type": "research", "prompt": "prompt-2"},
    {"id": "cart-3", "job_type": "research", "prompt": "prompt-3"}
  ]
}"""

_TEST_WRITER = RuntimeIdentity(runtime_id="0" * 32, pid=42, start_ticks=1000)
_MODEL_MAP = {"research": "model-a"}


def _write_magazine(tmp_path) -> str:
    p = tmp_path / "mag.json"
    p.write_text(MAGAZINE)
    return str(p)


def _write_state(state_path: str, state: dict) -> None:
    Path(state_path).write_text(json.dumps(state, indent=2) + "\n")


def make_paths(tmp_path) -> tuple[str, str, str, Path]:
    state_dir = tmp_path / "state"
    run_dir = state_dir / "run"
    run_dir.mkdir(parents=True)
    return (
        str(state_dir),
        str(run_dir / "state.json"),
        str(run_dir / ".lock"),
        run_dir,
    )


def _seed_claim(run_dir) -> tuple[int, RuntimeIdentity]:
    """Create a minimal execution claim so fenced execution operations can run."""
    exec_path = run_dir / "execution.json"
    save_execution_metadata(
        exec_path,
        ExecutionMetadata(
            attempt=1,
            runtime_id=_TEST_WRITER.runtime_id,
            pid=_TEST_WRITER.pid,
            start_ticks=_TEST_WRITER.start_ticks,
            created_at=utc_now_iso(),
            halted_by=None,
            halted_at=None,
            halt_reason=None,
        ),
    )
    return 1, _TEST_WRITER


def _public_run(magazine_path, state_dir, owner_id) -> dict:
    """The public runner: re-admit (HALTED/READY -> RUNNING via prepare_run)
    and run all remaining cartridges to completion."""
    prepared = run_preparation.prepare_run(magazine_path, state_dir, owner_id)
    assert prepared.state["status"] == STATUS_RUNNING
    dispatch = _RecordingDispatch()
    result = asyncio.run(
        run_loop.run_all_cartridges(
            magazine_path,
            prepared.state_path,
            prepared.lock_path,
            owner_id,
            prepared.attempt,
            prepared.identity,
            _MODEL_MAP,
            dispatch,
        )
    )
    return result.final_state, dispatch


class TestFullOperatorJourney:
    """Admit -> run -> HALT -> recover -> public re-run -> COMPLETE."""

    def test_full_journey_admit_run_halt_recover_public_complete(self, tmp_path):
        magazine_path = _write_magazine(tmp_path)
        state_dir, state_path, lock_path, _ = make_paths(tmp_path)
        prepared = run_preparation.prepare_run(
            magazine_path, state_dir, OWNER,
            runtime_id=_TEST_WRITER.runtime_id, pid=42, start_ticks=1000,
        )
        # cart-1 runs to completion under the real entrypoint.
        dispatch_a = _RecordingDispatch()
        result_a = asyncio.run(
            orchestration.execute_one_cartridge(
                magazine_path, state_path, lock_path, OWNER,
                prepared.attempt, prepared.identity,
                _MODEL_MAP, dispatch_a,
            )
        )
        assert result_a.state["status"] == STATUS_READY
        assert result_a.state["current"] is None
        assert result_a.state["revision"] == 2
        assert len(dispatch_a.calls) == 1
        assert dispatch_a.calls[0][0] == "prompt-1"

        # Advance to the second cartridge and persist the new current.
        run_action.persist_begin_next_cartridge(
            state_path, lock_path, OWNER, prepared.attempt, prepared.identity
        )

        # Uncertain-halt (RUNNING -> HALTED) is an operator action.
        halted = recovery_action.persist_uncertain_halt(state_path, lock_path, OWNER)
        assert halted["status"] == STATUS_HALTED
        assert halted["revision"] == 3
        # The claim was written by admission and is still present.
        claim = load_execution_metadata(
            str(Path(state_path).parent / "execution.json")
        )
        assert claim is not None

        # Recover: HALTED -> READY, interrupted cart-2 moved to front of todo.
        recovered = recovery_action.persist_recover_halted(state_path, lock_path, OWNER)
        assert recovered["status"] == STATUS_READY
        assert recovered["current"] is None
        assert recovered["todo"] == ["cart-2", "cart-3"]
        assert recovered["revision"] == 4

        # Public re-run: prepare_run re-admits (operator-recovered -> attempt+1),
        # runs cart-2 and cart-3 to COMPLETION.
        final_state, dispatch = _public_run(magazine_path, state_dir, OWNER)
        # The public runner is the entrypoint that re-admits and runs.
        assert final_state["status"] == STATUS_COMPLETED
        assert final_state["current"] is None
        assert final_state["todo"] == []
        assert final_state["revision"] == 8
        assert len(dispatch.calls) == 2
        assert [c[0] for c in dispatch.calls] == ["prompt-2", "prompt-3"]


class TestRevisionProgression:
    """Recovery must not reset revision; it must continue from the halted value."""

    def test_revision_continues_after_recovery_not_reset(self, tmp_path):
        magazine_path = _write_magazine(tmp_path)
        state_dir, state_path, lock_path, _ = make_paths(tmp_path)
        prepared = run_preparation.prepare_run(
            magazine_path, state_dir, OWNER,
            runtime_id=_TEST_WRITER.runtime_id, pid=42, start_ticks=1000,
        )

        # Complete cart-1; run_loop would call this and then re-admit via
        # prepare_run.  We only advance the state machine here.
        asyncio.run(
            orchestration.execute_one_cartridge(
                magazine_path, state_path, lock_path, OWNER,
                prepared.attempt, prepared.identity,
                _MODEL_MAP, _RecordingDispatch(),
            )
        )

        # Advance to cart-2 and persist.
        run_action.persist_begin_next_cartridge(
            state_path, lock_path, OWNER, prepared.attempt, prepared.identity
        )

        # HALT (RUNNING -> HALTED): revision stays.
        halted = recovery_action.persist_uncertain_halt(state_path, lock_path, OWNER)
        assert halted["status"] == STATUS_HALTED
        assert halted["revision"] == 3

        # Recover (HALTED -> READY): revision increments by exactly one.
        recovered = recovery_action.persist_recover_halted(state_path, lock_path, OWNER)
        assert recovered["status"] == STATUS_READY
        assert recovered["revision"] == halted["revision"] + 1
        assert recovered["revision"] == 4

        # Public re-run continues from 4 through to COMPLETED.
        final_state, _ = _public_run(magazine_path, state_dir, OWNER)
        assert final_state["status"] == STATUS_COMPLETED
        assert final_state["revision"] == 8
        # No step ever decremented revision below the halted value.
        assert all(
            rev >= 3 for rev in
            [halted["revision"], recovered["revision"], final_state["revision"]]
        )


class TestLockingAndConcurrentAccess:
    """Every recovery operation acquires the run lock before touching state."""

    def test_two_operations_serialized_by_lock(self, tmp_path):
        """A sequence of lock-protected operations (recover, re-admit, re-halt,
        recover) runs serially: each acquires the lock, mutates state under it
        and releases it, so the final persisted state is the last recovery and
        no stale lock remains."""
        state_dir, state_path, lock_path, run_dir = make_paths(tmp_path)
        _write_state(state_path, {
            "done": ["cart-1"],
            "current": "cart-2",
            "todo": ["cart-3"],
            "status": STATUS_HALTED,
            "revision": 2,
        })
        attempt, writer = _seed_claim(run_dir)

        # Op 1: recover (HALTED -> READY).
        recovered1 = recovery_action.persist_recover_halted(state_path, lock_path, OWNER)
        assert recovered1 == {
            "done": ["cart-1"],
            "current": None,
            "todo": ["cart-2", "cart-3"],
            "status": STATUS_READY,
            "revision": 3,
        }
        assert not Path(lock_path).exists()

        # Re-admit (READY -> RUNNING) so we can halt again.  This is a fenced
        # execution operation: it re-verifies the exact claim and writes state.
        running = run_action.persist_begin_next_cartridge(
            state_path, lock_path, OWNER, attempt, writer
        )
        assert running["status"] == STATUS_RUNNING

        # Op 2: halt (RUNNING -> HALTED).
        rehalted = recovery_action.persist_uncertain_halt(state_path, lock_path, OWNER)
        assert rehalted["status"] == STATUS_HALTED

        # Op 3: recover again (HALTED -> READY).
        recovered2 = recovery_action.persist_recover_halted(state_path, lock_path, OWNER)
        assert recovered2 == {
            "done": ["cart-1"],
            "current": None,
            "todo": ["cart-2", "cart-3"],
            "status": STATUS_READY,
            "revision": 5,
        }
        assert not Path(lock_path).exists()
        assert state_store.load_run_state(state_path) == recovered2


class _RecordingDispatch:
    """Minimal dispatch callable for orchestration tests.

    The runtime dispatch boundary is invoked as ``dispatch(prompt, model_id)``
    (see ``magazine.runtime_dispatch.dispatch_routed``).
    """

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def __call__(self, prompt: str, model_id: str) -> str:
        self.calls.append((prompt, model_id))
        return "done"
