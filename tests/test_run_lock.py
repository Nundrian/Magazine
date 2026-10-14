"""Tests for the single-run locking contract in magazine.run_lock.

The lock file holds the *composite* ownership key
``owner_id|runtime_id|pid|start_ticks``.  A "live" identity is resolved from
:mod:`magazine.runtime_identity` (a real, in-process ``pid`` / ``start_ticks``);
a "dead" identity uses a pid that is not in ``/proc``.
"""

from __future__ import annotations

import os

import pytest

from magazine.runtime_identity import get_runtime_identity
from magazine.run_lock import (
    acquire_run_lock,
    MagazineLockError,
    release_run_lock,
)

#: A pid we can be confident is not running in this sandbox.
_DEAD_PID = 999999


def _lock_path(tmp_path: pytest.Path, name: str = "run.lock") -> str:
    return str(tmp_path / name)


def _live_runtime() -> tuple[str, int, int]:
    """Return ``(runtime_id, pid, start_ticks)`` for a live process."""
    ident = get_runtime_identity()
    return ident.runtime_id, ident.pid, ident.start_ticks


def _dead_runtime() -> tuple[str, int, int]:
    """Return a runtime identity for a process that is definitely not alive."""
    return "d" * 32, _DEAD_PID, 123


# ---------------------------------------------------------------------------
# Acquisition
# ---------------------------------------------------------------------------

def test_first_owner_acquires_successfully(tmp_path):
    path = _lock_path(tmp_path)
    acquire_run_lock(path, "owner-a", *_live_runtime())
    assert os.path.exists(path)


def test_acquired_lock_contains_exact_composite_key(tmp_path):
    path = _lock_path(tmp_path)
    runtime_id, pid, ticks = _live_runtime()
    acquire_run_lock(path, "owner-a", runtime_id, pid, ticks)
    assert (tmp_path / "run.lock").read_text(encoding="utf-8") \
        == f"owner-a|{runtime_id}|{pid}|{ticks}"


def test_second_acquisition_while_locked_fails(tmp_path):
    path = _lock_path(tmp_path)
    acquire_run_lock(path, "owner-a", *_live_runtime())
    # A different owner cannot acquire the run even though the holder's process
    # is live: the composite key differs (owner-a) and that incarnation is live.
    with pytest.raises(MagazineLockError):
        acquire_run_lock(path, "owner-b", "b" * 32, 1, 1)


def test_own_key_is_not_re_acquired(tmp_path):
    path = _lock_path(tmp_path)
    acquire_run_lock(path, "owner-a", *_live_runtime())
    with pytest.raises(MagazineLockError):
        acquire_run_lock(path, "owner-a", *_live_runtime())


def test_existing_lock_contents_not_overwritten(tmp_path):
    path = _lock_path(tmp_path)
    acquire_run_lock(path, "owner-a", *_live_runtime())
    with pytest.raises(MagazineLockError):
        acquire_run_lock(path, "owner-b", "b" * 32, 1, 1)
    # The live foreign lock (owner-a) must be left untouched.
    assert (tmp_path / "run.lock").read_text(encoding="utf-8") \
        == f"owner-a|{_live_runtime()[0]}|{_live_runtime()[1]}|{_live_runtime()[2]}"


def test_acquisition_with_unicode_owner_id(tmp_path):
    path = _lock_path(tmp_path)
    owner = "owner-\U0001F60A-unicode-9"
    runtime_id, pid, ticks = _live_runtime()
    acquire_run_lock(path, owner, runtime_id, pid, ticks)
    assert (tmp_path / "run.lock").read_text(encoding="utf-8") \
        == f"{owner}|{runtime_id}|{pid}|{ticks}"


def test_missing_parent_directory_not_created(tmp_path):
    path = str(tmp_path / "no" / "sub" / "run.lock")
    with pytest.raises(OSError):
        acquire_run_lock(path, "owner-a", *_live_runtime())
    assert not (tmp_path / "no").exists()


def test_simulated_write_failure_cleans_newly_created_lock(tmp_path, monkeypatch):
    path = _lock_path(tmp_path)

    def boom(*args, **kwargs):
        raise OSError("write failed")

    monkeypatch.setattr(os, "write", boom)
    with pytest.raises(OSError):
        acquire_run_lock(path, "owner-a", *_live_runtime())
    assert not os.path.exists(path)


# ---------------------------------------------------------------------------
# Partial-write handling (now over the composite key)
# ---------------------------------------------------------------------------

def test_partial_first_write_then_subsequent_writes_produce_exact_composite_key(tmp_path, monkeypatch):
    path = _lock_path(tmp_path)
    owner = "owner-a"
    runtime_id, pid, ticks = _live_runtime()
    expected = f"{owner}|{runtime_id}|{pid}|{ticks}".encode("utf-8")

    real_write = os.write
    state = {"calls": 0}

    def fake_write(fd, buf):
        state["calls"] += 1
        if state["calls"] == 1:
            real_write(fd, buf[:3])
            return 3
        real_write(fd, buf)
        return len(buf)

    monkeypatch.setattr(os, "write", fake_write)
    acquire_run_lock(path, owner, runtime_id, pid, ticks)

    assert (tmp_path / "run.lock").read_bytes() == expected


