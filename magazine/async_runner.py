"""Top-level async composition for Magazine."""

from __future__ import annotations

from magazine import run_loop, run_preparation
from magazine.state import STATUS_COMPLETED, STATUS_HALTED, STATUS_RUNNING
from magazine.webui_dispatch import make_open_webui_dispatch

__all__ = ["run_magazine", "run_magazine_open_webui"]


async def run_magazine(
    magazine_path: str,
    state_dir: str,
    owner_id: str,
    model_map: dict,
    dispatch,
) -> dict:
    """Admit and run a Magazine workflow using an injected dispatch callable."""
    prepared = run_preparation.prepare_run(magazine_path, state_dir, owner_id)
    status = prepared.state["status"]

    if status == STATUS_RUNNING:
        result = await run_loop.run_all_cartridges(
            magazine_path,
            prepared.state_path,
            prepared.lock_path,
            owner_id,
            model_map,
            dispatch,
        )
        return result.final_state

    if status in (STATUS_COMPLETED, STATUS_HALTED):
        return prepared.state

    raise RuntimeError(
        f"unexpected run status {status!r}; expected {STATUS_RUNNING!r}, "
        f"{STATUS_COMPLETED!r}, or {STATUS_HALTED!r}"
    )


async def run_magazine_open_webui(
    magazine_path: str,
    state_dir: str,
    owner_id: str,
    base_url: str,
    api_key: str,
    model_map: dict,
) -> dict:
    """Run Magazine using the Open WebUI fresh-chat dispatch adapter."""
    dispatch = make_open_webui_dispatch(base_url, api_key)
    return await run_magazine(
        magazine_path=magazine_path,
        state_dir=state_dir,
        owner_id=owner_id,
        model_map=model_map,
        dispatch=dispatch,
    )
