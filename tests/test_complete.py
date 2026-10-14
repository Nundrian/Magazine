"""Focused tests for magazine.complete (stage-3 composition).

Exactly eight tests:
  1. A valid RUNNING run with remaining todos is completed end-to-end (READY).
  2. A valid RUNNING run completing the last cartridge is COMPLETED end-to-end.
  3. A valid but non-RUNNING (READY) run fails completion safely.
  4. A mismatching expected-current ID is rejected (RunIdentityError), leaving
     the persisted state byte-identical and the lock released.
  5. A matching explicit expected-current ID completes normally (READY): the
     matching cartridge is appended to done, current is cleared, todo is
     preserved, revision increments by one, and the lock is released.
  6. Fence (Slice 2): no claim -> ExecutionOwnershipError, state untouched.
  7. Fence (Slice 2): writer claiming a different attempt -> rejected.
  8. Fence (Slice 2): writer whose runtime_id does not match -> rejected.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from magazine import complete, state_store
from magazine.execution_meta import (
    ExecutionMetadata,
    save_execution_metadata,
    utc_now_iso,
)
from magazine.paths import execution_file_path
from magazine.runtime_identity import RuntimeIdentity
from magazine.schema import ExecutionOwnershipError
from magazine.state import (
    STATUS_COMPLETED,
    STATUS_READY,
    STATUS_RUNNING,
)
from magazine.run_action import RunIdentityError
from magazine.run_transition import RunTransitionError


# Deterministic writer identity (Slice 2) carried by every seeded claim.
_TEST_WRITER = RuntimeIdentity(runtime_id="0" * 32, pid=42, start_ticks=1000)
# A *stale / different-generation* writer, for ownership-rejection tests.
_TEST_STALE = RuntimeIdentity(runtime_id="1" * 32, pid=99, start_ticks=1)


def _seed_claim(state_path: Path, attempt: int = 1, writer: RuntimeIdentity | None = None) -> None:
    """Seed the Slice-2 fence prerequisite (``<run_dir>/execution.json``) with a
    claim that matches the writer's ``attempt`` + ``runtime_id``."""
    writer = writer or _TEST_WRITER
    run_dir = state_path.parent
    exec_path = execution_file_path(run_dir)
    save_execution_metadata(
        str(exec_path),
        ExecutionMetadata(
            attempt=attempt,
            runtime_id=writer.runtime_id,
            pid=writer.pid,
            start_ticks=writer.start_ticks,
            created_at=utc_now_iso(),
            halted_by=None,
            halted_at=None,
            halt_reason=None,
        ),
    )


# ---------------------------------------------------------------------------
# complete_current_cartridge — successful path
# ---------------------------------------------------------------------------


def test_complete_current_cartridge_running_with_todo_becomes_ready(
    tmp_path: Path
) -> None:
    """A valid RUNNING run with remaining todos is completed:
    READY, current cleared, previous current appended to done, remaining todo
    preserved, revision incremented by one, state persisted, lock released."""
    state_path = tmp_path / "state.json"
    lock_path = tmp_path / "run.lock"
    owner_id = "owner-1"

    # 1. Start from a valid RUNNING run: one in-flight cartridge (current) and
    #    more work still queued (todo).
    running = {
        "done": [],
        "current": "cart-1",
        "todo": ["cart-2"],
        "status": STATUS_RUNNING,
        "revision": 1,
    }
    # 2. Persist the starting state via the real store.
    state_store.save_run_state(str(state_path), running)
    # 3. Seed the matching execution-ownership claim (Slice 2 fence).
    _seed_claim(state_path, attempt=1, writer=_TEST_WRITER)

    # 4. Run the stage-3 completion against the real guarded action
    #    (magazine.complete.complete_current_cartridge -> run_action
    #    .persist_complete_current_cartridge -> run_lock + state_store +
    #    run_transition). No mocking of the guarded completion action.
    result = complete.complete_current_cartridge(
        str(state_path), lock_path, owner_id, 1, _TEST_WRITER
    )

    # 5. The returned state is exactly the READY state:
    expected = {
        "done": ["cart-1"],      # previous current appended to done
        "current": None,         # current cleared
        "todo": ["cart-2"],      # remaining todo preserved unchanged
        "status": STATUS_READY,  # run advances to READY (work remains)
        "revision": 2,           # incremented by exactly one
    }
    assert result == expected

    # 6. The on-disk state is exactly the returned state.
    assert state_store.load_run_state(str(state_path)) == expected

    # 7. The lock is released — no lock file remains.
    assert not lock_path.exists()


