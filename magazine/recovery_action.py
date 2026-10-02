"""Guarded persistence of an uncertain-run halt.

Explicit recovery actions must safely persist:

    RUNNING -> HALTED
    HALTED  -> RUNNING

without racing another Magazine process.  Each recovery action acquires the
existing run lock **before** loading or changing persisted state, so the
state is re-read under the lock and never trusted from an earlier
startup-inspection result.

The exact high-level flow is:

    acquire_run_lock(lock_path, owner_id)
    try:
        state = load_run_state(state_path)
        halted = halt_uncertain_run(state)
        save_run_state(state_path, halted)
        return halted
    finally:
        release_run_lock(lock_path, owner_id)

Nothing here implements stale-lock deletion, stale-lock detection, PID or
process checks, lock stealing, retries, polling, resume behaviour, dispatch,
dispatch intent, queue advancement, automatic continuation, timestamps, or
backup rotation.
"""

from __future__ import annotations

from magazine.recovery_transition import halt_uncertain_run, recover_halted_run
from magazine.run_lock import acquire_run_lock, release_run_lock
from magazine.state_store import load_run_state, save_run_state

__all__ = [
    "persist_recover_halted",
    "persist_uncertain_halt",
]


def persist_uncertain_halt(state_path: str, lock_path: str, owner_id: str) -> dict:
    """Persist the ``RUNNING -> HALTED`` transition for an uncertain run.

    The run lock is acquired first and the persisted state is only loaded
    after the lock is held.  The loaded state is transitioned with
    :func:`magazine.recovery_transition.halt_uncertain_run` and the result is
    persisted atomically.  The same lock is released after a successful
    persistence, and is also released if loading, the transition, or the
    persistence raises.

    Args:
        state_path: Path to the persisted run state file.
        lock_path: Path to the run lock file.
        owner_id: Owner ID passed unchanged to lock acquisition and release.

    Returns:
        The exact ``HALTED`` state that was successfully persisted.

    Raises:
        :class:`magazine.run_lock.MagazineLockError`: if the lock cannot be
            acquired (e.g. it is already held) or cannot be released.
        :class:`magazine.state.MagazineStateError`: if the persisted state is
            malformed (propagated from :func:`load_run_state`).
        :class:`magazine.recovery_transition.RecoveryTransitionError`: if the
            loaded state is valid but not ``RUNNING`` (e.g. ``READY``,
            ``COMPLETED``, or ``HALTED``).

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
        # 3. Transform with the pure recovery transition.
        halted = halt_uncertain_run(state)
        # 4. Persist atomically.
        save_run_state(state_path, halted)
        # 5. Return the exact HALTED state that was successfully persisted.
        return halted
    finally:
        # 6. Release the same lock with the same owner_id, whether the
        #    persistence succeeded or any step raised.
        release_run_lock(lock_path, owner_id)


def persist_recover_halted(state_path: str, lock_path: str, owner_id: str) -> dict:
    """Persist the ``HALTED -> RUNNING`` recovery for an explicitly halted run.

    The run lock is acquired first and the persisted state is only loaded
    after the lock is held.  The loaded state is transitioned with
    :func:`magazine.recovery_transition.recover_halted_run` and the result is
    persisted atomically.  The same lock is released after a successful
    persistence, and is also released if loading, the transition, or the
    persistence raises.

    The recovered run is positioned so that the existing execution path
    (:func:`magazine.run_loop.run_all_cartridges`) retries the interrupted
    cartridge: the state's ``current`` is preserved and no ``begin_next_cartridge``
    is invoked.

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
        :class:`magazine.recovery_transition.RecoveryTransitionError`: if the
            loaded state is valid but not ``HALTED`` (e.g. ``READY``,
            ``COMPLETED`` or ``RUNNING``), or if the HALTED state has no
            ``current`` cartridge.

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
        # 3. Transform with the pure recovery transition.
        recovered = recover_halted_run(state)
        # 4. Persist atomically.
        save_run_state(state_path, recovered)
        # 5. Return the exact RUNNING state that was successfully persisted.
        return recovered
    finally:
        # 6. Release the same lock with the same owner_id, whether the
        #    persistence succeeded or any step raised.
        release_run_lock(lock_path, owner_id)
