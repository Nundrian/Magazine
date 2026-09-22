"""Tests for the pure uncertain-run halt transition.

Covers ``magazine.recovery_transition.halt_uncertain_run`` and
``magazine.recovery_transition.RecoveryTransitionError``.
"""

from __future__ import annotations

import pytest

from magazine.recovery_transition import (
    RecoveryTransitionError,
    halt_uncertain_run,
)
from magazine.state import (
    STATUS_COMPLETED,
    STATUS_HALTED,
    STATUS_READY,
    STATUS_RUNNING,
    MagazineStateError,
    validate_run_state,
)


def _running_state() -> dict:
    return {
        "done": ["cart-1"],
        "current": "cart-2",
        "todo": ["cart-3", "cart-4"],
        "status": STATUS_RUNNING,
        "revision": 1,
    }


def test_valid_running_state_becomes_halted() -> None:
    result = halt_uncertain_run(_running_state())
    assert result == {
        "done": ["cart-1"],
        "current": "cart-2",
        "todo": ["cart-3", "cart-4"],
        "status": STATUS_HALTED,
        "revision": 1,
    }


def test_done_ordering_and_values_preserved_exactly() -> None:
    state = {
        "done": ["cart-a", "cart-b", "cart-c"],
        "current": "cart-d",
        "todo": ["cart-e"],
        "status": STATUS_RUNNING,
        "revision": 2,
    }
    result = halt_uncertain_run(state)
    assert result["done"] == ["cart-a", "cart-b", "cart-c"]


def test_current_preserved_exactly() -> None:
    state = {
        "done": ["cart-1"],
        "current": "cart-2",
        "todo": ["cart-3"],
        "status": STATUS_RUNNING,
        "revision": 2,
    }
    result = halt_uncertain_run(state)
    assert result["current"] == "cart-2"


def test_todo_ordering_and_values_preserved_exactly() -> None:
    state = {
        "done": ["cart-1"],
        "current": "cart-2",
        "todo": ["cart-3", "cart-4", "cart-5"],
        "status": STATUS_RUNNING,
        "revision": 2,
    }
    result = halt_uncertain_run(state)
    assert result["todo"] == ["cart-3", "cart-4", "cart-5"]


def test_revision_preserved_by_halt() -> None:
    result = halt_uncertain_run(_running_state())
    assert result["revision"] == _running_state()["revision"]


def test_original_state_unchanged() -> None:
    state = _running_state()
    original = {
        "done": list(state["done"]),
        "current": state["current"],
        "todo": list(state["todo"]),
        "status": state["status"],
        "revision": state["revision"],
    }
    halt_uncertain_run(state)
    assert state == original
    assert state["status"] == STATUS_RUNNING


def test_returned_state_is_different_dict_object() -> None:
    state = _running_state()
    result = halt_uncertain_run(state)
    assert result is not state


def test_returned_done_is_different_list_object() -> None:
    state = _running_state()
    result = halt_uncertain_run(state)
    assert result["done"] is not state["done"]


def test_returned_todo_is_different_list_object() -> None:
    state = _running_state()
    result = halt_uncertain_run(state)
    assert result["todo"] is not state["todo"]


def test_mutating_returned_done_does_not_mutate_original_done() -> None:
    state = _running_state()
    result = halt_uncertain_run(state)
    result["done"].append("cart-99")
    result["done"][0] = "cart-0"
    assert state["done"] == ["cart-1"]


def test_mutating_returned_todo_does_not_mutate_original_todo() -> None:
    state = _running_state()
    result = halt_uncertain_run(state)
    result["todo"].append("cart-99")
    result["todo"][0] = "cart-0"
    assert state["todo"] == ["cart-3", "cart-4"]


def test_returned_state_passes_validate_run_state() -> None:
    state = _running_state()
    result = halt_uncertain_run(state)
    assert validate_run_state(result) is result


def test_ready_is_rejected() -> None:
    state = {
        "done": [],
        "current": None,
        "todo": ["cart-1"],
        "status": STATUS_READY,
        "revision": 0,
    }
    with pytest.raises(RecoveryTransitionError):
        halt_uncertain_run(state)


def test_completed_is_rejected() -> None:
    state = {
        "done": ["cart-1"],
        "current": None,
        "todo": [],
        "status": STATUS_COMPLETED,
        "revision": 1,
    }
    with pytest.raises(RecoveryTransitionError):
        halt_uncertain_run(state)


def test_halted_is_rejected() -> None:
    state = {
        "done": ["cart-1"],
        "current": "cart-2",
        "todo": ["cart-3"],
        "status": STATUS_HALTED,
        "revision": 2,
    }
    with pytest.raises(RecoveryTransitionError):
        halt_uncertain_run(state)


def test_malformed_state_propagates_validation_error() -> None:
    state = {
        "done": ["cart-1"],
        "current": "cart-2",
        "todo": ["cart-3"],
        "status": "BOGUS",
        "revision": 0,
    }
    # The malformed state must propagate the existing state-validation error
    # (MagazineStateError), not RecoveryTransitionError.
    with pytest.raises(MagazineStateError):
        halt_uncertain_run(state)
