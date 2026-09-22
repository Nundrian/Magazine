"""Preflight validation primitives for the magazine module."""

from collections.abc import Collection


def require_exact_ids(required_ids, available_ids, label):
    """Validate that every *required_ids* ID is present in *available_ids*.

    Parameters
    ----------
    required_ids : Collection of str
        IDs that must be found.
    available_ids : Collection of str
        IDs that are available.
    label : str
        Human-readable label used in error messages.

    Raises
    ------
    ValueError
        If *label* is empty / whitespace-only, or if any required ID is
        missing from *available_ids*.

    Returns
    -------
    None
        On success (all required IDs found).
    """
    # --- label validation ---
    if not isinstance(label, str) or not label.strip():
        raise ValueError(f"label must be a non-empty string, got: {label!r}")

    # --- collection validation (bare str/bytes are NOT valid collections) ---
    def _validate_collection(ids, name):
        if isinstance(ids, (str, bytes)):
            raise ValueError(f"{name} must be a collection of strings, not {type(ids).__name__}")
        if not isinstance(ids, Collection):
            raise ValueError(f"{name} must be a collection of strings, not {type(ids).__name__}")
        for item in ids:
            if not isinstance(item, str):
                raise ValueError(
                    f"every item in {name} must be a string, got {type(item).__name__}: {item!r}"
                )

    _validate_collection(required_ids, "required_ids")
    _validate_collection(available_ids, "available_ids")

    # --- set look-up (no fuzzy matching) ---
    available_set = set(available_ids)
    missing = [rid for rid in required_ids if rid not in available_set]

    if missing:
        if len(missing) == 1:
            raise ValueError(
                f"{label}: missing required ID '{missing[0]}'"
            )
        raise ValueError(
            f"{label}: missing required IDs {missing}"
        )

    return None


def extract_model_ids(payload):
    """Extract model IDs from a validated payload dict.

    Parameters
    ----------
    payload : dict
        Must contain a ``data`` key whose value is a list of dicts,
        each with a string ``id`` key.

    Returns
    -------
    list of str
        The ``id`` values exactly as provided.

    Raises
    ------
    ValueError
        If *payload* is not a dict, ``data`` is missing or not a list,
        or any item in ``data`` is not a dict with a string ``id``.
    """
    if not isinstance(payload, dict):
        raise ValueError("payload must be a dict")

    if "data" not in payload:
        raise ValueError("payload must contain a 'data' key")

    data = payload["data"]
    if not isinstance(data, list):
        raise ValueError("payload['data'] must be a list")

    ids = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"data[{i}] must be a dict, got {type(item).__name__}")
        if "id" not in item:
            raise ValueError(f"data[{i}] must contain an 'id' key")
        item_id = item["id"]
        if not isinstance(item_id, str):
            raise ValueError(f"data[{i}]['id'] must be a string, got {type(item_id).__name__}")
        ids.append(item_id)

    return ids


def extract_tool_ids(payload):
    """Extract tool IDs from a validated top-level list payload.

    Parameters
    ----------
    payload : list
        Must be a list of dicts, each with a string ``id`` key.

    Returns
    -------
    list of str
        The ``id`` values exactly as provided.

    Raises
    ------
    ValueError
        If *payload* is not a list, or any item is not a dict with a
        string ``id``.
    """
    if not isinstance(payload, list):
        raise ValueError("payload must be a list")

    ids = []
    for i, item in enumerate(payload):
        if not isinstance(item, dict):
            raise ValueError(f"payload[{i}] must be a dict, got {type(item).__name__}")
        if "id" not in item:
            raise ValueError(f"payload[{i}] must contain an 'id' key")
        item_id = item["id"]
        if not isinstance(item_id, str):
            raise ValueError(f"payload[{i}]['id'] must be a string, got {type(item_id).__name__}")
        ids.append(item_id)

    return ids


