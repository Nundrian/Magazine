"""Tests for magazine.runtime_preflight.validate_live_ids and validate_live_runtime_pair."""

from unittest.mock import patch

import pytest

from magazine.runtime_preflight import (
    validate_live_ids,
    validate_live_runtime_pair,
    validate_runtime_terminal,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

BASE_URL = "http://localhost:8080"
API_KEY = "test-key"


# ---------------------------------------------------------------------------
# Test: valid required model and tool IDs → passes
# ---------------------------------------------------------------------------

@patch("magazine.runtime_preflight.discover_model_ids")
@patch("magazine.runtime_preflight.discover_tool_ids")
def test_valid_ids_passes(mock_tool_ids, mock_model_ids):
    mock_model_ids.return_value = ["gpt-4", "gpt-3.5"]
    mock_tool_ids.return_value = ["web_search", "code_interpreter"]

    result = validate_live_ids(
        BASE_URL,
        API_KEY,
        required_model_ids=["gpt-4"],
        required_tool_ids=["web_search"],
    )

    assert result is None
    mock_model_ids.assert_called_once_with(BASE_URL, API_KEY)
    mock_tool_ids.assert_called_once_with(BASE_URL, API_KEY)


# ---------------------------------------------------------------------------
# Test: missing required model → raises ValueError
# ---------------------------------------------------------------------------

@patch("magazine.runtime_preflight.discover_model_ids")
@patch("magazine.runtime_preflight.discover_tool_ids")
def test_missing_required_model_raises(mock_tool_ids, mock_model_ids):
    mock_model_ids.return_value = ["gpt-3.5"]
    mock_tool_ids.return_value = ["web_search"]

    with pytest.raises(ValueError, match="models: missing required ID 'gpt-4'"):
        validate_live_ids(
            BASE_URL,
            API_KEY,
            required_model_ids=["gpt-4"],
            required_tool_ids=["web_search"],
        )

    # Prove that when model validation fails, Tool discovery is never called.
    mock_tool_ids.assert_not_called()


# ---------------------------------------------------------------------------
# Test: missing required tool → raises ValueError
# ---------------------------------------------------------------------------

@patch("magazine.runtime_preflight.discover_model_ids")
@patch("magazine.runtime_preflight.discover_tool_ids")
def test_missing_required_tool_raises(mock_tool_ids, mock_model_ids):
    mock_model_ids.return_value = ["gpt-4"]
    mock_tool_ids.return_value = ["code_interpreter"]

    with pytest.raises(ValueError, match="tools: missing required ID 'web_search'"):
        validate_live_ids(
            BASE_URL,
            API_KEY,
            required_model_ids=["gpt-4"],
            required_tool_ids=["web_search"],
        )


# ---------------------------------------------------------------------------
# Tests: validate_live_runtime_pair
# ---------------------------------------------------------------------------

MODEL_ID = "gpt-4"
TOOL_ID = "web_search"


# ---------------------------------------------------------------------------
# Test: exact model and Tool IDs are passed to validate_live_ids()
# ---------------------------------------------------------------------------

@patch("magazine.runtime_preflight.discover_terminal_ids")
@patch("magazine.runtime_preflight.discover_model_runtime_meta")
@patch("magazine.runtime_preflight.validate_live_ids")
def test_runtime_pair_passes_exact_ids_to_validate_live_ids(
    mock_validate, mock_meta, mock_terminals
):
    mock_validate.return_value = None
    mock_meta.return_value = {"id": MODEL_ID, "meta": {"toolIds": [TOOL_ID]}}
    mock_terminals.return_value = ["terminal-1"]

    result = validate_live_runtime_pair(BASE_URL, API_KEY, MODEL_ID, TOOL_ID)

    assert result is None
    # Prove the exact model and Tool IDs are passed to validate_live_ids().
    mock_validate.assert_called_once_with(BASE_URL, API_KEY, [MODEL_ID], [TOOL_ID])
    # Then prove the same exact model and Tool IDs are passed to
    # discover_model_runtime_meta().
    mock_meta.assert_called_once_with(BASE_URL, API_KEY, MODEL_ID, TOOL_ID)
    # Then prove terminal discovery is reached only after metadata discovery.
    mock_terminals.assert_called_once_with(BASE_URL, API_KEY)


# ---------------------------------------------------------------------------
# Test: the same exact model and Tool IDs are passed to
# discover_model_runtime_meta()
# ---------------------------------------------------------------------------

@patch("magazine.runtime_preflight.discover_terminal_ids")
@patch("magazine.runtime_preflight.discover_model_runtime_meta")
@patch("magazine.runtime_preflight.discover_model_ids")
@patch("magazine.runtime_preflight.discover_tool_ids")
def test_runtime_pair_passes_exact_ids_to_discover_meta(
    mock_tool_ids, mock_model_ids, mock_meta, mock_terminals
):
    mock_model_ids.return_value = [MODEL_ID]
    mock_tool_ids.return_value = [TOOL_ID]
    mock_meta.return_value = {"id": MODEL_ID, "meta": {"toolIds": [TOOL_ID]}}
    mock_terminals.return_value = ["terminal-1"]

    validate_live_runtime_pair(BASE_URL, API_KEY, MODEL_ID, TOOL_ID)

    mock_meta.assert_called_once_with(BASE_URL, API_KEY, MODEL_ID, TOOL_ID)
    mock_terminals.assert_called_once_with(BASE_URL, API_KEY)


# ---------------------------------------------------------------------------
# Test: model/Tool ID preflight failure prevents runtime metadata discovery
# ---------------------------------------------------------------------------

@patch("magazine.runtime_preflight.discover_terminal_ids")
@patch("magazine.runtime_preflight.discover_model_runtime_meta")
@patch("magazine.runtime_preflight.discover_model_ids")
@patch("magazine.runtime_preflight.discover_tool_ids")
def test_runtime_pair_preflight_failure_prevents_meta_discovery(
    mock_tool_ids, mock_model_ids, mock_meta, mock_terminals
):
    # Model ID is missing from live discovery → preflight fails.
    mock_model_ids.return_value = ["gpt-3.5"]
    mock_tool_ids.return_value = [TOOL_ID]

    with pytest.raises(ValueError, match="models: missing required ID 'gpt-4'"):
        validate_live_runtime_pair(BASE_URL, API_KEY, MODEL_ID, TOOL_ID)

    # Runtime metadata discovery must never be reached.
    mock_meta.assert_not_called()
    # Terminal discovery must never be reached either.
    mock_terminals.assert_not_called()


@patch("magazine.runtime_preflight.discover_terminal_ids")
@patch("magazine.runtime_preflight.discover_model_runtime_meta")
@patch("magazine.runtime_preflight.discover_model_ids")
@patch("magazine.runtime_preflight.discover_tool_ids")
def test_runtime_pair_tool_preflight_failure_prevents_meta_discovery(
    mock_tool_ids, mock_model_ids, mock_meta, mock_terminals
):
    # Tool ID is missing from live discovery → preflight fails.
    mock_model_ids.return_value = [MODEL_ID]
    mock_tool_ids.return_value = ["code_interpreter"]

    with pytest.raises(ValueError, match="tools: missing required ID 'web_search'"):
        validate_live_runtime_pair(BASE_URL, API_KEY, MODEL_ID, TOOL_ID)

    mock_meta.assert_not_called()
    mock_terminals.assert_not_called()


# ---------------------------------------------------------------------------
# Test: runtime metadata failure is propagated
# ---------------------------------------------------------------------------

@patch("magazine.runtime_preflight.discover_terminal_ids")
@patch("magazine.runtime_preflight.discover_model_runtime_meta")
@patch("magazine.runtime_preflight.discover_model_ids")
@patch("magazine.runtime_preflight.discover_tool_ids")
def test_runtime_pair_meta_failure_propagates(
    mock_tool_ids, mock_model_ids, mock_meta, mock_terminals
):
    mock_model_ids.return_value = [MODEL_ID]
    mock_tool_ids.return_value = [TOOL_ID]
    mock_meta.side_effect = ValueError("request failed: HTTP 500 Internal Server Error")

    with pytest.raises(ValueError, match="request failed: HTTP 500"):
        validate_live_runtime_pair(BASE_URL, API_KEY, MODEL_ID, TOOL_ID)

    # Metadata discovery was attempted (preflight succeeded) and its error
    # propagated out of validate_live_runtime_pair.
    mock_meta.assert_called_once_with(BASE_URL, API_KEY, MODEL_ID, TOOL_ID)
    # Terminal discovery must never be reached when metadata validation fails.
    mock_terminals.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: validate_runtime_terminal
# ---------------------------------------------------------------------------

TERMINAL_ID = "terminal-1"


def test_validate_runtime_terminal_no_available_terminal_raises():
    with pytest.raises(ValueError, match="at least one chat enabled terminal ID"):
        validate_runtime_terminal({"meta": {}}, [])


def test_validate_runtime_terminal_no_terminal_id_succeeds():
    result = validate_runtime_terminal({"meta": {}}, [TERMINAL_ID])
    assert result is None


def test_validate_runtime_terminal_exact_match_succeeds():
    result = validate_runtime_terminal(
        {"meta": {"terminalId": TERMINAL_ID}}, [TERMINAL_ID, "terminal-2"]
    )
    assert result is None


def test_validate_runtime_terminal_missing_terminal_id_raises():
    with pytest.raises(ValueError, match="does not match"):
        validate_runtime_terminal(
            {"meta": {"terminalId": "terminal-9"}}, [TERMINAL_ID]
        )


def test_validate_runtime_terminal_non_string_terminal_id_raises():
    with pytest.raises(ValueError, match="terminalId.*must be a string"):
        validate_runtime_terminal(
            {"meta": {"terminalId": 123}}, [TERMINAL_ID]
        )


@patch("magazine.runtime_preflight.discover_terminal_ids")
@patch("magazine.runtime_preflight.discover_model_runtime_meta")
@patch("magazine.runtime_preflight.discover_model_ids")
@patch("magazine.runtime_preflight.discover_tool_ids")
def test_runtime_pair_terminal_discovery_not_reached_on_model_preflight_failure(
    mock_tool_ids, mock_model_ids, mock_meta, mock_terminals
):
    # Model ID is missing from live discovery → preflight fails.
    mock_model_ids.return_value = ["gpt-3.5"]
    mock_tool_ids.return_value = [TOOL_ID]

    with pytest.raises(ValueError, match="models: missing required ID 'gpt-4'"):
        validate_live_runtime_pair(BASE_URL, API_KEY, MODEL_ID, TOOL_ID)

    # Terminal discovery must never be reached.
    mock_terminals.assert_not_called()


@patch("magazine.runtime_preflight.discover_terminal_ids")
@patch("magazine.runtime_preflight.discover_model_runtime_meta")
@patch("magazine.runtime_preflight.discover_model_ids")
@patch("magazine.runtime_preflight.discover_tool_ids")
def test_runtime_pair_terminal_discovery_not_reached_on_meta_failure(
    mock_tool_ids, mock_model_ids, mock_meta, mock_terminals
):
    mock_model_ids.return_value = [MODEL_ID]
    mock_tool_ids.return_value = [TOOL_ID]
    mock_meta.side_effect = ValueError("request failed: HTTP 500 Internal Server Error")

    with pytest.raises(ValueError, match="request failed: HTTP 500"):
        validate_live_runtime_pair(BASE_URL, API_KEY, MODEL_ID, TOOL_ID)

    # Terminal discovery must never be reached when metadata validation fails.
    mock_terminals.assert_not_called()
