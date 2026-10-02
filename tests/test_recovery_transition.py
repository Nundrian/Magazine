"""Tests for the pure HALTED recovery transitions.

Covers ``magazine.recovery_transition.halt_uncertain_run`` (RUNNING ->
HALTED) and ``magazine.recovery_transition.recover_halted_run`` (HALTED ->
READY) plus ``magazine.recovery_transition.RecoveryTransitionError``.

``recover_halted_run`` is the re-admissible inverse of ``halt_uncertain_run``:
it restores an explicitly HALTED run to a ``READY`` state so the normal public
runner (``magazine.async_runner.run_magazine``) can re-admit it via the existing
admission path.  The interrupted ``current`` cartridge is moved to the front of
the ``todo`` queue (so it is retried before the remaining work), ``current``
is cleared, the status becomes ``READY``, and ``revision`` is incremented by
exactly one.
"""

from __future__ import annotations

import copy

import pytest

from magazine.recovery_transition import (
    RecoveryTransitionError,
    halt_uncertain_run,
    recover_halted_run,
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


def test_halted_is_rejected_by_halt() -> None:
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


# ---------------------------------------------------------------------------
# TestRecoverHaltedRun (HALTED -> READY, re-admissible)
# ---------------------------------------------------------------------------

class TestRecoverHaltedRun:
    def _halted_state(self, revision: int = 2) -> dict:
        return {
            "done": ["cart-1"],
            "current": "cart-2",
            "todo": ["cart-3", "cart-4"],
            "status": STATUS_HALTED,
            "revision": revision,
        }

    def test_halted_state_becomes_ready(self):
        """A valid HALTED state with a current cartridge transitions to
        READY (re-admissible), with ``done`` preserved, ``current`` cleared
        to ``None``, the interrupted cartridge moved to the front of ``todo``
        (preserving its position before the remaining queue), and ``revision``
        incremented by exactly one."""
        result = recover_halted_run(self._halted_state())
        assert result == {
            "done": ["cart-1"],
            "current": None,
            "todo": ["cart-2", "cart-3", "cart-4"],
            "status": STATUS_READY,
            "revision": 3,
        }

    def test_done_ordering_and_values_preserved_exactly_by_recover(self):
        """Recovery preserves the ``done`` list exactly: order and values."""
        state = {
            "done": ["cart-a", "cart-b", "cart-c"],
            "current": "cart-d",
            "todo": ["cart-e"],
            "status": STATUS_HALTED,
            "revision": 5,
        }
        result = recover_halted_run(state)
        assert result["done"] == ["cart-a", "cart-b", "cart-c"]

    def test_interrupted_cartridge_moved_to_front_of_todo_by_recover(self):
        """The interrupted ``current`` cartridge becomes the head of the
        ``todo`` queue (so the public runner's admission begins it first);
        ``current`` itself is cleared (a READY state cannot have a current)."""
        result = recover_halted_run(self._halted_state())
        assert result["current"] is None
        assert result["todo"][0] == "cart-2"
        assert result["todo"] == ["cart-2", "cart-3", "cart-4"]

    def test_todo_ordering_and_values_preserved_exactly_by_recover(self):
        """Recovery preserves the remaining ``todo`` order exactly after the
        interrupted cartridge is prepended to the queue."""
        state = {
            "done": ["cart-1"],
            "current": "cart-2",
            "todo": ["cart-3", "cart-4", "cart-5"],
            "status": STATUS_HALTED,
            "revision": 2,
        }
        result = recover_halted_run(state)
        assert result["todo"] == ["cart-2", "cart-3", "cart-4", "cart-5"]

    def test_last_cartridge_halted_run_recovers_to_ready_with_last_in_todo(self):
        """A HALTED run interrupted on its last cartridge (empty ``todo``)
        is recoverable to READY with the interrupted cartridge as the single
        remaining ``todo`` item."""
        state = {
            "done": ["cart-1"],
            "current": "cart-2",
            "todo": [],
            "status": STATUS_HALTED,
            "revision": 1,
        }
        result = recover_halted_run(state)
        assert result == {
            "done": ["cart-1"],
            "current": None,
            "todo": ["cart-2"],
            "status": STATUS_READY,
            "revision": 2,
        }

    def test_revision_increments_by_exactly_one_on_recover(self):
        """Recovery increments revision by exactly one, so the recovered
        run's progression is visible in the persisted state."""
        result = recover_halted_run(self._halted_state(revision=9))
        assert result["revision"] == 10

    def test_original_state_unchanged_by_recover(self):
        """Recovery does not mutate the input state."""
        state = self._halted_state()
        original = copy.deepcopy(state)
        recover_halted_run(state)
        assert state == original
        assert state["status"] == STATUS_HALTED

    def test_returned_state_is_different_dict_object_by_recover(self):
        """The returned recovered state is a new dict object."""
        state = self._halted_state()
        result = recover_halted_run(state)
        assert result is not state
        assert isinstance(result, dict)

    def test_returned_done_is_different_list_object_by_recover(self):
        """The returned ``done`` list is a fresh object so callers cannot
        alias the recovered run's completed history."""
        state = self._halted_state()
        result = recover_halted_run(state)
        assert result["done"] is not state["done"]

    def test_returned_todo_is_different_list_object_by_recover(self):
        """The returned ``todo`` list is a fresh object so callers cannot
        alias the recovered run's remaining queue."""
        state = self._halted_state()
        result = recover_halted_run(state)
        assert result["todo"] is not state["todo"]

    def test_mutating_returned_done_does_not_affect_original_done_by_recover(self):
        """Mutation of the returned ``done`` list must not affect the input
        state's completed history."""
        state = self._halted_state()
        result = recover_halted_run(state)
        result["done"].append("cart-99")
        result["done"][0] = "cart-99"
        assert state["done"] == ["cart-1"]

    def test_mutating_returned_todo_does_not_affect_original_todo_by_recover(self):
        """Mutation of the returned ``todo`` list must not affect the input
        state's remaining queue."""
        state = self._halted_state()
        result = recover_halted_run(state)
        result["todo"].append("cart-99")
        result["todo"][0] = "cart-99"
        assert state["todo"] == ["cart-3", "cart-4"]

    def test_returned_state_passes_validate_run_state_by_recover(self):
        """A recovered READY state is a valid run state on its own terms (a
        READY state with a non-empty completed history and a non-empty queue
        is contractually legal)."""
        result = recover_halted_run(self._halted_state())
        assert validate_run_state(result) is result

    @pytest.mark.parametrize("status,match", [
        (STATUS_READY, "status 'READY'"),
        (STATUS_COMPLETED, "status 'COMPLETED'"),
        (STATUS_RUNNING, "status 'RUNNING'"),
    ])
    def test_non_halted_state_rejected_by_recover(self, status, match):
        """Recovery is only valid on HALTED states; every other valid
        status is rejected without transition."""
        states = {
            STATUS_READY: {
                "done": ["cart-1"],
                "current": None,
                "todo": ["cart-3"],
                "status": STATUS_READY,
                "revision": 1,
            },
            STATUS_COMPLETED: {
                "done": ["cart-1"],
                "current": None,
                "todo": [],
                "status": STATUS_COMPLETED,
                "revision": 2,
            },
            STATUS_RUNNING: {
                "done": ["cart-1"],
                "current": "cart-2",
                "todo": ["cart-3"],
                "status": STATUS_RUNNING,
                "revision": 1,
            },
        }
        with pytest.raises(RecoveryTransitionError, match=match):
            recover_halted_run(states[status])

    def test_halted_without_current_rejected_by_recover(self):
        """A HALTED state with no ``current`` cartridge has no interrupted
        work position to retry; recovering it would put nothing at the front
        of the queue for admission to begin, so it is rejected explicitly."""
        state = {
            "done": ["cart-1"],
            "current": None,
            "todo": ["cart-2"],
            "status": STATUS_HALTED,
            "revision": 2,
        }
        with pytest.raises(
            RecoveryTransitionError,
            match="without a current cartridge",
        ):
            recover_halted_run(state)

    def test_malformed_state_propagates_magazine_state_error_by_recover(self):
        """Malformed input propagates ``MagazineStateError`` unchanged."""
        bad_state = {
            "done": ["cart-1"],
            "current": None,
            "todo": ["cart-3"],
            "status": "BOGUS",
            "revision": -1,
        }
        with pytest.raises(MagazineStateError):
            recover_halted_run(bad_state)
