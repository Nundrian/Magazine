"""Runtime preflight: discover and validate model / tool IDs."""

from __future__ import annotations

from collections.abc import Collection

from magazine.discovery import (
    discover_model_ids,
    discover_model_runtime_meta,
    discover_terminal_ids,
    discover_tool_ids,
)
from magazine.preflight import require_exact_ids


def validate_live_ids(
    base_url: str,
    api_key: str,
    required_model_ids: list[str],
    required_tool_ids: list[str],
) -> None:
    """Discover live model/tool IDs and validate that every required ID is present.

    Parameters
    ----------
    base_url : str
        Absolute http:// or https:// base URL of the Open WebUI instance.
    api_key : str
        Bearer API key used for authentication.
    required_model_ids : list of str
        Model IDs that must be available at runtime.
    required_tool_ids : list of str
        Tool IDs that must be available at runtime.

    Returns
    -------
    None
        On success (all required IDs found).

    Raises
    ------
    ValueError
        If any required model or tool ID is missing from the live discovery.
    """
    # --- discover live model IDs ---
    available_models = discover_model_ids(base_url, api_key)
    require_exact_ids(required_model_ids, available_models, "models")

    # --- discover live tool IDs ---
    available_tools = discover_tool_ids(base_url, api_key)
    require_exact_ids(required_tool_ids, available_tools, "tools")

    return None


def validate_runtime_terminal(model_meta, available_terminal_ids):
    """Validate a model's runtime metadata against discovered chat terminal IDs.

    Only one proven optional metadata field is inspected:
    ``model_meta["meta"]["terminalId"]``.

    Parameters
    ----------
    model_meta : dict
        Must contain a ``meta`` dict.  If ``meta`` contains a ``terminalId``
        key, its value must be a string that exactly matches one of
        *available_terminal_ids*.
    available_terminal_ids : list of str
        Chat-enabled terminal IDs discovered at runtime.  Must contain at
        least one ID.

    Returns
    -------
    None
        On success.

    Raises
    ------
    ValueError
        If *model_meta* is not a dict, ``meta`` is missing or not a dict,
        *available_terminal_ids* is empty or malformed, or ``terminalId``
        is present but is not a string or does not exactly match one of
        *available_terminal_ids*.
    """
    # --- model_meta must be a dict containing a 'meta' dict ---
    if not isinstance(model_meta, dict):
        raise ValueError(
            f"model_meta must be a dict, got {type(model_meta).__name__}"
        )
    if "meta" not in model_meta:
        raise ValueError("model_meta must contain a 'meta' key")
    meta = model_meta["meta"]
    if not isinstance(meta, dict):
        raise ValueError(f"model_meta['meta'] must be a dict, got {type(meta).__name__}")

    # --- available_terminal_ids must contain at least one chat enabled terminal ID ---
    if isinstance(available_terminal_ids, (str, bytes)):
        raise ValueError(
            "available_terminal_ids must be a collection of strings, "
            f"not {type(available_terminal_ids).__name__}"
        )
    if not isinstance(available_terminal_ids, Collection):
        raise ValueError(
            "available_terminal_ids must be a collection of strings, "
            f"not {type(available_terminal_ids).__name__}"
        )
    for item in available_terminal_ids:
        if not isinstance(item, str):
            raise ValueError(
                "every item in available_terminal_ids must be a string, "
                f"got {type(item).__name__}: {item!r}"
            )
    if not available_terminal_ids:
        raise ValueError("available_terminal_ids must contain at least one chat enabled terminal ID")

    # --- only the proven optional field meta.terminalId is inspected ---
    if "terminalId" not in meta:
        # Succeed when at least one chat enabled terminal is available.
        return None

    terminal_id = meta["terminalId"]
    if not isinstance(terminal_id, str):
        raise ValueError(
            "model_meta['meta']['terminalId'] must be a string, "
            f"got {type(terminal_id).__name__}: {terminal_id!r}"
        )
    if terminal_id not in available_terminal_ids:
        raise ValueError(
            f"model_meta['meta']['terminalId'] '{terminal_id}' does not match "
            f"any available terminal ID {list(available_terminal_ids)}"
        )

    return None


def validate_live_runtime_pair(
    base_url: str,
    api_key: str,
    model_id: str,
    tool_id: str,
) -> None:
    """Validate a single live model/Tool ID pair, its runtime metadata, and its chat terminal.

    First validates that *model_id* and *tool_id* are present at runtime via
    :func:`validate_live_ids`, then discovers the model's runtime metadata via
    :func:`discover_model_runtime_meta`, then discovers chat-enabled terminal
    IDs via :func:`discover_terminal_ids` and validates them against the
    metadata via :func:`validate_runtime_terminal`.

    Parameters
    ----------
    base_url : str
        Absolute http:// or https:// base URL of the Open WebUI instance.
    api_key : str
        Bearer API key used for authentication.
    model_id : str
        The model ID to validate and discover metadata for.
    tool_id : str
        The tool ID to validate and require in the runtime metadata.

    Returns
    -------
    None
        On success (both validation and metadata discovery succeed).

    Raises
    ------
    ValueError
        If the model or tool ID is missing from live discovery, if runtime
        metadata discovery fails, or if chat terminal discovery/validation
        fails.
    """
    # --- validate exact live model and Tool IDs first ---
    validate_live_ids(base_url, api_key, [model_id], [tool_id])

    # --- only after that succeeds, discover runtime metadata ---
    model_meta = discover_model_runtime_meta(base_url, api_key, model_id, tool_id)

    # --- only after that succeeds, discover and validate chat terminals ---
    available_terminal_ids = discover_terminal_ids(base_url, api_key)
    validate_runtime_terminal(model_meta, available_terminal_ids)

    return None
