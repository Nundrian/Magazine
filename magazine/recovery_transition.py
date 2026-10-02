"""Pure recovery transitions for uncertain and halted Magazine runs.

When startup inspection classifies a persisted ``RUNNING`` state (with no
lock) as ``UNCERTAIN``, Magazine must not silently resume it.  This module
defines two explicit transitions, both preserving the exact work position
(``done``, ``current``, ``todo``):

    RUNNING -> HALTED     (operator-initiated uncertain halt)
    HALTED  -> RUNNING    (operator-approved recovery)

Nothing is persisted, and no filesystem, locking, dispatch, or queue logic
is involved.  The module is pure and deterministic.  The recovery transition
restores an explicitly HALTED run to RUNNING so the existing execution path
retries the interrupted cartridge (the state's ``current`` is preserved, so
``begin_next_cartridge`` is never called)."""

from __future__ import annotations

from typing import Any

from magazine.state import (
    STATUS_HALTED,
    STATUS_RUNNING,
    validate_run_state,
)

__all__ = [
    "RecoveryTransitionError",
    "halt_uncertain_run",
    "recover_halted_run",
]


class RecoveryTransitionError(Exception):
    """Raised when a recovery transition is not applicable to the given state."""


def halt_uncertain_run(state: Any) -> dict[str, Any]:
    """Transition a valid ``RUNNING`` state to ``HALTED``.

    *state* must be a valid run state whose ``status`` is ``RUNNING``.  The
    function first validates *state* via :func:`magazine.state.validate_run_state`
    (propagating any ``MagazineStateError``), then returns a **new** state
    dict with ``status`` changed to ``HALTED``.  The ``done``, ``current``
    and ``todo`` fields are preserved exactly, and the returned ``done`` and
    ``todo`` are independent list copies so later mutation of the returned
    state cannot mutate the original.

    Raises:
        ``MagazineStateError``: if *state* is malformed (propagated from
            :func:`validate_run_state`).
        ``RecoveryTransitionError``: if *state* is valid but its ``status``
            is not ``RUNNING`` (i.e. ``READY``, ``COMPLETED``, or ``HALTED``).
    """
    # Rule 1: validate the supplied state first; propagate any validation error.
    validated = validate_run_state(state)

    # Rule 2/3: only a valid RUNNING state may be halted.
    if validated["status"] != STATUS_RUNNING:
        raise RecoveryTransitionError(
            f"cannot halt a run in status {validated['status']!r}; "
            f"only RUNNING states may transition to HALTED"
        )

    # Rules 4-8: build a new state dict, preserving the exact work position
    # and changing only the status.  done/todo are copied so the returned
    # state's lists are independent of the original.
    # Rules 4-8: build a new state dict, preserving the exact work position
    # and changing only the status.  done/todo are copied so the returned
    # state's lists are independent of the original.  revision is preserved.
    halted: dict[str, Any] = {
        "done": list(validated["done"]),
        "current": validated["current"],
        "todo": list(validated["todo"]),
        "status": STATUS_HALTED,
        "revision": validated["revision"],
    }

    # Rule 9: the supplied state is never mutated (we only read from it).
    # Rule 10: the returned state itself must pass validation.
    return validate_run_state(halted)


def recover_halted_run(state: Any) -> dict[str, Any]:
    """Transition a valid ``HALTED`` state back to ``RUNNING``.

    *state* must be a valid run state whose ``status`` is ``HALTED``.  This
    is the inverse of :func:`halt_uncertain_run` and restores an explicitly
    ``HALTED`` run to ``RUNNING`` so the existing execution path
    (:func:`magazine.run_loop.run_all_cartridges`) can resume it.  It does
    *not* advance the position: ``current`` is preserved, so the interrupted
    cartridge is the one retried (``begin_next_cartridge`` is never called).

    The function first validates *state* via :func:`magazine.state.validate_run_state`
    (propagating any ``MagazineStateError``), then returns a **new** state
    dict with ``status`` changed to ``RUNNING`` and ``revision`` incremented
    by exactly one (recovery is a new execution event, consistent with the
    ``begin_next_cartridge`` / ``complete_current_cartridge`` convention).  The
    ``done``, ``current`` and ``todo`` fields are preserved exactly, and the
    returned ``done`` and ``todo`` are independent list copies so later
    mutation of the returned state cannot mutate the original.

    Raises:
        ``MagazineStateError``: if *state* is malformed (propagated from
            :func:`validate_run_state`).
        ``RecoveryTransitionError``: if *state* is valid but its ``status``
            is not ``HALTED`` (i.e. ``READY``, ``COMPLETED`` or ``RUNNING``),
            or if the HALTED state has no ``current`` cartridge (there is no
            interrupted work position to retry).
    """
    # Rule 1: validate the supplied state first; propagate any validation error.
    validated = validate_run_state(state)

    # Rule 2: only a valid HALTED state may be recovered.
    if validated["status"] != STATUS_HALTED:
        raise RecoveryTransitionError(
            f"cannot recover a run in status {validated['status']!r}; only "
            f"HALTED states may transition to RUNNING"
        )

    # Rule 3: a HALTED run with no current cartridge has no interrupted work
    # position to retry; recovering it to RUNNING would be invalid.
    if validated["current"] is None:
        raise RecoveryTransitionError(
            "cannot recover a HALTED run without a current cartridge; no "
            "interrupted work position to retry"
        )

    # Rules 4-8: build a new state dict, preserving the exact work position
    # and changing only the status (plus a revision increment).  done/todo are
    # copied so the returned state's lists are independent of the original.
    recovered: dict[str, Any] = {
        "done": list(validated["done"]),
        "current": validated["current"],
        "todo": list(validated["todo"]),
        "status": STATUS_RUNNING,
        "revision": validated["revision"] + 1,
    }

    # Rule 9: the supplied state is never mutated (we only read from it).
    # Rule 10: the returned state itself must pass validation.
    return validate_run_state(recovered)
