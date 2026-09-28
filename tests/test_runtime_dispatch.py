"""Tests for magazine.runtime_dispatch.dispatch_after_preflight, dispatch_routed, and dispatch_runtime_pair."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from magazine.schema import MagazineSchemaError
from magazine.runtime_dispatch import (
    dispatch_after_preflight,
    dispatch_cartridge,
    dispatch_routed,
    dispatch_runtime_pair,
)


BASE_URL = "http://localhost:8080"
API_KEY = "test-key"
PROMPT = "Hello, world!"
MODEL_MAP = {"edit": "gpt-4", "summarize": "claude-3"}
TOOL_MAP = {"terminal": "bash", "web": "web_search"}


# ---------------------------------------------------------------------------
# dispatch_after_preflight tests (existing)
# ---------------------------------------------------------------------------

@patch("magazine.runtime_dispatch.validate_live_ids")
def test_valid_preflight_awaits_dispatch(mock_validate):
    """When preflight succeeds, dispatch is called exactly once."""
    mock_validate.return_value = None

    mock_dispatch = AsyncMock(return_value="dispatched result")

    result = asyncio.run(
        dispatch_after_preflight(
            BASE_URL,
            API_KEY,
            required_model_ids=["gpt-4"],
            required_tool_ids=["web_search"],
            dispatch=mock_dispatch,
            prompt=PROMPT,
        )
    )

    assert result == "dispatched result"
    # The preflight-only wrapper validates all required IDs, then dispatches
    # with the first (primary) required model ID.
    mock_dispatch.assert_awaited_once_with(PROMPT, "gpt-4")
    mock_validate.assert_called_once_with(
        BASE_URL, API_KEY, ["gpt-4"], ["web_search"]
    )


@patch("magazine.runtime_dispatch.validate_live_ids")
def test_invalid_preflight_raises_and_skips_dispatch(mock_validate):
    """When preflight fails, ValueError propagates and dispatch is never called."""
    mock_validate.side_effect = ValueError(
        "models: missing required ID 'gpt-4'"
    )

    mock_dispatch = AsyncMock(return_value="should not happen")

    with pytest.raises(ValueError, match="models: missing required ID 'gpt-4'"):
        asyncio.run(
            dispatch_after_preflight(
                BASE_URL,
                API_KEY,
                required_model_ids=["gpt-4"],
                required_tool_ids=["web_search"],
                dispatch=mock_dispatch,
                prompt=PROMPT,
            )
        )

    mock_dispatch.assert_not_awaited()


# ---------------------------------------------------------------------------
# dispatch_routed tests
# ---------------------------------------------------------------------------

@patch("magazine.runtime_dispatch.dispatch_runtime_pair", new_callable=AsyncMock)
@patch("magazine.runtime_dispatch.resolve_model", return_value="gpt-4")
@patch("magazine.runtime_dispatch.resolve_tool", return_value="bash")
def test_routed_passes_exact_ids(mock_resolve_tool, mock_resolve_model, mock_runtime_pair):
    """dispatch_routed passes the exact resolved model and Tool IDs to dispatch_runtime_pair."""
    mock_runtime_pair.return_value = "runtime pair result"

    mock_dispatch = AsyncMock(return_value="dispatched result")

    result = asyncio.run(
        dispatch_routed(
            BASE_URL,
            API_KEY,
            job_type="edit",
            tool_role="terminal",
            model_map=MODEL_MAP,
            tool_map=TOOL_MAP,
            dispatch=mock_dispatch,
            prompt=PROMPT,
        )
    )

    assert result == "runtime pair result"
    mock_resolve_model.assert_called_once_with("edit", MODEL_MAP)
    mock_resolve_tool.assert_called_once_with("terminal", TOOL_MAP)
    mock_runtime_pair.assert_awaited_once_with(
        BASE_URL,
        API_KEY,
        "gpt-4",
        "bash",
        dispatch=mock_dispatch,
        prompt=PROMPT,
    )


@patch("magazine.runtime_dispatch.dispatch_runtime_pair", new_callable=AsyncMock)
@patch("magazine.runtime_dispatch.resolve_model", return_value="claude-3")
@patch("magazine.runtime_dispatch.resolve_tool", return_value="web_search")
def test_routed_passes_different_ids(mock_resolve_tool, mock_resolve_model, mock_runtime_pair):
    """dispatch_routed passes the exact resolved model and Tool IDs to dispatch_runtime_pair."""
    mock_runtime_pair.return_value = "runtime pair result"

    mock_dispatch = AsyncMock(return_value="dispatched result")

    result = asyncio.run(
        dispatch_routed(
            BASE_URL,
            API_KEY,
            job_type="summarize",
            tool_role="web",
            model_map=MODEL_MAP,
            tool_map=TOOL_MAP,
            dispatch=mock_dispatch,
            prompt=PROMPT,
        )
    )

    assert result == "runtime pair result"
    mock_resolve_model.assert_called_once_with("summarize", MODEL_MAP)
    mock_resolve_tool.assert_called_once_with("web", TOOL_MAP)
    mock_runtime_pair.assert_awaited_once_with(
        BASE_URL,
        API_KEY,
        "claude-3",
        "web_search",
        dispatch=mock_dispatch,
        prompt=PROMPT,
    )


@patch("magazine.runtime_dispatch.dispatch_runtime_pair", new_callable=AsyncMock)
@patch("magazine.runtime_dispatch.resolve_model", return_value="gpt-4")
@patch("magazine.runtime_dispatch.resolve_tool", return_value="bash")
def test_routed_prompt_and_dispatch_passed_unchanged(mock_resolve_tool, mock_resolve_model, mock_runtime_pair):
    """dispatch_routed passes the prompt and dispatch callable to dispatch_runtime_pair unchanged."""
    mock_runtime_pair.return_value = "runtime pair result"

    mock_dispatch = AsyncMock(return_value="dispatched result")

    asyncio.run(
        dispatch_routed(
            BASE_URL,
            API_KEY,
            job_type="edit",
            tool_role="terminal",
            model_map=MODEL_MAP,
            tool_map=TOOL_MAP,
            dispatch=mock_dispatch,
            prompt=PROMPT,
        )
    )

    _, kwargs = mock_runtime_pair.call_args
    assert kwargs["dispatch"] is mock_dispatch
    assert kwargs["prompt"] is PROMPT


@patch("magazine.runtime_dispatch.dispatch_runtime_pair", new_callable=AsyncMock)
@patch("magazine.runtime_dispatch.resolve_model", return_value="gpt-4")
@patch("magazine.runtime_dispatch.resolve_tool", return_value="bash")
def test_routed_result_returned_unchanged(mock_resolve_tool, mock_resolve_model, mock_runtime_pair):
    """The result from dispatch_runtime_pair is returned unchanged."""
    sentinel = object()
    mock_runtime_pair.return_value = sentinel

    mock_dispatch = AsyncMock(return_value="dispatched result")

    result = asyncio.run(
        dispatch_routed(
            BASE_URL,
            API_KEY,
            job_type="edit",
            tool_role="terminal",
            model_map=MODEL_MAP,
            tool_map=TOOL_MAP,
            dispatch=mock_dispatch,
            prompt=PROMPT,
        )
    )

    assert result is sentinel


@patch("magazine.runtime_dispatch.dispatch_runtime_pair", new_callable=AsyncMock)
@patch("magazine.runtime_dispatch.resolve_model")
@patch("magazine.runtime_dispatch.resolve_tool")
def test_routed_unknown_job_type_does_not_call_runtime_pair(mock_resolve_tool, mock_resolve_model, mock_runtime_pair):
    """Unknown job_type raises before dispatch_runtime_pair is called."""
    mock_resolve_model.side_effect = KeyError("Unknown job_type: 'unknown_job'")

    mock_dispatch = AsyncMock(return_value="should not happen")

    with pytest.raises(KeyError, match="Unknown job_type: 'unknown_job'"):
        asyncio.run(
            dispatch_routed(
                BASE_URL,
                API_KEY,
                job_type="unknown_job",
                tool_role="terminal",
                model_map=MODEL_MAP,
                tool_map=TOOL_MAP,
                dispatch=mock_dispatch,
                prompt=PROMPT,
            )
        )

    mock_resolve_tool.assert_not_called()
    mock_runtime_pair.assert_not_awaited()
    mock_dispatch.assert_not_awaited()


# ---------------------------------------------------------------------------
# dispatch_runtime_pair tests
# ---------------------------------------------------------------------------

MODEL_ID = "gpt-4"
TOOL_ID = "web_search"


@patch("magazine.runtime_dispatch.validate_live_runtime_pair")
def test_runtime_pair_passes_exact_ids_to_validate_live_runtime_pair(mock_validate):
    """dispatch_runtime_pair passes the exact model and Tool IDs to validation."""
    mock_validate.return_value = None

    mock_dispatch = AsyncMock(return_value="dispatched result")

    asyncio.run(
        dispatch_runtime_pair(
            BASE_URL,
            API_KEY,
            MODEL_ID,
            TOOL_ID,
            dispatch=mock_dispatch,
            prompt=PROMPT,
        )
    )

    mock_validate.assert_called_once_with(BASE_URL, API_KEY, MODEL_ID, TOOL_ID)


@patch("magazine.runtime_dispatch.validate_live_runtime_pair")
def test_runtime_pair_successful_validation_dispatches_exactly_once(mock_validate):
    """When validation succeeds, dispatch is awaited exactly once with the resolved model ID."""
    mock_validate.return_value = None

    mock_dispatch = AsyncMock(return_value="dispatched result")

    asyncio.run(
        dispatch_runtime_pair(
            BASE_URL,
            API_KEY,
            MODEL_ID,
            TOOL_ID,
            dispatch=mock_dispatch,
            prompt=PROMPT,
        )
    )

    mock_dispatch.assert_awaited_once_with(PROMPT, MODEL_ID)


@patch("magazine.runtime_dispatch.validate_live_runtime_pair")
def test_runtime_pair_validation_failure_causes_zero_dispatches(mock_validate):
    """When validation fails, dispatch is never called."""
    mock_validate.side_effect = ValueError(
        "models: missing required ID 'gpt-4'"
    )

    mock_dispatch = AsyncMock(return_value="should not happen")

    with pytest.raises(ValueError, match="models: missing required ID 'gpt-4'"):
        asyncio.run(
            dispatch_runtime_pair(
                BASE_URL,
                API_KEY,
                MODEL_ID,
                TOOL_ID,
                dispatch=mock_dispatch,
                prompt=PROMPT,
            )
        )

    mock_dispatch.assert_not_awaited()


@patch("magazine.runtime_dispatch.validate_live_runtime_pair")
def test_runtime_pair_dispatch_result_returned_unchanged(mock_validate):
    """The dispatch result is returned unchanged."""
    mock_validate.return_value = None

    sentinel = object()
    mock_dispatch = AsyncMock(return_value=sentinel)

    result = asyncio.run(
        dispatch_runtime_pair(
            BASE_URL,
            API_KEY,
            MODEL_ID,
            TOOL_ID,
            dispatch=mock_dispatch,
            prompt=PROMPT,
        )
    )

    assert result is sentinel


@patch("magazine.runtime_dispatch.dispatch_runtime_pair", new_callable=AsyncMock)
@patch("magazine.runtime_dispatch.resolve_model")
@patch("magazine.runtime_dispatch.resolve_tool")
def test_routed_unknown_tool_role_does_not_call_runtime_pair(mock_resolve_tool, mock_resolve_model, mock_runtime_pair):
    """Unknown tool_role raises before dispatch_runtime_pair is called."""
    mock_resolve_model.return_value = "gpt-4"
    mock_resolve_tool.side_effect = KeyError("Unknown tool_role: 'unknown_role'")

    mock_dispatch = AsyncMock(return_value="should not happen")

    with pytest.raises(KeyError, match="Unknown tool_role: 'unknown_role'"):
        asyncio.run(
            dispatch_routed(
                BASE_URL,
                API_KEY,
                job_type="edit",
                tool_role="unknown_role",
                model_map=MODEL_MAP,
                tool_map=TOOL_MAP,
                dispatch=mock_dispatch,
                prompt=PROMPT,
            )
        )

    mock_runtime_pair.assert_not_awaited()
    mock_dispatch.assert_not_awaited()


# ---------------------------------------------------------------------------
# dispatch_cartridge integration boundary tests (S02)
# ---------------------------------------------------------------------------
# These prove the missing integration boundary:
#   validated cartridge -> exact job_type -> routed runtime dispatch
# The existing routed dispatch (dispatch_routed) is mocked so the tests prove
# ordering and exact argument transfer without invoking live Open WebUI
# discovery.
# ---------------------------------------------------------------------------

VALID_CARTRIDGE = {
    "id": "cart-1",
    "job_type": "summarize",
    "prompt": "Summarize the article.",
}


@patch("magazine.runtime_dispatch.dispatch_routed", new_callable=AsyncMock)
def test_valid_cartridge_exact_job_type_reaches_routing(mock_routed):
    """A valid cartridge's exact job_type is forwarded to dispatch_routed."""
    mock_routed.return_value = "routed result"
    mock_dispatch = AsyncMock(return_value="dispatched result")

    asyncio.run(
        dispatch_cartridge(
            BASE_URL,
            API_KEY,
            cartridge=VALID_CARTRIDGE,
            tool_role="web",
            model_map=MODEL_MAP,
            tool_map=TOOL_MAP,
            dispatch=mock_dispatch,
        )
    )

    mock_routed.assert_awaited_once()
    _, kwargs = mock_routed.call_args
    assert kwargs["job_type"] == VALID_CARTRIDGE["job_type"]
    assert kwargs["prompt"] == VALID_CARTRIDGE["prompt"]


