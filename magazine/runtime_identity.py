"""Process-lifetime runtime identity and extinction detection for Magazine.

Implements the ownership-identity primitive required by the execution-ownership
design (Slice 0): a stable, process-lifetime identifier for the Python process
that is executing a run, plus a helper that locates a run's execution metadata
file.

The identity survives OWUI *module reloads* because it is cached at module level
in ``sys.modules`` (which OWUI preserves across a Function reload). It changes
only on a genuine *process* replacement (new ``pid``) or a PID reuse
(detected via the ``start_time`` tick counter read from ``/proc/<pid>/stat``).

The design does NOT change the execution-flow code. This module only exposes the
identity primitive and its extinction detector; callers decide what to do with
them.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "RUNTIME_ID_HEX_LENGTH",
    "RUNTIME_GUARD_PATH",
    "RuntimeIdentity",
    "RuntimeIdentityError",
    "get_runtime_identity",
    "process_is_extinct",
    "_reset_runtime_identity_cache",
]


class RuntimeIdentityError(RuntimeError):
    """Raised when Linux process-incarnation evidence is unavailable."""


#: Number of hex characters in a runtime id (128-bit nonce, lowercase).
RUNTIME_ID_HEX_LENGTH = 32

#: Default guard file in /tmp carrying ``runtime_id|pid|start_ticks``.
RUNTIME_GUARD_PATH = Path("/tmp/magazine_runtime_id")

# Stable test seam and the only supported process-identity source.  Callers
# must not substitute absence of this Linux procfs root for proof of death.
_PROC_ROOT = Path("/proc")

# Index into the /proc/<pid>/stat field list (fields after the ``comm`` field,
# 0-based) that holds the process start time in clock ticks since boot -- the
# kernel ``start_time`` field. It is constant for the life of the process and
# differs between process incarnations, which makes (pid, start_time) a robust
# incarnation identifier.
#
# The relationship, documented so a field-number drift is obvious on review:
#   * Linux ``/proc/<pid>/stat`` puts ``start_time`` at 1-based field number 22.
#   * We strip the leading fields 1 (``pid``) and 2 (``comm``); ``comm`` must be
#     stripped because it may contain spaces and parentheses. The remaining list
#     then begins at field 3.
#   * Zero-based index = 22 - 3 = 19.
#
# Do NOT use field 28: that is ``start_stack`` (a virtual stack address), not
# ``start_time``. It is constant per process but ASLR-random across processes,
# so it is indistinguishable from ``start_time`` by per-process stability alone.
_START_TIME_FIELD_INDEX = 22 - 3  # == 19

#: Module-level cache: the identity for THIS Python process.
#
# This is the mechanism by which the identity survives an OWUI *module* reload:
# a reload re-runs this module but does not clear ``sys.modules`` for the core
# package, so a caller that holds a reference to the cached identity (or re-calls
# ``get_runtime_identity`` after the module is reloaded) gets the same value.
# On a genuine *process* replacement the process starts cold and the cache is
# ``None``.
_CACHED_IDENTITY: RuntimeIdentity | None = None


@dataclass(frozen=True)
class RuntimeIdentity:
    """A stable identity for one Python process incarnation.

    Attributes
    ----------
    runtime_id:
        128-bit random nonce as a 32-character lowercase hex string.
    pid:
        The ``pid`` of the process (``os.getpid()``).
    start_ticks:
        The process start time in clock ticks since boot, read from
        ``/proc/<pid>/stat`` (kernel ``start_time``). Together with ``pid`` it
        uniquely identifies this process incarnation (guards against PID reuse).
    """

    runtime_id: str
    pid: int
    start_ticks: int


def _read_guard_line(path: Path) -> tuple[str, int, int] | None:
    """Read the guard file if present and well-formed.

    Returns ``(runtime_id, pid, start_ticks)`` or ``None`` if the file is
    missing, empty, or malformed.
    """
    path = Path(path)
    if not path.is_file():
        return None
    try:
        text = path.read_text().strip()
    except OSError:
        return None
    if not text:
        return None
    parts = text.split("|")
    if len(parts) != 3:
        return None
    runtime_id, pid_text, ticks_text = parts
    try:
        pid = int(pid_text)
        start_ticks = int(ticks_text)
    except ValueError:
        return None
    return runtime_id, pid, start_ticks


def _write_guard_line(path: Path, runtime_id: str, pid: int, start_ticks: int) -> None:
    """Write ``runtime_id|pid|start_ticks`` to the guard file (one line)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{runtime_id}|{pid}|{start_ticks}\n")


