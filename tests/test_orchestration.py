"""Focused test for magazine.orchestration (stage-4 single-cartridge boundary).

Exactly one test — the normal, non-final cartridge path.

A persisted ``RUNNING`` run with ``current == "cart-b"``, a non-empty
``todo`` (so "cart-b" is *not* the last cartridge), at least one
already-completed cartridge in ``done``, and a valid revision is advanced by
exactly one cartridge through the real public boundary
``orchestration.execute_one_cartridge``.

What runs for real (no mocking of these):
  * ``magazine.execute.dispatch_current_cartridge`` (Stage-2 dispatch)
  * the runtime routing + preflight composition
    (``magazine.runtime_dispatch``)
  * ``magazine.complete.complete_current_cartridge`` (Stage-3 composition)
  * the guarded persisted completion
    (``magazine.run_action.persist_complete_current_cartridge``)
    with lock acquire/release, under-lock state re-read, the
    expected-current identity guard, the ``RUNNING -> READY`` transition,
    the revision increment and the atomic save.

Only the live runtime preflight (:func:`validate_live_runtime_pair`) and the
final external dispatch callback are faked, so no real Open WebUI / HTTP
calls are made.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from unittest.mock import AsyncMock, call, patch

import pytest

from magazine import orchestration
from magazine import state_store
from magazine.state import STATUS_READY, STATUS_RUNNING
from magazine.run_action import RunIdentityError


BASE_URL = "http://localhost:8080"
API_KEY = "test-key"
CARTRIDGE_ID = "cart-b"
CARTRIDGE_PROMPT = "prompt for cart-b"
MODEL_ID = "m1"
TOOL_ID = "bash"
MODEL_MAP = {"research": MODEL_ID}
TOOL_MAP = {"terminal": TOOL_ID}
TOOL_ROLE = "terminal"


class DispatchFailure(RuntimeError):
    """Unique test exception for the external dispatch failure path.

    Raised by the faked external dispatch callback to model an out-of-process
    transport/semantic failure of the runtime dispatch.  It is intentionally
    none of Magazine's own exception types, so a ``ValueError`` /
    ``MagazineStateError`` / ``RunIdentityError`` raised by the real
    boundaries can never be confused with it.
    """


def _write_magazine(path: Path, document: dict) -> None:
    path.write_text(json.dumps(document), encoding="utf-8")


@patch("magazine.runtime_dispatch.validate_live_runtime_pair")
def test_execute_one_cartridge_advances_a_non_final_cartridge(
    mock_preflight,
    tmp_path: Path,
) -> None:
    """A valid RUNNING run (current "cart-b", a non-empty todo, one earlier
    cartridge already in done, and a valid revision) advanced through the
    real public orchestration boundary dispatches exactly one cartridge and
    completes it.  The returned ``OneCartridgeResult`` carries "cart-b" and
    the dispatch result, and the persisted post-completion state is ``READY``
    with "cart-b" appended to done, current cleared, todo preserved and the
    revision incremented by exactly one; the run lock is released.
    """
    # No live discovery needed: the preflight boundary is a no-op.
    mock_preflight.return_value = None

    # --- fixtures ---------------------------------------------------------
    magazine_path = tmp_path / "magazine.json"
    _write_magazine(
        magazine_path,
        {
            "schema_version": "1.0",
            "cartridges": [
                {"id": "cart-a", "job_type": "research", "prompt": "prompt for cart-a"},
                {"id": "cart-b", "job_type": "research", "prompt": CARTRIDGE_PROMPT},
                {"id": "cart-c", "job_type": "research", "prompt": "prompt for cart-c"},
            ],
        },
    )

    state_path = tmp_path / "state.json"
    lock_path = tmp_path / "run.lock"
    owner_id = "owner-1"

    running = {
        "done": ["cart-a"],      # at least one already-completed cartridge
        "current": CARTRIDGE_ID, # the cartridge to be advanced ("cart-b")
        "todo": ["cart-c"],      # non-empty -> NOT the final cartridge
        "status": STATUS_RUNNING,
        "revision": 1,           # valid (non-negative int) revision
    }
    state_store.save_run_state(str(state_path), running)

    # No lock file before the call.
    assert not lock_path.exists()

    # Unique object so the dispatch result can be checked by identity.
    sentinel = object()
    mock_dispatch = AsyncMock(return_value=sentinel)

    # --- act: one single-cartridge orchestration --------------------------
    result = asyncio.run(
        orchestration.execute_one_cartridge(
            magazine_path=str(magazine_path),
            state_path=str(state_path),
            lock_path=str(lock_path),
            owner_id=owner_id,
            base_url=BASE_URL,
            api_key=API_KEY,
            tool_role=TOOL_ROLE,
            model_map=MODEL_MAP,
            tool_map=TOOL_MAP,
            dispatch=mock_dispatch,
        )
    )

    # --- 1. execute_one_cartridge returns OneCartridgeResult ---------------
    assert isinstance(result, orchestration.OneCartridgeResult)

    # --- 2. result.cartridge_id == "cart-b" -------------------------------
    assert result.cartridge_id == CARTRIDGE_ID

    # --- 3. result.dispatch_result is exactly the runtime result produced
    #     by the fake dispatch (identity check) --------------------------
    assert result.dispatch_result is sentinel

    # --- 4. the fake dispatch was called exactly once for "cart-b" --------
    #     (assert_awaited_once_with asserts a single await with this exact
    #      argument; "prompt for cart-b" is only ever produced for "cart-b").
    mock_dispatch.assert_awaited_once_with(CARTRIDGE_PROMPT)
    assert mock_dispatch.await_count == 1

    # --- 5. the returned post-completion state is exactly the non-final
    #     READY state -----------------------------------------------------
    expected = {
        "done": ["cart-a", CARTRIDGE_ID],  # previous done entries followed by "cart-b"
        "current": None,                   # current cleared
        "todo": ["cart-c"],                # remaining todo preserved unchanged
        "status": STATUS_READY,            # work remains -> READY (not COMPLETED)
        "revision": 2,                     # incremented by exactly one
    }
    assert result.state == expected

    # Field-level reinforcement of the state invariants.
    assert result.state["done"] == ["cart-a", CARTRIDGE_ID]
    assert result.state["current"] is None
    assert result.state["todo"] == ["cart-c"]
    assert result.state["status"] == STATUS_READY
    assert result.state["revision"] == running["revision"] + 1

    # --- 6. the persisted state exactly equals result.state ---------------
    loaded = state_store.load_run_state(str(state_path))
    assert loaded == result.state
    assert loaded == expected

    # --- 7. no lock file remains (released by the guarded completion) -----
    assert not lock_path.exists()

    # --- 8. only one cartridge was dispatched (already asserted in item 4
    #     via assert_awaited_once_with; re-stated explicitly via await_count)
    assert mock_dispatch.await_count == 1


@patch("magazine.runtime_dispatch.validate_live_runtime_pair")
def test_execute_one_cartridge_rejects_drifted_current_before_completion(
    mock_preflight,
    tmp_path: Path,
) -> None:
    """The original TOCTOU race is closed at the orchestration boundary.

    The initial run is a valid ``RUNNING`` state with ``current == "cart-b"``,
    a non-empty ``todo`` (cart-c still queued), and an earlier cartridge in
    done.  The real Stage-2 dispatch (``magazine.execute.dispatch_current_cartridge``)
    genuinely selects and dispatches "cart-b" (its prompt is the one the
    dispatch callback receives).  While the external dispatch callback is still
    in flight, a rival process persists a *drifted but valid* run state whose
    current is "cart-c" (cart-b recorded in done, todo emptied, revision
    advanced).  When the callback returns normally and the orchestration
    attempts completion, the guarded Stage-3 completion re-reads the persisted
    state under the run lock, sees current="cart-c" while still carrying
    ``expected_current_id="cart-b"`` (exactly the ID the dispatch selected),
    and raises ``RunIdentityError`` before any completion transition.  "cart-c"
    is therefore NOT completed, the drifted state is left byte-identical, the
    revision stays at 2, no second dispatch occurs, and the lock is released.
    """
    mock_preflight.return_value = None

    # --- fixtures ---------------------------------------------------------
    magazine_path = tmp_path / "magazine.json"
    _write_magazine(
        magazine_path,
        {
            "schema_version": "1.0",
            "cartridges": [
                {"id": "cart-a", "job_type": "research", "prompt": "prompt for cart-a"},
                {"id": "cart-b", "job_type": "research", "prompt": CARTRIDGE_PROMPT},
                {"id": "cart-c", "job_type": "research", "prompt": "prompt for cart-c"},
            ],
        },
    )

    state_path = tmp_path / "state.json"
    lock_path = tmp_path / "run.lock"
    owner_id = "owner-1"

    # Initial valid RUNNING state: current cart-b, a queued cart-c, done.
    initial = {
        "done": ["cart-a"],
        "current": CARTRIDGE_ID,      # "cart-b"
        "todo": ["cart-c"],
        "status": STATUS_RUNNING,
        "revision": 1,
    }
    state_store.save_run_state(str(state_path), initial)
    assert not lock_path.exists()

    # The state a rival process persists DURING the dispatch callback — a valid
    # RUNNING state with cart-c now current.
    drifted = {
        "done": ["cart-a", "cart-b"],  # cart-b recorded as done
        "current": "cart-c",            # cart-c becomes current
        "todo": [],                     # no work beyond the current cartridge
        "status": STATUS_RUNNING,
        "revision": 2,                 # advanced by exactly one
    }

    sentinel = object()
    drift_bytes: list[bytes] = []

    # External runtime dispatch callback: confirm it was invoked for cart-b,
    # drift the persisted state while the dispatch is in flight, capture the
    # exact bytes of the new state, and return a normal successful result.
    def _side_effect(prompt: str):
        # 1. Confirm it was invoked for cart-b's prompt.
        assert (
            prompt == CARTRIDGE_PROMPT
        ), f"dispatched unexpected prompt {prompt!r}; expected {CARTRIDGE_PROMPT!r}"
        # 2. Persist the drifted valid state BEFORE the callback returns.
        state_store.save_run_state(str(state_path), drifted)
        # 3. Capture the exact bytes of the newly persisted state.
        drift_bytes.append(state_path.read_bytes())
        # 4. Return a normal successful dispatch result.
        return sentinel

    mock_dispatch = AsyncMock(side_effect=_side_effect)

    # --- act: dispatch -> guarded completion (the identity chain) ---------
    with pytest.raises(RunIdentityError) as excinfo:
        asyncio.run(
            orchestration.execute_one_cartridge(
                magazine_path=str(magazine_path),
                state_path=str(state_path),
                lock_path=str(lock_path),
                owner_id=owner_id,
                base_url=BASE_URL,
                api_key=API_KEY,
                tool_role=TOOL_ROLE,
                model_map=MODEL_MAP,
                tool_map=TOOL_MAP,
                dispatch=mock_dispatch,
            )
        )

    # --- 1. the external dispatch callback was invoked exactly once for cart-b
    #     (a single await with cart-b's exact prompt).
    mock_dispatch.assert_awaited_once_with(CARTRIDGE_PROMPT)
    assert mock_dispatch.await_count == 1

    # --- 2/3. the dispatch callback returned normally: the only exception
    #     raised is the RunIdentityError from the *completion* step (point 3),
    #     not a dispatch failure.  (A failed dispatch would propagate a
    #     different exception before completion is ever attempted.)
    exc = excinfo.value
    assert isinstance(exc, RunIdentityError)

    # --- 4. the error identifies expected "cart-b" vs actual persisted
    #     current "cart-c" (the guarded re-read saw the drifted current).
    msg = str(exc)
    assert "cart-b" in msg, msg
    assert "cart-c" in msg, msg

    # --- 5. the state written during the dispatch callback remains byte-
    #     identical after the exception (no completion save occurred).
    assert state_path.read_bytes() == drift_bytes[0]

    # --- 6. the persisted state remains exactly the drifted state.
    loaded = state_store.load_run_state(str(state_path))
    assert loaded == drifted

    # --- 7. cart-c is NOT appended to done by this orchestration attempt.
    assert "cart-c" not in loaded["done"]
    assert loaded["done"] == ["cart-a", "cart-b"]

    # --- 8. revision remains 2.
    assert loaded["revision"] == 2

    # --- 9. no completion transition occurred after the mismatch: the run
    #     is still RUNNING with cart-c current (exactly the drifted state).
    assert loaded["status"] == STATUS_RUNNING
    assert loaded["current"] == "cart-c"

    # --- 10. no lock file remains (acquired then released by the guarded
    #     completion, even on the identity-mismatch path).
    assert not lock_path.exists()

    # --- 11. there is no second dispatch (covered by await_count == 1
    #         above; restated here).
    assert mock_dispatch.await_count == 1


@patch("magazine.runtime_dispatch.validate_live_runtime_pair")
@patch("magazine.orchestration.complete.complete_current_cartridge")
def test_execute_one_cartridge_never_completes_when_dispatch_fails(
    mock_completion,
    mock_preflight,
    tmp_path: Path,
) -> None:
    """An external dispatch failure propagates unchanged from the
    orchestration boundary; completion is never attempted and the
    persisted state is untouched.

    The initial run is the same valid ``RUNNING`` state as the existing
    tests: ``current == "cart-b"``, a non-empty ``todo`` (cart-c still
    queued), an earlier cartridge in ``done``, revision 1.  The real
    Stage-2 dispatch boundary
    (:mod:`magazine.execute.dispatch_current_cartridge`) genuinely selects
    and routes "cart-b" through the real runtime routing /preflight
    composition; only the live runtime pair preflight and the
    external dispatch callback are faked (the same preflight fake as the
    existing tests), so no live network discovery occurs.

    The faked dispatch callback raises ``DispatchFailure`` — a unique test
    exception — for cart-b's exact prompt.  Because
    ``magazine.orchestration.execute_one_cartridge`` composes
    ``dispatch_current_cartridge`` followed, *only if the dispatch returned
    without raising*, by ``complete.complete_current_cartridge`` with no
    ``try/except``, no retry and no recovery, the ``DispatchFailure``
    propagates out of ``execute_one_cartridge`` before the Stage-3
    completion boundary is ever entered.

    ``magazine.orchestration.complete.complete_current_cartridge`` is
    patched here only as a *call detector*: the assertion to prove is that
    completion is NEVER ENTERED after the dispatch raises.  It is never
    called (and hence never awaited, never given arguments, and never
    given ``expected_current_id``).

    Afterwards:
      * the external dispatch callback was awaited exactly once, for cart-b's
        exact prompt (no retry, no second dispatch);
      * the completion boundary was never called;
      * the persisted state bytes are byte-identical to the bytes captured
        before the dispatch (the state store wrote nothing after that);
      * the loaded state is exactly the original RUNNING state (done =
        ["cart-a"], current = "cart-b", todo = ["cart-c"], revision = 1);
      * cart-b was NOT appended to done; current remains cart-b; the run is
        still ``RUNNING``;
      * no run lock file exists (dispatch never holds a lock — only the
        never-reached completion boundary does).
    """
    # No live discovery needed: the preflight boundary is a no-op (same
    # fake as the existing orchestration tests).
    mock_preflight.return_value = None

    # --- fixtures ---------------------------------------------------------
    magazine_path = tmp_path / "magazine.json"
    _write_magazine(
        magazine_path,
        {
            "schema_version": "1.0",
            "cartridges": [
                {"id": "cart-a", "job_type": "research", "prompt": "prompt for cart-a"},
                {"id": "cart-b", "job_type": "research", "prompt": CARTRIDGE_PROMPT},
                {"id": "cart-c", "job_type": "research", "prompt": "prompt for cart-c"},
            ],
        },
    )

    state_path = tmp_path / "state.json"
    lock_path = tmp_path / "run.lock"
    owner_id = "owner-1"

    initial = {
        "done": ["cart-a"],
        "current": CARTRIDGE_ID,
        "todo": ["cart-c"],
        "status": STATUS_RUNNING,
        "revision": 1,
    }

    # --- persist the initial state with the REAL state store and capture
    #     the exact bytes it wrote on disk --------------------------------
    state_store.save_run_state(str(state_path), initial)
    initial_bytes = state_path.read_bytes()
    assert not lock_path.exists()  # no lock file before the call

    # --- external dispatch callback: unique test exception, called for
    #     cart-b's exact prompt ------------------------------------------
    failure = DispatchFailure("external dispatch of cart-b failed")
    # The dispatch raises before returning any result; the same exception
    # instance must then surface from ``execute_one_cartridge`` unchanged.
    mock_dispatch = AsyncMock(side_effect=failure)

    # --- act: one single-cartridge orchestration (dispatch -> complete) ---
    with pytest.raises(DispatchFailure) as excinfo:
        asyncio.run(
            orchestration.execute_one_cartridge(
                magazine_path=str(magazine_path),
                state_path=str(state_path),
                lock_path=str(lock_path),
                owner_id=owner_id,
                base_url=BASE_URL,
                api_key=API_KEY,
                tool_role=TOOL_ROLE,
                model_map=MODEL_MAP,
                tool_map=TOOL_MAP,
                dispatch=mock_dispatch,
            )
        )

    # --- 1. DispatchFailure propagates unchanged from execute_one_cartridge.
    #     The SAME exception instance reaches the caller: no wrapping, no
    #     re-raise, no conversion by any boundary. -------------------------
    assert isinstance(excinfo.value, DispatchFailure)
    assert excinfo.value is failure

    # --- 2. The external dispatch callback was awaited exactly once. -------
    assert mock_dispatch.await_count == 1

    # --- 3. It was invoked for cart-b's exact prompt.  "prompt for cart-b"
    #     is only ever produced for the cartridge with id "cart-b" (the
    #     exact current in the persisted state), so a single await with this
    #     exact argument proves cart-b — and only cart-b — was dispatched.
    #     Restated at the argument level via await_args. -------------------
    mock_dispatch.assert_awaited_once_with(CARTRIDGE_PROMPT)
    assert mock_dispatch.await_args == call(CARTRIDGE_PROMPT)

    # --- 4. The completion boundary mock was never called: the Stage-3
    #     boundary ``magazine.complete.complete_current_cartridge`` was
    #     never ENTERED after dispatch raised. (Mock kept purely as a call
    #     detector.) --------------------------------------------------------
    mock_completion.assert_not_called()

    # --- 5. Persisted state remains byte-identical to the bytes captured
    #     before the dispatch. ------------------------------------------------
    assert state_path.read_bytes() == initial_bytes

    # --- 6. Loaded state remains exactly the initial state. -----------------
    loaded = state_store.load_run_state(str(state_path))
    assert loaded == {
        "done": ["cart-a"],
        "current": CARTRIDGE_ID,
        "todo": ["cart-c"],
        "status": STATUS_RUNNING,
        "revision": 1,
    }

    # --- 7. Revision remains 1. ----------------------------------------------
    assert loaded["revision"] == 1

    # --- 8. cart-b is NOT appended to done. -----------------------------------
    assert loaded["done"] == ["cart-a"]
    assert CARTRIDGE_ID not in loaded["done"]

    # --- 9. current remains cart-b (the run is still mid-dispatch). ---------
    assert loaded["current"] == CARTRIDGE_ID
    assert loaded["status"] == STATUS_RUNNING

    # --- 10. No lock file exists after the failure.  Dispatch never holds the
    #     run lock (only the never-reached completion boundary does), so no
    #     lock was ever created. ----------------------------------------------
    assert not lock_path.exists()

    # --- 11. No retry and no second dispatch (re-stating await_count == 1). --
    assert mock_dispatch.await_count == 1
