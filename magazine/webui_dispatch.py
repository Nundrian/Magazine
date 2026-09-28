"""Open WebUI dispatch adapter for Magazine.

Factory that produces an async ``dispatch(prompt, model_id)`` callable
returning the final assistant response text, following the proven
Open WebUI request sequence documented in ``magazine.fresh_chat_proof``:

  1. create a fresh saved chat;
  2. submit a completion using the supplied ``model_id``;
  3. poll until the server task finishes;
  4. retrieve the saved chat and read the assistant response.

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
import time
import uuid
from urllib.parse import urlsplit

import httpx

__all__ = ["make_open_webui_dispatch"]


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


def make_open_webui_dispatch(
    base_url: str,
    api_key: str,
    *,
    timeout: float = 30.0,
    wait_timeout: float = 600.0,
    poll_interval: float = 2.0,
):
    """Create an async ``dispatch(prompt, model_id) -> str`` bound to
    *base_url* and *api_key*.

    Args:
        base_url: Absolute http(s) base URL of the Open WebUI instance.
        api_key: Bearer API key used for every request.
        timeout: Per-request timeout in seconds.
        wait_timeout: Seconds to wait for the server task to finish.
        poll_interval: Seconds between task polls.

    Returns:
        An async callable accepting a *prompt* (str) and *model_id* (str)
        and returning the saved assistant response text (str).

    Raises:
        ValueError: If *base_url* or *api_key* is not usable at factory
            creation time.
    """
    base_url = _normalize_base_url(base_url)
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError("api_key must be a non-empty string")

    async def dispatch(prompt: str, model_id: str) -> str:
        """Run one proven Open WebUI flow for *prompt* with *model_id*."""
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be a non-empty string")
        if not isinstance(model_id, str) or not model_id.strip():
            raise ValueError("model_id must be a non-empty string")

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
            "tools": [],
            "features": {
                "web_search": False,
                "code_interpreter": False,
                "image_generation": False,
                "memory": False,
            },
            "background_tasks": {
                "title_generation": False,
                "tags_generation": False,
                "follow_up_generation": False,
            },
        }

        async with httpx.AsyncClient(timeout=timeout) as client:
            # --- 1. create one fresh saved chat ----------------------------
            response = await _http_request(client, "POST", base_url + "/api/v1/chats/new", chat_body, api_key)
            _require_success(response, "POST /api/v1/chats/new")
            created = _json_body(response, "POST /api/v1/chats/new")
            chat_id = created.get("id") if isinstance(created, dict) else None
            if not isinstance(chat_id, str) or not chat_id:
                raise ValueError(f"POST /api/v1/chats/new: missing chat ID (HTTP {response.status_code})")

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

    return dispatch
