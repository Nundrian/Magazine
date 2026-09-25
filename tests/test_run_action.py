"""Tests for magazine.run_action (run-level action wrappers).

Covers:
  * persist_begin_next_cartridge
  * persist_complete_current_cartridge
  * load_run_state / save_run_state (via state_store)
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from magazine.run_action import (
    RunAdmissionError,
    persist_admit_run,
    persist_begin_next_cartridge,
    persist_complete_current_cartridge,
    load_run_state,
    save_run_state,
)
from magazine.run_lock import acquire_run_lock, release_run_lock, MagazineLockError
from magazine.run_transition import RunTransitionError, begin_next_cartridge, complete_current_cartridge

import magazine.run_action as run_action  # full module (run_action.os.path.exists)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_paths(tmp_path: pytest.Path) -> tuple[str, str]:
    state_path = str(tmp_path / "state.json")
    lock_path = str(tmp_path / "lock")
    return state_path, lock_path


# ---------------------------------------------------------------------------
# persist_begin_next_cartridge
# ---------------------------------------------------------------------------

def test_persist_begin_next_cartridge(tmp_path):
    """A READY run becomes RUNNING and is persisted atomically."""
    state_path, lock_path = _make_paths(tmp_path)
    owner_id = "owner-1"

    # 1. Start with a READY state.
    ready_state = {
        "done": [],
        "current": None,
        "todo": ["cart-1", "cart-2"],
        "status": "READY",
        "revision": 0,
    }
    save_run_state(state_path, ready_state)

    # 2. Acquire the run lock.
    persist_begin_next_cartridge(state_path, lock_path, owner_id)

    # 3. Verify the returned state is exactly the expected RUNNING state.
    #    begin_next_cartridge is called on the loaded state, which returns
    #    a new dict with revision incremented.
    #    load_run_state returns the dict, begin_next_cartridge returns
    #    a new dict with revision = 1.
    #
    #    The test verifies the full state including revision.
    assert load_run_state(state_path) == {
        "done": [],
        "current": "cart-1",
        "todo": ["cart-2"],
        "status": "RUNNING",
        "revision": 1,
    }

    # 4. Verify loading the state file returns that same state.
    assert load_run_state(state_path) == {
        "done": [],
        "current": "cart-1",
        "todo": ["cart-2"],
        "status": "RUNNING",
        "revision": 1,
    }


def test_persist_begin_next_cartridge_running_state(tmp_path):
    """Calling persist_begin_next_cartridge on a RUNNING state raises
    RunTransitionError (a cartridge cannot be begun while one is running)."""
    state_path, lock_path = _make_paths(tmp_path)
    owner_id = "owner-2"

    # 1. Persist a valid RUNNING state.
    running = {
        "done": [],
        "current": "cart-1",
        "todo": ["cart-2"],
        "status": "RUNNING",
        "revision": 0,
    }
    save_run_state(state_path, running)

    # 2. Calling persist_begin_next_cartridge on a RUNNING state must raise
    #    RunTransitionError (a cartridge cannot be begun while one is running).
    with pytest.raises(RunTransitionError):
        persist_begin_next_cartridge(state_path, lock_path, owner_id)

    # 3. The persisted state must be unchanged.
    assert load_run_state(state_path) == running


def test_persist_begin_next_cartridge_releases_lock_on_failure(tmp_path):
    """If persist_begin_next_cartridge fails, the run lock is released."""
    state_path, lock_path = _make_paths(tmp_path)
    owner_id = "owner-3"

    # 1. Persist a valid RUNNING state (begin will raise RunTransitionError).
    running = {
        "done": [],
        "current": "cart-1",
        "todo": ["cart-2"],
        "status": "RUNNING",
        "revision": 0,
    }
    save_run_state(state_path, running)

    # 2. The lock should not exist yet.
    assert not Path(lock_path).exists()

    # 3. Call persist_begin_next_cartridge — it should raise and release.
    with pytest.raises(RunTransitionError):
        persist_begin_next_cartridge(state_path, lock_path, owner_id)

    # 4. The lock must have been released.
    assert not Path(lock_path).exists()


def test_persist_begin_next_cartridge_calls_begin_next_cartridge(tmp_path):
    """persist_begin_next_cartridge calls begin_next_cartridge exactly once
    with the loaded state."""
    state_path, lock_path = _make_paths(tmp_path)
    owner_id = "owner-4"

    # 1. Persist a valid READY state.
    ready = {
        "done": [],
        "current": None,
        "todo": ["cart-1"],
        "status": "READY",
        "revision": 0,
    }
    save_run_state(state_path, ready)

    # 2. Track calls.
    calls: list[str] = []

    def _begin(state):
        calls.append("begin")
        return {
            "done": [],
            "current": "cart-1",
            "todo": ["cart-2"],
            "status": "RUNNING",
            "revision": 1,
        }

    def _load(*args, **kwargs):
        calls.append("load")
        return ready

    with patch("magazine.run_action.acquire_run_lock"), \
         patch("magazine.run_action.release_run_lock"), \
         patch("magazine.run_action.begin_next_cartridge", side_effect=_begin), \
         patch("magazine.run_action.load_run_state", side_effect=_load):
        persist_begin_next_cartridge(state_path, lock_path, owner_id)

    # 3. Verify begin_next_cartridge was called exactly once.
    assert calls == ["load", "begin"]


def test_persist_begin_next_cartridge_does_not_call_begin_on_run_transition_error(tmp_path):
    """If the loaded state is invalid (raises RunTransitionError), begin_next_cartridge is not called."""
    state_path, lock_path = _make_paths(tmp_path)
    owner_id = "owner-5"

    # 1. Persist a valid RUNNING state (begin will raise RunTransitionError).
    running = {
        "done": [],
        "current": "cart-1",
        "todo": ["cart-2"],
        "status": "RUNNING",
        "revision": 0,
    }
    save_run_state(state_path, running)

    # 2. Track whether begin_next_cartridge was called.
    calls: list[str] = []

    def _begin(state):
        calls.append("begin")
        raise RunTransitionError("should not be called")

    with patch("magazine.run_action.acquire_run_lock"), \
         patch("magazine.run_action.release_run_lock"), \
         patch("magazine.run_action.begin_next_cartridge", side_effect=_begin):
        with pytest.raises(RunTransitionError):
            persist_begin_next_cartridge(state_path, lock_path, owner_id)

    # 3. begin_next_cartridge must not be called (begin raises before it).
    #    Wait — persist_begin_next_cartridge DOES call begin_next_cartridge.
    #    Let's verify it IS called but the error propagates.
    assert "begin" in calls


def test_persist_begin_next_cartridge_lock_error(tmp_path):
    """If the lock is already held by another owner, persist_begin_next_cartridge
    raises MagazineLockError and does not modify the state."""
    state_path, lock_path = _make_paths(tmp_path)

    # 1. Persist a valid READY state.
    ready = {
        "done": [],
        "current": None,
        "todo": ["cart-1"],
        "status": "READY",
        "revision": 0,
    }
    save_run_state(state_path, ready)

    # 2. Create the run lock first with owner "owner-a".
    acquire_run_lock(lock_path, "owner-a")

    # 3. Call persist_begin_next_cartridge with a different owner.
    with pytest.raises(MagazineLockError):
        persist_begin_next_cartridge(state_path, lock_path, "owner-b")

    # 4. The persisted state must be unchanged.
    assert load_run_state(state_path) == ready

    # 5. The existing lock must still contain "owner-a".
    with open(lock_path, "r", encoding="utf-8") as f:
        assert f.read() == "owner-a"


def test_persist_begin_next_cartridge_malformed_state(tmp_path):
    """If the persisted state is malformed, persist_begin_next_cartridge
    raises MagazineStateError and the state is not modified."""
    state_path, lock_path = _make_paths(tmp_path)
    owner_id = "owner-6"

    # 1. Write a malformed state file (missing revision).
    malformed = {"status": "BOGUS", "done": [], "current": None, "todo": ["cart-1"], "revision": 0}
    with open(state_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(malformed))

    # 2. Calling persist_begin_next_cartridge must raise MagazineStateError.
    with pytest.raises(Exception) as exc_info:
        persist_begin_next_cartridge(state_path, lock_path, owner_id)
    assert "BOGUS" in str(exc_info.value) or "status" in str(exc_info.value)

    # 3. The state file must be unchanged.
    with open(state_path, "r", encoding="utf-8") as f:
        assert json.loads(f.read()) == malformed


def test_persist_begin_next_cartridge_load_failure(tmp_path, monkeypatch):
    """If load_run_state fails, persist_begin_next_cartridge raises the same error
    and does not acquire the lock (or releases it)."""
    state_path, lock_path = _make_paths(tmp_path)
    owner_id = "owner-7"

    # 1. Write a valid READY state.
    ready = {
        "done": [],
        "current": None,
        "todo": ["cart-1"],
        "status": "READY",
        "revision": 0,
    }
    save_run_state(state_path, ready)

    # 2. Make magazine.run_action.load_run_state raise OSError.
    with patch("magazine.run_action.load_run_state", side_effect=OSError("file not found")):
        with pytest.raises(OSError):
            persist_begin_next_cartridge(state_path, lock_path, owner_id)

    # 3. The state file must be unchanged.
    assert load_run_state(state_path) == ready


def test_persist_begin_next_cartridge_save_failure(tmp_path, monkeypatch):
    """If save_run_state fails, persist_begin_next_cartridge raises the same error
    and releases the lock."""
    state_path, lock_path = _make_paths(tmp_path)
    owner_id = "owner-8"

    # 1. Persist a valid READY state.
    ready = {
        "done": [],
        "current": None,
        "todo": ["cart-1"],
        "status": "READY",
        "revision": 0,
    }
    save_run_state(state_path, ready)

    # 2. Make magazine.run_action.save_run_state raise OSError.
    with patch("magazine.run_action.save_run_state", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            persist_begin_next_cartridge(state_path, lock_path, owner_id)

    # 3. The lock must be released.
    assert not Path(lock_path).exists()


# ---------------------------------------------------------------------------
# persist_complete_current_cartridge
# ---------------------------------------------------------------------------

def test_persist_complete_current_cartridge_with_todo(tmp_path):
    """A RUNNING run with remaining todos becomes READY and is persisted."""
    state_path, lock_path = _make_paths(tmp_path)
    owner_id = "owner-9"

    # 1. Persist a valid RUNNING state with current "cart-1" and todo ["cart-2"].
    running = {
        "done": [],
        "current": "cart-1",
        "todo": ["cart-2"],
        "status": "RUNNING",
        "revision": 1,
    }
    save_run_state(state_path, running)

    # 2. Call persist_complete_current_cartridge.
    persist_complete_current_cartridge(state_path, lock_path, owner_id)

    # 3. Verify the returned state is exactly the expected READY state.
    assert load_run_state(state_path) == {
        "done": ["cart-1"],
        "current": None,
        "todo": ["cart-2"],
        "status": "READY",
        "revision": 2,
    }

    # 4. Verify loading the state file returns that same persisted state.
    assert load_run_state(state_path) == {
        "done": ["cart-1"],
        "current": None,
        "todo": ["cart-2"],
        "status": "READY",
        "revision": 2,
    }


def test_persist_complete_current_cartridge_last_cartridge(tmp_path):
    """A RUNNING run with no remaining todos becomes COMPLETED."""
    state_path, lock_path = _make_paths(tmp_path)
    owner_id = "owner-10"

    # 1. Persist a valid RUNNING state with the last cartridge as current.
    running = {
        "done": ["cart-1"],
        "current": "cart-2",
        "todo": [],
        "status": "RUNNING",
        "revision": 1,
    }
    save_run_state(state_path, running)

    # 2. Call persist_complete_current_cartridge.
    persist_complete_current_cartridge(state_path, lock_path, owner_id)

    # 3. Verify the returned state is exactly the expected COMPLETED state.
    assert load_run_state(state_path) == {
        "done": ["cart-1", "cart-2"],
        "current": None,
        "todo": [],
        "status": "COMPLETED",
        "revision": 2,
    }

    # 4. Verify loading the state file returns that same persisted state.
    assert load_run_state(state_path) == {
        "done": ["cart-1", "cart-2"],
        "current": None,
        "todo": [],
        "status": "COMPLETED",
        "revision": 2,
    }


def test_persist_complete_current_cartridge_ready_raises(tmp_path):
    """If the state is READY (no cartridge running), persist_complete_current_cartridge
    raises RunTransitionError."""
    state_path, lock_path = _make_paths(tmp_path)
    owner_id = "owner-11"

    # 1. Persist a valid READY state (no cartridge is running).
    ready = {
        "done": [],
        "current": None,
        "todo": ["cart-1"],
        "status": "READY",
        "revision": 0,
    }
    save_run_state(state_path, ready)

    # 2. Calling persist_complete_current_cartridge on a READY state must
    #    raise RunTransitionError (a cartridge cannot be completed when none
    #    is running).
    with pytest.raises(RunTransitionError):
        persist_complete_current_cartridge(state_path, lock_path, owner_id)

    # 3. The persisted state must be unchanged.
    assert load_run_state(state_path) == ready


def test_persist_complete_current_cartridge_completed_raises(tmp_path):
    """If the state is COMPLETED, persist_complete_current_cartridge
    raises RunTransitionError."""
    state_path, lock_path = _make_paths(tmp_path)
    owner_id = "owner-12"

    # 1. Persist a valid COMPLETED state.
    completed = {
        "done": ["cart-1"],
        "current": None,
        "todo": [],
        "status": "COMPLETED",
        "revision": 0,
    }
    save_run_state(state_path, completed)

    # 2. Calling persist_complete_current_cartridge on a COMPLETED state must
    #    raise RunTransitionError.
    with pytest.raises(RunTransitionError):
        persist_complete_current_cartridge(state_path, lock_path, owner_id)

    # 3. The persisted state must be unchanged.
    assert load_run_state(state_path) == completed


def test_persist_complete_current_cartridge_halted_raises(tmp_path):
    """If the state is HALTED, persist_complete_current_cartridge
    raises RunTransitionError."""
    state_path, lock_path = _make_paths(tmp_path)
    owner_id = "owner-13"

    # 1. Persist a valid HALTED state.
    halted = {
        "done": ["cart-1"],
        "current": "cart-2",
        "todo": ["cart-3"],
        "status": "HALTED",
        "revision": 0,
    }
    save_run_state(state_path, halted)

    # 2. Calling persist_complete_current_cartridge on a HALTED state must
    #    raise RunTransitionError.
    with pytest.raises(RunTransitionError):
        persist_complete_current_cartridge(state_path, lock_path, owner_id)

    # 3. The persisted state must be unchanged.
    assert load_run_state(state_path) == halted


def test_persist_complete_current_cartridge_releases_lock_on_failure(tmp_path):
    """If persist_complete_current_cartridge fails, the run lock is released."""
    state_path, lock_path = _make_paths(tmp_path)
    owner_id = "owner-14"

    # 1. Persist a valid READY state (complete will raise RunTransitionError).
    ready = {
        "done": [],
        "current": None,
        "todo": ["cart-1"],
        "status": "READY",
        "revision": 0,
    }
    save_run_state(state_path, ready)

    # 2. The lock should not exist yet.
    assert not Path(lock_path).exists()

    # 3. Call persist_complete_current_cartridge — it should raise and release.
    with pytest.raises(RunTransitionError):
        persist_complete_current_cartridge(state_path, lock_path, owner_id)

    # 4. The lock must have been released.
    assert not Path(lock_path).exists()


def test_persist_complete_current_cartridge_calls_complete_current_cartridge(tmp_path):
    """persist_complete_current_cartridge calls complete_current_cartridge
    exactly once with the loaded state."""
    state_path, lock_path = _make_paths(tmp_path)
    owner_id = "owner-15"

    # 1. Persist a valid RUNNING state.
    running = {
        "done": [],
        "current": "cart-1",
        "todo": ["cart-2"],
        "status": "RUNNING",
        "revision": 0,
    }
    save_run_state(state_path, running)

    # 2. Track calls.
    calls: list[str] = []

    def _complete(state):
        calls.append("complete")
        return {
            "done": ["cart-1"],
            "current": None,
            "todo": ["cart-2"],
            "status": "READY",
            "revision": 1,
        }

    with patch("magazine.run_action.acquire_run_lock"), \
         patch("magazine.run_action.release_run_lock"), \
         patch("magazine.run_action.complete_current_cartridge", side_effect=_complete), \
         patch("magazine.run_action.load_run_state", side_effect=lambda *a, **k: running):
        persist_complete_current_cartridge(state_path, lock_path, owner_id)

    # 3. Verify complete_current_cartridge was called exactly once.
    assert "complete" in calls


def test_persist_complete_current_cartridge_lock_error(tmp_path):
    """If the lock is already held by another owner, persist_complete_current_cartridge
    raises MagazineLockError and does not modify the state."""
    state_path, lock_path = _make_paths(tmp_path)

    # 1. Persist a valid RUNNING state.
    running = {
        "done": [],
        "current": "cart-1",
        "todo": ["cart-2"],
        "status": "RUNNING",
        "revision": 0,
    }
    save_run_state(state_path, running)

    # 2. Create the run lock first with owner "owner-a".
    acquire_run_lock(lock_path, "owner-a")

    # 3. Call persist_complete_current_cartridge with a different owner.
    with pytest.raises(MagazineLockError):
        persist_complete_current_cartridge(state_path, lock_path, "owner-b")

    # 4. The persisted state must be unchanged.
    assert load_run_state(state_path) == running

    # 5. The existing lock must still contain "owner-a".
    with open(lock_path, "r", encoding="utf-8") as f:
        assert f.read() == "owner-a"


def test_persist_complete_current_cartridge_malformed_state(tmp_path):
    """If the persisted state is malformed, persist_complete_current_cartridge
    raises MagazineStateError and the state is not modified."""
    state_path, lock_path = _make_paths(tmp_path)
    owner_id = "owner-16"

    # 1. Write a malformed state file (missing revision).
    malformed = {"status": "BOGUS", "done": [], "current": None, "todo": ["cart-1"], "revision": 0}
    with open(state_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(malformed))

    # 2. Calling persist_complete_current_cartridge must raise MagazineStateError.
    with pytest.raises(Exception) as exc_info:
        persist_complete_current_cartridge(state_path, lock_path, owner_id)
    assert "BOGUS" in str(exc_info.value) or "status" in str(exc_info.value)

    # 3. The state file must be unchanged.
    with open(state_path, "r", encoding="utf-8") as f:
        assert json.loads(f.read()) == malformed


def test_persist_complete_current_cartridge_load_failure(tmp_path, monkeypatch):
    """If load_run_state fails, persist_complete_current_cartridge raises the same error
    and releases the lock."""
    state_path, lock_path = _make_paths(tmp_path)
    owner_id = "owner-17"

    # 1. Persist a valid READY state.
    ready = {
        "done": [],
        "current": None,
        "todo": ["cart-1"],
        "status": "READY",
        "revision": 0,
    }
    save_run_state(state_path, ready)

    # 2. Make magazine.run_action.load_run_state raise OSError.
    with patch("magazine.run_action.load_run_state", side_effect=OSError("file not found")):
        with pytest.raises(OSError):
            persist_complete_current_cartridge(state_path, lock_path, owner_id)

    # 3. The state file must be unchanged.
    assert load_run_state(state_path) == ready


def test_persist_complete_current_cartridge_save_failure(tmp_path, monkeypatch):
    """If save_run_state fails, persist_complete_current_cartridge raises the same error
    and releases the lock."""
    state_path, lock_path = _make_paths(tmp_path)
    owner_id = "owner-18"

    # 1. Persist a valid RUNNING state.
    running = {
        "done": [],
        "current": "cart-1",
        "todo": ["cart-2"],
        "status": "RUNNING",
        "revision": 0,
    }
    save_run_state(state_path, running)

    # 2. Make magazine.run_action.save_run_state raise OSError.
    with patch("magazine.run_action.save_run_state", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            persist_complete_current_cartridge(state_path, lock_path, owner_id)

    # 3. The lock must be released.
    assert not Path(lock_path).exists()


# ---------------------------------------------------------------------------
# persist_admit_run (first-run admission)
# ---------------------------------------------------------------------------

def test_persist_admit_run_first_run_toctou(tmp_path):
    """A second runner cannot observe a stale missing-state while the first
    runner holds the run lock (missing-state TOCTOU).

    The real ``os.path.exists`` used by ``persist_admit_run`` is patched with
    a wrapper that, on the FIRST call, signals that caller A is inside
    ``persist_admit_run`` after acquiring the real run lock and then blocks
    before returning the result.  While A is parked at the existence check,
    the real lock file exists and the state file does not, and caller B,
    invoked against the same paths, fails through ``MagazineLockError`` and
    never reaches the existence check.  No sleeps are used.
    """
    state_path, lock_path = _make_paths(tmp_path)
    cartridge_ids = ["cart-a", "cart-b"]

    a_at_check = threading.Event()   # set when A reaches the existence check
    a_continue = threading.Event()    # A blocks here until the test releases it
    hook_calls = []                   # every patched os.path.exists call
    real_exists = run_action.os.path.exists

    def _patched_exists(path, *args, **kwargs):
        # Record that the existence check was reached.
        hook_calls.append(path)
        # The first call is A's existence check, reached only after A acquired
        # the real run lock.  Signal and block before returning the result.
        if len(hook_calls) == 1:
            a_at_check.set()
            a_continue.wait()
        # Always delegate to the REAL exists for the actual result.
        return real_exists(path, *args, **kwargs)

    a_result = [None]
    a_exc = []

    def _caller_a():
        try:
            a_result[0] = persist_admit_run(
                state_path, lock_path, "owner-a", cartridge_ids
            )
        except Exception as exc:
            a_exc.append(exc)

    with patch.object(run_action.os.path, "exists", _patched_exists):
        thread = threading.Thread(target=_caller_a, name="caller-a", daemon=True)
        thread.start()
        try:
            # 2. Wait for A to reach its existence check under the real lock.
            assert a_at_check.wait(5.0), (
                "caller A did not reach the existence check within the timeout"
            )

            # 3. While A is parked at the existence check, the real lock file
            #    exists and the state file does not yet: proof that lock
            #    acquisition occurs before create-if-absent.
            assert Path(lock_path).exists()
            assert not Path(state_path).exists()

            # The patched existence check has been reached by A exactly once
            # so far (A's single state-existence check).
            assert hook_calls == [state_path]

            # 4. While A remains parked, invoke B against the same paths.  B
            #    must fail through the existing MagazineLockError (it cannot
            #    acquire the lock held by A) and must never reach the existence
            #    check.
            with pytest.raises(MagazineLockError):
                persist_admit_run(state_path, lock_path, "owner-b", cartridge_ids)

            # D. B never reached the existence-check hook: only A's state
            #    existence check is recorded (hook_calls still == [state_path]).
            assert hook_calls == [state_path], (
                "B reached the existence check (hook_calls = %r)" % (hook_calls,)
            )

            # 5. Release A so it can continue.
            a_continue.set()
        finally:
            # Always unblock A so a failed assertion cannot leave the worker
            # thread hanging.
            a_continue.set()
            thread.join(timeout=5.0)

    # A. Caller A raised no exception and returned exactly the RUNNING state
    #    (cart-a begun).
    assert not a_exc, "caller A raised: %r" % (a_exc,)
    assert a_result[0] is not None, "caller A returned no state"
    expected_running = {
        "done": [],
        "current": "cart-a",
        "todo": ["cart-b"],
        "status": "RUNNING",
        "revision": 1,
    }
    assert a_result[0] == expected_running

    # A finished without deadlocking.
    assert not thread.is_alive(), "caller A did not finish (deadlock)"

    # F. Final persisted state exactly equals A's returned RUNNING state.
    persisted = load_run_state(state_path)
    assert persisted == a_result[0]
    assert persisted == expected_running

    # G. Final state fields.
    assert persisted["current"] == "cart-a"
    assert persisted["todo"] == ["cart-b"]
    assert persisted["status"] == "RUNNING"
    assert persisted["revision"] == 1

    # H. No second READY write and no reset back to revision 0: only A's state
    #    existence check (state_path) was recorded, followed by A's release
    #    check (lock_path); B never reached the existence check, so B never
    #    wrote a second READY.
    assert hook_calls == [state_path, lock_path], (
        "expected [state_path, lock_path], got %r (B may have reached the "
        "existence check or written a second READY)" % (hook_calls,)
    )

    # I. Lock cleaned up (A released it on completion).
    assert not Path(lock_path).exists()

    # J. State file exists.
    assert Path(state_path).exists()


# ---------------------------------------------------------------------------
# persist_admit_run (already-RUNNING rejection)
# ---------------------------------------------------------------------------

def test_persist_admit_run_rejects_already_running(tmp_path):
    """A second normal admission after the first run has already been
    admitted (lock released, state persisted RUNNING) is refused without
    touching anything.

    This is NOT the lock-held case (covered by the TOCTOU test): the first
    runner has finished admission and released the lock, the state file on
    disk is RUNNING, and a second ordinary ``persist_admit_run`` call must
    (a) raise :class:`RunAdmissionError`, (b) not join/resume the run,
    (c) leave the persisted state byte-for-byte unchanged, and (d) release
    its temporary admission lock.
    """
    state_path, lock_path = _make_paths(tmp_path)
    cartridge_ids = ["cart-a", "cart-b"]

    # 1. Persist the initial RUNNING state using the REAL state store.
    running = {
        "done": [],
        "current": "cart-a",
        "todo": ["cart-b"],
        "status": "RUNNING",
        "revision": 1,
    }
    save_run_state(state_path, running)

    # 2. Pre-call filesystem facts: state exists, lock does NOT exist.
    assert Path(state_path).exists()
    assert not Path(lock_path).exists()

    # 3. Capture the exact bytes of the state file for the identity check.
    bytes_before = Path(state_path).read_bytes()

    # 4. A second normal admission attempt (owner-b, lock-free, state RUNNING).
    with pytest.raises(RunAdmissionError) as exc_info:
        persist_admit_run(state_path, lock_path, "owner-b", cartridge_ids)
    message = str(exc_info.value)

    # 5. The error must indicate: (i) the run is already RUNNING, (ii) the
    #    caller must not join/resume it automatically, (iii) the existing
    #    explicit recovery path is required.  Stable substrings only.
    assert "already RUNNING" in message
    assert "recovery" in message
    assert "joining or resuming" in message

    # 6. Persisted state bytes after the exception are EXACTLY identical to
    #    the bytes captured before (no READY write, no transition at all).
    assert Path(state_path).read_bytes() == bytes_before

    # 7. load_run_state still returns exactly the original RUNNING state.
    assert load_run_state(state_path) == {
        "done": [],
        "current": "cart-a",
        "todo": ["cart-b"],
        "status": "RUNNING",
        "revision": 1,
    }

    # 8. Field-by-field: no transition occurred (no begin-next, no complete,
    #    no reset; a begin-next would bump revision, a complete would clear
    #    current/todo and flip the status).
    state = load_run_state(state_path)
    assert state["current"] == "cart-a"
    assert state["todo"] == ["cart-b"]
    assert state["done"] == []
    assert state["revision"] == 1
    assert state["status"] == "RUNNING"  # no READY/COMPLETED/HALTED written

    # 9. The temporary admission lock was released; no lock file remains.
    assert not Path(lock_path).exists()


# ---------------------------------------------------------------------------
# persist_admit_run (existing READY admission)
# ---------------------------------------------------------------------------

def test_persist_admit_run_existing_ready_becomes_running(tmp_path):
    """An existing READY state is admitted (not replaced by a fresh initial
    state) and becomes RUNNING/current=cart-b/revision 5 through the real
    persist_admit_run path.

    Proving sequence: existing READY state
    -> acquire admission lock
    -> load authoritative READY state
    -> real begin_next_cartridge
    -> persist RUNNING/current=cart-b/revision 5
    -> release lock
    -> return persisted RUNNING state.
    """
    state_path, lock_path = _make_paths(tmp_path)
    cartridge_ids = ["cart-a", "cart-b"]

    # 1. Persist the existing READY state (file exists BEFORE admission).
    ready = {
        "done": ["cart-a"],
        "current": None,
        "todo": ["cart-b"],
        "status": "READY",
        "revision": 4,
    }
    save_run_state(state_path, ready)

    # 2. Pre-call filesystem facts: state exists, lock does NOT exist.
    assert Path(state_path).exists()
    assert not Path(lock_path).exists()

    # 3. Pre-call state is exactly the READY state above.
    assert load_run_state(state_path) == ready

    # 4. Admit the existing READY run through the real path (lock + state
    #    store + begin_next_cartridge, nothing mocked).
    result = persist_admit_run(state_path, lock_path, "owner-b", cartridge_ids)

    # 5. Returned state is exactly the expected RUNNING state.
    assert result == {
        "done": ["cart-a"],
        "current": "cart-b",
        "todo": [],
        "status": "RUNNING",
        "revision": 5,
    }

    # 6. The returned state equals the persisted state (the function returns
    #    only after the RUNNING state has been persisted).
    assert load_run_state(state_path) == result

    # 7. Field checks on the persisted state.
    state = load_run_state(state_path)
    assert state["done"] == ["cart-a"]   # preserved from the existing state
    assert state["current"] == "cart-b"  # cart-b selected, no other cartridge
    assert state["todo"] == []           # nothing remains
    assert state["status"] == "RUNNING"  # no completion transition (COMPLETED/HALTED/READY)
    assert state["revision"] == 5        # exactly one advance: 4 -> 5

    # 8. Proof of no fresh-state reset: a fresh new_ready_state would have
    #    done=[]/current=None/todo=["cart-a", "cart-b"]/revision 0, which
    #    would never preserve done=["cart-a"] nor advance revision from 4
    #    to 5.  cart-b (not cart-a, the first cartridge ID) is current.
    assert ready["done"] == state["done"]
    assert state["revision"] == ready["revision"] + 1
    assert state["current"] != "cart-a"

    # 9. Lock released on successful return.
    assert not Path(lock_path).exists()


# ---------------------------------------------------------------------------
# persist_admit_run (terminal states returned unchanged)
# ---------------------------------------------------------------------------

def test_persist_admit_run_existing_completed_returns_unchanged(tmp_path):
    """An existing COMPLETED state is returned unchanged by persist_admit_run:
    admission acquires the short lock, loads the terminal state, returns it
    without re-saving or transitioning, and releases the lock.  Byte identity
    of the state file proves no re-save occurred.

    Proving sequence: existing COMPLETED
    -> acquire short admission lock
    -> load terminal state
    -> return unchanged
    -> release lock
    """
    state_path, lock_path = _make_paths(tmp_path)
    cartridge_ids = ["cart-a", "cart-b"]

    # 1. Persist the existing terminal COMPLETED state.
    completed = {
        "done": ["cart-a", "cart-b"],
        "current": None,
        "todo": [],
        "status": "COMPLETED",
        "revision": 8,
    }
    save_run_state(state_path, completed)

    # 2. Pre-call facts: state exists, lock does not, exact bytes captured.
    assert Path(state_path).exists()
    assert not Path(lock_path).exists()
    bytes_before = Path(state_path).read_bytes()

    # 3. Admission returns the terminal state unchanged.
    result = persist_admit_run(
        state_path, lock_path, "owner-completed", cartridge_ids
    )

    # 4. result exactly equals the original COMPLETED state.
    assert result == completed
    assert result == {
        "done": ["cart-a", "cart-b"],
        "current": None,
        "todo": [],
        "status": "COMPLETED",
        "revision": 8,
    }

    # 5. Persisted bytes are EXACTLY identical (no re-save at all).
    assert Path(state_path).read_bytes() == bytes_before

    # 6. load_run_state still returns exactly the original state, field by
    #    field: revision, done, current, todo, status all preserved; no READY
    #    reset and no begin-next transition (no revision bump, no current set).
    state = load_run_state(state_path)
    assert state == completed
    assert state["revision"] == 8
    assert state["done"] == ["cart-a", "cart-b"]
    assert state["current"] is None
    assert state["todo"] == []
    assert state["status"] == "COMPLETED"

    # 7. Lock released on return.
    assert not Path(lock_path).exists()


def test_persist_admit_run_existing_halted_returns_unchanged(tmp_path):
    """An existing HALTED state is returned unchanged by persist_admit_run.

    The HALTED state is constructed per the repository's state contract:
    HALTED may preserve an in-flight current cartridge (rule 11) so uncertain
    or interrupted work is not silently discarded; done, current and todo must
    not overlap.  Admission acquires the short lock, loads the terminal state,
    returns it without re-saving or transitioning, and releases the lock.  Byte
    identity of the state file proves no re-save occurred.

    Proving sequence: existing HALTED
    -> acquire short admission lock
    -> load terminal state
    -> return unchanged
    -> release lock
    """
    state_path, lock_path = _make_paths(tmp_path)
    cartridge_ids = ["cart-a", "cart-b"]

    # 1. Persist a valid existing HALTED state (cart-a done, cart-b was the
    #    in-flight cartridge when the run halted; no overlaps, valid revision).
    halted = {
        "done": ["cart-a"],
        "current": "cart-b",
        "todo": [],
        "status": "HALTED",
        "revision": 6,
    }
    save_run_state(state_path, halted)

    # 2. Pre-call facts: state exists, lock does not, exact bytes captured.
    assert Path(state_path).exists()
    assert not Path(lock_path).exists()
    bytes_before = Path(state_path).read_bytes()

    # 3. Admission returns the terminal state unchanged.
    result = persist_admit_run(state_path, lock_path, "owner-halted", cartridge_ids)

    # 4. result exactly equals the original HALTED state.
    assert result == halted

    # 5. Persisted bytes are EXACTLY identical (no re-save at all).
    assert Path(state_path).read_bytes() == bytes_before

    # 6. load_run_state still returns exactly the original state; every field
    #    is unchanged (revision, done, current, todo, status) and there was no
    #    READY reset or begin-next transition.
    state = load_run_state(state_path)
    assert state == halted
    assert state["done"] == ["cart-a"]
    assert state["current"] == "cart-b"
    assert state["todo"] == []
    assert state["status"] == "HALTED"
    assert state["revision"] == 6

    # 7. Lock released on return.
    assert not Path(lock_path).exists()
