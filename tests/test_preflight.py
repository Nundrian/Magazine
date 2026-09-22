"""Tests for magazine.preflight.require_exact_ids."""

import pytest
from magazine.preflight import (
    require_exact_ids,
    extract_model_ids,
    extract_tool_ids,
    extract_chat_terminal_ids,
    validate_model_runtime_meta,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
AVAILABLE = ["alpha", "beta", "gamma"]


# ---------------------------------------------------------------------------
# 1. all required IDs present is accepted
# ---------------------------------------------------------------------------
def test_all_required_present():
    """When every required ID is in the available set, no error is raised."""
    result = require_exact_ids(["alpha", "beta"], AVAILABLE, "channels")
    assert result is None


# ---------------------------------------------------------------------------
# 2. one missing ID is rejected
# ---------------------------------------------------------------------------
def test_one_missing_id():
    """A single missing ID raises ValueError with that ID named."""
    with pytest.raises(ValueError, match="missing required ID 'delta'"):
        require_exact_ids(["alpha", "delta"], AVAILABLE, "channels")


# ---------------------------------------------------------------------------
# 3. multiple missing IDs are rejected
# ---------------------------------------------------------------------------
def test_multiple_missing_ids():
    """Two or more missing IDs raise ValueError listing them all."""
    with pytest.raises(ValueError, match="missing required IDs"):
        require_exact_ids(["alpha", "delta", "epsilon"], AVAILABLE, "channels")


# ---------------------------------------------------------------------------
# 4. similar but non-exact ID is rejected
# ---------------------------------------------------------------------------
def test_similar_but_non_exact_id_rejected():
    """An ID that is *almost* a match (e.g. different case or suffix) is
    NOT accepted — matching is strict / exact."""
    with pytest.raises(ValueError, match="missing required ID"):
        require_exact_ids(["Alpha"], AVAILABLE, "channels")

    with pytest.raises(ValueError, match="missing required ID"):
        require_exact_ids(["alpha-1"], AVAILABLE, "channels")


# ---------------------------------------------------------------------------
# 5. empty or whitespace-only label is rejected
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bad_label", ["", "  ", "\t\n"])
def test_empty_or_whitespace_label_rejected(bad_label):
    """A label that is empty or only whitespace raises ValueError."""
    with pytest.raises(ValueError, match="label must be a non-empty string"):
        require_exact_ids(["alpha"], AVAILABLE, bad_label)


# ---------------------------------------------------------------------------
# 6. bare string required_ids is rejected
# ---------------------------------------------------------------------------
def test_bare_string_required_ids_rejected():
    """Passing a bare str as required_ids raises ValueError."""
    with pytest.raises(ValueError, match="required_ids must be a collection"):
        require_exact_ids("alpha", AVAILABLE, "channels")


# ---------------------------------------------------------------------------
# 7. bare string available_ids is rejected
# ---------------------------------------------------------------------------
def test_bare_string_available_ids_rejected():
    """Passing a bare str as available_ids raises ValueError."""
    with pytest.raises(ValueError, match="available_ids must be a collection"):
        require_exact_ids(["alpha"], "alpha", "channels")


# ---------------------------------------------------------------------------
# 8. non-string member in required_ids is rejected
# ---------------------------------------------------------------------------
def test_non_string_member_in_required_ids_rejected():
    """A non-string element inside required_ids raises ValueError."""
    with pytest.raises(ValueError, match="every item in required_ids must be a string"):
        require_exact_ids(["alpha", 42], AVAILABLE, "channels")


# ---------------------------------------------------------------------------
# 9. non-string member in available_ids is rejected
# ---------------------------------------------------------------------------
def test_non_string_member_in_available_ids_rejected():
    """A non-string element inside available_ids raises ValueError."""
    with pytest.raises(ValueError, match="every item in available_ids must be a string"):
        require_exact_ids(["alpha"], ["alpha", 42], "channels")


# ---------------------------------------------------------------------------
# 10. a generator passed as required_ids is rejected
# ---------------------------------------------------------------------------
def test_generator_required_ids_rejected():
    """A generator is not a Collection and must be rejected."""
    with pytest.raises(ValueError, match="required_ids must be a collection"):
        require_exact_ids((x for x in ["alpha"]), AVAILABLE, "channels")


# ---------------------------------------------------------------------------
# 11. None passed as available_ids is rejected
# ---------------------------------------------------------------------------
def test_none_available_ids_rejected():
    """None is not a Collection and must be rejected."""
    with pytest.raises(ValueError, match="available_ids must be a collection"):
        require_exact_ids(["alpha"], None, "channels")


# ===================================================================
# extract_model_ids
# ===================================================================

# 12. valid model payload returns exact IDs
def test_valid_model_payload_returns_exact_ids():
    """A well-formed model payload returns the IDs unchanged."""
    payload = {
        "data": [
            {"id": "gpt-4o"},
            {"id": "claude-3.5-sonnet"},
            {"id": "llama-3.1-70b"},
        ]
    }
    result = extract_model_ids(payload)
    assert result == ["gpt-4o", "claude-3.5-sonnet", "llama-3.1-70b"]


# 13. malformed model payload is rejected
def test_malformed_model_payload_rejected():
    """A payload that is not a dict raises ValueError."""
    with pytest.raises(ValueError, match="payload must be a dict"):
        extract_model_ids("not a dict")

    with pytest.raises(ValueError, match="payload must be a dict"):
        extract_model_ids([{"id": "x"}])

    with pytest.raises(ValueError, match="payload must contain a 'data' key"):
        extract_model_ids({"models": []})

    with pytest.raises(ValueError, match="payload\\['data'\\] must be a list"):
        extract_model_ids({"data": "not a list"})


# 14. model item missing string id is rejected
def test_model_item_missing_string_id_rejected():
    """Items without a string 'id' key raise ValueError."""
    # missing 'id' key entirely
    with pytest.raises(ValueError, match="data\\[0\\] must contain an 'id' key"):
        extract_model_ids({"data": [{"name": "gpt-4o"}]})

    # 'id' is not a string
    with pytest.raises(ValueError, match="data\\[0\\]\\['id'\\] must be a string"):
        extract_model_ids({"data": [{"id": 123}]})

    # item is not a dict
    with pytest.raises(ValueError, match="data\\[0\\] must be a dict"):
        extract_model_ids({"data": ["gpt-4o"]})


# ===================================================================
# extract_tool_ids
# ===================================================================

# 15. valid Tool payload returns exact IDs
def test_valid_tool_payload_returns_exact_ids():
    """A well-formed tool payload returns the IDs unchanged."""
    payload = [
        {"id": "tool-search"},
        {"id": "tool-code-interpreter"},
    ]
    result = extract_tool_ids(payload)
    assert result == ["tool-search", "tool-code-interpreter"]


# 16. malformed Tool payload is rejected
def test_malformed_tool_payload_rejected():
    """A payload that is not a list raises ValueError."""
    with pytest.raises(ValueError, match="payload must be a list"):
        extract_tool_ids("not a list")

    with pytest.raises(ValueError, match="payload must be a list"):
        extract_tool_ids({"data": []})


# 17. Tool item missing string id is rejected
def test_tool_item_missing_string_id_rejected():
    """Items without a string 'id' key raise ValueError."""
    # missing 'id' key entirely
    with pytest.raises(ValueError, match="payload\\[0\\] must contain an 'id' key"):
        extract_tool_ids([{"name": "search"}])

    # 'id' is not a string
    with pytest.raises(ValueError, match="payload\\[0\\]\\['id'\\] must be a string"):
        extract_tool_ids([{"id": 42}])

    # item is not a dict
    with pytest.raises(ValueError, match="payload\\[0\\] must be a dict"):
        extract_tool_ids(["tool-search"])


# ===================================================================
# extract_chat_terminal_ids
# ===================================================================

# 18. valid terminal payload returns only chat-enabled IDs
def test_valid_terminal_payload_returns_only_chat_enabled_ids():
    """Only terminals where contexts.chat is True are returned."""
    payload = [
        {"id": "term-1", "contexts": {"chat": True, "shell": False}},
        {"id": "term-2", "contexts": {"chat": False, "shell": True}},
        {"id": "term-3", "contexts": {"chat": True}},
    ]
    result = extract_chat_terminal_ids(payload)
    assert result == ["term-1", "term-3"]


# 19. empty payload returns empty list
def test_empty_terminal_payload_returns_empty_list():
    assert extract_chat_terminal_ids([]) == []


# 20. malformed terminal payload is rejected
def test_malformed_terminal_payload_rejected():
    """A payload that is not a list raises ValueError."""
    with pytest.raises(ValueError, match="payload must be a list"):
        extract_chat_terminal_ids("not a list")

    with pytest.raises(ValueError, match="payload must be a list"):
        extract_chat_terminal_ids({"data": []})


# 21. terminal item missing string id is rejected
def test_terminal_item_missing_string_id_rejected():
    """Items without a string 'id' key raise ValueError."""
    with pytest.raises(ValueError, match="payload\\[0\\] must contain an 'id' key"):
        extract_chat_terminal_ids([{"contexts": {"chat": True}}])

    with pytest.raises(ValueError, match="payload\\[0\\]\\['id'\\] must be a string"):
        extract_chat_terminal_ids([{"id": 42, "contexts": {"chat": True}}])

    with pytest.raises(ValueError, match="payload\\[0\\] must be a dict"):
        extract_chat_terminal_ids(["term-1"])


# 22. terminal item missing or malformed contexts is rejected
def test_terminal_item_missing_or_malformed_contexts_rejected():
    """Items without a valid 'contexts' dict raise ValueError."""
    with pytest.raises(ValueError, match="payload\\[0\\] must contain a 'contexts' key"):
        extract_chat_terminal_ids([{"id": "t1"}])

    with pytest.raises(ValueError, match="payload\\[0\\]\\['contexts'\\] must be a dict"):
        extract_chat_terminal_ids([{"id": "t1", "contexts": "not a dict"}])

    with pytest.raises(ValueError, match="payload\\[0\\]\\['contexts'\\] must contain a 'chat' key"):
        extract_chat_terminal_ids([{"id": "t1", "contexts": {"shell": True}}])

    with pytest.raises(ValueError, match="payload\\[0\\]\\['contexts'\\]\\['chat'\\] must be a boolean"):
        extract_chat_terminal_ids([{"id": "t1", "contexts": {"chat": "yes"}}])


# ===================================================================
# validate_model_runtime_meta
# ===================================================================

# 23. valid metadata passes
def test_validate_model_runtime_meta_valid():
    """A well-formed payload with terminal=True and the required tool ID passes."""
    payload = {
        "meta": {
            "capabilities": {"terminal": True},
            "toolIds": ["dev_run_command", "dev_read_file"],
        }
    }
    result = validate_model_runtime_meta(payload, "dev_run_command")
    assert result is None


# 24. terminal capability false or missing is rejected
def test_validate_model_runtime_meta_terminal_false_or_missing():
    """terminal=False or a missing terminal key raises ValueError."""
    # terminal is False
    payload_false = {
        "meta": {
            "capabilities": {"terminal": False},
            "toolIds": ["dev_run_command"],
        }
    }
    with pytest.raises(ValueError, match="must be exactly True"):
        validate_model_runtime_meta(payload_false, "dev_run_command")

    # terminal key missing entirely
    payload_missing = {
        "meta": {
            "capabilities": {"other": True},
            "toolIds": ["dev_run_command"],
        }
    }
    with pytest.raises(ValueError, match="must contain a 'terminal' key"):
        validate_model_runtime_meta(payload_missing, "dev_run_command")


# 25. malformed toolIds is rejected
def test_validate_model_runtime_meta_malformed_tool_ids():
    """toolIds that is not a list, or contains non-string items, raises ValueError."""
    # toolIds is not a list
    payload_not_list = {
        "meta": {
            "capabilities": {"terminal": True},
            "toolIds": "dev_run_command",
        }
    }
    with pytest.raises(ValueError, match="toolIds'\\] must be a list"):
        validate_model_runtime_meta(payload_not_list, "dev_run_command")

    # toolIds contains a non-string item
    payload_non_str = {
        "meta": {
            "capabilities": {"terminal": True},
            "toolIds": ["dev_run_command", 42],
        }
    }
    with pytest.raises(ValueError, match="toolIds'\\]\\[1\\] must be a string"):
        validate_model_runtime_meta(payload_non_str, "dev_run_command")


# 26. required tool missing is rejected
def test_validate_model_runtime_meta_required_tool_missing():
    """A required tool ID not present in toolIds raises ValueError."""
    payload = {
        "meta": {
            "capabilities": {"terminal": True},
            "toolIds": ["dev_read_file", "dev_glob_search"],
        }
    }
    with pytest.raises(ValueError, match="required tool ID 'dev_run_command' not found"):
        validate_model_runtime_meta(payload, "dev_run_command")


# 27. exact tool ID matching (case-sensitive, no fuzzy)
def test_validate_model_runtime_meta_exact_tool_id_matching():
    """Tool ID matching is exact — case differences or suffixes are rejected."""
    payload = {
        "meta": {
            "capabilities": {"terminal": True},
            "toolIds": ["dev_run_command"],
        }
    }
    # different case
    with pytest.raises(ValueError, match="required tool ID 'DEV_RUN_COMMAND' not found"):
        validate_model_runtime_meta(payload, "DEV_RUN_COMMAND")

    # extra suffix
    with pytest.raises(ValueError, match="required tool ID 'dev_run_command_v2' not found"):
        validate_model_runtime_meta(payload, "dev_run_command_v2")

    # exact match passes
    assert validate_model_runtime_meta(payload, "dev_run_command") is None

