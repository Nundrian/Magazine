"""Pure startup/recovery classification for a Magazine run.

Given a validated run state and a strict boolean lock flag, classify the
recovery posture of the run.  This module is pure and deterministic: it
performs no filesystem access, no state mutation, no recovery actions, no
dispatch, no queue movement, no PID checks, no timestamps, and no automatic
continuation.
"""

from __future__ import annotations

from magazine.state import (
    STATUS_COMPLETED,
    STATUS_HALTED,
    STATUS_READY,
    STATUS_RUNNING,
    validate_run_state,
)

__all__ = [
    "RECOVERY_STARTABLE",
    "RECOVERY_LOCKED",
    "RECOVERY_COMPLETED",
    "RECOVERY_HALTED",
    "RECOVERY_UNCERTAIN",
    "classify_recovery",
]

# ---------------------------------------------------------------------------
# Explicit recovery classifications
# ---------------------------------------------------------------------------

RECOVERY_STARTABLE = "STARTABLE"
RECOVERY_LOCKED = "LOCKED"
RECOVERY_COMPLETED = "COMPLETED"
RECOVERY_HALTED = "HALTED"
RECOVERY_UNCERTAIN = "UNCERTAIN"

# Mapping used only when no lock is present.
_NO_LOCK_BY_STATUS = {
    STATUS_READY: RECOVERY_STARTABLE,
    STATUS_RUNNING: RECOVERY_UNCERTAIN,
    STATUS_COMPLETED: RECOVERY_COMPLETED,
    STATUS_HALTED: RECOVERY_HALTED,
}


def classify_recovery(state, lock_present):
    """Classify the recovery posture of a Magazine run.

    Args:
        state: A Magazine run state dict, validated via
            ``magazine.state.validate_run_state``.  The state is never
            mutated.
        lock_present: Whether a run lock is present.  Must be exactly a
            ``bool``; truthy/falsy values of other types are rejected.

    Returns:
        One of the ``RECOVERY_*`` constants:

        * ``RECOVERY_LOCKED`` when ``lock_present is True``, for every
          valid state.
        * Otherwise, by status: ``READY`` -> ``RECOVERY_STARTABLE``,
          ``RUNNING`` -> ``RECOVERY_UNCERTAIN``, ``COMPLETED`` ->
          ``RECOVERY_COMPLETED``, ``HALTED`` -> ``RECOVERY_HALTED``.

    Raises:
        ``magazine.state.MagazineStateError``: when *state* violates the
        state contract.
        ``TypeError``: when *lock_present* is not exactly a ``bool``.
    """
    validate_run_state(state)

    if type(lock_present) is not bool:
        raise TypeError("lock_present must be a bool")

    if lock_present is True:
        return RECOVERY_LOCKED

    return _NO_LOCK_BY_STATUS[state["status"]]
