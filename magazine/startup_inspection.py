"""Read-only startup inspection for a Magazine run.

Connects the persisted-state loader to the pure recovery classifier.  This
module performs no filesystem writes, no lock acquisition or release, no
state mutation, no recovery actions, no dispatch, and no queue movement.
It only observes whether a lock file exists and returns the classification
result unchanged.
"""

from __future__ import annotations

import os

from magazine.recovery import classify_recovery
from magazine.state_store import load_run_state

__all__ = ["inspect_startup"]


def inspect_startup(state_path: str, lock_path: str):
    """Inspect the startup posture of a Magazine run without changing anything.

    Loads the persisted run state via :func:`load_run_state`, observes only
    whether *lock_path* currently exists, and returns the result of
    :func:`classify_recovery` unchanged.

    Args:
        state_path: Path to the persisted run state file.
        lock_path: Path to the run lock file. Only its existence is observed.

    Returns:
        The recovery classification returned by :func:`classify_recovery`
        (one of ``STARTABLE``, ``UNCERTAIN``, ``COMPLETED``, ``HALTED``,
        ``LOCKED``).

    Raises:
        Exception: Any error raised by state loading, parsing, or validation
            propagates unchanged. No repair or normalisation is performed.
    """
    state = load_run_state(state_path)
    lock_present = os.path.exists(lock_path)
    return classify_recovery(state, lock_present)
