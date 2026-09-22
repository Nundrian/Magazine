"""Async dispatch guarded by a live preflight check."""

from __future__ import annotations

from magazine.routing import resolve_model, resolve_tool
from magazine.runtime_preflight import validate_live_ids, validate_live_runtime_pair
from magazine.schema import validate_cartridge


async def dispatch_after_preflight(
    base_url: str,
    api_key: str,
    required_model_ids: list[str],
    required_tool_ids: list[str],
    dispatch,
    prompt: str,
):
    """Run a preflight validation, then await *dispatch* on success.

    Parameters
    ----------
    base_url : str
        Base URL of the Open WebUI instance.
    api_key : str
        Bearer API key.
    required_model_ids : list of str
        Model IDs that must be available.
    required_tool_ids : list of str
        Tool IDs that must be available.
    dispatch : callable[[str], Awaitable]
        Async callable that accepts a *prompt* string and returns a result.
    prompt : str
        The prompt to pass to *dispatch*.

    Returns
    -------
    Any
        The result returned by ``await dispatch(prompt)``.

    Raises
    ------
    ValueError
        If the preflight validation fails (propagated from
        :func:`validate_live_ids`).
    """
    validate_live_ids(base_url, api_key, required_model_ids, required_tool_ids)
    return await dispatch(prompt)


async def dispatch_runtime_pair(
    base_url: str,
    api_key: str,
    model_id: str,
    tool_id: str,
    dispatch,
    prompt: str,
):
    """Validate a live model/Tool ID pair, then await *dispatch* on success.

    Parameters
    ----------
    base_url : str
        Base URL of the Open WebUI instance.
    api_key : str
        Bearer API key.
    model_id : str
        The model ID to validate and dispatch with.
    tool_id : str
        The tool ID to validate and dispatch with.
    dispatch : callable[[str], Awaitable]
        Async callable that accepts a *prompt* string and returns a result.
    prompt : str
        The prompt to pass to *dispatch*.

    Returns
    -------
    Any
        The result returned by ``await dispatch(prompt)``.

    Raises
    ------
    ValueError
        If the runtime pair validation fails (propagated from
        :func:`validate_live_runtime_pair`).
    """
    validate_live_runtime_pair(base_url, api_key, model_id, tool_id)
    return await dispatch(prompt)


async def dispatch_routed(
    base_url: str,
    api_key: str,
    job_type: str,
    tool_role: str,
    model_map: dict,
    tool_map: dict,
    dispatch,
    prompt: str,
):
    """Resolve model and tool IDs, then run preflight + dispatch.

    Parameters
    ----------
    base_url : str
        Base URL of the Open WebUI instance.
    api_key : str
        Bearer API key.
    job_type : str
        Key into *model_map* to look up the model ID.
    tool_role : str
        Key into *tool_map* to look up the Tool ID.
    model_map : dict
        Mapping of job types to model IDs.
    tool_map : dict
        Mapping of tool roles to tool IDs.
    dispatch : callable[[str], Awaitable]
        Async callable that accepts a *prompt* string and returns a result.
    prompt : str
        The prompt to pass to *dispatch*.

    Returns
    -------
    Any
        The result returned by ``await dispatch_runtime_pair(...)``.

    Raises
    ------
    TypeError / KeyError / ValueError
        If *job_type* or *tool_role* cannot be resolved (propagated from
        :func:`resolve_model` / :func:`resolve_tool`).
    ValueError
        If the runtime pair validation fails (propagated from
        :func:`validate_live_runtime_pair`).
    """
    model_id = resolve_model(job_type, model_map)
    tool_id = resolve_tool(tool_role, tool_map)
    return await dispatch_runtime_pair(
        base_url,
        api_key,
        model_id,
        tool_id,
        dispatch=dispatch,
        prompt=prompt,
    )


async def dispatch_cartridge(
    base_url: str,
    api_key: str,
    cartridge: dict,
    tool_role: str,
    model_map: dict,
    tool_map: dict,
    dispatch,
):
    """Validate a cartridge, then route it through the existing dispatch path.

    This is the integration boundary between schema validation and runtime
    dispatch. It guarantees that a raw cartridge is validated *before* any
    runtime/worker dispatch is attempted, and that the validated cartridge's
    exact ``job_type`` and ``prompt`` are forwarded unchanged to the existing
    routed dispatch path.

    Parameters
    ----------
    base_url : str
        Base URL of the Open WebUI instance.
    api_key : str
        Bearer API key.
    cartridge : dict
        A Magazine cartridge dict with ``id``, ``job_type`` and ``prompt``.
        Validated via :func:`validate_cartridge` before any dispatch.
    tool_role : str
        Key into *tool_map* to look up the Tool ID.
    model_map : dict
        Mapping of job types to model IDs.
    tool_map : dict
        Mapping of tool roles to tool IDs.
    dispatch : callable[[str], Awaitable]
        Async callable that accepts a *prompt* string and returns a result.

    Returns
    -------
    Any
        The result returned by :func:`dispatch_routed`.

    Raises
    ------
    MagazineSchemaError
        If *cartridge* fails validation. In this case zero runtime/worker
        dispatch is performed.
    TypeError / KeyError / ValueError
        If routing or runtime-pair validation fails (propagated from
        :func:`dispatch_routed`).
    """
    # Validate the cartridge BEFORE any runtime/worker dispatch.
    validated = validate_cartridge(cartridge)

    # Route through the existing path using the validated cartridge's exact
    # job_type and prompt, unchanged. No Magazine state or other fields are
    # appended to the prompt.
    return await dispatch_routed(
        base_url,
        api_key,
        job_type=validated["job_type"],
        tool_role=tool_role,
        model_map=model_map,
        tool_map=tool_map,
        dispatch=dispatch,
        prompt=validated["prompt"],
    )
