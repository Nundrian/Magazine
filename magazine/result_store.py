"""Durable persistence of per-cartridge dispatch results.

The run loop in ``magazine.run_loop`` discards the ``dispatch_result`` produced
by a successful dispatch (it only inspects ``result.state``).  This module is the
first durable-output primitive: it writes the exact returned model result to a
deterministic, cartridge-specific path *inside* the run directory, before the
run is marked complete.  It mirrors the durable atomic-write of
``magazine.state_store``: the write goes through the shared crash-durable
protocol of :mod:`magazine.durability` (temp file in the target directory,
file fsync before the atomic ``os.replace``, directory fsync afterwards
where supported).

Slice 2 execution ownership
---------------------------
The result write is *fenced*, not merely locked.  Under the same run-lock hold,
the writer's exact execution-ownership claim (``attempt`` + ``runtime_id``
from ``execution.json``) is verified, and the run's freshly-loaded status must
still be ``RUNNING``.  The verification and the mutation are serialized under a
*single* lock acquisition, closing the TOCTOU window between a "check" and the
durable write:

    with run lock held:
        claim  = load_execution_metadata(...)          # (1) fencing claim
        verify claim.attempt == writer_attempt and
               claim.runtime_id == writer_identity.runtime_id
        state  = load_run_state(...)                  # (2) fresh, not a
        verify state.status == RUNNING                #     cached snapshot

Path convention
---------------
``<run_dir>/results/<safe_token>.txt`` where ``run_dir`` is the directory that
owns the run's ``state.json`` / ``run.lock``.  ``<safe_token>`` is a single,
filename-safe, *collision-resistant* token derived from the logical
``cartridge_id`` so that a malformed or hostile ID cannot escape ``results/``
or create subdirectories, and so that two distinct ``cartridge_id``s cannot
collapse to the same file.  Unsafe IDs are fingerprinted into the reserved
``__sha256__`` namespace (which no literal safe ID may occupy), so that a safe
ID that happens to equal some ID's digest cannot collide with the hashed ID.  The
token is additionally verified with the project's standard path-safety
primitive (``magazine.paths.resolve_contained_path``) so the derived path is
guaranteed to stay inside the run-owned ``results/`` directory.

Only a successful dispatch's result is persisted: a failed dispatch (or a
failed write) never reaches the guarded completion boundary.  The fenced
``save_cartridge_result`` is the *only* writer of a result; a stale executor's
rejected write is an error (it is never a run-side transition).
"""

from __future__ import annotations

import hashlib
import os
import re
from contextlib import contextmanager

from magazine.durability import atomic_write
from magazine.execution_meta import ExecutionOwnershipError, load_execution_metadata
from magazine.paths import execution_file_path, resolve_contained_path
from magazine.run_lock import acquire_run_lock, release_run_lock
from magazine.run_transition import RunTransitionError
from magazine.runtime_identity import RuntimeIdentity
from magazine.state import STATUS_RUNNING
from magazine.state_store import load_run_state

__all__ = ["result_path", "save_cartridge_result"]

# A file-name token may contain plain alphanumerics, dots, underscores, and
# hyphens.  Every other character (path separators, NUL, whitespace, ...) is
# collapsed to a single underscore.  This leaves practical, safe Magazine
# cartridge IDs byte-for-byte readable (one-to-one) while making any separator
# a harmless part of a *single* path component.  The collapse is *not*
# injective (``"a/b"`` and ``"a_b"`` both collapse to ``"a_b"``); this token is
# therefore used only for readable, all-safe IDs, and :func:`_safe_file_name`
# fingerprints the full ID into the reserved ``__sha256__`` namespace in every
# other case.
_SAFE_TOKEN_RE = re.compile(r"[^A-Za-z0-9._-]")

# Reserved namespace for fingerprinted result files.  Filenames of the form
# ``__sha256__<hex>`` are always produced by :func:`_safe_file_name`'s hashing
# branch; no literal safe cartridge ID may return its token unchanged while
# beginning with this prefix, otherwise a literal ID could occupy the hashed
# namespace and collide with a hashed ID.
_HASH_PREFIX = "__sha256__"


