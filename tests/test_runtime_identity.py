"""Tests for magazine.runtime_identity.

Covers the process-incarnation identity primitive and the ``/proc``-based
extinction proof required by the execution-ownership design (Slice 0):

* ``get_runtime_identity`` is stable within a process and stable across an
  in-process *module* reload (the guard file matches the same ``(pid, stime)``).
* It changes on a genuine *process* replacement (new ``pid``/new ``stime``) or
  when the ``(pid, stime)`` composite differs from the guard file.
* ``process_is_extinct`` distinguishes a dead process (``True``), a live
  process (``False``), and a *reused* pid (a different process now occupying
  the pid, ``True``).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import magazine.runtime_identity as ri
from magazine.runtime_identity import (
    RUNTIME_ID_HEX_LENGTH,
    RuntimeIdentity,
    get_runtime_identity,
    process_is_extinct,
)


def _valid_runtime_id() -> str:
    """A deterministic 32-char lowercase-hex nonce for fixtures."""
    return "0" * RUNTIME_ID_HEX_LENGTH


def _self_start_ticks() -> int:
    """Read this process's ``start_time`` field from /proc/<pid>/stat.

    Uses the documented 0-based index 19 into the post-``comm`` field list
    (1-based field 22 = the kernel ``start_time`` ticks counter). This reads
    the field independently of ``magazine.runtime_identity`` so a later drift
    in the implementation's index is detected by the cross-check tests, which
    compare the test-supplied ticks against the implementation's own read.
    """
    raw = (Path("/proc") / str(os.getpid()) / "stat").read_text()
    return int(raw[raw.rindex(")") + 1:].split()[19])


@pytest.fixture()
def isolated_guard(tmp_path, monkeypatch):
    """Point the guard file at a unique temp location per test.

    Also clears the module-level identity cache so a test starts fresh
    (no stale identity from an earlier test in the session).
    """
    monkeypatch.setattr(ri, "RUNTIME_GUARD_PATH", tmp_path / "guard.txt")
    ri._reset_runtime_identity_cache()
    yield tmp_path
    ri._reset_runtime_identity_cache()


class TestGetRuntimeIdentity:
    def test_returns_a_valid_identity(self, isolated_guard) -> None:
        identity = get_runtime_identity()
        assert isinstance(identity, RuntimeIdentity)
        assert len(identity.runtime_id) == RUNTIME_ID_HEX_LENGTH
        assert all(c in "0123456789abcdef" for c in identity.runtime_id)
        assert identity.pid == os.getpid()
        assert isinstance(identity.start_ticks, int)
        assert identity.start_ticks >= 0

    def test_is_cached_and_stable_within_a_process(self, isolated_guard) -> None:
        first = get_runtime_identity()
        second = get_runtime_identity()
        assert first.runtime_id == second.runtime_id
        assert first.pid == second.pid
        assert first.start_ticks == second.start_ticks
        assert first == second

    def test_guard_file_is_written_with_the_identity(self, isolated_guard) -> None:
        guard = Path(str(isolated_guard)) / "guard.txt"
        identity = get_runtime_identity()
        assert guard.is_file()
        stored = guard.read_text().strip().split("|")
        assert stored == [identity.runtime_id, str(identity.pid), str(identity.start_ticks)]

    def test_guard_file_matching_composite_reuses_runtime_id(self, isolated_guard) -> None:
        guard = Path(str(isolated_guard)) / "guard.txt"
        known_id = _valid_runtime_id()
        guard.write_text(f"{known_id}|{os.getpid()}|{_self_start_ticks()}\n")
        ri._reset_runtime_identity_cache()
        assert get_runtime_identity().runtime_id == known_id

    def test_guard_file_mismatched_composite_generates_new_runtime_id(self, isolated_guard) -> None:
        guard = Path(str(isolated_guard)) / "guard.txt"
        known_id = _valid_runtime_id()
        # A *different* pid than ours -> composite mismatch -> fresh nonce.
        guard.write_text(f"{known_id}|{os.getpid() + 1}|{_self_start_ticks()}\n")
        ri._reset_runtime_identity_cache()
        identity = get_runtime_identity()
        assert identity.runtime_id != known_id
        # The guard file must now carry *ours* (pid, start_ticks) + the new id.
        stored = guard.read_text().strip().split("|")
        assert stored[0] == identity.runtime_id
        assert int(stored[1]) == os.getpid()
        assert int(stored[2]) == identity.start_ticks

    def test_guard_file_missing_generates_and_writes(self, isolated_guard) -> None:
        guard = Path(str(isolated_guard)) / "guard.txt"
        assert not guard.exists()
        identity = get_runtime_identity()
        assert guard.is_file()
        assert identity.pid == os.getpid()

    def test_malformed_guard_file_generates_new_runtime_id(self, isolated_guard) -> None:
        guard = Path(str(isolated_guard)) / "guard.txt"
        guard.write_text("not-a-valid-guard-line\n")
        ri._reset_runtime_identity_cache()
        identity = get_runtime_identity()
        # Malformed -> treated as mismatch -> fresh id, guard rewritten.
        assert identity.pid == os.getpid()
        assert len(identity.runtime_id) == RUNTIME_ID_HEX_LENGTH
        stored = guard.read_text().strip().split("|")
        assert stored[0] == identity.runtime_id

    def test_in_process_reload_via_guard_still_same_identity(self, isolated_guard) -> None:
        """Simulate an OWUI Function reload: the *module* is re-run (cache
        cleared) but it is the *same* process, so the guard matches and the
        same runtime_id is reused."""
        guard = Path(str(isolated_guard)) / "guard.txt"
        identity_a = get_runtime_identity()
        assert identity_a is not None
        # Emulate a module reload: drop the module-level cache.
        ri._reset_runtime_identity_cache()
        identity_b = get_runtime_identity()
        assert identity_b.runtime_id == identity_a.runtime_id
        assert identity_b.pid == identity_a.pid

    def test_genuine_process_replacement_yields_new_runtime_id(self) -> None:
        """A child *process* (different pid, different start_ticks) resolves a
        different runtime_id than the parent."""
        import os as _os

        # Use the canonical guard path here so the child can read it. Delete
        # any stale file and clear the parent cache to make the parent's
        # identity a fresh, well-defined reference.
        ri._reset_runtime_identity_cache()
        canonical = ri.Path("/tmp/magazine_runtime_id")
        if canonical.is_file():
            canonical.unlink()

        parent = get_runtime_identity()
        assert parent.pid == _os.getpid()
        assert canonical.read_text().strip().split("|")[0] == parent.runtime_id

        child_code = "import magazine.runtime_identity as ri; print(ri.get_runtime_identity().runtime_id)"
        proc = subprocess.run(
            [sys.executable, "-c", child_code],
            cwd=str(_os.path.abspath("")),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0, proc.stderr
        child_runtime_id = proc.stdout.strip()
        assert child_runtime_id
        assert child_runtime_id != parent.runtime_id
        # Tidy up the shared file so a later test with the canonical path
        # does not see the child's state.
        if canonical.is_file():
            canonical.unlink()


class TestProcessIsExtinct:
    def test_dead_pid_is_extinct(self) -> None:
        # A pid that has exited: /proc/<pid>/stat is absent.
        assert process_is_extinct(999999999, 0) is True

    def test_live_pid_with_matching_stime_is_not_extinct(self) -> None:
        pid = os.getpid()
        ticks = _self_start_ticks()
        assert process_is_extinct(pid, ticks) is False
        # A second read a moment later: same ticks (process still alive).
        assert process_is_extinct(pid, ticks) is False

    def test_live_pid_with_wrong_stime_is_extinct(self) -> None:
        """A reused pid (different start_time) must be reported extinct, even
        though /proc/<pid>/stat exists."""
        pid = os.getpid()
        ticks = _self_start_ticks()
        assert process_is_extinct(pid, ticks + 1) is True

    def test_extinct_fails_conservative_for_unreadable_existing_stat(
        self, monkeypatch
    ) -> None:
        stat_path = Path("/proc") / "123" / "stat"
        monkeypatch.setattr(Path, "is_file", lambda self: self == stat_path)
        monkeypatch.setattr(
            Path,
            "read_text",
            lambda self: (_ for _ in ()).throw(PermissionError("denied")),
        )
        assert process_is_extinct(123, 456) is False

    def test_reused_pid_via_live_other_process_is_extinct(self) -> None:
        """Simulate a *reused* pid: the live occupant of a pid has a start_time
        that differs from the stale claim's, so the old owner's (pid, stime) is
        reported extinct even though /proc/<pid>/stat exists. We prove it with a
        live child process queried with the *parent's* start_ticks."""
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time as _t; _t.sleep(2)"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            child_pid = proc.pid
            assert child_pid != os.getpid()
            # /proc/<child_pid>/stat exists, but its start_time is NOT the
            # parent's -> the stale claim's (pid, stime) is extinct.
            assert process_is_extinct(child_pid, _self_start_ticks()) is True
        finally:
            proc.terminate()
            proc.wait()


class TestStartTicksField:
    def test_field_is_ticks_not_memory_address(self) -> None:
        """The documented index must point at the ticks counter (start_time),

        not a virtual memory address. We distinguish by magnitude: a real
        start_time is bounded above by uptime * HZ, and HZ is always
        << 100000, whereas a stack address is ~1e14 (it would exceed the
        bound unless the host were up for decades)."""
        ticks = _self_start_ticks()
        assert ticks >= 0
        uptime_s = float(Path("/proc/uptime").read_text().split()[0])
        assert ticks < uptime_s * 100000, (
            f"start_ticks={ticks} is not a plausible clock-tick count for "
            f"uptime={uptime_s:.1f}s; the index is reading a memory address "
            f"(e.g. start_stack), not start_time"
        )
