"""Tests for the magazine state contract (status, required fields, revision)."""

from __future__ import annotations

import pytest

from magazine.state import (
    STATUS_COMPLETED,
    STATUS_HALTED,
    STATUS_READY,
    STATUS_RUNNING,
    REQUIRED_STATE_FIELDS,
    MagazineStateError,
    new_ready_state,
    validate_run_state,
)


def _state(status: str, todo: list | None = None, done: list | None = None,
           current: str | None = None, revision: int = 0) -> dict:
    """Build a valid state dict for the given status and revision."""
    return {
        "done": list(done or []),
        "current": current,
        "todo": list(todo or []),
        "status": status,
        "revision": revision,
    }


# ---------------------------------------------------------------------------
# new_ready_state
# ---------------------------------------------------------------------------

def test_new_ready_state_has_status_ready():
    state = new_ready_state(["cart-1", "cart-2"])
    assert state["status"] == STATUS_READY


def test_new_ready_state_has_correct_todo_and_empty_done():
    state = new_ready_state(["cart-a", "cart-b", "cart-c"])
    assert state["todo"] == ["cart-a", "cart-b", "cart-c"]
    assert state["done"] == []


def test_new_ready_state_current_is_none():
    state = new_ready_state(["cart-1"])
    assert state["current"] is None


def test_new_ready_state_revision_is_zero():
    state = new_ready_state(["cart-1", "cart-2"])
    assert state["revision"] == 0


# ---------------------------------------------------------------------------
# validate_run_state: field presence
# ---------------------------------------------------------------------------

def test_validate_accepts_full_valid_state():
    state = _state(STATUS_RUNNING, done=["c1"], current="c2", todo=["c3"])
    assert validate_run_state(state) is state


def test_validate_accepts_ready_state():
    state = _state(STATUS_READY, todo=["c1"])
    assert validate_run_state(state) is state


def test_validate_accepts_completed_state():
    state = _state(STATUS_COMPLETED, done=["c1"])
    assert validate_run_state(state) is state


def test_validate_accepts_halted_state():
    state = _state(STATUS_HALTED, done=["c1"], current="c2", todo=["c3"])
    assert validate_run_state(state) is state


# ---------------------------------------------------------------------------
# validate_run_state: missing required fields
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "missing_field",
    [f for f in REQUIRED_STATE_FIELDS],
)
def test_validate_rejects_missing_required_fields(missing_field: str):
    state = _state(STATUS_READY, todo=["c1"])
    del state[missing_field]
    with pytest.raises(MagazineStateError):
        validate_run_state(state)


# ---------------------------------------------------------------------------
# validate_run_state: unknown fields
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "bad_state,expected_message",
    [
        ({
            "done": [],
            "current": None,
            "todo": [],
            "status": STATUS_READY,
            "revision": 0,
            "bogus": True,
        }, "bogus"),
        ({
            "done": [],
            "current": None,
            "todo": [],
            "status": STATUS_READY,
            "revision": 0,
            "revision": 0,  # duplicate key; JSON-like but dict collapses
            "extra_field": 42,
        }, "extra_field"),
    ],
)
def test_validate_rejects_unknown_fields(bad_state: dict, expected_message: str):
    with pytest.raises(MagazineStateError, match=expected_message):
        validate_run_state(bad_state)


def test_validate_rejects_non_dict_input():
    with pytest.raises(MagazineStateError):
        validate_run_state(["done", "current"])  # list, not dict


def test_validate_rejects_none_input():
    with pytest.raises(MagazineStateError):
        validate_run_state(None)


# ---------------------------------------------------------------------------
# validate_run_state: field type checks
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "field, bad_value",
    [
        ("done", "not-a-list"),
        ("done", 42),
        ("done", {"a": 1}),
        ("todo", "not-a-list"),
        ("todo", 42),
        ("todo", {"a": 1}),
        ("current", 12345),
        ("current", ["list"]),
        ("status", 12345),
        ("status", ["list"]),
        ("status", None),
        ("revision", "not-an-int"),
        ("revision", -1),
        ("revision", True),   # bool is a subclass of int in Python
    ],
)
def test_validate_rejects_bad_field_types(field: str, bad_value):
    state = _state(STATUS_READY)
    state[field] = bad_value
    with pytest.raises(MagazineStateError):
        validate_run_state(state)


# ---------------------------------------------------------------------------
# validate_run_state: status must be one of the four allowed
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_status", ["PAUSED", "UNKNOWN", "", "RUNNING ", "running"])
def test_validate_rejects_unknown_status(bad_status: str):
    state = _state(STATUS_READY)
    state["status"] = bad_status
    with pytest.raises(MagazineStateError):
        validate_run_state(state)


@pytest.mark.parametrize("status", [STATUS_READY, STATUS_RUNNING, STATUS_COMPLETED, STATUS_HALTED])
def test_validate_accepts_each_known_status(status: str):
    if status == STATUS_RUNNING:
        state = _state(status, done=["c1"], current="c2", todo=["c3"])
    elif status == STATUS_HALTED:
        state = _state(status, done=["c1"], current="c2", todo=["c3"])
    else:
        state = _state(status)
    assert validate_run_state(state) is state


