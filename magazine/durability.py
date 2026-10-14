"""Shared crash-durable atomic-write primitive for Magazine's persistence
primitives (run state, cartridge results).

Atomic *replace* alone is not enough for durability: ``f.flush()`` only moves
bytes from userspace into the kernel page cache, and the ``os.replace`` rename
is itself a directory-entry update that also sits in the page cache.  On a
platform without an explicit durability protocol, a successful save return
therefore does not guarantee that the *new* data and the *swap* are on stable
storage — after a power loss or abrupt kill the kernel's cache flush order
is uncontrolled, so the target may point at a partially-written file (data
corruption) or the rename may be lost while the data survives (orphan
temp file, lost work).

This module layers the standard POSIX durability protocol on top of the
atomic replace:

    1. Open a temporary file in the target's directory (``tempfile.mkstemp``:
       exclusive creation on the same filesystem, so the swap is atomic).
    2. Write all bytes to the temporary file and flush them to the kernel.
    3. ``os.fsync`` the temporary file -- the new contents reach stable
       storage *before* anything is swapped.
    4. ``os.replace`` the temporary file into the target (atomic).
    5. ``os.fsync`` the containing directory -- the swap (a directory-entry
       update) reaches stable storage.

Guarantees
----------
On platforms that support both file fsync and directory fsync (Linux POSIX
filesystems, the current Magazine runtime), a *successful* return from
:func:`atomic_write` means the new file's contents *and* the directory entry
naming it are durably on stable storage.  Atomic replacement and the
existing public API behavior are preserved.

Platform handling
-----------------
File fsync is available on all platforms Magazine targets and is always
enforced (a failure there raises; the target is left untouched and the
temporary file is unlinked).  Directory fsync is not supported on every
platform (e.g. Windows raises "not supported").  The capability is probed
once per process (:func:`directory_fsync_supported`); where unsupported,
step 5 degrades to a documented no-op: file data is still durable and the
replace is still atomic, but directory-entry durability is not guaranteed.

Failure semantics
-----------------
A failed durability operation is never silently reported as success:

* A failure before the swap (write, file fsync, replace) raises with the
  temporary file unlinked and the target unchanged (old contents intact).
* A failed *directory* fsync on a supported platform also raises (rare; a
  disk-level fault).  By then the swap has completed, so the destination
  holds the new, file-fsynced data; the caller must treat the save as
  incomplete and retry.

"""

from __future__ import annotations

import errno
import os
import shutil
import tempfile
import sys

__all__ = [
    "atomic_write",
    "directory_fsync_supported",
    "fsync_directory",
]

# ``OSError`` errnos that mean "directory fsync is not implemented here"
# rather than "the fsync failed": ENOSYS (function not implemented by the
# filesystem) and ENOTDIR/ENOTTY (some platforms reject directory fds for
# fsync).  A *real* fsync error (EACCES, disk I/O failure, ...) is treated
# as a supported platform with a genuine failure, so it is reported.
_NOT_SUPPORTED_ERRNOS = (errno.ENOSYS, errno.ENOTDIR, errno.ENOTTY)

# Process-wide cache for the directory-fsync capability probe.  Patched by
# tests to simulate unsupported platforms; restored by monkeypatch.
_directory_fsync_support: bool | None = None