@patch("magazine.runtime_dispatch.dispatch_routed", new_callable=AsyncMock)
def test_valid_cartridge_exact_prompt_reaches_routed_dispatch_unchanged(mock_routed):
    """A valid cartridge's exact prompt is forwarded to dispatch_routed unchanged."""
    mock_routed.return_value = "routed result"
    mock_dispatch = AsyncMock(return_value="dispatched result")

    asyncio.run(
        dispatch_cartridge(
            BASE_URL,
            API_KEY,
            cartridge=VALID_CARTRIDGE,
            tool_role="web",
            model_map=MODEL_MAP,
            tool_map=TOOL_MAP,
            dispatch=mock_dispatch,
        )
    )

    _, kwargs = mock_routed.call_args
    # Identity check: the exact prompt object is passed, not a copy/append.
    assert kwargs["prompt"] is VALID_CARTRIDGE["prompt"]
    # No Magazine state or other cartridge fields are appended to the prompt.
    assert kwargs["prompt"] == "Summarize the article."


@patch("magazine.runtime_dispatch.dispatch_routed", new_callable=AsyncMock)
def test_non_string_prompt_causes_zero_routed_dispatch(mock_routed):
    """A non-string prompt fails validation -> zero routed dispatch."""
    mock_dispatch = AsyncMock(return_value="should not happen")

    with pytest.raises(MagazineSchemaError):
        asyncio.run(
            dispatch_cartridge(
                BASE_URL,
                API_KEY,
                cartridge={
                    "id": "cart-1",
                    "job_type": "summarize",
                    "prompt": 12345,
                },
                tool_role="web",
                model_map=MODEL_MAP,
                tool_map=TOOL_MAP,
                dispatch=mock_dispatch,
            )
        )

    mock_routed.assert_not_awaited()
    mock_dispatch.assert_not_awaited()


