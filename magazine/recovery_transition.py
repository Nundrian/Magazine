"""Pure recovery transitions for uncertain and halted Magazine runs.

When startup inspection classifies a persisted ``RUNNING`` state (with no
lock) as ``UNCERTAIN``, Magazine must not silently resume it.  This module
defines two explicit transitions, both preserving the exact work history
(``done``) and remaining queue (``todo``):

    RUNNING -> HALTED     (operator-initiated uncertain halt)
    HALTED  -> READY      (operator-approved recovery; re-admissible)

Nothing is persisted, and no filesystem, locking, dispatch, or queue logic
is involved.  The module is pure and deterministic.  The recovery transition
restores an explicitly HALTED run to ``READY`` so the normal public runner
(:func:`magazine.async_runner.run_magazine`) can re-admit it via the existing
admission path; the interrupted cartridge is moved to the front of the ``todo``
queue, ``current`` is cleared and ``revision`` is incremented by exactly one,
so the public runner retries the interrupted cartridge before advancing to
the rest."""

from __future__ import annotations

from typing import Any

from magazine.state import (
    STATUS_HALTED,
    STATUS_READY,
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
    """Transition a valid ``HALTED`` state to ``READY`` so the normal public
    runner can re-admit it.

    *state* must be a valid run state whose ``status`` is ``HALTED``.  This
    is the re-admissible inverse of :func:`halt_uncertain_run`: it restores an
    explicitly ``HALTED`` run to ``READY`` so the normal public runner
    (:func:`magazine.async_runner.run_magazine`) can re-admit it via the
    existing admission path (:func:`magazine.run_action.persist_admit_run`),
    which performs the ``READY -> RUNNING`` transition and begins the first
    queued cartridge.

    The function first validates *state* via :func:`magazine.state.validate_run_state`
    (propagating any ``MagazineStateError``), then returns a **new** state
    dict with ``status`` changed to ``READY``, ``current`` cleared to ``None``,
    and ``revision`` incremented by exactly one (recovery is a new execution
    event, consistent with the ``begin_next_cartridge`` /
    ``complete_current_cartridge`` convention).  The ``done`` history is
    preserved exactly and the interrupted ``current`` cartridge is moved to the
    front of the ``todo`` queue (so it is retried before the remaining work);
    the returned ``done`` and ``todo`` are independent list copies so later
    mutation of the returned state cannot mutate the original.

    This is precisely the state shape ``persist_admit_run`` accepts and
    re-admits: ``done`` (possibly non-empty), ``current == None`` and a
    non-empty ``todo``.

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
            f"HALTED states may transition to READY"
        )

    # Rule 3: a HALTED run with no current cartridge has no interrupted work
    # position to retry; there is nothing to put at the front of the queue
    # for admission to begin.
    if validated["current"] is None:
        raise RecoveryTransitionError(
            "cannot recover a HALTED run without a current cartridge; no "
            "interrupted work position to retry"
        )

    # Rules 4-8: build a new state dict, preserving the exact work history
    # and moving the interrupted cartridge to the front of the queue (plus a
    # revision increment).  done/todo are copied so the returned state's
    # lists are independent of the original.
    recovered: dict[str, Any] = {
        "done": list(validated["done"]),
        "current": None,
        "todo": [validated["current"]] + list(validated["todo"]),
        "status": STATUS_READY,
        "revision": validated["revision"] + 1,
    }

    # Rule 9: the supplied state is never mutated (we only read from it).
    # Rule 10: the returned state itself must pass validation.
    return validate_run_state(recovered)
