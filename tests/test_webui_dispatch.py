"""Mocked-HTTP tests for magazine.webui_dispatch.

Proves that the Open WebUI dispatch adapter:

  * carries the *supplied* ``model_id`` into the Open WebUI requests;
  * returns the final saved assistant response text;
  * uses a *different* model when a different ``model_id`` is supplied;
  * raises a clear ``ValueError`` on a failed Open WebUI response;
  * propagates transport errors (httpx) unchanged;
  * uses ``asyncio.sleep`` for polling.

No live Open WebUI instance is involved.  A fake ``httpx.AsyncClient`` is
monkeypatched onto ``webui_dispatch.httpx.AsyncClient``.
"""

import asyncio
import itertools
import json

import httpx
import pytest

from magazine import webui_dispatch
from magazine.webui_dispatch import make_open_webui_dispatch


BASE_URL = "http://localhost:3000"
API_KEY = "key-123"
PROMPT = "Reply with exactly: MAGAZINE_TEST_OK"
ASSISTANT_TEXT = "MAGAZINE_TEST_OK"
MODEL_A = "hf.co/ggml-org/Qwen3.6-35B-A3B-GGUF:Q4_K_M"
MODEL_B = "hf.co/other/SomeModel:Q4_K_M"
CHAT_ID = "chat-1"
USER_ID = "user-1"
ASSISTANT_ID = "asst-2"
SESSION_ID = "sess-3"


class _FakeResponse:
    def __init__(self, status_code, data, reason="OK"):
        self.status_code = status_code
        self.reason = reason
        self._data = data

    def json(self):
        return self._data


class _FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def request(self, method, url, content=None, headers=None, **kw):
        self.calls.append(
            {"method": method, "url": url, "headers": dict(headers or {}), "body": json.loads(content) if content else None}
        )
        status, data = self.responses.pop(0)
        return _FakeResponse(status, data)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


class _TransportClient:
    """A client whose context entry raises a transport error."""

    def __init__(self, exc):
        self.exc = exc

    async def __aenter__(self):
        raise self.exc

    async def __aexit__(self, *exc_info):
        return False


def _pin_ids(monkeypatch):
    """Make ``uuid.uuid4`` yield the pinned ids, cycling as needed."""
    it = itertools.cycle((USER_ID, ASSISTANT_ID, SESSION_ID))
    monkeypatch.setattr(webui_dispatch.uuid, "uuid4", lambda: next(it))


def _retrieved_chat(model_id):
    return {
        "id": CHAT_ID,
        "chat": {
            "models": [model_id],
            "history": {
                "currentId": ASSISTANT_ID,
                "messages": {
                    USER_ID: {"id": USER_ID, "role": "user", "content": PROMPT},
                    ASSISTANT_ID: {
                        "id": ASSISTANT_ID,
                        "role": "assistant",
                        "content": ASSISTANT_TEXT,
                        "parentId": USER_ID,
                        "childrenIds": [],
                        "model": model_id,
                        "modelName": model_id,
                        "modelIdx": 0,
                        "done": True,
                        "timestamp": 1,
                    },
                },
            },
        },
    }


def _responses(model_id, active_polls=0):
    polls = [{"task_ids": ["t-1"]}] * active_polls + [{"task_ids": []}]
    return [
        (201, {"id": CHAT_ID}),
        (200, {"status": True, "chat_id": CHAT_ID, "task_ids": ["t-1"]}),
        *( (200, p) for p in polls ),
        (200, _retrieved_chat(model_id)),
    ]


def _dispatch(monkeypatch, client):
    monkeypatch.setattr(webui_dispatch.httpx, "AsyncClient", lambda *a, **kw: client)
    return make_open_webui_dispatch(BASE_URL, API_KEY)