@patch("magazine.runtime_dispatch.dispatch_routed", new_callable=AsyncMock)
def test_empty_prompt_causes_zero_routed_dispatch(mock_routed):
    """An empty prompt fails validation -> zero routed dispatch."""
    mock_dispatch = AsyncMock(return_value="should not happen")

    with pytest.raises(MagazineSchemaError):
        asyncio.run(
            dispatch_cartridge(
                BASE_URL,
                API_KEY,
                cartridge={
                    "id": "cart-1",
                    "job_type": "summarize",
                    "prompt": "",
                },
                tool_role="web",
                model_map=MODEL_MAP,
                tool_map=TOOL_MAP,
                dispatch=mock_dispatch,
            )
        )

    mock_routed.assert_not_awaited()
    mock_dispatch.assert_not_awaited()


@patch("magazine.runtime_dispatch.dispatch_routed", new_callable=AsyncMock)
def test_whitespace_only_prompt_causes_zero_routed_dispatch(mock_routed):
    """A whitespace-only prompt fails validation -> zero routed dispatch."""
    mock_dispatch = AsyncMock(return_value="should not happen")

    with pytest.raises(MagazineSchemaError):
        asyncio.run(
            dispatch_cartridge(
                BASE_URL,
                API_KEY,
                cartridge={
                    "id": "cart-1",
                    "job_type": "summarize",
                    "prompt": "   ",
                },
                tool_role="web",
                model_map=MODEL_MAP,
                tool_map=TOOL_MAP,
                dispatch=mock_dispatch,
            )
        )

    mock_routed.assert_not_awaited()
    mock_dispatch.assert_not_awaited()


