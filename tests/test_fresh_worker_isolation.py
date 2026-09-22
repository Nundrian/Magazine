"""Focused tests for Fresh Worker completion-body isolation.

Proves that the completion request body sent to POST /api/chat/completions
is isolated from Magazine and unrelated Open WebUI features:

- ``tools`` is an empty list (no Magazine Tool ID, no unrelated Tool ID)
- ``features`` disables web_search, code_interpreter, image_generation, memory
- ``background_tasks`` disables title_generation, tags_generation,
  follow_up_generation
- the worker prompt and selected worker model remain unchanged by the
  isolation settings
"""

from __future__ import annotations

import copy
import json

import pytest

from magazine.workspace_tools.fresh_worker import Tools


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CHAT_ID = "chat-abc123"
ASSISTANT_ID = "asst-456def"
MODEL_ID = "hf.co/ggml-org/Qwen3.6-35B-A3B-GGUF:Q4_K_M"
PROMPT = "Summarize the article in three bullet points."
SESSION_ID = "sess-789ghi"

# Tool IDs that must NOT appear in the completion body.
MAGAZINE_TOOL_ID = "fresh_worker"
UNRELATED_TOOL_IDS = [
    "development_toolkit",
    "dev_run_command",
    "dev_read_file",
    "dev_write_file",
    "dev_glob_search",
    "dev_grep_search",
    "dev_list_files",
    "dev_kill_process",
    "dev_get_process_status",
    "dev_replace_file_content",
    "test_open_terminal_auth",
    "test_open_terminal_execute",
    "diagnose_runtime_config",
]

