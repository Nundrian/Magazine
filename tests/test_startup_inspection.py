"""Tests for magazine.startup_inspection.inspect_startup."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from magazine import startup_inspection
from magazine.recovery import (
    RECOVERY_COMPLETED,
    RECOVERY_HALTED,
    RECOVERY_LOCKED,
    RECOVERY_STARTABLE,
    RECOVERY_UNCERTAIN,
)
from magazine.state_store import load_run_state

STATUSES = {
    "READY": RECOVERY_STARTABLE,
    "RUNNING": RECOVERY_UNCERTAIN,
    "COMPLETED": RECOVERY_COMPLETED,
    "HALTED": RECOVERY_HALTED,
}


def _valid_state(status: str) -> dict:
    """Build a structurally valid run state for the given status."""
    return {
        "status": status,
        "current": None if status in ("READY", "COMPLETED") else "cart-1",
        "done": [],
        "todo": [],
        "revision": 0,
    }


def _write_state(path: Path, state: dict) -> None:
    path.write_text(json.dumps(state), encoding="utf-8")


@pytest.fixture
def tmp_paths(tmp_path: Path):
    state_path = tmp_path / "state.json"
    lock_path = tmp_path / "run.lock"
    return state_path, lock_path


def test_ready_no_lock_startable(tmp_paths):
    state_path, lock_path = tmp_paths
    _write_state(state_path, _valid_state("READY"))
    assert startup_inspection.inspect_startup(state_path, lock_path) == RECOVERY_STARTABLE


def test_running_no_lock_uncertain(tmp_paths):
    state_path, lock_path = tmp_paths
    _write_state(state_path, _valid_state("RUNNING"))
    assert startup_inspection.inspect_startup(state_path, lock_path) == RECOVERY_UNCERTAIN


def test_completed_no_lock_completed(tmp_paths):
    state_path, lock_path = tmp_paths
    _write_state(state_path, _valid_state("COMPLETED"))
    assert startup_inspection.inspect_startup(state_path, lock_path) == RECOVERY_COMPLETED


def test_halted_no_lock_halted(tmp_paths):
    state_path, lock_path = tmp_paths
    _write_state(state_path, _valid_state("HALTED"))
    assert startup_inspection.inspect_startup(state_path, lock_path) == RECOVERY_HALTED


def test_valid_state_existing_lock_locked(tmp_paths):
    state_path, lock_path = tmp_paths
    _write_state(state_path, _valid_state("READY"))
    lock_path.write_text("some lock bytes", encoding="utf-8")
    assert startup_inspection.inspect_startup(state_path, lock_path) == RECOVERY_LOCKED


def test_lock_contents_irrelevant(tmp_paths):
    state_path, lock_path = tmp_paths
    _write_state(state_path, _valid_state("READY"))
    lock_path.write_text("garbage-not-a-pid", encoding="utf-8")
    assert startup_inspection.inspect_startup(state_path, lock_path) == RECOVERY_LOCKED


def test_malformed_json_propagates(tmp_paths):
    state_path, lock_path = tmp_paths
    state_path.write_text("not json at all", encoding="utf-8")
    with pytest.raises(Exception) as exc_info:
        startup_inspection.inspect_startup(state_path, lock_path)
    # The load error must propagate unchanged (same exception type as load_run_state).
    with pytest.raises(type(exc_info.value)):
        load_run_state(state_path)


def test_structurally_invalid_state_propagates(tmp_paths):
    state_path, lock_path = tmp_paths
    # Valid JSON but structurally invalid state (bad status).
    _write_state(state_path, {"status": "NOT_A_STATUS", "current": None, "done": [], "todo": [], "revision": 0})
    with pytest.raises(Exception) as exc_info:
        startup_inspection.inspect_startup(state_path, lock_path)
    with pytest.raises(type(exc_info.value)):
        load_run_state(state_path)


def test_state_file_contents_unchanged(tmp_paths):
    state_path, lock_path = tmp_paths
    state = _valid_state("READY")
    _write_state(state_path, state)
    original = state_path.read_bytes()
    startup_inspection.inspect_startup(state_path, lock_path)
    assert state_path.read_bytes() == original


def test_lock_file_contents_unchanged(tmp_paths):
    state_path, lock_path = tmp_paths
    _write_state(state_path, _valid_state("READY"))
    lock_path.write_text("lock bytes", encoding="utf-8")
    original = lock_path.read_bytes()
    startup_inspection.inspect_startup(state_path, lock_path)
    assert lock_path.read_bytes() == original


def test_inspection_does_not_create_missing_lock(tmp_paths):
    state_path, lock_path = tmp_paths
    _write_state(state_path, _valid_state("READY"))
    assert not lock_path.exists()
    startup_inspection.inspect_startup(state_path, lock_path)
    assert not lock_path.exists()


def test_classify_recovery_receives_loaded_state_and_bool_lock(tmp_paths):
    state_path, lock_path = tmp_paths
    state = _valid_state("READY")
    _write_state(state_path, state)
    lock_path.write_text("x", encoding="utf-8")

    seen = {}

    def _spy(state, lock_present):
        seen["state"] = state
        seen["lock_present"] = lock_present
        return RECOVERY_LOCKED

    original = startup_inspection.classify_recovery
    try:
        startup_inspection.classify_recovery = _spy
        result = startup_inspection.inspect_startup(state_path, lock_path)
    finally:
        startup_inspection.classify_recovery = original

    assert result == RECOVERY_LOCKED
    # The loaded state must be passed through (validated, same content).
    assert seen["state"] == state
    # The lock flag must be an actual bool (True here because the lock exists).
    assert seen["lock_present"] is True
    assert type(seen["lock_present"]) is bool

    # And when no lock exists, the flag must be False.
    seen.clear()
    lock_path.unlink()
    try:
        startup_inspection.classify_recovery = _spy
        startup_inspection.inspect_startup(state_path, lock_path)
    finally:
        startup_inspection.classify_recovery = original
    assert seen["lock_present"] is False
    assert type(seen["lock_present"]) is bool
