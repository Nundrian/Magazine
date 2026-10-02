"""Shared run-preparation boundary.

Centralizes the composition that the entrypoint used to own directly.  This
module is neutral and shared: both the synchronous CLI adapter
(:mod:`magazine.entrypoint`) and the async runner consume
:func:`prepare_run`, so that

  * two composition layers do not independently reconstruct ``state_path`` /
    ``lock_path``;
  * the async runner does not depend on a private CLI helper;
  * ``state`` and the lock path are handed out once, so the runner never
    re-derives paths or re-reads state between admission and dispatch.

This module is strictly *preparation*: it validates the owner ID, loads and
validates the magazine through the single document boundary
(:mod:`magazine.document`), creates the run directory, derives the run
paths exactly once, and performs the single atomic first-run admission via
:func:`magazine.run_action.persist_admit_run`.  It does not print, does not
map errors to exit codes, does not catch :class:`magazine.run_action.RunAdmissionError`
or :class:`magazine.run_lock.MagazineLockError`, does not dispatch any
cartridge, and does not re-read state after the admission returns.  Every
exception propagates unchanged, and the admission lock is released by
``persist_admit_run`` before this function returns.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from magazine import document
from magazine import run_action
from magazine import run_lock
from magazine import state


__all__ = ["PreparedRun", "prepare_run"]


@dataclass(frozen=True)
class PreparedRun:
    """Immutable result of one run preparation.

    :attr:`state` is the *exact* authoritative dict returned by
    :func:`magazine.run_action.persist_admit_run` — not a copy re-read from
    disk.  :attr:`state_path` and :attr:`lock_path` are the exact paths used
    for that admission, derived once, so a consuming runner need not recompute
    them.
    """

    state: dict
    state_path: str
    lock_path: str


def prepare_run(
    magazine_path: str,
    state_dir: str,
    owner_id: str,
) -> PreparedRun:
    """Load, validate, and admit a first run; return a :class:`PreparedRun`.

    Args:
        magazine_path: Path to a Magazine document (JSON file or unpacked
            Magazine folder).
        state_dir: Directory containing the run/ state subdirectory.
        owner_id: Owner ID written into the run lock.

    Returns:
        A :class:`PreparedRun` whose ``state`` is the exact dict returned by
        :func:`magazine.run_action.persist_admit_run` and whose ``state_path`` /
        ``lock_path`` are the paths that admission used.

    Raises:
        All exceptions from the individual steps propagate unchanged (schema
        errors, :class:`magazine.run_lock.MagazineLockError`,
        :class:`magazine.run_action.RunAdmissionError`, state errors).  This
        function performs no catching, no retry, and no dispatch.
    """
    # Validate the lock owner before the lock layer sees it (public mirror of
    # run_lock's owner-id contract; better error text than a lock error).
    if not isinstance(owner_id, str) or not owner_id.strip():
        raise run_lock.MagazineLockError(
            "owner_id must not be empty or whitespace-only"
        )

    # 1. Load the magazine document through the single loading boundary.
    #    Strict-JSON parsing and schema validation happen inside the boundary,
    #    before any filesystem side effect here.  For an unpacked folder the
    #    boundary also returns the immutable execution plan; the compatibility
    #    document (`.document`) drives the once-derived cartridge IDs, exactly
    #    as before, so admission is unchanged.
    magazine_load = document.load_document(magazine_path)
    cartridge_ids = [c["id"] for c in magazine_load.document["cartridges"]]

    # 2. Prepare the state directory.
    run_dir = Path(state_dir) / "run"
    run_dir.mkdir(parents=True, exist_ok=True)

    # 3. Derive state_path and lock_path exactly once.
    state_path = str(run_dir / "state.json")
    lock_path = str(run_dir / ".lock")

    # 4. Atomic first-run admission.  persist_admit_run acquires the run lock
    #    BEFORE checking the state file, creates the state file as a fresh
    #    READY only when it is missing (still under that lock), then branches
    #    on the freshly-loaded status: READY -> RUNNING, COMPLETED / HALTED
    #    returned unchanged, RUNNING -> RunAdmissionError.  The lock is released
    #    before the call returns; this caller acquires no lock of its own.
    admitted = run_action.persist_admit_run(
        state_path, lock_path, owner_id, cartridge_ids
    )

    # 6. Return the exact admitted state and the once-derived paths.  The
    #    state is never re-read from disk.
    return PreparedRun(
        state=admitted,
        state_path=state_path,
        lock_path=lock_path,
    )