def test_multiple_partial_writes_produce_exact_composite_key(tmp_path, monkeypatch):
    path = _lock_path(tmp_path)
    owner = "owner-a"
    runtime_id, pid, ticks = _live_runtime()
    expected = f"{owner}|{runtime_id}|{pid}|{ticks}".encode("utf-8")

    real_write = os.write

    def fake_write(fd, buf):
        real_write(fd, buf[:1])
        return 1

    monkeypatch.setattr(os, "write", fake_write)
    acquire_run_lock(path, owner, runtime_id, pid, ticks)

    assert (tmp_path / "run.lock").read_bytes() == expected


def test_zero_byte_write_before_completion_fails_and_removes_lock(tmp_path, monkeypatch):
    path = _lock_path(tmp_path)
    owner = "owner-a"
    runtime_id, pid, ticks = _live_runtime()

    real_write = os.write
    state = {"calls": 0}

    def fake_write(fd, buf):
        state["calls"] += 1
        if state["calls"] == 1:
            real_write(fd, buf[:3])
            return 3
        return 0

    monkeypatch.setattr(os, "write", fake_write)
    with pytest.raises(OSError):
        acquire_run_lock(path, owner, runtime_id, pid, ticks)
    assert not os.path.exists(path)


# ---------------------------------------------------------------------------
# Stale-lock recovery (Slice 4)
# ---------------------------------------------------------------------------

def test_stale_foreign_lock_is_recovered(tmp_path):
    """A lock left by a positively-extinct foreign process is removed and
    re-acquired by the live writer."""
    path = _lock_path(tmp_path)
    # Simulate a stale lock: the file was written by a dead process.
    _write_raw(path, "owner-a", "a" * 32, _DEAD_PID, 7)
    acquire_run_lock(path, "owner-b", *_live_runtime())
    expected = f"owner-b|{_live_runtime()[0]}|{_live_runtime()[1]}|{_live_runtime()[2]}"
    assert (tmp_path / "run.lock").read_text(encoding="utf-8") == expected


def test_stale_recovery_via_pid_reuse(tmp_path):
    """A foreign lock whose pid is ours but whose start_ticks differ (PID reuse)
    is positively extinct and therefore recovered."""
    path = _lock_path(tmp_path)
    runtime_id, pid, ticks = _live_runtime()
    # Same pid as us, but a different incarnation's start_ticks => PID reused.
    _write_raw(path, "owner-a", "a" * 32, pid, ticks + 1)
    acquire_run_lock(path, "owner-b", *_live_runtime())
    assert (tmp_path / "run.lock").read_text(encoding="utf-8") \
        == f"owner-b|{_live_runtime()[0]}|{_live_runtime()[1]}|{_live_runtime()[2]}"


def test_live_foreign_lock_is_rejected(tmp_path):
    """A foreign lock held by a live process (same pid, correct ticks) is
    NOT removed; acquisition fails conservatively."""
    path = _lock_path(tmp_path)
    runtime_id, pid, ticks = _live_runtime()
    # owner-a holds the lock in our own (live) process incarnation.
    _write_raw(path, "owner-a", "a" * 32, pid, ticks)
    with pytest.raises(MagazineLockError):
        acquire_run_lock(path, "owner-b", "b" * 32, 1, 1)
    assert (tmp_path / "run.lock").read_text(encoding="utf-8") \
        == f"owner-a|{'a'*32}|{pid}|{ticks}"


def test_unparseable_foreign_lock_is_not_removed(tmp_path):
    path = _lock_path(tmp_path)
    # A lock we cannot parse is never removed (fail conservatively).
    with open(path, "w", encoding="utf-8") as f:
        f.write("garbage-without-the-right-shape")
    with pytest.raises(MagazineLockError):
        acquire_run_lock(path, "owner-b", *_live_runtime())
    # The unparseable lock is left in place.
    assert (tmp_path / "run.lock").read_text(encoding="utf-8") \
        == "garbage-without-the-right-shape"


def test_stale_recovery_fails_when_a_third_party_wins_retry(tmp_path, monkeypatch):
    """If unlinking the stale lock does not free it before our single retry
    (a third process created the lock in the window), acquisition fails."""
    path = _lock_path(tmp_path)
    _write_raw(path, "owner-a", "a" * 32, _DEAD_PID, 7)

    # Monkeypatch os.unlink so the stale lock is NOT removed; the single
    # re-acquire after the (failed) unlink must then observe the lock and fail.
    monkeypatch.setattr(os, "unlink", lambda *args, **kwargs: None)
    with pytest.raises(MagazineLockError):
        acquire_run_lock(path, "owner-b", *_live_runtime())
    assert os.path.exists(path)


def _write_raw(path: str, owner: str, runtime_id: str, pid: int, ticks: int) -> None:
    """Write a raw composite lock file, bypassing acquire_run_lock (simulates a
    stale lock left by a (dead) process)."""
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"{owner}|{runtime_id}|{pid}|{ticks}")


