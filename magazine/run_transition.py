"""Pure run transitions for an ordered Magazine run.

This module defines the explicit transitions for a Magazine run state:

    READY -> RUNNING      (begin_next_cartridge)
    RUNNING -> READY      (complete_current_cartridge, when work remains)
    RUNNING -> COMPLETED  (complete_current_cartridge, when no work remains)

``begin_next_cartridge`` moves the first ``todo`` cartridge into ``current``
and preserves the exact work position (``done`` and the remaining ``todo``
order).  ``complete_current_cartridge`` appends the ``current`` cartridge to
the end of ``done``, clears ``current``, and advances the status to ``READY``
or ``COMPLETED`` depending on whether ``todo`` is non-empty.

Nothing is persisted, and no filesystem, locking, dispatch, or queue logic is
involved.  The module is pure and deterministic.
"""

from __future__ import annotations

from typing import Any

from magazine.state import (
    STATUS_READY,
    STATUS_RUNNING,
    STATUS_COMPLETED,
    validate_run_state,
)

__all__ = [
    "RunTransitionError",
    "begin_next_cartridge",
    "complete_current_cartridge",
]


class RunTransitionError(Exception):
    """Raised when a run transition is not applicable to the given state."""


def begin_next_cartridge(state: Any) -> dict[str, Any]:
    """Begin the next cartridge in a valid ``READY`` run state.

    *state* must be a valid run state whose ``status`` is ``READY``, whose
    ``current`` is ``None``, and whose ``todo`` holds at least one cartridge.
    The function first validates *state* via :func:`magazine.state.validate_run_state`
    (propagating any ``MagazineStateError``), then returns a **new** state dict
    with the first ``todo`` cartridge moved into ``current`` and the status
    changed to ``RUNNING``.  The ``done`` field and the remaining ``todo``
    order are preserved exactly, and the returned ``done`` and ``todo`` are
    independent list copies so later mutation of the returned state cannot
    mutate the original.

    Raises:
        ``MagazineStateError``: if *state* is malformed (propagated from
            :func:`validate_run_state`).
        ``RunTransitionError``: if *state* is valid but a cartridge cannot be
            begun (``READY`` with an empty ``todo``, or any non-``READY``
            status such as ``RUNNING``, ``COMPLETED``, or ``HALTED``).
    """
    # Rule 1: validate the supplied state first; propagate any validation error.
    validated = validate_run_state(state)

    # Rule 2: only a valid READY state may begin a cartridge.
    if validated["status"] != STATUS_READY:
        raise RunTransitionError(
            f"cannot begin a cartridge in status {validated['status']!r}; "
            f"only READY states may transition to RUNNING"
        )

    # Rule 2 (defensive): a valid READY state always has current is None;
    # this guard makes the acceptance contract explicit.
    if validated["current"] is not None:
        raise RunTransitionError(
            "cannot begin a cartridge in a READY state that already has a "
            f"current cartridge: {validated['current']!r}"
        )

    # Rule 2: at least one queued cartridge is required.
    if not validated["todo"]:
        raise RunTransitionError(
            "cannot begin a cartridge in a READY state with an empty todo queue"
        )

    # Rules 4-7: move the first todo cartridge into current, preserve the
    # remaining todo order and done exactly, and change only the status.
    # done/todo are copied so the returned state's lists are independent of
    # the original.
    # Rule 21: revision is incremented by exactly one on begin_next_cartridge.
    result: dict[str, Any] = {
        "done": list(validated["done"]),
        "current": validated["todo"][0],
        "todo": list(validated["todo"][1:]),
        "status": STATUS_RUNNING,
        "revision": validated["revision"] + 1,
    }

    # Rule 8: the supplied state is never mutated (we only read from it).
    # Rule 10: the returned state itself must pass validation.
    return validate_run_state(result)


def complete_current_cartridge(state: Any) -> dict[str, Any]:
    """Complete the current cartridge in a valid ``RUNNING`` run state.

    *state* must be a valid run state whose ``status`` is ``RUNNING``.  The
    function first validates *state* via :func:`magazine.state.validate_run_state`
    (propagating any ``MagazineStateError``), then returns a **new** state dict
    with the ``current`` cartridge appended to the end of ``done``, ``current``
    set to ``None``, and the status changed to ``READY`` (when ``todo`` is
    non-empty) or ``COMPLETED`` (when ``todo`` is empty).  The existing
    ``done`` order and the ``todo`` order are preserved exactly, and the
    returned ``done`` and ``todo`` are independent list copies so later
    mutation of the returned state cannot mutate the original.

    Raises:
        ``MagazineStateError``: if *state* is malformed (propagated from
            :func:`validate_run_state`).
        ``RunTransitionError``: if *state* is valid but a cartridge cannot be
            completed (any non-``RUNNING`` status such as ``READY``,
            ``COMPLETED``, or ``HALTED``).
    """
    # Rule 1: validate the supplied state first; propagate any validation error.
    validated = validate_run_state(state)

    # Rule 2: only a valid RUNNING state may complete a cartridge.
    if validated["status"] != STATUS_RUNNING:
        raise RunTransitionError(
            f"cannot complete a cartridge in status {validated['status']!r}; "
            f"only RUNNING states may transition to READY or COMPLETED"
        )

    # Rules 3-6: append current to the end of done, preserve existing done order,
    # set current to None, and preserve todo exactly.  done/todo are copied so
    # the returned state's lists are independent of the original.
    # Rule 22: revision is incremented by exactly one on
    # complete_current_cartridge.
    result: dict[str, Any] = {
        "done": list(validated["done"]) + [validated["current"]],
        "current": None,
        "todo": list(validated["todo"]),
        "status": STATUS_READY if validated["todo"] else STATUS_COMPLETED,
        "revision": validated["revision"] + 1,
    }

    # Rule 9: the supplied state is never mutated (we only read from it).
    # Rule 10: the returned state itself must pass validation.
    return validate_run_state(result)
