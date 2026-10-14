"""Regression contract for durable, owner-fenced workflow Stop."""

import asyncio
import json

import pytest

from magazine import async_runner, run_control, run_loop
from magazine.execution_meta import ExecutionMetadata, save_execution_metadata
from magazine.runtime_identity import RuntimeIdentity
from magazine.state import STATUS_COMPLETED, STATUS_HALTED, STATUS_RUNNING
from magazine.state_store import load_run_state, save_run_state


OWNER = "owner-a"
WRITER = RuntimeIdentity(runtime_id="1" * 32, pid=4242, start_ticks=101)
MODEL_MAP = {"research": "model-a", "synthesis": "model-b"}


def _setup(tmp_path):
    magazine_path = tmp_path / "magazine.json"
    magazine_path.write_text(
        json.dumps({
            "schema_version": "1.0",
            "cartridges": [
                {"id": "a", "job_type": "research", "prompt": "A"},
                {"id": "b", "job_type": "synthesis", "prompt": "B"},
            ],
        }),
        encoding="utf-8",
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    state_path = run_dir / "state.json"
    lock_path = run_dir / ".lock"
    save_run_state(str(state_path), {
        "done": [],
        "current": "a",
        "todo": ["b"],
        "status": STATUS_RUNNING,
        "revision": 1,
    })
    save_execution_metadata(
        str(run_dir / "execution.json"),
        ExecutionMetadata(
            attempt=1,
            runtime_id=WRITER.runtime_id,
            pid=WRITER.pid,
            start_ticks=WRITER.start_ticks,
            created_at="2026-01-01T00:00:00+00:00",
            halted_by=None,
            halted_at=None,
            halt_reason=None,
        ),
    )
    return magazine_path, run_dir, state_path, lock_path


class _StopAfterFirstResult:
    def __init__(self, state_path, lock_path):
        self.state_path = state_path
        self.lock_path = lock_path
        self.calls = []

    async def __call__(self, prompt, model_id):
        self.calls.append((prompt, model_id))
        if len(self.calls) == 1:
            run_control.persist_request_stop(
                str(self.state_path), str(self.lock_path), OWNER, 1, WRITER
            )
            return "RESULT-A"
        raise AssertionError("Stop must prevent dispatch of cartridge b")


def test_stop_at_safe_boundary_preserves_completed_output_and_blocks_next_dispatch(tmp_path):
    magazine_path, run_dir, state_path, lock_path = _setup(tmp_path)
    dispatch = _StopAfterFirstResult(state_path, lock_path)

    result = asyncio.run(run_loop.run_all_cartridges(
        str(magazine_path), str(state_path), str(lock_path), OWNER,
        1, WRITER, MODEL_MAP, dispatch,
    ))

    assert dispatch.calls == [("A", "model-a")]
    assert result.final_state["status"] == STATUS_HALTED
    assert result.final_state["done"] == ["a"]
    assert result.final_state["current"] is None
    assert result.final_state["todo"] == ["b"]
    assert (run_dir / "results" / "a.txt").read_text(encoding="utf-8") == "RESULT-A"
    control = run_control.load_stop_control(str(state_path))
    assert control["request"] == "STOP"
    assert control["termination_status"] == "CONFIRMED"
    assert control["termination_method"] == "boundary-after-current"


def test_repeated_stop_request_is_idempotent(tmp_path):
    _, _, state_path, lock_path = _setup(tmp_path)
    first = run_control.persist_request_stop(
        str(state_path), str(lock_path), OWNER, 1, WRITER
    )
    second = run_control.persist_request_stop(
        str(state_path), str(lock_path), OWNER, 1, WRITER
    )

    assert second == first
    assert second["request"] == "STOP"
    assert second["termination_status"] == "REQUESTED"


def test_stop_request_survives_process_restart_without_becoming_confirmed(tmp_path):
    _, _, state_path, lock_path = _setup(tmp_path)
    requested = run_control.persist_request_stop(
        str(state_path), str(lock_path), OWNER, 1, WRITER
    )

    # Reloading from disk models a new process generation. A durable request
    # must survive, but a process exit is not proof that active inference was
    # cancelled.
    reloaded = run_control.load_stop_control(str(state_path))
    assert reloaded == requested
    assert reloaded["termination_status"] == "REQUESTED"
    assert reloaded["cancellation_status"] == "NOT_ATTEMPTED"


def test_cancellation_failure_is_durable_but_never_reported_as_termination(tmp_path):
    _, _, state_path, lock_path = _setup(tmp_path)
    run_control.persist_request_stop(
        str(state_path), str(lock_path), OWNER, 1, WRITER
    )
    failed = run_control.persist_cancellation_result(
        str(state_path), str(lock_path), OWNER, 1, WRITER,
        confirmed=False,
        detail="Open WebUI stop endpoint returned HTTP 500",
    )

    assert failed["request"] == "STOP"
    assert failed["cancellation_status"] == "FAILED"
    assert failed["termination_status"] == "REQUESTED"
    assert failed["actual_at"] is None
    assert load_run_state(str(state_path))["status"] == STATUS_RUNNING


@pytest.mark.parametrize("confirmation", [False, True])
def test_stop_during_final_cartridge_records_completed_not_stopped(tmp_path, confirmation):
    magazine_path, run_dir, state_path, lock_path = _setup(tmp_path)
    save_run_state(str(state_path), {
        "done": ["a"], "current": "b", "todo": [],
        "status": STATUS_RUNNING, "revision": 3,
    })

    async def final_cartridge(prompt, model_id):
        assert (prompt, model_id) == ("B", "model-b")
        run_control.persist_request_stop(str(state_path), str(lock_path), OWNER, 1, WRITER)
        if confirmation:
            run_control.persist_cancellation_result(
                str(state_path), str(lock_path), OWNER, 1, WRITER,
                confirmed=True, detail="chat task gone while completion was finishing",
            )
        return "FINAL RESULT"

    result = asyncio.run(run_loop.run_all_cartridges(
        str(magazine_path), str(state_path), str(lock_path), OWNER,
        1, WRITER, MODEL_MAP, final_cartridge,
    ))
    assert result.final_state["status"] == STATUS_COMPLETED
    assert result.final_state["done"] == ["a", "b"]
    assert (run_dir / "results" / "b.txt").read_text(encoding="utf-8") == "FINAL RESULT"
    control = run_control.load_stop_control(str(state_path))
    assert control["termination_status"] == "NOT_APPLIED"
    assert control["termination_method"] == "workflow-completed"
    assert control["actual_at"]


def test_stop_then_dispatch_error_is_recorded_as_error_not_stop(tmp_path):
    magazine_path, _, state_path, lock_path = _setup(tmp_path)

    async def failing_dispatch(prompt, model_id):
        run_control.persist_request_stop(str(state_path), str(lock_path), OWNER, 1, WRITER)
        raise RuntimeError("inference failed")

    with pytest.raises(RuntimeError, match="inference failed"):
        asyncio.run(async_runner._run_cartridges_or_halt(
            str(magazine_path), str(state_path), str(lock_path), OWNER,
            1, WRITER, MODEL_MAP, failing_dispatch,
        ))
    state = load_run_state(str(state_path))
    assert state["status"] == STATUS_HALTED
    assert state["current"] == "a"
    control = run_control.load_stop_control(str(state_path))
    assert control["termination_status"] == "NOT_APPLIED"
    assert control["termination_method"] == "execution-failed"
    with pytest.raises(run_control.StopControlError, match="not applied"):
        run_control.persist_confirm_termination(
            str(state_path), str(lock_path), OWNER, 1, WRITER,
            method="active-inference-cancelled",
        )


def test_confirmed_cancelled_task_remains_available_for_stop_confirmation(tmp_path):
    magazine_path, _, state_path, lock_path = _setup(tmp_path)

    async def cancelled_dispatch(prompt, model_id):
        run_control.persist_request_stop(str(state_path), str(lock_path), OWNER, 1, WRITER)
        run_control.persist_cancellation_result(
            str(state_path), str(lock_path), OWNER, 1, WRITER,
            confirmed=True, detail="Open WebUI acknowledged stop",
        )
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(async_runner._run_cartridges_or_halt(
            str(magazine_path), str(state_path), str(lock_path), OWNER,
            1, WRITER, MODEL_MAP, cancelled_dispatch,
        ))
    assert load_run_state(str(state_path))["status"] == STATUS_HALTED
    control = run_control.load_stop_control(str(state_path))
    assert control["termination_status"] == "REQUESTED"
    confirmed = run_control.persist_confirm_termination(
        str(state_path), str(lock_path), OWNER, 1, WRITER,
        method="active-inference-cancelled",
    )
    assert confirmed["termination_status"] == "CONFIRMED"
