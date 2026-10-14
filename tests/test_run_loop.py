import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from magazine import run_loop
from magazine.execution_meta import ExecutionMetadata, save_execution_metadata, utc_now_iso
from magazine.runtime_identity import RuntimeIdentity
from magazine.state import STATUS_COMPLETED, STATUS_RUNNING
from magazine.state_store import load_run_state, save_run_state

MODEL_MAP = {"research": "model-a", "synthesis": "model-b"}

_TEST_WRITER = RuntimeIdentity(runtime_id="0" * 32, pid=42, start_ticks=1000)


def _claim(run_dir):
    """Persist the run's execution-ownership claim (attempt 1, test writer)."""
    save_execution_metadata(
        str(run_dir / "execution.json"),
        ExecutionMetadata(
            attempt=1,
            runtime_id=_TEST_WRITER.runtime_id,
            pid=_TEST_WRITER.pid,
            start_ticks=_TEST_WRITER.start_ticks,
            created_at=utc_now_iso(),
            halted_by=None,
            halted_at=None,
            halt_reason=None,
        ),
    )


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
    _claim(run_dir)
    return mag, run_dir, state_path, lock_path


def test_runs_two_cartridges_in_order_to_completion(tmp_path):
    mag, run_dir, state_path, lock_path = _setup(tmp_path)
    dispatch = AsyncMock(side_effect=["RA", "RB"])
    result = asyncio.run(run_loop.run_all_cartridges(
        str(mag), str(state_path), str(lock_path), "owner",
        1, _TEST_WRITER, MODEL_MAP, dispatch
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
            str(mag), str(state_path), str(lock_path), "owner",
            1, _TEST_WRITER, MODEL_MAP, dispatch
        ))
    state = load_run_state(str(state_path))
    assert state["done"] == ["a"]
    assert state["current"] == "b"
    assert state["status"] == STATUS_RUNNING
    assert (run_dir / "results/a.txt").exists()
    assert not (run_dir / "results/b.txt").exists()


@patch("magazine.run_loop.run_action.persist_begin_next_cartridge")
@patch("magazine.run_loop.run_control.persist_stop_if_requested", return_value=None)
@patch("magazine.run_loop.orchestration.execute_one_cartridge", new_callable=AsyncMock)
def test_final_completed_state_does_not_begin_next(mock_execute, mock_stop, mock_begin):
    final = {"status": STATUS_COMPLETED}
    mock_execute.return_value = type("R", (), {"state": final})()
    result = asyncio.run(run_loop.run_all_cartridges(
        "mag", "state", "lock", "owner", 1, _TEST_WRITER, MODEL_MAP, object()
    ))
    assert result.final_state is final
    mock_begin.assert_not_called()


@patch("magazine.run_loop.run_control.persist_stop_if_requested", return_value=None)
@patch("magazine.run_loop.orchestration.execute_one_cartridge", new_callable=AsyncMock)
def test_unexpected_post_completion_status_fails(mock_execute, mock_stop):
    mock_execute.return_value = type("R", (), {"state": {"status": "HALTED"}})()
    with pytest.raises(RuntimeError, match="unexpected post-completion"):
        asyncio.run(run_loop.run_all_cartridges(
            "mag", "state", "lock", "owner", 1, _TEST_WRITER, MODEL_MAP, object()
        ))


class _CleanupTrackingDispatch:
    """Awaitable dispatch that records both dispatch calls and cleanup calls."""

    def __init__(self, results):
        self._iter = iter(results)
        self.dispatch_calls = []
        self.cleanup_calls = 0

    async def __call__(self, prompt, model_id):
        self.dispatch_calls.append((prompt, model_id))
        return next(self._iter)

    async def cleanup(self):
        self.cleanup_calls += 1


def test_multi_cartridge_cleanup_each_chat_independently(tmp_path):
    """Each dispatched cartridge's temporary Open WebUI chat is cleaned up exactly
    once, independently of the other cartridges, across the full run."""
    mag, run_dir, state_path, lock_path = _setup(tmp_path)
    dispatch = _CleanupTrackingDispatch(["RA", "RB"])
    result = asyncio.run(run_loop.run_all_cartridges(
        str(mag), str(state_path), str(lock_path), "owner",
        1, _TEST_WRITER, MODEL_MAP, dispatch
    ))
    assert result.final_state["status"] == STATUS_COMPLETED
    # Both cartridges dispatched through the same injected callable...
    assert dispatch.dispatch_calls == [("A", "model-a"), ("B", "model-b")]
    # ...and each one's temporary chat was cleaned up exactly once, independently.
    assert dispatch.cleanup_calls == 2
    # Every result was durably persisted before its respective cleanup.
    assert (run_dir / "results/a.txt").read_text() == "RA"
    assert (run_dir / "results/b.txt").read_text() == "RB"
