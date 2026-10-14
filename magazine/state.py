"""Pure state contract for an ordered Magazine run.

A Magazine run is represented as a plain dict with exactly four fields:

    {
        "done":    [cartridge_id, ...],   # ordered list of completed IDs
        "current": cartridge_id | None,    # at most one in-flight ID
        "todo":    [cartridge_id, ...],   # ordered list of remaining IDs
        "status":  "READY" | "RUNNING" | "COMPLETED" | "HALTED",
    }

This module is pure and deterministic.  It performs no filesystem access,
timestamps, locking, atomic writes, dispatch, recovery, queue advancement,
or automatic state mutation.  :func:`validate_run_state` returns the accepted
state dict unchanged (same object, same ID values, same list order).
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "STATUS_READY",
    "STATUS_RUNNING",
    "STATUS_COMPLETED",
    "STATUS_HALTED",
    "VALID_STATUSES",
    "REQUIRED_STATE_FIELDS",
    "MagazineStateError",
    "new_ready_state",
    "validate_run_state",
]

# ---------------------------------------------------------------------------
# Explicit run statuses
# ---------------------------------------------------------------------------

STATUS_READY = "READY"
STATUS_RUNNING = "RUNNING"
STATUS_COMPLETED = "COMPLETED"
STATUS_HALTED = "HALTED"

VALID_STATUSES: frozenset[str] = frozenset(
    (STATUS_READY, STATUS_RUNNING, STATUS_COMPLETED, STATUS_HALTED)
)

REQUIRED_STATE_FIELDS: frozenset[str] = frozenset(
    ("done", "current", "todo", "status", "revision")
)


class MagazineStateError(Exception):
    """Raised when a Magazine run state violates the state contract."""


def _validate_cartridge_id(value: Any, field: str, position: str) -> None:
    """Reject non-string or empty/whitespace-only cartridge IDs."""
    if not isinstance(value, str):
        raise MagazineStateError(
            f"{field} must contain only cartridge ID strings; "
            f"{position} is {type(value).__name__}"
        )
    if not value.strip():
        raise MagazineStateError(
            f"{field} must not contain empty or whitespace-only cartridge IDs"
        )


def _validate_id_list(field: str, values: list[Any]) -> None:
    """Validate that *values* is a list of valid, unique cartridge IDs.

    Enforces: every element is a valid cartridge ID string (rules 1 and 3),
    and no ID appears twice inside the same list (rules 6 and 7).
    """
    for index, value in enumerate(values):
        _validate_cartridge_id(value, field, f"index {index}")
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise MagazineStateError(
                f"Duplicate cartridge ID in {field}: {value!r}"
            )
        seen.add(value)


def new_ready_state(todo: list[str]) -> dict[str, Any]:
    """Create a fresh READY run state with no current cartridge.

    *todo* is copied so the caller cannot mutate the state through the
    original list.  The returned state is always valid; malformed *todo*
    entries raise ``MagazineStateError``.  ``revision`` is initialized to
    ``0``.
    """
    state: dict[str, Any] = {
        "done": [],
        "current": None,
        "todo": list(todo),
        "status": STATUS_READY,
        "revision": 0,
    }
    return validate_run_state(state)


def validate_run_state(state: Any) -> dict[str, Any]:
    """Validate *state* against the Magazine run state contract.

    Returns the accepted state dict unchanged (same object, same ID values,
    same list order) on success.

    Raises ``MagazineStateError`` when:

    * *state* is not a dict.
    * Unknown fields are present (rule 12).
    * Required fields are missing (rule 13).
    * Field types are invalid (rule 14): ``done``/``todo`` must be lists,
      ``current`` must be a cartridge ID string or ``None``, ``status`` must
      be a string.
    * Cartridge IDs are malformed (non-string, empty, or whitespace-only).
    * Cartridge IDs overlap between ``done``, ``current`` and ``todo``
      (rule 5).
    * Cartridge IDs are duplicated inside ``done`` (rule 6) or inside
      ``todo`` (rule 7).
    * ``status`` is not one of the explicit statuses (rule 4).
    * ``RUNNING`` has no current cartridge (rule 8).
    * ``READY`` has a current cartridge (rule 9).
    * ``COMPLETED`` has a current cartridge or remaining todo cartridges
      (rule 10).
    * ``revision`` is not a non-negative integer (rule 14b).

    ``HALTED`` may preserve a current cartridge so uncertain or interrupted
    work is not silently discarded (rule 11); both ``current=None`` and
    ``current=<id>`` are valid.

    ``revision`` is a non-negative integer: it starts at 0 in a fresh run,
    increments by exactly one on each ``begin_next_cartridge`` /
    ``complete_current_cartridge`` transition, and is otherwise preserved
    unchanged.
    """
    if not isinstance(state, dict):
        raise MagazineStateError(
            f"run state must be a dict, got {type(state).__name__}"
        )

    # Rule 12: unknown fields are rejected.
    unknown = set(state.keys()) - REQUIRED_STATE_FIELDS
    if unknown:
        raise MagazineStateError(f"Unknown state fields: {sorted(unknown)}")

    # Rule 13: missing required fields are rejected.
    missing = REQUIRED_STATE_FIELDS - set(state.keys())
    if missing:
        raise MagazineStateError(f"Missing required state fields: {sorted(missing)}")

    done = state["done"]
    current = state["current"]
    todo = state["todo"]
    status = state["status"]

    # Rule 14: field types.
    if not isinstance(done, list):
        raise MagazineStateError(
            f"'done' must be a list, got {type(done).__name__}"
        )
    if not isinstance(todo, list):
        raise MagazineStateError(
            f"'todo' must be a list, got {type(todo).__name__}"
        )
    if current is not None and not isinstance(current, str):
        raise MagazineStateError(
            f"'current' must be a cartridge ID string or None, "
            f"got {type(current).__name__}"
        )
    if not isinstance(status, str):
        raise MagazineStateError(
            f"'status' must be a string, got {type(status).__name__}"
        )

    # Rule 14b: revision type.
    revision = state["revision"]
    if isinstance(revision, bool) or not isinstance(revision, int):
        raise MagazineStateError(
            f"'revision' must be an int, got {type(revision).__name__}"
        )
    if revision < 0:
        raise MagazineStateError(f"'revision' must be non-negative, got {revision}")

    # Rule 4: status must be one of the explicit statuses.
    if status not in VALID_STATUSES:
        raise MagazineStateError(
            f"'status' must be one of {sorted(VALID_STATUSES)}, got {status!r}"
        )

    # Rules 1, 3, 6, 7: lists of valid, unique cartridge IDs.
    _validate_id_list("done", done)
    _validate_id_list("todo", todo)

    # Rule 2: current is a valid cartridge ID or None.
    if current is not None:
        _validate_cartridge_id(current, "current", "value")

    # Rule 8: RUNNING requires exactly one non-null current cartridge.
    if status == STATUS_RUNNING and current is None:
        raise MagazineStateError(
            "RUNNING state requires exactly one non-null 'current' cartridge"
        )

    # Rule 9: READY must have no current cartridge.
    if status == STATUS_READY and current is not None:
        raise MagazineStateError(
            "READY state must have no current cartridge"
        )

    # Rule 10: COMPLETED must have no current cartridge and no remaining
    # todo cartridges.
    if status == STATUS_COMPLETED:
        if current is not None:
            raise MagazineStateError(
                "COMPLETED state must have no current cartridge"
            )
        if todo:
            raise MagazineStateError(
                "COMPLETED state must have no remaining todo cartridges"
            )

    # Rule 11: HALTED may preserve a current cartridge.  Both
    # current=None and current=<id> are valid; nothing is enforced or
    # discarded.

    # Rule 5: no overlap between done, current and todo.
    seen: set[str] = set(done)
    if current is not None:
        if current in seen:
            raise MagazineStateError(
                f"Cartridge ID overlaps between 'current' and 'done': {current!r}"
            )
        seen.add(current)
    for value in todo:
        if value in seen:
            raise MagazineStateError(
                f"Cartridge ID overlaps between 'todo' and "
                f"completed/current state: {value!r}"
            )

    # Rule 15: return the accepted state unchanged.
    return state
