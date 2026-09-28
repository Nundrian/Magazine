"""Focused test for magazine.run_loop (stage-5 bounded multi-cartridge loop).

Exactly one test — the normal two-cartridge happy path of
``run_all_cartridges``:

    RUNNING cart-b
      -> dispatch/complete cart-b
      -> READY
      -> guarded begin-next cart-c
      -> RUNNING cart-c
      -> dispatch/complete cart-c
      -> COMPLETED
      -> return MultiCartridgeResult

What runs for real (no mocking of these):
  * ``magazine.run_loop.run_all_cartridges``
  * ``magazine.orchestration.execute_one_cartridge``
  * ``magazine.execute.dispatch_current_cartridge``
  * the runtime routing + preflight composition (``magazine.runtime_dispatch``)
  * ``magazine.complete.complete_current_cartridge``
  * ``magazine.run_action.persist_complete_current_cartridge``
    (lock acquire/release, under-lock state re-read, expected-current identity
    guard, RUNNING -> READY/COMPLETED transition, revision increment, atomic save)
  * ``magazine.run_action.persist_begin_next_cartridge``
    (lock acquire/release, under-lock state re-read, READY -> RUNNING transition,
    revision increment, atomic save)
  * the real ``magazine.run_transition`` transitions
  * the real ``magazine.state_store`` atomic persistence
  * the real ``magazine.run_lock`` behaviour

Only the live runtime preflight boundary and the final external dispatch
callback are faked, so no live Open WebUI / HTTP discovery occurs:
  * ``magazine.runtime_dispatch.validate_live_runtime_pair`` (preflight)
  * the external ``dispatch`` callback

The initial persisted run is already ``RUNNING`` with a ``current`` cartridge
(``cart-b``), satisfying ``run_all_cartridges``' precondition.  The external
dispatch records every prompt it receives (in exact call order) and returns a
distinct successful sentinel value per cartridge.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from unittest.mock import AsyncMock, patch

import pytest

from magazine import run_action
from magazine import run_loop
from magazine import state_store
from magazine.run_transition import RunTransitionError
from magazine.state import STATUS_COMPLETED, STATUS_READY, STATUS_RUNNING


BASE_URL = "http://localhost:8080"
API_KEY = "test-key"

# --- cartridges (distinct prompts make dispatch order unambiguous) ----------
PROMPT_A = "prompt for cart-a"
PROMPT_B = "prompt for cart-b"
PROMPT_C = "prompt for cart-c"

# --- distinct successful return value per cartridge --------------------------
SENTINEL_B = object()
SENTINEL_C = object()

# --- dispatch-failure exception (a single instance, identity-checked) --------
class DispatchFailure(RuntimeError):
    pass


def _write_magazine(path: Path, document: dict) -> None:
    path.write_text(json.dumps(document), encoding="utf-8")


@patch("magazine.runtime_dispatch.validate_live_runtime_pair")
def test_run_all_cartridges_advances_two_cartridges_to_completion(
    mock_preflight,
    tmp_path: Path,
) -> None:
    """A valid ``RUNNING`` run with ``current == "cart-b"`` and one queued
    ``cart-c`` is advanced through the real Stage-5 loop until ``COMPLETED``.

    The two dispatched cartridges use *different* job types — and hence two
    *different* resolved models: "model-A" for cart-b (research) and
    "model-B" for cart-c (summarize).  This proves the single shared dispatch
    dependency receives the resolved per-cartridge model ID on each call.
    The loop dispatches exactly cart-b/model-A then cart-c/model-B (two
    distinct (prompt, model_id) pairs, in order), completes each,
    guarded-begins cart-c between the two, and stops after cart-c completes.
    The returned ``MultiCartridgeResult.final_state`` is exactly the fully-
    completed state, the persisted state matches it, and the run lock is
    released.
    """
    # No live discovery needed: the runtime-pair preflight boundary is a no-op.
    mock_preflight.return_value = None

    # --- fixtures -----------------------------------------------------------
    magazine_path = tmp_path / "magazine.json"
    _write_magazine(
        magazine_path,
        {
            "schema_version": "1.0",
            "cartridges": [
                {"id": "cart-a", "job_type": "research", "prompt": PROMPT_A},
                {"id": "cart-b", "job_type": "research", "prompt": PROMPT_B},
                {"id": "cart-c", "job_type": "summarize", "prompt": PROMPT_C},
            ],
        },
    )

    state_path = str(tmp_path / "state.json")
    lock_path = tmp_path / "run.lock"
    owner_id = "owner-1"

    # Already RUNNING/current (run_all_cartridges precondition).
    initial = {
        "done": ["cart-a"],
        "current": "cart-b",
        "todo": ["cart-c"],
        "status": STATUS_RUNNING,
        "revision": 1,
    }
    state_store.save_run_state(state_path, initial)

    # No lock file before the call.
    assert not Path(lock_path).exists()

    # External runtime dispatch callback: records every (prompt, model_id)
    # pair in exact order and returns a distinct stable successful value per
    # cartridge.  No state mutation.
    pairs: list[tuple[str, str]] = []

    def _fake_dispatch(prompt: str, model_id: str):
        pairs.append((prompt, model_id))
        if prompt == PROMPT_B:
            return SENTINEL_B
        if prompt == PROMPT_C:
            return SENTINEL_C
        raise AssertionError(f"external dispatch received an unexpected prompt: {prompt!r}")

    mock_dispatch = AsyncMock(side_effect=_fake_dispatch)

    # --- act: the full two-cartridge bounded loop --------------------------
    result = asyncio.run(
        run_loop.run_all_cartridges(
            magazine_path=magazine_path,
            state_path=state_path,
            lock_path=str(lock_path),
            owner_id=owner_id,
            base_url=BASE_URL,
            api_key=API_KEY,
            tool_role="terminal",
            model_map={"research": "model-A", "summarize": "model-B"},
            tool_map={"terminal": "bash"},
            dispatch=mock_dispatch,
        )
    )

    # --- 1. result is a run_loop.MultiCartridgeResult -----------------------
    assert isinstance(result, run_loop.MultiCartridgeResult)

    # --- 2. the external dispatch callback was invoked exactly twice ---------
    assert mock_dispatch.await_count == 2

    # --- 3. exact dispatch order: cart-b/model-A first, cart-c/model-B second
    #     (two different models prove the per-cartridge model_id is forwarded)
    assert pairs == [(PROMPT_B, "model-A"), (PROMPT_C, "model-B")]

    # --- 4. no cartridge was dispatched twice --------------------------------
    assert len(set(pairs)) == 2

    # --- 5. no other cartridge was dispatched --------------------------------
    assert set(pairs) == {(PROMPT_B, "model-A"), (PROMPT_C, "model-B")}

    # --- 6. the returned final_state is exactly the completed state ---------
    expected_final = {
        "done": ["cart-a", "cart-b", "cart-c"],
        "current": None,
        "todo": [],
        "status": STATUS_COMPLETED,
        "revision": 4,
        # revision reasoning, explicit:
        #   initial revision 1
        #   -> complete cart-b   (complete_current_cartridge) = 2
        #   -> begin cart-c      (begin_next_cartridge)       = 3
        #   -> complete cart-c   (complete_current_cartridge) = 4
    }
    assert result.final_state == expected_final

    # --- 7. the persisted state exactly equals result.final_state -----------
    loaded = state_store.load_run_state(state_path)
    assert loaded == expected_final
    assert loaded == result.final_state

    # --- 8. persisted status is COMPLETED ------------------------------------
    assert loaded["status"] == STATUS_COMPLETED

    # --- 9. current is None ---------------------------------------------------
    assert loaded["current"] is None

    # --- 10. todo is empty ----------------------------------------------------
    assert loaded["todo"] == []

    # --- 11. done ordering is exactly ["cart-a", "cart-b", "cart-c"] ---------
    assert loaded["done"] == ["cart-a", "cart-b", "cart-c"]

    # --- 12. no run lock file remains -----------------------------------------
    assert not Path(lock_path).exists()

    # --- 13. the loop stopped after cart-c completed --------------------------
    #     * no third dispatch (await_count == 2 above), and
    #     * no begin-next attempt after COMPLETED: a begin-next on the
    #       COMPLETED state would raise RunTransitionError (begin requires
    #       READY), so a clean return with revision exactly 4 proves none
    #       occurred.
    assert mock_dispatch.await_count == 2
    assert loaded["revision"] == 4


@patch("magazine.runtime_dispatch.validate_live_runtime_pair")
@patch("magazine.run_loop.run_action.persist_begin_next_cartridge")
def test_run_all_cartridges_stops_on_dispatch_failure(
    mock_begin_next,
    mock_preflight,
    tmp_path: Path,
) -> None:
    """A dispatch failure on the first current cartridge (cart-b) propagates
    unchanged from ``run_all_cartridges`` and stops the Stage-5 loop immediately.

    cart-b is dispatched exactly once (for its exact prompt) and the unique
    ``DispatchFailure`` instance reaches the caller.  The loop never advances:
    ``begin-next`` is never called, cart-c is never dispatched, there is no
    retry and no second dispatch, and the persisted state is byte-identical to
    its pre-loop form (cart-b not appended to done, revision still 1, current
    still cart-b).  The run lock is released.

    What runs for real (not mocked):
      * run_loop.run_all_cartridges
      * orchestration.execute_one_cartridge
      * execute.dispatch_current_cartridge
      * the runtime routing / preflight composition
      * the real state store, transitions, and lock behaviour

    Only two boundaries are faked (same as the happy-path test):
      * the live-runtime preflight boundary ``validate_live_runtime_pair``
      * the external dispatch callback (which raises the unique failure)

    ``persist_begin_next_cartridge`` is patched ONLY as a call detector; it
    must never be called.
    """
    # No live discovery needed: the runtime-pair preflight boundary is a no-op.
    mock_preflight.return_value = None

    # --- fixtures -----------------------------------------------------------
    magazine_path = tmp_path / "magazine.json"
    _write_magazine(
        magazine_path,
        {
            "schema_version": "1.0",
            "cartridges": [
                {"id": "cart-a", "job_type": "research", "prompt": PROMPT_A},
                {"id": "cart-b", "job_type": "research", "prompt": PROMPT_B},
                {"id": "cart-c", "job_type": "research", "prompt": PROMPT_C},
            ],
        },
    )

    state_path = str(tmp_path / "state.json")
    lock_path = tmp_path / "run.lock"
    owner_id = "owner-2"

    # Already RUNNING/current (run_all_cartridges precondition).
    initial = {
        "done": ["cart-a"],
        "current": "cart-b",
        "todo": ["cart-c"],
        "status": STATUS_RUNNING,
        "revision": 1,
    }
    state_store.save_run_state(state_path, initial)

    # No lock file before the call.
    assert not Path(lock_path).exists()
    # Capture the exact bytes of the persisted state for a byte-identical check.
    bytes_before = Path(state_path).read_bytes()

    # External dispatch callback: when cart-b's prompt is dispatched it raises
    # ONE specific DispatchFailure instance (identity-checked below).  It
    # performs no state mutation.  cart-c's prompt would be unexpected.
    prompts: list[str] = []
    FAILURE = DispatchFailure("external dispatch for cart-b failed")

    def _fake_dispatch(prompt: str, model_id: str):
        prompts.append(prompt)
        if prompt == PROMPT_B:
            raise FAILURE
        raise AssertionError(
            f"external dispatch received an unexpected prompt: {prompt!r}"
        )

    mock_dispatch = AsyncMock(side_effect=_fake_dispatch)

    # --- act: the loop must raise on the first dispatch ---------------------
    with pytest.raises(DispatchFailure) as exc_info:
        asyncio.run(
            run_loop.run_all_cartridges(
                magazine_path=magazine_path,
                state_path=state_path,
                lock_path=str(lock_path),
                owner_id=owner_id,
                base_url=BASE_URL,
                api_key=API_KEY,
                tool_role="terminal",
                model_map={"research": "m1"},
                tool_map={"terminal": "bash"},
                dispatch=mock_dispatch,
            )
        )

    # --- 1/2. DispatchFailure propagates from run_all_cartridges, unchanged ---
    assert exc_info.value is FAILURE

    # --- 3. the external dispatch callback was invoked exactly once ----------
    assert mock_dispatch.await_count == 1

    # --- 4. invoked for cart-b's exact prompt --------------------------------
    assert prompts == [PROMPT_B]

    # --- 5. cart-c was never dispatched --------------------------------------
    assert PROMPT_C not in prompts

    # --- 6. no retry and no second dispatch ----------------------------------
    assert len(prompts) == 1
    assert len(set(prompts)) == 1
    assert mock_dispatch.await_count == 1

    # --- 7. persist_begin_next_cartridge was never called --------------------
    assert mock_begin_next.call_count == 0

    # --- 8. persisted state remains byte-identical to pre-loop bytes ---------
    assert Path(state_path).read_bytes() == bytes_before

    # --- 9. loaded state remains exactly the initial RUNNING state -------------
    loaded = state_store.load_run_state(state_path)
    assert loaded == initial

    # --- 10. cart-b is not appended to done ------------------------------------
    assert "cart-b" not in loaded["done"]
    assert loaded["done"] == ["cart-a"]

    # --- 11. revision remains 1 -----------------------------------------------
    assert loaded["revision"] == 1

    # --- 12. current remains cart-b -------------------------------------------
    assert loaded["current"] == "cart-b"

    # --- 13. no run lock file exists after the failure -------------------------
    assert not Path(lock_path).exists()


@patch("magazine.runtime_dispatch.validate_live_runtime_pair")
def test_run_all_cartridges_completion_failure_never_begins_next(
    mock_preflight,
    tmp_path: Path,
) -> None:
    """A *successful* external dispatch of cart-b followed by a raising
    completion boundary propagates the unique ``CompletionFailure`` instance
    from ``run_all_cartridges`` and stops the Stage-5 loop before advancement.

    Proven chain:

        cart-b external dispatch returns successfully
          -> execute_one_cartridge calls complete_current_cartridge
             with expected_current_id="cart-b"
          -> completion raises the unique FAILURE
          -> run_all_cartridges propagates FAILURE unchanged
          -> begin-next is never entered
          -> cart-c is never dispatched
          -> no retry / no second dispatch
          -> persisted state is byte-identical to its pre-loop form
             (cart-b not appended to done, revision still 1, current still
             cart-b), and the run lock is released (never left behind)

    What runs for real (not mocked):
      * run_loop.run_all_cartridges
      * orchestration.execute_one_cartridge
      * execute.dispatch_current_cartridge
      * the runtime routing composition (preflight is a no-op via the
        patched ``validate_live_runtime_pair`` boundary)
      * the real state store, transitions, and lock behaviour

    Faked boundaries (exactly three, no other production calls are mocked):
      * the live-runtime preflight boundary ``validate_live_runtime_pair``
        (module-level ``@patch`` decorator, a no-op)
      * ``magazine.orchestration.complete.complete_current_cartridge`` — the
        completion boundary, injected via context manager with ``side_effect``
        so the *exact* ``FAILURE`` instance is raised (identity-checked)
      * ``magazine.run_loop.run_action.persist_begin_next_cartridge`` —
        patched ONLY as a call detector via context manager; it must never
        be called
    """
    # No live discovery needed: the runtime-pair preflight boundary is a no-op.
    mock_preflight.return_value = None

    # The injected completion failure: one class, one unique instance.
    class CompletionFailure(RuntimeError):
        pass

    FAILURE = CompletionFailure("completion of cart-b failed")

    # --- fixtures ------------------------------------------------------------
    magazine_path = tmp_path / "magazine.json"
    _write_magazine(
        magazine_path,
        {
            "schema_version": "1.0",
            "cartridges": [
                {"id": "cart-a", "job_type": "research", "prompt": PROMPT_A},
                {"id": "cart-b", "job_type": "research", "prompt": PROMPT_B},
                {"id": "cart-c", "job_type": "research", "prompt": PROMPT_C},
            ],
        },
    )

    state_path = str(tmp_path / "state.json")
    lock_path = tmp_path / "run.lock"
    owner_id = "owner-3"

    # Already RUNNING/current (run_all_cartridges precondition).
    initial = {
        "done": ["cart-a"],
        "current": "cart-b",
        "todo": ["cart-c"],
        "status": STATUS_RUNNING,
        "revision": 1,
    }
    # Save the initial state with the real state store.
    state_store.save_run_state(state_path, initial)

    # No lock file before the call.
    assert not Path(lock_path).exists()
    # Capture the exact bytes of the persisted state for a byte-identity check.
    bytes_before = Path(state_path).read_bytes()

    # External runtime dispatch callback: for cart-b's exact prompt it must
    # be awaited once and return one distinct stable successful sentinel
    # (the existing module-level ``SENTINEL_B``); it performs no state
    # mutation.  Any unexpected prompt fails the test.
    prompts: list[str] = []

    def _fake_dispatch(prompt: str, model_id: str):
        prompts.append(prompt)
        if prompt == PROMPT_B:
            return SENTINEL_B
        raise AssertionError(
            f"external dispatch received an unexpected prompt: {prompt!r}"
        )

    mock_dispatch = AsyncMock(side_effect=_fake_dispatch)

    # --- act: dispatch succeeds, completion raises, failure propagates --------
    with pytest.raises(CompletionFailure) as exc_info:
        with (
            patch(
                "magazine.orchestration.complete.complete_current_cartridge",
                side_effect=FAILURE,
            ) as mock_complete,
            patch(
                "magazine.run_loop.run_action.persist_begin_next_cartridge",
            ) as mock_begin_next,
        ):
            asyncio.run(
                run_loop.run_all_cartridges(
                    magazine_path=magazine_path,
                    state_path=state_path,
                    lock_path=str(lock_path),
                    owner_id=owner_id,
                    base_url=BASE_URL,
                    api_key=API_KEY,
                    tool_role="terminal",
                    model_map={"research": "m1"},
                    tool_map={"terminal": "bash"},
                    dispatch=mock_dispatch,
                )
            )

    # --- 1. the unique FAILURE instance propagates, identity-checked ----------
    assert exc_info.value is FAILURE

    # --- 2. the external dispatch callback was awaited exactly once -----------
    assert mock_dispatch.await_count == 1

    # --- 3. its sole arguments were cart-b's exact prompt and model ID -------
    mock_dispatch.assert_awaited_once_with(PROMPT_B, "m1")
    assert prompts == [PROMPT_B]

    # --- 4. cart-c was never dispatched ---------------------------------------
    assert PROMPT_C not in prompts

    # --- 5. no retry / no second dispatch --------------------------------------
    assert len(prompts) == 1
    assert len(set(prompts)) == 1

    # --- 6. the completion boundary was called exactly once --------------------
    #     7. with exactly the real call signature and cart-b as the identity
    #         guard (arg types follow the existing test conventions: state_path
    #         and lock_path are str as forwarded to run_all_cartridges).
    mock_complete.assert_called_once_with(
        state_path,
        str(lock_path),
        owner_id,
        expected_current_id="cart-b",
    )

    # --- 8. begin-next was never entered --------------------------------------
    mock_begin_next.assert_not_called()

    # --- 9. persisted state bytes remain exactly identical to pre-loop bytes ---
    assert Path(state_path).read_bytes() == bytes_before

    # --- 10. loaded state remains exactly the initial RUNNING state -----------
    loaded = state_store.load_run_state(state_path)
    assert loaded == {
        "done": ["cart-a"],
        "current": "cart-b",
        "todo": ["cart-c"],
        "status": STATUS_RUNNING,
        "revision": 1,
    }

    # --- 11. cart-b is not in done ---------------------------------------------
    assert "cart-b" not in loaded["done"]
    assert loaded["done"] == ["cart-a"]

    # --- 12. revision remains 1 -------------------------------------------------
    assert loaded["revision"] == 1

    # --- 13. current remains cart-b --------------------------------------------
    assert loaded["current"] == "cart-b"

    # --- 14. no run lock file remains ------------------------------------------
    assert not Path(lock_path).exists()


@patch("magazine.runtime_dispatch.validate_live_runtime_pair")
def test_run_all_cartridges_begin_next_race_detected_under_lock(
    mock_preflight,
    tmp_path: Path,
) -> None:
    """The completion -> begin-next TOCTOU gap is closed by the real guarded
    ``begin-next`` action re-reading the state under the run lock.

    Sequence proven:

        our loop dispatches cart-b successfully (real path; one distinct
        sentinel return; no state mutation)
          -> the REAL completion path persists exactly:
               done [cart-a, cart-b], current None, todo [cart-c],
               READY, revision 2
          -> a simulated second process begins cart-c with the SAME REAL
             guarded action (owner "other-owner"), persisting exactly:
               done [cart-a, cart-b], current cart-c, todo [],
               RUNNING, revision 3
          -> our loop's guarded begin-next runs: its under-lock re-read sees
             RUNNING (not READY) and raises RunTransitionError
          -> the error propagates from run_all_cartridges unchanged
          -> cart-c is NEVER externally dispatched by our loop

    What runs for real (not mocked):
      * run_loop.run_all_cartridges
      * orchestration.execute_one_cartridge
      * execute.dispatch_current_cartridge
      * the runtime routing composition
      * complete.complete_current_cartridge
      * run_action.persist_complete_current_cartridge
        (lock acquire/release, under-lock re-read, identity guard,
         RUNNING -> READY transition, revision increment, atomic save)
      * the real complete_current_cartridge / begin_next_cartridge transitions
      * run_action.persist_begin_next_cartridge — BOTH the competing call and
        the loop's own call go through the real function (captured before the
        loop's reference was patched); each acquires and releases the real
        run lock

    Faked boundaries (exactly two, plus the injection wrapper):
      * ``magazine.runtime_dispatch.validate_live_runtime_pair`` (preflight
        no-op, module-level decorator)
      * the external dispatch callback (AsyncMock; distinct sentinel for
        cart-b's exact prompt only)
      * ``magazine.run_loop.run_action.persist_begin_next_cartridge`` is
        patched with a side-effect wrapper that injects the competing
        transition BEFORE delegating the loop's own call to the real function.
        The real function is captured via the direct ``run_action`` module
        attribute first; the wrapper must call THAT object, never the
        patched reference (which would recurse into the mock).
    """
    # No live discovery needed: the runtime-pair preflight boundary is a no-op.
    mock_preflight.return_value = None

    # Capture the REAL begin-next function BEFORE patching the loop's
    # reference.  The wrapper below delegates to this captured object so the
    # competing transition and the loop's attempted begin-next both execute
    # the real guarded action (lock, under-lock re-read, real transition).
    real_begin_next = run_action.persist_begin_next_cartridge

    # --- fixtures ------------------------------------------------------------
    magazine_path = tmp_path / "magazine.json"
    _write_magazine(
        magazine_path,
        {
            "schema_version": "1.0",
            "cartridges": [
                {"id": "cart-a", "job_type": "research", "prompt": PROMPT_A},
                {"id": "cart-b", "job_type": "research", "prompt": PROMPT_B},
                {"id": "cart-c", "job_type": "research", "prompt": PROMPT_C},
            ],
        },
    )

    state_path = str(tmp_path / "state.json")
    lock_path = tmp_path / "run.lock"
    owner_id = "owner-3"

    # Already RUNNING/current (run_all_cartridges precondition).
    initial = {
        "done": ["cart-a"],
        "current": "cart-b",
        "todo": ["cart-c"],
        "status": STATUS_RUNNING,
        "revision": 1,
    }
    state_store.save_run_state(state_path, initial)

    # No lock file before the call.
    assert not Path(lock_path).exists()

    # External dispatch: for cart-b's exact prompt it returns the distinct
    # stable module sentinel and performs no state mutation; any cart-c or
    # other dispatch fails the test.
    prompts: list[str] = []

    def _fake_dispatch(prompt: str, model_id: str):
        prompts.append(prompt)
        if prompt == PROMPT_B:
            return SENTINEL_B
        raise AssertionError(
            f"external dispatch received an unexpected prompt: {prompt!r}"
        )

    mock_dispatch = AsyncMock(side_effect=_fake_dispatch)

    # Expected states, for the exact-equality checks.
    expected_ready = {
        "done": ["cart-a", "cart-b"],
        "current": None,
        "todo": ["cart-c"],
        "status": STATUS_READY,
        "revision": 2,
    }
    expected_competing = {
        "done": ["cart-a", "cart-b"],
        "current": "cart-c",
        "todo": [],
        "status": STATUS_RUNNING,
        "revision": 3,
    }

    # Facts the wrapper records for the assertions below.
    observed_after_completion: list[dict] = []
    competitor_result: list[dict] = []
    bytes_after_competing_begin: list[bytes] = []
    lock_released_after_competing: list[bool] = []

    # Injection wrapper: replaces ONLY the loop's begin-next reference.  It
    # (1) verifies the real completion produced the exact READY state at
    # revision 2, (2) runs the COMPETING real guarded begin-next under owner
    # "other-owner", (3) captures the persisted bytes and confirms the real
    # lock is released, then (4) delegates the loop's own call to the SAME
    # REAL function with the loop's original state_path/lock_path/owner_id.
    # That under-lock re-read now sees RUNNING/current=cart-c and the real
    # begin_next_cartridge raises RunTransitionError.  The wrapper does NOT
    # catch it: it propagates out of run_all_cartridges unchanged.
    def _begin_next_wrapper(state_path: str, lock_path: str, owner_id: str):
        # 1. Immediately after cart-b completion: the exact READY state at rev 2.
        state = state_store.load_run_state(state_path)
        observed_after_completion.append(state)
        assert state == expected_ready, f"unexpected state after cart-b completion: {state!r}"

        # 2. Simulate another process beginning cart-c with the SAME REAL
        #    guarded action, a different owner.  It must produce exactly the
        #    competing RUNNING state at revision 3.
        result = real_begin_next(state_path, lock_path, "other-owner")
        competitor_result.append(result)
        assert result == expected_competing, f"competing begin-next produced an unexpected state: {result!r}"

        # 3. Capture the exact persisted bytes after the competing transition.
        bytes_after_competing_begin.append(Path(state_path).read_bytes())

        # 4. The competing real call used the real lock and released it.
        lock_released_after_competing.append(not Path(lock_path).exists())
        assert not Path(lock_path).exists(), "run lock still held after competing begin-next"

        # 5. Our loop's attempted begin-next — the real guarded action with the
        #    loop's original args.  The under-lock re-read sees
        #    RUNNING/current=cart-c, so begin_next_cartridge raises
        #    RunTransitionError; not caught here, so it propagates.
        return real_begin_next(state_path, lock_path, owner_id)

    # --- act: dispatch cart-b, complete it, then race the begin-next step -----
    with pytest.raises(RunTransitionError) as exc_info:
        with patch(
            "magazine.run_loop.run_action.persist_begin_next_cartridge",
            side_effect=_begin_next_wrapper,
        ) as mock_begin_next:
            asyncio.run(
                run_loop.run_all_cartridges(
                    magazine_path=magazine_path,
                    state_path=state_path,
                    lock_path=str(lock_path),
                    owner_id=owner_id,
                    base_url=BASE_URL,
                    api_key=API_KEY,
                    tool_role="terminal",
                    model_map={"research": "m1"},
                    tool_map={"terminal": "bash"},
                    dispatch=mock_dispatch,
                )
            )

    # --- 1. cart-b was externally dispatched exactly once --------------------
    assert mock_dispatch.await_count == 1
    mock_dispatch.assert_awaited_once_with(PROMPT_B, "m1")

    # --- 2/3. cart-b was genuinely completed by the real completion path; the
    #     wrapper observed the exact READY state at revision 2 immediately
    #     after completion -----------------------------------------------------
    assert len(observed_after_completion) == 1
    assert observed_after_completion[0] == expected_ready

    # --- 4. the competing REAL begin-next transitioned that READY state to
    #     exactly the competing RUNNING/current=cart-c state at revision 3 ----
    assert len(competitor_result) == 1
    assert competitor_result[0] == expected_competing

    # --- 5. the competing begin-next used the real lock and released it -------
    assert lock_released_after_competing == [True]

    # --- 6. our loop attempted begin-next exactly once (one call to the loop's
    #     patched reference; the two REAL calls ran inside the wrapper) --------
    assert mock_begin_next.call_count == 1

    # --- 7/8. the loop's real guarded begin-next raised RunTransitionError
    #     (under-lock re-read saw RUNNING, not READY) and it propagated from
    #     run_all_cartridges ------------------------------------------------------
    assert isinstance(exc_info.value, RunTransitionError)
    assert "'RUNNING'" in str(exc_info.value)

    # --- 9/10. cart-c was NEVER externally dispatched; no second dispatch ----
    assert PROMPT_C not in prompts
    assert len(prompts) == 1
    assert len(set(prompts)) == 1

    # --- 11. persisted bytes are byte-identical to the post-compete capture ---
    assert Path(state_path).read_bytes() == bytes_after_competing_begin[0]

    # --- 12. final persisted state is exactly the competing RUNNING state -----
    loaded = state_store.load_run_state(state_path)
    assert loaded == {
        "done": ["cart-a", "cart-b"],
        "current": "cart-c",
        "todo": [],
        "status": STATUS_RUNNING,
        "revision": 3,
    }

    # --- 13. cart-c is NOT in done -------------------------------------------
    assert "cart-c" not in loaded["done"]

    # --- 14. revision remains 3 and current remains cart-c ---------------------
    assert loaded["revision"] == 3
    assert loaded["current"] == "cart-c"

    # --- 15. no run lock file remains after the failure -----------------------
    assert not Path(lock_path).exists()


@patch("magazine.runtime_dispatch.validate_live_runtime_pair")
def test_run_all_cartridges_final_cartridge_returns_completed_without_begin_next(
    mock_preflight,
    tmp_path: Path,
) -> None:
    """Final-cartridge stop: ``run_all_cartridges`` completes the last cartridge,
    returns ``COMPLETED`` with the final state, and never attempts ``begin-next``.

    Sequence proven:

        RUNNING / current=cart-c / revision 3 / todo=[]
          -> external dispatch of cart-c succeeds (real dispatch path; exact
             prompt; distinct sentinel; no state mutation)
          -> real completion of cart-c via the full guarded completion boundary
             (lock acquire, under-lock re-read, identity check, RUNNING ->
             COMPLETED transition, revision 3 -> 4, atomic save, lock release)
          -> COMPLETED / current=None / todo=[] / revision 4
          -> run_all_cartridges returns MultiCartridgeResult
          -> NO begin-next attempt
          -> NO further dispatch

    What runs for real (no mocking of these):
      * ``magazine.run_loop.run_all_cartridges``
      * ``magazine.orchestration.execute_one_cartridge``
      * ``magazine.execute.dispatch_current_cartridge``
      * the runtime routing + preflight composition
        (``magazine.runtime_dispatch``)
      * ``magazine.complete.complete_current_cartridge``
      * ``magazine.run_action.persist_complete_current_cartridge``
        (lock acquire/release, under-lock state re-read, expected-current
         identity guard, RUNNING -> COMPLETED transition, revision increment,
         atomic save)
      * the real ``magazine.run_transition.complete_current_cartridge``
        (the pure transition: RUNNING -> COMPLETED when ``todo`` is empty)
      * the real ``magazine.state_store`` atomic persistence
      * the real ``magazine.run_lock`` behaviour

    Only three boundaries are faked:
      * ``magazine.runtime_dispatch.validate_live_runtime_pair`` — the live
        runtime preflight boundary (module-level ``@patch`` decorator, no-op)
      * the external ``dispatch`` callback (``AsyncMock`` with ``side_effect``;
        returns a distinct successful sentinel for cart-c's exact prompt only,
        awaited exactly once, performs no state mutation)
      * ``magazine.run_loop.run_action.persist_begin_next_cartridge`` —
        patched ONLY as a call detector via context manager; it must never
        be called (the loop sees ``COMPLETED`` after the real completion and
        returns immediately without entering the ``begin-next`` branch)
    """
    # No live discovery needed: the runtime-pair preflight boundary is a no-op.
    mock_preflight.return_value = None

    # --- fixtures -----------------------------------------------------------
    magazine_path = tmp_path / "magazine.json"
    _write_magazine(
        magazine_path,
        {
            "schema_version": "1.0",
            "cartridges": [
                {"id": "cart-a", "job_type": "research", "prompt": PROMPT_A},
                {"id": "cart-b", "job_type": "research", "prompt": PROMPT_B},
                {"id": "cart-c", "job_type": "research", "prompt": PROMPT_C},
            ],
        },
    )

    state_path = str(tmp_path / "state.json")
    lock_path = tmp_path / "run.lock"
    owner_id = "owner-4"

    # Already RUNNING/current — cart-c is the FINAL cartridge:
    #   done: [cart-a, cart-b], current: cart-c, todo: [], revision 3.
    initial = {
        "done": ["cart-a", "cart-b"],
        "current": "cart-c",
        "todo": [],
        "status": STATUS_RUNNING,
        "revision": 3,
    }
    state_store.save_run_state(state_path, initial)

    # No lock file before the call.
    assert not Path(lock_path).exists()

    # External dispatch callback: records every prompt it receives (in exact
    # call order) and returns the distinct successful sentinel for cart-c's
    # exact prompt.  Any other prompt or a second call fails the test.
    prompts: list[str] = []

    def _fake_dispatch(prompt: str, model_id: str):
        prompts.append(prompt)
        if prompt == PROMPT_C:
            return SENTINEL_C
        raise AssertionError(
            f"external dispatch received an unexpected prompt: {prompt!r}"
        )

    mock_dispatch = AsyncMock(side_effect=_fake_dispatch)

    # Expected final state after completing the final cartridge:
    #   done: [cart-a, cart-b, cart-c] (cart-c appended)
    #   current: None (cleared by completion)
    #   todo: [] (was already empty; no next cartridge)
    #   status: COMPLETED (todo empty -> COMPLETED, not READY)
    #   revision: 4 (3 + 1 for the single completion transition)
    expected_final = {
        "done": ["cart-a", "cart-b", "cart-c"],
        "current": None,
        "todo": [],
        "status": STATUS_COMPLETED,
        "revision": 4,
    }

    # --- act: the loop must complete cart-c and return COMPLETED ------------
    with patch(
        "magazine.run_loop.run_action.persist_begin_next_cartridge",
    ) as mock_begin_next:
        result = asyncio.run(
            run_loop.run_all_cartridges(
                magazine_path=magazine_path,
                state_path=state_path,
                lock_path=str(lock_path),
                owner_id=owner_id,
                base_url=BASE_URL,
                api_key=API_KEY,
                tool_role="terminal",
                model_map={"research": "m1"},
                tool_map={"terminal": "bash"},
                dispatch=mock_dispatch,
            )
        )

    # --- 1. result is a run_loop.MultiCartridgeResult ----------------------
    assert isinstance(result, run_loop.MultiCartridgeResult)

    # --- 2. the external dispatch callback was awaited exactly once ----------
    assert mock_dispatch.await_count == 1

    # --- 3. its sole prompt was cart-c's exact prompt ------------------------
    assert prompts == [PROMPT_C]

    # --- 4. no retry or second dispatch occurred ----------------------------
    assert len(prompts) == 1
    assert len(set(prompts)) == 1

    # --- 5. cart-c was completed through the real completion path -----------
    #     Proven by the final state: cart-c appears in ``done``, ``current``
    #     is ``None``, status is ``COMPLETED``, and revision advanced by
    #     exactly one — all effects of the REAL
    #     ``persist_complete_current_cartridge`` +
    #     ``run_transition.complete_current_cartridge`` (not mocked).
    assert "cart-c" in result.final_state["done"]
    assert result.final_state["current"] is None
    assert result.final_state["status"] == STATUS_COMPLETED

    # --- 6. result.final_state is exactly the expected COMPLETED state ------
    assert result.final_state == expected_final

    # --- 7. the persisted state exactly equals result.final_state ------------
    loaded = state_store.load_run_state(state_path)
    assert loaded == expected_final
    assert loaded == result.final_state

    # --- 8. persisted status is COMPLETED -------------------------------------
    assert loaded["status"] == STATUS_COMPLETED

    # --- 9. current is None --------------------------------------------------
    assert loaded["current"] is None

    # --- 10. todo is empty ----------------------------------------------------
    assert loaded["todo"] == []

    # --- 11. done ordering is exactly ["cart-a", "cart-b", "cart-c"] ---------
    assert loaded["done"] == ["cart-a", "cart-b", "cart-c"]

    # --- 12. revision advanced exactly once: 3 -> 4 --------------------------
    assert loaded["revision"] == 4

    # --- 13. begin-next was never attempted ----------------------------------
    #     The loop sees COMPLETED (not READY) after the real completion and
    #     returns immediately; the ``begin-next`` branch is never entered.
    mock_begin_next.assert_not_called()

    # --- 14. no run lock file remains -----------------------------------------
    #     The real completion acquires and releases the lock; the loop holds
    #     no lock of its own, so nothing is left behind.
    assert not Path(lock_path).exists()

    # --- 15. the loop returned immediately after final completion ------------
    #     * exactly one external dispatch (await_count == 1 above)
    #     * no begin-next attempt (assert_not_called above)
    #     * no further state change: revision is 4, not 5, and the persisted
    #       state is the final COMPLETED state with no pending work
    assert mock_dispatch.await_count == 1
    assert loaded["revision"] == 4
    assert loaded["todo"] == []
    assert loaded["status"] == STATUS_COMPLETED
