import asyncio
import json
import os
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch


def _build_archive(package_dir: Path, archive_path: Path) -> None:
    """Zip the unpacked package directory into a ``.magazine`` file."""
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for root, dirs, files in os.walk(package_dir):
            for f in files:
                p = Path(root) / f
                archive.write(str(p), os.path.relpath(p, package_dir))
            for d in dirs:
                p = Path(root) / d
                archive.writestr(os.path.relpath(p, package_dir) + "/", "")

import pytest

from magazine import run_loop, run_preparation
from magazine.async_runner import run_magazine
from magazine.runtime_identity import RuntimeIdentity
from magazine.state import STATUS_COMPLETED, STATUS_HALTED, STATUS_READY, STATUS_RUNNING

MODEL_MAP = {"research": "model-a", "synthesis": "model-b"}

# Deterministic writer identity (Slice 2) carried on every prepared run.
_TEST_WRITER = RuntimeIdentity(runtime_id="0" * 32, pid=42, start_ticks=1000)


def _prepared(status):
    current = "a" if status in (STATUS_RUNNING, STATUS_HALTED) else None
    return run_preparation.PreparedRun(
        state={"done": [], "current": current, "todo": [], "status": status, "revision": 1},
        state_path="/tmp/state.json",
        lock_path="/tmp/.lock",
        attempt=1,
        identity=_TEST_WRITER,
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
    mock_loop.assert_awaited_once_with(
        "mag", "/tmp/state.json", "/tmp/.lock", "owner", 1, _TEST_WRITER, MODEL_MAP, dispatch
    )
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


def test_real_archive_run_completes(tmp_path):
    """A two-cartridge run driven entirely by a ``.magazine`` archive.

    This exercises the full path: ``prepare_run`` over the archive, then one
    archive reload per cartridge dispatch, then finalization and reporting.
    """
    package_dir = tmp_path / "pkg"
    (package_dir / "cartridges").mkdir(parents=True, exist_ok=True)
    (package_dir / "cartridges" / "a.md").write_text(
        "prompt for a", encoding="utf-8"
    )
    (package_dir / "cartridges" / "b.md").write_text(
        "prompt for b", encoding="utf-8"
    )
    (package_dir / "magazine.yaml").write_text(
        '''schema_version: "1.0"
name: Demo
cartridges:
  - id: a
    model: research
    prompt_file: cartridges/a.md
  - id: b
    model: synthesis
    prompt_file: cartridges/b.md
''',
        encoding="utf-8",
    )
    _build_archive(package_dir, tmp_path / "magazine.magazine")

    dispatch = AsyncMock(side_effect=["RA", "RB"])
    final = asyncio.run(
        run_magazine(
            str(tmp_path / "magazine.magazine"),
            str(tmp_path / "state"),
            "owner",
            MODEL_MAP,
            dispatch,
        )
    )

    assert final["status"] == STATUS_COMPLETED
    assert final["done"] == ["a", "b"]
    # ``complete_current_cartridge`` clears ``current`` on completion, so the
    # terminal state has no current cartridge (see run_transition).
    assert final["current"] is None
    assert (tmp_path / "state/run/results/a.txt").read_text() == "RA"
    assert (tmp_path / "state/run/results/b.txt").read_text() == "RB"


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
