"""Focused tests for magazine.execute (stage-2 composition).

Three tests:
  1. The successful path — a valid RUNNING state whose current cartridge is
     present in the Magazine document, with valid routing and a passing
     runtime preflight, dispatches the exact cartridge prompt once through
     the existing runtime dispatch and returns a CartridgeDispatch bundling
     the dispatched cartridge ID with the dispatch result, leaving the run
     untouched (state byte-identical, no lock file).
  2. Failure before dispatch — a missing current cartridge ID (ValueError)
     and a present cartridge whose job_type is absent from model_map
     (KeyError) both raise before the dispatch callback is awaited and
     leave the run untouched (state byte-identical, no lock file, no
     preflight, no HTTP).
  3. Runtime preflight failure — a valid RUNNING state, a present cartridge
     and successful routing, but a preflight that raises ValueError: the
     error is propagated, the final dispatch callback is never awaited, and
     the run stays untouched (state byte-identical, no lock file, no HTTP).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from unittest.mock import AsyncMock, patch

from magazine import execute


BASE_URL = "http://localhost:8080"
API_KEY = "test-key"
CURRENT_ID = "b"
CURRENT_PROMPT = "prompt for b"
MODEL_ID = "m1"
TOOL_ID = "bash"
MODEL_MAP = {"research": MODEL_ID}
TOOL_MAP = {"terminal": TOOL_ID}
TOOL_ROLE = "terminal"


def _write_magazine(path: Path, document: dict) -> None:
    path.write_text(json.dumps(document), encoding="utf-8")


def _write_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state), encoding="utf-8")


@patch("magazine.runtime_dispatch.validate_live_runtime_pair")
def test_successful_path_dispatches_exact_cartridge_prompt_once(
    mock_preflight,
    tmp_path: Path,
) -> None:
    """RUNNING + matching cartridge + valid routing + passing preflight
    dispatches the exact cartridge prompt and resolved model ID once, returns
    a CartridgeDispatch bundling the dispatched cartridge ID with the dispatch
    result, and leaves the persisted state and run lock untouched.

    The existing runtime dispatch is exercised for real: routing
    (magazine.routing) and the dispatch composition
    (magazine.runtime_dispatch) both run. Only the preflight
    (:func:`validate_live_runtime_pair`) and the final dispatch callback
    are stubbed, so no real Open WebUI / HTTP calls are made.  The external
    dispatch is invoked as ``dispatch(prompt, model_id)`` with the per-cartridge
    model resolved by routing.
    """
    # Preflight succeeds without any live discovery (no HTTP).
    mock_preflight.return_value = None

    # --- fixtures ---------------------------------------------------------
    magazine_path = tmp_path / "magazine.json"
    _write_magazine(
        magazine_path,
        {
            "schema_version": "1.0",
            "cartridges": [
                {"id": "a", "job_type": "research", "prompt": "prompt for a"},
                {"id": "b", "job_type": "research", "prompt": CURRENT_PROMPT},
                {"id": "c", "job_type": "research", "prompt": "prompt for c"},
            ],
        },
    )

    state_path = tmp_path / "state.json"
    initial_state: dict = {
        "done": ["a"],
        "current": CURRENT_ID,
        "todo": ["c"],
        "status": "RUNNING",
        "revision": 1,
    }
    _write_state(state_path, initial_state)
    original_bytes = state_path.read_bytes()

    sentinel = object()
    mock_dispatch = AsyncMock(return_value=sentinel)

    # --- act: dispatch the current cartridge -------------------------------
    result = asyncio.run(
        execute.dispatch_current_cartridge(
            magazine_path=str(magazine_path),
            state_path=str(state_path),
            base_url=BASE_URL,
            api_key=API_KEY,
            tool_role=TOOL_ROLE,
            model_map=MODEL_MAP,
            tool_map=TOOL_MAP,
            dispatch=mock_dispatch,
        )
    )

    # --- reaches the existing runtime dispatch composition --------------
    # Preflight ran with the exact model/tool IDs that routing resolved
    # from the cartridge's job_type and the supplied tool role.
    mock_preflight.assert_called_once_with(BASE_URL, API_KEY, MODEL_ID, TOOL_ID)

    # --- dispatches the exact cartridge prompt + resolved model ID once --
    mock_dispatch.assert_awaited_once_with(CURRENT_PROMPT, MODEL_ID)

    # --- returns a CartridgeDispatch bundling the dispatched cartridge ID
    #     with the exact runtime result object ---------------------------
    assert result.cartridge_id == CURRENT_ID
    assert result.dispatch_result is sentinel

    # --- leaves the persisted state byte-identical ----------------------
    assert state_path.read_bytes() == original_bytes

    # --- creates no lock file -------------------------------------------
    assert not (state_path.parent / ".lock").exists()


@patch("magazine.runtime_dispatch.validate_live_runtime_pair")
def test_failure_before_dispatch_raises_and_leaves_run_untouched(
    mock_preflight,
    tmp_path: Path,
) -> None:
    """A dispatch that fails before reaching the existing runtime dispatch
    leaves the run completely untouched.

    Two failure modes, both raised before the final dispatch callback is
    awaited:

    Case 1 — the current cartridge ID is absent from the Magazine document:
    dispatch_current_cartridge raises ValueError when it looks up the
    current cartridge.

    Case 2 — the current cartridge is present but its job_type is absent
    from model_map: routing.resolve_model raises KeyError inside
    runtime_dispatch.dispatch_cartridge, before dispatch is awaited.

    In both cases the persisted state document is byte-identical to what
    was written, no lock file is created, the dispatch callback is never
    awaited, the runtime preflight is never called, and no real Open WebUI
    / HTTP calls are made.
    """
    # --- case 1: current cartridge ID absent from the Magazine document ---
    magazine_path = tmp_path / "magazine.json"
    _write_magazine(
        magazine_path,
        {
            "schema_version": "1.0",
            "cartridges": [
                {"id": "a", "job_type": "research", "prompt": "prompt for a"},
                {"id": "c", "job_type": "research", "prompt": "prompt for c"},
            ],
        },
    )

    state_path = tmp_path / "state.json"
    initial_state_missing: dict = {
        "done": ["a"],
        "current": "b",
        "todo": ["c"],
        "status": "RUNNING",
        "revision": 1,
    }
    _write_state(state_path, initial_state_missing)
    original_bytes_missing = state_path.read_bytes()

    mock_dispatch_missing = AsyncMock(return_value=None)

    with pytest.raises(ValueError):
        asyncio.run(
            execute.dispatch_current_cartridge(
                magazine_path=str(magazine_path),
                state_path=str(state_path),
                base_url=BASE_URL,
                api_key=API_KEY,
                tool_role=TOOL_ROLE,
                model_map=MODEL_MAP,
                tool_map=TOOL_MAP,
                dispatch=mock_dispatch_missing,
            )
        )

    mock_dispatch_missing.assert_not_awaited()
    mock_preflight.assert_not_called()
    assert state_path.read_bytes() == original_bytes_missing
    assert not (state_path.parent / ".lock").exists()

    # --- case 2: current cartridge present, job_type absent from model_map
    magazine_path = tmp_path / "magazine.json"
    _write_magazine(
        magazine_path,
        {
            "schema_version": "1.0",
            "cartridges": [
                {"id": "b", "job_type": "unrouted", "prompt": CURRENT_PROMPT},
            ],
        },
    )

    state_path = tmp_path / "state.json"
    initial_state_unrouted: dict = {
        "done": ["a"],
        "current": CURRENT_ID,
        "todo": [],
        "status": "RUNNING",
        "revision": 1,
    }
    _write_state(state_path, initial_state_unrouted)
    original_bytes_unrouted = state_path.read_bytes()

    mock_dispatch_unrouted = AsyncMock(return_value=None)

    with pytest.raises(KeyError):
        asyncio.run(
            execute.dispatch_current_cartridge(
                magazine_path=str(magazine_path),
                state_path=str(state_path),
                base_url=BASE_URL,
                api_key=API_KEY,
                tool_role=TOOL_ROLE,
                model_map=MODEL_MAP,
                tool_map=TOOL_MAP,
                dispatch=mock_dispatch_unrouted,
            )
        )

    mock_dispatch_unrouted.assert_not_awaited()
    mock_preflight.assert_not_called()
    assert state_path.read_bytes() == original_bytes_unrouted
    assert not (state_path.parent / ".lock").exists()


@patch("magazine.runtime_dispatch.validate_live_runtime_pair")
def test_preflight_failure_propagates_and_leaves_run_untouched(
    mock_preflight,
    tmp_path: Path,
) -> None:
    """A valid RUNNING state + present cartridge + successful routing whose
    runtime preflight fails propagates the ValueError and leaves the run
    completely untouched.

    The preflight boundary (:func:`validate_live_runtime_pair`) is patched
    to raise ``ValueError``; routing (magazine.routing) and the dispatch
    composition (magazine.runtime_dispatch) run for real, and only the final
    dispatch callback is stubbed, so no real Open WebUI / HTTP calls are
    made.

    Demonstrates that a preflight failure:
    * propagates ``ValueError`` out of ``dispatch_current_cartridge``,
    * never awaits the final dispatch callback,
    * leaves the persisted state byte-identical, and
    * creates no lock file.
    """
    # Preflight fails (raises ValueError) without any live discovery (no HTTP).
    mock_preflight.side_effect = ValueError("runtime pair not live")

    # --- fixtures ---------------------------------------------------------
    # Valid RUNNING state with the current cartridge present in the Magazine
    # document, and job_type in model_map, so routing succeeds and we reach
    # the preflight (the failure point).
    magazine_path = tmp_path / "magazine.json"
    _write_magazine(
        magazine_path,
        {
            "schema_version": "1.0",
            "cartridges": [
                {"id": "a", "job_type": "research", "prompt": "prompt for a"},
                {"id": "b", "job_type": "research", "prompt": CURRENT_PROMPT},
                {"id": "c", "job_type": "research", "prompt": "prompt for c"},
            ],
        },
    )

    state_path = tmp_path / "state.json"
    initial_state: dict = {
        "done": ["a"],
        "current": CURRENT_ID,
        "todo": ["c"],
        "status": "RUNNING",
        "revision": 1,
    }
    _write_state(state_path, initial_state)
    original_bytes = state_path.read_bytes()

    mock_dispatch = AsyncMock(return_value=None)

    # --- act: dispatch the current cartridge -------------------------------
    with pytest.raises(ValueError):
        asyncio.run(
            execute.dispatch_current_cartridge(
                magazine_path=str(magazine_path),
                state_path=str(state_path),
                base_url=BASE_URL,
                api_key=API_KEY,
                tool_role=TOOL_ROLE,
                model_map=MODEL_MAP,
                tool_map=TOOL_MAP,
                dispatch=mock_dispatch,
            )
        )

    # --- routing succeeded: preflight reached with the resolved model/tool
    #     IDs (proves routing + the dispatch composition ran for real, and
    #     that the preflight is the actual failure point) --------------
    mock_preflight.assert_called_once_with(BASE_URL, API_KEY, MODEL_ID, TOOL_ID)

    # --- final dispatch callback never awaited --------------------------
    mock_dispatch.assert_not_awaited()

    # --- leaves the persisted state byte-identical ----------------------
    assert state_path.read_bytes() == original_bytes

    # --- creates no lock file -------------------------------------------
    assert not (state_path.parent / ".lock").exists()