@patch("magazine.runtime_dispatch.dispatch_routed", new_callable=AsyncMock)
def test_missing_prompt_causes_zero_routed_dispatch(mock_routed):
    """A missing prompt fails validation -> zero routed dispatch."""
    mock_dispatch = AsyncMock(return_value="should not happen")

    with pytest.raises(MagazineSchemaError):
        asyncio.run(
            dispatch_cartridge(
                BASE_URL,
                API_KEY,
                cartridge={
                    "id": "cart-1",
                    "job_type": "summarize",
                },
                tool_role="web",
                model_map=MODEL_MAP,
                tool_map=TOOL_MAP,
                dispatch=mock_dispatch,
            )
        )

    mock_routed.assert_not_awaited()
    mock_dispatch.assert_not_awaited()


@patch("magazine.runtime_dispatch.dispatch_routed", new_callable=AsyncMock)
def test_unknown_cartridge_field_causes_zero_routed_dispatch(mock_routed):
    """An unknown cartridge field fails validation -> zero routed dispatch."""
    mock_dispatch = AsyncMock(return_value="should not happen")

    with pytest.raises(MagazineSchemaError):
        asyncio.run(
            dispatch_cartridge(
                BASE_URL,
                API_KEY,
                cartridge={
                    "id": "cart-1",
                    "job_type": "summarize",
                    "prompt": "Summarize.",
                    "extra": "field",
                },
                tool_role="web",
                model_map=MODEL_MAP,
                tool_map=TOOL_MAP,
                dispatch=mock_dispatch,
            )
        )

    mock_routed.assert_not_awaited()
    mock_dispatch.assert_not_awaited()
