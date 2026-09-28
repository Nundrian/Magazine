"""Focused tests for the Open WebUI composition entry
``magazine.async_runner.run_magazine_open_webui``.

Only the three direct dependency boundaries of that composition function are
mocked:

  * ``magazine.async_runner.make_open_webui_dispatch`` (sync factory)
  * ``magazine.async_runner.run_preparation.prepare_run``
    (sync; returns :class:`PreparedRun`)
  * ``magazine.async_runner.run_loop.run_all_cartridges``
    (async; returns :class:`MultiCartridgeResult`)

Nothing else is faked: the ``run_magazine_open_webui`` body (one factory call
+ one ``run_magazine`` delegation + return), ``run_magazine``'s status branch
(it runs with its REAL body), the ``PreparedRun`` / ``MultiCartridgeResult``
dataclasses, and the status constants.  Because the composition function
performs exactly two external actions — (a) create the Open WebUI dispatch,
(b) await ``run_magazine`` — the call counts and identity checks on those
three boundaries fully cover the contract:

  * :func:`make_open_webui_dispatch` is called exactly once with the supplied
    ``base_url`` and ``api_key``;
  * the dispatch object returned by that factory is the very object ``run_magazine``
    forwards to the run loop (identity-checked), i.e. the resulting dispatch
    is what is passed into ``run_magazine``;
  * the final result is the ``run_magazine`` ``final_state`` object itself
    (identity, unchanged);
  * factory and run-loop exceptions propagate unchanged.
"""

from __future__ import annotations

import asyncio

from unittest.mock import AsyncMock, patch

import pytest

from magazine import run_loop
from magazine import run_preparation
from magazine.async_runner import run_magazine_open_webui
from magazine.state import STATUS_COMPLETED, STATUS_RUNNING

# --- fixed composition fixtures (only these reach the new function) ------------
MAGAZINE_PATH = "/tmp/magazine.json"
STATE_DIR = "/tmp/state-dir"
OWNER_ID = "owner-open-webui-1"
BASE_URL = "http://localhost:8080"
API_KEY = "test-key"
TOOL_ROLE = "terminal"
MODEL_MAP = {"research": "m1"}
TOOL_MAP = {"terminal": "bash"}


def _call_run_open_webui():
    """Await the composition function with the fixed fixtures above."""
    return asyncio.run(
        run_magazine_open_webui(
            magazine_path=MAGAZINE_PATH,
            state_dir=STATE_DIR,
            owner_id=OWNER_ID,
            base_url=BASE_URL,
            api_key=API_KEY,
            tool_role=TOOL_ROLE,
            model_map=MODEL_MAP,
            tool_map=TOOL_MAP,
        )
    )


def _admitted_running_state() -> dict:
    return {
        "done": [],
        "current": "cart-a",
        "todo": ["cart-b"],
        "status": STATUS_RUNNING,
        "revision": 1,
    }


def _completed_final_state() -> dict:
    return {
        "done": ["cart-a", "cart-b"],
        "current": None,
        "todo": [],
        "status": STATUS_COMPLETED,
        "revision": 4,
    }


@patch("magazine.async_runner.run_loop.run_all_cartridges", new_callable=AsyncMock)
@patch("magazine.async_runner.run_preparation.prepare_run")
@patch("magazine.async_runner.make_open_webui_dispatch")
def test_factory_receives_supplied_credentials(mock_factory, mock_prepare, mock_loop):
    """The Open WebUI dispatch factory is called exactly once with the supplied
    ``base_url`` and ``api_key`` (in order), and the run proceeds from that."""
    sentinel = object()
    mock_factory.return_value = sentinel
    mock_prepare.return_value = run_preparation.PreparedRun(
        state=_admitted_running_state(),
        state_path="/tmp/exact-state.json",
        lock_path="/tmp/exact-lock",
    )
    final_state = _completed_final_state()
    mock_loop.return_value = run_loop.MultiCartridgeResult(final_state=final_state)

    result = _call_run_open_webui()

    # --- factory called once, with exactly the supplied base_url and api_key ---
    mock_factory.assert_called_once()
    mock_factory.assert_called_once_with(BASE_URL, API_KEY)
    # --- and the run actually proceeded (RUNNING -> run loop delegated) -------
    mock_loop.assert_awaited_once()
    mock_prepare.assert_called_once_with(MAGAZINE_PATH, STATE_DIR, OWNER_ID)
    assert result is final_state


