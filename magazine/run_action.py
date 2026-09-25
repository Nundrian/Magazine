"""Guarded persistence of the run-cartridge transitions.

Explicit run actions must safely persist:

    READY -> RUNNING        (begin_next_cartridge)
    RUNNING -> READY        (complete_current_cartridge, when work remains)
    RUNNING -> COMPLETED    (complete_current_cartridge, when no work remains)

and, separately, must admit a first run atomically: when no state file exists
yet, create one as a fresh ``READY`` and transition that same ``READY`` to
``RUNNING`` — without racing another Magazine process.  Each action acquires
the existing run lock **before** loading or changing persisted state, so the
state is re-read under the lock and never trusted from an earlier
startup-inspection result.

The exact high-level flow is the same for the first two actions:

    acquire_run_lock(lock_path, owner_id)
    try:
        state = load_run_state(state_path)
        next_state = <transition>(state)
        save_run_state(state_path, next_state)
        return next_state
    finally:
        release_run_lock(lock_path, owner_id)

Nothing here implements stale-lock deletion, stale-lock detection, PID or
process checks, lock stealing, retries, polling, resume behaviour, dispatch,
dispatch intent, queue advancement, automatic continuation, timestamps, or
backup rotation.
"""

from __future__ import annotations

import os

from magazine.run_lock import acquire_run_lock, release_run_lock
from magazine.run_transition import (
    RunTransitionError,
    begin_next_cartridge,
    complete_current_cartridge,
)
from magazine.state import (
    STATUS_READY,
    STATUS_RUNNING,
    STATUS_COMPLETED,
    STATUS_HALTED,
    new_ready_state,
)
from magazine.state_store import load_run_state, save_run_state

__all__ = [
    "RunAdmissionError",
    "RunIdentityError",
    "persist_admit_run",
    "persist_begin_next_cartridge",
    "persist_complete_current_cartridge",
]


class RunIdentityError(RunTransitionError):
    """Raised when the run's current cartridge, freshly loaded under the run
    lock, is not the cartridge the caller expected to complete (i.e. the ID
    whose dispatch just succeeded).

    The persisted state is left untouched and the lock is released, so the
    caller sees a visible failure instead of a silently-wrong completion.
    """


class RunAdmissionError(RunTransitionError):
    """Raised when admission finds an already-RUNNING persisted run."""


def persist_begin_next_cartridge(state_path: str, lock_path: str, owner_id: str) -> dict:
    """Persist the ``READY -> RUNNING`` transition for the next cartridge.

    The run lock is acquired first and the persisted state is only loaded
    after the lock is held.  The loaded state is transitioned with
    :func:`magazine.run_transition.begin_next_cartridge` and the result is
    persisted atomically.  The same lock is released after a successful
    persistence, and is also released if loading, the transition, or the
    persistence raises.

    Args:
        state_path: Path to the persisted run state file.
        lock_path: Path to the run lock file.
        owner_id: Owner ID passed unchanged to lock acquisition and release.

    Returns:
        The exact ``RUNNING`` state that was successfully persisted.

    Raises:
        :class:`magazine.run_lock.MagazineLockError`: if the lock cannot be
            acquired (e.g. it is already held) or cannot be released.
        :class:`magazine.state.MagazineStateError`: if the persisted state is
            malformed (propagated from :func:`load_run_state`).
        :class:`magazine.run_transition.RunTransitionError`: if the loaded
            state is valid but a cartridge cannot be begun (e.g. ``RUNNING``,
            ``COMPLETED``, ``HALTED``, or ``READY`` with an empty ``todo``).

    The lock is always released in a ``finally`` block, so the caller never
    holds the lock after a successful or failed call.  If the lock already
    exists, acquisition fails through the existing lock primitive and the
    persisted state is left untouched.
    """
    # 1. Acquire the existing run lock first, passing owner_id unchanged.
    acquire_run_lock(lock_path, owner_id)
    try:
        # 2. Only load persisted state after the lock is acquired.
        state = load_run_state(state_path)
        # 3. Transform with the pure run transition.
        running = begin_next_cartridge(state)
        # 4. Persist atomically.
        save_run_state(state_path, running)
        # 5. Return the exact RUNNING state that was successfully persisted.
        return running
    finally:
        # 6. Release the same lock with the same owner_id, whether the
        #    persistence succeeded or any step raised.
        release_run_lock(lock_path, owner_id)


