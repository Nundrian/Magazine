"""Bounded sequential execution loop for an admitted Magazine run."""

from __future__ import annotations

from dataclasses import dataclass

from magazine import orchestration, run_action, run_control
from magazine.runtime_identity import RuntimeIdentity
from magazine.state import STATUS_COMPLETED, STATUS_READY

__all__ = ["MultiCartridgeResult", "run_all_cartridges"]


@dataclass(frozen=True)
class MultiCartridgeResult:
    final_state: dict


async def run_all_cartridges(
    magazine_path: str,
    state_path: str,
    lock_path: str,
    owner_id: str,
    writer_attempt: int,
    writer_identity: RuntimeIdentity,
    model_map: dict,
    dispatch,
) -> MultiCartridgeResult:
    """Run cartridges sequentially until the state reaches COMPLETED."""
    while True:
        stopped = run_control.persist_stop_if_requested(
            state_path, lock_path, owner_id, writer_attempt, writer_identity,
            method="boundary-before-dispatch",
        )
        if stopped is not None:
            return MultiCartridgeResult(final_state=stopped)
        try:
            result = await orchestration.execute_one_cartridge(
                magazine_path,
                state_path,
                lock_path,
                owner_id,
                writer_attempt,
                writer_identity,
                model_map,
                dispatch,
            )
        except run_control.StopRequested as stopped_signal:
            return MultiCartridgeResult(final_state=stopped_signal.state)
        status = result.state["status"]
        if status == STATUS_COMPLETED:
            return MultiCartridgeResult(final_state=result.state)
        if status != STATUS_READY:
            raise RuntimeError(
                f"unexpected post-completion run status {status!r}; expected "
                f"{STATUS_COMPLETED!r} or {STATUS_READY!r}"
            )
        stopped = run_control.persist_stop_if_requested(
            state_path, lock_path, owner_id, writer_attempt, writer_identity,
            method="boundary-after-current",
        )
        if stopped is not None:
            return MultiCartridgeResult(final_state=stopped)
        run_action.persist_begin_next_cartridge(
            state_path, lock_path, owner_id, writer_attempt, writer_identity
        )
