"""Single-run locking for Magazine.

Prevents two active runs from owning the same run lock at the same time.
The lock is a plain file created with exclusive creation (``O_CREAT |
O_EXCL``).  The file contains exactly the owner ID as UTF-8.  No stale-lock
recovery, timeout, retry, or process-liveness logic is implemented here.
"""

from __future__ import annotations

import os

__all__ = ["acquire_run_lock", "release_run_lock", "MagazineLockError"]


class MagazineLockError(Exception):
    """Raised when a run lock cannot be acquired or released."""


def _validate_owner_id(owner_id: str) -> None:
    """Raise :class:`MagazineLockError` if *owner_id* is not a non-empty,
    non-whitespace-only string."""
    if not isinstance(owner_id, str):
        raise MagazineLockError("owner_id must be a string")
    if not owner_id.strip():
        raise MagazineLockError("owner_id must not be empty or whitespace-only")


def acquire_run_lock(path: str, owner_id: str) -> None:
    """Atomically create the run lock at *path* owned by *owner_id*.

    *owner_id* is validated before any filesystem access.  The lock file is
    created with exclusive creation, so a second acquisition while the lock
    exists fails.  The exact *owner_id* is written as UTF-8.  If writing the
    owner ID fails, the newly created lock file is removed.  Missing parent
    directories are not created.
    """
    _validate_owner_id(owner_id)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise MagazineLockError("run lock already held")
    try:
        data = owner_id.encode("utf-8")
        offset = 0
        while offset < len(data):
            written = os.write(fd, data[offset:])
            if written == 0:
                raise OSError("zero-byte write before completion")
            offset += written
    except Exception:
        os.close(fd)
        if os.path.exists(path):
            os.unlink(path)
        raise
    os.close(fd)


def release_run_lock(path: str, owner_id: str) -> None:
    """Remove the run lock at *path* only if it is owned by *owner_id*.

    *owner_id* is validated first.  The lock file must exist.  The stored
    owner ID is read and must exactly equal *owner_id*; otherwise the lock
    is left untouched and :class:`MagazineLockError` is raised.  A matching
    lock is removed.  An ownership mismatch is not treated as stale state.
    """
    _validate_owner_id(owner_id)
    if not os.path.exists(path):
        raise MagazineLockError("lock file does not exist")
    with open(path, "r", encoding="utf-8") as f:
        stored = f.read()
    if stored != owner_id:
        raise MagazineLockError("lock is owned by another owner")
    os.unlink(path)
