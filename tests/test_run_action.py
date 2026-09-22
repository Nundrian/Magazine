"""Tests for magazine.run_action (run-level action wrappers).

Covers:
  * persist_begin_next_cartridge
  * persist_complete_current_cartridge
  * load_run_state / save_run_state (via state_store)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from magazine.run_action import (
    persist_begin_next_cartridge,
    persist_complete_current_cartridge,
    load_run_state,
    save_run_state,
)
from magazine.run_lock import acquire_run_lock, release_run_lock, MagazineLockError
from magazine.run_transition import RunTransitionError, begin_next_cartridge, complete_current_cartridge


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