def extract_chat_terminal_ids(payload):
    """Extract terminal IDs where ``contexts.chat`` is ``True``.

    Parameters
    ----------
    payload : list
        Must be a list of dicts.  Each dict must have a string ``id`` key
        and a ``contexts`` key whose value is a dict containing a boolean
        ``chat`` key.

    Returns
    -------
    list of str
        The ``id`` values of terminals whose ``contexts.chat`` is ``True``,
        in the order they appear in *payload*.

    Raises
    ------
    ValueError
        If *payload* is not a list, or any item is not a dict with a
        string ``id``, a dict ``contexts``, or a boolean ``contexts.chat``.
    """
    if not isinstance(payload, list):
        raise ValueError("payload must be a list")

    ids = []
    for i, item in enumerate(payload):
        if not isinstance(item, dict):
            raise ValueError(f"payload[{i}] must be a dict, got {type(item).__name__}")
        if "id" not in item:
            raise ValueError(f"payload[{i}] must contain an 'id' key")
        item_id = item["id"]
        if not isinstance(item_id, str):
            raise ValueError(f"payload[{i}]['id'] must be a string, got {type(item_id).__name__}")

        if "contexts" not in item:
            raise ValueError(f"payload[{i}] must contain a 'contexts' key")
        contexts = item["contexts"]
        if not isinstance(contexts, dict):
            raise ValueError(f"payload[{i}]['contexts'] must be a dict, got {type(contexts).__name__}")

        if "chat" not in contexts:
            raise ValueError(f"payload[{i}]['contexts'] must contain a 'chat' key")
        chat = contexts["chat"]
        if not isinstance(chat, bool):
            raise ValueError(f"payload[{i}]['contexts']['chat'] must be a boolean, got {type(chat).__name__}")

        if chat:
            ids.append(item_id)

    return ids


def validate_model_runtime_meta(payload, required_tool_id):
    """Validate a model's runtime metadata for terminal tool availability.

    Only two proven Open WebUI fields are inspected:
    ``payload["meta"]["capabilities"]["terminal"]`` and
    ``payload["meta"]["toolIds"]``.

    Parameters
    ----------
    payload : dict
        Must contain a ``meta`` dict with a ``capabilities`` dict whose
        ``terminal`` key is exactly ``True``, and a ``toolIds`` list of
        strings.
    required_tool_id : str
        A tool ID that must be present exactly in ``toolIds``.

    Raises
    ------
    ValueError
        If any of the required fields is missing or malformed, or if
        *required_tool_id* is not found exactly in ``toolIds``.

    Returns
    -------
    None
        On success.
    """
    if not isinstance(payload, dict):
        raise ValueError("payload must be a dict")

    if "meta" not in payload:
        raise ValueError("payload must contain a 'meta' key")
    meta = payload["meta"]
    if not isinstance(meta, dict):
        raise ValueError("payload['meta'] must be a dict")

    if "capabilities" not in meta:
        raise ValueError("payload['meta'] must contain a 'capabilities' key")
    capabilities = meta["capabilities"]
    if not isinstance(capabilities, dict):
        raise ValueError("payload['meta']['capabilities'] must be a dict")

    if "terminal" not in capabilities:
        raise ValueError("payload['meta']['capabilities'] must contain a 'terminal' key")
    terminal = capabilities["terminal"]
    if terminal is not True:
        raise ValueError(
            "payload['meta']['capabilities']['terminal'] must be exactly True, "
            f"got {type(terminal).__name__}: {terminal!r}"
        )

    if "toolIds" not in meta:
        raise ValueError("payload['meta'] must contain a 'toolIds' key")
    tool_ids = meta["toolIds"]
    if not isinstance(tool_ids, list):
        raise ValueError("payload['meta']['toolIds'] must be a list")
    for i, tid in enumerate(tool_ids):
        if not isinstance(tid, str):
            raise ValueError(
                f"payload['meta']['toolIds'][{i}] must be a string, got {type(tid).__name__}"
            )

    if required_tool_id not in tool_ids:
        raise ValueError(
            f"required tool ID '{required_tool_id}' not found in toolIds"
        )

    return None

