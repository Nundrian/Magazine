"""Magazine's injected dispatch boundary.

Magazine chooses the cartridge's assigned model and sends the cartridge prompt
to an injected ``dispatch(prompt, model_id)`` callable.  The host harness is
responsible for the capabilities available inside that fresh execution
context; generic Magazine orchestration does not discover, select, provision
or preflight them.
"""

from __future__ import annotations

from magazine.package import CartridgeRecord
from magazine.routing import resolve_model
from magazine.schema import validate_cartridge

__all__ = ["dispatch_routed", "dispatch_cartridge"]


async def dispatch_routed(model_key: str, model_map: dict, dispatch, prompt: str):
    """Resolve one model key and dispatch the prompt to that concrete model."""
    model_id = resolve_model(model_key, model_map)
    return await dispatch(prompt, model_id)


async def dispatch_cartridge(cartridge: dict | CartridgeRecord, model_map: dict, dispatch):
    """Validate one cartridge and send its exact prompt through the model route."""
    if isinstance(cartridge, CartridgeRecord):
        validated = validate_cartridge(
            {"id": cartridge.id, "job_type": cartridge.model, "prompt": cartridge.prompt}
        )
    else:
        validated = validate_cartridge(cartridge)

    return await dispatch_routed(
        validated["job_type"],
        model_map,
        dispatch,
        validated["prompt"],
    )
