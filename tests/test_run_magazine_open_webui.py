import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from magazine import run_loop, run_preparation
from magazine.async_runner import run_magazine_open_webui
from magazine.state import STATUS_COMPLETED, STATUS_RUNNING

MAGAZINE_PATH = "/tmp/magazine.json"
STATE_DIR = "/tmp/state-dir"
OWNER_ID = "owner"
BASE_URL = "http://localhost:3000"
API_KEY = "key"
MODEL_MAP = {"research": "model-a"}


def _call():
    return asyncio.run(run_magazine_open_webui(
        MAGAZINE_PATH, STATE_DIR, OWNER_ID, BASE_URL, API_KEY, MODEL_MAP
    ))


def _running():
    return run_preparation.PreparedRun(
        state={"done": [], "current": "a", "todo": [], "status": STATUS_RUNNING, "revision": 1},
        state_path="/tmp/state.json", lock_path="/tmp/.lock"
    )


@patch("magazine.async_runner.run_loop.run_all_cartridges", new_callable=AsyncMock)
@patch("magazine.async_runner.run_preparation.prepare_run")
@patch("magazine.async_runner.make_open_webui_dispatch")
def test_factory_credentials_and_dispatch_forwarded(mock_factory, mock_prepare, mock_loop):
    dispatch = object(); mock_factory.return_value = dispatch
    mock_prepare.return_value = _running()
    final = {"status": STATUS_COMPLETED}
    mock_loop.return_value = run_loop.MultiCartridgeResult(final_state=final)
    result = _call()
    mock_factory.assert_called_once_with(BASE_URL, API_KEY)
    mock_loop.assert_awaited_once_with(MAGAZINE_PATH, "/tmp/state.json", "/tmp/.lock", OWNER_ID, MODEL_MAP, dispatch)
    assert result is final


@patch("magazine.async_runner.run_preparation.prepare_run")
@patch("magazine.async_runner.make_open_webui_dispatch")
def test_factory_exception_propagates_before_prepare(mock_factory, mock_prepare):
    err = ValueError("bad url")
    mock_factory.side_effect = err
    with pytest.raises(ValueError) as exc:
        _call()
    assert exc.value is err
    mock_prepare.assert_not_called()


@patch("magazine.async_runner.run_loop.run_all_cartridges", new_callable=AsyncMock)
@patch("magazine.async_runner.run_preparation.prepare_run")
@patch("magazine.async_runner.make_open_webui_dispatch")
def test_loop_exception_propagates(mock_factory, mock_prepare, mock_loop):
    mock_factory.return_value = object(); mock_prepare.return_value = _running()
    err = RuntimeError("loop")
    mock_loop.side_effect = err
    with pytest.raises(RuntimeError) as exc:
        _call()
    assert exc.value is err
