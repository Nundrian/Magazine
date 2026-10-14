"""Runtime-aware run locking for Magazine.

Slice 4: the run lock file stores a *composite ownership key*

    {owner_id}|{runtime_id}|{pid}|{start_ticks}

so the lock can distinguish (a) the exact process/runtime that created it
(the same process re-acquiring), (b) a *different runtime* that happened to
own the same run (a stale executor after a restart, or a concurrent second
generation), and (c) a foreign owner.

The lock file is published atomically from a complete same-directory temporary
file.  Its content is the UTF-8 composite key.  No timeout, heartbeat or lease
is implemented: staleness is decided purely from the recorded process
incarnation (``pid`` + ``start_ticks``) via
``magazine.runtime_identity.process_is_extinct``.

A foreign lock is unlinked and re-acquired only when the recorded incarnation
is *positively* proven extinct (process table entry missing or ``start_ticks``
mismatch).  Unreadable, malformed, unparseable, or still-live evidence is
treated conservatively: the lock is never touched and acquisition fails with
:class:`MagazineLockError`.
"""

from __future__ import annotations

import errno
import os
import tempfile
import time
from contextlib import contextmanager

try:  # Linux is the supported execution environment.
    import fcntl
except ImportError:  # pragma: no cover - exercised only off POSIX.
    fcntl = None

from .runtime_identity import process_is_extinct

__all__ = ["acquire_run_lock", "release_run_lock", "MagazineLockError"]

_RELEASE_CONTENTION_TIMEOUT_SECONDS = 5.0
_LOCK_RETRY_INTERVAL_SECONDS = 0.01


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


def _publish_complete_lock(path: str, data: bytes) -> None:
    """Publish a complete lock without exposing partial contents.

    The composite key is fully written and closed in a same-directory
    temporary file.  ``os.link`` then publishes that inode at *path* only if
    *path* is still absent, preserving the exclusive-create contract.  A
    process killed before the link can leave only an irrelevant temporary
    file, never an empty or partial public lock.
    """
    directory = os.path.dirname(os.path.abspath(path))
    prefix = f".{os.path.basename(path)}."
    fd, temporary = tempfile.mkstemp(prefix=prefix, suffix=".tmp", dir=directory)
    closed = False
    try:
        try:
            os.fchmod(fd, 0o644)
            _write_all(fd, data)
        finally:
            os.close(fd)
            closed = True
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise MagazineLockError(
                "run lock already owned by another process"
            ) from exc
    finally:
        if not closed:
            os.close(fd)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


@contextmanager
def _hold_existing_lock_inode(path: str, *, contention_timeout: float = 0.0):
    """Exclusively serialize inspection and mutation of an existing lock.

    The advisory lock is held on the existing lock inode.  It closes the
    stale-takeover read/check/unlink race without adding a persistent lock
    registry or lease.  Acquisition callers use the default non-blocking mode
    and fail closed when another inspector holds the inode lock.  A legitimate
    release supplies a bounded *contention_timeout* so a temporary losing
    inspection cannot strand the owner's public lock indefinitely.
    """
    if fcntl is None:
        raise MagazineLockError("run locking requires Linux/POSIX flock support")
    try:
        fd = os.open(path, os.O_RDONLY)
    except FileNotFoundError as exc:
        raise MagazineLockError(
            "run lock changed during concurrent acquisition"
        ) from exc
    locked = False
    try:
        deadline = time.monotonic() + contention_timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
                remaining = deadline - time.monotonic()
                if contention_timeout <= 0 or remaining <= 0:
                    if contention_timeout > 0:
                        message = "timed out waiting to release run lock"
                    else:
                        message = "run lock is being recovered by another process"
                    raise MagazineLockError(message) from exc
                time.sleep(min(_LOCK_RETRY_INTERVAL_SECONDS, remaining))
        yield fd
    finally:
        if locked:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _read_lock_inode(fd: int) -> str:
    """Read and UTF-8 decode the complete contents of an opened lock inode."""
    os.lseek(fd, 0, os.SEEK_SET)
    chunks = []
    while True:
        chunk = os.read(fd, 4096)
        if not chunk:
            break
        chunks.append(chunk)
    try:
        return b"".join(chunks).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MagazineLockError("lock content is malformed (not UTF-8)") from exc


def _path_still_names_inode(path: str, fd: int) -> bool:
    """Return whether *path* still names the inode opened as *fd*."""
    try:
        current = os.stat(path, follow_symlinks=False)
    except FileNotFoundError:
        return False
    opened = os.fstat(fd)
    return (current.st_dev, current.st_ino) == (opened.st_dev, opened.st_ino)


def acquire_run_lock(
    path: str, owner_id: str, runtime_id: str, pid: int, start_ticks: int,
) -> None:
    """Atomically acquire the run lock at *path* for the given runtime identity.

    The complete lock file is atomically published with exclusive hard-link
    creation and stores the UTF-8 composite key

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

    try:
        _publish_complete_lock(path, data)
        return
    except MagazineLockError:
        # A public lock already exists.  Serialize the stale decision and any
        # removal against every other contender inspecting this same inode.
        pass

    with _hold_existing_lock_inode(path) as fd:
        existing = _read_lock_inode(fd)
        lock_owner, lock_runtime, lock_pid, lock_ticks = _parse_lock_content(existing)
        if (
            lock_owner == owner_id
            and lock_runtime == runtime_id
            and lock_pid == pid
            and lock_ticks == start_ticks
        ):
            # Same process/runtime as the existing lock: a re-acquire.
            raise MagazineLockError("run lock already owned by this process")
        try:
            extinct = process_is_extinct(lock_pid, lock_ticks)
        except Exception as exc:
            raise MagazineLockError(
                "cannot prove existing run-lock owner is extinct"
            ) from exc
        if extinct:
            # Positively extinct incarnation: remove the stale lock, then
            # re-acquire with this incarnation.  Verify that the pathname
            # still identifies the inode whose ownership evidence we checked;
            # never unlink a replacement based on an earlier stale read.
            if not _path_still_names_inode(path, fd):
                raise MagazineLockError(
                    "run lock changed during stale-owner verification"
                )
            try:
                os.unlink(path)
            except FileNotFoundError as exc:
                raise MagazineLockError(
                    "run lock changed during stale-owner recovery"
                ) from exc
            _publish_complete_lock(path, data)
            return
        # Still-live or unparseable foreign lock: fail closed.
        raise MagazineLockError("run lock already owned by another process")


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
    state (the caller does not get to unlink a lock it does not own).  Release
    retries temporary inode-lock contention for a bounded interval, then
    revalidates both the exact stored owner and pathname-to-inode identity
    before unlinking.
    """
    _validate_lock_content(owner_id, runtime_id, pid, start_ticks)
    expected = f"{owner_id}|{runtime_id}|{pid}|{start_ticks}"
    with _hold_existing_lock_inode(
        path, contention_timeout=_RELEASE_CONTENTION_TIMEOUT_SECONDS,
    ) as fd:
        stored = _read_lock_inode(fd)
        if stored != expected:
            raise MagazineLockError("lock is owned by another process")
        if not _path_still_names_inode(path, fd):
            raise MagazineLockError("lock changed during release")
        try:
            os.unlink(path)
        except FileNotFoundError as exc:
            raise MagazineLockError("lock changed during release") from exc
