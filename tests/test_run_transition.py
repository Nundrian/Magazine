"""Tests for the pure begin-next and complete-current cartridge transitions.

Covers ``magazine.run_transition.begin_next_cartridge`` and
``magazine.run_transition.complete_current_cartridge`` plus the
``RunTransitionError`` they raise.
"""

from __future__ import annotations

import pytest

from magazine.run_transition import (
    RunTransitionError,
    begin_next_cartridge,
    complete_current_cartridge,
)
from magazine.state import (
    STATUS_COMPLETED,
    STATUS_READY,
    STATUS_RUNNING,
    MagazineStateError,
    validate_run_state,
)


def _ready_state(todo=None, revision=0) -> dict:
    return {
        "done": [],
        "current": None,
        "todo": list(todo or []),
        "status": STATUS_READY,
        "revision": revision,
    }


def _running_state(done=None, current=None, todo=None, revision=1) -> dict:
    return {
        "done": list(done or []),
        "current": current,
        "todo": list(todo or []),
        "status": STATUS_RUNNING,
        "revision": revision,
    }


def _completed_state(done=None) -> dict:
    return {
        "done": list(done or []),
        "current": None,
        "todo": [],
        "status": STATUS_COMPLETED,
        "revision": 0,
    }


def _halted_state(done=None, current=None, todo=None) -> dict:
    return {
        "done": list(done or []),
        "current": current,
        "todo": list(todo or []),
        "status": "HALTED",
        "revision": 0,
    }


# ---------------------------------------------------------------------------
# begin_next_cartridge
# ---------------------------------------------------------------------------

def test_begin_next_cartridge_on_ready_returns_running():
    state = _ready_state(["cart-1", "cart-2"])
    result = begin_next_cartridge(state)
    assert result == {
        "done": [],
        "current": "cart-1",
        "todo": ["cart-2"],
        "status": STATUS_RUNNING,
        "revision": 1,
    }


def test_begin_next_cartridge_first_cartridge_becomes_current():
    state = _ready_state(["cart-a", "cart-b", "cart-c"])
    result = begin_next_cartridge(state)
    assert result["current"] == "cart-a"


def test_begin_next_cartridge_rest_of_todo_shifts():
    state = _ready_state(["cart-a", "cart-b", "cart-c"])
    result = begin_next_cartridge(state)
    assert result["todo"] == ["cart-b", "cart-c"]


def test_begin_next_cartridge_done_remains_empty_on_first_begin():
    state = _ready_state(["cart-1"])
    result = begin_next_cartridge(state)
    assert result["done"] == []


def test_begin_next_cartridge_with_nonzero_done():
    # A valid state must have done before begin only if status is RUNNING,
    # so this test is not meaningful. begin_next_cartridge requires READY.
    state = _ready_state(["cart-1"])
    result = begin_next_cartridge(state)
    assert result["done"] == []


def test_begin_next_cartridge_increments_revision_by_one():
    state = _ready_state(["cart-1"], revision=0)
    result = begin_next_cartridge(state)
    assert result["revision"] == state["revision"] + 1


def test_begin_next_cartridge_preserves_high_revision():
    state = _ready_state(["cart-1"], revision=999)
    result = begin_next_cartridge(state)
    assert result["revision"] == state["revision"] + 1


def test_begin_next_cartridge_on_running_raises():
    state = _running_state(done=["c1"], current="c2", todo=["c3"])
    with pytest.raises(RunTransitionError):
        begin_next_cartridge(state)


def test_begin_next_cartridge_on_completed_raises():
    state = _completed_state(done=["c1"])
    with pytest.raises(RunTransitionError):
        begin_next_cartridge(state)


def test_begin_next_cartridge_on_halted_raises():
    state = _halted_state(done=["c1"], current="c2", todo=["c3"])
    with pytest.raises(RunTransitionError):
        begin_next_cartridge(state)


def test_begin_next_cartridge_malformed_state_raises():
    state = {"done": [], "current": None, "todo": ["cart-1"], "status": "PAUSED", "revision": 0}
    with pytest.raises(MagazineStateError):
        begin_next_cartridge(state)


def test_begin_next_cartridge_empty_todo_raises():
    state = _ready_state([])
    with pytest.raises(RunTransitionError):
        begin_next_cartridge(state)


def test_begin_next_cartridge_non_empty_todo_ok():
    state = _ready_state(["cart-1", "cart-2"])
    begin_next_cartridge(state)
    begin_next_cartridge(state)


def test_begin_next_cartridge_input_state_unchanged():
    state = _ready_state(["cart-1", "cart-2"])
    done_before = state["done"]
    todo_before = state["todo"]
    current_before = state["current"]
    status_before = state["status"]
    revision_before = state["revision"]
    begin_next_cartridge(state)
    assert state["done"] is done_before
    assert state["todo"] is todo_before
    assert state["current"] is current_before
    assert state["status"] == status_before
    assert state["revision"] == revision_before
    assert state == {"done": [], "current": None, "todo": ["cart-1", "cart-2"],
                     "status": STATUS_READY, "revision": 0}


def test_begin_next_cartridge_returns_new_dict_object():
    state = _ready_state(["cart-1", "cart-2"])
    result = begin_next_cartridge(state)
    assert result is not state


# ---------------------------------------------------------------------------
# complete_current_cartridge
# ---------------------------------------------------------------------------

