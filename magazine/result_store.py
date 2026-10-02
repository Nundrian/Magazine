"""Durable persistence of per-cartridge dispatch results.

The run loop in ``magazine.run_loop`` discards the ``dispatch_result`` produced
by a successful dispatch (it only inspects ``result.state``).  This module is the
first durable-output primitive: it writes the exact returned model result to a
deterministic, cartridge-specific path *inside* the run directory, before the
run is marked complete.  It mirrors the atomic-write and no-``fsync`` reliability
principles of ``magazine.state_store``.

Path convention
---------------
``<run_dir>/results/<safe_token>.txt`` where ``run_dir`` is the directory that
owns the run's ``state.json`` / ``run.lock``.  ``<safe_token>`` is a single,
filename-safe, *collision-free* token derived from the logical
``cartridge_id`` so that a malformed or hostile ID cannot escape ``results/``
or create subdirectories, and so that two distinct ``cartridge_id``s cannot
collapse to the same file.  The token is additionally verified with the project's
standard path-safety
primitive (``magazine.paths.resolve_contained_path``) so the derived path is
guaranteed to stay inside the run-owned ``results/`` directory.

Only a successful dispatch's result is persisted: a failed dispatch (or a
failed write) never reaches the guarded completion boundary.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile

from magazine.paths import resolve_contained_path

__all__ = ["result_path", "save_cartridge_result"]

# A file-name token may contain plain alphanumerics, dots, underscores, and
# hyphens.  Every other character (path separators, NUL, whitespace, ...) is
# collapsed to a single underscore.  This leaves practical, safe Magazine
# cartridge IDs byte-for-byte readable (one-to-one) while making any separator
# a harmless part of a *single* path component.  The collapse is *not* injective
# (``"a/b"`` and ``"a_b"`` both collapse to ``"a_b"``); this token is therefore
# used only for readable, all-safe IDs, and :func:`_safe_file_name` fingerprints
# the full ID in every other case so distinct IDs never collide.
_SAFE_TOKEN_RE = re.compile(r"[^A-Za-z0-9._-]")


def _safe_file_name(cartridge_id: str) -> str:
    """Derive a single, deterministic, filename-safe *and collision-free*
    token from a logical ID.

    The token contains no path separators and is never ``.`` / ``..`` / empty
    (the only single components that could traverse to a parent), so it cannot
    escape ``results/`` or create subdirectories.  Two distinct ``cartridge_id``s
    must never map to the same filename.

    The character-collapse in ``_SAFE_TOKEN_RE`` is many-to-one (e.g. ``"a/b"``
    and ``"a b"`` both collapse to ``"a_b"``), so it cannot be the sole
    identifier.  This function therefore:

    * If the ID is *already* a single, all-safe, non-trivial component
      (``_SAFE_TOKEN_RE.sub("_", id) == id`` and not ``""`` / ``"."`` / ``".."``),
      the token is returned unchanged: practical, safe cartridge IDs stay
      byte-for-byte readable (``"cart-b"`` -> ``"cart-b.txt"``).
    * For every other ID (unsafe separators/whitespace, or ``""`` / ``"."`` /
      ``".."``), the token is the full SHA-256 fingerprint of the *original*
      ID.  That is a single safe path component and is distinct for every
      distinct ID, so no two results can overwrite one another.

    Returns
    -------
    str
        A single safe file-name token (no separators; not ``.`` / ``..`` /
        empty).  Distinct ``cartridge_id``s always yield distinct tokens.
    """
    token = _SAFE_TOKEN_RE.sub("_", cartridge_id)
    if token == cartridge_id and token not in ("", ".", ".."):
        return token
    return hashlib.sha256(cartridge_id.encode("utf-8")).hexdigest()


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


def save_cartridge_result(run_dir: str, cartridge_id: str, text: object) -> str:
    """Persist the exact returned model result durably.

    The result is written UTF-8, atomically, to ``result_path(run_dir,
    cartridge_id)`` using the same reliability pattern as
    ``state_store.save_run_state``: ``tempfile.mkstemp`` in the target directory
    (so ``os.replace`` is atomic), a binary write of the UTF-8 bytes,
    ``f.flush()`` (buffers to the OS; no ``fsync``), and ``os.replace``.  The
    temporary file is unlinked on any failure.

    Parameters
    ----------
    run_dir:
        The directory that owns this run's ``state.json`` / ``run.lock``.
    cartridge_id:
        The (logical, untrusted) identifier of the dispatched cartridge.
    text:
        The model result to persist (the ``dispatch_result``).  Any ``str`` is
        written byte-for-byte; non-``str`` values are coerced via ``str()``
        (the run loop's sentinel pattern) so a result is never lost.

    Returns
    -------
    str
        The durable, verified path of the written result.

    Raises
    ------
    OSError
        If the atomic write fails (disk error, ``results`` directory not
        creatable, etc.).  The run state is left untouched by this failure.
    ValueError
        If the derived path would escape ``<run_dir>/results`` (from
        :func:`result_path`; unattainable for a sanitized token).
    """
    dest = result_path(run_dir, cartridge_id)
    results_dir = os.path.join(run_dir, "results")
    os.makedirs(results_dir, exist_ok=True)
    data = (
        text.encode("utf-8") if isinstance(text, str) else str(text).encode("utf-8")
    )
    fd, tmp = tempfile.mkstemp(dir=results_dir)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
        os.replace(tmp, dest)
        return dest
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
