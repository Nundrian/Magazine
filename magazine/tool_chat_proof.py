"""One saved Open WebUI chat proving native terminal tool execution."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from .fresh_chat_proof import MODEL_ID, PROJECT_DIR, ProofHalt, _request
from .probe import API_KEY_ENV

BASE_URL = "http://localhost:3000"
TOOL_ID = "development_toolkit"
PROMPT = (
    "Use the terminal to determine the current working directory.\n"
    "Then reply with:\nMAGAZINE_TOOL_TEST_OK\n"
    "followed by the working directory you observed."
)
WAIT_SECONDS = 600
POLL_SECONDS = 2


def api_key_from_file() -> str:
    env_file = PROJECT_DIR / ".env.local"
    for line in env_file.read_text(encoding="utf-8-sig").splitlines():
        if line.strip().startswith(f"{API_KEY_ENV}="):
            value = line.split("=", 1)[1].strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            if value:
                return value
    raise ProofHalt(f"{API_KEY_ENV} is missing from {env_file}")


def discover(api_key: str) -> str:
    _, models, _ = _request(BASE_URL, api_key, "GET", "/api/models")
    if not isinstance(models, dict) or not any(
        isinstance(item, dict) and item.get("id") == MODEL_ID
        for item in models.get("data", [])
    ):
        raise ProofHalt(f"Requested model is not available: {MODEL_ID}")

    _, tools, _ = _request(BASE_URL, api_key, "GET", "/api/v1/tools/")
    if not isinstance(tools, list) or not any(
        isinstance(item, dict) and item.get("id") == TOOL_ID
        and item.get("name") == "Development Toolkit" for item in tools
    ):
        raise ProofHalt(f"Development Toolkit is not available as {TOOL_ID}")

    _, terminals, _ = _request(BASE_URL, api_key, "GET", "/api/v1/terminals/")
    if not isinstance(terminals, list):
        raise ProofHalt("Terminal discovery did not return an array")
    print("Discovered terminal servers:", flush=True)
    for item in terminals:
        print(f"  id={item.get('id')!r}; name={item.get('name')!r}; contexts={item.get('contexts')!r}", flush=True)
    usable = [item for item in terminals if isinstance(item, dict)
              and isinstance(item.get("id"), str)
              and item.get("contexts", {}).get("chat") is not False]
    if len(usable) != 1:
        raise ProofHalt(f"Expected exactly one chat-usable terminal, found {len(usable)}; no terminal selected")
    terminal_id = usable[0]["id"]

    path = "/api/v1/models/model?id=" + quote(MODEL_ID, safe="")
    _, model, _ = _request(BASE_URL, api_key, "GET", path)
    meta = model.get("meta", {}) if isinstance(model, dict) else {}
    if meta.get("capabilities", {}).get("terminal") is not True:
        raise ProofHalt("Requested model does not advertise terminal capability")
    if TOOL_ID not in meta.get("toolIds", []):
        raise ProofHalt("Requested model metadata does not list Development Toolkit")
    configured_terminal = meta.get("terminalId")
    if configured_terminal is not None and configured_terminal != terminal_id:
        raise ProofHalt("Model terminalId conflicts with sole accessible terminal")
    print(f"Selected terminal ID: {terminal_id}; Workspace tool ID: {TOOL_ID}", flush=True)
    return terminal_id


def run() -> None:
    api_key = api_key_from_file()
    terminal_id = discover(api_key)
    user_id, assistant_id, session_id = (str(uuid4()) for _ in range(3))
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
        "title": "Magazine terminal tool proof", "models": [MODEL_ID],
        "toolIds": [TOOL_ID], "terminalId": terminal_id,
        "messages": [user_message, assistant_message],
        "history": {
            "currentId": assistant_id,
            "messages": {user_id: user_message, assistant_id: assistant_message},
        },
        "currentId": assistant_id,
    }}
    create_status, created, _ = _request(BASE_URL, api_key, "POST", "/api/v1/chats/new", chat_body)
    chat_id = created.get("id") if isinstance(created, dict) else None
    if not isinstance(chat_id, str) or not chat_id:
        raise ProofHalt(f"Chat creation did not return an ID (HTTP {create_status})")
    print(f"Created chat ID: {chat_id} (HTTP {create_status})", flush=True)

    completion_body = {
        "chat_id": chat_id, "id": assistant_id,
        "messages": [{"role": "user", "content": PROMPT}],
        "model": MODEL_ID, "stream": True, "session_id": session_id,
        "tool_ids": [TOOL_ID], "terminal_id": terminal_id,
        "features": {
            "web_search": False, "code_interpreter": False,
            "image_generation": False, "memory": False,
        },
        "background_tasks": {
            "title_generation": False, "tags_generation": False,
            "follow_up_generation": False,
        },
    }
    # Do not send a caller-defined "tools" field: it disables server-side resolution.
    start_status, started, _ = _request(BASE_URL, api_key, "POST", "/api/chat/completions", completion_body)
    task_ids = started.get("task_ids") if isinstance(started, dict) else None
    if not isinstance(started, dict) or started.get("status") is not True or started.get("chat_id") != chat_id or not isinstance(task_ids, list) or not task_ids:
        raise ProofHalt(f"Completion was not accepted as a server task (HTTP {start_status}; chat_id={chat_id})")
    print(f"Task IDs: {task_ids}", flush=True)

    deadline = time.monotonic() + WAIT_SECONDS
    last_count = None
    while True:
        _, tasks, _ = _request(BASE_URL, api_key, "GET", f"/api/tasks/chat/{chat_id}")
        active = tasks.get("task_ids") if isinstance(tasks, dict) else None
        if not isinstance(active, list):
            raise ProofHalt(f"Task polling returned unexpected data; chat_id={chat_id}")
        if len(active) != last_count:
            print(f"Active tasks: {len(active)}", flush=True)
            last_count = len(active)
        if not active:
            break
        if time.monotonic() >= deadline:
            raise ProofHalt(f"Task did not finish within {WAIT_SECONDS} seconds; chat_id={chat_id}")
        time.sleep(POLL_SECONDS)

    read_status, retrieved, raw = _request(BASE_URL, api_key, "GET", f"/api/v1/chats/{chat_id}")
    evidence_path = PROJECT_DIR / "evidence" / f"chat-{chat_id}.json"
    with evidence_path.open("xb") as evidence_file:
        evidence_file.write(raw)
    print(f"Retrieved chat: HTTP {read_status}; raw evidence: {evidence_path}", flush=True)

    chat = retrieved.get("chat", {}) if isinstance(retrieved, dict) else {}
    messages = chat.get("history", {}).get("messages", {})
    assistant = messages.get(assistant_id, {})
    print(f"Saved chat keys: {list(chat)}", flush=True)
    print(f"Assistant keys: {list(assistant)}", flush=True)
    print(f"Assistant output: {json.dumps(assistant.get('output'), ensure_ascii=False)[:6000]}", flush=True)
    print(f"Final response: {assistant.get('content')!r}", flush=True)
    if retrieved.get("id") != chat_id or MODEL_ID not in chat.get("models", []):
        raise ProofHalt(f"Retrieved chat ID or model differs; evidence={evidence_path}")
    if chat.get("toolIds") != [TOOL_ID] or chat.get("terminalId") != terminal_id:
        raise ProofHalt(f"Saved chat lacks selected Toolkit or terminal; evidence={evidence_path}")
    if messages.get(user_id, {}).get("content") != PROMPT:
        raise ProofHalt(f"Saved prompt differs; evidence={evidence_path}")
    if assistant.get("model") != MODEL_ID or assistant.get("done") is not True:
        raise ProofHalt(f"Assistant model or completion flag differs; evidence={evidence_path}")
    if "MAGAZINE_TOOL_TEST_OK" not in str(assistant.get("content", "")):
        raise ProofHalt(f"Final response lacks proof marker; evidence={evidence_path}")
    print("Saved-chat metadata and final response verified; inspect tool call/result in raw evidence.", flush=True)


def main() -> int:
    try:
        run()
    except (ProofHalt, ValueError, OSError, KeyError, TypeError) as exc:
        print(f"HALT: {exc}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
