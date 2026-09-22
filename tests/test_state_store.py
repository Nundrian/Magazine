"""Tests for the atomic persistence contract in magazine.state_store."""

from __future__ import annotations

import copy
import json
import os
import tempfile

import pytest

from magazine.state import (
    STATUS_COMPLETED,
    STATUS_HALTED,
    STATUS_READY,
    STATUS_RUNNING,
    MagazineStateError,
    new_ready_state,
)
from magazine.state_store import load_run_state, save_run_state


def _state(status: str, done=None, current=None, todo=None, revision=0) -> dict:
    return {
        "done": list(done or []),
        "current": current,
        "todo": list(todo or []),
        "status": status,
        "revision": revision,
    }


def _no_temp_files(directory: pytest.Path) -> bool:
    return not any(p.suffix == ".tmp" for p in directory.iterdir())


# ---------------------------------------------------------------------------
# Round trips
# ---------------------------------------------------------------------------

def test_ready_roundtrip(tmp_path):
    state = new_ready_state(["c1", "c2"])
    path = tmp_path / "state.json"
    save_run_state(str(path), state)
    assert load_run_state(str(path)) == state


def test_running_roundtrip(tmp_path):
    state = _state(STATUS_RUNNING, done=["c1"], current="c2", todo=["c3"])
    path = tmp_path / "state.json"
    save_run_state(str(path), state)
    assert load_run_state(str(path)) == state


def test_completed_roundtrip(tmp_path):
    state = _state(STATUS_COMPLETED, done=["c1", "c2"], current=None, todo=[])
    path = tmp_path / "state.json"
    save_run_state(str(path), state)
    assert load_run_state(str(path)) == state


def test_halted_roundtrip(tmp_path):
    state = _state(STATUS_HALTED, done=["c1"], current="c2", todo=["c3"])
    path = tmp_path / "state.json"
    save_run_state(str(path), state)
    assert load_run_state(str(path)) == state


# ---------------------------------------------------------------------------
# Ordering and exact values
# ---------------------------------------------------------------------------

def test_done_and_todo_ordering_preserved(tmp_path):
    state = _state(STATUS_HALTED, done=["d3", "d1", "d2"], current=None, todo=["t2", "t0", "t1"])
    path = tmp_path / "state.json"
    save_run_state(str(path), state)
    loaded = load_run_state(str(path))
    assert loaded["done"] == ["d3", "d1", "d2"]
    assert loaded["todo"] == ["t2", "t0", "t1"]


def test_exact_id_strings_preserved(tmp_path):
    ids = ["cartridge 01", "CART-Ünïcode-9", "a"]
    state = new_ready_state(ids)
    path = tmp_path / "state.json"
    save_run_state(str(path), state)
    assert load_run_state(str(path))["todo"] == ids


# ---------------------------------------------------------------------------
# Rejection
# ---------------------------------------------------------------------------

def test_malformed_json_rejected(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("not json {", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        load_run_state(str(path))


def test_invalid_state_rejected(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({"done": [], "current": None, "todo": [], "status": "BOGUS", "revision": 0}),
        encoding="utf-8",
    )
    with pytest.raises(MagazineStateError):
        load_run_state(str(path))


# ---------------------------------------------------------------------------
# Save-side contract
# ---------------------------------------------------------------------------

def test_validation_failure_performs_zero_os_replace(tmp_path, monkeypatch):
    bad = _state("BOGUS")
    calls = []

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        raise OSError("replace must not be called")

    monkeypatch.setattr(os, "replace", spy)
    with pytest.raises(MagazineStateError):
        save_run_state(str(tmp_path / "state.json"), bad)
    assert calls == []


def test_existing_target_replaced_on_successful_save(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"old": "content"}), encoding="utf-8")
    state = new_ready_state(["c1"])
    save_run_state(str(path), state)
    assert json.loads(path.read_text(encoding="utf-8")) == state


def test_target_path_not_opened_directly_for_writing(tmp_path, monkeypatch):
    path = str(tmp_path / "state.json")
    calls = []
    real_open = open

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return real_open(*args, **kwargs)

    monkeypatch.setattr("builtins.open", spy)
    save_run_state(path, new_ready_state(["c1"]))
    for call in calls:
        assert path not in call


def test_successful_save_leaves_no_temp_file(tmp_path):
    path = tmp_path / "state.json"
    save_run_state(str(path), new_ready_state(["c1"]))
    assert _no_temp_files(tmp_path)


def test_simulated_write_failure_cleans_temp_file(tmp_path, monkeypatch):
    path = str(tmp_path / "state.json")

    def boom(*args, **kwargs):
        raise OSError("write failed")

    monkeypatch.setattr("json.dump", boom)
    with pytest.raises(OSError):
        save_run_state(path, new_ready_state(["c1"]))
    assert _no_temp_files(tmp_path)


def test_simulated_replace_failure_cleans_temp_file(tmp_path, monkeypatch):
    path = str(tmp_path / "state.json")

    def boom(*args, **kwargs):
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        save_run_state(path, new_ready_state(["c1"]))
    assert _no_temp_files(tmp_path)


def test_supplied_state_unchanged(tmp_path):
    state = new_ready_state(["c1", "c2"])
    snapshot = copy.deepcopy(state)
    save_run_state(str(tmp_path / "state.json"), state)
    assert state == snapshot


def test_missing_parent_directory_not_created(tmp_path):
    path = str(tmp_path / "no" / "sub" / "state.json")
    with pytest.raises(OSError):
        save_run_state(path, new_ready_state(["c1"]))
    assert not (tmp_path / "no").exists()


# ---------------------------------------------------------------------------
# Same-directory temp contract
# ---------------------------------------------------------------------------

def test_relative_target_temp_created_in_cwd(tmp_path, monkeypatch):
    """A bare relative filename must get its temp file in the CWD, not the
    system temp dir, so os.replace() is a same-filesystem atomic swap."""
    state = new_ready_state(["c1", "c2"])
    target = "state.json"

    replace_calls = []
    real_replace = os.replace

    def spy(*args, **kwargs):
        replace_calls.append((args, kwargs))
        return real_replace(*args, **kwargs)

    monkeypatch.setattr(os, "replace", spy)

    original_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        save_run_state(target, state)

        assert len(replace_calls) == 1
        tmp_path_arg, target_path_arg = replace_calls[0][0]
        assert target_path_arg == target

        # The temp path actually passed to os.replace() must live in the same
        # directory as the target (the CWD), not the system temp directory.
        assert os.path.dirname(tmp_path_arg) == os.path.dirname(os.path.abspath(target))
        assert os.path.dirname(tmp_path_arg) == str(tmp_path)
        assert os.path.dirname(tmp_path_arg) != tempfile.gettempdir()

        # Final state loads correctly.
        assert load_run_state(target) == state

        # No temp file remains.
        assert _no_temp_files(tmp_path)
    finally:
        os.chdir(original_cwd)
