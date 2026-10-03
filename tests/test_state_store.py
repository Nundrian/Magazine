"""Tests for the atomic persistence contract in magazine.state_store."""

from __future__ import annotations

import copy
import json
import os
import tempfile

import pytest

import magazine.durability as durability

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

    # The serialization step feeding the shared atomic writer fails; the
    # save must raise and leave no temp file behind (behavior unchanged).
    monkeypatch.setattr("json.dumps", boom)
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


# ---------------------------------------------------------------------------
# Crash-durability protocol (fsync-before-replace, durable dir swap)
# ---------------------------------------------------------------------------

def _fake_protocol_hooks(monkeypatch, events):
    """Log os.replace / os.open / os.fsync while forwarding to the real
    implementations (the write actually succeeds)."""
    real_replace = os.replace
    real_open = os.open

    def fake_replace(src, dst):
        events.append(("replace", src, dst))
        return real_replace(src, dst)

    def fake_open(path, flags, *a, **kw):
        fd = real_open(path, flags, *a, **kw)
        events.append(("open", path, flags, fd))
        return fd

    def fake_fsync(fd):
        events.append(("fsync", fd))

    monkeypatch.setattr(os, "replace", fake_replace)
    monkeypatch.setattr(os, "open", fake_open)
    monkeypatch.setattr(os, "fsync", fake_fsync)


def _assert_durable_protocol(events, target_dir):
    """The protocol must run: file fsync (on the temp file's open fd) ->
    replace -> directory fsync (on the O_RDONLY fd of the target's directory).
    The file fd is closed before the directory open, so fd numbers may be
    reused; identity is proven via the open events, not fd-number inequality."""
    i_fs = [i for i, e in enumerate(events) if e[0] == "fsync"]
    i_re = [i for i, e in enumerate(events) if e[0] == "replace"]
    assert i_fs[0] < i_re[0] < i_fs[-1], f"protocol order violated: {events}"
    fsync_fds = [e[1] for e in events if e[0] == "fsync"]
    assert len(fsync_fds) == 2, f"expected file + directory fsync, got: {events}"
    tmp_open = next(
        (e for e in events if e[0] == "open" and e[2] & os.O_EXCL),
        "no O_EXCL temp-file open found",
    )
    assert fsync_fds[0] == tmp_open[3], "file fsync must be on the temp file's fd"
    dir_opens = [e for e in events if e[0] == "open" and e[2] == os.O_RDONLY]
    assert len(dir_opens) == 1 and dir_opens[0][1] == target_dir, dir_opens
    assert fsync_fds[1] == dir_opens[0][3], "dir fsync must use the dir open's fd"


def test_file_fsynced_before_replace_and_directory_fsynced_after(tmp_path, monkeypatch):
    """save_run_state runs the full durable protocol: the file fd is fsync'd
    BEFORE os.replace (new contents reach stable storage first), and the
    containing directory is fsync'd AFTER the swap (the swap itself is
    durable)."""
    durability.directory_fsync_supported()  # pre-warm the (cached) probe
    path = str(tmp_path / "state.json")
    events = []
    _fake_protocol_hooks(monkeypatch, events)
    save_run_state(path, new_ready_state(["c1"]))
    _assert_durable_protocol(events, os.path.dirname(path))
    assert load_run_state(path) == new_ready_state(["c1"])


def test_file_fsync_failure_leaves_target_unchanged_and_no_temp(tmp_path, monkeypatch):
    """A failed file fsync (the data-durability step) raises with the
    old state intact and no temp file left behind -- the pre-fix corruption
    window (flush-only, no fsync) is closed."""
    durability.directory_fsync_supported()
    path = tmp_path / "state.json"
    # Seed a valid previous state; a failed re-save must leave it intact.
    save_run_state(str(path), new_ready_state(["a"]))

    def boom(fd):
        raise OSError("no room for data")

    monkeypatch.setattr(os, "fsync", boom)
    with pytest.raises(OSError, match="no room for data"):
        save_run_state(str(path), new_ready_state(["b"]))
    assert load_run_state(str(path)) == new_ready_state(["a"])
    assert _no_temp_files(tmp_path)


def test_directory_fsync_failure_raises_after_swap(tmp_path, monkeypatch):
    """On a supported platform a failing directory fsync (post-swap) raises
    -- a failed durability operation is never silently reported as success."
    Post-swap semantics: the destination holds the new (file-fsynced) data
    and the caller must treat the save as incomplete and retry."""
    durability.directory_fsync_supported()
    path = tmp_path / "state.json"
    state = new_ready_state(["c1"])

    real_open = os.open
    dir_fd = {}

    def fake_open(p, flags, *a, **kw):
        fd = real_open(p, flags, *a, **kw)
        if flags == os.O_RDONLY:
            dir_fd["fd"] = fd
        return fd

    def fake_fsync(fd):
        if fd == dir_fd.get("fd"):
            raise OSError("directory fsync failed")

    monkeypatch.setattr(os, "open", fake_open)
    monkeypatch.setattr(os, "fsync", fake_fsync)
    with pytest.raises(OSError, match="directory fsync failed"):
        save_run_state(str(path), state)
    assert load_run_state(str(path)) == state
    assert _no_temp_files(tmp_path)


def test_unsupported_dir_fsync_still_succeeds_with_file_fsync_only(tmp_path, monkeypatch):
    """Platform degradation (e.g. Windows): with the cached directory-fsync
    capability flag forced False the save still succeeds, exactly one fsync
    runs (the file), and the round-trip is byte-exact."""
    durability.directory_fsync_supported()
    monkeypatch.setattr(durability, "_directory_fsync_support", False)
    fsyncs = []
    monkeypatch.setattr(os, "fsync", lambda fd: fsyncs.append(fd))

    path = tmp_path / "state.json"
    save_run_state(str(path), new_ready_state(["c1"]))
    assert fsyncs.__len__() == 1, f"expected exactly one fsync, got {fsyncs}"
    assert load_run_state(str(path)) == new_ready_state(["c1"])