def persist_complete_current_cartridge(
    state_path: str,
    lock_path: str,
    owner_id: str,
    expected_current_id: str | None = None,
) -> dict:
    """Persist the ``RUNNING -> READY`` (or ``COMPLETED``) transition for the
    current cartridge.

    The run lock is acquired first and the persisted state is only loaded
    after the lock is held.  The loaded state is transitioned with
    :func:`magazine.run_transition.complete_current_cartridge` and the result
    is persisted atomically.  The same lock is released after a successful
    persistence, and is also released if loading, the transition, or the
    persistence raises.

    Args:
        state_path: Path to the persisted run state file.
        lock_path: Path to the run lock file.
        owner_id: Owner ID passed unchanged to lock acquisition and release.
        expected_current_id: Optional ID of the cartridge that was actually
            dispatched before this completion is recorded.  When ``None``
            (the default) no identity check is performed and behaviour is
            identical to before.  When set, the freshly-loaded ``current``
            is compared against it under the lock before the transition; if
            they differ, :class:`RunIdentityError` is raised and the state is
            left untouched.

    Returns:
        The exact ``READY`` (or ``COMPLETED``) state that was successfully
        persisted.

    Raises:
        :class:`magazine.run_lock.MagazineLockError`: if the lock cannot be
            acquired (e.g. it is already held) or cannot be released.
        :class:`magazine.state.MagazineStateError`: if the persisted state is
            malformed (propagated from :func:`load_run_state`).
        :class:`magazine.run_action.RunIdentityError`: if a non-``None``
            ``expected_current_id`` differs from the freshly-loaded
            ``current``.  The persisted state is left untouched and the lock
            is released.
        :class:`magazine.run_transition.RunTransitionError`: if the loaded
            state is valid but a cartridge cannot be completed (e.g. any
            non-``RUNNING`` status such as ``READY``, ``COMPLETED``, or
            ``HALTED``).

    The lock is always released in a ``finally`` block, so the caller never
    holds the lock after a successful or failed call.  If the lock already
    exists, acquisition fails through the existing lock primitive and the
    persisted state is left untouched.
    """
    # 1. Acquire the existing run lock first, passing owner_id unchanged.
    acquire_run_lock(lock_path, owner_id)
    try:
        # 2. Only load persisted state after the lock is acquired.
        state = load_run_state(state_path)
        # 3. Identity guard: the freshly-loaded ``current`` must match the
        #    caller's expected cartridge ID before anything is completed.
        #    A mismatch raises before the transition and before any save, so
        #    the state is left untouched and no wrong cartridge is marked done.
        if expected_current_id is not None and state["current"] != expected_current_id:
            raise RunIdentityError(
                f"cannot complete: expected current "
                f"{expected_current_id!r} but the run's current is "
                f"{state['current']!r}"
            )
        # 4. Transform with the pure run transition.
        completed = complete_current_cartridge(state)
        # 5. Persist atomically.
        save_run_state(state_path, completed)
        # 6. Return the exact completed state that was successfully persisted.
        return completed
    finally:
        # 7. Release the same lock with the same owner_id, whether the
        #    persistence succeeded or any step raised.
        release_run_lock(lock_path, owner_id)


