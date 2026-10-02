"""One-cartridge orchestration: dispatch -> durable result -> completion."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from magazine import complete, execute, result_store

__all__ = ["OneCartridgeResult", "execute_one_cartridge"]


@dataclass(frozen=True)
class OneCartridgeResult:
    cartridge_id: str
    dispatch_result: Any
    state: dict


async def execute_one_cartridge(
    magazine_path: str,
    state_path: str,
    lock_path: str,
    owner_id: str,
    model_map: dict,
    dispatch,
) -> OneCartridgeResult:
    """Dispatch one current cartridge, save its result, then complete it."""
    dispatched = await execute.dispatch_current_cartridge(
        magazine_path, state_path, model_map, dispatch
    )

    run_dir = os.path.dirname(state_path) or os.getcwd()
    result_store.save_cartridge_result(
        run_dir, dispatched.cartridge_id, dispatched.dispatch_result
    )

    completed_state = complete.complete_current_cartridge(
        state_path,
        lock_path,
        owner_id,
        expected_current_id=dispatched.cartridge_id,
    )
    return OneCartridgeResult(
        cartridge_id=dispatched.cartridge_id,
        dispatch_result=dispatched.dispatch_result,
        state=completed_state,
    )