def test_complete_current_cartridge_with_todo_remains():
    state = _running_state(done=[], current="cart-1", todo=["cart-2"])
    result = complete_current_cartridge(state)
    assert result == {
        "done": ["cart-1"],
        "current": None,
        "todo": ["cart-2"],
        "status": STATUS_READY,
        "revision": 2,
    }


def test_complete_current_cartridge_last_cartridge_completes_run():
    state = _running_state(done=["cart-1"], current="cart-2", todo=[])
    result = complete_current_cartridge(state)
    assert result == {
        "done": ["cart-1", "cart-2"],
        "current": None,
        "todo": [],
        "status": STATUS_COMPLETED,
        "revision": 2,
    }


def test_complete_current_cartridge_current_moved_to_done():
    state = _running_state(done=[], current="cart-1", todo=["cart-2"])
    result = complete_current_cartridge(state)
    assert "cart-1" in result["done"]
    assert result["current"] is None


def test_complete_current_cartridge_increments_revision_by_one():
    state = _running_state(done=["c1"], current="c2", todo=["c3"], revision=1)
    result = complete_current_cartridge(state)
    assert result["revision"] == state["revision"] + 1


def test_complete_current_cartridge_preserves_high_revision():
    state = _running_state(done=["c1"], current="c2", todo=[], revision=42)
    result = complete_current_cartridge(state)
    assert result["revision"] == 43


def test_complete_current_cartridge_on_ready_raises():
    state = _ready_state(["cart-1"])
    with pytest.raises(RunTransitionError):
        complete_current_cartridge(state)


def test_complete_current_cartridge_on_completed_raises():
    state = _completed_state(done=["cart-1"])
    with pytest.raises(RunTransitionError):
        complete_current_cartridge(state)


def test_complete_current_cartridge_on_halted_raises():
    state = _halted_state(done=["c1"], current="c2", todo=["c3"])
    with pytest.raises(RunTransitionError):
        complete_current_cartridge(state)


def test_complete_current_cartridge_malformed_state_raises():
    state = {"done": ["c1"], "current": "c2", "todo": ["c3"], "status": "PAUSED", "revision": 1}
    with pytest.raises(MagazineStateError):
        complete_current_cartridge(state)


def test_complete_current_cartridge_current_none_raises():
    # A running state must always have a non-null current by contract.
    state = {"done": ["c1"], "current": None, "todo": [], "status": STATUS_RUNNING, "revision": 0}
    with pytest.raises(MagazineStateError):
        complete_current_cartridge(state)


def test_complete_current_cartridge_input_state_unchanged():
    state = _running_state(done=["c1"], current="c2", todo=["c3"])
    done_before = state["done"]
    todo_before = state["todo"]
    current_before = state["current"]
    status_before = state["status"]
    revision_before = state["revision"]
    complete_current_cartridge(state)
    assert state["done"] is done_before
    assert state["todo"] is todo_before
    assert state["current"] is current_before
    assert state["status"] == status_before
    assert state["revision"] == revision_before
    assert state == {"done": ["c1"], "current": "c2", "todo": ["c3"],
                     "status": STATUS_RUNNING, "revision": 1}


def test_complete_current_cartridge_returns_new_dict_object():
    state = _running_state(done=["c1"], current="c2", todo=["c3"])
    result = complete_current_cartridge(state)
    assert result is not state


def test_complete_current_cartridge_returns_state_with_no_current():
    state = _running_state(done=["c1"], current="c2", todo=["c3"])
    result = complete_current_cartridge(state)
    assert result["current"] is None


def test_complete_current_cartridge_with_empty_todo():
    state = _running_state(done=[], current="c1", todo=[])
    result = complete_current_cartridge(state)
    assert result["status"] == STATUS_COMPLETED
    assert result["todo"] == []
    assert result["current"] is None


def test_complete_current_cartridge_with_many_done():
    state = _running_state(done=["a", "b", "c"], current="d", todo=["e"], revision=2)
    result = complete_current_cartridge(state)
    assert result["done"] == ["a", "b", "c", "d"]
    assert result["current"] is None
    assert result["status"] == STATUS_READY
    assert result["todo"] == ["e"]
    assert result["revision"] == 3


def test_complete_current_cartridge_preserves_done_order():
    state = _running_state(done=["z1", "z2"], current="z3", todo=[], revision=1)
    result = complete_current_cartridge(state)
    assert result["done"] == ["z1", "z2", "z3"]


def test_complete_current_cartridge_full_running_with_empty_todo_output():
    state = _running_state(done=["c1", "c2"], current="c3", todo=[], revision=1)
    result = complete_current_cartridge(state)
    assert result["done"] == ["c1", "c2", "c3"]
    assert result["current"] is None
    assert result["todo"] == []
    assert result["status"] == STATUS_COMPLETED
    assert result["revision"] == 2


def test_input_state_remains_unchanged():
    state = _running_state(done=["c1"], current="c2", todo=["c3"])
    original = dict(state)
    complete_current_cartridge(state)
    assert state == original
    assert state["status"] == STATUS_RUNNING


def test_input_state_does_not_gain_current_from_completing():
    state = _running_state(done=["c1"], current="c2", todo=["c3"])
    original_current = state["current"]
    complete_current_cartridge(state)
    assert state["current"] == original_current


def test_input_state_does_not_gain_extra_done():
    state = _running_state(done=["c1"], current="c2", todo=["c3"])
    original_done = list(state["done"])
    complete_current_cartridge(state)
    assert state["done"] == original_done
