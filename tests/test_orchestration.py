import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from magazine import orchestration, result_store
from magazine.state import STATUS_COMPLETED, STATUS_READY, STATUS_RUNNING
from magazine.state_store import load_run_state, save_run_state

MODEL_MAP = {"research": "model-a", "synthesis": "model-b"}


def _magazine(path: Path, carts):
    path.write_text(json.dumps({"schema_version":"1.0","cartridges":carts}), encoding="utf-8")


def _running_state(path: Path, current="a", todo=None):
    save_run_state(str(path), {
        "done": [], "current": current, "todo": list(todo or []),
        "status": STATUS_RUNNING, "revision": 1,
    })


class _DispatchStub:
    """Awaitable dispatch stub whose ``cleanup()`` can be observed.

    ``return_value`` is yielded by ``dispatch(prompt, model_id)``; the optional
    ``cleanup_fn`` is awaited by ``dispatch.cleanup()`` and counted.
    """

    def __init__(self, return_value="RESULT", cleanup_fn=None):
        self.return_value = return_value
        self._cleanup_fn = cleanup_fn
        self.cleanup_calls = 0

    async def __call__(self, prompt, model_id):
        return self.return_value

    async def cleanup(self):
        self.cleanup_calls += 1
        if self._cleanup_fn is not None:
            await self._cleanup_fn()


def test_execute_one_cartridge_persists_then_advances(tmp_path):
    mag = tmp_path / "mag.json"
    _magazine(mag, [
        {"id":"a","job_type":"research","prompt":"A"},
        {"id":"b","job_type":"synthesis","prompt":"B"},
    ])
    run_dir = tmp_path / "run"; run_dir.mkdir()
    state_path = run_dir / "state.json"; lock_path = run_dir / ".lock"
    _running_state(state_path, "a", ["b"])
    dispatch = AsyncMock(return_value="RESULT-A")

    result = asyncio.run(orchestration.execute_one_cartridge(
        str(mag), str(state_path), str(lock_path), "owner", MODEL_MAP, dispatch
    ))

    assert result.cartridge_id == "a"
    assert result.dispatch_result == "RESULT-A"
    assert result.state["status"] == STATUS_READY
    assert result.state["done"] == ["a"]
    assert Path(result_store.result_path(str(run_dir), "a")).read_text(encoding="utf-8") == "RESULT-A"
    dispatch.assert_awaited_once_with("A", "model-a")


def test_final_cartridge_completes(tmp_path):
    mag = tmp_path / "mag.json"
    _magazine(mag, [{"id":"a","job_type":"research","prompt":"A"}])
    run_dir = tmp_path / "run"; run_dir.mkdir()
    state_path = run_dir / "state.json"; lock_path = run_dir / ".lock"
    _running_state(state_path)
    result = asyncio.run(orchestration.execute_one_cartridge(
        str(mag), str(state_path), str(lock_path), "owner", MODEL_MAP, AsyncMock(return_value="ok")
    ))
    assert result.state["status"] == STATUS_COMPLETED
    assert result.state["done"] == ["a"]


def test_dispatch_failure_does_not_persist_or_complete(tmp_path):
    mag = tmp_path / "mag.json"
    _magazine(mag, [{"id":"a","job_type":"research","prompt":"A"}])
    run_dir = tmp_path / "run"; run_dir.mkdir()
    state_path = run_dir / "state.json"; lock_path = run_dir / ".lock"
    _running_state(state_path)
    error = RuntimeError("dispatch failed")
    with pytest.raises(RuntimeError) as exc:
        asyncio.run(orchestration.execute_one_cartridge(
            str(mag), str(state_path), str(lock_path), "owner", MODEL_MAP, AsyncMock(side_effect=error)
        ))
    assert exc.value is error
    assert load_run_state(str(state_path))["status"] == STATUS_RUNNING
    assert not (run_dir / "results").exists()


@patch("magazine.orchestration.complete.complete_current_cartridge")
@patch("magazine.orchestration.result_store.save_cartridge_result")
@patch("magazine.orchestration.execute.dispatch_current_cartridge", new_callable=AsyncMock)
def test_result_save_occurs_before_completion(mock_dispatch, mock_save, mock_complete):
    events = []
    mock_dispatch.return_value = type("D", (), {"cartridge_id":"a", "dispatch_result":"raw"})()
    mock_save.side_effect = lambda *a, **k: events.append("save")
    mock_complete.side_effect = lambda *a, **k: events.append("complete") or {"status": STATUS_COMPLETED}
    asyncio.run(orchestration.execute_one_cartridge(
        "mag", "/tmp/run/state.json", "/tmp/run/.lock", "owner", MODEL_MAP, object()
    ))
    assert events == ["save", "complete"]


