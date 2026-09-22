"""Tests for the single-run locking contract in magazine.run_lock."""

from __future__ import annotations

import os

import pytest

from magazine.run_lock import (
    acquire_run_lock,
    MagazineLockError,
    release_run_lock,
)


def _lock_path(tmp_path: pytest.Path, name: str = "run.lock") -> str:
    return str(tmp_path / name)


# ---------------------------------------------------------------------------
# Acquisition
# ---------------------------------------------------------------------------

def test_first_owner_acquires_successfully(tmp_path):
    path = _lock_path(tmp_path)
    acquire_run_lock(path, "owner-a")
    assert os.path.exists(path)


def test_acquired_lock_contains_exact_owner_id(tmp_path):
    path = _lock_path(tmp_path)
    acquire_run_lock(path, "owner-a")
    assert (tmp_path / "run.lock").read_text(encoding="utf-8") == "owner-a"


def test_second_acquisition_while_locked_fails(tmp_path):
    path = _lock_path(tmp_path)
    acquire_run_lock(path, "owner-a")
    with pytest.raises(MagazineLockError):
        acquire_run_lock(path, "owner-b")


def test_existing_lock_contents_not_overwritten(tmp_path):
    path = _lock_path(tmp_path)
    acquire_run_lock(path, "owner-a")
    with pytest.raises(MagazineLockError):
        acquire_run_lock(path, "owner-b")
    assert (tmp_path / "run.lock").read_text(encoding="utf-8") == "owner-a"


def test_acquisition_with_unicode_owner_id(tmp_path):
    path = _lock_path(tmp_path)
    acquire_run_lock(path, "owner-Ünïcode-9")
    assert (tmp_path / "run.lock").read_text(encoding="utf-8") == "owner-Ünïcode-9"


def test_missing_parent_directory_not_created(tmp_path):
    path = str(tmp_path / "no" / "sub" / "run.lock")
    with pytest.raises(OSError):
        acquire_run_lock(path, "owner-a")
    assert not (tmp_path / "no").exists()


def test_simulated_write_failure_cleans_newly_created_lock(tmp_path, monkeypatch):
    path = _lock_path(tmp_path)

    def boom(*args, **kwargs):
        raise OSError("write failed")

    monkeypatch.setattr(os, "write", boom)
    with pytest.raises(OSError):
        acquire_run_lock(path, "owner-a")
    assert not os.path.exists(path)


# ---------------------------------------------------------------------------
# Partial-write handling
# ---------------------------------------------------------------------------

def test_partial_first_write_then_subsequent_writes_produce_exact_owner_id(tmp_path, monkeypatch):
    path = _lock_path(tmp_path)
    owner_id = "owner-a"
    expected = owner_id.encode("utf-8")

    real_write = os.write
    state = {"calls": 0}

    def fake_write(fd, buf):
        state["calls"] += 1
        if state["calls"] == 1:
            # First write is partial: only 3 of 7 bytes
            real_write(fd, buf[:3])
            return 3
        # Subsequent writes complete the rest
        real_write(fd, buf)
        return len(buf)

    monkeypatch.setattr(os, "write", fake_write)
    acquire_run_lock(path, owner_id)

    assert (tmp_path / "run.lock").read_bytes() == expected


def test_multiple_partial_writes_produce_exact_owner_id(tmp_path, monkeypatch):
    path = _lock_path(tmp_path)
    owner_id = "owner-a"
    expected = owner_id.encode("utf-8")

    real_write = os.write

    def fake_write(fd, buf):
        # Every write returns only 1 byte
        real_write(fd, buf[:1])
        return 1

    monkeypatch.setattr(os, "write", fake_write)
    acquire_run_lock(path, owner_id)

    assert (tmp_path / "run.lock").read_bytes() == expected


def test_zero_byte_write_before_completion_fails_and_removes_lock(tmp_path, monkeypatch):
    path = _lock_path(tmp_path)
    owner_id = "owner-a"

    real_write = os.write
    state = {"calls": 0}

    def fake_write(fd, buf):
        state["calls"] += 1
        if state["calls"] == 1:
            # First write succeeds partially
            real_write(fd, buf[:3])
            return 3
        # Second write returns 0 before completion
        return 0

    monkeypatch.setattr(os, "write", fake_write)
    with pytest.raises(OSError):
        acquire_run_lock(path, owner_id)
    assert not os.path.exists(path)


# ---------------------------------------------------------------------------
# Release
# ---------------------------------------------------------------------------

def test_matching_owner_can_release(tmp_path):
    path = _lock_path(tmp_path)
    acquire_run_lock(path, "owner-a")
    release_run_lock(path, "owner-a")
    assert not os.path.exists(path)


def test_different_owner_cannot_release(tmp_path):
    path = _lock_path(tmp_path)
    acquire_run_lock(path, "owner-a")
    with pytest.raises(MagazineLockError):
        release_run_lock(path, "owner-b")


def test_mismatched_release_leaves_lock_unchanged(tmp_path):
    path = _lock_path(tmp_path)
    acquire_run_lock(path, "owner-a")
    with pytest.raises(MagazineLockError):
        release_run_lock(path, "owner-b")
    assert os.path.exists(path)
    assert (tmp_path / "run.lock").read_text(encoding="utf-8") == "owner-a"


def test_release_of_missing_lock_fails(tmp_path):
    path = _lock_path(tmp_path)
    with pytest.raises(MagazineLockError):
        release_run_lock(path, "owner-a")
    assert not os.path.exists(path)


def test_lock_can_be_acquired_again_after_successful_release(tmp_path):
    path = _lock_path(tmp_path)
    acquire_run_lock(path, "owner-a")
    release_run_lock(path, "owner-a")
    acquire_run_lock(path, "owner-b")
    assert (tmp_path / "run.lock").read_text(encoding="utf-8") == "owner-b"


# ---------------------------------------------------------------------------
# Owner ID validation
# ---------------------------------------------------------------------------

def test_empty_owner_id_rejected(tmp_path):
    path = _lock_path(tmp_path)
    with pytest.raises(MagazineLockError):
        acquire_run_lock(path, "")
    with pytest.raises(MagazineLockError):
        release_run_lock(path, "")


def test_whitespace_only_owner_id_rejected(tmp_path):
    path = _lock_path(tmp_path)
    with pytest.raises(MagazineLockError):
        acquire_run_lock(path, "   ")
    with pytest.raises(MagazineLockError):
        release_run_lock(path, "\t\n")


def test_non_string_owner_id_rejected(tmp_path):
    path = _lock_path(tmp_path)
    with pytest.raises(MagazineLockError):
        acquire_run_lock(path, 123)
    with pytest.raises(MagazineLockError):
        release_run_lock(path, None)


def test_validation_failure_touches_no_filesystem(tmp_path, monkeypatch):
    path = _lock_path(tmp_path)
    calls = []

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        raise RuntimeError("must not be called")

    monkeypatch.setattr(os, "open", spy)
    with pytest.raises(MagazineLockError):
        acquire_run_lock(path, "")
    with pytest.raises(MagazineLockError):
        release_run_lock(path, "")
    assert calls == []
    assert not os.path.exists(path)
