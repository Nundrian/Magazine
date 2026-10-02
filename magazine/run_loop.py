"""Bounded sequential execution loop for an admitted Magazine run."""

from __future__ import annotations

from dataclasses import dataclass

from magazine import orchestration, run_action
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
    model_map: dict,
    dispatch,
) -> MultiCartridgeResult:
    """Run cartridges sequentially until the state reaches COMPLETED."""
    while True:
        result = await orchestration.execute_one_cartridge(
            magazine_path,
            state_path,
            lock_path,
            owner_id,
            model_map,
            dispatch,
        )
        status = result.state["status"]
        if status == STATUS_COMPLETED:
            return MultiCartridgeResult(final_state=result.state)
        if status != STATUS_READY:
            raise RuntimeError(
                f"unexpected post-completion run status {status!r}; expected "
                f"{STATUS_COMPLETED!r} or {STATUS_READY!r}"
            )
        run_action.persist_begin_next_cartridge(state_path, lock_path, owner_id)
