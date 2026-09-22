"""Deterministic model and tool routing for magazine jobs."""


def resolve_model(job_type: str, role_map: dict) -> str:
    """Return the exact model ID mapped to *job_type* in *role_map*.

    Raises
    ------
    TypeError
        If *job_type* is not a non-empty string or *role_map* is not a dict.
    KeyError
        If *job_type* is not present in *role_map*.
    ValueError
        If the mapped model ID is empty.
    """
    if not isinstance(job_type, str) or not job_type.strip():
        raise TypeError("job_type must be a non-empty string")
    if not isinstance(role_map, dict):
        raise TypeError("role_map must be a dict")
    if job_type not in role_map:
        raise KeyError(f"Unknown job_type: {job_type!r}")
    model_id = role_map[job_type]
    if not isinstance(model_id, str) or not model_id.strip():
        raise ValueError("mapped model ID must be a non-empty string")
    return model_id


def resolve_tool(tool_role: str, tool_map: dict) -> str:
    """Return the exact Tool ID mapped to *tool_role* in *tool_map*.

    Raises
    ------
    TypeError
        If *tool_role* is not a non-empty string or *tool_map* is not a dict.
    KeyError
        If *tool_role* is not present in *tool_map*.
    ValueError
        If the mapped Tool ID is empty.
    """
    if not isinstance(tool_role, str) or not tool_role.strip():
        raise TypeError("tool_role must be a non-empty string")
    if not isinstance(tool_map, dict):
        raise TypeError("tool_map must be a dict")
    if tool_role not in tool_map:
        raise KeyError(f"Unknown tool_role: {tool_role!r}")
    tool_id = tool_map[tool_role]
    if not isinstance(tool_id, str) or not tool_id.strip():
        raise ValueError("mapped Tool ID must be a non-empty string")
    return tool_id
