"""Tests for magazine.discovery.discover_model_ids and discover_model_runtime_meta (mocked responses only)."""

import json
from urllib.error import HTTPError, URLError

import pytest

from magazine.discovery import discover_model_ids, discover_tool_ids, discover_terminal_ids, discover_model_runtime_meta

BASE_URL = "https://openwebui.example.com"
API_KEY = "sk-test-key"
MODEL_ID = "gpt-4o"
REQUIRED_TOOL_ID = "fresh_worker"


class _FakeResponse:
    """Minimal stand-in for the context manager returned by opener.open()."""

    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def read(self):
        return self._body


@pytest.fixture
def opener(monkeypatch):
    """Patch build_opener so no real network request is made.

    The returned fake opener records the Request it was asked to open and
    raises/returns whatever the test configured via ``set_response`` /
    ``set_error``.
    """
    state = {"request": None, "response": None, "error": None}

    class _FakeOpener:
        def open(self, request, timeout=None):
            state["request"] = request
            if state["error"] is not None:
                raise state["error"]
            return state["response"]

    monkeypatch.setattr("magazine.discovery.build_opener", lambda *a, **k: _FakeOpener())
    return state


def _set_response(opener, payload):
    opener["response"] = _FakeResponse(json.dumps(payload).encode("utf-8"))


def _set_raw_response(opener, body: bytes):
    opener["response"] = _FakeResponse(body)


# ---------------------------------------------------------------------------
# 1. valid response returns exact model IDs
# ---------------------------------------------------------------------------
def test_valid_response_returns_exact_model_ids(opener):
    payload = {
        "data": [
            {"id": "gpt-4o", "name": "GPT-4o"},
            {"id": "claude-3.5-sonnet", "name": "Claude 3.5 Sonnet"},
            {"id": "llama-3.1-70b"},
        ]
    }
    _set_response(opener, payload)

    result = discover_model_ids(BASE_URL, API_KEY)

    assert result == ["gpt-4o", "claude-3.5-sonnet", "llama-3.1-70b"]


def test_valid_response_empty_data_returns_empty_list(opener):
    _set_response(opener, {"data": []})
    assert discover_model_ids(BASE_URL, API_KEY) == []


# ---------------------------------------------------------------------------
# 2. authentication header is sent
# ---------------------------------------------------------------------------
def test_authentication_header_is_sent(opener):
    _set_response(opener, {"data": [{"id": "m1"}]})

    discover_model_ids(BASE_URL, API_KEY)

    request = opener["request"]
    assert request.get_header("Authorization") == f"Bearer {API_KEY}"
    assert request.get_header("Accept") == "application/json"
    assert request.get_method() == "GET"
    assert request.full_url == BASE_URL + "/api/models"


# ---------------------------------------------------------------------------
# 3. request failure is rejected
# ---------------------------------------------------------------------------
def test_http_error_is_rejected(opener):
    opener["error"] = HTTPError(BASE_URL + "/api/models", 401, "Unauthorized", {}, None)

    with pytest.raises(ValueError, match="request failed: HTTP 401"):
        discover_model_ids(BASE_URL, API_KEY)


def test_url_error_is_rejected(opener):
    opener["error"] = URLError("connection refused")

    with pytest.raises(ValueError, match="request failed: URLError"):
        discover_model_ids(BASE_URL, API_KEY)


def test_timeout_is_rejected(opener):
    opener["error"] = TimeoutError("timed out")

    with pytest.raises(ValueError, match="request failed: TimeoutError"):
        discover_model_ids(BASE_URL, API_KEY)


# ---------------------------------------------------------------------------
# 4. invalid JSON is rejected
# ---------------------------------------------------------------------------
def test_invalid_json_is_rejected(opener):
    _set_raw_response(opener, b"this is not json")

    with pytest.raises(ValueError, match="invalid JSON"):
        discover_model_ids(BASE_URL, API_KEY)


def test_empty_body_is_rejected(opener):
    _set_raw_response(opener, b"")

    with pytest.raises(ValueError, match="invalid JSON"):
        discover_model_ids(BASE_URL, API_KEY)


# ---------------------------------------------------------------------------
# 5. malformed model payload is rejected
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "payload",
    [
        "not a dict",
        [{"id": "m1"}],
        {"models": [{"id": "m1"}]},
        {"data": "not a list"},
        {"data": [{"name": "no id"}]},
        {"data": [{"id": 123}], "extra": "value"},
        {"data": ["m1"], "other": 1},
    ],
)
def test_malformed_model_payload_is_rejected(opener, payload):
    _set_raw_response(opener, json.dumps(payload).encode("utf-8"))

    with pytest.raises(ValueError):
        discover_model_ids(BASE_URL, API_KEY)


# ---------------------------------------------------------------------------
# 6. discover_tool_ids — mocked success
# ---------------------------------------------------------------------------
def test_discover_tool_ids_returns_exact_ids(opener):
    payload = [
        {"id": "fresh_worker"},
        {"id": "development_toolkit"},
    ]
    _set_response(opener, payload)

    result = discover_tool_ids(BASE_URL, API_KEY)

    assert result == ["fresh_worker", "development_toolkit"]