# ---------------------------------------------------------------------------
# validate_run_state: idempotent
# ---------------------------------------------------------------------------

def test_validate_is_idempotent():
    state = _state(STATUS_RUNNING, done=["c1"], current="c2", todo=["c3"])
    first = validate_run_state(state)
    second = validate_run_state(first)
    assert first is second
    assert first is state


def test_validate_does_not_mutate_input():
    state = _state(STATUS_HALTED, done=["c1", "c2"], current="c3", todo=["c4"])
    snapshot_done = list(state["done"])
    snapshot_todo = list(state["todo"])
    snapshot_status = state["status"]
    snapshot_current = state["current"]
    snapshot_revision = state["revision"]
    validate_run_state(state)
    assert state["done"] == snapshot_done
    assert state["todo"] == snapshot_todo
    assert state["status"] == snapshot_status
    assert state["current"] == snapshot_current
    assert state["revision"] == snapshot_revision
    assert set(state.keys()) == {"done", "current", "todo", "status", "revision"}


# ---------------------------------------------------------------------------
# validate_run_state: revision type checks
# ---------------------------------------------------------------------------

def test_validate_rejects_negative_revision():
    state = _state(STATUS_READY, revision=-1)
    with pytest.raises(MagazineStateError):
        validate_run_state(state)


def test_validate_rejects_float_revision():
    state = _state(STATUS_READY, revision=1.5)
    with pytest.raises(MagazineStateError):
        validate_run_state(state)


def test_validate_rejects_bool_revision():
    state = _state(STATUS_READY, revision=True)
    with pytest.raises(MagazineStateError):
        validate_run_state(state)


def test_validate_accepts_zero_revision():
    state = _state(STATUS_READY, revision=0)
    assert validate_run_state(state) is state


def test_validate_accepts_positive_revision():
    state = _state(STATUS_RUNNING, revision=42, done=["c1"], current="c2", todo=["c3"])
    assert validate_run_state(state) is state


# ---------------------------------------------------------------------------
# Revision preservation
# ---------------------------------------------------------------------------

def test_validate_preserves_revision_value():
    state = _state(STATUS_HALTED, revision=7)
    result = validate_run_state(state)
    assert result["revision"] == 7


def test_new_ready_state_always_sets_revision_to_zero():
    state = new_ready_state(["a", "b", "c"])
    assert state["revision"] == 0


# ---------------------------------------------------------------------------
# Revision increment on begin_next_cartridge
# ---------------------------------------------------------------------------

from magazine.run_transition import begin_next_cartridge


def test_begin_next_cartridge_increments_revision():
    ready = _state(STATUS_READY, todo=["c1", "c2"], revision=0)
    result = begin_next_cartridge(ready)
    assert result["revision"] == 1
    assert result["status"] == STATUS_RUNNING
    assert result["current"] == "c1"
    assert result["todo"] == ["c2"]
    assert result["done"] == []


def test_begin_next_cartridge_increments_from_nonzero():
    ready = _state(STATUS_READY, todo=["c1", "c2"], revision=5)
    result = begin_next_cartridge(ready)
    assert result["revision"] == 6


# ---------------------------------------------------------------------------
# Revision increment on complete_current_cartridge
# ---------------------------------------------------------------------------

from magazine.run_transition import complete_current_cartridge


def test_complete_current_cartridge_increments_revision():
    running = _state(STATUS_RUNNING, done=[], current="c1", todo=["c2"], revision=1)
    result = complete_current_cartridge(running)
    assert result["revision"] == 2
    assert result["status"] == STATUS_READY
    assert result["done"] == ["c1"]
    assert result["current"] is None
    assert result["todo"] == ["c2"]


def test_complete_current_cartridge_increments_from_nonzero():
    running = _state(STATUS_RUNNING, done=["c1"], current="c2", todo=[], revision=3)
    result = complete_current_cartridge(running)
    assert result["revision"] == 4
    assert result["status"] == STATUS_COMPLETED
    assert result["done"] == ["c1", "c2"]


# ---------------------------------------------------------------------------
# Revision preserved by halt_uncertain_run
# ---------------------------------------------------------------------------

from magazine.recovery_transition import halt_uncertain_run


def test_halt_uncertain_run_preserves_revision():
    running = _state(STATUS_RUNNING, done=["c1"], current="c2", todo=["c3"], revision=3)
    result = halt_uncertain_run(running)
    assert result["revision"] == 3
    assert result["status"] == STATUS_HALTED
    assert result["current"] == "c2"
    assert result["todo"] == ["c3"]
    assert result["done"] == ["c1"]


def test_halt_uncertain_run_preserves_high_revision():
    running = _state(STATUS_RUNNING, done=["c1"], current="c2", todo=["c3"], revision=99)
    result = halt_uncertain_run(running)
    assert result["revision"] == 99