def persist_admit_run(
    state_path: str,
    lock_path: str,
    owner_id: str,
    cartridge_ids: list[str],
) -> dict:
    """Admit a first run atomically under one run-lock hold.

    The run lock is acquired **before** checking whether the state file
    exists.  While that single lock is held the function:

    * creates the state file as a fresh ``READY`` (via
      :func:`magazine.state.new_ready_state`) and persists it, only when the
      file is missing, then continues with the ``READY -> RUNNING`` admission
      below; or
    * transitions an already-existing ``READY`` file to ``RUNNING`` and
      persists it; or
    * returns an existing ``COMPLETED`` or ``HALTED`` file unchanged (no
      re-save, no transition); or
    * raises :class:`RunAdmissionError` when the freshly-loaded state is
      already ``RUNNING`` \u2014 the caller must then use the existing explicit
      recovery path rather than joining or resuming the run.

    The same lock spans the initial ``READY`` creation and the ``READY ->
    RUNNING`` admission, so a second normal runner arriving after the first
    admitted and released its short admission lock is refused instead of
    silently overwriting the running state.  This function performs no dispatch,
    no retry, no polling, and holds no lock across any external work; the lock
    is always released in a ``finally`` block on every path.

    Args:
        state_path: Path to the persisted run state file.
        lock_path: Path to the run lock file.
        owner_id: Owner ID passed unchanged to lock acquisition and release.
        cartridge_ids: Cartridge ID sequence used to build the fresh ``READY``
            state when the state file does not yet exist.

    Returns:
        The exact ``RUNNING`` state successfully persisted (first-run or
        existing ``READY`` admission), or the exact ``COMPLETED`` / ``HALTED``
        state returned unchanged.

    Raises:
        :class:`magazine.run_lock.MagazineLockError`: if the lock cannot be
            acquired (e.g. it is already held) or cannot be released.
        :class:`magazine.state.MagazineStateError`: if the persisted state is
            malformed (propagated from :func:`load_run_state`).
        :class:`magazine.run_transition.RunTransitionError`: if the loaded
            ``READY`` state cannot be begun (e.g. an empty ``todo``).
        :class:`RunAdmissionError`: if the freshly-loaded state is already
            ``RUNNING``; the state is left untouched and the lock is released.

    The lock is always released in a ``finally`` block, so the caller never
    holds the lock after a successful or failed call.  If the lock already
    exists, acquisition fails through the existing lock primitive and the
    persisted state is left untouched.
    """
    # 1. Acquire the existing run lock first, passing owner_id unchanged \u2014
    #    BEFORE checking whether the state file exists.
    acquire_run_lock(lock_path, owner_id)
    try:
        # 2. First-run admission: create the state file as a fresh ``READY``
        #    only when it does not yet exist.
        if not os.path.exists(state_path):
            ready = new_ready_state(cartridge_ids)
            save_run_state(state_path, ready)

        # 3. Load (and validate) the persisted state while still holding the
        #    SAME lock, then branch on its freshly-loaded status.
        persisted = load_run_state(state_path)
        status = persisted["status"]

        if status == STATUS_READY:
            # 4. Existing/fresh READY -> RUNNING under the same lock, then
            #    persist and return the exact RUNNING state.
            running = begin_next_cartridge(persisted)
            save_run_state(state_path, running)
            return running

        if status == STATUS_COMPLETED:
            # 5a. Terminal: return the loaded state unchanged; do not re-save
            #     and do not begin another cartridge.
            return persisted

        if status == STATUS_HALTED:
            # 5b. Terminal: return the loaded state unchanged; do not re-save
            #     and do not begin another cartridge.
            return persisted

        if status == STATUS_RUNNING:
            # 6. A second normal runner arriving after the first admitted and
            #    released its short admission lock: refuse to join or resume,
            #    and require the existing explicit recovery path.
            raise RunAdmissionError(
                "run is already RUNNING; use the existing explicit recovery "
                "path rather than joining or resuming it"
            )

    finally:
        # 7. Release the same lock with the same owner_id on every path.
        release_run_lock(lock_path, owner_id)