def _safe_file_name(cartridge_id: str) -> str:
    """Derive a single, deterministic, filename-safe, *collision-resistant*
    token from a logical ID.

    The token contains no path separators and is never ``.`` / ``..`` / empty
    (the only single components that could traverse to a parent), so it cannot
    escape ``results/`` or create subdirectories.

    The character-collapse in ``_SAFE_TOKEN_RE`` is many-to-one (e.g.
    ``"a/b"`` and ``"a b"`` both collapse to ``"a_b"``), so it cannot be the
    sole identifier.  Two result namespaces are used:

    * **Readable namespace.**  If the ID is *already* a single, all-safe,
      non-trivial component *and does not use the reserved hash prefix*, the
      token is returned unchanged: ordinary, safe cartridge IDs stay byte-for-
      byte readable (``"cart-b"`` -> ``"cart-b.txt"``).
    * **Reserved hash namespace.**  Every other ID -- unsafe separators/
      whitespace, ``""`` / ``"."`` / ``".."``, or a literal ID that already
      begins with ``_HASH_PREFIX`` -- is written as ``__sha256__`` followed by
      the full SHA-256 fingerprint of the *original* ID.  SHA-256 is
      collision-*resistant* (not provably collision-free), so a distinct ID is
      distinct in practice; the ``__sha256__`` prefix additionally guarantees the
      hashed namespace is exclusive: no literal safe ID can return a token that
      equals a hash (they all begin with ``__sha256__``), so a safe ID that
      happens to equal some ID's digest never collides with the hashed ID.

    Returns
    -------
    str
        A single safe file-name token (no separators; not ``.`` / ``..`` /
        empty).  Distinct ``cartridge_id``s yield distinct tokens.
    """
    token = _SAFE_TOKEN_RE.sub("_", cartridge_id)
    readable = (
        token == cartridge_id
        and token not in ("", ".", "..")
        and not token.startswith(_HASH_PREFIX)
    )
    if readable:
        return token
    return _HASH_PREFIX + hashlib.sha256(cartridge_id.encode("utf-8")).hexdigest()


def result_path(run_dir: str, cartridge_id: str) -> str:
    """Return the deterministic path of a cartridge's durable result.

    The path is ``<run_dir>/results/<safe_token>.txt`` where ``<safe_token>``
    is the filename-safe derivation of ``cartridge_id`` (see
    :func:`_safe_file_name`).  The path is verified against ``<run_dir>/results``
    with ``magazine.paths.resolve_contained_path`` before being returned, so the
    result is *guaranteed* to remain inside the run-owned ``results/`` directory
    even for a hostile ``cartridge_id``.

    Parameters
    ----------
    run_dir:
        The directory that owns this run's ``state.json`` / ``run.lock``.
    cartridge_id:
        The (logical, untrusted) identifier of the dispatched cartridge.

    Returns
    -------
    str
        The verified result path (a child of ``<run_dir>/results``).

    Raises
    ------
    ValueError
        If the derived path would escape ``<run_dir>/results`` (the
        ``resolve_contained_path`` containment check).  Unattainable for a
        token produced by :func:`_safe_file_name`; a defensive guarantee.
    """
    root = os.path.join(run_dir, "results")
    token = _safe_file_name(cartridge_id)
    # resolve_contained_path verifies (root / relative) stays inside *root* and
    # rejects an absolute / empty / escaping relative component.  The token is
    # never absolute, empty, or separator-bearing, so this always passes.
    resolve_contained_path(root, f"{token}.txt")
    return os.path.join(run_dir, "results", f"{token}.txt")


@contextmanager
def _hold_run_lock(
    lock_path: str, owner_id: str, writer_identity: RuntimeIdentity,
):
    """Acquire the run lock for the body and release it (always).

    The body performs the ownership verification *and* the durable write while
    the lock is held, so the check and the mutation are serialised against every
    other executor of the run.  The runtime identity (``runtime_id`` / ``pid`` /
    ``start_ticks``) is carried in the composite ownership key so a stale
    executor (a different incarnation) cannot hold the lock.
    """
    acquire_run_lock(
        lock_path,
        owner_id,
        writer_identity.runtime_id,
        writer_identity.pid,
        writer_identity.start_ticks,
    )
    try:
        yield
    finally:
        release_run_lock(
            lock_path,
            owner_id,
            writer_identity.runtime_id,
            writer_identity.pid,
            writer_identity.start_ticks,
        )


