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

from magazine.recovery_action import (
    persist_recover_halted,
    persist_uncertain_halt,
)
from magazine.recovery_transition import RecoveryTransitionError
from magazine.run_lock import MagazineLockError

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
    with patch("magazine.recovery_action.acquire_run_lock") as acquire, \
         patch("magazine.recovery_action.release_run_lock") as release, \
         patch("magazine.recovery_action.load_run_state", return_value=RUNNING), \
         patch("magazine.recovery_action.halt_uncertain_run", return_value=HALTED), \
         patch("magazine.recovery_action.save_run_state"):
        persist_uncertain_halt(state_path, lock_path, owner)
    acquire.assert_called_once_with(lock_path, owner)
    release.assert_called_once_with(lock_path, owner)


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


def test_halted_persisted_as_running_by_recovery(tmp_path):
    state_path, lock_path = make_paths(tmp_path)
    write_json(state_path, HALTED)
    recovered = persist_recover_halted(state_path, lock_path, "owner-1")
    assert read_json(state_path) == {
        "done": ["cart-1"],
        "current": "cart-2",
        "todo": ["cart-3"],
        "status": "RUNNING",
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


def test_current_id_preserved_exactly_by_recovery_persistence(tmp_path):
    state_path, lock_path = make_paths(tmp_path)
    write_json(state_path, HALTED)
    persist_recover_halted(state_path, lock_path, "owner-1")
    assert read_json(state_path)["current"] == "cart-2"


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
    assert read_json(state_path)["todo"] == ["cart-3", "cart-4", "cart-5"]


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
    with patch("magazine.recovery_action.acquire_run_lock") as acquire, \
         patch("magazine.recovery_action.release_run_lock") as release, \
         patch("magazine.recovery_action.load_run_state", return_value=HALTED), \
         patch("magazine.recovery_action.recover_halted_run",
               return_value={**HALTED, "status": "RUNNING", "revision": 2}), \
         patch("magazine.recovery_action.save_run_state"):
        persist_recover_halted(state_path, lock_path, owner)
    acquire.assert_called_once_with(lock_path, owner)
    release.assert_called_once_with(lock_path, owner)


def test_call_order_by_recovery(tmp_path):
    state_path, lock_path = make_paths(tmp_path)
    write_json(state_path, HALTED)
    calls = []
    with patch("magazine.recovery_action.acquire_run_lock",
               side_effect=lambda *a, **k: calls.append("acquire")), \
         patch("magazine.recovery_action.load_run_state",
               side_effect=lambda *a, **k: (calls.append("load"), HALTED)[1]), \
         patch("magazine.recovery_action.recover_halted_run",
               side_effect=lambda *a, **k: (calls.append("recover"), {**HALTED, "status": "RUNNING", "revision": 2})[1]), \
         patch("magazine.recovery_action.save_run_state",
               side_effect=lambda *a, **k: calls.append("save")), \
         patch("magazine.recovery_action.release_run_lock",
               side_effect=lambda *a, **k: calls.append("release")):
        persist_recover_halted(state_path, lock_path, "owner-1")
    assert calls == ["acquire", "load", "recover", "save", "release"]
