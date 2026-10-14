"""Slice 1 core tests: execution admission and the durable execution claim.

This module exercises the *new* admission behaviour introduced by Slice 1,
specifically the **authoritative claim-existence gate** for ``READY`` runs:

* a fresh *unowned* ``READY`` run (no execution metadata) is admitted as
  attempt 1 and a durable claim (``execution.json``) is written **before**
  the ``READY -> RUNNING`` commit (claim-first ordering);
* a ``READY`` run carrying an **exact pre-start-aborted** claim is re-admitted
  as ``claim.attempt + 1``;
* a ``READY`` run carrying **any other** execution claim (a live claim of an
  in-progress run seen during an inter-cartridge ``READY`` window, or a
  recovered run whose claim was never retired) is **rejected conservatively**
  with :class:`RunAdmissionError` — a second admission must never take over an
  already-owned run.  This is the ownership invariant the weakened
  "any READY + claim -> N+1" gate violated.
* On a ``READY -> RUNNING`` commit failure the just-written claim is **retired**
  (``halt_reason == "pre-start-aborted"``, ``halted_by`` set to the aborting
  runtime id) *only* while the state is positively still ``READY``.

These are pure filesystem + lock unit tests: no dispatch, no webui, no
recovery-from-crash semantics.  The recovered-run re-admission path (an
``operator-recovered`` claim) is now covered here
test_admit_ready_re_admits_despite_operator_recovered_claim) through the same
authoritative claim-existence gate, as is the pre-start-aborted re-admission;
together they close the recovery-boundary ownership hole of Slice 1.

NOTE: ``runtime_id`` (and ``halted_by`` when non-null) must be a 32-character
lowercase-hex 128-bit nonce, so fixtures below use explicit hex constants.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from magazine.execution_meta import (
    ExecutionMetadata,
    HALT_REASON_OPERATOR_RECOVERED,
    load_execution_metadata,
    save_execution_metadata,
)
from magazine.run_action import RunAdmissionError, persist_admit_run
from magazine.run_lock import MagazineLockError, acquire_run_lock, release_run_lock
from magazine.runtime_identity import get_runtime_identity
from magazine.state_store import load_run_state, save_run_state

__all__ = [
    "test_admit_fresh_unowned_ready_writes_claim_attempt_1",
    "test_admit_ready_with_live_claim_rejects",
    "test_admit_ready_re_admits_despite_pre_start_aborted_claim",
    "test_admit_completed_returns_unchanged_attempt_0",
    "test_admit_halted_returns_unchanged_attempt_0",
    "test_admit_already_running_raises_run_admission_error",
    "test_admit_second_runner_fails_on_toctou",
    "test_admit_commit_failure_retires_claim_when_still_ready",
    "test_admit_commit_failure_leaves_claim_when_state_not_ready",
    "test_admit_ready_re_admits_despite_operator_recovered_claim",
]

OWNER = "owner-1"

# Distinct 32-character lowercase-hex runtime nonces for the fixtures.
_RT_A = "aa" * 16
_RT_B = "bb" * 16
_RT_C = "cc" * 16
_RT_D = "dd" * 16
_RT_E = "ee" * 16
_RT_F = "ff" * 16


def _paths(tmp_path: Path) -> tuple[str, str, str]:
    """State / lock / execution paths for a single run directory.

    Mirrors the canonical layout: ``<run_dir>/{state.json,.lock,execution.json}``.
    """
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    return (
        str(run_dir / "state.json"),
        str(run_dir / ".lock"),
        str(run_dir / "execution.json"),
    )


def _state(
    done: list[str] | None = None,
    current: str | None = None,
    todo: list[str] | None = None,
    status: str = "READY",
    revision: int = 0,
) -> dict:
    """Build a run-state dict with independent list copies."""
    return {
        "done": list(done or []),
        "current": current,
        "todo": list(todo or []),
        "status": status,
        "revision": revision,
    }


def _claim(
    attempt: int,
    runtime_id: str = _RT_A,
    pid: int = 1,
    start_ticks: int = 0,
    created_at: str = "2000-01-01T00:00:00Z",
    halted_by: str | None = None,
    halted_at: str | None = None,
    halt_reason: str | None = None,
) -> ExecutionMetadata:
    """Build an ExecutionMetadata claim for a seed execution.json."""
    return ExecutionMetadata(
        attempt=attempt,
        runtime_id=runtime_id,
        pid=pid,
        start_ticks=start_ticks,
        created_at=created_at,
        halted_by=halted_by,
        halted_at=halted_at,
        halt_reason=halt_reason,
    )


# --- claim-first ordering: fresh / unowned READY -> attempt 1 ------------------

def test_admit_fresh_unowned_ready_writes_claim_attempt_1(tmp_path: Path) -> None:
    """A READY run with no claim is admitted as attempt 1 and a durable
    ``execution.json`` is created, recording the admitting identity."""
    state_path, lock_path, exec_path = _paths(tmp_path)
    save_run_state(state_path, _state(todo=["cart-a", "cart-b"]))
    assert not Path(exec_path).exists()

    state, attempt = persist_admit_run(
        state_path, lock_path, OWNER, ["cart-a", "cart-b"],
        runtime_id=_RT_A, pid=1, start_ticks=0,
    )

    assert attempt == 1
    assert state == {
        "done": [],
        "current": "cart-a",
        "todo": ["cart-b"],
        "status": "RUNNING",
        "revision": 1,
    }
    claim = load_execution_metadata(exec_path)
    assert claim is not None
    assert claim.attempt == 1
    assert claim.runtime_id == _RT_A
    assert claim.pid == 1
    assert claim.start_ticks == 0
    assert claim.created_at is not None
    assert claim.halted_by is None
    assert claim.halt_reason is None
    assert claim.halted_at is None
    # state.json and execution.json agree; lock released.
    assert load_run_state(state_path) == state
    assert not Path(lock_path).exists()


# --- ownership invariant: a live claim must never be re-claimed ----------------

def test_admit_ready_with_live_claim_rejects(tmp_path: Path) -> None:
    """A ``READY`` run whose execution claim is *live* (not pre-start-aborted)
    is rejected conservatively: an in-progress run (e.g. seen during an
    inter-cartridge ``READY`` window) must never be re-claimed by a second
    admission.  This is the ownership invariant the weakened gate violated.
    The state and the live claim are left untouched and the lock is released."""
    state_path, lock_path, exec_path = _paths(tmp_path)
    # A live claim: attempt 1, all halt fields None (not pre-start-aborted).
    save_run_state(state_path, _state(done=[], current=None,
                                      todo=["cart-b"], status="READY",
                                      revision=1))
    save_execution_metadata(exec_path, _claim(attempt=1, runtime_id=_RT_A))

    with pytest.raises(RunAdmissionError, match="already claimed"):
        persist_admit_run(
            state_path, lock_path, OWNER, ["cart-a", "cart-b"],
            runtime_id=_RT_B, pid=2, start_ticks=2,
        )

    # State and live claim untouched; lock released.
    assert load_run_state(state_path) == {
        "done": [],
        "current": None,
        "todo": ["cart-b"],
        "status": "READY",
        "revision": 1,
    }
    claim = load_execution_metadata(exec_path)
    assert claim is not None
    assert claim.attempt == 1
    assert claim.runtime_id == _RT_A
    assert claim.halt_reason is None
    assert not Path(lock_path).exists()


# --- exact pre-start-aborted claim -> re-admit as N+1 --------------------------

def test_admit_ready_re_admits_despite_pre_start_aborted_claim(tmp_path: Path) -> None:
    """A ``READY`` run whose only prior claim was retired as *pre-start-aborted*
    (the claim-failure contract) is the single existing-claim case permitted to
    be re-admitted, and it begins as attempt N+1."""
    state_path, lock_path, exec_path = _paths(tmp_path)
    save_run_state(state_path, _state(todo=["cart-a"]))
    save_execution_metadata(
        exec_path,
        _claim(
            attempt=1,
            runtime_id=_RT_C,
            halted_by=_RT_C,
            halted_at="2000-01-01T00:00:01Z",
            halt_reason="pre-start-aborted",
        ),
    )

    state, attempt = persist_admit_run(
        state_path, lock_path, OWNER, ["cart-a"],
        runtime_id=_RT_D, pid=2, start_ticks=2,
    )

    assert attempt == 2
    assert state["status"] == "RUNNING"
    claim = load_execution_metadata(exec_path)
    assert claim is not None
    assert claim.attempt == 2
    assert claim.runtime_id == _RT_D
    assert claim.halted_by is None
    assert claim.halt_reason is None
    assert load_run_state(state_path) == state


# --- terminal states: no re-admission, attempt 0 --------------------------------

def test_admit_completed_returns_unchanged_attempt_0(tmp_path: Path) -> None:
    """A ``COMPLETED`` run is returned unchanged; no claim is written and the
    attempt number is 0."""
    state_path, lock_path, exec_path = _paths(tmp_path)
    state = _state(done=["cart-a", "cart-b"], status="COMPLETED", revision=3)
    save_run_state(state_path, state)

    result_state, attempt = persist_admit_run(
        state_path, lock_path, OWNER, ["cart-a", "cart-b"],
        runtime_id=_RT_F, pid=1, start_ticks=0,
    )
    assert attempt == 0
    assert result_state == state
    assert load_run_state(state_path) == state
    assert not Path(exec_path).exists()
    assert not Path(lock_path).exists()


def test_admit_halted_returns_unchanged_attempt_0(tmp_path: Path) -> None:
    """A ``HALTED`` run is returned unchanged (the explicit recovery path is the
    only way to restart it); no claim is written and the attempt number is 0."""
    state_path, lock_path, exec_path = _paths(tmp_path)
    state = _state(done=["cart-a"], current="cart-b", todo=["cart-c"],
                   status="HALTED", revision=2)
    save_run_state(state_path, state)

    result_state, attempt = persist_admit_run(
        state_path, lock_path, OWNER, ["cart-a", "cart-b", "cart-c"],
        runtime_id=_RT_F, pid=1, start_ticks=0,
    )
    assert attempt == 0
    assert result_state == state
    assert load_run_state(state_path) == state
    assert not Path(exec_path).exists()
    assert not Path(lock_path).exists()


# --- running rejection: refuse to join an in-flight run ------------------------

def test_admit_already_running_raises_run_admission_error(tmp_path: Path) -> None:
    """A second normal runner arriving at a ``RUNNING`` state is refused with
    ``RunAdmissionError``; the state is untouched, no claim is written, and the
    lock is released."""
    state_path, lock_path, exec_path = _paths(tmp_path)
    running = _state(done=["cart-a"], current="cart-b", todo=["cart-c"],
                     status="RUNNING", revision=2)
    save_run_state(state_path, running)

    with pytest.raises(RunAdmissionError, match="already RUNNING"):
        persist_admit_run(state_path, lock_path, OWNER,
                          ["cart-a", "cart-b", "cart-c"])

    assert load_run_state(state_path) == running
    assert not Path(exec_path).exists()
    assert not Path(lock_path).exists()


# --- TOCTOU: a second runner holding the lock fails on the first admission ------

def test_admit_second_runner_fails_on_toctou(tmp_path: Path) -> None:
    """A second runner that holds the run lock fails with ``MagazineLockError``.
    The first admission's state and claim remain intact."""
    state_path, lock_path, exec_path = _paths(tmp_path)

    first_state, first_attempt = persist_admit_run(
        state_path, lock_path, "owner-1", ["cart-a"],
        runtime_id=_RT_A, pid=1, start_ticks=0,
    )
    assert first_attempt == 1
    assert not Path(lock_path).exists()

    # Second runner holds the lock (under this process's live identity), then
    # attempts admission -> lock error.  The same incarnation re-acquiring its
    # own lock raises, which is the TOCTOU failure this test exercises.
    identity = get_runtime_identity()
    acquire_run_lock(
        lock_path,
        "owner-2",
        identity.runtime_id,
        identity.pid,
        identity.start_ticks,
    )
    try:
        with pytest.raises(MagazineLockError):
            persist_admit_run(state_path, lock_path, "owner-2", ["cart-a"])
    finally:
        release_run_lock(
            lock_path,
            "owner-2",
            identity.runtime_id,
            identity.pid,
            identity.start_ticks,
        )

    # First admission intact and untouched.
    assert load_run_state(state_path) == first_state
    claim = load_execution_metadata(exec_path)
    assert claim is not None
    assert claim.attempt == 1
    assert not Path(lock_path).exists()