def test_requested_model_id_reaches_open_webui_request(monkeypatch):
    _pin_ids(monkeypatch)
    client = _FakeClient(_responses(MODEL_A))
    dispatch = _dispatch(monkeypatch, client)
    asyncio.run(dispatch(PROMPT, MODEL_A))
    # --- 1. chat creation carries model_id in chat.models, the user message
    #     models, and the assistant placeholder model/modelName ------------
    assert client.calls[0]["method"] == "POST"
    assert client.calls[0]["url"] == BASE_URL + "/api/v1/chats/new"
    chat = client.calls[0]["body"]["chat"]
    assert chat["models"] == [MODEL_A]
    assert chat["messages"][0]["models"] == [MODEL_A]
    assert chat["messages"][1]["model"] == MODEL_A
    assert chat["messages"][1]["modelName"] == MODEL_A
    # --- 2. completion carries model_id in the completion body --------------
    comp = client.calls[1]["body"]
    assert client.calls[1]["url"] == BASE_URL + "/api/chat/completions"
    assert comp["model"] == MODEL_A
    assert comp["chat_id"] == CHAT_ID
    assert comp["messages"][0]["content"] == PROMPT
    # --- 3. shared Open WebUI headers on every request ----------------------
    for call in client.calls:
        assert call["headers"]["Authorization"] == f"Bearer {API_KEY}"
        assert call["headers"]["Accept"] == "application/json"
    # POST bodies send JSON, GET requests do not.
    assert client.calls[0]["headers"]["Content-Type"] == "application/json"
    assert client.calls[2]["headers"].get("Content-Type") is None



def test_final_assistant_text_is_returned(monkeypatch):
    _pin_ids(monkeypatch)
    client = _FakeClient(_responses(MODEL_A))
    dispatch = _dispatch(monkeypatch, client)
    result = asyncio.run(dispatch(PROMPT, MODEL_A))
    assert isinstance(result, str)
    assert result == ASSISTANT_TEXT


def test_single_dispatch_callable_proves_each_request_uses_supplied_model(monkeypatch):
    """One dispatch callable is invoked twice with two different model_id
    values; every request in each invocation must carry only the supplied
    model (no factory/client re-creation between the calls)."""
    _pin_ids(monkeypatch)
    # A single fake client serves both dispatches back-to-back.
    client = _FakeClient([
        # dispatch #1 (MODEL_A): create, completion, poll (idle), retrieve
        (201, {"id": CHAT_ID}),
        (200, {"status": True, "chat_id": CHAT_ID, "task_ids": ["t-1"]}),
        (200, {"task_ids": []}),
        (200, _retrieved_chat(MODEL_A)),
        # dispatch #2 (MODEL_B): create, completion, poll (idle), retrieve
        (201, {"id": CHAT_ID}),
        (200, {"status": True, "chat_id": CHAT_ID, "task_ids": ["t-1"]}),
        (200, {"task_ids": []}),
        (200, _retrieved_chat(MODEL_B)),
    ])
    dispatch = _dispatch(monkeypatch, client)
    assert MODEL_A != MODEL_B

    # --- invoke the SAME callable twice with two different model_ids ----------
    result_a = asyncio.run(dispatch(PROMPT, MODEL_A))
    assert result_a == ASSISTANT_TEXT
    result_b = asyncio.run(dispatch(PROMPT, MODEL_B))
    assert result_b == ASSISTANT_TEXT
    # Eight requests total (create, completion, poll, retrieve x2) — the same
    # callable and the same fake client were used for both dispatches.
    assert len(client.calls) == 8
    a = client.calls

    # --- the first four requests must use only MODEL_A -------------------------
    assert a[0]["method"] == "POST"
    assert a[0]["url"] == BASE_URL + "/api/v1/chats/new"
    chat_a = a[0]["body"]["chat"]
    assert chat_a["models"] == [MODEL_A]
    assert chat_a["messages"][0]["models"] == [MODEL_A]
    assert chat_a["messages"][1]["model"] == MODEL_A
    assert chat_a["messages"][1]["modelName"] == MODEL_A
    comp_a = a[1]["body"]
    assert a[1]["method"] == "POST"
    assert a[1]["url"] == BASE_URL + "/api/chat/completions"
    assert comp_a["model"] == MODEL_A
    assert comp_a["chat_id"] == CHAT_ID
    assert comp_a["messages"][0]["content"] == PROMPT

    # --- the next four requests must use only MODEL_B --------------------------
    assert a[4]["method"] == "POST"
    assert a[4]["url"] == BASE_URL + "/api/v1/chats/new"
    chat_b = a[4]["body"]["chat"]
    assert chat_b["models"] == [MODEL_B]
    assert chat_b["messages"][0]["models"] == [MODEL_B]
    assert chat_b["messages"][1]["model"] == MODEL_B
    assert chat_b["messages"][1]["modelName"] == MODEL_B
    comp_b = a[5]["body"]
    assert a[5]["method"] == "POST"
    assert a[5]["url"] == BASE_URL + "/api/chat/completions"
    assert comp_b["model"] == MODEL_B
    assert comp_b["chat_id"] == CHAT_ID
    assert comp_b["messages"][0]["content"] == PROMPT

    # --- cross-check: no model bleeds into the other dispatch's requests -------
    assert MODEL_B not in json.dumps(a[0]["body"], default=str)
    assert MODEL_B not in json.dumps(a[1]["body"], default=str)
    assert MODEL_A not in json.dumps(a[4]["body"], default=str)
    assert MODEL_A not in json.dumps(a[5]["body"], default=str)


