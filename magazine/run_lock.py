"""Runtime-aware run locking for Magazine.

Slice 4: the run lock file stores a *composite ownership key*

    {owner_id}|{runtime_id}|{pid}|{start_ticks}

so the lock can distinguish (a) the exact process/runtime that created it
(the same process re-acquiring), (b) a *different runtime* that happened to
own the same run (a stale executor after a restart, or a concurrent second
generation), and (c) a foreign owner.

The lock file is created with ``O_CREAT | O_EXCL``.  Its content is the UTF-8
composite key.  No timeout, heartbeat or lease is implemented: staleness is
decided purely from the recorded process incarnation (``pid`` +
``start_ticks``) via ``magazine.runtime_identity.process_is_extinct``.

A foreign lock is unlinked and re-acquired only when the recorded incarnation
is *positively* proven extinct (process table entry missing or ``start_ticks``
mismatch).  Unreadable, malformed, unparseable, or still-live evidence is
treated conservatively: the lock is never touched and acquisition fails with
:class:`MagazineLockError`.
"""

from __future__ import annotations

import os
from pathlib import Path

from .runtime_identity import process_is_extinct

__all__ = ["acquire_run_lock", "release_run_lock", "MagazineLockError"]


class MagazineLockError(Exception):
    """Raised when a run lock cannot be acquired or released."""


def _validate_lock_content(owner_id: str, runtime_id: str, pid: int, start_ticks: int) -> None:
    """Validate all four lock fields before any filesystem access."""
    if not isinstance(owner_id, str):
        raise MagazineLockError("owner_id must be a string")
    if not owner_id.strip():
        raise MagazineLockError("owner_id must not be empty or whitespace-only")
    if not isinstance(runtime_id, str):
        raise MagazineLockError("runtime_id must be a string")
    if not runtime_id:
        raise MagazineLockError("runtime_id must not be empty")
    if not isinstance(pid, int) or isinstance(pid, bool):
        raise MagazineLockError("pid must be a non-negative integer")
    if pid < 0:
        raise MagazineLockError("pid must be a non-negative integer")
    if not isinstance(start_ticks, int) or isinstance(start_ticks, bool):
        raise MagazineLockError("start_ticks must be a non-negative integer")
    if start_ticks < 0:
        raise MagazineLockError("start_ticks must be a non-negative integer")


def _write_all(fd: int, data: bytes) -> None:
    """Write *data* to *fd*, looping until every byte is committed."""
    offset = 0
    while offset < len(data):
        written = os.write(fd, data[offset:])
        if written == 0:
            raise OSError("zero-byte write before completion")
        offset += written