# --- claim-failure contract: retire only when still READY ------------------------

def test_admit_commit_failure_retires_claim_when_still_ready(tmp_path: Path) -> None:
    """If the ``READY -> RUNNING`` commit fails after the claim was written and
    the state is *positively still READY*, the claim is retired with
    ``halt_reason == "pre-start-aborted"`` and ``halted_by`` set to the aborting
    runtime id (never a free-form label).  The claim is retired, not silently
    deleted."""
    state_path, lock_path, exec_path = _paths(tmp_path)
    save_run_state(state_path, _state(todo=["cart-a"]))
    assert not Path(exec_path).exists()

    with mock.patch(
        "magazine.run_action.save_run_state",
        side_effect=OSError("commit failed"),
    ):
        with pytest.raises(OSError):
            persist_admit_run(
                state_path, lock_path, OWNER, ["cart-a"],
                runtime_id=_RT_E, pid=42, start_ticks=1,
            )

    # Commit failed after the claim was written -> the claim must be retired.
    claim = load_execution_metadata(exec_path)
    assert claim is not None
    assert claim.attempt == 1
    assert claim.halt_reason == "pre-start-aborted"
    assert claim.halted_by == _RT_E
    assert claim.halted_at is not None
    # halted_by must be the runtime id of the process that aborted, not a label.
    assert claim.runtime_id == _RT_E
    # The state is still READY (the commit did not persist).
    assert load_run_state(state_path)["status"] == "READY"
    assert not Path(lock_path).exists()