@patch("magazine.orchestration.complete.complete_current_cartridge")
@patch("magazine.orchestration.result_store.save_cartridge_result", side_effect=OSError("disk full"))
@patch("magazine.orchestration.execute.dispatch_current_cartridge", new_callable=AsyncMock)
def test_result_persistence_failure_prevents_completion(mock_dispatch, mock_save, mock_complete):
    mock_dispatch.return_value = type("D", (), {"cartridge_id":"a", "dispatch_result":"raw"})()
    with pytest.raises(OSError, match="disk full"):
        asyncio.run(orchestration.execute_one_cartridge(
            "mag", "/tmp/run/state.json", "/tmp/run/.lock", "owner", MODEL_MAP, object()
        ))
    mock_complete.assert_not_called()


@patch("magazine.orchestration.complete.complete_current_cartridge")
@patch("magazine.orchestration.result_store.save_cartridge_result")
@patch("magazine.orchestration.execute.dispatch_current_cartridge", new_callable=AsyncMock)
def test_cleanup_happens_between_persist_and_completion(mock_dispatch, mock_save, mock_complete):
    """Ordering contract: dispatch -> save -> cleanup -> complete."""
    mock_dispatch.return_value = type("D", (), {"cartridge_id":"a", "dispatch_result":"raw"})()

    events = []

    async def on_cleanup():
        events.append("cleanup")

    dispatch = _DispatchStub(return_value="raw", cleanup_fn=on_cleanup)
    mock_save.side_effect = lambda *a, **k: events.append("save")
    mock_complete.side_effect = lambda *a, **k: (events.append("complete"), {"status": STATUS_COMPLETED})[-1]

    asyncio.run(orchestration.execute_one_cartridge(
        "mag", "/tmp/run/state.json", "/tmp/run/.lock", "owner", MODEL_MAP, dispatch
    ))
    assert events == ["save", "cleanup", "complete"]
    assert dispatch.cleanup_calls == 1
    mock_save.assert_called_once()
    mock_complete.assert_called_once()


@patch("magazine.orchestration.complete.complete_current_cartridge")
@patch("magazine.orchestration.result_store.save_cartridge_result", side_effect=OSError("disk full"))
@patch("magazine.orchestration.execute.dispatch_current_cartridge", new_callable=AsyncMock)
def test_persistence_failure_prevents_cleanup(mock_dispatch, mock_save, mock_complete):
    """If the result is not durably saved, the temporary chat must not be deleted."""
    mock_dispatch.return_value = type("D", (), {"cartridge_id":"a", "dispatch_result":"raw"})()
    dispatch = _DispatchStub(return_value="raw")
    with pytest.raises(OSError, match="disk full"):
        asyncio.run(orchestration.execute_one_cartridge(
            "mag", "/tmp/run/state.json", "/tmp/run/.lock", "owner", MODEL_MAP, dispatch
        ))
    # No durable result => no cleanup; and the run never reaches completion.
    assert dispatch.cleanup_calls == 0
    mock_complete.assert_not_called()


def test_cleanup_failure_leaves_result_intact_and_completes(tmp_path):
    """A failed cleanup must not invalidate an already-persisted result or stop completion."""
    mag = tmp_path / "mag.json"
    _magazine(mag, [{"id":"a","job_type":"research","prompt":"A"}])
    run_dir = tmp_path / "run"; run_dir.mkdir()
    state_path = run_dir / "state.json"; lock_path = run_dir / ".lock"
    _running_state(state_path)

    async def failing_cleanup():
        raise RuntimeError("cleanup failed")

    dispatch = _DispatchStub(return_value="RESULT-A", cleanup_fn=failing_cleanup)
    result = asyncio.run(
        orchestration.execute_one_cartridge(
            str(mag), str(state_path), str(lock_path), "owner", MODEL_MAP, dispatch
        )
    )
    # cleanup was attempted but its exception was swallowed.
    assert dispatch.cleanup_calls == 1
    # The result was persisted *before* cleanup and survives the cleanup failure.
    assert Path(result_store.result_path(str(run_dir), "a")).read_text(encoding="utf-8") == "RESULT-A"
    # The failed cleanup must not prevent the run from completing.
    assert result.state["status"] == STATUS_COMPLETED
    assert result.state["done"] == ["a"]
