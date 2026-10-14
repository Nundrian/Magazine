"""Tests for magazine.recovery_action.

Covers the explicit, operator-initiated ``HALTED`` transition of an
in-progress ``RUNNING`` magazine run (``persist_uncertain_halt``) and the
operator-approved recovery of a ``HALTED`` run (``persist_recover_halted``),
including lock acquisition, atomic persistence, and error paths."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from magazine.execution_meta import (
    HALT_REASON_OPERATOR_RECOVERED,
    ExecutionMetadata,
    load_execution_metadata,
    save_execution_metadata,
    utc_now_iso,
)
from magazine.paths import execution_file_path
from magazine.recovery_action import (
    persist_recover_halted,
    persist_uncertain_halt,
)
from magazine.recovery_transition import RecoveryTransitionError
from magazine.run_action import persist_admit_run
from magazine.run_lock import MagazineLockError
from magazine.runtime_identity import get_runtime_identity

RUNNING = {
    "done": ["cart-1"],
    "current": "cart-2",
    "todo": ["cart-3", "cart-4"],
    "status": "RUNNING",
    "revision": 1,
}
READY = {
    "done": [],
    "current": None,
    "todo": ["cart-1"],
    "status": "READY",
    "revision": 0,
}
COMPLETED = {
    "done": ["cart-1"],
    "current": None,
    "todo": [],
    "status": "COMPLETED",
    "revision": 1,
}
HALTED = {
    "done": ["cart-1"],
    "current": "cart-2",
    "todo": ["cart-3"],
    "status": "HALTED",
    "revision": 2,
}


def write_json(path, data):
    Path(path).write_text(json.dumps(data))


def read_json(path):
    return json.loads(Path(path).read_text())


def make_paths(tmp_path):
    return str(tmp_path / "state.json"), str(tmp_path / "lock")


def test_running_persisted_as_halted(tmp_path):
    state_path, lock_path = make_paths(tmp_path)
    write_json(state_path, RUNNING)
    persist_uncertain_halt(state_path, lock_path, "owner-1")
    assert read_json(state_path)["status"] == "HALTED"


def test_done_remains_exact(tmp_path):
    state_path, lock_path = make_paths(tmp_path)
    write_json(state_path, RUNNING)
    persist_uncertain_halt(state_path, lock_path, "owner-1")
    assert read_json(state_path)["done"] == ["cart-1"]


def test_current_remains_exact(tmp_path):
    state_path, lock_path = make_paths(tmp_path)
    write_json(state_path, RUNNING)
    persist_uncertain_halt(state_path, lock_path, "owner-1")
    assert read_json(state_path)["current"] == "cart-2"


def test_todo_remains_exact(tmp_path):
    state_path, lock_path = make_paths(tmp_path)
    write_json(state_path, RUNNING)
    persist_uncertain_halt(state_path, lock_path, "owner-1")
    assert read_json(state_path)["todo"] == ["cart-3", "cart-4"]


def test_returned_state_equals_persisted(tmp_path):
    state_path, lock_path = make_paths(tmp_path)
    write_json(state_path, RUNNING)
    result = persist_uncertain_halt(state_path, lock_path, "owner-1")
    assert result == read_json(state_path)


def test_lock_removed_after_success(tmp_path):
    state_path, lock_path = make_paths(tmp_path)
    write_json(state_path, RUNNING)
    persist_uncertain_halt(state_path, lock_path, "owner-1")
    assert not Path(lock_path).exists()


def test_preexisting_lock_prevents_recovery(tmp_path):
    state_path, lock_path = make_paths(tmp_path)
    write_json(state_path, RUNNING)
    Path(lock_path).write_text("other-owner")
    with pytest.raises(MagazineLockError):
        persist_uncertain_halt(state_path, lock_path, "owner-1")
    assert read_json(state_path) == RUNNING


@pytest.mark.parametrize("state", [READY, COMPLETED, HALTED])
def test_non_running_raises_and_releases(tmp_path, state):
    state_path, lock_path = make_paths(tmp_path)
    write_json(state_path, state)
    with pytest.raises(RecoveryTransitionError):
        persist_uncertain_halt(state_path, lock_path, "owner-1")
    assert not Path(lock_path).exists()


def test_malformed_state_propagates_and_releases(tmp_path):
    state_path, lock_path = make_paths(tmp_path)
    Path(state_path).write_text("not valid json")
    with pytest.raises(json.JSONDecodeError):
        persist_uncertain_halt(state_path, lock_path, "owner-1")
    assert not Path(lock_path).exists()


def test_save_failure_propagates_and_releases(tmp_path):
    state_path, lock_path = make_paths(tmp_path)
    write_json(state_path, RUNNING)
    with patch("magazine.recovery_action.save_run_state", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            persist_uncertain_halt(state_path, lock_path, "owner-1")
    assert not Path(lock_path).exists()


def test_owner_id_passed_unchanged(tmp_path):
    state_path, lock_path = make_paths(tmp_path)
    write_json(state_path, RUNNING)
    owner = "owner-42"
    identity = get_runtime_identity()
    with patch("magazine.recovery_action.acquire_run_lock") as acquire, \
         patch("magazine.recovery_action.release_run_lock") as release, \
         patch("magazine.recovery_action.load_run_state", return_value=RUNNING), \
         patch("magazine.recovery_action.halt_uncertain_run", return_value=HALTED), \
         patch("magazine.recovery_action.save_run_state"):
        persist_uncertain_halt(state_path, lock_path, owner)
    acquire.assert_called_once_with(
        lock_path, owner, identity.runtime_id, identity.pid, identity.start_ticks
    )
    release.assert_called_once_with(
        lock_path, owner, identity.runtime_id, identity.pid, identity.start_ticks
    )


def test_call_order(tmp_path):
    state_path, lock_path = make_paths(tmp_path)
    write_json(state_path, RUNNING)
    calls = []
    with patch("magazine.recovery_action.acquire_run_lock",
               side_effect=lambda *a, **k: calls.append("acquire")), \
         patch("magazine.recovery_action.load_run_state",
               side_effect=lambda *a, **k: (calls.append("load"), RUNNING)[1]), \
         patch("magazine.recovery_action.halt_uncertain_run",
               side_effect=lambda *a, **k: (calls.append("transition"), HALTED)[1]), \
         patch("magazine.recovery_action.save_run_state",
               side_effect=lambda *a, **k: calls.append("save")), \
         patch("magazine.recovery_action.release_run_lock",
               side_effect=lambda *a, **k: calls.append("release")):
        persist_uncertain_halt(state_path, lock_path, "owner-1")
    assert calls == ["acquire", "load", "transition", "save", "release"]


# ---------------------------------------------------------------------------
# persist_recover_halted
# ---------------------------------------------------------------------------

HALTED_NO_CURRENT = {
    "done": ["cart-1"],
    "current": None,
    "todo": ["cart-3"],
    "status": "HALTED",
    "revision": 2,
}

# A distinct 32-character lowercase-hex runtime nonce used when the recovered
# run is re-admitted in test_recover_halted_then_admit_n_plus_one.
_RT_ADMIT = "dd" * 16


def test_halted_persisted_as_ready_by_recovery(tmp_path):
    state_path, lock_path = make_paths(tmp_path)
    write_json(state_path, HALTED)
    recovered = persist_recover_halted(state_path, lock_path, "owner-1")
    assert read_json(state_path) == {
        "done": ["cart-1"],
        "current": None,
        "todo": ["cart-2", "cart-3"],
        "status": "READY",
        "revision": 3,
    }
    assert recovered == read_json(state_path)


def test_done_ordering_and_values_preserved_exactly_by_recovery_persistence(tmp_path):
    state_path, lock_path = make_paths(tmp_path)
    write_json(state_path, {
        "done": ["cart-a", "cart-b", "cart-c"],
        "current": "cart-d",
        "todo": ["cart-e"],
        "status": "HALTED",
        "revision": 5,
    })
    persist_recover_halted(state_path, lock_path, "owner-1")
    assert read_json(state_path)["done"] == ["cart-a", "cart-b", "cart-c"]


def test_interrupted_cartridge_moved_to_front_of_todo_by_recovery_persistence(tmp_path):
    state_path, lock_path = make_paths(tmp_path)
    write_json(state_path, HALTED)
    persist_recover_halted(state_path, lock_path, "owner-1")
    persisted = read_json(state_path)
    assert persisted["current"] is None
    assert persisted["todo"] == ["cart-2", "cart-3"]


def test_todo_ordering_and_values_preserved_exactly_by_recovery_persistence(tmp_path):
    state_path, lock_path = make_paths(tmp_path)
    write_json(state_path, {
        "done": ["cart-1"],
        "current": "cart-2",
        "todo": ["cart-3", "cart-4", "cart-5"],
        "status": "HALTED",
        "revision": 2,
    })
    persist_recover_halted(state_path, lock_path, "owner-1")
    # the interrupted cart-2 is prepended; the rest keep their order.
    assert read_json(state_path)["todo"] == ["cart-2", "cart-3", "cart-4", "cart-5"]


def test_revision_increments_by_one_by_recovery_persistence(tmp_path):
    state_path, lock_path = make_paths(tmp_path)
    write_json(state_path, {
        "done": ["cart-1"],
        "current": "cart-2",
        "todo": ["cart-3"],
        "status": "HALTED",
        "revision": 7,
    })
    persist_recover_halted(state_path, lock_path, "owner-1")
    assert read_json(state_path)["revision"] == 8


def test_returned_state_equals_persisted_state_by_recovery(tmp_path):
    state_path, lock_path = make_paths(tmp_path)
    write_json(state_path, HALTED)
    recovered = persist_recover_halted(state_path, lock_path, "owner-1")
    assert read_json(state_path) == recovered


def test_lock_released_after_successful_recovery(tmp_path):
    state_path, lock_path = make_paths(tmp_path)
    write_json(state_path, HALTED)
    persist_recover_halted(state_path, lock_path, "owner-1")
    assert not Path(lock_path).exists()


def test_preexisting_lock_prevents_concurrent_recovery(tmp_path):
    """A run lock held by another owner blocks recovery; the persisted
    state stays HALTED."""
    state_path, lock_path = make_paths(tmp_path)
    write_json(state_path, HALTED)
    Path(lock_path).write_text("other-owner")
    with pytest.raises(MagazineLockError):
        persist_recover_halted(state_path, lock_path, "owner-1")
    assert read_json(state_path) == HALTED


@pytest.mark.parametrize("state", [READY, COMPLETED, RUNNING])
def test_non_halted_states_rejected_and_lock_released(tmp_path, state):
    state_path, lock_path = make_paths(tmp_path)
    write_json(state_path, state)
    with pytest.raises(RecoveryTransitionError):
        persist_recover_halted(state_path, lock_path, "owner-1")
    assert not Path(lock_path).exists()


def test_halted_without_current_rejected_and_lock_released(tmp_path):
    """A HALTED state with no current cartridge has no interrupted work
    position to retry; recovery is explicitly rejected and the lock is
    released."""
    state_path, lock_path = make_paths(tmp_path)
    write_json(state_path, HALTED_NO_CURRENT)
    with pytest.raises(RecoveryTransitionError, match="without a current cartridge"):
        persist_recover_halted(state_path, lock_path, "owner-1")
    assert not Path(lock_path).exists()


def test_malformed_state_propagates_and_releases_by_recovery(tmp_path):
    state_path, lock_path = make_paths(tmp_path)
    Path(state_path).write_text("not valid json")
    with pytest.raises(json.JSONDecodeError):
        persist_recover_halted(state_path, lock_path, "owner-1")
    assert not Path(lock_path).exists()


def test_save_failure_propagates_and_releases_by_recovery(tmp_path):
    state_path, lock_path = make_paths(tmp_path)
    write_json(state_path, HALTED)
    with patch("magazine.recovery_action.save_run_state",
               side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            persist_recover_halted(state_path, lock_path, "owner-1")
    assert not Path(lock_path).exists()


def test_owner_id_passed_unchanged_to_lock_by_recovery(tmp_path):
    state_path, lock_path = make_paths(tmp_path)
    write_json(state_path, HALTED)
    owner = "owner-42"
    identity = get_runtime_identity()
    with patch("magazine.recovery_action.acquire_run_lock") as acquire, \
         patch("magazine.recovery_action.release_run_lock") as release, \
         patch("magazine.recovery_action.load_run_state", return_value=HALTED), \
         patch("magazine.recovery_action.recover_halted_run",
               return_value={**HALTED, "current": None, "status": "READY",
               "revision": 3, "todo": ["cart-2", "cart-3"]}), \
         patch("magazine.recovery_action.save_run_state"):
        persist_recover_halted(state_path, lock_path, owner)
    acquire.assert_called_once_with(
        lock_path, owner, identity.runtime_id, identity.pid, identity.start_ticks
    )
    release.assert_called_once_with(
        lock_path, owner, identity.runtime_id, identity.pid, identity.start_ticks
    )


def test_call_order_by_recovery(tmp_path):
    state_path, lock_path = make_paths(tmp_path)
    write_json(state_path, HALTED)
    calls = []
    with patch("magazine.recovery_action.acquire_run_lock",
               side_effect=lambda *a, **k: calls.append("acquire")), \
         patch("magazine.recovery_action.load_run_state",
               side_effect=lambda *a, **k: (calls.append("load"), HALTED)[1]), \
         patch("magazine.recovery_action.recover_halted_run",
               side_effect=lambda *a, **k: (calls.append("recover"),
               {**HALTED, "current": None, "status": "READY", "revision": 3,
               "todo": ["cart-2", "cart-3"]})[1]), \
         patch("magazine.recovery_action.save_run_state",
               side_effect=lambda *a, **k: calls.append("save")), \
         patch("magazine.recovery_action.release_run_lock",
               side_effect=lambda *a, **k: calls.append("release")):
        persist_recover_halted(state_path, lock_path, "owner-1")
    assert calls == ["acquire", "load", "recover", "save", "release"]


# ---------------------------------------------------------------------------
# persist_recover_halted: execution-claim retirement (Slice 1)
# ---------------------------------------------------------------------------


def _make_halted_run(tmp_path):
    state_path, lock_path = make_paths(tmp_path)
    write_json(state_path, HALTED)
    return state_path, lock_path


def _write_claim(exec_path, attempt=1,
                 runtime_id="0123456789abcdef0123456789abcdef",
                 halted_by=None, halted_at=None, halt_reason=None):
    """Write a valid ``execution.json`` claim (Slice 1 fixtures)."""
    claim = ExecutionMetadata(
        attempt=attempt,
        runtime_id=runtime_id,
        pid=12345,
        start_ticks=98765,
        created_at=utc_now_iso(),
        halted_by=halted_by,
        halted_at=halted_at,
        halt_reason=halt_reason,
    )
    save_execution_metadata(exec_path, claim)
    return claim


def test_recover_halted_retires_existing_claim(tmp_path):
    """Slice 1: recovering a ``HALTED`` run that carries an existing execution
    claim retires that claim under the held run lock.  The claim is not
    deleted; it is replaced with a frozen copy that preserves ``attempt`` and
    ``runtime_id`` and augments ``halt_reason`` to ``operator-recovered``,
    ``halted_at`` to the recovering timestamp, and ``halted_by`` to the
    recovering runtime ID (a 32-char lowercase-hex nonce, never the literal
    reason).  The run is left re-admissible (``READY`` + a retired claim)."""
    state_path, lock_path = _make_halted_run(tmp_path)
    exec_path = execution_file_path(Path(state_path).parent)
    original = _write_claim(exec_path)
    assert original.halt_reason is None

    recovered = persist_recover_halted(state_path, lock_path, "owner-1")
    assert recovered["status"] == "READY"
    assert recovered == read_json(state_path)

    retired = load_execution_metadata(exec_path)
    assert retired is not None
    # The claim is retired (augmented), not deleted.
    assert retired.attempt == original.attempt
    assert retired.runtime_id == original.runtime_id
    assert retired.pid == original.pid
    assert retired.start_ticks == original.start_ticks
    assert retired.created_at == original.created_at
    # Retirement fields are set with the recovering runtime's identity.
    assert retired.halt_reason == HALT_REASON_OPERATOR_RECOVERED
    assert retired.halted_by is not None
    # ``halted_by`` must be a runtime ID (32-char lowercase-hex), not the reason.
    assert retired.halted_by != HALT_REASON_OPERATOR_RECOVERED
    assert len(retired.halted_by) == 32
    assert all(c in "0123456789abcdef" for c in retired.halted_by)
    assert isinstance(retired.halted_at, str) and retired.halted_at != ""
    # Lock released; state is re-admissible.
    assert not Path(lock_path).exists()


def test_recover_halted_no_claim_still_proceeds(tmp_path):
    """Slice 1 edge case: a ``HALTED`` state may carry no execution claim
    (e.g. written directly, or via ``persist_begin_next_cartridge`` which never
    creates one).  Recovery must still transition to ``READY`` and must not
    crash on the absent claim; no claim file is created."""
    state_path, lock_path = _make_halted_run(tmp_path)
    exec_path = execution_file_path(Path(state_path).parent)
    assert not Path(exec_path).exists()

    recovered = persist_recover_halted(state_path, lock_path, "owner-1")
    assert recovered["status"] == "READY"
    assert recovered["current"] is None
    assert recovered["todo"] == ["cart-2", "cart-3"]
    assert read_json(state_path) == recovered
    assert not Path(exec_path).exists()  # no claim was ever present
    assert not Path(lock_path).exists()


# ---------------------------------------------------------------------------
# Crash-window ordering (Slice 1 fix): retirement before commit, failures
# propagate, no-claim path, and N+1 re-admission ------------------------------


def test_claim_retirement_precedes_ready_commit(tmp_path):
    """Crash-consistency: the durable claim retirement must complete *before*
    the ``READY`` state is committed.  This closes the crash window: if the
    process dies between the two operations, the run is left in
    HALTED + retired claim (recoverable) rather than READY + unretired claim
    (stranded, and rejected by the admission gate).

    We spy on the two durability writes and assert the relative order: the
    ``execution.json`` retirement (``save_execution_metadata``) precedes the
    state commit (``save_run_state``)."""
    state_path, lock_path = _make_halted_run(tmp_path)
    exec_path = execution_file_path(Path(state_path).parent)
    _write_claim(exec_path)  # existing claim that must be retired
    calls = []
    with patch("magazine.recovery_action.save_execution_metadata",
               side_effect=lambda *a, **k: calls.append("retire")), \
         patch("magazine.recovery_action.save_run_state",
               side_effect=lambda *a, **k: calls.append("commit")), \
         patch("magazine.recovery_action.acquire_run_lock"), \
         patch("magazine.recovery_action.release_run_lock"):
        persist_recover_halted(state_path, lock_path, "owner-1")

    assert "retire" in calls and "commit" in calls
    assert calls.index("retire") < calls.index("commit")


def test_retirement_failure_propagates_and_state_stays_halted(tmp_path):
    """Crash-consistency: if the durable claim retirement fails, the error is
    *propagated* (never swallowed) and the state is left durably HALTED, so the
    operator-recovery path remains retryable.  Because the state commit now
    runs *after* a successful retirement, a failed retirement means the
    ``READY`` commit never happens."""
    state_path, lock_path = _make_halted_run(tmp_path)
    exec_path = execution_file_path(Path(state_path).parent)
    _write_claim(exec_path)  # existing claim; its retirement is made to fail

    with patch("magazine.recovery_action.save_execution_metadata",
               side_effect=OSError("fsync failed")), \
         patch("magazine.recovery_action.acquire_run_lock"), \
         patch("magazine.recovery_action.release_run_lock"):
        with pytest.raises(OSError):
            persist_recover_halted(state_path, lock_path, "owner-1")

    # The READY commit never landed: state stays HALTED (retryable), not READY.
    assert read_json(state_path) == HALTED
    # Lock released even on the failure path.
    assert not Path(lock_path).exists()


def test_recover_halted_then_admit_n_plus_one(tmp_path):
    """Normal operator recovery still retires the claim so the SAME run is
    immediately re-admissible through the authoritative gate: after
    ``persist_recover_halted`` retires the claim (``operator-recovered``) and
    commits READY, a following ``persist_admit_run`` is permitted (not rejected)
    and begins the recovered run as ``attempt N + 1`` in ``RUNNING`` state.

    This is the end-to-end chain the crash-window fix depends on: recovery must
    leave the run in a re-admissible state, or the operator would never be able
    to resume it."""
    state_path, lock_path = _make_halted_run(tmp_path)
    exec_path = execution_file_path(Path(state_path).parent)
    original = _write_claim(exec_path)  # attempt=1, live (no halt reason)

    # --- Step 1: operator recovery retires the claim and commits READY ---
    recovered = persist_recover_halted(state_path, lock_path, "owner-1")
    assert recovered == read_json(state_path)
    assert recovered["status"] == "READY"
    assert recovered["current"] is None
    assert recovered["todo"] == ["cart-2", "cart-3"]
    assert not Path(lock_path).exists()
    # The claim was retired (not deleted) with the recovering runtime's identity.
    retired = load_execution_metadata(exec_path)
    assert retired is not None
    assert retired.attempt == original.attempt
    assert retired.halt_reason == HALT_REASON_OPERATOR_RECOVERED
    assert retired.halted_by is not None
    assert retired.halted_by != HALT_REASON_OPERATOR_RECOVERED

    # --- Step 2: the recovered run is re-admitted as attempt N+1 (RUNNING) ---
    admitted, attempt = persist_admit_run(
        state_path, lock_path, "owner-1", ["cart-2", "cart-3"],
        runtime_id=_RT_ADMIT, pid=2, start_ticks=2,
    )
    admitted_state = read_json(state_path)
    assert admitted == admitted_state
    assert admitted_state["status"] == "RUNNING"
    assert admitted_state["current"] == "cart-2"
    assert admitted_state["done"] == ["cart-1"]
    assert admitted_state["todo"] == ["cart-3"]
    assert admitted_state["revision"] == 4
    assert attempt == 2  # N+1 (original claim attempt=1 -> 2)
    admitted_claim = load_execution_metadata(exec_path)
    assert admitted_claim is not None
    assert admitted_claim.attempt == 2
    assert admitted_claim.runtime_id == _RT_ADMIT
    assert admitted_claim.halted_by is None
    assert admitted_claim.halted_at is None
    assert admitted_claim.halt_reason is None
    assert not Path(lock_path).exists()