def test_admit_commit_failure_leaves_claim_when_state_not_ready(tmp_path: Path) -> None:
    """If, at the point of re-reading, the state is *no longer* positively
    ``READY``, the just-written claim must be left in place (a valid claim is
    never silently deleted).  This simulates a dirty/partial commit that has
    already advanced the state to ``RUNNING`` yet whose durability step failed."""
    state_path, lock_path, exec_path = _paths(tmp_path)
    save_run_state(state_path, _state(todo=["cart-a"]))
    assert not Path(exec_path).exists()

    from magazine.state_store import save_run_state as _real_save

    def _commit(*args, **kwargs):
        # A dirty commit: the state file records RUNNING, but durability still
        # fails.  Because the state is no longer READY, the claim must NOT be
        # retired.
        _real_save(*args, **kwargs)
        raise OSError("durability failed")

    with mock.patch(
        "magazine.run_action.save_run_state",
        side_effect=_commit,
    ):
        with pytest.raises(OSError):
            persist_admit_run(
                state_path, lock_path, OWNER, ["cart-a"],
                runtime_id=_RT_E, pid=42, start_ticks=1,
            )

    claim = load_execution_metadata(exec_path)
    assert claim is not None
    assert claim.attempt == 1
    # State is no longer READY -> the claim is left untouched (un-aborted).
    assert claim.halt_reason is None
    assert claim.halted_by is None
    assert load_run_state(state_path)["status"] == "RUNNING"
    assert not Path(lock_path).exists()


