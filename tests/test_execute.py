import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from magazine.execute import dispatch_current_cartridge
from magazine.package import CartridgeRecord
from magazine.state import STATUS_READY, STATUS_RUNNING
from magazine.state_store import save_run_state

MODEL_MAP = {"research": "model-a", "synthesis": "model-b"}


def _state(path, status=STATUS_RUNNING, current="a"):
    state = {"done": [], "current": current, "todo": ["b"] if current == "a" else [], "status": status, "revision": 1}
    save_run_state(str(path), state)
    return state


def test_dispatch_current_json_cartridge(tmp_path):
    mag = tmp_path / "mag.json"
    mag.write_text(json.dumps({"schema_version":"1.0","cartridges":[{"id":"a","job_type":"research","prompt":"A"},{"id":"b","job_type":"synthesis","prompt":"B"}]}), encoding="utf-8")
    state_path = tmp_path / "state.json"
    _state(state_path)
    dispatch = AsyncMock(return_value="result-a")
    result = asyncio.run(dispatch_current_cartridge(str(mag), str(state_path), MODEL_MAP, dispatch))
    assert result.cartridge_id == "a"
    assert result.dispatch_result == "result-a"
    assert result.cartridge["prompt"] == "A"
    dispatch.assert_awaited_once_with("A", "model-a")


def test_dispatch_current_package_returns_record(tmp_path):
    package = tmp_path / "pkg"
    (package / "cartridges").mkdir(parents=True)
    (package / "cartridges/a.md").write_text("Package A", encoding="utf-8")
    (package / "magazine.yaml").write_text('''schema_version: "1.0"
name: Demo
cartridges:
  - id: a
    model: research
    prompt_file: cartridges/a.md
''', encoding="utf-8")
    state_path = tmp_path / "state.json"
    save_run_state(str(state_path), {"done": [], "current": "a", "todo": [], "status": STATUS_RUNNING, "revision": 1})
    dispatch = AsyncMock(return_value="ok")
    result = asyncio.run(dispatch_current_cartridge(str(package), str(state_path), MODEL_MAP, dispatch))
    assert isinstance(result.cartridge, CartridgeRecord)
    assert result.cartridge.prompt == "Package A"


def test_non_running_state_rejected_before_dispatch(tmp_path):
    mag = tmp_path / "mag.json"
    mag.write_text(json.dumps({"schema_version":"1.0","cartridges":[{"id":"a","job_type":"research","prompt":"A"}]}), encoding="utf-8")
    state_path = tmp_path / "state.json"
    save_run_state(str(state_path), {"done": [], "current": None, "todo": ["a"], "status": STATUS_READY, "revision": 0})
    dispatch = AsyncMock()
    with pytest.raises(ValueError, match="cannot dispatch"):
        asyncio.run(dispatch_current_cartridge(str(mag), str(state_path), MODEL_MAP, dispatch))
    dispatch.assert_not_awaited()


def test_missing_current_id_rejected(tmp_path):
    mag = tmp_path / "mag.json"
    mag.write_text(json.dumps({"schema_version":"1.0","cartridges":[{"id":"a","job_type":"research","prompt":"A"}]}), encoding="utf-8")
    state_path = tmp_path / "state.json"
    save_run_state(str(state_path), {"done": [], "current": "missing", "todo": [], "status": STATUS_RUNNING, "revision": 1})
    dispatch = AsyncMock()
    with pytest.raises(ValueError, match="no cartridge"):
        asyncio.run(dispatch_current_cartridge(str(mag), str(state_path), MODEL_MAP, dispatch))
    dispatch.assert_not_awaited()


def test_dispatch_failure_propagates(tmp_path):
    mag = tmp_path / "mag.json"
    mag.write_text(json.dumps({"schema_version":"1.0","cartridges":[{"id":"a","job_type":"research","prompt":"A"}]}), encoding="utf-8")
    state_path = tmp_path / "state.json"
    save_run_state(str(state_path), {"done": [], "current": "a", "todo": [], "status": STATUS_RUNNING, "revision": 1})
    error = RuntimeError("dispatch failed")
    dispatch = AsyncMock(side_effect=error)
    with pytest.raises(RuntimeError) as exc:
        asyncio.run(dispatch_current_cartridge(str(mag), str(state_path), MODEL_MAP, dispatch))
    assert exc.value is error
