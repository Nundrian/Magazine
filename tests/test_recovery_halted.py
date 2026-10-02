"""Integration regression tests for explicit operator-approved recovery of
HALTED Magazine runs, through the *public* runner.

These tests drive the real operator-recovery path end-to-end:

    RUNNING  --persist_uncertain_halt-->  HALTED
    HALTED   --persist_recover_halted--> READY    (explicit operator action;
                                                      re-admissible state)
    READY    --run_magazine / persist_admit_run--> RUNNING
    RUNNING  --run_loop.run_all_cartridges--> COMPLETED

proving that the recovered run is re-admissible by the normal public runner
(:func:`magazine.async_runner.run_magazine`) and that the interrupted cartridge
is the first one retried (rather than being advanced past), while completed
work, queue ordering, atomic persistence, lock-based serialization and revision
progression all hold through the recovery.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, call, patch

import pytest

from magazine import (
    async_runner,
    orchestration,
    recovery_action,
    run_action,
    run_preparation,
)
from magazine.recovery_transition import RecoveryTransitionError
from magazine.run_lock import release_run_lock, MagazineLockError
from magazine.state import (
    STATUS_COMPLETED,
    STATUS_HALTED,
    STATUS_READY,
    STATUS_RUNNING,
    MagazineStateError,
)
from magazine.state_store import load_run_state


OWNER = "operator-1"
MODEL_MAP = {"research": "model-a", "synthesis": "model-b"}


def _write_magazine(path: Path, cartridges, job_types: dict[str, str]) -> Path:
    """Write a minimal valid magazine document with the given cartridges."""
    document = {
        "schema_version": "1.0",
        "cartridges": [
            {
                "id": cartridge_id,
                "job_type": job_types[cartridge_id],
                "prompt": f"prompt-{cartridge_id}",
            }
            for cartridge_id in cartridges
        ],
    }
    Path(path).write_text(json.dumps(document))
    return Path(path)


def make_paths(tmp_path):
    state_dir = tmp_path / "state"
    run_dir = state_dir / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    return state_dir, str(run_dir / "state.json"), str(run_dir / ".lock"), run_dir


def _dispatch_canned(*outputs) -> AsyncMock:
    """Return a dispatch mock that returns *outputs* in order."""
    return AsyncMock(side_effect=list(outputs))


def _write_state(state_path: str, state: dict) -> None:
    Path(state_path).write_text(json.dumps(state))


def _read_state(state_path: str) -> dict:
    return json.loads(Path(state_path).read_text())


def _run_public(magazine_path, state_dir, dispatch):
    """Run the recovered / admitted state through the normal public runner."""
    return asyncio.run(
        async_runner.run_magazine(
            str(magazine_path), str(state_dir), OWNER, MODEL_MAP, dispatch,
        )
    )


# ---------------------------------------------------------------------------
# Recovery produces a re-admissible READY state
# ---------------------------------------------------------------------------

class TestRecoveryProducesReAdmissibleState:
    def test_recover_halted_produces_ready_state(self, tmp_path):
        """A valid HALTED run recovers to a re-admissible READY state: the
        interrupted cartridge is moved to the front of ``todo`` (retried first),
        ``current`` is ``None``, and ``revision`` increments by one."""
        state_dir, state_path, lock_path, run_dir = make_paths(tmp_path)
        _write_state(state_path, {
            "done": ["a"],
            "current": "b",
            "todo": ["c"],
            "status": STATUS_HALTED,
            "revision": 2,
        })
        recovered = recovery_action.persist_recover_halted(state_path, lock_path, OWNER)
        assert recovered == {
            "done": ["a"],
            "current": None,
            "todo": ["b", "c"],
            "status": STATUS_READY,
            "revision": 3,
        }
        assert _read_state(state_path) == recovered

    def test_interrupted_cartridge_is_first_in_todo(self, tmp_path):
        """The interrupted cartridge becomes the head of the ``todo`` queue and
        the rest of the queue keep their relative order."""
        state_dir, state_path, lock_path, run_dir = make_paths(tmp_path)
        _write_state(state_path, {
            "done": ["a"],
            "current": "b",
            "todo": ["c", "d"],
            "status": STATUS_HALTED,
            "revision": 2,
        })
        recovered = recovery_action.persist_recover_halted(state_path, lock_path, OWNER)
        assert recovered["todo"] == ["b", "c", "d"]
        assert recovered["current"] is None

    def test_halted_final_cartridge_empty_todo_recovers_to_ready(self, tmp_path):
        """A HALTED run on its final cartridge (empty ``todo``) recovers to
        READY with the interrupted cartridge as the single remaining item."""
        state_dir, state_path, lock_path, run_dir = make_paths(tmp_path)
        _write_state(state_path, {
            "done": ["a", "b"],
            "current": "c",
            "todo": [],
            "status": STATUS_HALTED,
            "revision": 1,
        })
        recovered = recovery_action.persist_recover_halted(state_path, lock_path, OWNER)
        assert recovered == {
            "done": ["a", "b"],
            "current": None,
            "todo": ["c"],
            "status": STATUS_READY,
            "revision": 2,
        }

    def test_malformed_state_propagates_and_releases(self, tmp_path):
        """Malformed persisted state propagates ``MagazineStateError`` and the
        lock is released."""
        state_dir, state_path, lock_path, run_dir = make_paths(tmp_path)
        _write_state(state_path, {"status": "BOGUS"})
        with pytest.raises(MagazineStateError):
            recovery_action.persist_recover_halted(state_path, lock_path, OWNER)
        assert not Path(lock_path).exists()


# ---------------------------------------------------------------------------
# Public runner re-admits a recovered run and retries the interrupted cartridge
# ---------------------------------------------------------------------------

class TestPublicRunnerRecovery:
    def test_public_runner_recovers_halted_and_completes(self, tmp_path):
        """Required regression: a HALTED run (a done, b interrupted, c queued)
        is explicitly recovered, then re-admitted and completed by the normal
        public runner — b is retried first, c follows, and the run ends
        COMPLETED."""
        magazine_path = _write_magazine(
            tmp_path / "magazine.json", ("a", "b", "c"),
            {"a": "research", "b": "synthesis", "c": "research"},
        )
        state_dir, state_path, lock_path, run_dir = make_paths(tmp_path)
        _write_state(state_path, {
            "done": ["a"],
            "current": "b",
            "todo": ["c"],
            "status": STATUS_HALTED,
            "revision": 2,
        })
        dispatch = _dispatch_canned("RB", "RC")

        # Explicit operator-approved recovery (HALTED -> READY, re-admissible).
        recovered = recovery_action.persist_recover_halted(state_path, lock_path, OWNER)
        assert recovered == {
            "done": ["a"],
            "current": None,
            "todo": ["b", "c"],
            "status": STATUS_READY,
            "revision": 3,
        }

        # The normal public runner re-admits and completes the run.
        final_state = _run_public(magazine_path, state_dir, dispatch)
        assert final_state == {
            "done": ["a", "b", "c"],
            "current": None,
            "todo": [],
            "status": STATUS_COMPLETED,
            "revision": 7,
        }

        # The interrupted cartridge b was dispatched first, c after it.
        assert dispatch.call_count == 2
        assert dispatch.await_args_list == [
            call("prompt-b", "model-b"),
            call("prompt-c", "model-a"),
        ]

        # Results persisted for the retried and following cartridges.
        assert (run_dir / "results" / "b.txt").read_text() == "RB"
        assert (run_dir / "results" / "c.txt").read_text() == "RC"

    def test_public_runner_retries_interrupted_cartridge_not_next(self, tmp_path):
        """After recovery the public runner retries the *interrupted* cartridge
        (b) rather than the next queued one (c)."""
        magazine_path = _write_magazine(
            tmp_path / "magazine.json", ("a", "b", "c"),
            {"a": "research", "b": "synthesis", "c": "research"},
        )
        state_dir, state_path, lock_path, run_dir = make_paths(tmp_path)
        _write_state(state_path, {
            "done": ["a"],
            "current": "b",
            "todo": ["c"],
            "status": STATUS_HALTED,
            "revision": 2,
        })
        dispatch = _dispatch_canned("RB", "RC")

        recovery_action.persist_recover_halted(state_path, lock_path, OWNER)
        assert _read_state(state_path)["current"] is None
        # b must be the very first dispatch made by the public runner.
        final_state = _run_public(magazine_path, state_dir, dispatch)
        first = dispatch.await_args_list[0]
        assert first == call("prompt-b", "model-b")
        assert final_state["status"] == STATUS_COMPLETED
        assert (run_dir / "results" / "b.txt").read_text() == "RB"

    def test_public_runner_preserves_completed_work_and_queue_order(self, tmp_path):
        """With out-of-order history (a and c done, b interrupted, d queued)
        the public runner completes b then d, preserving completed work and
        queue ordering."""
        magazine_path = _write_magazine(
            tmp_path / "magazine.json", ("a", "b", "c", "d"),
            {"a": "research", "b": "synthesis", "c": "research", "d": "synthesis"},
        )
        state_dir, state_path, lock_path, run_dir = make_paths(tmp_path)
        _write_state(state_path, {
            "done": ["a", "c"],
            "current": "b",
            "todo": ["d"],
            "status": STATUS_HALTED,
            "revision": 2,
        })
        dispatch = _dispatch_canned("RB", "RD")

        recovered = recovery_action.persist_recover_halted(state_path, lock_path, OWNER)
        assert recovered == {
            "done": ["a", "c"],
            "current": None,
            "todo": ["b", "d"],
            "status": STATUS_READY,
            "revision": 3,
        }

        final_state = _run_public(magazine_path, state_dir, dispatch)
        assert final_state["done"] == ["a", "c", "b", "d"]
        assert final_state["status"] == STATUS_COMPLETED
        assert final_state["revision"] == 7
        assert dispatch.await_args_list == [
            call("prompt-b", "model-b"),
            call("prompt-d", "model-b"),
        ]
        assert (run_dir / "results" / "b.txt").read_text() == "RB"
        assert (run_dir / "results" / "d.txt").read_text() == "RD"

    def test_public_runner_halted_last_cartridge_completes(self, tmp_path):
        """A HALTED run on its final cartridge recovers and the public runner
        completes it (the recovered state must be re-admissible)."""
        magazine_path = _write_magazine(
            tmp_path / "magazine.json", ("a", "b", "c"),
            {"a": "research", "b": "synthesis", "c": "research"},
        )
        state_dir, state_path, lock_path, run_dir = make_paths(tmp_path)
        _write_state(state_path, {
            "done": ["a", "b"],
            "current": "c",
            "todo": [],
            "status": STATUS_HALTED,
            "revision": 2,
        })
        dispatch = _dispatch_canned("RC")

        recovered = recovery_action.persist_recover_halted(state_path, lock_path, OWNER)
        assert recovered == {
            "done": ["a", "b"],
            "current": None,
            "todo": ["c"],
            "status": STATUS_READY,
            "revision": 3,
        }

        final_state = _run_public(magazine_path, state_dir, dispatch)
        assert final_state == {
            "done": ["a", "b", "c"],
            "current": None,
            "todo": [],
            "status": STATUS_COMPLETED,
            "revision": 5,
        }
        assert dispatch.await_args_list == [call("prompt-c", "model-a")]
        assert (run_dir / "results" / "c.txt").read_text() == "RC"


# ---------------------------------------------------------------------------
# Full operator journey: admission, run, halt, recovery, public re-admission
# ---------------------------------------------------------------------------

class TestFullOperatorJourney:
    def test_full_journey_admit_run_halt_recover_public_complete(self, tmp_path):
        """End-to-end operator journey: a fresh run halts mid-b, is explicitly
        recovered, then re-admitted and completed by the public runner.  The
        interrupted cartridge b is retried first after a."""
        magazine_path = _write_magazine(
            tmp_path / "magazine.json", ("a", "b", "c"),
            {"a": "research", "b": "synthesis", "c": "research"},
        )
        state_dir, state_path, lock_path, run_dir = make_paths(tmp_path)
        dispatch = _dispatch_canned("RA", "RB", "RC")

        # Fresh admission -> RUNNING on a.
        prepared = run_preparation.prepare_run(str(magazine_path), str(state_dir), OWNER)
        assert prepared.state["status"] == STATUS_RUNNING
        assert prepared.state["current"] == "a"
        assert prepared.state["revision"] == 1
        assert prepared.state_path == state_path
        assert prepared.lock_path == lock_path

        # Run a -> READY.
        result_a = asyncio.run(
            orchestration.execute_one_cartridge(
                str(magazine_path), state_path, lock_path, OWNER, MODEL_MAP, dispatch,
            )
        )
        assert result_a.cartridge_id == "a"
        assert result_a.state["status"] == STATUS_READY
        assert result_a.state["done"] == ["a"]
        assert result_a.state["revision"] == 2

        # Begin b -> RUNNING.
        running_b = run_action.persist_begin_next_cartridge(state_path, lock_path, OWNER)
        assert running_b["status"] == STATUS_RUNNING
        assert running_b["current"] == "b"
        assert running_b["todo"] == ["c"]
        assert running_b["revision"] == 3

        # Explicitly halt -> HALTED (revision preserved).
        halted = recovery_action.persist_uncertain_halt(state_path, lock_path, OWNER)
        assert load_run_state(state_path) == {
            "done": ["a"],
            "current": "b",
            "todo": ["c"],
            "status": STATUS_HALTED,
            "revision": 3,
        }
        assert halted["revision"] == 3

        # Operator-approved recovery -> READY (re-admissible).
        recovered = recovery_action.persist_recover_halted(state_path, lock_path, OWNER)
        assert recovered == {
            "done": ["a"],
            "current": None,
            "todo": ["b", "c"],
            "status": STATUS_READY,
            "revision": 4,
        }

        # Public runner re-admits and completes: b then c.
        final_state = _run_public(magazine_path, state_dir, dispatch)
        assert final_state == {
            "done": ["a", "b", "c"],
            "current": None,
            "todo": [],
            "status": STATUS_COMPLETED,
            "revision": 8,
        }

        # Dispatch order spans both phases: a (pre-halt), then b, c (post-recovery).
        assert dispatch.call_count == 3
        assert dispatch.await_args_list == [
            call("prompt-a", "model-a"),
            call("prompt-b", "model-b"),
            call("prompt-c", "model-a"),
        ]

        assert (run_dir / "results" / "a.txt").read_text() == "RA"
        assert (run_dir / "results" / "b.txt").read_text() == "RB"
        assert (run_dir / "results" / "c.txt").read_text() == "RC"


# ---------------------------------------------------------------------------
# Revision progression through recovery
# ---------------------------------------------------------------------------

class TestRevisionProgression:
    def test_revision_increments_by_exactly_one_on_recovery(self, tmp_path):
        """Recovery increments revision by exactly one (no reset, no double
        increment)."""
        state_dir, state_path, lock_path, run_dir = make_paths(tmp_path)
        _write_state(state_path, {
            "done": ["a"],
            "current": "b",
            "todo": ["c"],
            "status": STATUS_HALTED,
            "revision": 4,
        })
        recovered = recovery_action.persist_recover_halted(state_path, lock_path, OWNER)
        assert recovered["status"] == STATUS_READY
        assert recovered["revision"] == 5

    def test_revision_continues_after_recovery_not_reset(self, tmp_path):
        """Recovery's revision is one past the halted revision, and the
        public runner's subsequent execution continues from there (nothing
        resets to 0)."""
        magazine_path = _write_magazine(
            tmp_path / "magazine.json", ("a", "b", "c"),
            {"a": "research", "b": "synthesis", "c": "research"},
        )
        state_dir, state_path, lock_path, run_dir = make_paths(tmp_path)
        dispatch = _dispatch_canned("RA", "RB", "RC")

        prepared = run_preparation.prepare_run(str(magazine_path), str(state_dir), OWNER)
        asyncio.run(
            orchestration.execute_one_cartridge(
                str(magazine_path), state_path, lock_path, OWNER, MODEL_MAP, dispatch,
            )
        )
        run_action.persist_begin_next_cartridge(state_path, lock_path, OWNER)
        halted = recovery_action.persist_uncertain_halt(state_path, lock_path, OWNER)
        recovered = recovery_action.persist_recover_halted(state_path, lock_path, OWNER)
        final_state = _run_public(magazine_path, state_dir, dispatch)

        assert recovered["revision"] == halted["revision"] + 1
        assert final_state["status"] == STATUS_COMPLETED
        assert final_state["done"] == ["a", "b", "c"]
        assert final_state["revision"] == 8
        assert all(rev >= 3 for rev in [halted["revision"],
                                        recovered["revision"],
                                        final_state["revision"]])


# ---------------------------------------------------------------------------
# Locking and concurrent access
# ---------------------------------------------------------------------------

class TestLockingAndConcurrentAccess:
    def test_preexisting_lock_blocks_recovery_and_state_stays_halted(self, tmp_path):
        """A run lock held by another owner blocks recovery; the persisted
        state stays HALTED (unmodified) and the lock error is raised."""
        state_dir, state_path, lock_path, run_dir = make_paths(tmp_path)
        state = {
            "done": ["cart-1"],
            "current": "cart-2",
            "todo": ["cart-3"],
            "status": STATUS_HALTED,
            "revision": 2,
        }
        _write_state(state_path, state)
        Path(lock_path).write_text("other-owner")
        with pytest.raises(MagazineLockError, match="run lock already held"):
            recovery_action.persist_recover_halted(state_path, lock_path, OWNER)
        assert _read_state(state_path) == state

        # After the other process releases the lock (ownership must match the
        # owner that created it), recovery proceeds and yields a re-admissible
        # READY state.
        release_run_lock(lock_path, "other-owner")
        recovered = recovery_action.persist_recover_halted(state_path, lock_path, OWNER)
        assert recovered == {
            "done": ["cart-1"],
            "current": None,
            "todo": ["cart-2", "cart-3"],
            "status": STATUS_READY,
            "revision": 3,
        }
        assert not Path(lock_path).exists()

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

        # Re-admit (READY -> RUNNING) so we can halt again.
        running = run_action.persist_begin_next_cartridge(state_path, lock_path, OWNER)
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
        assert load_run_state(state_path) == recovered2


# ---------------------------------------------------------------------------
# Invalid recovery state (rejected, bytes unchanged)
# ---------------------------------------------------------------------------

class TestInvalidRecoveryState:
    @pytest.mark.parametrize("state", [
        {
            "done": ["cart-1"],
            "current": None,
            "todo": ["cart-2"],
            "status": STATUS_READY,
            "revision": 1,
        },
        {
            "done": ["cart-1"],
            "current": None,
            "todo": [],
            "status": STATUS_COMPLETED,
            "revision": 1,
        },
        {
            "done": ["cart-1"],
            "current": "cart-2",
            "todo": ["cart-3"],
            "status": STATUS_RUNNING,
            "revision": 1,
        },
    ])
    def test_non_halted_states_rejected_bytes_unchanged(self, tmp_path, state):
        """Recovery is only valid on HALTED states; any other valid state is
        rejected and the file bytes stay identical."""
        state_dir, state_path, lock_path, run_dir = make_paths(tmp_path)
        _write_state(state_path, state)
        original_bytes = Path(state_path).read_bytes()
        with pytest.raises(RecoveryTransitionError):
            recovery_action.persist_recover_halted(state_path, lock_path, OWNER)
        assert Path(state_path).read_bytes() == original_bytes

    def test_halted_without_current_rejected_bytes_unchanged(self, tmp_path):
        """A HALTED state with no current cartridge has no interrupted work
        position to retry; it is rejected and the file bytes stay identical."""
        state_dir, state_path, lock_path, run_dir = make_paths(tmp_path)
        state = {
            "done": ["cart-1"],
            "current": None,
            "todo": ["cart-2"],
            "status": STATUS_HALTED,
            "revision": 2,
        }
        _write_state(state_path, state)
        original_bytes = Path(state_path).read_bytes()
        with pytest.raises(RecoveryTransitionError,
                          match="without a current cartridge"):
            recovery_action.persist_recover_halted(state_path, lock_path, OWNER)
        assert Path(state_path).read_bytes() == original_bytes

    def test_malformed_state_rejected_bytes_unchanged(self, tmp_path):
        """A malformed state is rejected (``MagazineStateError``) and the file
        bytes stay identical."""
        state_dir, state_path, lock_path, run_dir = make_paths(tmp_path)
        state = {"done": [], "current": None, "todo": ["cart-1"],
                 "status": "BOGUS", "revision": 0}
        _write_state(state_path, state)
        original_bytes = Path(state_path).read_bytes()
        with pytest.raises(MagazineStateError):
            recovery_action.persist_recover_halted(state_path, lock_path, OWNER)
        assert Path(state_path).read_bytes() == original_bytes

    def test_malformed_json_rejected_bytes_unchanged(self, tmp_path):
        """Unparseable JSON is rejected and the file bytes stay identical."""
        state_dir, state_path, lock_path, run_dir = make_paths(tmp_path)
        Path(state_path).write_text("not valid json")
        original_bytes = Path(state_path).read_bytes()
        with pytest.raises(json.JSONDecodeError):
            recovery_action.persist_recover_halted(state_path, lock_path, OWNER)
        assert Path(state_path).read_bytes() == original_bytes

    def test_rejection_releases_lock(self, tmp_path):
        """A rejected recovery (HALTED without current) still releases the lock
        so a subsequent call can acquire it."""
        state_dir, state_path, lock_path, run_dir = make_paths(tmp_path)
        state = {
            "done": ["cart-1"],
            "current": None,
            "todo": ["cart-2"],
            "status": STATUS_HALTED,
            "revision": 2,
        }
        _write_state(state_path, state)
        with pytest.raises(RecoveryTransitionError):
            recovery_action.persist_recover_halted(state_path, lock_path, OWNER)
        assert not Path(lock_path).exists()
        # A later recovery of a valid HALTED state must succeed (lock released).
        _write_state(state_path, {
            "done": ["cart-1"],
            "current": "cart-2",
            "todo": ["cart-3"],
            "status": STATUS_HALTED,
            "revision": 2,
        })
        recovered = recovery_action.persist_recover_halted(state_path, lock_path, OWNER)
        assert recovered["status"] == STATUS_READY