def _probe_directory_fsync_support() -> bool:
    """Probe, once per process, whether ``os.fsync`` on a directory fd is
    usable on this platform.

    Creates a scratch directory in the system temp dir and fsyncs it.  A
    probe success means directory fsync is available (e.g. Linux local
    POSIX filesystems).  A probe failure means the platform does not support
    directory fsync (e.g. Windows) or the probe filesystem does not; in
    that case :func:`fsync_directory` degrades to a no-op.  Probe errors
    that indicate a genuine (not "not supported") failure are counted as
    supported, so a real fault is still reported on later saves rather than
    being mistaken for "unsupported".

    The scratch directory is always removed, including on probe failure.
    """
    probe = tempfile.mkdtemp(prefix=".magazine_dirfsync_probe_")
    try:
        fd = os.open(probe, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        return True
    except OSError as e:
        # Windows never fsyncs directory handles (ERROR_INVALID_FUNCTION);
        # on POSIX, only "not implemented / not a directory" style errors
        # mean "unsupported" -- everything else is a real fault and must be
        # surfaced later as an error, not swallowed as a no-op.
        if sys.platform == "win32":
            return False
        # POSIX: only the "not supported" errnos mean the platform can't fsync
        # a directory -> degrade to the no-op.  Any other errno (EIO, EACCES, ...
        #) is a *genuine* fsync fault on a supported platform and must be
        # classified as supported so a real later failure stays visible.
        return e.errno not in _NOT_SUPPORTED_ERRNOS
    finally:
        shutil.rmtree(probe, ignore_errors=True)


def directory_fsync_supported() -> bool:
    """Return whether directory fsync is supported on this platform.

    Lazily probed once per process via :func:`_probe_directory_fsync_support`
    (see the module docstring for what "supported" means).  Returns ``False``
    on platforms where directory fsync is unavailable (e.g. Windows); in that
    case :func:`fsync_directory` is a documented no-op and only file-level
    durability is guaranteed.
    """
    global _directory_fsync_support
    if _directory_fsync_support is None:
        _directory_fsync_support = _probe_directory_fsync_support()
    return _directory_fsync_support


def fsync_directory(directory: str) -> None:
    """Make directory-entry changes (e.g. a just-performed ``os.replace``)
    durable.

    Must be called *after* the rename whose metadata it makes durable.  On
    supported platforms (e.g. Linux) the directory fd is opened ``O_RDONLY``
    and fsync'd, so an abrupt power loss or process kill cannot lose the
    rename.  On unsupported platforms (e.g. Windows) this is a documented
    no-op: file data is still durable (file fsync) and the replace is still
    atomic; only directory-entry durability is not guaranteed.

    Raises
    ------
    OSError
        If the directory fsync fails on a supported platform.  A failure here
        is never swallowed and is never reported as success.
    """
    if not directory_fsync_supported():
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(path: str, data: bytes, *, prefix: str = "_", suffix: str = ".tmp") -> None:
    """Atomically and crash-durably replace *path* with *data*.

    The write uses ``tempfile.mkstemp`` in the target's directory (so
    ``os.replace`` is a same-filesystem atomic swap), followed by the
    durability protocol described in the module docstring:

    1. all bytes of *data* are written to the temporary file;
    2. the temporary file fd is fsync'd -- new contents reach stable storage
       *before* the swap;
    3. ``os.replace`` swaps the temporary file into *path*;
    4. the containing directory is fsync'd (best-effort no-op on platforms
       without directory fsync).

    Missing parent directories are not created.  A failure before the swap
    (write, file fsync, replace) raises with the temporary file unlinked and
    *path* left untouched (old contents intact).  A failed directory fsync
    on a supported platform also raises (see module docstring, "Failure
    semantics"): it must never be silently reported as success.

    Parameters
    ----------
    path:
        Target path (absolute or relative, the latter resolved against the
        CWD -- the temporary file is created there too, so the swap stays
        atomic).  The parent directory must exist.
    data:
        Exact bytes to persist.
    prefix:
        ``mkstemp`` prefix for the temporary file (kept for backward
        compatibility with the existing temp-file naming, e.g.
        ``".magazine_state_"``).
    suffix:
        ``mkstemp`` suffix for the temporary file.
    """
    directory = os.path.dirname(path) or os.getcwd()
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=prefix, suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            # Step 2: file-data durability -- reach stable storage BEFORE the
            # swap (fsync must precede close; the context manager closes the
            # fd after this block, so this fsync is on the live fd).
            os.fsync(fd)
        # Step 3: atomic swap.
        os.replace(tmp_path, path)
    except BaseException:
        # Any failure before the swap: leave the target untouched and drop
        # the temporary file.  Cleanup is best-effort so it cannot mask the
        # primary exception.
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass
        raise
    # Step 4: directory-metadata durability.  Raises on supported platforms
    # if it fails (must not be treated as success).
    fsync_directory(directory)