# --- recovered-run re-admission: operator-recovered claim -> N+1 ---------------

def test_admit_ready_re_admits_despite_operator_recovered_claim(tmp_path: Path) -> None:
    """A ``READY`` run whose only prior claim was retired as *operator-recovered*
    (the recovery path's claim-retirement contract) is the *second* existing-
    claim case the authoritative gate permits to re-admit, and it begins as
    ``claim.attempt + 1``.  The retired claim's ``halt_*`` fields are cleared on
    re-admission, exactly as for the pre-start-aborted case."""
    state_path, lock_path, exec_path = _paths(tmp_path)
    save_run_state(state_path, _state(todo=["cart-a"]))
    save_execution_metadata(
        exec_path,
        _claim(
            attempt=1,
            runtime_id=_RT_C,
            halted_by=_RT_C,
            halted_at="2000-01-01T00:00:01Z",
            halt_reason=HALT_REASON_OPERATOR_RECOVERED,
        ),
    )

    state, attempt = persist_admit_run(
        state_path, lock_path, OWNER, ["cart-a"],
        runtime_id=_RT_D, pid=2, start_ticks=2,
    )

    assert attempt == 2
    assert state["status"] == "RUNNING"
    claim = load_execution_metadata(exec_path)
    assert claim is not None
    assert claim.attempt == 2
    assert claim.runtime_id == _RT_D
    assert claim.halted_by is None
    assert claim.halted_at is None
    assert claim.halt_reason is None
    assert load_run_state(state_path) == state