@patch("magazine.async_runner.run_loop.run_all_cartridges", new_callable=AsyncMock)
@patch("magazine.async_runner.run_preparation.prepare_run")
@patch("magazine.async_runner.make_open_webui_dispatch")
def test_factory_dispatch_forwarded_into_run_magazine(mock_factory, mock_prepare, mock_loop):
    """The dispatch object returned by the factory is the very object that
    reaches the run loop (identity-checked): the resulting dispatch is what
    ``run_magazine`` forwards unchanged to the external dispatch path, alongside
    the normal run inputs."""
    dispatch = object()  # the unique dispatch the factory will hand back
    mock_factory.return_value = dispatch
    mock_prepare.return_value = run_preparation.PreparedRun(
        state=_admitted_running_state(),
        state_path="/tmp/exact-state.json",
        lock_path="/tmp/exact-lock",
    )
    mock_loop.return_value = run_loop.MultiCartridgeResult(
        final_state=_completed_final_state()
    )

    _call_run_open_webui()

    # --- run_all_cartridges got the factory's dispatch object (identity) --------
    mock_loop.assert_awaited_once()
    loop_args = mock_loop.call_args_list[0].args
    assert loop_args[9] is dispatch
    # --- the full normal-input sequence was forwarded to the same single call --
    assert loop_args == (
        MAGAZINE_PATH,
        "/tmp/exact-state.json",
        "/tmp/exact-lock",
        OWNER_ID,
        BASE_URL,
        API_KEY,
        TOOL_ROLE,
        MODEL_MAP,
        TOOL_MAP,
        dispatch,
    )


@patch("magazine.async_runner.run_loop.run_all_cartridges", new_callable=AsyncMock)
@patch("magazine.async_runner.run_preparation.prepare_run")
@patch("magazine.async_runner.make_open_webui_dispatch")
def test_final_result_returned_unchanged(mock_factory, mock_prepare, mock_loop):
    """The returned object IS the run_magazine ``final_state`` dict (identity,
    not merely equality), so nothing is copied or wrapped in this layer."""
    mock_factory.return_value = object()
    mock_prepare.return_value = run_preparation.PreparedRun(
        state=_admitted_running_state(),
        state_path="/tmp/exact-state.json",
        lock_path="/tmp/exact-lock",
    )
    unique_final_state = {
        "done": ["cart-a", "cart-b"],
        "current": None,
        "todo": [],
        "status": STATUS_COMPLETED,
        "revision": 4,
    }
    mock_loop.return_value = run_loop.MultiCartridgeResult(
        final_state=unique_final_state
    )

    result = _call_run_open_webui()

    # --- identity: the same dict object, not a copy ---------------------------
    assert result is unique_final_state


@patch("magazine.async_runner.run_loop.run_all_cartridges", new_callable=AsyncMock)
@patch("magazine.async_runner.run_preparation.prepare_run")
@patch("magazine.async_runner.make_open_webui_dispatch")
def test_factory_exception_propagates_before_run(mock_factory, mock_prepare, mock_loop):
    """An unusable base_url/api_key fails in the factory (ValueError) before
    ``run_magazine`` is ever called; the error propagates unchanged and no
    preparation / dispatch happens."""
    error = ValueError("base_url must be a string, got <class 'int'>")
    mock_factory.side_effect = error
    # If preparation ever ran, the composition leaked past the failed factory.
    mock_prepare.side_effect = AssertionError("run_magazine preparation reached")

    with pytest.raises(ValueError) as exc_info:
        _call_run_open_webui()

    # --- the factory's error is what surfaced (identity) -----------------------
    assert exc_info.value is error
    # --- preparation and the loop were never reached ---------------------------
    mock_prepare.assert_not_called()
    mock_loop.assert_not_awaited()


@patch("magazine.async_runner.run_loop.run_all_cartridges", new_callable=AsyncMock)
@patch("magazine.async_runner.run_preparation.prepare_run")
@patch("magazine.async_runner.make_open_webui_dispatch")
def test_run_loop_exception_propagates_unchanged(mock_factory, mock_prepare, mock_loop):
    """A run-loop exception propagates unchanged through the composition layer
    (the dispatch was created and handed off, then the loop failed)."""
    mock_factory.return_value = object()
    mock_prepare.return_value = run_preparation.PreparedRun(
        state=_admitted_running_state(),
        state_path="/tmp/exact-state.json",
        lock_path="/tmp/exact-lock",
    )
    loop_error = RuntimeError("loop sentinel")
    mock_loop.side_effect = loop_error

    with pytest.raises(RuntimeError) as exc_info:
        _call_run_open_webui()

    # --- the same loop error object surfaced (identity) -----------------------
    assert exc_info.value is loop_error
    # --- factory ran once and the loop ran once before failing ------------------
    mock_factory.assert_called_once_with(BASE_URL, API_KEY)
    mock_loop.assert_awaited_once()
