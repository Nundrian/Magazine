import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from magazine import run_loop, run_preparation
from magazine.async_runner import run_magazine
from magazine.state import STATUS_COMPLETED, STATUS_HALTED, STATUS_READY, STATUS_RUNNING

MODEL_MAP = {"research": "model-a"}


def _prepared(status):
    current = "a" if status in (STATUS_RUNNING, STATUS_HALTED) else None
    return run_preparation.PreparedRun(
        state={"done": [], "current": current, "todo": [], "status": status, "revision": 1},
        state_path="/tmp/state.json", lock_path="/tmp/.lock"
    )


@patch("magazine.async_runner.run_loop.run_all_cartridges", new_callable=AsyncMock)
@patch("magazine.async_runner.run_preparation.prepare_run")
def test_running_delegates_exactly_once(mock_prepare, mock_loop):
    mock_prepare.return_value = _prepared(STATUS_RUNNING)
    final = {"status": STATUS_COMPLETED}
    mock_loop.return_value = run_loop.MultiCartridgeResult(final_state=final)
    dispatch = object()
    result = asyncio.run(run_magazine("mag", "state-dir", "owner", MODEL_MAP, dispatch))
    mock_prepare.assert_called_once_with("mag", "state-dir", "owner")
    mock_loop.assert_awaited_once_with("mag", "/tmp/state.json", "/tmp/.lock", "owner", MODEL_MAP, dispatch)
    assert result is final


@pytest.mark.parametrize("status", [STATUS_COMPLETED, STATUS_HALTED])
@patch("magazine.async_runner.run_loop.run_all_cartridges", new_callable=AsyncMock)
@patch("magazine.async_runner.run_preparation.prepare_run")
def test_terminal_state_bypasses_loop(mock_prepare, mock_loop, status):
    prepared = _prepared(status)
    mock_prepare.return_value = prepared
    result = asyncio.run(run_magazine("mag", "state-dir", "owner", MODEL_MAP, object()))
    assert result is prepared.state
    mock_loop.assert_not_awaited()


@patch("magazine.async_runner.run_loop.run_all_cartridges", new_callable=AsyncMock)
@patch("magazine.async_runner.run_preparation.prepare_run")
def test_unexpected_status_fails_without_loop(mock_prepare, mock_loop):
    mock_prepare.return_value = _prepared(STATUS_READY)
    with pytest.raises(RuntimeError, match="unexpected run status"):
        asyncio.run(run_magazine("mag", "state-dir", "owner", MODEL_MAP, object()))
    mock_loop.assert_not_awaited()


@patch("magazine.async_runner.run_preparation.prepare_run")
def test_preparation_exception_propagates(mock_prepare):
    err = RuntimeError("admission")
    mock_prepare.side_effect = err
    with pytest.raises(RuntimeError) as exc:
        asyncio.run(run_magazine("mag", "state-dir", "owner", MODEL_MAP, object()))
    assert exc.value is err


def test_real_two_cartridge_run_completes(tmp_path):
    mag = tmp_path / "mag.json"
    mag.write_text(json.dumps({"schema_version":"1.0","cartridges":[
        {"id":"a","job_type":"research","prompt":"A"},
        {"id":"b","job_type":"research","prompt":"B"},
    ]}), encoding="utf-8")
    dispatch = AsyncMock(side_effect=["RA", "RB"])
    final = asyncio.run(run_magazine(str(mag), str(tmp_path / "state"), "owner", MODEL_MAP, dispatch))
    assert final["status"] == STATUS_COMPLETED
    assert final["done"] == ["a", "b"]
    assert (tmp_path / "state/run/results/a.txt").read_text() == "RA"
    assert (tmp_path / "state/run/results/b.txt").read_text() == "RB"
