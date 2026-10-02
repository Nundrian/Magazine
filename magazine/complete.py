"""Guarded post-dispatch completion composition for Magazine.

Thin composition layer that records the *successful* completion of the
current cartridge after :mod:`magazine.execute.dispatch_current_cartridge`
has returned without raising.

This is the Stage 3 counterpart to the Stage 2 dispatch layer
(:mod:`magazine.execute`): each stage is its own composition module
(:mod:`magazine.entrypoint` = Stage 1, :mod:`magazine.execute` = Stage 2,
:mod:`magazine.complete` = Stage 3).

Everything that touches the lock or the persisted run state is delegated to
the existing, already-tested guarded persisted action
:func:`magazine.run_action.persist_complete_current_cartridge`.  This module
re-implements none of that logic.

* lock acquisition / release
* re-loading the current persisted state
* the ``RUNNING -> READY`` / ``RUNNING -> COMPLETED`` transition
* the ``revision`` increment
* the atomic save

It only adds a single well-named boundary that a execution loop
can call after a successful dispatch.

Nothing here dispatches a cartridge, begins the next cartridge, adds a loop,
or changes any transition or locking API.
"""

from __future__ import annotations

from magazine import run_action

__all__ = ["complete_current_cartridge"]


def complete_current_cartridge(
    state_path: str,
    lock_path: str,
    owner_id: str,
    expected_current_id: str | None = None,
) -> dict:
    """Record that the run's current cartridge completed successfully.

    Delegates the full guarded persistence to the existing, already-tested
    persisted action :func:`magazine.run_action.persist_complete_current_cartridge`:
    lock acquisition, state re-read under the lock, the ``RUNNING -> READY``
    or ``RUNNING -> COMPLETED`` transition, the ``revision`` increment, and the
    atomic save, with the lock released on every path.  The current cartridge
    is appended to ``done`` and cleared from ``current``; the run advances to
    ``READY`` when cartridges remain, or ``COMPLETED`` when this was the last
    one.

    Args:
        state_path: Path to the persisted run state file (as accepted by the
            guarded persisted action).
        lock_path: Path to the run lock file.
        owner_id: Owner ID written into the run lock and passed through to the
            guarded persisted action unchanged.
        expected_current_id: Optional ID of the cartridge that was actually
            dispatched before this completion is recorded.  Passed unchanged
            to :func:`magazine.run_action.persist_complete_current_cartridge`,
            which compares it against the freshly-loaded ``current`` under the
            lock.  When ``None`` (the default) no identity check is performed
            and behaviour is identical to before.

    Returns:
        The exact ``READY`` or ``COMPLETED`` state that was successfully
        persisted, unchanged.

    Raises:
        :class:`magazine.run_lock.MagazineLockError`: if the lock cannot be
            acquired or released (propagated from the guarded action).
        :class:`magazine.state.MagazineStateError`: if the persisted state is
            malformed (propagated from the guarded action).
        :class:`magazine.run_transition.RunTransitionError`: if the loaded
            state is valid but is not ``RUNNING`, so no current cartridge can
            be completed (e.g. ``READY``, ``COMPLETED``, or ``HALTED``)
            (propagated from the guarded action).

    Note:
        This is a composition boundary only.  It trusts the caller to have
        already dispatched the current cartridge successfully and to supply the
        dispatched cartridge ID as ``expected_current_id``.  It does not
        dispatch, begin the next cartridge, or loop; it forwards
        ``expected_current_id`` unchanged to
        :func:`magazine.run_action.persist_complete_current_cartridge`, which
        performs the identity check against the freshly-loaded ``current``
        under the run lock.
    """
    return run_action.persist_complete_current_cartridge(
        state_path,
        lock_path,
        owner_id,
        expected_current_id=expected_current_id,
    )
