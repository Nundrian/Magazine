"""Stage-2 cartridge dispatch for Magazine.

Thin composition layer only: it loads the persisted run state, requires it to
be ``RUNNING``, reads and validates the Magazine document, selects the
cartridge matching the persisted ``current`` ID (the persisted state is
authoritative), and delegates everything runtime-related to
:maint:runtime_dispatch.dispatch_cartridge.

Composition sequence:

  1. Load the persisted state via ``state_store.load_run_state``.
  2. Require ``status == STATUS_RUNNING``; otherwise raise ``ValueError``.
  3. Read the Magazine document and validate it via
     ``schema.load_magazine_json`` + ``schema.validate_magazine``.
  4. Take ``current_id`` from the persisted state.
  5. Find the cartridge whose ``id`` exactly matches ``current_id``.
  6. If no matching cartridge exists, raise ``ValueError``.
  7. Await ``runtime_dispatch.dispatch_cartridge(...)`` and return a
     :class:`CartridgeDispatch` bundling the selected ``current`` ID with
     its result.

This module performs no state transitions, no completion/advancement logic,
no loops, no locking, and no environment loading.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from magazine import runtime_dispatch
from magazine import schema
from magazine import state
from magazine import state_store

__all__ = ["CartridgeDispatch", "dispatch_current_cartridge"]


@dataclass(frozen=True)
class CartridgeDispatch:
    """Bundle naming which cartridge was dispatched and its dispatch result.

    Fields:
        cartridge_id: The exact persisted ``current`` ID that was selected
            and dispatched.
        dispatch_result: The result returned by
            ``await runtime_dispatch.dispatch_cartridge``.
    """

    cartridge_id: str
    dispatch_result: Any


async def dispatch_current_cartridge(
    magazine_path: str,
    state_path: str,
    base_url: str,
    api_key: str,
    tool_role: str,
    model_map: dict,
    tool_map: dict,
    dispatch,
) -> CartridgeDispatch:
    """Dispatch the run's current cartridge.

    Args:
        magazine_path: Path to the Magazine JSON document.
        state_path: Path to the persisted run state file.
        base_url: Base URL of the Open WebUI instance.
        api_key: Bearer API key.
        tool_role: Key into *tool_map* to look up the Tool ID.
        model_map: Mapping of job types to model IDs.
        tool_map: Mapping of tool roles to Tool IDs.
        dispatch: Async callable accepting a *prompt* string.

    Returns:
        A :class:`CartridgeDispatch` whose ``cartridge_id`` is the exact
        persisted ``current`` ID that selected the dispatched cartridge and
        whose ``dispatch_result`` is the result returned by
        ``await runtime_dispatch.dispatch_cartridge``.

    Raises:
        ValueError: If the persisted status is not ``RUNNING``, or if no
            cartridge in the Magazine document has an ``id`` equal to the
            persisted ``current``.
        FileNotFoundError: If the Magazine document cannot be read.
        magazine.state.MagazineStateError: If the persisted state is malformed
            (propagated from ``state_store.load_run_state``).
        magazine.schema.MagazineSchemaError: If the Magazine document is
            invalid (propagated from ``schema.validate_magazine``).
    """
    # 1. Load the persisted run state.
    run_state = state_store.load_run_state(state_path)

    # 2. Require the authoritative status to be RUNNING.
    if run_state["status"] != state.STATUS_RUNNING:
        raise ValueError(
            f"cannot dispatch while the run status is "
            f"{run_state['status']!r}; expected {state.STATUS_RUNNING!r}"
        )

    # 3. Read the Magazine document and validate it strictly.
    document_text = Path(magazine_path).read_text(encoding="utf-8")
    document = schema.load_magazine_json(document_text)
    schema.validate_magazine(document)

    # 4. The persisted state is authoritative: take current_id from it.
    current_id = run_state["current"]

    # 5. Find the cartridge whose id exactly matches current_id.
    cartridge = None
    for item in document["cartridges"]:
        if item["id"] == current_id:
            cartridge = item
            break

    # 6. No matching cartridge -> raise.
    if cartridge is None:
        raise ValueError(
            f"no cartridge with id {current_id!r} found in the "
            f"Magazine document at {magazine_path!r}"
        )

    # 7. Delegate the runtime dispatch; bundle the selected ID with its
    #    result in a CartridgeDispatch.
    result = await runtime_dispatch.dispatch_cartridge(
        base_url,
        api_key,
        cartridge,
        tool_role,
        model_map,
        tool_map,
        dispatch,
    )
    return CartridgeDispatch(
        cartridge_id=current_id,
        dispatch_result=result,
    )
