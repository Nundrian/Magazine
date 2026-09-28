"""Focused tests for the magazine.async_runner composition layer.

Six composition tests for ``magazine.async_runner.run_magazine`` (mocking
only the two direct dependency boundaries) plus one real-component
concurrency integration test (the final test) that proves two normal async
runners cannot dispatch the same run.

Only the two direct dependency boundaries are mocked:

  * ``magazine.run_preparation.prepare_run`` (sync; returns a
    :class:`magazine.run_preparation.PreparedRun`)
  * ``magazine.run_loop.run_all_cartridges`` (async; returns a
    :class:`magazine.run_loop.MultiCartridgeResult` — the existing
    run-loop result type)

Nothing else is faked: the ``run_magazine`` body (status branch, the single
``prepare_run`` call, the argument forwarding), the ``PreparedRun`` /
``MultiCartridgeResult`` dataclasses, the ``magazine.state`` status constants,
and ``asyncio`` machinery.  ``run_magazine`` performs no other external action,
so the call counts on these two boundaries are a complete proof of the
composition contract:

  * RUNNING      -> ``run_all_cartridges`` awaited exactly once with the exact
                    argument sequence; its exact ``final_state`` object
                    returned
  * COMPLETED    -> admitted terminal state returned, zero dispatch
  * HALTED       -> admitted terminal state returned, zero dispatch
  * unexpected   -> ``RuntimeError`` identifying the status, zero dispatch
  * ``prepare_run`` / ``run_all_cartridges`` exceptions propagate unchanged
                    (identity-checked, not translated or swallowed)

Out of scope for this module: CLI integration, retries/recovery, and any
additional runner API. The concurrency test still patches the live-runtime
preflight boundary so it performs no live API discovery.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from magazine.run_action import RunAdmissionError
from magazine.run_lock import MagazineLockError
from magazine import run_loop
from magazine import run_preparation
from magazine.async_runner import run_magazine
from magazine.state import (
    STATUS_COMPLETED,
    STATUS_HALTED,
    STATUS_READY,
    STATUS_RUNNING,
)

# --- fixed composition fixtures (only these are handed to run_magazine) -------
MAGAZINE_PATH = "/tmp/magazine.json"
STATE_DIR = "/tmp/state-dir"
OWNER_ID = "owner-async-1"
BASE_URL = "http://localhost:8080"
API_KEY = "test-key"
TOOL_ROLE = "terminal"
MODEL_MAP = {"research": "m1"}
TOOL_MAP = {"terminal": "bash"}
# A distinct external dispatch callback; the runner must forward it
# unchanged (identity is checked in the delegation test).
DISPATCH = AsyncMock(name="external-dispatch")


def _call_run_magazine():
    """Await ``run_magazine`` with the fixed fixtures above.

    Because only ``prepare_run`` and ``run_all_cartridges`` are faked, every
    external action ``run_magazine`` performs is observable through exactly
    those two mocks.
    """
    return asyncio.run(
        run_magazine(
            magazine_path=MAGAZINE_PATH,
            state_dir=STATE_DIR,
            owner_id=OWNER_ID,
            base_url=BASE_URL,
            api_key=API_KEY,
            tool_role=TOOL_ROLE,
            model_map=MODEL_MAP,
            tool_map=TOOL_MAP,
            dispatch=DISPATCH,
        )
    )


@patch("magazine.async_runner.run_preparation.prepare_run")
@patch(
    "magazine.async_runner.run_loop.run_all_cartridges",
    new_callable=AsyncMock,
)
def test_running_delegates_exactly_once(mock_loop, mock_prepare) -> None:
    """A RUNNING admission is handed to the existing run loop exactly once
    and its exact ``final_state`` object is returned.

    ``prepare_run`` is mocked to return a distinctive ``PreparedRun`` (RUNNING
    state, ``state_path == "/tmp/exact-state.json"`` and
    ``lock_path == "/tmp/exact-lock"``).  ``run_all_cartridges`` is mocked to
    return one real ``MultiCartridgeResult`` wrapping a unique ``final_state``
    dict object.

    Verified:

      1. ``prepare_run`` called exactly once with ``magazine_path`` /
         ``state_dir`` / ``owner_id``.
      2. ``run_all_cartridges`` awaited exactly once.
      3. Its arguments are exactly ``magazine_path`` / ``prepared.state_path``
         / ``prepared.lock_path`` / ``owner_id`` / ``base_url`` / ``api_key``
         / ``tool_role`` / ``model_map`` / ``tool_map`` / ``dispatch``;
         ``model_map``, ``tool_map`` and ``dispatch`` are passed by identity.
      4. ``run_magazine`` returns exactly the ``final_state`` object itself
         (identity, not merely equality).
    """
    admitted_state = {
        "done": [],
        "current": "cart-a",
        "todo": ["cart-b"],
        "status": STATUS_RUNNING,
        "revision": 1,
    }
    mock_prepare.return_value = run_preparation.PreparedRun(
        state=admitted_state,
        state_path="/tmp/exact-state.json",
        lock_path="/tmp/exact-lock",
    )

    final_state = {
        "done": ["cart-a", "cart-b"],
        "current": None,
        "todo": [],
        "status": STATUS_COMPLETED,
        "revision": 4,
    }
    # A real run-loop result type carrying one unique final-state object.
    mock_loop.return_value = run_loop.MultiCartridgeResult(final_state=final_state)

    result = _call_run_magazine()

    # --- 1. prepare_run called exactly once with the three preparation args --
    mock_prepare.assert_called_once_with(MAGAZINE_PATH, STATE_DIR, OWNER_ID)

    # --- 2. run_all_cartridges awaited exactly once --------------------------
    mock_loop.assert_awaited_once()

    # --- 3. exact argument sequence, once-derived paths, positional order ---
    mock_loop.assert_awaited_once_with(
        MAGAZINE_PATH,
        "/tmp/exact-state.json",
        "/tmp/exact-lock",
        OWNER_ID,
        BASE_URL,
        API_KEY,
        TOOL_ROLE,
        MODEL_MAP,
        TOOL_MAP,
        DISPATCH,
    )
    # Identity is preserved for the mutable and callback arguments (equality
    # alone would also pass; identity proves nothing was copied).
    loop_args = mock_loop.call_args_list[0].args
    assert loop_args[7] is MODEL_MAP
    assert loop_args[8] is TOOL_MAP
    assert loop_args[9] is DISPATCH

    # --- 4. returned object IS the unique final_state dict, not a copy -------
    assert result is final_state


@patch("magazine.async_runner.run_preparation.prepare_run")
@patch(
    "magazine.async_runner.run_loop.run_all_cartridges",
    new_callable=AsyncMock,
)
def test_completed_bypasses_execution(mock_loop, mock_prepare) -> None:
    """An already-COMPLETED run returns its admitted state with zero dispatch.

    ``run_all_cartridges`` is configured to raise if ever awaited, so any
    execution attempt would fail the test.  Verified:

      1. ``prepare_run`` called exactly once.
      2. ``run_all_cartridges`` never called.
      3. The returned object IS ``prepared.state`` (identity).
      4. No state reload or additional action is performed by ``async_runner``:
         ``prepare_run`` and ``run_all_cartridges`` are the module's only
         external actions, so the two mocked call counts fully cover it
         (no reload or save path exists in ``run_magazine`` itself).
    """
    completed_state = {
        "done": ["cart-a", "cart-b"],
        "current": None,
        "todo": [],
        "status": STATUS_COMPLETED,
        "revision": 4,
    }
    mock_prepare.return_value = run_preparation.PreparedRun(
        state=completed_state,
        state_path="/tmp/exact-state.json",
        lock_path="/tmp/exact-lock",
    )
    # Any attempt to execute the loop fails the test.
    mock_loop.side_effect = RuntimeError(
        "run_all_cartridges must never be called for a COMPLETED run"
    )

    result = _call_run_magazine()

    # --- 1. prepare_run called exactly once -----------------------------------
    mock_prepare.assert_called_once_with(MAGAZINE_PATH, STATE_DIR, OWNER_ID)

    # --- 2. run_all_cartridges never awaited ------------------------------------
    mock_loop.assert_not_awaited()

    # --- 3. returned object IS prepared.state (identity) -----------------------
    assert result is completed_state


@patch("magazine.async_runner.run_preparation.prepare_run")
@patch(
    "magazine.async_runner.run_loop.run_all_cartridges",
    new_callable=AsyncMock,
)
def test_halted_bypasses_execution(mock_loop, mock_prepare) -> None:
    """An already-HALTED run returns its admitted state with zero dispatch.

    ``prepare_run`` is mocked to return a valid HALTED state (HALTED may
    preserve a ``current`` cartridge and remaining ``todo``).  ``run_all_cartridges``
    is configured to raise if ever awaited.  Verified:

      1. ``prepare_run`` called exactly once.
      2. ``run_all_cartridges`` never called.
      3. The returned object IS ``prepared.state`` (identity).
    """
    halted_state = {
        "done": ["cart-a"],
        "current": "cart-b",
        "todo": ["cart-c"],
        "status": STATUS_HALTED,
        "revision": 3,
    }
    mock_prepare.return_value = run_preparation.PreparedRun(
        state=halted_state,
        state_path="/tmp/exact-state.json",
        lock_path="/tmp/exact-lock",
    )
    # Any attempt to execute the loop fails the test.
    mock_loop.side_effect = RuntimeError(
        "run_all_cartridges must never be called for a HALTED run"
    )

    result = _call_run_magazine()

    # --- 1. prepare_run called exactly once -----------------------------------
    mock_prepare.assert_called_once_with(MAGAZINE_PATH, STATE_DIR, OWNER_ID)

    # --- 2. run_all_cartridges never awaited ------------------------------------
    mock_loop.assert_not_awaited()

    # --- 3. returned object IS prepared.state (identity) -----------------------
    assert result is halted_state


@patch("magazine.async_runner.run_preparation.prepare_run")
@patch(
    "magazine.async_runner.run_loop.run_all_cartridges",
    new_callable=AsyncMock,
)
def test_unexpected_ready_status_fails_without_dispatch(mock_loop, mock_prepare) -> None:
    """A READY status — not normally producible by ``prepare_run`` — fails inside
    ``run_magazine`` itself without any dispatch.

    This is a composition-boundary test: ``prepare_run`` is mocked to hand
    back a raw (not state-validated) dict whose status is ``READY``, so the
    failure path is proven in ``async_runner``, not in the preparation layer.
    Verified:

      1. ``run_magazine`` raises ``RuntimeError``.
      2. The message identifies the unexpected ``READY`` status.
      3. ``run_all_cartridges`` is never called.
    """
    raw_state = {
        "done": [],
        "current": None,
        "todo": ["cart-a"],
        "status": STATUS_READY,
        "revision": 0,
    }
    mock_prepare.return_value = run_preparation.PreparedRun(
        state=raw_state,
        state_path="/tmp/exact-state.json",
        lock_path="/tmp/exact-lock",
    )
    # If the loop ran, the test fails.
    mock_loop.side_effect = RuntimeError(
        "run_all_cartridges must never be called for an unexpected status"
    )

    with pytest.raises(RuntimeError, match="unexpected run status 'READY'") as exc_info:
        _call_run_magazine()

    # --- 1/2. RuntimeError whose message identifies the unexpected status ------
    assert isinstance(exc_info.value, RuntimeError)
    assert "READY" in str(exc_info.value)

    # --- 3. no dispatch happened ----------------------------------------------
    mock_loop.assert_not_awaited()


@patch("magazine.async_runner.run_preparation.prepare_run")
@patch(
    "magazine.async_runner.run_loop.run_all_cartridges",
    new_callable=AsyncMock,
)
def test_preparation_exception_propagates_unchanged(mock_loop, mock_prepare) -> None:
    """A unique exception instance raised by ``prepare_run`` propagates
    unchanged through ``run_magazine``.

    One unique exception instance is raised by the mocked preparation.
    Verified:

      1. ``run_magazine`` raises that exact object (identity-checked).
      2. ``run_all_cartridges`` is never called.

    No specific exception class is required — ``RunAdmissionError`` behaviour
    is owned and already tested by its own layer; this proves only that
    ``async_runner`` does not translate or swallow preparation/admission
    errors.
    """
    # One unique exception instance (a plain RuntimeError stands in).
    error = RuntimeError("prepare sentinel")
    mock_prepare.side_effect = error
    # If the loop ran, the test fails.
    mock_loop.side_effect = RuntimeError(
        "run_all_cartridges must never be reached after a preparation failure"
    )

    with pytest.raises(RuntimeError) as exc_info:
        _call_run_magazine()

    # --- 1. the same object propagates (identity, not just type/message) ------
    assert exc_info.value is error

    # --- 2. run_all_cartridges never awaited -------------------------------------
    mock_loop.assert_not_awaited()


@patch("magazine.async_runner.run_preparation.prepare_run")
@patch(
    "magazine.async_runner.run_loop.run_all_cartridges",
    new_callable=AsyncMock,
)
def test_run_loop_exception_propagates_unchanged(mock_loop, mock_prepare) -> None:
    """A unique exception instance raised by ``run_all_cartridges`` propagates
    unchanged through ``run_magazine``.

    ``prepare_run`` returns a RUNNING ``PreparedRun`` (so the loop branch is
    active), and the mocked loop raises one unique exception instance.
    Verified:

      1. ``run_magazine`` raises that exact object (identity-checked).
      2. ``prepare_run`` was called exactly once.
      3. ``run_all_cartridges`` was awaited exactly once.

    This proves dispatch/completion/begin-next/loop failures are not translated
    or swallowed by the composition layer.
    """
    admitted_state = {
        "done": [],
        "current": "cart-a",
        "todo": ["cart-b"],
        "status": STATUS_RUNNING,
        "revision": 1,
    }
    mock_prepare.return_value = run_preparation.PreparedRun(
        state=admitted_state,
        state_path="/tmp/exact-state.json",
        lock_path="/tmp/exact-lock",
    )

    # One unique exception instance (a plain RuntimeError stands in).
    error = RuntimeError("loop sentinel")
    mock_loop.side_effect = error

    with pytest.raises(RuntimeError) as exc_info:
        _call_run_magazine()

    # --- 1. the same object propagates (identity, not just type/message) ------
    assert exc_info.value is error

    # --- 2. prepare_run called exactly once -----------------------------------
    mock_prepare.assert_called_once_with(MAGAZINE_PATH, STATE_DIR, OWNER_ID)

    # --- 3. run_all_cartridges awaited exactly once (then raised) ----------------
    mock_loop.assert_awaited_once()

# --- 7. Two REAL runners cannot dispatch the same run -------------------------
@patch("magazine.runtime_dispatch.validate_live_runtime_pair")
def test_two_normal_runners_cannot_dispatch_same_run(
    mock_preflight, tmp_path
) -> None:
    """Two REAL runners cannot dispatch the same run.

    A real valid two-cartridge ``Magazine`` is written to disk.  Both
    ``magazine.run_preparation.prepare_run`` and ``magazine.run_loop.run_all_cartridges``
    run with their REAL bodies (nothing mocked); the only environment-boundary
    patch is the live-runtime preflight (``magazine.runtime_dispatch.validate_live_runtime_pair``
    -> no-op), so no live API discovery is performed.

    Deterministic ``asyncio.Event`` synchronisation only — no ``sleep``.

    Verified:

      1. Runner A performs real admission, reaches the external dispatch and
         blocks there.  The admitted ``RUNNING`` state (revision 1) is persisted
         and the admission lock is released (not held during dispatch).
      2. While A is blocked, Runner B is admitted by the REAL ``prepare_run``.
         It sees the persisted ``RUNNING`` state and raises
         ``magazine.run_action.RunAdmissionError`` (not ``MagazineLockError``)
         without ever reaching its external dispatch.
      3. The persisted state is byte-identical after B's rejection and still
         exactly the admitted ``RUNNING`` state; no lock remains.
      4. A is released and completes: its callback received exactly
         ``["prompt for cart-a", "prompt for cart-b"]`` in order, B's callback
         received an empty list, A's returned final state is
         ``{"done": ["cart-a", "cart-b"], "current": None, "todo": [],
         "status": "COMPLETED", "revision": 4}``, the persisted state equals
         it, and no lock file remains.
    """
    # No-op live preflight: this test performs no live API discovery.
    mock_preflight.return_value = None

    # A real valid two-cartridge Magazine, written to a fresh test dir.
    magazine_path = str(tmp_path / "magazine.json")
    Path(magazine_path).write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "cartridges": [
                    {
                        "id": "cart-a",
                        "job_type": "research",
                        "prompt": "prompt for cart-a",
                    },
                    {
                        "id": "cart-b",
                        "job_type": "research",
                        "prompt": "prompt for cart-b",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    state_dir = str(tmp_path / "state-dir")
    state_path = str(Path(state_dir) / "run" / "state.json")
    lock_path = str(Path(state_dir) / "run" / ".lock")

    async def main() -> object:
        # Events and dispatch callbacks are created inside the running loop.
        first_dispatch_entered = asyncio.Event()
        release_first_dispatch = asyncio.Event()
        a_prompts: list = []
        b_prompts: list = []

        # A's external dispatch: blocks on the FIRST call (cart-a) only.
        async def _dispatch_a(prompt: str, model_id: str) -> object:
            a_prompts.append(prompt)
            if len(a_prompts) == 1:  # first invocation only
                first_dispatch_entered.set()
                await release_first_dispatch.wait()
            return "done"

        # B's external dispatch: must never be reached; failing loudly.
        async def _dispatch_b(prompt: str, model_id: str) -> object:
            b_prompts.append(prompt)
            raise AssertionError(
                f"runner B external dispatch was invoked: {prompt!r}"
            )

        # Start runner A as a task (real preparation + real loop).
        task_a = asyncio.create_task(
            run_magazine(
                magazine_path=magazine_path,
                state_dir=state_dir,
                owner_id="owner-a",
                base_url=BASE_URL,
                api_key=API_KEY,
                tool_role=TOOL_ROLE,
                model_map=MODEL_MAP,
                tool_map=TOOL_MAP,
                dispatch=_dispatch_a,
            )
        )

        admitted_state = {
            "done": [],
            "current": "cart-a",
            "todo": ["cart-b"],
            "status": STATUS_RUNNING,
            "revision": 1,
        }
        expected_a = {
            "done": ["cart-a", "cart-b"],
            "current": None,
            "todo": [],
            "status": STATUS_COMPLETED,
            "revision": 4,
        }

        try:
            # --- A is admitted, then blocked at the external dispatch boundary.
            await asyncio.wait_for(first_dispatch_entered.wait(), timeout=10.0)
            assert a_prompts == ["prompt for cart-a"], a_prompts
            # The admitted RUNNING state (rev 1) is persisted; lock released.
            assert json.loads(Path(state_path).read_bytes()) == admitted_state
            assert not Path(lock_path).exists()
            bytes_before_b = Path(state_path).read_bytes()

            # --- Runner B: real admission rejects the persisted RUNNING state.
            with pytest.raises(RunAdmissionError) as exc_info:
                await run_magazine(
                    magazine_path=magazine_path,
                    state_dir=state_dir,
                    owner_id="owner-b",
                    base_url=BASE_URL,
                    api_key=API_KEY,
                    tool_role=TOOL_ROLE,
                    model_map=MODEL_MAP,
                    tool_map=TOOL_MAP,
                    dispatch=_dispatch_b,
                )
            assert not isinstance(exc_info.value, MagazineLockError)
            assert "RUNNING" in str(exc_info.value)
            # B never reached external dispatch.
            assert b_prompts == [], f"runner B dispatch invoked: {b_prompts!r}"
            # Persisted state byte-identical; still the admitted RUNNING state;
            # no lock remains.
            assert Path(state_path).read_bytes() == bytes_before_b
            assert json.loads(Path(state_path).read_bytes()) == admitted_state
            assert not Path(lock_path).exists()

            # --- Release runner A; it must now finish the run.
            release_first_dispatch.set()
            result_a = await asyncio.wait_for(task_a, timeout=10.0)

            # --- A completed the run.
            assert a_prompts == ["prompt for cart-a", "prompt for cart-b"]
            assert b_prompts == [], f"runner B dispatch invoked: {b_prompts!r}"
            assert result_a == expected_a, result_a
            # Persisted state.json at completion equals A's returned final state.
            assert json.loads(Path(state_path).read_bytes()) == expected_a
            # No lock file remains.
            assert not Path(lock_path).exists()
        finally:
            # Never leave runner A suspended on release_first_dispatch.
            release_first_dispatch.set()
            if not task_a.done():
                task_a.cancel()
                try:
                    await task_a
                except BaseException:
                    # The real failure was raised earlier; do not mask it.
                    pass

    asyncio.run(main())
