"""One saved Open WebUI chat and one prompt, using the documented API flow."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener
from uuid import uuid4

from .probe import API_KEY_ENV, BASE_URL_ENV, _NoRedirect, _base_url

MODEL_ID = "hf.co/ggml-org/Qwen3.6-35B-A3B-GGUF:Q4_K_M"
PROMPT = "Reply with exactly: MAGAZINE_TEST_OK"
EXPECTED_RESPONSE = "MAGAZINE_TEST_OK"
PROJECT_DIR = Path(__file__).resolve().parent.parent
WAIT_SECONDS = 600
POLL_SECONDS = 2


class ProofHalt(Exception):
    """A required step failed; never retry a chat-creating request."""


def _api_key() -> str:
    value = os.environ.get(API_KEY_ENV)
    if value:
        return value
    env_file = PROJECT_DIR / ".env.local"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            if line.strip().startswith(f"{API_KEY_ENV}="):
                value = line.split("=", 1)[1].strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                if value:
                    return value
    raise ProofHalt(f"Set {API_KEY_ENV} or put it in {env_file}")


def _request(base_url: str, api_key: str, method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Accept": "application/json", "Authorization": f"Bearer {api_key}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = Request(base_url + path, data=data, headers=headers, method=method)
    try:
        with build_opener(_NoRedirect()).open(request, timeout=30) as response:
            status, raw = response.status, response.read()
    except HTTPError as exc:
        raise ProofHalt(f"{method} {path}: HTTP {exc.code} {exc.reason}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise ProofHalt(f"{method} {path}: {type(exc).__name__}: {exc}") from exc
    try:
        return status, json.loads(raw), raw
    except ValueError as exc:
        raise ProofHalt(f"{method} {path}: response is not JSON (HTTP {status})") from exc


def run() -> None:
    base_url = _base_url(os.environ.get(BASE_URL_ENV, "http://localhost:3000"))
    api_key = _api_key()
    user_id = str(uuid4())
    assistant_id = str(uuid4())
    session_id = str(uuid4())
    timestamp = int(time.time())
    user_message = {
        "id": user_id, "role": "user", "content": PROMPT,
        "timestamp": timestamp, "models": [MODEL_ID], "childrenIds": [assistant_id],
    }
    assistant_message = {
        "id": assistant_id, "role": "assistant", "content": "",
        "parentId": user_id, "childrenIds": [], "model": MODEL_ID,
        "modelName": MODEL_ID, "modelIdx": 0, "done": False,
        "timestamp": timestamp + 1,
    }
    chat_body = {"chat": {
        "title": "Magazine fresh chat proof", "models": [MODEL_ID],
        "messages": [user_message, assistant_message],
        "history": {
            "currentId": assistant_id,
            "messages": {user_id: user_message, assistant_id: assistant_message},
        },
        "currentId": assistant_id,
    }}

    create_status, created, _ = _request(base_url, api_key, "POST", "/api/v1/chats/new", chat_body)
    chat_id = created.get("id") if isinstance(created, dict) else None
    if not isinstance(chat_id, str) or not chat_id:
        raise ProofHalt(f"POST /api/v1/chats/new: missing chat ID (HTTP {create_status})")
    print(f"Created saved chat: HTTP {create_status}; chat_id={chat_id}", flush=True)

    completion_body = {
        "chat_id": chat_id, "id": assistant_id,
        "messages": [{"role": "user", "content": PROMPT}],
        "model": MODEL_ID, "stream": True, "session_id": session_id,
        "tools": [],
        "features": {
            "web_search": False, "code_interpreter": False,
            "image_generation": False, "memory": False,
        },
        "background_tasks": {
            "title_generation": False, "tags_generation": False,
            "follow_up_generation": False,
        },
    }
    start_status, started, _ = _request(base_url, api_key, "POST", "/api/chat/completions", completion_body)
    if not isinstance(started, dict) or started.get("status") is not True or started.get("chat_id") != chat_id or not isinstance(started.get("task_ids"), list):
        raise ProofHalt(f"POST /api/chat/completions: unexpected acceptance response (HTTP {start_status}; chat_id={chat_id})")
    print(f"Completion accepted: HTTP {start_status}; task_ids={started['task_ids']}", flush=True)

    deadline = time.monotonic() + WAIT_SECONDS
    last_count = None
    while True:
        task_status, tasks, _ = _request(base_url, api_key, "GET", f"/api/tasks/chat/{chat_id}")
        task_ids = tasks.get("task_ids") if isinstance(tasks, dict) else None
        if not isinstance(task_ids, list):
            raise ProofHalt(f"GET /api/tasks/chat/{chat_id}: unexpected response (HTTP {task_status})")
        if len(task_ids) != last_count:
            print(f"Server task poll: HTTP {task_status}; active={len(task_ids)}", flush=True)
            last_count = len(task_ids)
        if not task_ids:
            break
        if time.monotonic() >= deadline:
            raise ProofHalt(f"Server task did not complete within {WAIT_SECONDS} seconds; chat_id={chat_id}")
        time.sleep(POLL_SECONDS)

    read_status, retrieved, raw = _request(base_url, api_key, "GET", f"/api/v1/chats/{chat_id}")
    evidence_dir = PROJECT_DIR / "evidence"
    evidence_dir.mkdir(exist_ok=True)
    evidence_path = evidence_dir / f"chat-{chat_id}.json"
    with evidence_path.open("xb") as evidence_file:
        evidence_file.write(raw)
    print(f"Retrieved saved chat: HTTP {read_status}; evidence={evidence_path}", flush=True)

    if not isinstance(retrieved, dict):
        raise ProofHalt(f"Retrieved chat is not an object; evidence={evidence_path}")
    chat = retrieved.get("chat")
    history = chat.get("history") if isinstance(chat, dict) else None
    messages = history.get("messages") if isinstance(history, dict) else None
    saved_user = messages.get(user_id) if isinstance(messages, dict) else None
    saved_assistant = messages.get(assistant_id) if isinstance(messages, dict) else None
    saved_models = chat.get("models") if isinstance(chat, dict) else None
    if retrieved.get("id") != chat_id or not isinstance(saved_models, list) or MODEL_ID not in saved_models:
        raise ProofHalt(f"Retrieved chat ID or model list does not match; evidence={evidence_path}")
    if not isinstance(saved_user, dict) or saved_user.get("content") != PROMPT:
        raise ProofHalt(f"Saved user prompt does not match; evidence={evidence_path}")
    if not isinstance(saved_assistant, dict):
        raise ProofHalt(f"Saved assistant message is missing; evidence={evidence_path}")
    response = saved_assistant.get("content")
    print(f"Saved assistant: model={saved_assistant.get('model')}; done={saved_assistant.get('done')}; response={response!r}", flush=True)
    if saved_assistant.get("model") != MODEL_ID or saved_assistant.get("done") is not True:
        raise ProofHalt(f"Saved assistant model or completion flag does not match; evidence={evidence_path}")
    if response != EXPECTED_RESPONSE:
        raise ProofHalt(f"Assistant response is not exactly {EXPECTED_RESPONSE!r}; evidence={evidence_path}")
    print("PASS: one saved chat, one prompt, task complete, exact response, chat retrievable", flush=True)


def main() -> int:
    try:
        run()
    except (ProofHalt, ValueError, OSError) as exc:
        print(f"HALT: {exc}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