# ---------------------------------------------------------------------------
# Release
# ---------------------------------------------------------------------------

def test_matching_owner_can_release(tmp_path):
    path = _lock_path(tmp_path)
    acquire_run_lock(path, "owner-a", *_live_runtime())
    release_run_lock(path, "owner-a", *_live_runtime())
    assert not os.path.exists(path)


def test_different_owner_cannot_release(tmp_path):
    path = _lock_path(tmp_path)
    acquire_run_lock(path, "owner-a", *_live_runtime())
    with pytest.raises(MagazineLockError):
        release_run_lock(path, "owner-b", *_live_runtime())


def test_mismatched_release_leaves_lock_unchanged(tmp_path):
    path = _lock_path(tmp_path)
    acquire_run_lock(path, "owner-a", *_live_runtime())
    with pytest.raises(MagazineLockError):
        release_run_lock(path, "owner-b", *_live_runtime())
    assert os.path.exists(path)
    assert (tmp_path / "run.lock").read_text(encoding="utf-8") \
        == f"owner-a|{_live_runtime()[0]}|{_live_runtime()[1]}|{_live_runtime()[2]}"


def test_release_of_missing_lock_fails(tmp_path):
    path = _lock_path(tmp_path)
    with pytest.raises(MagazineLockError):
        release_run_lock(path, "owner-a", *_live_runtime())
    assert not os.path.exists(path)


def test_release_with_different_incarnation_is_rejected(tmp_path):
    """A release must match the exact composite key; same owner but a different
    incarnation (e.g. a reused pid) must not remove the lock."""
    path = _lock_path(tmp_path)
    runtime_id, pid, ticks = _live_runtime()
    acquire_run_lock(path, "owner-a", runtime_id, pid, ticks)
    with pytest.raises(MagazineLockError):
        release_run_lock(path, "owner-a", runtime_id, pid, ticks + 1)
    assert os.path.exists(path)


def test_lock_can_be_acquired_again_after_successful_release(tmp_path):
    path = _lock_path(tmp_path)
    acquire_run_lock(path, "owner-a", *_live_runtime())
    release_run_lock(path, "owner-a", *_live_runtime())
    acquire_run_lock(path, "owner-b", *_live_runtime())
    assert (tmp_path / "run.lock").read_text(encoding="utf-8") \
        == f"owner-b|{_live_runtime()[0]}|{_live_runtime()[1]}|{_live_runtime()[2]}"


# ---------------------------------------------------------------------------
# Owner ID / runtime identity validation
# ---------------------------------------------------------------------------

def test_empty_owner_id_rejected(tmp_path):
    path = _lock_path(tmp_path)
    with pytest.raises(MagazineLockError):
        acquire_run_lock(path, "", *_live_runtime())
    with pytest.raises(MagazineLockError):
        release_run_lock(path, "", *_live_runtime())


def test_whitespace_only_owner_id_rejected(tmp_path):
    path = _lock_path(tmp_path)
    with pytest.raises(MagazineLockError):
        acquire_run_lock(path, "   ", *_live_runtime())
    with pytest.raises(MagazineLockError):
        release_run_lock(path, "\t\n", *_live_runtime())


def test_non_string_owner_id_rejected(tmp_path):
    path = _lock_path(tmp_path)
    runtime_id, pid, ticks = _live_runtime()
    with pytest.raises(MagazineLockError):
        acquire_run_lock(path, 123, runtime_id, pid, ticks)
    with pytest.raises(MagazineLockError):
        release_run_lock(path, None, runtime_id, pid, ticks)


def test_non_string_runtime_id_rejected(tmp_path):
    path = _lock_path(tmp_path)
    runtime_id, pid, ticks = _live_runtime()
    with pytest.raises(MagazineLockError):
        acquire_run_lock(path, "owner-a", 123, pid, ticks)
    with pytest.raises(MagazineLockError):
        release_run_lock(path, "owner-a", 123, pid, ticks)


def test_negative_pid_or_ticks_rejected(tmp_path):
    path = _lock_path(tmp_path)
    runtime_id = _live_runtime()[0]
    pid, ticks = _live_runtime()[1], _live_runtime()[2]
    with pytest.raises(MagazineLockError):
        acquire_run_lock(path, "owner-a", runtime_id, -1, ticks)
    with pytest.raises(MagazineLockError):
        acquire_run_lock(path, "owner-a", runtime_id, pid, -1)
    with pytest.raises(MagazineLockError):
        release_run_lock(path, "owner-a", runtime_id, -1, ticks)
    with pytest.raises(MagazineLockError):
        release_run_lock(path, "owner-a", runtime_id, pid, -1)


def test_validation_failure_touches_no_filesystem(tmp_path, monkeypatch):
    path = _lock_path(tmp_path)
    calls = []

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        raise RuntimeError("must not be called")

    monkeypatch.setattr(os, "open", spy)
    with pytest.raises(MagazineLockError):
        acquire_run_lock(path, "", *_live_runtime())
    with pytest.raises(MagazineLockError):
        release_run_lock(path, "", *_live_runtime())
    assert calls == []
    assert not os.path.exists(path)
