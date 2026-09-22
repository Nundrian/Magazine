"""Guarded persistence of the run-cartridge transitions.

Explicit run actions must safely persist:

    READY -> RUNNING        (begin_next_cartridge)
    RUNNING -> READY        (complete_current_cartridge, when work remains)
    RUNNING -> COMPLETED    (complete_current_cartridge, when no work remains)

without racing another Magazine process.  Each action acquires the existing
run lock **before** loading or changing persisted state, so the state is
re-read under the lock and never trusted from an earlier startup-inspection
result.

The exact high-level flow is the same for both actions:

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

from magazine.run_lock import acquire_run_lock, release_run_lock
from magazine.run_transition import begin_next_cartridge, complete_current_cartridge
from magazine.state_store import load_run_state, save_run_state

__all__ = [
    "persist_begin_next_cartridge",
    "persist_complete_current_cartridge",
]


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


def persist_complete_current_cartridge(state_path: str, lock_path: str, owner_id: str) -> dict:
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

    Returns:
        The exact ``READY`` (or ``COMPLETED``) state that was successfully
        persisted.

    Raises:
        :class:`magazine.run_lock.MagazineLockError`: if the lock cannot be
            acquired (e.g. it is already held) or cannot be released.
        :class:`magazine.state.MagazineStateError`: if the persisted state is
            malformed (propagated from :func:`load_run_state`).
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
        # 3. Transform with the pure run transition.
        completed = complete_current_cartridge(state)
        # 4. Persist atomically.
        save_run_state(state_path, completed)
        # 5. Return the exact completed state that was successfully persisted.
        return completed
    finally:
        # 6. Release the same lock with the same owner_id, whether the
        #    persistence succeeded or any step raised.
        release_run_lock(lock_path, owner_id)
