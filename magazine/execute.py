"""Select and dispatch the persisted current Magazine cartridge."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from magazine import document, runtime_dispatch, state, state_store
from magazine.package import CartridgeRecord

__all__ = ["CartridgeDispatch", "CartridgeRecord", "dispatch_current_cartridge"]


@dataclass(frozen=True)
class CartridgeDispatch:
    cartridge_id: str
    dispatch_result: str
    cartridge: Any


async def dispatch_current_cartridge(
    magazine_path: str,
    state_path: str,
    model_map: dict,
    dispatch,
) -> CartridgeDispatch:
    """Dispatch exactly the cartridge named by the persisted RUNNING state."""
    run_state = state_store.load_run_state(state_path)
    if run_state["status"] != state.STATUS_RUNNING:
        raise ValueError(
            f"cannot dispatch while the run status is {run_state['status']!r}; "
            f"expected {state.STATUS_RUNNING!r}"
        )

    loaded = document.load_document(magazine_path)
    current_id = run_state["current"]

    cartridge = None
    if loaded.plan is not None:
        for record in loaded.plan.cartridges:
            if record.id == current_id:
                cartridge = record
                break
    if cartridge is None:
        for item in loaded.document["cartridges"]:
            if item["id"] == current_id:
                cartridge = item
                break
    if cartridge is None:
        raise ValueError(
            f"no cartridge with id {current_id!r} found in the Magazine document "
            f"at {magazine_path!r}"
        )

    result = await runtime_dispatch.dispatch_cartridge(cartridge, model_map, dispatch)
    if not isinstance(result, str):
        raise TypeError(
            "dispatch result must be a string, "
            f"got {type(result).__name__}"
        )
    return CartridgeDispatch(current_id, result, cartridge)
