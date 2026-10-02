import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from magazine import run_loop
from magazine.state import STATUS_COMPLETED, STATUS_RUNNING
from magazine.state_store import load_run_state, save_run_state

MODEL_MAP = {"research": "model-a", "synthesis": "model-b"}


def _setup(tmp_path):
    mag = tmp_path / "mag.json"
    mag.write_text(json.dumps({"schema_version":"1.0","cartridges":[
        {"id":"a","job_type":"research","prompt":"A"},
        {"id":"b","job_type":"synthesis","prompt":"B"},
    ]}), encoding="utf-8")
    run_dir = tmp_path / "run"; run_dir.mkdir()
    state_path = run_dir / "state.json"; lock_path = run_dir / ".lock"
    save_run_state(str(state_path), {
        "done": [], "current":"a", "todo":["b"], "status":STATUS_RUNNING, "revision":1
    })
    return mag, run_dir, state_path, lock_path


def test_runs_two_cartridges_in_order_to_completion(tmp_path):
    mag, run_dir, state_path, lock_path = _setup(tmp_path)
    dispatch = AsyncMock(side_effect=["RA", "RB"])
    result = asyncio.run(run_loop.run_all_cartridges(
        str(mag), str(state_path), str(lock_path), "owner", MODEL_MAP, dispatch
    ))
    assert result.final_state["status"] == STATUS_COMPLETED
    assert result.final_state["done"] == ["a", "b"]
    assert dispatch.await_args_list[0].args == ("A", "model-a")
    assert dispatch.await_args_list[1].args == ("B", "model-b")
    assert (run_dir / "results/a.txt").read_text() == "RA"
    assert (run_dir / "results/b.txt").read_text() == "RB"


def test_stops_on_second_dispatch_failure(tmp_path):
    mag, run_dir, state_path, lock_path = _setup(tmp_path)
    dispatch = AsyncMock(side_effect=["RA", RuntimeError("boom")])
    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(run_loop.run_all_cartridges(
            str(mag), str(state_path), str(lock_path), "owner", MODEL_MAP, dispatch
        ))
    state = load_run_state(str(state_path))
    assert state["done"] == ["a"]
    assert state["current"] == "b"
    assert state["status"] == STATUS_RUNNING
    assert (run_dir / "results/a.txt").exists()
    assert not (run_dir / "results/b.txt").exists()


@patch("magazine.run_loop.run_action.persist_begin_next_cartridge")
@patch("magazine.run_loop.orchestration.execute_one_cartridge", new_callable=AsyncMock)
def test_final_completed_state_does_not_begin_next(mock_execute, mock_begin):
    final = {"status": STATUS_COMPLETED}
    mock_execute.return_value = type("R", (), {"state": final})()
    result = asyncio.run(run_loop.run_all_cartridges(
        "mag", "state", "lock", "owner", MODEL_MAP, object()
    ))
    assert result.final_state is final
    mock_begin.assert_not_called()


@patch("magazine.run_loop.orchestration.execute_one_cartridge", new_callable=AsyncMock)
def test_unexpected_post_completion_status_fails(mock_execute):
    mock_execute.return_value = type("R", (), {"state": {"status": "HALTED"}})()
    with pytest.raises(RuntimeError, match="unexpected post-completion"):
        asyncio.run(run_loop.run_all_cartridges(
            "mag", "state", "lock", "owner", MODEL_MAP, object()
        ))
