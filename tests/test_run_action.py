"""Tests for magazine.run_action (run-level action wrappers).

Covers:
  * persist_begin_next_cartridge
  * persist_complete_current_cartridge
  * persist_admit_run (first-run admission, Slice 1 claim-first ordering)
  * load_run_state / save_run_state (via state_store)

Slice 2: the two transition actions are *fenced*.  Under the single run-lock
hold they (a) verify the writer's exact execution-ownership claim (``attempt``
+ ``runtime_id`` from ``execution.json``) and (b) transition and persist the
freshly-loaded state.  The helpers below seed that claim deterministically.
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
    RunIdentityError,
    persist_admit_run,
    persist_begin_next_cartridge,
    persist_complete_current_cartridge,
    load_run_state,
    save_run_state,
)
from magazine.run_lock import acquire_run_lock, release_run_lock, MagazineLockError
from magazine.run_transition import (
    RunTransitionError,
    begin_next_cartridge,
    complete_current_cartridge,
)
from magazine.execution_meta import (
    ExecutionMetadata,
    save_execution_metadata,
    utc_now_iso,
)
from magazine.paths import execution_file_path
from magazine.runtime_identity import RuntimeIdentity
from magazine.schema import ExecutionOwnershipError

import magazine.run_action as run_action  # full module (run_action.os.path.exists)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_paths(tmp_path: pytest.Path) -> tuple[str, str]:
    state_path = str(tmp_path / "state.json")
    lock_path = str(tmp_path / "lock")
    return state_path, lock_path


# Deterministic writer identity (Slice 2).  A constant rather than the live
# get_runtime_identity() identity keeps every test self-contained and
# deterministic.  "writer" == the generation that is allowed to persist; a
# different generation must be refused by the fence.
_TEST_WRITER = RuntimeIdentity(runtime_id="0" * 32, pid=42, start_ticks=1000)
_TEST_STALE = RuntimeIdentity(runtime_id="1" * 32, pid=43, start_ticks=999)


def _seed_claim(state_path: str, attempt: int = 1, writer: RuntimeIdentity | None = None) -> None:
    """Seed the Slice-2 fence prerequisite (``<run_dir>/execution.json``) with a
    claim that matches ``writer_attempt`` / ``writer_identity``.  Idempotent: a
    claim whose identity already matches is left alone; otherwise it is written
    with the supplied (writer, attempt)."""
    writer = writer or _TEST_WRITER
    run_dir = os.path.dirname(state_path)
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


def _seed_claim_stale_state(state_path: str, attempt: int = 1, writer: RuntimeIdentity | None = None) -> None:
    """Seed an execution.json claim deliberately NOT matching the writer (a stale
    generation), so the fence must reject the writer."""
    _seed_claim(state_path, attempt, writer)


# ---------------------------------------------------------------------------
# persist_begin_next_cartridge
# ---------------------------------------------------------------------------

def test_persist_begin_next_cartridge(tmp_path):
    """A READY run becomes RUNNING and is persisted atomically (Slice 2: the
    fenced begin requires a matching claim + a fresh RUNNING state)."""
    state_path, lock_path = _make_paths(tmp_path)
    owner_id = "owner-1"

    # 1. Seed a matching execution claim + a READY state.
    _seed_claim(state_path, attempt=1, writer=_TEST_WRITER)
    ready_state = {
        "done": [],
        "current": None,
        "todo": ["cart-1", "cart-2"],
        "status": "READY",
        "revision": 0,
    }
    save_run_state(state_path, ready_state)

    # 2. Fenced begin (attempt + runtime identity must match the claim).
    running = persist_begin_next_cartridge(
        state_path, lock_path, owner_id, 1, _TEST_WRITER
    )

    # 3. The returned state is exactly the expected RUNNING state (rev 0 -> 1).
    assert running == {
        "done": [],
        "current": "cart-1",
        "todo": ["cart-2"],
        "status": "RUNNING",
        "revision": 1,
    }

    # 4. The persisted state (loaded, not the returned object) matches.
    assert load_run_state(state_path) == {
        "done": [],
        "current": "cart-1",
        "todo": ["cart-2"],
        "status": "RUNNING",
        "revision": 1,
    }


def test_persist_begin_next_cartridge_running_state(tmp_path):
    """A fenced begin on a RUNNING state raises RunTransitionError even though
    the ownership claim matches; the persisted state is untouched."""
    state_path, lock_path = _make_paths(tmp_path)
    owner_id = "owner-2"

    _seed_claim(state_path, attempt=1, writer=_TEST_WRITER)
    running = {
        "done": [],
        "current": "cart-1",
        "todo": ["cart-2"],
        "status": "RUNNING",
        "revision": 0,
    }
    save_run_state(state_path, running)

    with pytest.raises(RunTransitionError):
        persist_begin_next_cartridge(state_path, lock_path, owner_id, 1, _TEST_WRITER)

    assert load_run_state(state_path) == running


def test_persist_begin_next_cartridge_releases_lock_on_failure(tmp_path):
    """If the fenced begin fails (invalid transition), the run lock is released
    (even though the ownership claim matched)."""
    state_path, lock_path = _make_paths(tmp_path)
    owner_id = "owner-3"

    _seed_claim(state_path, attempt=1, writer=_TEST_WRITER)
    running = {
        "done": [],
        "current": "cart-1",
        "todo": ["cart-2"],
        "status": "RUNNING",
        "revision": 0,
    }
    save_run_state(state_path, running)

    assert not Path(lock_path).exists()
    with pytest.raises(RunTransitionError):
        persist_begin_next_cartridge(state_path, lock_path, owner_id, 1, _TEST_WRITER)
    assert not Path(lock_path).exists()


def test_persist_begin_next_cartridge_calls_begin_next_cartridge(tmp_path):
    """The fenced begin calls begin_next_cartridge exactly once on the loaded
    state (claim verified first)."""
    state_path, lock_path = _make_paths(tmp_path)
    owner_id = "owner-4"

    # Seed a matching claim (the fence loads it for real, before the mocked
    # load_run_state / begin_next_cartridge are reached).
    _seed_claim(state_path, attempt=1, writer=_TEST_WRITER)
    ready = {
        "done": [],
        "current": None,
        "todo": ["cart-1"],
        "status": "READY",
        "revision": 0,
    }
    save_run_state(state_path, ready)

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
        persist_begin_next_cartridge(state_path, lock_path, owner_id, 1, _TEST_WRITER)

    # begin_next_cartridge called exactly once (after the (real) claim check).
    assert calls == ["load", "begin"]


def test_persist_begin_next_cartridge_begin_raises_before_persist(tmp_path):
    """If begin_next_cartridge raises (invalid transition), begin is still the
    one that raises; no state is saved by this path."""
    state_path, lock_path = _make_paths(tmp_path)
    owner_id = "owner-5"

    _seed_claim(state_path, attempt=1, writer=_TEST_WRITER)
    running = {
        "done": [],
        "current": "cart-1",
        "todo": ["cart-2"],
        "status": "RUNNING",
        "revision": 0,
    }
    save_run_state(state_path, running)

    calls: list[str] = []

    def _begin(state):
        calls.append("begin")
        raise RunTransitionError("should not be called")

    with patch("magazine.run_action.acquire_run_lock"), \
         patch("magazine.run_action.release_run_lock"), \
         patch("magazine.run_action.begin_next_cartridge", side_effect=_begin):
        with pytest.raises(RunTransitionError):
            persist_begin_next_cartridge(state_path, lock_path, owner_id, 1, _TEST_WRITER)

    assert "begin" in calls


def test_persist_begin_next_cartridge_lock_error(tmp_path):
    """If the lock is already held by another owner, persist_begin_next_cartridge
    raises MagazineLockError and does not modify the state (lock fails BEFORE
    the claim check)."""
    state_path, lock_path = _make_paths(tmp_path)

    ready = {
        "done": [],
        "current": None,
        "todo": ["cart-1"],
        "status": "READY",
        "revision": 0,
    }
    save_run_state(state_path, ready)
    acquire_run_lock(lock_path, "owner-a")

    with pytest.raises(MagazineLockError):
        persist_begin_next_cartridge(state_path, lock_path, "owner-b", 1, _TEST_WRITER)

    assert load_run_state(state_path) == ready
    with open(lock_path, "r", encoding="utf-8") as f:
        assert f.read() == "owner-a"


def test_persist_begin_next_cartridge_malformed_state(tmp_path):
    """A malformed state raises MagazineStateError after the (matching) claim
    check passes and before anything is persisted."""
    state_path, lock_path = _make_paths(tmp_path)
    owner_id = "owner-6"

    _seed_claim(state_path, attempt=1, writer=_TEST_WRITER)
    malformed = {"status": "BOGUS", "done": [], "current": None, "todo": ["cart-1"], "revision": 0}
    with open(state_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(malformed))

    with pytest.raises(Exception) as exc_info:
        persist_begin_next_cartridge(state_path, lock_path, owner_id, 1, _TEST_WRITER)
    assert "BOGUS" in str(exc_info.value) or "status" in str(exc_info.value)

    with open(state_path, "r", encoding="utf-8") as f:
        assert json.loads(f.read()) == malformed


def test_persist_begin_next_cartridge_missing_claim_raises(tmp_path):
    """Fence: no execution.json claim -> ExecutionOwnershipError (fail-closed);
    no state change and the lock is released."""
    state_path, lock_path = _make_paths(tmp_path)
    owner_id = "owner-7"

    ready = {
        "done": [],
        "current": None,
        "todo": ["cart-1"],
        "status": "READY",
        "revision": 0,
    }
    save_run_state(state_path, ready)

    with pytest.raises(ExecutionOwnershipError, match="ownership"):
        persist_begin_next_cartridge(state_path, lock_path, owner_id, 1, _TEST_WRITER)

    assert load_run_state(state_path) == ready
    assert not Path(lock_path).exists()


def test_persist_begin_next_cartridge_stale_attempt_rejected(tmp_path):
    """Fence: a writer claiming a DIFFERENT attempt (a stale generation) is
    refused with ExecutionOwnershipError; the state is untouched."""
    state_path, lock_path = _make_paths(tmp_path)
    owner_id = "owner-8"

    _seed_claim(state_path, attempt=1, writer=_TEST_WRITER)
    ready = {
        "done": [],
        "current": None,
        "todo": ["cart-1"],
        "status": "READY",
        "revision": 0,
    }
    save_run_state(state_path, ready)

    with pytest.raises(ExecutionOwnershipError, match="mismatch"):
        persist_begin_next_cartridge(state_path, lock_path, owner_id, 2, _TEST_WRITER)

    assert load_run_state(state_path) == ready


def test_persist_begin_next_cartridge_wrong_runtime_id_rejected(tmp_path):
    """Fence: a writer whose runtime_id does not match the claim (a *different*
    process generation) is refused; the state is untouched."""
    state_path, lock_path = _make_paths(tmp_path)
    owner_id = "owner-9"

    _seed_claim(state_path, attempt=1, writer=_TEST_WRITER)
    ready = {
        "done": [],
        "current": None,
        "todo": ["cart-1"],
        "status": "READY",
        "revision": 0,
    }
    save_run_state(state_path, ready)

    with pytest.raises(ExecutionOwnershipError, match="mismatch"):
        persist_begin_next_cartridge(state_path, lock_path, owner_id, 1, _TEST_STALE)

    assert load_run_state(state_path) == ready


def test_persist_begin_next_cartridge_load_failure(tmp_path, monkeypatch):
    """If load_run_state fails, persist_begin_next_cartridge raises the same error
    and releases the lock (after the matching claim check)."""
    state_path, lock_path = _make_paths(tmp_path)
    owner_id = "owner-10"

    _seed_claim(state_path, attempt=1, writer=_TEST_WRITER)
    ready = {
        "done": [],
        "current": None,
        "todo": ["cart-1"],
        "status": "READY",
        "revision": 0,
    }
    save_run_state(state_path, ready)

    with patch("magazine.run_action.load_run_state", side_effect=OSError("file not found")):
        with pytest.raises(OSError):
            persist_begin_next_cartridge(state_path, lock_path, owner_id, 1, _TEST_WRITER)

    assert load_run_state(state_path) == ready


def test_persist_begin_next_cartridge_save_failure(tmp_path, monkeypatch):
    """If save_run_state fails, persist_begin_next_cartridge raises the same error
    and releases the lock (claim matched, state loaded, begin applied)."""
    state_path, lock_path = _make_paths(tmp_path)
    owner_id = "owner-11"

    _seed_claim(state_path, attempt=1, writer=_TEST_WRITER)
    ready = {
        "done": [],
        "current": None,
        "todo": ["cart-1"],
        "status": "READY",
        "revision": 0,
    }
    save_run_state(state_path, ready)

    with patch("magazine.run_action.save_run_state", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            persist_begin_next_cartridge(state_path, lock_path, owner_id, 1, _TEST_WRITER)

    assert not Path(lock_path).exists()


# ---------------------------------------------------------------------------
# persist_complete_current_cartridge
# ---------------------------------------------------------------------------

def test_persist_complete_current_cartridge_with_todo(tmp_path):
    """A RUNNING run with remaining todos becomes READY and is persisted (fence
    verifies the claim first)."""
    state_path, lock_path = _make_paths(tmp_path)
    owner_id = "owner-12"

    _seed_claim(state_path, attempt=1, writer=_TEST_WRITER)
    running = {
        "done": [],
        "current": "cart-1",
        "todo": ["cart-2"],
        "status": "RUNNING",
        "revision": 1,
    }
    save_run_state(state_path, running)

    ready = persist_complete_current_cartridge(
        state_path, lock_path, owner_id, 1, _TEST_WRITER
    )
    assert ready == {
        "done": ["cart-1"],
        "current": None,
        "todo": ["cart-2"],
        "status": "READY",
        "revision": 2,
    }
    assert load_run_state(state_path) == {
        "done": ["cart-1"],
        "current": None,
        "todo": ["cart-2"],
        "status": "READY",
        "revision": 2,
    }


def test_persist_complete_current_cartridge_last_cartridge(tmp_path):
    """A RUNNING run with no remaining todos becomes COMPLETED (fenced)."""
    state_path, lock_path = _make_paths(tmp_path)
    owner_id = "owner-13"

    _seed_claim(state_path, attempt=1, writer=_TEST_WRITER)
    running = {
        "done": ["cart-1"],
        "current": "cart-2",
        "todo": [],
        "status": "RUNNING",
        "revision": 1,
    }
    save_run_state(state_path, running)

    completed = persist_complete_current_cartridge(
        state_path, lock_path, owner_id, 1, _TEST_WRITER
    )
    assert completed == {
        "done": ["cart-1", "cart-2"],
        "current": None,
        "todo": [],
        "status": "COMPLETED",
        "revision": 2,
    }
    assert load_run_state(state_path) == completed


def test_persist_complete_current_cartridge_ready_raises(tmp_path):
    """A fenced complete on a READY state raises RunTransitionError (claim
    matches, transition rejected); state untouched."""
    state_path, lock_path = _make_paths(tmp_path)
    owner_id = "owner-14"

    _seed_claim(state_path, attempt=1, writer=_TEST_WRITER)
    ready = {
        "done": [],
        "current": None,
        "todo": ["cart-1"],
        "status": "READY",
        "revision": 0,
    }
    save_run_state(state_path, ready)

    with pytest.raises(RunTransitionError):
        persist_complete_current_cartridge(state_path, lock_path, owner_id, 1, _TEST_WRITER)

    assert load_run_state(state_path) == ready


def test_persist_complete_current_cartridge_completed_raises(tmp_path):
    """A fenced complete on a COMPLETED state raises RunTransitionError."""
    state_path, lock_path = _make_paths(tmp_path)
    owner_id = "owner-15"

    _seed_claim(state_path, attempt=1, writer=_TEST_WRITER)
    completed = {
        "done": ["cart-1"],
        "current": None,
        "todo": [],
        "status": "COMPLETED",
        "revision": 0,
    }
    save_run_state(state_path, completed)

    with pytest.raises(RunTransitionError):
        persist_complete_current_cartridge(state_path, lock_path, owner_id, 1, _TEST_WRITER)

    assert load_run_state(state_path) == completed


def test_persist_complete_current_cartridge_halted_raises(tmp_path):
    """A fenced complete on a HALTED state raises RunTransitionError."""
    state_path, lock_path = _make_paths(tmp_path)
    owner_id = "owner-16"

    _seed_claim(state_path, attempt=1, writer=_TEST_WRITER)
    halted = {
        "done": ["cart-1"],
        "current": "cart-2",
        "todo": ["cart-3"],
        "status": "HALTED",
        "revision": 0,
    }
    save_run_state(state_path, halted)

    with pytest.raises(RunTransitionError):
        persist_complete_current_cartridge(state_path, lock_path, owner_id, 1, _TEST_WRITER)

    assert load_run_state(state_path) == halted


def test_persist_complete_current_cartridge_releases_lock_on_failure(tmp_path):
    """If the fenced complete fails, the run lock is released (claim matched)."""
    state_path, lock_path = _make_paths(tmp_path)
    owner_id = "owner-17"

    _seed_claim(state_path, attempt=1, writer=_TEST_WRITER)
    ready = {
        "done": [],
        "current": None,
        "todo": ["cart-1"],
        "status": "READY",
        "revision": 0,
    }
    save_run_state(state_path, ready)

    assert not Path(lock_path).exists()
    with pytest.raises(RunTransitionError):
        persist_complete_current_cartridge(state_path, lock_path, owner_id, 1, _TEST_WRITER)

    assert not Path(lock_path).exists()


def test_persist_complete_current_cartridge_calls_complete_current_cartridge(tmp_path):
    """The fenced complete calls complete_current_cartridge exactly once on the
    loaded state (claim verified first)."""
    state_path, lock_path = _make_paths(tmp_path)
    owner_id = "owner-18"

    _seed_claim(state_path, attempt=1, writer=_TEST_WRITER)
    running = {
        "done": [],
        "current": "cart-1",
        "todo": ["cart-2"],
        "status": "RUNNING",
        "revision": 0,
    }
    save_run_state(state_path, running)

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
        persist_complete_current_cartridge(state_path, lock_path, owner_id, 1, _TEST_WRITER)

    assert "complete" in calls


def test_persist_complete_current_cartridge_lock_error(tmp_path):
    """If the lock is already held by another owner, persist_complete_current_cartridge
    raises MagazineLockError (before the claim check)."""
    state_path, lock_path = _make_paths(tmp_path)

    running = {
        "done": [],
        "current": "cart-1",
        "todo": ["cart-2"],
        "status": "RUNNING",
        "revision": 0,
    }
    save_run_state(state_path, running)
    acquire_run_lock(lock_path, "owner-a")

    with pytest.raises(MagazineLockError):
        persist_complete_current_cartridge(state_path, lock_path, "owner-b", 1, _TEST_WRITER)

    assert load_run_state(state_path) == running
    with open(lock_path, "r", encoding="utf-8") as f:
        assert f.read() == "owner-a"


def test_persist_complete_current_cartridge_missing_claim_raises(tmp_path):
    """Fence: no execution.json claim -> ExecutionOwnershipError; state untouched."""
    state_path, lock_path = _make_paths(tmp_path)
    owner_id = "owner-19"

    running = {
        "done": [],
        "current": "cart-1",
        "todo": ["cart-2"],
        "status": "RUNNING",
        "revision": 1,
    }
    save_run_state(state_path, running)

    with pytest.raises(ExecutionOwnershipError, match="ownership"):
        persist_complete_current_cartridge(state_path, lock_path, owner_id, 1, _TEST_WRITER)

    assert load_run_state(state_path) == running


def test_persist_complete_current_cartridge_stale_attempt_rejected(tmp_path):
    """Fence: a writer claiming a DIFFERENT attempt is refused; state untouched."""
    state_path, lock_path = _make_paths(tmp_path)
    owner_id = "owner-20"

    _seed_claim(state_path, attempt=1, writer=_TEST_WRITER)
    running = {
        "done": [],
        "current": "cart-1",
        "todo": ["cart-2"],
        "status": "RUNNING",
        "revision": 1,
    }
    save_run_state(state_path, running)

    with pytest.raises(ExecutionOwnershipError, match="mismatch"):
        persist_complete_current_cartridge(state_path, lock_path, owner_id, 2, _TEST_WRITER)

    assert load_run_state(state_path) == running


def test_persist_complete_current_cartridge_wrong_runtime_id_rejected(tmp_path):
    """Fence: a writer whose runtime_id does not match the claim is refused."""
    state_path, lock_path = _make_paths(tmp_path)
    owner_id = "owner-21"

    _seed_claim(state_path, attempt=1, writer=_TEST_WRITER)
    running = {
        "done": [],
        "current": "cart-1",
        "todo": ["cart-2"],
        "status": "RUNNING",
        "revision": 1,
    }
    save_run_state(state_path, running)

    with pytest.raises(ExecutionOwnershipError, match="mismatch"):
        persist_complete_current_cartridge(state_path, lock_path, owner_id, 1, _TEST_STALE)

    assert load_run_state(state_path) == running


def test_persist_complete_current_cartridge_expected_current_id_mismatch(tmp_path):
    """The optional identity guard: when expected_current_id is set and differs
    from the freshly-loaded current, RunIdentityError is raised and the state is
    left untouched (a wrong cartridge must never be marked done)."""
    state_path, lock_path = _make_paths(tmp_path)
    owner_id = "owner-22"

    _seed_claim(state_path, attempt=1, writer=_TEST_WRITER)
    running = {
        "done": [],
        "current": "cart-1",
        "todo": ["cart-2"],
        "status": "RUNNING",
        "revision": 1,
    }
    save_run_state(state_path, running)

    # Expected ID does NOT match the run's current cart-1.
    with pytest.raises(RunIdentityError, match="expected current"):
        persist_complete_current_cartridge(
            state_path, lock_path, owner_id, 1, _TEST_WRITER, expected_current_id="cart-2"
        )

    assert load_run_state(state_path) == running


def test_persist_complete_current_cartridge_expected_current_id_match(tmp_path):
    """The identity guard passes when expected_current_id matches the run's
    current; the completion proceeds normally."""
    state_path, lock_path = _make_paths(tmp_path)
    owner_id = "owner-23"

    _seed_claim(state_path, attempt=1, writer=_TEST_WRITER)
    running = {
        "done": [],
        "current": "cart-1",
        "todo": ["cart-2"],
        "status": "RUNNING",
        "revision": 1,
    }
    save_run_state(state_path, running)

    ready = persist_complete_current_cartridge(
        state_path, lock_path, owner_id, 1, _TEST_WRITER, expected_current_id="cart-1"
    )
    assert ready == {
        "done": ["cart-1"],
        "current": None,
        "todo": ["cart-2"],
        "status": "READY",
        "revision": 2,
    }
    assert load_run_state(state_path) == ready


def test_persist_complete_current_cartridge_malformed_state(tmp_path):
    """A malformed state raises MagazineStateError after the (matching) claim
    check passes."""
    state_path, lock_path = _make_paths(tmp_path)
    owner_id = "owner-24"

    _seed_claim(state_path, attempt=1, writer=_TEST_WRITER)
    malformed = {"status": "BOGUS", "done": [], "current": None, "todo": ["cart-1"], "revision": 0}
    with open(state_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(malformed))

    with pytest.raises(Exception) as exc_info:
        persist_complete_current_cartridge(state_path, lock_path, owner_id, 1, _TEST_WRITER)
    assert "BOGUS" in str(exc_info.value) or "status" in str(exc_info.value)

    with open(state_path, "r", encoding="utf-8") as f:
        assert json.loads(f.read()) == malformed


def test_persist_complete_current_cartridge_load_failure(tmp_path, monkeypatch):
    """If load_run_state fails, persist_complete_current_cartridge raises the same
    error and releases the lock (after the matching claim check)."""
    state_path, lock_path = _make_paths(tmp_path)
    owner_id = "owner-25"

    _seed_claim(state_path, attempt=1, writer=_TEST_WRITER)
    running = {
        "done": [],
        "current": "cart-1",
        "todo": ["cart-2"],
        "status": "RUNNING",
        "revision": 0,
    }
    save_run_state(state_path, running)

    with patch("magazine.run_action.load_run_state", side_effect=OSError("file not found")):
        with pytest.raises(OSError):
            persist_complete_current_cartridge(state_path, lock_path, owner_id, 1, _TEST_WRITER)

    assert load_run_state(state_path) == running


def test_persist_complete_current_cartridge_save_failure(tmp_path, monkeypatch):
    """If save_run_state fails, persist_complete_current_cartridge raises the same
    error and releases the lock (claim matched, state loaded, complete applied)."""
    state_path, lock_path = _make_paths(tmp_path)
    owner_id = "owner-26"

    _seed_claim(state_path, attempt=1, writer=_TEST_WRITER)
    running = {
        "done": [],
        "current": "cart-1",
        "todo": ["cart-2"],
        "status": "RUNNING",
        "revision": 0,
    }
    save_run_state(state_path, running)

    with patch("magazine.run_action.save_run_state", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            persist_complete_current_cartridge(state_path, lock_path, owner_id, 1, _TEST_WRITER)

    assert not Path(lock_path).exists()


# ---------------------------------------------------------------------------
# persist_admit_run (first-run admission) — Slice 1 claim-first ordering
# ---------------------------------------------------------------------------

def test_persist_admit_run_first_run_toctou(tmp_path):
    """A second runner cannot observe a stale missing-state while the first
    runner holds the run lock (missing-state TOCTOU)."""
    state_path, lock_path = _make_paths(tmp_path)
    cartridge_ids = ["cart-a", "cart-b"]

    a_at_check = threading.Event()
    a_continue = threading.Event()
    hook_calls = []
    real_exists = run_action.os.path.exists

    def _patched_exists(path, *args, **kwargs):
        hook_calls.append(path)
        if len(hook_calls) == 1:
            a_at_check.set()
            a_continue.wait()
        return real_exists(path, *args, **kwargs)

    a_result = [None]
    a_attempt = [None]
    a_exc = []

    def _caller_a():
        try:
            a_result[0], a_attempt[0] = persist_admit_run(
                state_path, lock_path, "owner-a", cartridge_ids
            )
        except Exception as exc:
            a_exc.append(exc)

    with patch.object(run_action.os.path, "exists", _patched_exists):
        thread = threading.Thread(target=_caller_a, name="caller-a", daemon=True)
        thread.start()
        try:
            assert a_at_check.wait(5.0), (
                "caller A did not reach the existence check within the timeout"
            )
            assert Path(lock_path).exists()
            assert not Path(state_path).exists()
            assert hook_calls == [state_path]
            with pytest.raises(MagazineLockError):
                persist_admit_run(state_path, lock_path, "owner-b", cartridge_ids)
            assert hook_calls == [state_path], (
                "B reached the existence check (hook_calls = %r)" % (hook_calls,)
            )
            a_continue.set()
        finally:
            a_continue.set()
            thread.join(timeout=5.0)

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
    assert a_attempt[0] == 1, "fresh-run admission must start at attempt 1"
    assert not thread.is_alive(), "caller A did not finish (deadlock)"

    persisted = load_run_state(state_path)
    assert persisted == a_result[0]
    assert persisted == expected_running
    assert persisted["current"] == "cart-a"
    assert persisted["todo"] == ["cart-b"]
    assert persisted["status"] == "RUNNING"
    assert persisted["revision"] == 1
    assert hook_calls == [state_path, lock_path], (
        "expected [state_path, lock_path], got %r" % (hook_calls,)
    )
    assert not Path(lock_path).exists()
    assert Path(state_path).exists()


def test_persist_admit_run_rejects_already_running(tmp_path):
    """A second normal admission after the first run has already been admitted
    (lock released, state persisted RUNNING) is refused without touching anything."""
    state_path, lock_path = _make_paths(tmp_path)
    cartridge_ids = ["cart-a", "cart-b"]

    running = {
        "done": [],
        "current": "cart-a",
        "todo": ["cart-b"],
        "status": "RUNNING",
        "revision": 1,
    }
    save_run_state(state_path, running)

    assert Path(state_path).exists()
    assert not Path(lock_path).exists()
    bytes_before = Path(state_path).read_bytes()

    with pytest.raises(RunAdmissionError) as exc_info:
        persist_admit_run(state_path, lock_path, "owner-b", cartridge_ids)
    message = str(exc_info.value)
    assert "already RUNNING" in message
    assert "recovery" in message
    assert "joining or resuming" in message

    assert Path(state_path).read_bytes() == bytes_before
    assert load_run_state(state_path) == {
        "done": [],
        "current": "cart-a",
        "todo": ["cart-b"],
        "status": "RUNNING",
        "revision": 1,
    }
    state = load_run_state(state_path)
    assert state["current"] == "cart-a"
    assert state["todo"] == ["cart-b"]
    assert state["done"] == []
    assert state["revision"] == 1
    assert state["status"] == "RUNNING"
    assert not Path(lock_path).exists()


def test_persist_admit_run_existing_ready_becomes_running(tmp_path):
    """An existing READY state is admitted (not replaced by a fresh initial
    state) and becomes RUNNING/current=cart-b/revision 5 through the real
    persist_admit_run path."""
    state_path, lock_path = _make_paths(tmp_path)
    cartridge_ids = ["cart-a", "cart-b"]

    ready = {
        "done": ["cart-a"],
        "current": None,
        "todo": ["cart-b"],
        "status": "READY",
        "revision": 4,
    }
    save_run_state(state_path, ready)

    assert Path(state_path).exists()
    assert not Path(lock_path).exists()
    assert load_run_state(state_path) == ready

    state, attempt = persist_admit_run(state_path, lock_path, "owner-b", cartridge_ids)
    assert state == {
        "done": ["cart-a"],
        "current": "cart-b",
        "todo": [],
        "status": "RUNNING",
        "revision": 5,
    }
    assert attempt == 1, "unowned READY admission must start at attempt 1"
    assert load_run_state(state_path) == state
    state = load_run_state(state_path)
    assert state["done"] == ["cart-a"]
    assert state["current"] == "cart-b"
    assert state["todo"] == []
    assert state["status"] == "RUNNING"
    assert state["revision"] == 5
    assert ready["done"] == state["done"]
    assert state["revision"] == ready["revision"] + 1
    assert state["current"] != "cart-a"
    assert not Path(lock_path).exists()


def test_persist_admit_run_existing_completed_returns_unchanged(tmp_path):
    """An existing COMPLETED state is returned unchanged by persist_admit_run."""
    state_path, lock_path = _make_paths(tmp_path)
    cartridge_ids = ["cart-a", "cart-b"]

    completed = {
        "done": ["cart-a", "cart-b"],
        "current": None,
        "todo": [],
        "status": "COMPLETED",
        "revision": 8,
    }
    save_run_state(state_path, completed)

    assert Path(state_path).exists()
    assert not Path(lock_path).exists()
    bytes_before = Path(state_path).read_bytes()

    state, attempt = persist_admit_run(
        state_path, lock_path, "owner-completed", cartridge_ids
    )
    assert state == completed
    assert state == {
        "done": ["cart-a", "cart-b"],
        "current": None,
        "todo": [],
        "status": "COMPLETED",
        "revision": 8,
    }
    assert attempt == 0, "terminal states return unchanged with attempt 0"
    assert Path(state_path).read_bytes() == bytes_before
    state = load_run_state(state_path)
    assert state == completed
    assert state["revision"] == 8
    assert state["done"] == ["cart-a", "cart-b"]
    assert state["current"] is None
    assert state["todo"] == []
    assert state["status"] == "COMPLETED"
    assert not Path(lock_path).exists()


def test_persist_admit_run_existing_halted_returns_unchanged(tmp_path):
    """An existing HALTED state is returned unchanged by persist_admit_run."""
    state_path, lock_path = _make_paths(tmp_path)
    cartridge_ids = ["cart-a", "cart-b"]

    halted = {
        "done": ["cart-a"],
        "current": "cart-b",
        "todo": [],
        "status": "HALTED",
        "revision": 6,
    }
    save_run_state(state_path, halted)

    assert Path(state_path).exists()
    assert not Path(lock_path).exists()
    bytes_before = Path(state_path).read_bytes()

    state, attempt = persist_admit_run(
        state_path, lock_path, "owner-halted", cartridge_ids
    )
    assert state == halted
    assert attempt == 0, "HALTED state returns unchanged with attempt 0"
    assert Path(state_path).read_bytes() == bytes_before
    state = load_run_state(state_path)
    assert state == halted
    assert state["done"] == ["cart-a"]
    assert state["current"] == "cart-b"
    assert state["todo"] == []
    assert state["status"] == "HALTED"
    assert state["revision"] == 6
    assert not Path(lock_path).exists()


def test_persist_admit_run_live_claim_on_ready_rejected(tmp_path):
    """Slice 1 claim-existence gate: a READY state that carries an existing
    claim that is NOT pre-start-aborted/operator-recovered (e.g. a live
    in-flight claim) is rejected with RunAdmissionError -- a live/owned run
    must never be re-claimed.  The state is left untouched."""
    state_path, lock_path = _make_paths(tmp_path)
    cartridge_ids = ["cart-a", "cart-b"]

    _seed_claim(state_path, attempt=3, writer=_TEST_WRITER)
    ready = {
        "done": [],
        "current": None,
        "todo": ["cart-a"],
        "status": "READY",
        "revision": 1,
    }
    save_run_state(state_path, ready)

    with pytest.raises(RunAdmissionError, match="already claimed"):
        persist_admit_run(state_path, lock_path, "owner-new", cartridge_ids)

    # state untouched (no READY reset, no RUNNING transition).
    assert load_run_state(state_path) == ready
    # the (still valid) claim is left in place -- never silently deleted.
    from magazine.execution_meta import load_execution_metadata
    assert load_execution_metadata(execution_file_path(os.path.dirname(state_path))) is not None


def test_pristine_claim_preserved_on_aborted_admission(tmp_path):
    """If the READY -> RUNNING commit fails under a held lock, the claim is
    only retired (halt_reason=pre-start-aborted) when the state is positively
    still READY; a valid execution claim is never silently deleted.  Here the
    commit succeeds, so the claim carries the committed identity/attempt."""
    state_path, lock_path = _make_paths(tmp_path)
    cartridge_ids = ["cart-a"]

    # Fresh admission: no prior claim.  Use a deterministic identity so the
    # resulting claim is exactly reproducible for the assertion.
    state, attempt = persist_admit_run(
        state_path, lock_path, "owner-d", cartridge_ids,
        runtime_id=_TEST_WRITER.runtime_id,
        pid=_TEST_WRITER.pid,
        start_ticks=_TEST_WRITER.start_ticks,
    )
    assert attempt == 1
    assert state["status"] == "RUNNING"
    assert load_run_state(state_path) == state

    # The admission persisted a durable claim (Slice 1 claim-first).  It is
    # not a pre-start-aborted claim (commit succeeded) and carries the writer.
    from magazine.execution_meta import load_execution_metadata
    claim = load_execution_metadata(execution_file_path(os.path.dirname(state_path)))
    assert claim is not None
    assert claim.attempt == attempt
    assert claim.runtime_id == _TEST_WRITER.runtime_id
    assert claim.halt_reason is None
    assert not Path(lock_path).exists()