# ---------------------------------------------------------------------------
# complete_current_cartridge — successful path (last cartridge)
# ---------------------------------------------------------------------------


def test_complete_current_cartridge_last_cartridge_becomes_completed(
    tmp_path: Path
) -> None:
    """A valid RUNNING run with empty todo (current is the final cartridge)
    is completed: COMPLETED, current cleared, previous current appended to
    done, todo remains empty, revision incremented by one, state persisted,
    lock released."""
    state_path = tmp_path / "state.json"
    lock_path = tmp_path / "run.lock"
    owner_id = "owner-2"

    # 1. Start from a valid RUNNING run where the current cartridge is the
    #    final one: todo is empty (no work remains beyond current).
    running = {
        "done": ["cart-1"],
        "current": "cart-2",
        "todo": [],
        "status": STATUS_RUNNING,
        "revision": 1,
    }
    # 2. Persist the starting state via the real store.
    state_store.save_run_state(str(state_path), running)
    # 3. Seed the matching execution-ownership claim (Slice 2 fence).
    _seed_claim(state_path, attempt=1, writer=_TEST_WRITER)

    # 4. Run the stage-3 completion against the real guarded action
    #    (magazine.complete.complete_current_cartridge -> run_action
    #    .persist_complete_current_cartridge -> run_lock + state_store +
    #    run_transition). No mocking of the guarded completion action.
    result = complete.complete_current_cartridge(
        str(state_path), lock_path, owner_id, 1, _TEST_WRITER
    )

    # 5. The returned state is exactly the COMPLETED state:
    expected = {
        "done": ["cart-1", "cart-2"],   # previous current appended to done
        "current": None,                 # current cleared
        "todo": [],                      # no work remains; todo left empty
        "status": STATUS_COMPLETED,      # run advances to COMPLETED
        "revision": 2,                   # incremented by exactly one
    }
    assert result == expected

    # 6. The on-disk state is exactly the returned state.
    assert state_store.load_run_state(str(state_path)) == expected

    # 7. The lock is released — no lock file remains.
    assert not lock_path.exists()


# ---------------------------------------------------------------------------
# complete_current_cartridge — failure path (valid but non-RUNNING state)
# ---------------------------------------------------------------------------


def test_complete_current_cartridge_ready_state_fails_safely(
    tmp_path: Path
) -> None:
    """A valid READY run (no cartridge running) cannot be completed: the
    completion action propagates RunTransitionError, leaves the persisted state
    byte-identical, makes no transition, and releases the lock."""
    state_path = tmp_path / "state.json"
    lock_path = tmp_path / "run.lock"
    owner_id = "owner-3"

    # 1. Start from a valid READY run: no current cartridge, work still
    #    queued (todo non-empty), valid revision.
    ready = {
        "done": [],
        "current": None,
        "todo": ["cart-1"],
        "status": STATUS_READY,
        "revision": 0,
    }
    # 2. Persist the starting state via the real store.
    state_store.save_run_state(str(state_path), ready)
    # 3. Seed the matching execution-ownership claim (Slice 2 fence).
    _seed_claim(state_path, attempt=1, writer=_TEST_WRITER)
    # Capture the exact pre-call bytes so the invariant is byte-exact.
    pre_call_bytes = state_path.read_bytes()

    # 4. The lock must not exist before the call.
    assert not lock_path.exists()

    # 5. The stage-3 completion against a non-RUNNING state must propagate
    #    RunTransitionError (no current cartridge can be completed in READY).
    with pytest.raises(RunTransitionError):
        complete.complete_current_cartridge(
            str(state_path), lock_path, owner_id, 1, _TEST_WRITER
        )

    # 6. No completion transition occurred: the persisted state is byte-
    #    identical to its pre-call contents (still READY, unchanged revision).
    assert state_path.read_bytes() == pre_call_bytes
    assert state_store.load_run_state(str(state_path)) == ready

    # 7. The lock is released — no lock file remains (acquired then released
    #    on the failure path via run_action's finally block).
    assert not lock_path.exists()


# ---------------------------------------------------------------------------
# complete_current_cartridge — identity-guard failure path (expected-current
# mismatch)
# ---------------------------------------------------------------------------