def test_failed_open_webui_response_raises_clearly(monkeypatch):
    _pin_ids(monkeypatch)
    dispatch = _dispatch(monkeypatch, _FakeClient([(201, {"id": CHAT_ID}), (500, {"error": "Internal server error"})]))
    with pytest.raises(ValueError) as excinfo:
        asyncio.run(dispatch(PROMPT, MODEL_A))
    msg = str(excinfo.value)
    assert "POST /api/chat/completions" in msg
    assert "500" in msg


def test_semantic_mismatch_on_completion_raises_clearly(monkeypatch):
    _pin_ids(monkeypatch)
    # Completion accepted a *different* chat id than the one we created.
    dispatch = _dispatch(monkeypatch, _FakeClient([(201, {"id": CHAT_ID}), (200, {"status": True, "chat_id": "other-id", "task_ids": ["t-1"]})]))
    with pytest.raises(ValueError) as excinfo:
        asyncio.run(dispatch(PROMPT, MODEL_A))
    assert "POST /api/chat/completions" in str(excinfo.value)
    assert "other-id" in str(excinfo.value)


def test_transport_error_propagates_unchanged(monkeypatch):
    _pin_ids(monkeypatch)
    dispatch = _dispatch(monkeypatch, _TransportClient(httpx.ConnectError("connection refused")))
    with pytest.raises(httpx.ConnectError) as excinfo:
        asyncio.run(dispatch(PROMPT, MODEL_A))
    # httpx transport error must surface as-is (not wrapped in ValueError).
    assert "connection refused" in str(excinfo.value)
    assert not isinstance(excinfo.value, ValueError)


def test_polling_uses_asyncio_sleep(monkeypatch):
    _pin_ids(monkeypatch)
    sleep_calls = []

    async def fake_sleep(interval):
        sleep_calls.append(interval)

    monkeypatch.setattr(webui_dispatch.asyncio, "sleep", fake_sleep)
    client = _FakeClient(_responses(MODEL_A, active_polls=1))
    dispatch = _dispatch(monkeypatch, client)
    asyncio.run(dispatch(PROMPT, MODEL_A))
    # One active poll -> one asyncio.sleep before the task completed.
    assert sleep_calls == [2.0]


def test_factory_requires_credentials_and_valid_base_url():
    with pytest.raises(ValueError, match="api_key"):
        make_open_webui_dispatch(BASE_URL, "")
    with pytest.raises(ValueError, match="base_url"):
        make_open_webui_dispatch("ftp://nope", "key")
    with pytest.raises(ValueError, match="base_url"):
        make_open_webui_dispatch("http://user:pass@example.com", "key")


def test_dispatch_requires_non_empty_prompt_and_model_id(monkeypatch):
    _pin_ids(monkeypatch)
    client = _FakeClient(_responses(MODEL_A))
    dispatch = _dispatch(monkeypatch, client)
    with pytest.raises(ValueError, match="prompt"):
        asyncio.run(dispatch("", MODEL_A))
    with pytest.raises(ValueError, match="model_id"):
        asyncio.run(dispatch(PROMPT, "  "))


if __name__ == "__main__":
    raise SystemExit("run via pytest: pytest tests/test_webui_dispatch.py -q")
