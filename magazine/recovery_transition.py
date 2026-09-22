"""Pure recovery transition for an uncertain Magazine run.

When startup inspection classifies a persisted ``RUNNING`` state (with no
lock) as ``UNCERTAIN``, Magazine must not silently resume it.  This module
defines the single explicit conservative transition:

    RUNNING -> HALTED

while preserving the exact work position (``done``, ``current``, ``todo``).
Nothing is persisted, and no filesystem, locking, dispatch, or queue logic
is involved.  The module is pure and deterministic.
"""

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
