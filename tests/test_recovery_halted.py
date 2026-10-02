"""Integration regression tests for explicit operator-approved recovery
of HALTED magazine runs.

These tests drive the full operator-recovery path end-to-end:

    RUNNING  --persist_uncertain_halt-->  HALTED
    HALTED   --persist_recover_halted--> RUNNING   (explicit operator action)
    RUNNING  --run_loop.run_all_cartridges--> COMPLETED

proving that the recovered run is positioned so the *existing* execution
path retries the interrupted cartridge (rather than advancing past it),
and that completed work, queue ordering, atomic persistence, lock-based
serialization and revision progression all hold through the recovery.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from unittest.mock import AsyncMock

from magazine import (
    orchestration,
    recovery_action,
    run_action,
    run_loop,
    run_preparation,
)
from magazine.recovery_transition import RecoveryTransitionError
from magazine.run_lock import (
    acquire_run_lock,
    release_run_lock,
    MagazineLockError,
)
from magazine.state import (
    MagazineStateError,
    STATUS_COMPLETED,
    STATUS_HALTED,
    STATUS_READY,
    STATUS_RUNNING,
)
from magazine.state_store import load_run_state, save_run_state


OWNER = "operator-1"


def _cartridges(cartridges, job_types):
    """Build a ``magazine.json`` document for the given cartridge ids and
    their ``{id: job_type}`` map."""
    return {
        "schema_version": "1.0",
        "cartridges": [
            {"id": i, "job_type": job_types[i], "prompt": f"prompt-{i}"}
            for i in cartridges
        ],
    }


def _write_magazine(path: Path, cartridges, job_types) -> None:
    path.write_text(json.dumps(_cartridges(cartridges, job_types)),
                   encoding="utf-8")


def make_paths(tmp_path):
    """state.json / .lock paths and the run dir for a run under ``tmp_path``.

    ``prepare_run`` derives the run dir from the *state* dir, so the run
    dir is always ``tmp_path / state / run``.
    """
    run_dir = tmp_path / "state" / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    return str(run_dir / "state.json"), str(run_dir / ".lock"), run_dir


def _dispatch_canned(*outputs):
    """An ``AsyncMock`` dispatch that returns the given canned outputs
    in call order."""
    return AsyncMock(side_effect=list(outputs))


# ---------------------------------------------------------------------------
# Normal HALTED recovery (full operator flow)
# ---------------------------------------------------------------------------

class TestNormalHaltedRecovery:
    def test_full_flow_running_halted_recovered_and_completed(self, tmp_path):
        """The normal operator path: a run that halted mid-flight is
        explicitly recovered and completes through the existing execution
        path, retrying the interrupted cartridge first."""
        job_types = {"a": "research", "b": "synthesis", "c": "research"}
        magazine_path = tmp_path / "magazine.json"
        _write_magazine(magazine_path, ("a", "b", "c"), job_types)
        state_dir = tmp_path / "state"
        state_path, lock_path, run_dir = make_paths(tmp_path)
        model_map = {"research": "model-a", "synthesis": "model-b"}
        dispatch = _dispatch_canned("RA", "RB", "RC")

        # 1) admission: READY -> RUNNING (current: a)
        run_preparation.prepare_run(str(magazine_path), str(state_dir), OWNER)
        # 2) cartridge 'a' completes through the real orchestration path
        asyncio.run(orchestration.execute_one_cartridge(
            str(magazine_path), state_path, lock_path,
            OWNER, model_map, dispatch,
        ))
        # 3) 'b' is admitted as the current cartridge (RUNNING, revision 3)
        run_action.persist_begin_next_cartridge(state_path, lock_path, OWNER)
        # 4) operator halts the uncertain run -> HALTED
        recovery_action.persist_uncertain_halt(state_path, lock_path, OWNER)
        assert load_run_state(state_path) == {
            "done": ["a"], "current": "b", "todo": ["c"],
            "status": STATUS_HALTED, "revision": 3,
        }
        # 5) explicit operator recovery
        recovered = recovery_action.persist_recover_halted(state_path, lock_path, OWNER)
        assert recovered == {
            "done": ["a"], "current": "b", "todo": ["c"],
            "status": STATUS_RUNNING, "revision": 4,
        }
        assert load_run_state(state_path) == recovered
        # 6) the existing execution path resumes from the interrupted cartridge
        final = asyncio.run(run_loop.run_all_cartridges(
            str(magazine_path), state_path, lock_path,
            OWNER, model_map, dispatch,
        ))
        assert final.final_state == {
            "done": ["a", "b", "c"], "current": None, "todo": [],
            "status": STATUS_COMPLETED, "revision": 7,
        }
        # dispatch order: 'a' (pre-halt), then 'b' (interrupted, retried), then 'c'
        dispatch_calls = [c.args for c in dispatch.await_args_list]
        assert dispatch_calls[0] == ("prompt-a", "model-a")
        assert dispatch_calls[1] == ("prompt-b", "model-b")
        assert dispatch_calls[2] == ("prompt-c", "model-a")
        # results persisted for every cartridge
        assert (run_dir / "results" / "a.txt").read_text() == "RA"
        assert (run_dir / "results" / "b.txt").read_text() == "RB"
        assert (run_dir / "results" / "c.txt").read_text() == "RC"

    def test_direct_halted_state_recoverable(self, tmp_path):
        """A freshly written HALTED state with a current cartridge is
        recoverable to RUNNING without going through prepare_run."""
        state_path, lock_path, run_dir = make_paths(tmp_path)
        save_run_state(state_path, {
            "done": ["a"], "current": "b", "todo": ["c"],
            "status": STATUS_HALTED, "revision": 2,
        })
        recovered = recovery_action.persist_recover_halted(state_path, lock_path, OWNER)
        assert load_run_state(state_path) == recovered == {
            "done": ["a"], "current": "b", "todo": ["c"],
            "status": STATUS_RUNNING, "revision": 3,
        }
        assert not (run_dir / ".lock").exists()


# ---------------------------------------------------------------------------
# The recovered run retries the interrupted cartridge, not the next one
# ---------------------------------------------------------------------------

class TestRetriesInterruptedCartridge:
    def test_execution_retries_interrupted_cartridge_not_next(self, tmp_path):
        """After recovery the first dispatch is the interrupted cartridge
        ('b'), proving the existing execution path does NOT advance past
        it."""
        job_types = {"a": "research", "b": "synthesis", "c": "research"}
        magazine_path = tmp_path / "magazine.json"
        _write_magazine(magazine_path, ("a", "b", "c"), job_types)
        model_map = {"research": "model-a", "synthesis": "model-b"}
        state_path, lock_path, run_dir = make_paths(tmp_path)
        save_run_state(state_path, {
            "done": ["a"], "current": "b", "todo": ["c"],
            "status": STATUS_HALTED, "revision": 2,
        })
        recovery_action.persist_recover_halted(
            state_path, lock_path, OWNER,
        )
        # confirm the recovered state still points at 'b'
        assert load_run_state(state_path)["current"] == "b"
        dispatch = _dispatch_canned("RB", "RC")
        final = asyncio.run(run_loop.run_all_cartridges(
            str(magazine_path), state_path, lock_path,
            OWNER, model_map,
            dispatch,
        ))
        dispatch_calls = [c.args for c in dispatch.await_args_list]
        # 'b' (interrupted) is dispatched before 'c' (the next cartridge)
        assert dispatch_calls == [
            ("prompt-b", "model-b"),
            ("prompt-c", "model-a"),
        ]
        assert final.final_state == {
            "done": ["a", "b", "c"], "current": None, "todo": [],
            "status": STATUS_COMPLETED, "revision": 6,
        }
        assert (run_dir / "results" / "b.txt").read_text() == "RB"
        assert (run_dir / "results" / "c.txt").read_text() == "RC"

    def test_completed_work_and_queue_order_preserved(self, tmp_path):
        """The recovered run keeps the completed history in order and the
        remaining queue in order; execution finishes in that same order."""
        job_types = {
            "a": "research", "b": "research",
            "c": "synthesis", "d": "research",
        }
        magazine_path = tmp_path / "magazine.json"
        _write_magazine(magazine_path, ("a", "b", "c", "d"), job_types)
        model_map = {"research": "model-a", "synthesis": "model-b"}
        state_path, lock_path, run_dir = make_paths(tmp_path)
        save_run_state(state_path, {
            "done": ["a", "c"], "current": "b", "todo": ["d"],
            "status": STATUS_HALTED, "revision": 2,
        })
        recovered = recovery_action.persist_recover_halted(state_path, lock_path, OWNER)
        # both the history and the queue are preserved exactly, in order
        assert recovered["done"] == ["a", "c"]
        assert recovered["current"] == "b"
        assert recovered["todo"] == ["d"]
        final = asyncio.run(run_loop.run_all_cartridges(
            str(magazine_path), state_path, lock_path,
            OWNER, model_map,
            _dispatch_canned("RB", "RD"),
        ))
        # final done is the preserved history in order, plus the queue
        # finished in its preserved order
        assert final.final_state["done"] == ["a", "c", "b", "d"]
        assert final.final_state["status"] == STATUS_COMPLETED
        # the interrupted 'b' ran before the queued 'd'
        results = {p.stem: (run_dir / "results" / p).read_text()
                   for p in (run_dir / "results" / "b.txt",
                             run_dir / "results" / "d.txt")}
        assert results == {"b": "RB", "d": "RD"}


# ---------------------------------------------------------------------------
# Revision progression
# ---------------------------------------------------------------------------

class TestRevisionProgression:
    def test_revision_increments_by_exactly_one_on_recovery(self, tmp_path):
        state_path, lock_path, _ = make_paths(tmp_path)
        save_run_state(state_path, {
            "done": ["a"], "current": "b", "todo": ["c"],
            "status": STATUS_HALTED, "revision": 4,
        })
        recovered = recovery_action.persist_recover_halted(state_path, lock_path, OWNER)
        assert recovered["revision"] == 5
        assert load_run_state(state_path)["revision"] == 5

    def test_revision_continues_after_recovery_not_reset(self, tmp_path):
        """The run's revision progression continues from the recovered
        state (it is never reset to 0/1 by the operator action)."""
        job_types = {"a": "research", "b": "synthesis", "c": "research"}
        magazine_path = tmp_path / "magazine.json"
        _write_magazine(magazine_path, ("a", "b", "c"), job_types)
        state_dir = tmp_path / "state"
        state_path, lock_path, run_dir = make_paths(tmp_path)
        model_map = {"research": "model-a", "synthesis": "model-b"}

        # build up a real revision through a genuine admission + one completion
        run_preparation.prepare_run(str(magazine_path), str(state_dir), OWNER)
        asyncio.run(orchestration.execute_one_cartridge(
            str(magazine_path), state_path, lock_path,
            OWNER, model_map, _dispatch_canned("RA"),
        ))
        run_action.persist_begin_next_cartridge(state_path, lock_path, OWNER)
        recovery_action.persist_uncertain_halt(state_path, lock_path, OWNER)
        halted = load_run_state(state_path)
        assert halted["revision"] == 3 and halted["status"] == STATUS_HALTED

        recovery_action.persist_recover_halted(state_path, lock_path, OWNER)
        running = load_run_state(state_path)
        # recovery is exactly one revision ahead of the halt; then the loop
        # continues from there (5, 6, 7), never resetting to a low value
        assert running["revision"] == halted["revision"] + 1
        final = asyncio.run(run_loop.run_all_cartridges(
            str(magazine_path), state_path, lock_path,
            OWNER, model_map,
            _dispatch_canned("RB", "RC"),
        ))
        assert final.final_state["revision"] == 7
        assert all(rev >= 3
                   for rev in [halted["revision"], running["revision"],
                               final.final_state["revision"]])


# ---------------------------------------------------------------------------
# Locking / concurrent access
# ---------------------------------------------------------------------------

class TestLockingAndConcurrentAccess:
    def test_preexisting_lock_blocks_recovery_and_state_stays_halted(self, tmp_path):
        state_path, lock_path, _ = make_paths(tmp_path)
        save_run_state(state_path, {
            "done": ["a"], "current": "b", "todo": ["c"],
            "status": STATUS_HALTED, "revision": 2,
        })
        before_bytes = Path(state_path).read_bytes()

        # another owner holds the run lock
        acquire_run_lock(lock_path, "other-owner")
        try:
            with pytest.raises(MagazineLockError, match="run lock already held"):
                recovery_action.persist_recover_halted(
                    state_path, lock_path, OWNER,
                )
        finally:
            release_run_lock(lock_path, "other-owner")

        # blocked recovery never mutated the state or the lock
        assert Path(state_path).read_bytes() == before_bytes
        # the lock is released, and recovery then succeeds
        assert not Path(lock_path).exists()
        recovered = recovery_action.persist_recover_halted(
            state_path, lock_path, OWNER,
        )
        assert recovered["status"] == STATUS_RUNNING
        assert recovered["revision"] == 3
        assert not Path(lock_path).exists()

    def test_two_operations_serialized_by_lock(self, tmp_path):
        """Two recovery actions (simulated concurrent access) are serialized
        by the exclusive run lock; the last one wins and both release the
        lock on completion."""
        state_path, lock_path, _ = make_paths(tmp_path)
        save_run_state(state_path, {
            "done": ["a"], "current": "b", "todo": ["c"],
            "status": STATUS_HALTED, "revision": 2,
        })
        # first operator recovers
        first = recovery_action.persist_recover_halted(state_path, lock_path, "op-1")
        assert first["status"] == STATUS_RUNNING and first["revision"] == 3
        # a second operator can now recover the now-RUNNING state only if it
        # is HALTED again; simulate by re-halting
        recovery_action.persist_uncertain_halt(state_path, lock_path, "op-2")
        second = recovery_action.persist_recover_halted(state_path, lock_path, "op-3")
        assert second["status"] == STATUS_RUNNING and second["revision"] == 4
        assert not Path(lock_path).exists()


# ---------------------------------------------------------------------------
# Invalid recovery state
# ---------------------------------------------------------------------------

class TestInvalidRecoveryState:
    def test_non_halted_states_rejected_bytes_unchanged(self, tmp_path):
        """READY / COMPLETED / RUNNING states are not recoverable; the
        persisted bytes are unchanged and the lock is released."""
        state_path, lock_path, _ = make_paths(tmp_path)
        states = {
            STATUS_READY: {
                "done": ["a"], "current": None, "todo": ["b"],
                "status": STATUS_READY, "revision": 1,
            },
            STATUS_COMPLETED: {
                "done": ["a"], "current": None, "todo": [],
                "status": STATUS_COMPLETED, "revision": 3,
            },
            STATUS_RUNNING: {
                "done": ["a"], "current": "b", "todo": ["c"],
                "status": STATUS_RUNNING, "revision": 2,
            },
        }
        for status, state in states.items():
            save_run_state(state_path, state)
            before = Path(state_path).read_bytes()
            with pytest.raises(RecoveryTransitionError):
                recovery_action.persist_recover_halted(state_path, lock_path, OWNER)
            assert Path(state_path).read_bytes() == before, (
                f"state mutated for status {status!r}"
            )
            assert not Path(lock_path).exists()

    def test_halted_without_current_rejected_bytes_unchanged(self, tmp_path):
        state_path, lock_path, _ = make_paths(tmp_path)
        state = {
            "done": ["a"], "current": None, "todo": ["b"],
            "status": STATUS_HALTED, "revision": 2,
        }
        save_run_state(state_path, state)
        before = Path(state_path).read_bytes()
        with pytest.raises(
            RecoveryTransitionError,
            match="without a current cartridge",
        ):
            recovery_action.persist_recover_halted(state_path, lock_path, OWNER)
        assert Path(state_path).read_bytes() == before
        assert not Path(lock_path).exists()

    def test_malformed_state_rejected_bytes_unchanged(self, tmp_path):
        """A structurally malformed HALTED state raises the state error;
        bytes unchanged, lock released."""
        state_path, lock_path, _ = make_paths(tmp_path)
        Path(state_path).write_text(
            json.dumps({"done": ["a"], "current": "b", "status": "BOGUS"}),
            encoding="utf-8",
        )
        before = Path(state_path).read_bytes()
        with pytest.raises(MagazineStateError):
            recovery_action.persist_recover_halted(state_path, lock_path, OWNER)
        assert Path(state_path).read_bytes() == before
        assert not Path(lock_path).exists()

    def test_malformed_json_rejected_bytes_unchanged(self, tmp_path):
        state_path, lock_path, _ = make_paths(tmp_path)
        Path(state_path).write_text("not valid json {", encoding="utf-8")
        before = Path(state_path).read_bytes()
        with pytest.raises(json.JSONDecodeError):
            recovery_action.persist_recover_halted(state_path, lock_path, OWNER)
        assert Path(state_path).read_bytes() == before
        assert not Path(lock_path).exists()

    def test_rejection_releases_lock(self, tmp_path):
        """When recovery is rejected (raises), the lock must be released so
        a later valid operation can acquire it."""
        state_path, lock_path, _ = make_paths(tmp_path)
        save_run_state(state_path, {
            "done": ["a"], "current": None, "todo": ["b"],
            "status": STATUS_HALTED, "revision": 2,
        })
        # the HALTED state has no current cartridge -> recovery is rejected
        with pytest.raises(RecoveryTransitionError):
            recovery_action.persist_recover_halted(
                state_path, lock_path, "owner-x",
            )
        # the lock is released on rejection
        assert not Path(lock_path).exists()
        # and a valid recovery still works (fix the state, then recover it)
        save_run_state(state_path, {
            "done": ["a"], "current": "b", "todo": ["c"],
            "status": STATUS_HALTED, "revision": 2,
        })
        recovered = recovery_action.persist_recover_halted(
            state_path, lock_path, OWNER,
        )
        assert recovered["status"] == STATUS_RUNNING
        assert not Path(lock_path).exists()