# The exact isolation settings required in the completion body.
REQUIRED_FEATURES = {
    "web_search": False,
    "code_interpreter": False,
    "image_generation": False,
    "memory": False,
}
REQUIRED_BACKGROUND_TASKS = {
    "title_generation": False,
    "tags_generation": False,
    "follow_up_generation": False,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tools() -> Tools:
    """Create a Tools instance with the default worker model."""
    return Tools()


def _completion_body() -> dict:
    """Build the completion body under test."""
    return _make_tools()._completion_body(
        CHAT_ID, ASSISTANT_ID, MODEL_ID, PROMPT, SESSION_ID
    )


def _all_string_values(obj) -> list[str]:
    """Recursively collect every string value in a JSON-like structure."""
    values: list[str] = []
    if isinstance(obj, str):
        values.append(obj)
    elif isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(key, str):
                values.append(key)
            values.extend(_all_string_values(value))
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            values.extend(_all_string_values(item))
    return values


# ---------------------------------------------------------------------------
# 1. tools is an empty list
# ---------------------------------------------------------------------------

def test_completion_body_tools_is_empty_list():
    body = _completion_body()
    assert body["tools"] == []
    assert isinstance(body["tools"], list)
    assert len(body["tools"]) == 0


# ---------------------------------------------------------------------------
# 2. features contains exactly the required disabled settings
# ---------------------------------------------------------------------------

def test_completion_body_features_contains_required_disabled_settings():
    body = _completion_body()
    features = body["features"]
    assert isinstance(features, dict)
    for key, expected in REQUIRED_FEATURES.items():
        assert key in features, f"features missing key: {key}"
        assert features[key] is expected, (
            f"features[{key!r}] is {features[key]!r}, expected {expected!r}"
        )


def test_completion_body_features_contains_exactly_required_keys():
    body = _completion_body()
    assert set(body["features"].keys()) == set(REQUIRED_FEATURES.keys())


# ---------------------------------------------------------------------------
# 3. background_tasks contains exactly the required disabled settings
# ---------------------------------------------------------------------------

def test_completion_body_background_tasks_contains_required_disabled_settings():
    body = _completion_body()
    background_tasks = body["background_tasks"]
    assert isinstance(background_tasks, dict)
    for key, expected in REQUIRED_BACKGROUND_TASKS.items():
        assert key in background_tasks, f"background_tasks missing key: {key}"
        assert background_tasks[key] is expected, (
            f"background_tasks[{key!r}] is {background_tasks[key]!r}, "
            f"expected {expected!r}"
        )


def test_completion_body_background_tasks_contains_exactly_required_keys():
    body = _completion_body()
    assert set(body["background_tasks"].keys()) == set(REQUIRED_BACKGROUND_TASKS.keys())


# ---------------------------------------------------------------------------
# 4. no Magazine Tool ID is added to the completion body
# ---------------------------------------------------------------------------

def test_completion_body_contains_no_magazine_tool_id():
    body = _completion_body()
    assert MAGAZINE_TOOL_ID not in body["tools"]
    # The Magazine Tool ID must not appear anywhere in the body.
    assert MAGAZINE_TOOL_ID not in _all_string_values(body)


# ---------------------------------------------------------------------------
# 5. no unrelated Tool ID is added to the completion body
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tool_id", UNRELATED_TOOL_IDS)
def test_completion_body_contains_no_unrelated_tool_id(tool_id):
    body = _completion_body()
    assert tool_id not in body["tools"]
    # The unrelated Tool ID must not appear anywhere in the body.
    assert tool_id not in _all_string_values(body)


# ---------------------------------------------------------------------------
# 6. the worker prompt remains unchanged by the isolation settings
# ---------------------------------------------------------------------------

def test_completion_body_prompt_is_unchanged():
    body = _completion_body()
    messages = body["messages"]
    assert isinstance(messages, list)
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == PROMPT


def test_completion_body_prompt_is_not_mutated_by_isolation_settings():
    """The prompt value is the exact string passed in, not a modified copy."""
    body = _completion_body()
    assert body["messages"][0]["content"] is PROMPT or body["messages"][0]["content"] == PROMPT
    # Ensure the prompt does not contain any isolation-related text.
    assert "web_search" not in body["messages"][0]["content"]
    assert "code_interpreter" not in body["messages"][0]["content"]
    assert "image_generation" not in body["messages"][0]["content"]
    assert "memory" not in body["messages"][0]["content"]
    assert "title_generation" not in body["messages"][0]["content"]
    assert "tags_generation" not in body["messages"][0]["content"]
    assert "follow_up_generation" not in body["messages"][0]["content"]


# ---------------------------------------------------------------------------
# 7. the selected worker model remains unchanged by the isolation settings
# ---------------------------------------------------------------------------

def test_completion_body_model_is_unchanged():
    body = _completion_body()
    assert body["model"] == MODEL_ID


def test_completion_body_model_is_not_mutated_by_isolation_settings():
    """The model value is the exact string passed in, not a modified copy."""
    body = _completion_body()
    assert body["model"] == MODEL_ID
    # Ensure the model does not contain any isolation-related text.
    assert "web_search" not in body["model"]
    assert "code_interpreter" not in body["model"]
    assert "image_generation" not in body["model"]
    assert "memory" not in body["model"]
    assert "title_generation" not in body["model"]
    assert "tags_generation" not in body["model"]
    assert "follow_up_generation" not in body["model"]


# ---------------------------------------------------------------------------
# 8. the isolation settings do not alter the core completion fields
# ---------------------------------------------------------------------------

def test_completion_body_core_fields_unchanged():
    """The core fields (chat_id, id, model, stream, session_id) are
    unaffected by the isolation settings."""
    body = _completion_body()
    assert body["chat_id"] == CHAT_ID
    assert body["id"] == ASSISTANT_ID
    assert body["model"] == MODEL_ID
    assert body["stream"] is True
    assert body["session_id"] == SESSION_ID


# ---------------------------------------------------------------------------
# 9. the isolation settings are stable across multiple calls
# ---------------------------------------------------------------------------

def test_completion_body_isolation_settings_are_stable_across_calls():
    """Calling _completion_body multiple times yields identical isolation
    settings each time."""
    bodies = [_completion_body() for _ in range(3)]
    for body in bodies:
        assert body["tools"] == []
        assert body["features"] == REQUIRED_FEATURES
        assert body["background_tasks"] == REQUIRED_BACKGROUND_TASKS


# ---------------------------------------------------------------------------
# 10. the isolation settings are not affected by the prompt or model values
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "model_id, prompt",
    [
        ("gpt-4o", "Hello"),
        ("claude-3.5-sonnet", "Write a poem about the ocean"),
        ("llama-3.1-70b", "Explain quantum entanglement simply"),
    ],
)
def test_completion_body_isolation_settings_independent_of_model_and_prompt(
    model_id, prompt
):
    body = _make_tools()._completion_body(
        CHAT_ID, ASSISTANT_ID, model_id, prompt, SESSION_ID
    )
    assert body["tools"] == []
    assert body["features"] == REQUIRED_FEATURES
    assert body["background_tasks"] == REQUIRED_BACKGROUND_TASKS
    # Prompt and model are passed through unchanged.
    assert body["model"] == model_id
    assert body["messages"][0]["content"] == prompt