def _read_proc_start_ticks(pid: int) -> int:
    """Read one process start-time value from the configured procfs root."""
    raw = (_PROC_ROOT / str(pid) / "stat").read_text()
    comm_end = raw.rindex(")") + 1
    fields = raw[comm_end:].split()
    return int(fields[_START_TIME_FIELD_INDEX])


def _linux_proc_is_reliable() -> bool:
    """Return whether procfs can identify this live process reliably."""
    try:
        _read_proc_start_ticks(os.getpid())
    except (FileNotFoundError, OSError, ValueError, IndexError):
        return False
    return True


def _stat_start_ticks(pid: int) -> int:
    """Return Linux procfs ``start_time`` ticks for *pid*.

    Missing or unreliable procfs is an unsupported runtime condition, not a
    raw filesystem error and never evidence that a process is dead.
    """
    if not _linux_proc_is_reliable():
        raise RuntimeIdentityError(
            "Linux /proc process identity is unavailable or unreliable"
        )
    try:
        return _read_proc_start_ticks(pid)
    except (FileNotFoundError, OSError, ValueError, IndexError) as exc:
        raise RuntimeIdentityError(
            f"cannot establish Linux /proc identity for pid {pid}"
        ) from exc


def _build_runtime_identity() -> RuntimeIdentity:
    """Resolve the runtime identity for THIS process.

    Reads the guard file; if its (pid, start_ticks) match the current process,
    reuses that ``runtime_id``. Otherwise generates a fresh 128-bit nonce and
    (re)writes the guard file.
    """
    pid = os.getpid()
    start_ticks = _stat_start_ticks(pid)
    stored = _read_guard_line(RUNTIME_GUARD_PATH)
    if (
        stored is not None
        and stored[1] == pid
        and stored[2] == start_ticks
    ):
        runtime_id = stored[0]
    else:
        runtime_id = secrets.token_hex(RUNTIME_ID_HEX_LENGTH // 2)
        _write_guard_line(RUNTIME_GUARD_PATH, runtime_id, pid, start_ticks)
    return RuntimeIdentity(runtime_id=runtime_id, pid=pid, start_ticks=start_ticks)


def get_runtime_identity() -> RuntimeIdentity:
    """Return the stable runtime identity for THIS Python process.

    Cached for the lifetime of the process (module-level cache), so it survives
    OWUI module reloads.  A forked child detects that its current PID differs
    from the inherited cached identity and resolves a new process-specific
    identity.  On a genuine process replacement the process is fresh and the
    identity is (re)solved from the guard file.
    """
    global _CACHED_IDENTITY
    if _CACHED_IDENTITY is not None and _CACHED_IDENTITY.pid == os.getpid():
        return _CACHED_IDENTITY
    _CACHED_IDENTITY = _build_runtime_identity()
    return _CACHED_IDENTITY


def process_is_extinct(pid: int, start_ticks: int) -> bool:
    """Return ``True`` if the process (pid, start_ticks) is no longer alive.

    ``True`` when:
      * ``/proc/<pid>/stat`` does not exist (process exited), OR
      * the ``start_time`` field for ``pid`` differs from ``start_ticks``
        (the pid was reused by a different process).

    Otherwise returns ``False`` (the same process incarnation is still alive).
    Fail-closed: any ambiguous error reading or parsing an existing ``/proc``
    entry is treated as still live.  Stale ownership requires positive
    extinction evidence, never merely unreadable evidence.
    """
    if not _linux_proc_is_reliable():
        return False
    try:
        observed = _read_proc_start_ticks(pid)
    except FileNotFoundError:
        # Re-verify procfs after the missing target read.  If procfs vanished
        # between checks, absence is ambiguous rather than proof of death.
        return _linux_proc_is_reliable()
    except (OSError, ValueError, IndexError):
        return False
    return observed != start_ticks


def _reset_runtime_identity_cache() -> None:
    """Clear the module-level identity cache.

    Public only for testing (forces re-resolution from the guard file).
    Not used by the execution flow.
    """
    global _CACHED_IDENTITY
    _CACHED_IDENTITY = None