def test_complete_current_cartridge_identity_mismatch_leaves_state_unchanged(
    tmp_path: Path
) -> None:
    """A completion whose expected current ID does not match the run's current
    cartridge is rejected by the expected-current identity guard: RunIdentityError
    is raised, naming both the expected ID and the actual persisted current, the
    persisted state remains byte-identical, no completion transition occurs, and
    the lock is released.

    Exercises the real path:
        magazine.complete.complete_current_cartridge
        -> magazine.run_action.persist_complete_current_cartridge
        -> lock acquire -> load under lock -> expected-current comparison.
    No mocking of the guarded completion action."""
    state_path = tmp_path / "state.json"
    lock_path = tmp_path / "run.lock"
    owner_id = "owner-4"

    # 1. Start from a valid RUNNING run: current is "cart-b", with at least one
    #    remaining cartridge still queued, and a valid revision.
    running = {
        "done": ["cart-1"],
        "current": "cart-b",
        "todo": ["cart-c"],
        "status": STATUS_RUNNING,
        "revision": 1,
    }

    # 2. Persist the starting state via the real store.
    state_store.save_run_state(str(state_path), running)
    # 3. Seed the matching execution-ownership claim (Slice 2 fence).
    _seed_claim(state_path, attempt=1, writer=_TEST_WRITER)

    # 4. Capture the exact pre-call bytes and confirm there is no lock file.
    pre_call_bytes = state_path.read_bytes()
    assert not lock_path.exists()

    # 5. Drive the real stage-3 boundary with the mismatching expected ID
    #    ("cart-a") while the persisted current is "cart-b".  No mocking of the
    #    guarded completion action.
    with pytest.raises(RunIdentityError) as excinfo:
        complete.complete_current_cartridge(
            str(state_path), lock_path, owner_id, 1, _TEST_WRITER,
            expected_current_id="cart-a",
        )

    # 6. The identity error names both the expected ID ("cart-a") and the
    #    actual persisted current ("cart-b").
    msg = str(excinfo.value)
    assert "cart-a" in msg, msg
    assert "cart-b" in msg, msg

    # 7. The persisted state is byte-identical to its pre-call contents: no
    #    completion transition occurred.
    assert state_path.read_bytes() == pre_call_bytes

    # 8. Each field is unchanged: current is still "cart-b", and done / todo /
    #    status / revision are untouched (cart-b did not move into done and the
    #    revision did not increment).
    loaded = state_store.load_run_state(str(state_path))
    assert loaded == running
    assert loaded["current"] == "cart-b"
    assert loaded["done"] == ["cart-1"]
    assert loaded["todo"] == ["cart-c"]
    assert loaded["status"] == STATUS_RUNNING
    assert loaded["revision"] == 1

    # 9. The lock is released — no lock file remains (acquired then released
    #    on the failure path via run_action's finally block).
    assert not lock_path.exists()


# ---------------------------------------------------------------------------
# complete_current_cartridge — identity-guard success path (matching expected-
# current ID)
# ---------------------------------------------------------------------------


def test_complete_current_cartridge_matching_expected_id_allows_completion(
    tmp_path: Path
) -> None:
    """An explicit ``expected_current_id`` that EXACTLY matches the run's
    current cartridge passes the expected-current identity guard and the run
    completes normally: READY, current cleared, the matching cartridge appended
    to done, the remaining todo preserved, revision incremented by one, state
    persisted, lock released.

    Exercises the real path:
        magazine.complete.complete_current_cartridge
        -> magazine.run_action.persist_complete_current_cartridge
        -> lock acquire -> load under lock -> expected-current comparison
           (matches) -> existing RUNNING -> READY completion -> atomic persistence.
    No mocking of the guarded completion action."""
    state_path = tmp_path / "state.json"
    lock_path = tmp_path / "run.lock"
    owner_id = "owner-5"

    # 1. Start from a valid persisted RUNNING run: current is "cart-b",
    #    an earlier cartridge already finished in done, one more still queued,
    #    and a valid (non-negative int) revision.
    running = {
        "done": ["cart-1"],      # at least one earlier cartridge
        "current": "cart-b",     # the cartridge to be completed
        "todo": ["cart-c"],      # remaining work after the current cartridge
        "status": STATUS_RUNNING,
        "revision": 1,           # valid revision
    }

    # 2. Persist the starting state via the real store.
    state_store.save_run_state(str(state_path), running)
    # 3. Seed the matching execution-ownership claim (Slice 2 fence).
    _seed_claim(state_path, attempt=1, writer=_TEST_WRITER)

    # 4. Confirm there is no lock file before the call.
    assert not lock_path.exists()

    # 5. Drive the real stage-3 boundary with an explicit expected ID that
    #    exactly matches the persisted current ("cart-b").  No mocking of the
    #    guarded completion action.
    result = complete.complete_current_cartridge(
        str(state_path), lock_path, owner_id, 1, _TEST_WRITER,
        expected_current_id="cart-b",
    )

    # 6. No exception was raised; the returned state is exactly the READY state.
    expected = {
        "done": ["cart-1", "cart-b"],  # matching current appended to done
        "current": None,               # current cleared
        "todo": ["cart-c"],            # remaining todo preserved unchanged
        "status": STATUS_READY,        # run advances to READY (work remains)
        "revision": 2,                 # incremented by exactly one
    }
    assert result == expected

    # 7. Field-level checks: READY, cart-b in done, current None, todo intact.
    assert result["status"] == STATUS_READY
    assert "cart-b" in result["done"]
    assert result["current"] is None
    assert result["todo"] == ["cart-c"]

    # 8. Revision incremented by exactly one.
    assert result["revision"] == running["revision"] + 1

    # 9. The persisted state exactly matches the returned state (on-disk ==
    #    returned, and both are the exact expected READY state).
    assert state_store.load_run_state(str(state_path)) == expected
    assert state_store.load_run_state(str(state_path)) == result

    # 10. The lock is released — no lock file remains (acquired then released
    #     via run_action's finally block on the success path).
    assert not lock_path.exists()