def test_discover_tool_ids_requests_exact_url(opener):
    _set_response(opener, [{"id": "fresh_worker"}])

    discover_tool_ids(BASE_URL, API_KEY)

    request = opener["request"]
    assert request.full_url == BASE_URL + "/api/v1/tools/"
    assert request.get_header("Authorization") == f"Bearer {API_KEY}"
    assert request.get_header("Accept") == "application/json"
    assert request.get_method() == "GET"


# ---------------------------------------------------------------------------
# 7. discover_terminal_ids — mocked success
# ---------------------------------------------------------------------------
def test_discover_terminal_ids_returns_only_chat_enabled(opener):
    payload = [
        {"id": "term-1", "contexts": {"chat": True}},
        {"id": "term-2", "contexts": {"chat": False}},
        {"id": "term-3", "contexts": {"chat": True}},
    ]
    _set_response(opener, payload)

    result = discover_terminal_ids(BASE_URL, API_KEY)

    assert result == ["term-1", "term-3"]


def test_discover_terminal_ids_requests_exact_url(opener):
    _set_response(opener, [{"id": "term-1", "contexts": {"chat": True}}])

    discover_terminal_ids(BASE_URL, API_KEY)

    request = opener["request"]
    assert request.full_url == BASE_URL + "/api/v1/terminals/"
    assert request.get_header("Authorization") == f"Bearer {API_KEY}"
    assert request.get_header("Accept") == "application/json"
    assert request.get_method() == "GET"


# ---------------------------------------------------------------------------
# 8. discover_model_runtime_meta — exact encoded endpoint
# ---------------------------------------------------------------------------
def test_discover_model_runtime_meta_requests_exact_endpoint(opener):
    payload = {
        "meta": {
            "capabilities": {"terminal": True},
            "toolIds": ["fresh_worker"],
        },
    }
    _set_response(opener, payload)

    discover_model_runtime_meta(BASE_URL, API_KEY, MODEL_ID, REQUIRED_TOOL_ID)

    request = opener["request"]
    assert request.full_url == BASE_URL + "/api/v1/models/model?id=gpt-4o"
    assert request.get_header("Authorization") == f"Bearer {API_KEY}"
    assert request.get_header("Accept") == "application/json"
    assert request.get_method() == "GET"


def test_discover_model_runtime_meta_url_encodes_model_id(opener):
    payload = {
        "meta": {
            "capabilities": {"terminal": True},
            "toolIds": ["fresh_worker"],
        },
    }
    _set_response(opener, payload)

    model_id = "hf.co/logic65/Qwen3.8-Whittle-MoE-27B-A17.8B-GGUF:Q4_K_M"
    discover_model_runtime_meta(BASE_URL, API_KEY, model_id, REQUIRED_TOOL_ID)

    request = opener["request"]
    assert request.full_url == (
        "https://openwebui.example.com/api/v1/models/model"
        "?id=hf.co%2Flogic65%2FQwen3.8-Whittle-MoE-27B-A17.8B-GGUF%3AQ4_K_M"
    )


def test_discover_model_runtime_meta_returns_payload_unchanged(opener):
    payload = {
        "meta": {
            "capabilities": {"terminal": True},
            "toolIds": ["fresh_worker"],
        },
        "model": "gpt-4o",
    }
    _set_response(opener, payload)

    result = discover_model_runtime_meta(BASE_URL, API_KEY, MODEL_ID, REQUIRED_TOOL_ID)

    assert result == payload


# ---------------------------------------------------------------------------
# 9. invalid runtime metadata is rejected
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"meta": "not a dict"},
        {"meta": {"capabilities": "not a dict"}},
        {"meta": {"capabilities": "not a boolean"}},
        {"meta": {"capabilities": False}},
        {"meta": {"capabilities": True}},
        {"meta": {"capabilities": {"terminal": True}}, "toolIds": "not a list"},
        {"meta": {"capabilities": {"terminal": True}}, "toolIds": ["other_tool"]},
        {"meta": {"capabilities": {"terminal": True}}, "toolIds": ["fresh_worker"]},
    ],
)
def test_invalid_runtime_meta_is_rejected(opener, payload):
    _set_raw_response(opener, json.dumps(payload).encode("utf-8"))

    with pytest.raises(ValueError):
        discover_model_runtime_meta(BASE_URL, API_KEY, MODEL_ID, REQUIRED_TOOL_ID)


def test_missing_required_tool_id_is_rejected(opener):
    payload = {
        "meta": {
            "capabilities": {"terminal": True},
            "toolIds": ["other_tool"],
        },
    }
    _set_response(opener, payload)

    with pytest.raises(ValueError, match="required tool ID 'fresh_worker' not found in toolIds"):
        discover_model_runtime_meta(BASE_URL, API_KEY, MODEL_ID, REQUIRED_TOOL_ID)