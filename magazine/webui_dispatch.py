"""Open WebUI dispatch adapter for Magazine.

Factory that produces an async ``dispatch(prompt, model_id)`` callable
returning the final assistant response text, following the proven
Open WebUI fresh-chat request sequence:

  1. create a fresh saved chat;
  2. submit a completion using the supplied ``model_id``;
  3. poll until the server task finishes;
  4. retrieve the saved chat and read the assistant response.

Optional ``tool_ids`` are Open WebUI tool DB IDs already selected by the
surrounding harness/operator.  Magazine carries them verbatim into the
completion request as ``tool_ids``; Open WebUI resolves them into the tools
available in each fresh chat.  Magazine never discovers, provisions, inspects
or manages tools.  When omitted, no tool IDs are sent and the previous
behaviour is preserved.

``base_url`` and ``api_key`` are supplied to the factory and are used for
every request.  A second set of credentials is never resolved from the
environment.

The supplied ``model_id`` is used verbatim in every place the proven flow
specifies the model (chat ``models``, the user message ``models`` and the
assistant message ``model``/``modelName``, and the completion ``model``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from urllib.parse import urlsplit

import httpx

__all__ = ["make_open_webui_dispatch"]

logger = logging.getLogger(__name__)


def _normalize_tool_ids(raw) -> tuple:
    """Validate *raw* and return an immutable copy of the supplied tool IDs.

    Returns an empty tuple when *raw* is ``None``.  Non-empty list/tuple
    inputs must contain only non-empty strings; anything else raises a
    ``ValueError`` before any HTTP request is made.
    """
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)):
        raise ValueError(
            f"tool_ids must be a list or tuple of non-empty strings, got {type(raw).__name__}"
        )
    for tool_id in raw:
        if not isinstance(tool_id, str) or not tool_id.strip():
            raise ValueError("tool_ids must contain only non-empty string IDs")
    return tuple(raw)


def _normalize_base_url(raw: str) -> str:
    """Validate and normalize *raw* to an absolute http/https base URL."""
    if not isinstance(raw, str):
        raise ValueError(f"base_url must be a string, got {type(raw).__name__}")
    value = raw.strip().rstrip("/")
    if not value:
        raise ValueError("base_url must be a non-empty string")
    try:
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError("must be an absolute http:// or https:// URL")
        if parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("must not contain credentials, a query, or a fragment")
        _ = parts.port  # Reject malformed port values before making a request.
    except ValueError as exc:
        raise ValueError(f"base_url {exc}") from exc
    return value


async def _http_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    body: dict | None,
    api_key: str,
) -> httpx.Response:
    """Send one request with the shared Open WebUI headers.

    Transport errors (connect/read timeouts, DNS, TLS, etc.) propagate from
    httpx unchanged; only response-level handling happens in the caller.
    """
    headers: dict[str, str] = {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    return await client.request(
        method,
        url,
        content=json.dumps(body).encode("utf-8") if body is not None else None,
        headers=headers,
    )


def _require_success(response: httpx.Response, step: str) -> httpx.Response:
    """Raise a clear semantic error when the Open WebUI HTTP status failed."""
    if response.status_code >= 400:
        raise ValueError(f"{step}: HTTP {response.status_code} {response.reason or '<reason>'}")
    return response


def _json_body(response: httpx.Response, step: str) -> dict:
    """Parse and return the JSON response, raising a clear error if absent."""
    try:
        return response.json()
    except ValueError as exc:
        raise ValueError(f"{step}: response body is not JSON (HTTP {response.status_code})") from exc


class _OpenWebUIDispatch:
    """Callable wrapper around the proven Open WebUI fresh-chat flow.

    Implements the generic Magazine dispatch contract ``dispatch(prompt, model_id) -> str`` while
    exposing two *adapter-only* attributes the generic orchestration layer may use without
    learning the Open WebUI API shape:

      * ``last_chat_id`` -- the ID of the chat created by the most recent successful
        dispatch (``None`` when there is nothing to clean up);
      * ``cleanup()`` -- a best-effort ``DELETE`` of that temporary chat.  It always
        returns (``True`` if the server acknowledged removal, ``False`` otherwise)
        and never raises, so a cleanup problem can never invalidate an already
        persisted result or corrupt run state.

    The actual fresh-chat request sequence lives in the injected ``dispatch_fn``
    closure; this class only adds the post-persistence cleanup affordance.
    """

    def __init__(self, dispatch_fn, base_url, api_key, timeout, chat_state):
        self._dispatch = dispatch_fn
        self._base_url = base_url
        self._api_key = api_key
        self._timeout = timeout
        self._chat_state = chat_state

    @property
    def last_chat_id(self):
        """Chat ID created by the most recent successful dispatch, or None."""
        return self._chat_state.get("chat_id")

    async def __call__(self, prompt, model_id):
        return await self._dispatch(prompt, model_id)

    async def cleanup(self):
        """Remove the temporary Open WebUI chat created by the most recent
        successful dispatch.  Never raises; returns a bool."""
        chat_id = self._chat_state.get("chat_id")
        # Reset before the request so a failed or slow delete cannot leave a
        # stale id that a later cycle would re-target.
        self._chat_state["chat_id"] = None
        if chat_id is None:
            return False
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.request(
                    "DELETE",
                    self._base_url + "/api/v1/chats/" + chat_id,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Accept": "application/json",
                    },
                )
            if 200 <= response.status_code < 300:
                return True
            if response.status_code != 404:
                logger.warning(
                    "Open WebUI chat cleanup %r failed with HTTP %s",
                    chat_id,
                    response.status_code,
                )
            return False
        except Exception as exc:
            logger.warning(
                "Open WebUI chat cleanup %r failed: %s",
                chat_id,
                exc,
                exc_info=True,
            )
            return False


def make_open_webui_dispatch(
    base_url: str,
    api_key: str,
    *,
    tool_ids: list | tuple | None = None,
    timeout: float = 30.0,
    wait_timeout: float = 600.0,
    poll_interval: float = 2.0,
):
    """Create an async ``dispatch(prompt, model_id) -> str`` bound to
    *base_url* and *api_key*.

    Args:
        base_url: Absolute http(s) base URL of the Open WebUI instance.
        api_key: Bearer API key used for every request.
        tool_ids: Optional sequence of Open WebUI tool DB IDs already
            selected by the surrounding harness/operator, e.g. a list
            of ``"development_toolkit"``.  Magazine forwards them verbatim
            to each completion request as ``tool_ids``; Open WebUI resolves
            them into the tools actually available in each fresh chat.
            Magazine does not query, resolve or manage tools.  When omitted,
            no tool IDs are sent and the previous behaviour is preserved.
        timeout: Per-request timeout in seconds.
        wait_timeout: Seconds to wait for the server task to finish.
        poll_interval: Seconds between task polls.

    Returns:
        An async callable accepting a *prompt* (str) and *model_id* (str)
        and returning the saved assistant response text (str).

    Raises:
        ValueError: If *base_url*, *api_key* or *tool_ids* is not usable
            at factory creation time.
    """
    base_url = _normalize_base_url(base_url)
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError("api_key must be a non-empty string")
    tool_ids = _normalize_tool_ids(tool_ids)

    # Shared holder for the ID of the most recent successful chat creation.
    # The closure writes to it; the adapter wrapper (returned below) reads it
    # when the orchestration layer asks for post-persistence cleanup.
    _chat_state = {"chat_id": None}

    async def dispatch(prompt: str, model_id: str) -> str:
        """Run one proven Open WebUI flow for *prompt* with *model_id*."""
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be a non-empty string")
        if not isinstance(model_id, str) or not model_id.strip():
            raise ValueError("model_id must be a non-empty string")

        # Never let a stale chat id from a prior dispatch cycle survive into
        # this one; a failed dispatch must not leave a half-tracked chat.
        _chat_state["chat_id"] = None

        user_id = str(uuid.uuid4())
        assistant_id = str(uuid.uuid4())
        session_id = str(uuid.uuid4())
        timestamp = int(time.time())

        user_message: dict = {
            "id": user_id,
            "role": "user",
            "content": prompt,
            "timestamp": timestamp,
            "models": [model_id],
            "childrenIds": [assistant_id],
        }
        assistant_message: dict = {
            "id": assistant_id,
            "role": "assistant",
            "content": "",
            "parentId": user_id,
            "childrenIds": [],
            "model": model_id,
            "modelName": model_id,
            "modelIdx": 0,
            "done": False,
            "timestamp": timestamp + 1,
        }
        chat_body: dict = {
            "chat": {
                "title": "Magazine dispatch",
                "models": [model_id],
                "messages": [user_message, assistant_message],
                "history": {
                    "currentId": assistant_id,
                    "messages": {user_id: user_message, assistant_id: assistant_message},
                },
                "currentId": assistant_id,
            }
        }
        completion_body: dict = {
            "chat_id": None,
            "id": assistant_id,
            "messages": [{"role": "user", "content": prompt}],
            "model": model_id,
            "stream": True,
            "session_id": session_id,
            "features": {
                "memory": False,
            },
            "background_tasks": {
                "title_generation": False,
                "tags_generation": False,
                "follow_up_generation": False,
            },
        }
        if tool_ids:
            completion_body["tool_ids"] = list(tool_ids)

        async with httpx.AsyncClient(timeout=timeout) as client:
            # --- 1. create one fresh saved chat ----------------------------
            response = await _http_request(client, "POST", base_url + "/api/v1/chats/new", chat_body, api_key)
            _require_success(response, "POST /api/v1/chats/new")
            created = _json_body(response, "POST /api/v1/chats/new")
            chat_id = created.get("id") if isinstance(created, dict) else None
            if not isinstance(chat_id, str) or not chat_id:
                raise ValueError(f"POST /api/v1/chats/new: missing chat ID (HTTP {response.status_code})")

            # Track the freshly created temporary chat so the adapter can
            # remove it after the result has been durably persisted.
            _chat_state["chat_id"] = chat_id

            completion_body["chat_id"] = chat_id

            # --- 2. submit a completion using the supplied model_id --------
            response = await _http_request(client, "POST", base_url + "/api/chat/completions", completion_body, api_key)
            _require_success(response, "POST /api/chat/completions")
            started = _json_body(response, "POST /api/chat/completions")
            if not (
                isinstance(started, dict)
                and started.get("status") is True
                and started.get("chat_id") == chat_id
                and isinstance(started.get("task_ids"), list)
            ):
                received_id = started.get("chat_id") if isinstance(started, dict) else None
                received_status = started.get("status") if isinstance(started, dict) else None
                raise ValueError(
                    f"POST /api/chat/completions: unexpected acceptance response "
                    f"(HTTP {response.status_code}); expected chat_id={chat_id} "
                    f"status=True, received chat_id={received_id!r} status={received_status!r}"
                )

            # --- 3. poll until the server task finishes --------------------
            deadline = time.monotonic() + wait_timeout
            last_count = None
            poll_path = f"/api/tasks/chat/{chat_id}"
            while True:
                response = await _http_request(client, "GET", base_url + poll_path, None, api_key)
                _require_success(response, f"GET {poll_path}")
                tasks = _json_body(response, f"GET {poll_path}")
                task_ids = tasks.get("task_ids") if isinstance(tasks, dict) else None
                if not isinstance(task_ids, list):
                    raise ValueError(f"GET {poll_path}: unexpected response (HTTP {response.status_code})")
                if len(task_ids) != last_count:
                    last_count = len(task_ids)
                if not task_ids:
                    break
                if time.monotonic() >= deadline:
                    raise ValueError(f"Server task did not complete within {wait_timeout} seconds; chat_id={chat_id}")
                await asyncio.sleep(poll_interval)

            # --- 4. retrieve the saved assistant response ------------------
            retrieve_path = f"/api/v1/chats/{chat_id}"
            response = await _http_request(client, "GET", base_url + retrieve_path, None, api_key)
            _require_success(response, f"GET {retrieve_path}")
            retrieved = _json_body(response, f"GET {retrieve_path}")
            if not isinstance(retrieved, dict):
                raise ValueError(f"GET {retrieve_path}: retrieved chat is not an object (HTTP {response.status_code})")
            chat = retrieved.get("chat")
            history = chat.get("history") if isinstance(chat, dict) else None
            messages = history.get("messages") if isinstance(history, dict) else None
            saved_models = chat.get("models") if isinstance(chat, dict) else None
            if retrieved.get("id") != chat_id or not isinstance(saved_models, list) or model_id not in saved_models:
                raise ValueError(f"GET {retrieve_path}: retrieved chat ID or model list does not match (HTTP {response.status_code})")
            saved_user = messages.get(user_id) if isinstance(messages, dict) else None
            if not isinstance(saved_user, dict) or saved_user.get("content") != prompt:
                raise ValueError(f"GET {retrieve_path}: saved user prompt does not match (HTTP {response.status_code})")
            saved_assistant = messages.get(assistant_id) if isinstance(messages, dict) else None
            if not isinstance(saved_assistant, dict):
                raise ValueError(f"GET {retrieve_path}: saved assistant message is missing (HTTP {response.status_code})")
            if saved_assistant.get("model") != model_id or saved_assistant.get("done") is not True:
                raise ValueError(f"GET {retrieve_path}: saved assistant model or done flag does not match (HTTP {response.status_code})")
            return saved_assistant.get("content", "")

    # Wrap the proven flow in the adapter so callers can still use the plain
    # ``dispatch(prompt, model_id) -> str`` contract while also gaining the
    # adapter-only ``cleanup()`` capability for post-persistence chat removal.
    return _OpenWebUIDispatch(dispatch, base_url, api_key, timeout, _chat_state)