def acquire_run_lock(
    path: str, owner_id: str, runtime_id: str, pid: int, start_ticks: int,
) -> None:
    """Atomically acquire the run lock at *path* for the given runtime identity.

    The lock file is created with exclusive creation (``O_CREAT | O_EXCL``)
    and stores the UTF-8 composite key

        ``owner_id|runtime_id|pid|start_ticks``

    All four fields are validated before any filesystem access.  If the lock
    file already exists, a three-way decision is made:

    * **Same incarnation** (identical composite key) -- the same process/runtime
      re-acquires the lock it already holds.  This raises
      :class:`MagazineLockError`; a caller must not re-acquire a lock it
      already holds (it is the caller's responsibility to release first).
    * **Foreign but positively extinct** -- the recorded ``pid`` /
      ``start_ticks`` incarnate a process that is no longer alive (entry
      missing from ``/proc`` or ``start_ticks`` mismatch indicating PID
      reuse).  The stale lock file is unlinked and re-acquired with the calling
      incarnation.  This is the only path that ever removes a pre-existing
      lock.  Unreadable or malformed ``/proc`` evidence is ambiguous and is
      therefore treated as potentially live.
    * **Foreign and still-live**, or **unparseable** -- the existing lock is
      left untouched and :class:`MagazineLockError` is raised.  An unparseable
      lock (corrupt content) is *never* unlinked: without positive proof it
      is foreign and potentially live, so removal is refused.

    :class:`MagazineLockError` is also raised when a third process wins the
    re-creation race after a stale lock is removed.
    """
    _validate_lock_content(owner_id, runtime_id, pid, start_ticks)
    data = f"{owner_id}|{runtime_id}|{pid}|{start_ticks}".encode("utf-8")

    # Fresh-creation path.  O_CREAT | O_EXCL raises FileExistsError when the
    # lock already exists.  This is the *only* filesystem access before any
    # decision is taken about an existing lock.
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        # A lock already exists: inspect it before any action.
        existing = Path(path).read_text(encoding="utf-8")
        lock_owner, lock_runtime, lock_pid, lock_ticks = _parse_lock_content(existing)
        if (
            lock_owner == owner_id
            and lock_runtime == runtime_id
            and lock_pid == pid
            and lock_ticks == start_ticks
        ):
            # Same process/runtime as the existing lock: a re-acquire.
            raise MagazineLockError("run lock already owned by this process")
        if process_is_extinct(lock_pid, lock_ticks):
            # Positively extinct incarnation: remove the stale lock, then
            # re-acquire with this incarnation.  The re-acquire itself may
            # fail if a third process wins the race (fail closed).
            os.unlink(path)
            try:
                fd2 = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                raise MagazineLockError(
                    "run lock already owned by another process (race on stale recovery)"
                )
            try:
                _write_all(fd2, data)
            finally:
                os.close(fd2)
            return
        # Still-live or unparseable foreign lock: fail closed.
        raise MagazineLockError("run lock already owned by another process")

    # Fresh lock: write the composite key.
    try:
        _write_all(fd, data)
    except Exception:
        # A half-written lock is removed; the partial file must never be
        # left behind to masquerade as an owner.
        os.unlink(path)
        raise
    finally:
        os.close(fd)


def _parse_lock_content(raw: str) -> tuple[str, str, int, int]:
    """Parse a composite lock file content.  Raises on malformed content."""
    parts = raw.split("|")
    if len(parts) != 4:
        raise MagazineLockError("lock content is malformed (expected 4 parts)")
    lock_owner, lock_runtime, lock_pid_s, lock_ticks_s = parts
    if not lock_owner:
        raise MagazineLockError("lock content is malformed (empty owner_id)")
    if not lock_runtime:
        raise MagazineLockError("lock content is malformed (empty runtime_id)")
    try:
        lock_pid = int(lock_pid_s)
    except ValueError:
        raise MagazineLockError("lock content is malformed (bad pid)")
    if lock_pid < 0:
        raise MagazineLockError("lock content is malformed (negative pid)")
    try:
        lock_ticks = int(lock_ticks_s)
    except ValueError:
        raise MagazineLockError("lock content is malformed (bad start_ticks)")
    if lock_ticks < 0:
        raise MagazineLockError("lock content is malformed (negative start_ticks)")
    return lock_owner, lock_runtime, lock_pid, lock_ticks


def release_run_lock(
    path: str, owner_id: str, runtime_id: str, pid: int, start_ticks: int,
) -> None:
    """Release the run lock at *path* only if it is owned by the caller.

    The stored composite key is read and must exactly match the caller's
    ``owner_id`` / ``runtime_id`` / ``pid`` / ``start_ticks``; otherwise the
    lock is left untouched and :class:`MagazineLockError` is raised.  A
    matching lock is removed.  An ownership mismatch is not treated as stale
    state (the caller does not get to unlink a lock it does not own).
    """
    _validate_lock_content(owner_id, runtime_id, pid, start_ticks)
    if not os.path.exists(path):
        raise MagazineLockError("lock file does not exist")
    with open(path, "r", encoding="utf-8") as f:
        stored = f.read()
    expected = f"{owner_id}|{runtime_id}|{pid}|{start_ticks}"
    if stored != expected:
        raise MagazineLockError("lock is owned by another process")
    os.unlink(path)