def save_cartridge_result(
    run_dir: str,
    cartridge_id: str,
    text: str,
    lock_path: str,
    owner_id: str,
    writer_attempt: int,
    writer_identity: RuntimeIdentity,
) -> str:
    """Persist the exact returned model result durably.

    The result is written UTF-8, atomically and crash-durably, to
    ``result_path(run_dir, cartridge_id)`` via :mod:`magazine.durability`'s shared
    durable atomic write: ``tempfile.mkstemp`` in the target directory (so
    ``os.replace`` is atomic), a binary write of the UTF-8 bytes, ``f.flush()``
    and ``fsync`` of the temporary file before the atomic ``os.replace`` (new
    contents reach stable storage before the swap), then a fsync of the
    containing directory where the platform supports directory fsync (best-effort
    no-op on platforms that do not, e.g. Windows).  The temporary file is
    unlinked on any failure before the swap.

    Slice 2 fencing: under the run lock the writer's execution-ownership claim
    is verified (``execution.json`` must exist and its ``attempt`` /
    ``runtime_id`` must exactly match ``writer_attempt`` /
    ``writer_identity.runtime_id``), and the freshly-loaded run status must be
    ``RUNNING``.  These checks and the write share one lock acquisition, so no
    stale executor (a different ``attempt`` / ``runtime_id``) or a run that is
    no longer ``RUNNING`` can persist a result.

    Parameters
    ----------
    run_dir:
        The directory that owns this run's ``state.json`` / ``run.lock``.
    cartridge_id:
        The (logical, untrusted) identifier of the dispatched cartridge.
    text:
        The model result to persist (the ``dispatch_result``).  Any ``str`` is
        written byte-for-byte.  Non-string values are invalid and rejected
        before any persistence work begins.
    lock_path:
        Path of the run's lock file, acquired while the write proceeds.
    owner_id:
        The owner identifier for the lock acquisition.
    writer_attempt:
        The ``attempt`` the writer claims as its current execution identity.
    writer_identity:
        The ``RuntimeIdentity`` of the writer; ``runtime_id`` is the fencing
        identity.

    Returns
    -------
    str
        The durable, verified path of the written result.

    Raises
    ------
    ExecutionOwnershipError
        If the claim is missing or its ``attempt`` / ``runtime_id`` do not
        exactly match the writer's identity, or if the run's status is not
        ``RUNNING``.  The result is not written; the run state is untouched.
    OSError
        If the atomic write fails (disk error, ``results`` directory not
        creatable, etc.).
    ValueError
        If the derived path would escape ``<run_dir>/results`` (from
        :func:`result_path`; unattainable for a sanitized token).
    TypeError
        If *text* is not a string.
    """
    if not isinstance(text, str):
        raise TypeError(
            "dispatch result must be a string, "
            f"got {type(text).__name__}"
        )
    state_path = os.path.join(run_dir, "state.json")
    execution_path = execution_file_path(run_dir)

    with _hold_run_lock(lock_path, owner_id, writer_identity):
        # (1) Fencing claim: exact ownership identity under the same lock hold.
        claim = load_execution_metadata(execution_path)
        if claim is None:
            raise ExecutionOwnershipError(
                f"execution ownership claim not found at {execution_path}"
            )
        if claim.attempt != writer_attempt or claim.runtime_id != writer_identity.runtime_id:
            raise ExecutionOwnershipError(
                f"execution ownership mismatch: claim={claim.attempt}/{claim.runtime_id} "
                f"writer={writer_attempt}/{writer_identity.runtime_id}"
            )
        # (2) Fresh state: the run must still be RUNNING (no cached snapshot).
        state = load_run_state(state_path)
        if state.get("status") != STATUS_RUNNING:
            raise RunTransitionError(
                f"cannot save result for run in status {state.get('status')}; "
                f"expected {STATUS_RUNNING}"
            )

        # Durable write, still under the same lock hold.
        dest = result_path(run_dir, cartridge_id)
        results_dir = os.path.join(run_dir, "results")
        os.makedirs(results_dir, exist_ok=True)
        data = text.encode("utf-8")
        atomic_write(dest, data, prefix=".result_", suffix=".tmp")
        return dest