# ---------------------------------------------------------------------------
# complete_current_cartridge — ownership-fence failure paths (Slice 2)
# ---------------------------------------------------------------------------


def test_complete_current_cartridge_missing_claim_raises(tmp_path: Path):
    """Fence: no execution.json claim -> ExecutionOwnershipError; the state is
    left untouched and the lock is released."""
    state_path = tmp_path / "state.json"
    lock_path = tmp_path / "run.lock"
    owner_id = "owner-6"

    running = {
        "done": [],
        "current": "cart-1",
        "todo": ["cart-2"],
        "status": STATUS_RUNNING,
        "revision": 1,
    }
    state_store.save_run_state(str(state_path), running)
    pre_call_bytes = state_path.read_bytes()

    with pytest.raises(ExecutionOwnershipError, match="ownership"):
        complete.complete_current_cartridge(
            str(state_path), lock_path, owner_id, 1, _TEST_WRITER
        )

    assert state_path.read_bytes() == pre_call_bytes
    assert not lock_path.exists()


def test_complete_current_cartridge_stale_attempt_rejected(tmp_path: Path):
    """Fence: a writer claiming a different attempt is refused; the state is
    left untouched and the lock is released."""
    state_path = tmp_path / "state.json"
    lock_path = tmp_path / "run.lock"
    owner_id = "owner-7"

    _seed_claim(state_path, attempt=1, writer=_TEST_WRITER)
    running = {
        "done": [],
        "current": "cart-1",
        "todo": ["cart-2"],
        "status": STATUS_RUNNING,
        "revision": 1,
    }
    state_store.save_run_state(str(state_path), running)
    pre_call_bytes = state_path.read_bytes()

    with pytest.raises(ExecutionOwnershipError, match="mismatch"):
        complete.complete_current_cartridge(
            str(state_path), lock_path, owner_id, 2, _TEST_WRITER
        )

    assert state_path.read_bytes() == pre_call_bytes
    assert not lock_path.exists()


def test_complete_current_cartridge_wrong_runtime_id_rejected(tmp_path: Path):
    """Fence: a writer whose runtime_id does not match the claim is refused;
    the state is left untouched and the lock is released."""
    state_path = tmp_path / "state.json"
    lock_path = tmp_path / "run.lock"
    owner_id = "owner-8"

    _seed_claim(state_path, attempt=1, writer=_TEST_WRITER)
    running = {
        "done": [],
        "current": "cart-1",
        "todo": ["cart-2"],
        "status": STATUS_RUNNING,
        "revision": 1,
    }
    state_store.save_run_state(str(state_path), running)
    pre_call_bytes = state_path.read_bytes()

    # writer_attempt=1 matches the claim but writer_identity.runtime_id does not
    # -> ExecutionOwnershipError (the runtime_id part of the ownership pair).
    with pytest.raises(ExecutionOwnershipError, match="mismatch"):
        complete.complete_current_cartridge(
            str(state_path), lock_path, owner_id, 1, _TEST_STALE
        )

    assert state_path.read_bytes() == pre_call_bytes
    assert not lock_path.exists()
