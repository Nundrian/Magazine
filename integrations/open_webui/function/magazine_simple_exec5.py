"""
title: Magazine Simple
author: Nundrian
author_url: https://github.com/Nundrian/Magazine
version: 0.2.0-exec.5
required_open_webui_version: 0.12.5
requirements: httpx==0.28.1, PyYAML==6.0.3
"""

# Magazine Simple: a production line of prompts for Open WebUI.
#
# A workflow is an ordered list of prompts (cartridges) written in advance,
# usually by a stronger model. Magazine runs them one after another. Each
# cartridge gets a fresh, normal Open WebUI chat with the model, Terminal and
# tools you chose. Work moves forward as files and, where a cartridge asks for
# it, a value from an earlier cartridge's JSON reply. Every chat is kept. A
# separate ledger (on the page and as LEDGER.md) records what was done.
#
# Magazine does not plan, review, retry, compress context or impose time
# limits. A cartridge is finished when its final message ends with a
# {"status": "DONE"} line and its required files exist. Magazine stops when a
# cartridge fails, ends without that line, replies with a top-level
# {"status": "HALT"} JSON object, or you press Stop. You can restart from any
# cartridge; earlier work and chats are kept.

from __future__ import annotations  # Open WebUI resolves route annotations from module globals

import asyncio
import base64
import datetime as _dt
import hashlib
import io
import json
import os
import re
import shutil
import stat
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath

import httpx

try:
    import yaml
except Exception:  # pragma: no cover - PyYAML is a declared requirement
    yaml = None

try:
    from pydantic import BaseModel
except Exception:  # pragma: no cover
    BaseModel = object

try:
    from fastapi import Request  # must be module-level for FastAPI to inject it
except Exception:  # pragma: no cover - only absent outside Open WebUI
    Request = None

VERSION = "0.2.0-exec.5"
PAGE_PATH = "/magazine-simple"
API = "/api/magazine-simple"
DEFAULT_WORKSPACE = "/workspace/magazine-simple"
WORKFLOW_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
MAX_ZIP_BYTES = 16 * 1024 * 1024
MAX_UNPACKED_BYTES = 64 * 1024 * 1024
MAX_ZIP_MEMBERS = 2000
MAX_DOWNLOAD_BYTES = 256 * 1024 * 1024
POLL_SECONDS = 2.0
SAVE_WAIT_SECONDS = 60.0
REQUIRE_DONE = True   # the default for the require_done_line valve
DONE_LINE = '{"status":"DONE"}'
DONE_INSTRUCTION = ("When this step is finished:\n"
                    "- End your final message with this line, on its own: " + DONE_LINE + "\n"
                    '- If you cannot finish it, end with {"status":"HALT","reason":"why"} instead.\n'
                    "A reply that ends without one of these lines is treated as unfinished.")


class MagazineError(ValueError):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


def now_iso():
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Workflow format (unchanged from Magazine beta.10):
#   magazine.yaml
#     schema_version: "1.0"
#     name: "My workflow"
#     cartridges:
#       - id: c01
#         model: writer              # a model key you map to a model on the page
#         prompt_file: cartridges/01.md
#         handover:                  # optional: value from an earlier JSON reply
#           - {from: c00, select: /field, as: name}
#         artifacts:                 # optional: files passed between cartridges
#           inputs:  [{from: c00, path: data/x.csv}]
#           outputs: [reports/y.json]
# Unused older fields (for example artifacts.review or constraints) are ignored.
# ---------------------------------------------------------------------------

def safe_relpath(value, what):
    if not isinstance(value, str) or not value.strip():
        raise MagazineError(f"{what} must be a non-empty relative path")
    if "\\" in value or "\x00" in value:
        raise MagazineError(f"{what} must use forward slashes: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in value.split("/")):
        raise MagazineError(f"{what} must stay inside the workflow: {value!r}")
    return str(path)


def _text(value, what):
    if not isinstance(value, str) or not value.strip():
        raise MagazineError(f"{what} must be a non-empty string")
    return value.strip()


def load_workflow(root):
    root = Path(root)
    manifest = next((root / n for n in ("magazine.yaml", "magazine.yml") if (root / n).is_file()), None)
    if manifest is None:
        raise MagazineError("magazine.yaml not found")
    if yaml is None:
        raise MagazineError("PyYAML is not available")
    try:
        data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    except Exception as exc:
        raise MagazineError(f"magazine.yaml could not be read: {exc}") from None
    if not isinstance(data, dict):
        raise MagazineError("magazine.yaml must be a mapping")
    items = data.get("cartridges")
    if not isinstance(items, list) or not items:
        raise MagazineError("magazine.yaml needs a non-empty 'cartridges' list")
    name = str(data.get("name") or root.name).strip()
    cartridges, seen, outputs_by = [], set(), {}
    for index, item in enumerate(items, 1):
        where = f"cartridge {index}"
        if not isinstance(item, dict):
            raise MagazineError(f"{where} must be a mapping")
        cid = _text(item.get("id"), f"{where} id")
        if cid in seen:
            raise MagazineError(f"duplicate cartridge id {cid!r}")
        where = f"cartridge {cid!r}"
        model = _text(item.get("model") or item.get("job_type"), f"{where} model")
        if item.get("prompt_file"):
            rel = safe_relpath(item["prompt_file"], f"{where} prompt_file")
            path = (root / rel)
            if not path.is_file() or path.is_symlink():
                raise MagazineError(f"{where}: prompt file {rel!r} not found")
            prompt = path.read_text(encoding="utf-8")
        else:
            rel, prompt = None, item.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise MagazineError(f"{where} has an empty prompt")
        handover = []
        for h in item.get("handover") or []:
            if not isinstance(h, dict):
                raise MagazineError(f"{where}: handover entries must be mappings")
            source = _text(h.get("from"), f"{where} handover 'from'")
            if source not in seen:
                raise MagazineError(f"{where}: handover from {source!r} must be an earlier cartridge")
            pointer = _text(h.get("select"), f"{where} handover 'select'")
            if not pointer.startswith("/"):
                raise MagazineError(f"{where}: handover select must start with '/'")
            handover.append({"from": source, "select": pointer,
                             "as": _text(h.get("as") or pointer.strip("/"), f"{where} handover 'as'")})
        artifacts = item.get("artifacts") or {}
        if not isinstance(artifacts, dict):
            raise MagazineError(f"{where}: artifacts must be a mapping")
        inputs = []
        for f in artifacts.get("inputs") or []:
            if not isinstance(f, dict):
                raise MagazineError(f"{where}: artifact inputs must be mappings")
            source = _text(f.get("from"), f"{where} input 'from'")
            path = safe_relpath(f.get("path"), f"{where} input path")
            if source not in seen:
                raise MagazineError(f"{where}: input from {source!r} must be an earlier cartridge")
            if path not in outputs_by.get(source, ()):
                raise MagazineError(f"{where}: {source!r} does not declare output {path!r}")
            inputs.append({"from": source, "path": path})
        outputs = [safe_relpath(p, f"{where} output") for p in artifacts.get("outputs") or []]
        seen.add(cid)
        outputs_by[cid] = set(outputs)
        cartridges.append({"id": cid, "model": model, "prompt_file": rel, "prompt": prompt,
                           "handover": handover, "inputs": inputs, "outputs": outputs})
    return {"name": name, "cartridges": cartridges}


# ---------------------------------------------------------------------------
# Library: one folder per workflow, separate from the original Magazine.
# ---------------------------------------------------------------------------

def list_workflows(library):
    library = Path(library)
    result = []
    if not library.is_dir():
        return result
    for entry in sorted(library.iterdir(), key=lambda p: p.name.lower()):
        if not WORKFLOW_ID.fullmatch(entry.name) or not entry.is_dir():
            continue
        try:
            wf = load_workflow(entry)
            result.append({"id": entry.name, "name": wf["name"], "valid": True,
                           "cartridges": len(wf["cartridges"])})
        except Exception as exc:
            result.append({"id": entry.name, "name": entry.name, "valid": False, "error": str(exc)})
    return result


def workflow_summary(library, workflow_id):
    if not isinstance(workflow_id, str) or not WORKFLOW_ID.fullmatch(workflow_id):
        raise MagazineError("Unknown workflow")
    root = Path(library) / workflow_id
    if not root.is_dir():
        raise MagazineError("Unknown workflow", 404)
    wf = load_workflow(root)
    models = []
    for c in wf["cartridges"]:
        if c["model"] not in models:
            models.append(c["model"])
    return {"id": workflow_id, "name": wf["name"], "model_keys": models,
            "cartridges": [{"id": c["id"], "model": c["model"], "prompt_file": c["prompt_file"],
                            "prompt": c["prompt"][:4000], "inputs": c["inputs"], "outputs": c["outputs"],
                            "handover": c["handover"]} for c in wf["cartridges"]]}


def delete_workflow(library, workflow_id):
    """Remove a workflow from the library. Runs keep their own copy, so past
    work and its ledger are unaffected."""
    if not isinstance(workflow_id, str) or not WORKFLOW_ID.fullmatch(workflow_id):
        raise MagazineError("Unknown workflow")
    root = Path(library) / workflow_id
    if root.is_symlink() or not root.is_dir() or root.parent.resolve() != Path(library).resolve():
        raise MagazineError("Unknown workflow", 404)
    shutil.rmtree(root)
    return {"deleted": workflow_id}


def _zip_prefix(names):
    if "magazine.yaml" in names or "magazine.yml" in names:
        return ""
    tops = {n.split("/", 1)[0] for n in names if "/" in n}
    if len(tops) == 1:
        top = tops.pop() + "/"
        if top + "magazine.yaml" in names or top + "magazine.yml" in names:
            return top
    raise MagazineError("The ZIP must contain magazine.yaml at its top level")


def import_workflow(library, file_name, raw, workflow_id=""):
    library = Path(library)
    if len(raw) > MAX_ZIP_BYTES:
        raise MagazineError("ZIP is larger than 16 MB", 413)
    if not workflow_id:
        stem = re.sub(r"\.zip$", "", str(file_name or ""), flags=re.I)
        workflow_id = re.sub(r"[^A-Za-z0-9_-]+", "-", stem).strip("-_")[:64] or "workflow"
    if not WORKFLOW_ID.fullmatch(workflow_id):
        raise MagazineError("Workflow name may use letters, digits, '-' and '_' only")
    library.mkdir(parents=True, exist_ok=True)
    if any(p.name.lower() == workflow_id.lower() for p in library.iterdir()):
        raise MagazineError(f"A workflow called {workflow_id!r} already exists", 409)
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise MagazineError("Not a valid ZIP file") from None
    members = [i for i in archive.infolist() if not i.is_dir()]
    if len(members) > MAX_ZIP_MEMBERS:
        raise MagazineError("ZIP has too many files", 413)
    if sum(i.file_size for i in members) > MAX_UNPACKED_BYTES:
        raise MagazineError("ZIP unpacks to more than 64 MB", 413)
    names = [i.filename for i in members]
    prefix = _zip_prefix(names)
    staging = library / (".import-" + uuid.uuid4().hex)
    staging.mkdir()
    try:
        for info in members:
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise MagazineError(f"ZIP contains a link: {info.filename}")
            if not info.filename.startswith(prefix):
                continue
            rel = safe_relpath(info.filename[len(prefix):], "ZIP entry")
            target = staging / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
        wf = load_workflow(staging)
        target = library / workflow_id
        if target.exists():
            raise MagazineError(f"A workflow called {workflow_id!r} already exists", 409)
        os.rename(staging, target)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {"id": workflow_id, "name": wf["name"], "cartridges": len(wf["cartridges"])}


# ---------------------------------------------------------------------------
# Replies: HALT and JSON values.
# ---------------------------------------------------------------------------

_FENCE = re.compile(r"(?ms)^[ \t]*(```+|~~~+)[ \t]*(?:json)?[ \t]*\r?\n(.*?)^[ \t]*\1[ \t]*$")


def json_objects(text):
    """Top-level JSON objects in a reply: the whole reply, fenced blocks, and
    objects that start a line. Most specific first is not needed; callers
    decide which one to use."""
    found = []

    def add(candidate):
        try:
            value = json.loads(candidate)
        except Exception:
            return
        if isinstance(value, dict):
            found.append(value)

    stripped = (text or "").strip()
    add(stripped)
    for match in _FENCE.finditer(text or ""):
        add(match.group(2).strip())
    decoder = json.JSONDecoder()
    for match in re.finditer(r"(?m)^[ \t]*\{", text or ""):
        try:
            value, _ = decoder.raw_decode(text, match.end() - 1)
        except Exception:
            continue
        if isinstance(value, dict):
            found.append(value)
    return found


def _halt_text(obj):
    return str(obj.get("reason") or obj.get("message") or "The model replied HALT.")


def halt_reason(text):
    for obj in json_objects(text):
        if obj.get("status") == "HALT":
            return _halt_text(obj)
    return None


def _status_object(value):
    status = str(value.get("status") or "").strip().upper() if isinstance(value, dict) else ""
    return dict(value, status=status) if status in ("DONE", "HALT") else None


def closing_object(text):
    """The status object a message ends with: {"status": "DONE"} or a HALT
    object. It counts when it is the last thing in the message and either
    stands alone on the last line(s) (or in a closing fenced block), or
    follows a finished sentence on that sentence's line ("...saved. {...}").
    Anything earlier in the message, inside a sentence, quoted, or followed
    by more text is not a closing line."""
    plain = (text or "").rstrip()
    text = plain.rstrip("`~*").rstrip()
    decoder = json.JSONDecoder()
    for match in reversed(list(re.finditer(r"(?m)^[ \t`*]*\{", text))):
        try:
            value, end = decoder.raw_decode(text, match.end() - 1)
        except Exception:
            continue
        if end == len(text):
            return _status_object(value)
    # Straight after a sentence that has ended (. ! ?), with nothing wrapped round it and nothing after it.
    start = max(0, len(plain) - 4000)
    for match in reversed(list(re.finditer(r"(?<=[.!?])[ \t]+\{", plain[start:]))):
        try:
            value, end = decoder.raw_decode(plain, start + match.end() - 1)
        except Exception:
            continue
        if end == len(plain):
            return _status_object(value)
    return None


def closing_status(text):
    return (closing_object(text) or {}).get("status")


def final_message(reply, content):
    """The text the model closed with: the message items after the last tool
    call in Open WebUI's saved `output` list. Narration written between tool
    calls is not part of it, so a status object quoted there decides nothing.
    A saved reply without that list is taken whole."""
    items = [i for i in (reply.get("output") or []) if isinstance(i, dict)]
    if not items:
        return content
    parts = []
    for item in reversed(items):
        if item.get("type") == "message":
            parts.insert(0, "".join(p["text"] for p in item.get("content") or []
                                    if isinstance(p, dict) and isinstance(p.get("text"), str)))
        elif item.get("type") != "reasoning":
            break
    return "\n".join(parts)


def reply_facts(reply):
    """What Open WebUI saved about how the reply ended, as raw observations.
    It saves no stop reason. The token counts belong to the last round that
    reported usage, which is not always the last round."""
    items = [i for i in (reply.get("output") or []) if isinstance(i, dict)]
    usage = reply.get("usage") if isinstance(reply.get("usage"), dict) else {}
    facts = {"last_item": items[-1].get("type") if items else None,
             "prompt_tokens": usage.get("prompt_eval_count", usage.get("prompt_tokens")),
             "generated_tokens": usage.get("eval_count", usage.get("completion_tokens")),
             "usage_incomplete": usage.get("incomplete")}
    return {k: v for k, v in facts.items() if v is not None}


def judge(final, missing, require_done, facts=None):
    """(status, note) for a reply whose chat ended without an error."""
    closing = closing_object(final) or {}
    if closing.get("status") == "HALT":
        return "halted", _halt_text(closing)
    closing = closing.get("status")
    # Without the closing line there is nothing to tell a HALT from a quoted one, so the
    # 0.2.0 rule stands: a top-level HALT object anywhere in the final message halts.
    quoted = halt_reason(final) if closing != "DONE" else None
    if quoted and not require_done:
        return "halted", quoted
    unfinished = ""
    if require_done and closing != "DONE":
        facts = facts or {}
        seen = [f"the reply ended on a {facts['last_item']} item"] if facts.get("last_item") else []
        if facts.get("prompt_tokens") is not None and facts.get("generated_tokens") is not None:
            seen.append(f"last reported round {facts['prompt_tokens']} prompt + "
                        f"{facts['generated_tokens']} generated tokens")
        unfinished = ("Unfinished: the final message did not end with " + DONE_LINE + ", so the step is not "
                      "known to be complete" + (" (" + "; ".join(seen) + "; Open WebUI records no stop reason)"
                                                if seen else "")
                      + (". A HALT object appears in the final message but is not its last line, so it was "
                         "not taken as a HALT" if quoted else "")
                      + ". Files and the reply are kept. Use \"Continue from this step\" on the Magazine page "
                      "to run it again in a fresh chat; typing in the chat does not restore the cartridge prompt.")
    if missing:
        return "failed", ("Required output file(s) not found: " + ", ".join(missing)
                          + (". " + unfinished if unfinished else ""))
    if unfinished:
        return "failed", unfinished
    return "done", None


def result_json(text):
    objects = [o for o in json_objects(text) if o != {"status": "DONE"}]
    return objects[0] if objects and (text or "").strip().startswith("{") else (objects[-1] if objects else None)


def select_pointer(value, pointer):
    for part in pointer[1:].split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict) and key in value:
            value = value[key]
        elif isinstance(value, list) and key.isdigit() and int(key) < len(value):
            value = value[int(key)]
        else:
            raise MagazineError(f"value {pointer!r} not found")
    return value


# ---------------------------------------------------------------------------
# Open WebUI client: the same public HTTP API the normal chat page uses.
# ---------------------------------------------------------------------------

class ChatFailed(RuntimeError):
    pass


class Stopped(RuntimeError):
    pass


class OpenWebUI:
    def __init__(self, base_url, token, transport=None, timeout=60.0):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.transport = transport
        self.timeout = timeout

    async def request(self, method, path, body=None):
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout,
                                     transport=self.transport) as client:
            response = await client.request(method, path, json=body, headers=headers)
        if response.status_code >= 400:
            detail = response.text[:300]
            raise ChatFailed(f"{method} {path} failed: HTTP {response.status_code} {detail}")
        try:
            return response.json()
        except Exception:
            raise ChatFailed(f"{method} {path} returned no JSON") from None

    async def models(self):
        data = await self.request("GET", "/api/models")
        items = data.get("data", data) if isinstance(data, dict) else data
        return [{"id": m.get("id"), "name": m.get("name") or m.get("id")}
                for m in items or [] if isinstance(m, dict) and m.get("id")]

    async def tools(self):
        data = await self.request("GET", "/api/v1/tools/")
        return [{"id": t.get("id"), "name": t.get("name") or t.get("id"),
                 "description": ((t.get("meta") or {}).get("description") or "")}
                for t in data or [] if isinstance(t, dict) and t.get("id")]

    async def terminals(self):
        data = await self.request("GET", "/api/v1/terminals/")
        return [{"id": t.get("id"), "name": t.get("name") or t.get("id")}
                for t in data or [] if isinstance(t, dict) and t.get("id") and t.get("enabled") is not False]


async def run_chat(client, *, model, prompt, title, tool_ids=(), terminal_id=None, params=None,
                   on_chat=None, should_stop=lambda: False, poll=None, save_wait=None):
    """One fresh saved chat, exactly as a person would start it. Returns
    (chat_id, reply_text, saved_assistant_message). Only the assistant message
    Magazine created is read, so anything typed into the chat afterwards is
    never taken for the cartridge's reply. There is no time limit; Stop ends it."""
    poll = POLL_SECONDS if poll is None else poll
    save_wait = SAVE_WAIT_SECONDS if save_wait is None else save_wait
    user_id, assistant_id, session_id = (str(uuid.uuid4()) for _ in range(3))
    ts = int(time.time())
    user_msg = {"id": user_id, "role": "user", "content": prompt, "timestamp": ts,
                "models": [model], "childrenIds": [assistant_id], "parentId": None}
    assistant_msg = {"id": assistant_id, "role": "assistant", "content": "", "parentId": user_id,
                     "childrenIds": [], "model": model, "modelName": model, "modelIdx": 0,
                     "done": False, "timestamp": ts + 1}
    chat = {"title": title, "models": [model], "messages": [user_msg, assistant_msg],
            "history": {"currentId": assistant_id,
                        "messages": {user_id: user_msg, assistant_id: assistant_msg}},
            "currentId": assistant_id}
    completion = {"id": assistant_id, "messages": [{"role": "user", "content": prompt}],
                  "model": model, "stream": True, "session_id": session_id,
                  "features": {"memory": False},
                  "background_tasks": {"title_generation": False, "tags_generation": False,
                                       "follow_up_generation": False}}
    if tool_ids:
        completion["tool_ids"] = list(tool_ids)
    if terminal_id:
        completion["terminal_id"] = terminal_id
        chat["terminalId"] = terminal_id
    if params:
        completion["params"] = json.loads(json.dumps(params))
        chat["params"] = json.loads(json.dumps(params))

    created = await client.request("POST", "/api/v1/chats/new", {"chat": chat})
    chat_id = created.get("id") if isinstance(created, dict) else None
    if not chat_id:
        raise ChatFailed("Open WebUI did not return a chat ID")
    if on_chat:
        on_chat(chat_id)
    if should_stop():
        raise Stopped()
    completion["chat_id"] = chat_id
    started = await client.request("POST", "/api/chat/completions", completion)
    if not (isinstance(started, dict) and started.get("status") is True):
        raise ChatFailed(f"Open WebUI did not start the chat: {str(started)[:200]}")

    while True:
        if should_stop():
            try:
                await client.request("POST", f"/api/tasks/chat/{chat_id}/stop")
            except Exception:
                pass
            raise Stopped()
        tasks = await client.request("GET", f"/api/tasks/chat/{chat_id}")
        if not (tasks or {}).get("task_ids"):
            break
        await asyncio.sleep(poll)

    deadline = time.monotonic() + save_wait
    while True:
        saved = await client.request("GET", f"/api/v1/chats/{chat_id}")
        messages = (((saved or {}).get("chat") or {}).get("history") or {}).get("messages") or {}
        reply = messages.get(assistant_id) or {}
        error = reply.get("error")
        if error:
            text = error.get("content") if isinstance(error, dict) else error
            raise ChatFailed(f"The model returned an error: {str(text)[:300]}")
        content = reply.get("content") or ""
        if not content.strip():
            parts = []
            for item in reply.get("output") or []:
                if isinstance(item, dict) and item.get("type") == "message":
                    for part in item.get("content") or []:
                        if isinstance(part, dict) and isinstance(part.get("text"), str):
                            parts.append(part["text"])
            content = "".join(parts)
        if reply.get("done") and content.strip():
            return chat_id, content, reply
        if time.monotonic() >= deadline:
            raise ChatFailed("The chat finished but no answer was saved")
        await asyncio.sleep(poll)


# ---------------------------------------------------------------------------
# Runs, files and the ledger.
# ---------------------------------------------------------------------------

def write_json(path, data):
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _open_dir(path, mode=0o777):
    """Make a folder with this mode. 0o777: the Terminal (which may run as a
    different user) can add files. 0o1777: it can add files but not rename or
    remove what Magazine put there. 0o755: it can only read."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    try:
        if not path.is_symlink():
            os.chmod(path, mode)
    except OSError:
        pass
    return path


def _others_can_rename_in(path):
    """True when the folder's mode lets other users rename or remove entries
    they do not own. Read back from the filesystem, because a chmod that
    returns without error is not proof (some bind mounts ignore it)."""
    try:
        mode = os.lstat(path).st_mode
    except OSError:
        return False
    return bool(mode & 0o002) and not mode & stat.S_ISVTX


def _replace_text(path, text, mode=0o644):
    """Write a file Magazine owns by putting a new file in its place, so a link
    planted under that name is replaced, never written through."""
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _open_plain(base, rel):
    """Open base/rel for reading only if it is a regular file reached without
    passing through a link. Raises OSError otherwise."""
    path = Path(base)
    if path.is_symlink():
        raise OSError(f"{path} is a link")
    for part in PurePosixPath(rel).parts:
        path = path / part
        if path.is_symlink():
            raise OSError(f"{path} is a link")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise OSError(f"{path} is not a regular file")
    return os.fdopen(fd, "rb")


def _hash_stream(src, dst=None):
    """(bytes, sha256) of an open file, read once in 1 MB pieces. With dst,
    the same pieces are written there, so what is hashed is what is copied."""
    digest, size = hashlib.sha256(), 0
    while True:
        chunk = src.read(1024 * 1024)
        if not chunk:
            return size, digest.hexdigest()
        digest.update(chunk)
        size += len(chunk)
        if dst is not None:
            dst.write(chunk)


class Store:
    """Run records live in Open WebUI's data folder; working files live in the
    shared workspace the Terminal can see."""

    def __init__(self, data_root, workspace_root):
        self.data_root = Path(data_root)
        self.workspace_root = Path(workspace_root)

    def run_dir(self, owner, run_id):
        return self.data_root / "runs" / owner / run_id

    def work_dir(self, run_id):
        return self.workspace_root / run_id

    def cartridge_dir(self, run_id, cid):
        return self.work_dir(run_id) / cid

    def load(self, owner, run_id):
        path = self.run_dir(owner, run_id) / "run.json"
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(run_id)) or not path.is_file():
            raise MagazineError("Run not found", 404)
        return json.loads(path.read_text(encoding="utf-8"))

    def save(self, run):
        folder = self.run_dir(run["owner"], run["id"])
        folder.mkdir(parents=True, exist_ok=True)
        run["updated"] = now_iso()
        write_json(folder / "run.json", run)
        ledger = render_ledger(run)
        (folder / "LEDGER.md").write_text(ledger, encoding="utf-8")
        try:
            # The run folder belongs to Magazine: the Terminal can read it and work
            # inside its own cartridge folder, but cannot add, rename or remove entries.
            work = _open_dir(self.work_dir(run["id"]), 0o755)
            _replace_text(work / "LEDGER.md", ledger)
        except OSError:
            pass  # the ledger in the data folder is still saved

    def runs(self, owner):
        root = self.data_root / "runs" / owner
        found = []
        if root.is_dir():
            for path in root.glob("*/run.json"):
                try:
                    found.append(json.loads(path.read_text(encoding="utf-8")))
                except Exception:
                    continue
        return sorted(found, key=lambda r: r.get("created", ""), reverse=True)


def new_run(store, owner, library, workflow_id, model_map, tool_ids, terminal_id):
    """model_map maps a step's id (or its model key) to an Open WebUI model."""
    wf = load_workflow(Path(library) / workflow_id)
    model_map = model_map or {}
    models = {c["id"]: model_map.get(c["id"]) or model_map.get(c["model"]) for c in wf["cartridges"]}
    missing = [cid for cid, m in models.items() if not m]
    if missing:
        raise MagazineError("Choose a model for: " + ", ".join(missing))
    run_id = _dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    folder = store.run_dir(owner, run_id)
    shutil.copytree(Path(library) / workflow_id, folder / "workflow")
    run = {"id": run_id, "owner": owner, "version": VERSION, "workflow_id": workflow_id,
           "workflow_name": wf["name"], "created": now_iso(), "status": "ready",
           "tool_ids": list(tool_ids or []), "terminal_id": terminal_id or None,
           "work_folder": str(store.work_dir(run_id)),
           "cartridges": [{"id": c["id"], "model": models[c["id"]], "prompt_file": c["prompt_file"],
                           "status": "pending", "attempts": []} for c in wf["cartridges"]]}
    store.save(run)
    return run


def build_prompt(cartridge, run, store, results, require_done=False):
    """The cartridge prompt, unchanged, followed by the inputs it asked for and,
    with require_done, how to close the reply."""
    prompt = cartridge["prompt"].rstrip()
    extra = []
    if cartridge["handover"]:
        lines = ["Inputs from earlier steps:"]
        for h in cartridge["handover"]:
            source = results.get(h["from"])
            data = result_json(source or "")
            if data is None:
                raise MagazineError(f"{h['from']} did not reply with JSON, so {h['as']!r} is unavailable")
            value = select_pointer(data, h["select"])
            lines.append(f"\n{h['as']} (from {h['from']}):\n```json\n"
                         + json.dumps(value, indent=2, ensure_ascii=False) + "\n```")
        extra.append("\n".join(lines))
    if cartridge["inputs"] or cartridge["outputs"]:
        out_dir = store.cartridge_dir(run["id"], cartridge["id"]) / "outputs"
        lines = ["Files for this step:", f"- Save your output files in: {out_dir}"]
        if cartridge["outputs"]:
            lines.append("- Required output files: " + ", ".join(cartridge["outputs"]))
        if cartridge["inputs"]:
            lines.append("- Input files:")
            for f in cartridge["inputs"]:
                local = store.cartridge_dir(run["id"], cartridge["id"]) / "inputs" / f["from"] / f["path"]
                lines.append(f"  - {local} (from {f['from']}: {f['path']})")
        extra.append("\n".join(lines))
    if require_done:
        extra.append(DONE_INSTRUCTION)
    if not extra:
        return prompt
    return prompt + "\n\n---\n" + "\n\n".join(extra) + "\n"


def recorded_output(run, cid, rel):
    """What Magazine recorded for a declared output when its cartridge finished."""
    for record in run.get("cartridges") or []:
        if record.get("id") == cid:
            for attempt in reversed(record.get("attempts") or []):
                if attempt.get("status") == "done":
                    return next((f for f in attempt.get("outputs") or [] if f.get("path") == rel), None)
    return None


def prepare_files(cartridge, run, store):
    """Make the cartridge folder and hand over its declared inputs. Each input is
    read once: the bytes that are hashed are the bytes that are copied, and they
    must match what was recorded when the earlier cartridge finished."""
    here = store.cartridge_dir(run["id"], cartridge["id"])
    for path in (here, here / "inputs", here / "outputs"):
        if path.is_symlink():
            raise MagazineError(f"{path} is a link; Magazine did not make it and will not use it")
    # The cartridge folder is writable (sticky, so the Terminal cannot rename or remove
    # the inputs/ and outputs/ folders Magazine made); outputs/ is plainly writable.
    _open_dir(here, 0o1777)
    _open_dir(here / "outputs")
    delivered = []
    for f in cartridge["inputs"]:
        what = f"Input file {f['path']} from {f['from']}"
        source = store.cartridge_dir(run["id"], f["from"])
        recorded = recorded_output(run, f["from"], f["path"])
        dst = here / "inputs" / f["from"] / f["path"]
        try:
            src = _open_plain(source, "outputs/" + f["path"])
        except OSError:
            raise MagazineError(f"{what} is missing or is not a regular file") from None
        part = dst.with_name(dst.name + ".part")
        with src:
            _open_dir(dst.parent)
            with open(part, "wb") as out:
                size, digest = _hash_stream(src, out)
        if recorded and (digest != recorded.get("sha256") or size != recorded.get("bytes")):
            os.unlink(part)
            raise MagazineError(
                f"{what} changed after {f['from']} finished: recorded {recorded.get('bytes')} B "
                f"{str(recorded.get('sha256'))[:12]}, found {size} B {digest[:12]}. Nothing was handed over; "
                f"the changed file is still in {f['from']}/outputs.")
        os.replace(part, dst)
        try:
            os.chmod(dst, 0o666)
        except OSError:
            pass
        delivered.append({"from": f["from"], "path": f["path"], "bytes": size, "sha256": digest,
                          "verified": bool(recorded)})
    return delivered


def collect_outputs(cartridge, run, store):
    """Size and SHA-256 of each declared output that is a regular file."""
    here = store.cartridge_dir(run["id"], cartridge["id"])
    files, missing = [], []
    for rel in cartridge["outputs"]:
        try:
            with _open_plain(here, "outputs/" + rel) as src:
                size, digest = _hash_stream(src)
            files.append({"path": rel, "bytes": size, "sha256": digest})
        except OSError:
            missing.append(rel + (" (a link, not a file)" if (here / "outputs" / rel).is_symlink() else ""))
    return files, missing


def seal_cartridge(cartridge, run, store):
    """A finished cartridge's folder goes back to Magazine: the Terminal can read
    it, but cannot add, rename or remove entries in it or directly in outputs/.
    Files the Terminal made stay its own; the handover hash check covers those."""
    here = store.cartridge_dir(run["id"], cartridge["id"])
    for path in (here / "outputs", here):
        try:
            if path.is_dir() and not path.is_symlink():
                os.chmod(path, 0o755)
        except OSError:
            pass


def _fmt_time(value):
    return value.replace("T", " ").replace("+00:00", " UTC") if value else ""


def _duration(start, end):
    try:
        seconds = int((_dt.datetime.fromisoformat(end) - _dt.datetime.fromisoformat(start)).total_seconds())
    except Exception:
        return ""
    return f"{seconds // 60}m {seconds % 60:02d}s"


def render_ledger(run):
    lines = [f"# Ledger: {run['workflow_name']}", "",
             f"- Run: `{run['id']}`", f"- Started: {_fmt_time(run['created'])}",
             f"- Status: **{run['status']}**", f"- Work folder: `{run.get('work_folder', '')}`",
             "- Models: " + ", ".join(sorted({f"`{c['model']}`" for c in run["cartridges"]})),
             f"- Terminal: {run.get('terminal_id') or 'none'}",
             f"- Tools: {', '.join(run.get('tool_ids') or []) or 'none'}", "",
             "| # | Cartridge | Attempt | Model | Status | Started | Took | Chat | Files produced |",
             "|---|---|---|---|---|---|---|---|---|"]
    for index, c in enumerate(run["cartridges"], 1):
        if not c["attempts"]:
            lines.append(f"| {index} | {c['id']} | – | `{c['model']}` | {c['status']} | | | | |")
        for n, a in enumerate(c["attempts"], 1):
            chat = f"[open](/c/{a['chat_id']})" if a.get("chat_id") else ""
            files = "<br>".join(f"{f['path']} ({f['bytes']} B, {f['sha256'][:12]})" for f in a.get("outputs", []))
            lines.append(f"| {index} | {c['id']} | {n} | `{a['model']}` | {a['status']} | "
                         f"{_fmt_time(a.get('started'))} | {_duration(a.get('started'), a.get('finished'))} | "
                         f"{chat} | {files} |")
    notes = [(c["id"], n, a["note"]) for c in run["cartridges"]
             for n, a in enumerate(c["attempts"], 1) if a.get("note")]
    if notes or run.get("warning"):
        lines += ["", "## Notes", ""]
        lines += [f"- **Work folder:** {run['warning']}"] if run.get("warning") else []
        lines += [f"- **{cid}, attempt {n}:** {note}" for cid, n, note in notes]
    lines += ["", f"_Magazine Simple {VERSION}. Chats are kept in Open WebUI; replies are saved in results/._", ""]
    return "\n".join(lines)


class Engine:
    """Runs one cartridge at a time. One run at a time per Open WebUI process.
    Runs started while another is running wait in a first-come queue and
    start on their own when the model is free."""

    def __init__(self, store, library, client_factory):
        self.store = store
        self.library = library
        self.client_factory = client_factory
        self.active = {}      # run_id -> asyncio.Task
        self.stop_flags = set()
        self.queue = []       # [{run_id, owner, token, params, from_index}]
        self.require_done = REQUIRE_DONE

    def busy(self):
        return any(not t.done() for t in self.active.values())

    def position(self, run_id):
        for n, entry in enumerate(self.queue, 1):
            if entry["run_id"] == run_id:
                return n
        return None

    def queue_view(self):
        return {"running": list(self.active), "waiting": [e["run_id"] for e in self.queue]}

    def dequeue(self, run, save=True):
        """Take a waiting run out of the queue; it goes back to how it was."""
        self.queue = [e for e in self.queue if e["run_id"] != run["id"]]
        if run.get("status") == "queued":
            run["status"] = run.pop("queued_from", None) or "ready"
            if save:
                self.store.save(run)

    def mark_interrupted(self, owner):
        for run in self.store.runs(owner):
            if run["status"] == "queued" and self.position(run["id"]) is None:
                self.dequeue(run)    # the queue lives in memory; after a restart, press Start again
            if run["status"] == "running" and run["id"] not in self.active:
                run["status"] = "interrupted"
                for c in run["cartridges"]:
                    if c["status"] == "running":
                        c["status"] = "interrupted"
                        c["attempts"][-1].update(status="interrupted", finished=now_iso(),
                                                 note="Open WebUI restarted while this cartridge was running.")
                self.store.save(run)

    def start(self, run, token, params, from_index=0):
        """Start now if the model is free, otherwise join the queue.
        Returns the queue position (None when started)."""
        if run["id"] in self.active or self.position(run["id"]):
            raise MagazineError("This run is already running or waiting.", 409)
        if from_index > 0:
            for c in run["cartridges"][:from_index]:
                if c["status"] != "done":
                    raise MagazineError(f"{c['id']} has not completed, so the run cannot restart after it.", 409)
        if self.busy() or self.queue:
            run["queued_from"] = run["status"]
            run["status"] = "queued"
            self.store.save(run)
            self.queue.append({"run_id": run["id"], "owner": run["owner"], "token": token,
                               "params": params, "from_index": from_index})
            return len(self.queue)
        self._launch(run, token, params, from_index)
        return None

    def _next(self):
        while self.queue and not self.busy():
            entry = self.queue.pop(0)
            try:
                run = self.store.load(entry["owner"], entry["run_id"])
            except Exception:
                continue
            if run.get("status") != "queued":
                continue
            run["status"] = run.pop("queued_from", None) or "ready"
            try:
                self._launch(run, entry["token"], entry["params"], entry["from_index"])
            except Exception as exc:  # keep the queue moving; record why this one did not start
                run["status"] = "failed"
                run["note"] = f"Could not start from the queue: {exc}"[:500]
                self.store.save(run)

    def _launch(self, run, token, params, from_index):
        results = self.store.run_dir(run["owner"], run["id"]) / "results"
        for index, c in enumerate(run["cartridges"]):
            if index < from_index:
                continue
            if c["attempts"]:
                kept = f"~attempt{len(c['attempts'])}"
                old = self.store.cartridge_dir(run["id"], c["id"])
                if old.exists():
                    os.rename(old, old.with_name(c["id"] + kept))
                for suffix in (".md", ".prompt.md"):   # the earlier reply and prompt stay as evidence
                    saved = results / f"{index + 1:02d}-{c['id']}{suffix}"
                    if saved.is_file():
                        os.replace(saved, results / f"{index + 1:02d}-{c['id']}{kept}{suffix}")
            c["status"] = "pending"
        run["status"] = "running"
        self.store.save(run)
        work = self.store.work_dir(run["id"])
        if _others_can_rename_in(work) or _others_can_rename_in(work.parent):
            run["warning"] = ("this filesystem lets other users rename or replace entries in the run folder "
                              "(the requested permissions did not take effect, or the workspace root is "
                              "open). Handed-over files are still checked against their recorded hashes.")
        else:
            run.pop("warning", None)
        self.store.save(run)
        self.stop_flags.discard(run["id"])
        task = asyncio.ensure_future(self._execute(run, self.client_factory(token), params, from_index))
        task.magazine_owner = run["owner"]
        self.active[run["id"]] = task
        return task

    def stop(self, run_id):
        self.stop_flags.add(run_id)

    async def _execute(self, run, client, params, from_index):
        folder = self.store.run_dir(run["owner"], run["id"])
        workflow = load_workflow(folder / "workflow")
        results_dir = folder / "results"
        results_dir.mkdir(exist_ok=True)
        results = {}
        for index, (c, record) in enumerate(zip(workflow["cartridges"], run["cartridges"])):
            path = results_dir / f"{index + 1:02d}-{c['id']}.md"
            if index < from_index and path.is_file():
                results[c["id"]] = path.read_text(encoding="utf-8")
        try:
            for index in range(from_index, len(workflow["cartridges"])):
                c, record = workflow["cartridges"][index], run["cartridges"][index]
                if run["id"] in self.stop_flags:
                    run["status"] = "stopped"
                    break
                model = record["model"]
                attempt = {"model": model, "status": "running", "started": now_iso(), "chat_id": None}
                record["attempts"].append(attempt)
                record["status"] = "running"
                self.store.save(run)

                def on_chat(chat_id, attempt=attempt):
                    attempt["chat_id"] = chat_id
                    self.store.save(run)
                try:
                    attempt["inputs"] = prepare_files(c, run, self.store)
                    unverified = [f["path"] for f in attempt["inputs"] if not f["verified"]]
                    prompt = build_prompt(c, run, self.store, results, self.require_done)
                    path = results_dir / f"{index + 1:02d}-{c['id']}.md"
                    path.with_suffix(".prompt.md").write_text(prompt, encoding="utf-8")
                    title = f"Magazine · {run['workflow_name']} · {c['id']}"
                    _, reply, saved = await run_chat(client, model=model, prompt=prompt, title=title,
                                                     tool_ids=run["tool_ids"], terminal_id=run["terminal_id"],
                                                     params=params, on_chat=on_chat,
                                                     should_stop=lambda: run["id"] in self.stop_flags)
                    path.write_text(reply, encoding="utf-8")
                    results[c["id"]] = reply
                    attempt["ended"] = reply_facts(saved)
                    attempt["outputs"], missing = collect_outputs(c, run, self.store)
                    status, note = judge(final_message(saved, reply), missing, self.require_done,
                                         attempt["ended"])
                    if status == "done":
                        seal_cartridge(c, run, self.store)
                        if unverified:
                            note = ("Handed over without an integrity check (no recorded hash for the "
                                    "earlier step): " + ", ".join(unverified))
                    attempt["status"] = status
                    if note:
                        attempt["note"] = note
                except Stopped:
                    attempt.update(status="stopped", note="Stopped by you.")
                except (MagazineError, ChatFailed) as exc:
                    attempt.update(status="failed", note=str(exc))
                except Exception as exc:  # report anything unexpected, keep the run record
                    attempt.update(status="failed", note=f"Unexpected error: {exc!r}"[:500])
                attempt["finished"] = now_iso()
                record["status"] = attempt["status"]
                self.store.save(run)
                if attempt["status"] != "done":
                    run["status"] = attempt["status"]
                    break
            else:
                run["status"] = "completed"
        finally:
            if run["status"] == "running":
                run["status"] = "stopped"
            self.store.save(run)
            self.stop_flags.discard(run["id"])
            self.active.pop(run["id"], None)
            self._next()


def delete_run(store, engine, owner, run_id):
    """Remove one of the owner's runs for good: its work folder (earlier attempts
    included) and its record, ledger, saved replies, prompts and workflow copy.
    Chats are not touched. A run that is running or waiting is refused."""
    if not isinstance(run_id, str):
        raise MagazineError("Run not found", 404)
    run = store.load(owner, run_id)          # checks the id's form and that the run is this owner's
    if run_id in engine.active or engine.position(run_id):
        raise MagazineError(f"{run['workflow_name']} ({run_id}) is running or waiting. Stop it first.", 409)
    work = store.work_dir(run_id)
    if work.is_symlink():
        os.unlink(work)                      # a link is removed, never followed
    elif work.is_dir():
        shutil.rmtree(work)
    record = store.run_dir(owner, run_id)
    for entry in sorted(record.iterdir()):   # run.json goes last: a deletion that fails stays listed and can be repeated
        if entry.name == "run.json":
            continue
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()
    shutil.rmtree(record)
    return {"deleted": run_id}


def build_download(store, run):
    folder = store.run_dir(run["owner"], run["id"])
    buffer = io.BytesIO()
    total = 0
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("LEDGER.md", render_ledger(run))
        archive.writestr("run.json", json.dumps(run, indent=2, ensure_ascii=False))
        for base, prefix in ((folder / "workflow", "workflow"), (folder / "results", "results"),
                             (store.work_dir(run["id"]), "work")):
            if not base.is_dir():
                continue
            for path in sorted(base.rglob("*")):
                if path.is_file() and not path.is_symlink() and path.name != "LEDGER.md":
                    total += path.stat().st_size
                    if total > MAX_DOWNLOAD_BYTES:
                        archive.writestr("TRUNCATED.txt", "Download limit reached; remaining files are in the work folder.")
                        return buffer.getvalue()
                    archive.write(path, f"{prefix}/{path.relative_to(base).as_posix()}")
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Open WebUI integration.
# ---------------------------------------------------------------------------

def _data_root():
    try:
        from open_webui.config import DATA_DIR
        return Path(DATA_DIR) / "magazine-simple"
    except Exception:
        return Path("/app/backend/data/magazine-simple")


def _base_url(configured):
    if configured and configured.strip():
        return configured.strip().rstrip("/")
    port = os.environ.get("PORT", "8080")
    return f"http://127.0.0.1:{port if port.isdigit() else '8080'}"


def _token(request):
    auth = request.headers.get("Authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.cookies.get("token") or ""


def _user_params(user):
    """The user's own saved chat settings, as a normal chat would use them."""
    settings = getattr(user, "settings", None) or {}
    if callable(getattr(settings, "model_dump", None)):
        settings = settings.model_dump(exclude_none=True)
    ui = settings.get("ui") if isinstance(settings, dict) else None
    ui = ui if isinstance(ui, dict) else {}
    params = ui.get("params") if isinstance(ui.get("params"), dict) else {}
    params = json.loads(json.dumps(params))
    if isinstance(ui.get("system"), str) and ui["system"] and not params.get("system"):
        params["system"] = ui["system"]
    return params


_STATE = {"engine": None, "routes": False}


def install_routes(app, valves):
    from fastapi import APIRouter, Depends, HTTPException
    from fastapi.responses import HTMLResponse, Response
    from open_webui.utils.auth import get_current_user

    library = valves.library_root.strip() or str(_data_root() / "library")
    store = Store(_data_root(), valves.workspace_root)
    engine = _STATE.get("engine")
    if engine is None:
        engine = Engine(store, library,
                        lambda token: OpenWebUI(_base_url(valves.open_webui_base_url), token))
        _STATE["engine"] = engine
    engine.store, engine.library = store, library
    engine.require_done = bool(getattr(valves, "require_done_line", REQUIRE_DONE))
    engine.client_factory = lambda token: OpenWebUI(_base_url(valves.open_webui_base_url), token)
    router = APIRouter()
    auth = Depends(get_current_user)

    def fail(exc):
        if isinstance(exc, MagazineError):
            raise HTTPException(status_code=exc.status_code, detail=str(exc))
        if isinstance(exc, ChatFailed):
            raise HTTPException(status_code=502, detail=str(exc))
        raise HTTPException(status_code=500, detail=f"Unexpected error: {exc}")

    @router.get(PAGE_PATH, response_class=HTMLResponse)
    async def page():
        return PAGE_HTML

    @router.get(API + "/workflows")
    async def workflows(user=auth):
        # Any signed-in user may add a workflow, as any signed-in user may delete one.
        return {"workflows": list_workflows(library), "can_import": True}

    @router.get(API + "/workflows/{workflow_id}")
    async def workflow(workflow_id: str, user=auth):
        try:
            return workflow_summary(library, workflow_id)
        except Exception as exc:
            fail(exc)

    @router.post(API + "/workflows/delete")
    async def remove_workflows(request: Request, user=auth):
        try:
            body = await request.json()
            if body.get("all"):
                ids = [w["id"] for w in list_workflows(library)]
            else:
                ids = body.get("ids")
                if not isinstance(ids, list) or not ids:
                    raise MagazineError("Choose at least one workflow to delete.")
            deleted, failed = [], []
            for wid in ids:
                try:
                    delete_workflow(library, wid)
                    deleted.append(wid)
                except MagazineError as exc:
                    failed.append({"id": str(wid), "error": str(exc)})
            return {"deleted": deleted, "failed": failed}
        except Exception as exc:
            fail(exc)

    @router.delete(API + "/workflows/{workflow_id}")
    async def remove_workflow(workflow_id: str, user=auth):
        try:
            return delete_workflow(library, workflow_id)
        except Exception as exc:
            fail(exc)

    @router.post(API + "/workflows/import")
    async def import_zip(request: Request, user=auth):
        try:
            body = await request.json()
            raw = base64.b64decode(body.get("data_b64") or "", validate=True)
            return import_workflow(library, body.get("file_name", ""), raw,
                                   body.get("workflow_id") or "")
        except Exception as exc:
            fail(exc)

    @router.get(API + "/options")
    async def options(request: Request, user=auth):
        client = engine.client_factory(_token(request))
        result = {}
        for key, call in (("models", client.models), ("tools", client.tools), ("terminals", client.terminals)):
            try:
                result[key] = await call()
            except Exception as exc:
                result[key] = []
                result[key + "_error"] = str(exc)[:200]
        return result

    @router.get(API + "/runs")
    async def runs(user=auth):
        engine.mark_interrupted(user.id)
        runs = [{k: r.get(k) for k in ("id", "workflow_name", "status", "created", "updated")}
                for r in store.runs(user.id)]
        for r in runs:
            r["queue_position"] = engine.position(r["id"])
        return {"runs": runs, "busy": engine.busy()}

    @router.get(API + "/queue")
    async def queue(user=auth):
        """What is running and what is waiting. Other people's runs are shown without names."""
        def describe(run_id, owner):
            if owner != user.id:
                return {"id": None, "workflow_name": "Someone else's workflow", "mine": False}
            try:
                r = store.load(owner, run_id)
                return {"id": run_id, "workflow_name": r["workflow_name"], "mine": True}
            except Exception:
                return {"id": run_id, "workflow_name": run_id, "mine": True}
        running = []
        for run_id, task in engine.active.items():
            owner = getattr(task, "magazine_owner", None)
            running.append(describe(run_id, owner))
        waiting = [describe(e["run_id"], e["owner"]) for e in engine.queue]
        return {"running": running, "waiting": waiting}

    @router.post(API + "/runs")
    async def create(request: Request, user=auth):
        try:
            body = await request.json()
            run = new_run(store, user.id, library, body.get("workflow_id"),
                          body.get("model_map") or {}, body.get("tool_ids") or [], body.get("terminal_id"))
            if body.get("start"):
                run["queue_position"] = engine.start(run, _token(request), _user_params(user))
            return run
        except Exception as exc:
            fail(exc)

    @router.post(API + "/runs/delete")
    async def remove_runs(request: Request, user=auth):
        """Delete the caller's own previous work: the ids given, or all of it."""
        try:
            body = await request.json()
            if body.get("all"):
                ids = [r["id"] for r in store.runs(user.id)]
            else:
                ids = body.get("ids")
                if not isinstance(ids, list) or not ids:
                    raise MagazineError("Choose at least one item of previous work to delete.")
            deleted, failed = [], []
            for rid in ids:
                try:
                    deleted.append(delete_run(store, engine, user.id, rid)["deleted"])
                except MagazineError as exc:
                    failed.append({"id": str(rid), "error": str(exc)})
                except OSError as exc:
                    failed.append({"id": str(rid), "error": f"{rid} could not be removed completely: {exc}"})
            return {"deleted": deleted, "failed": failed}
        except Exception as exc:
            fail(exc)

    @router.post(API + "/runs/{run_id}/start")
    async def start(run_id: str, request: Request, user=auth):
        try:
            run = store.load(user.id, run_id)
            if run["status"] != "ready":
                raise MagazineError("This run has already started. Use Continue from this step instead.", 409)
            position = engine.start(run, _token(request), _user_params(user))
            return {"started": position is None, "queue_position": position}
        except Exception as exc:
            fail(exc)

    @router.get(API + "/runs/{run_id}")
    async def get_run(run_id: str, user=auth):
        try:
            run = store.load(user.id, run_id)
            run["ledger"] = render_ledger(run)
            run["queue_position"] = engine.position(run_id)
            results = store.run_dir(user.id, run_id) / "results"
            for index, c in enumerate(run["cartridges"], 1):
                path = results / f"{index:02d}-{c['id']}.md"
                if path.is_file():
                    text = path.read_text(encoding="utf-8")
                    c["reply"] = text if len(text) <= 20000 else text[:20000] + "\n… (full reply in the download)"
            return run
        except Exception as exc:
            fail(exc)

    @router.post(API + "/runs/{run_id}/stop")
    async def stop(run_id: str, user=auth):
        try:
            run = store.load(user.id, run_id)
        except Exception as exc:
            fail(exc)
        if engine.position(run_id):
            engine.dequeue(run)
            return {"stopping": False, "removed_from_queue": True}
        engine.stop(run_id)
        return {"stopping": True}

    @router.post(API + "/runs/{run_id}/restart")
    async def restart(run_id: str, request: Request, user=auth):
        try:
            body = await request.json()
            run = store.load(user.id, run_id)
            if run_id in engine.active:
                raise MagazineError("This run is still running. Stop it first.", 409)
            ids = [c["id"] for c in run["cartridges"]]
            if body.get("cartridge_id") not in ids:
                raise MagazineError("Choose a cartridge to restart from")
            position = engine.start(run, _token(request), _user_params(user), ids.index(body["cartridge_id"]))
            return {"restarted": position is None, "queue_position": position}
        except Exception as exc:
            fail(exc)

    @router.get(API + "/runs/{run_id}/download")
    async def download(run_id: str, user=auth):
        try:
            run = store.load(user.id, run_id)
        except Exception as exc:
            fail(exc)
        data = build_download(store, run)
        return Response(data, media_type="application/zip", headers={
            "Content-Disposition": f'attachment; filename="Magazine-{run_id}.zip"'})

    def mine(route):
        path = getattr(route, "path", "") or ""
        return path == PAGE_PATH or path.startswith(API + "/")

    app.router.routes[:] = [r for r in app.router.routes if not mine(r)]
    for route in reversed(list(router.routes)):
        app.router.routes.insert(0, route)
    _STATE["routes"] = True


def remove_routes(app):
    app.router.routes[:] = [r for r in app.router.routes
                            if not ((getattr(r, "path", "") or "") == PAGE_PATH
                                    or (getattr(r, "path", "") or "").startswith(API + "/"))]
    _STATE["routes"] = False


class Event:
    class Valves(BaseModel):
        library_root: str = ""  # empty: <DATA_DIR>/magazine-simple/library
        workspace_root: str = DEFAULT_WORKSPACE
        open_webui_base_url: str = ""
        require_done_line: bool = REQUIRE_DONE  # off: a step is done when its files exist, as in 0.2.0

    def __init__(self):
        self.valves = self.Valves()

    async def event(self, __app__=None, **kwargs):
        if __app__ is None:
            return
        payload = kwargs.get("event") or {}
        name = kwargs.get("__event_name__") or payload.get("event")
        subject = (payload.get("subject") or {}).get("id")
        if name == "function.disable_started" and subject == kwargs.get("__id__"):
            remove_routes(__app__)
            return
        install_routes(__app__, self.valves)


PAGE_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Magazine — Workflows</title>
<style>
  :root{--bg:#f5f7fb;--card:#ffffff;--soft:#eef2f8;--ink:#172033;--muted:#49576c;--line:#778399;--accent:#175bc4;--button-ink:#ffffff;--ok:#17622f;--err:#a32721;color-scheme:light}
  [data-theme="dark"]{--bg:#111827;--card:#182234;--soft:#202d42;--ink:#edf2fa;--muted:#c0ccde;--line:#78879d;--accent:#98c0ff;--button-ink:#111827;--ok:#91ddb0;--err:#ffaaa3;color-scheme:dark}
  *{box-sizing:border-box}
  body{font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;max-width:1080px;margin:20px auto;padding:0 16px;color:var(--ink);background:var(--bg);line-height:1.4}
  h1{font-size:22px;margin:0 0 4px}
  p{margin:8px 0} header,.actions{display:flex;gap:10px;align-items:center;flex-wrap:wrap} header{justify-content:space-between;margin-bottom:16px}
  section{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin-bottom:12px}
  .card-header{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:12px;gap:12px;flex-wrap:wrap}
  h2{font-size:17px;margin:0} h3{font-size:15px;margin:0 0 8px}
  .badge{font-size:12px;font-weight:600;padding:2px 8px;border:1px solid var(--line);border-radius:999px;background:var(--soft)}
  .badge.ok{color:var(--ok)} .badge.err{color:var(--err)}
  input,select,textarea{max-width:100%;font:inherit;font-size:14px;padding:8px;border:1px solid var(--line);border-radius:7px;background:var(--card);color:var(--ink)}
  select{min-width:140px}
  button{cursor:pointer;border:1px solid var(--line);border-radius:7px;padding:8px 14px;background:var(--card);color:var(--ink);font:inherit;font-size:14px;font-weight:600}
  button.primary{background:var(--accent);color:var(--button-ink);border-color:var(--accent)}
  button:hover:not(:disabled){filter:brightness(.94)} button:disabled{cursor:not-allowed;border-style:dashed;background:var(--soft);color:var(--muted);border-color:var(--line)}
  button.small{padding:4px 10px;font-size:13px}
  :focus-visible{outline:3px solid var(--accent);outline-offset:3px}
  a{color:var(--accent)}
  .field-group{display:flex;flex-direction:column;gap:6px;min-width:0}
  .field-group + .field-group{margin-top:10px}
  .field-group + button{margin-top:10px}
  .field-group > input,.field-group > select{align-self:flex-start;min-width:0;max-width:100%}
  .field-actions{display:flex;flex-wrap:wrap;align-items:center;gap:10px;min-width:0}
  .field-actions > select{flex:1 1 260px;min-width:0;max-width:100%}
  .field-actions > button{flex:0 0 auto;max-width:100%}
  .queue{border:1px solid var(--border,#ccc);border-radius:10px;padding:10px 14px;margin:0 0 14px}
  .queue h3{margin:0 0 4px}
  .queue ul{margin:6px 0 0;padding-left:22px}
  .queue li{margin:4px 0;overflow-wrap:anywhere}
  .delete-list{display:flex;flex-direction:column;gap:6px;margin:8px 0 12px}
  .delete-list label{display:flex;align-items:center;gap:8px;font-weight:normal;overflow-wrap:anywhere}
  .delete-list input{width:auto;margin:0}
  .workflow-name,#library-msg,#library-guidance{min-width:0;max-width:100%;overflow-wrap:anywhere}
  details{margin:10px 0} summary{cursor:pointer;color:var(--muted)}
  .cartridge{display:grid;grid-template-columns:54px minmax(80px,1fr) minmax(170px,1.5fr) minmax(160px,2fr);gap:10px;align-items:start;background:var(--soft);border:1px solid var(--line);border-radius:8px;padding:10px;margin-bottom:8px}
  .cartridge .lead{display:flex;align-items:center;gap:6px;min-height:24px;cursor:pointer}
  .cartridge .pick{width:auto;margin:0;flex:0 0 auto}
  .cartridge.shared .pick{display:none}
  .cartridge .ord{font-weight:800;color:var(--accent);font-size:14px}
  #cartridge-s > summary{color:var(--ink);font-weight:600;font-size:15px}
  #cartridge-s > summary h3{display:inline;margin:0;font:inherit}
  #cartridge-s > summary .badge{margin-left:8px}
  .bulk{background:var(--soft);border:1px solid var(--line);border-radius:8px;padding:10px;margin-bottom:12px}
  .bulk .field-group{margin-top:10px}
  .bulk-count{font-size:13px;font-weight:600}
  .cartridge .id{font-size:14px;overflow-wrap:anywhere}
  .cartridge .cellm{min-width:0}
  .cartridge.shared{grid-template-columns:28px minmax(80px,1fr) minmax(160px,3.5fr)}
  .cartridge.shared .cellm{display:none}
  #model-mode-control,#shared-model-control{margin-bottom:12px}
  .cartridge .mk{font-size:12px;color:var(--muted);margin-bottom:4px;display:block}
  .cartridge select{width:100%}
  .cartridge .prompt{font-size:13px;color:var(--muted);max-height:140px;overflow:auto;overflow-wrap:anywhere;white-space:pre-wrap}
  .tool-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,18rem),1fr));align-items:start;gap:8px;max-height:min(24rem,50vh);overflow-y:auto;overflow-x:hidden;min-width:0;padding:6px}
  .tool{min-width:0;border:1px solid var(--line);border-radius:8px;padding:8px 10px}
  .tool label{display:flex;align-items:flex-start;gap:8px;min-width:0;cursor:pointer}
  .tool .tool-text{min-width:0;flex:1}
  .tool input[type=checkbox]{flex:0 0 auto;margin:2px 0}
  .tool .tn,.tool .td{display:block;overflow-wrap:anywhere}
  .tool .tn{font-size:13px;font-weight:600}
  .tool .td{font-size:11px;color:var(--muted)}
  .result{background:var(--soft);border:1px solid var(--line);border-radius:8px;padding:12px;margin-top:12px}
  .msg{font-size:13px;margin:8px 0}
  .msg.err{color:var(--err)}
  .msg.ok{color:var(--ok)}
  .muted{color:var(--muted);font-size:12px}
  .hidden{display:none!important}
  .pre-doc,.output{white-space:pre-wrap;overflow-wrap:anywhere;max-height:260px;overflow:auto;font-size:14px;background:var(--card);color:var(--ink);padding:10px;border-radius:6px;margin:6px 0 0}
  .output{font-family:inherit} .saved-result{border-top:1px solid var(--line);padding-top:12px;margin-top:12px}
  progress{width:100%;height:18px;accent-color:var(--accent)}
  .timeline{padding-left:24px;max-height:320px;overflow:auto}
  .timeline li{padding:4px 0} .timeline .finished{color:var(--ok)} .timeline .working{font-weight:700} .timeline .problem{color:var(--err)}
  .timeline .note{display:block;font-size:12px;color:var(--muted);font-weight:400}
  .ledger{width:100%;border-collapse:collapse;font-size:13px;margin-top:6px}
  .ledger th,.ledger td{text-align:left;padding:5px 6px;border-bottom:1px solid var(--line);vertical-align:top}
  .ledger th{color:var(--muted);font-weight:600}
  @media(max-width:700px){body{padding:0 10px}.cartridge{grid-template-columns:54px 1fr}.cartridge.shared{grid-template-columns:24px 1fr}.cartridge .cellm,.cartridge .prompt{grid-column:2}.actions>*{max-width:100%}section{padding:12px}.tool-grid{grid-template-columns:1fr}.ledger{display:block;overflow-x:auto}}
</style>
</head>
<body>
<header><h1>Magazine</h1><div class="actions"><label for="appearance">Appearance</label><select id="appearance"><option value="follow">Follow Open WebUI</option><option value="light">Light</option><option value="dark">Dark</option></select></div></header>
<main>
<section aria-labelledby="workflow-heading">
  <div class="card-header">
    <h2 id="workflow-heading">Your workflows</h2>
    <span id="library-badge" class="badge muted">loading</span>
  </div>
  <div class="field-group">
    <label for="library-workflows">Workflow</label>
    <div class="field-actions">
      <select id="library-workflows" aria-describedby="library-guidance library-msg"></select>
      <button id="btn-library-select" class="primary" disabled>Load your workflow</button>
      <button id="btn-library-refresh">Refresh list</button>
    </div>
  </div>
  <p id="library-guidance" class="muted" role="status">Select a workflow, then choose Load your workflow.</p>
  <p id="library-msg" class="msg hidden" role="status"></p>
  <details id="delete-details"><summary>Delete workflows</summary>
    <p class="muted">Tick the workflows to remove from the list. Previous work and its results are kept.</p>
    <div id="delete-list" class="delete-list"></div>
    <div class="field-actions">
      <button id="btn-delete-select-all">Select all</button>
      <button id="btn-delete-clear">Clear all</button>
      <button id="btn-delete-selected" disabled>Delete selected</button>
      <button id="btn-delete-all" disabled>Delete all workflows</button>
    </div>
    <p id="delete-msg" class="msg hidden" role="status"></p>
  </details>
  <details id="add-details"><summary>Add a workflow</summary>
    <div class="field-group">
      <label for="project-zip">Workflow ZIP</label>
      <input id="project-zip" type="file" accept=".zip">
    </div>
    <div class="field-group">
      <label for="project-id">Short name (optional)</label>
      <input id="project-id" maxlength="64" placeholder="Derived from the ZIP filename">
    </div>
    <button id="btn-project-import" disabled>Add a workflow</button>
    <p id="project-import-msg" class="msg hidden" role="status"></p>
    <p class="muted" id="import-note">Only administrators can add workflows. Added workflows are shared; your runs and results remain private.</p>
  </details>
</section>

<section aria-labelledby="setup-heading">
  <div class="card-header"><h2 id="setup-heading">Set up</h2><span id="setup-workflow" class="muted workflow-name hidden"></span></div>
  <p id="setup-summary" class="muted">Load a workflow to see its steps.</p>
  <details id="cartridge-s" class="hidden">
    <summary><h3 id="cartridges-h">Workflow steps</h3> <span id="cartridge-badge" class="badge ok">in order</span></summary>
    <div id="model-mode-control" class="field-group hidden">
      <label for="model-mode">AI model selection</label>
      <select id="model-mode">
        <option value="independent">Choose a model for each step</option>
        <option value="shared">Use the same model for all steps</option>
      </select>
    </div>
    <div id="shared-model-control" class="field-group hidden">
      <label for="model-all">AI model for all steps</label>
      <select id="model-all"></select>
    </div>
    <div id="bulk-control" class="bulk hidden" role="group" aria-label="Choose one model for several steps">
      <div class="field-actions">
        <button id="btn-bulk-select-all">Select all</button>
        <button id="btn-bulk-clear" disabled>Clear selection</button>
        <span id="bulk-count" class="bulk-count" role="status">0 of 0 selected</span>
      </div>
      <div class="field-group">
        <label for="bulk-model">AI model for the selected steps</label>
        <div class="field-actions">
          <select id="bulk-model"></select>
          <button id="btn-bulk-apply" disabled>Apply to selected</button>
        </div>
      </div>
      <p id="bulk-msg" class="msg hidden" role="status"></p>
    </div>
    <div id="cartridges"></div>
  </details>
  <details><summary>Model information</summary>
    <div class="card-header">
      <h3>Available models (reference only)</h3>
      <span id="models-badge" class="badge muted">loading</span>
    </div>
    <p class="muted">Choose models using the dropdowns above.</p>
    <pre id="models" class="muted pre-doc">loading…</pre>
  </details>
  <h3>Terminal for cartridge workers</h3>
  <label for="terminal-select">Native Open Terminal connection</label>
  <select id="terminal-select"><option value="">No terminal</option></select>
  <p id="terminal-help" class="muted" role="status">Loading available terminals…</p>
  <p class="muted">Choose once for this run. Every step uses this Terminal.</p>
  <div class="card-header">
    <h3 id="tools-heading">Tools for this workflow</h3>
    <span id="tools-badge" class="badge muted">loading</span>
  </div>
  <div class="actions">
    <span class="muted" id="tool-count" role="status">Tools selected: 0 of 0</span>
    <button id="btn-tools-select-all" disabled>Select all</button>
    <button id="btn-tools-clear-all" disabled>Clear all</button>
  </div>
  <p id="tools-empty" class="muted">Loading tools…</p>
  <div id="tools" class="tool-grid" role="group" aria-labelledby="tools-heading"></div>
  <p class="muted">No tools are selected by default. Choose only the tools this workflow needs. Selected tools are available to every step.</p>
  <button class="primary" id="btn-create" aria-describedby="prepare-note prepare-help" disabled>Prepare workflow</button>
  <p id="prepare-note" class="muted">This saves your choices for this run. It doesn't change the workflow or start it.</p>
  <p id="prepare-help" class="muted" role="status">Load a workflow to set it up.</p>
  <p class="msg hidden" id="create-msg" role="status"></p>
</section>

<section aria-labelledby="control-heading">
  <h2 id="control-heading">Run workflow</h2>
  <div id="queue-box" class="queue hidden" aria-live="polite">
    <h3>Queue</h3>
    <p class="muted">One workflow uses the model at a time. The next one starts on its own when the current one finishes or stops.</p>
    <ul id="queue-list"></ul>
  </div>
  <p id="run-empty" class="muted">Prepare a workflow or open previous work below.</p>
  <div class="result hidden" id="run-result">
    <div class="card-header">
      <h3 id="run-title"></h3>
      <span id="run-badge" class="badge ok"></span>
    </div>
    <p id="run-reference" class="muted"></p>
    <p id="run-terminal" class="muted"></p>
    <p id="run-summary" role="status"></p>
    <progress id="run-progress" aria-label="Finished workflow steps" value="0" max="1"></progress>
    <ol id="run-steps" class="timeline"></ol>
    <div class="actions"><button class="primary" id="btn-start" disabled>Start workflow</button><button id="btn-stop" disabled>Stop workflow</button><button id="btn-refresh-run" disabled>Check progress</button></div>
    <p id="control-msg" class="msg hidden" role="status"></p>
    <details><summary>Ledger</summary><div id="run-ledger"></div></details>
  </div>
</section>

<section aria-labelledby="results-heading">
  <h2 id="results-heading">Results and previous work</h2>
  <div class="field-group">
    <label for="history-runs">Previous work</label>
    <div class="field-actions">
      <select id="history-runs"></select>
      <button id="btn-history-open" disabled>View previous work</button>
      <button id="btn-history-refresh">Refresh list</button>
    </div>
  </div>
  <details id="history-delete-details"><summary>Delete previous work</summary>
    <p class="muted">Tick the previous work to delete. Its ledger, saved replies, results and work files are removed for good. The chats stay in Open WebUI. Work that is running or waiting in the queue cannot be deleted.</p>
    <div id="history-delete-list" class="delete-list"></div>
    <div class="field-actions">
      <button id="btn-history-delete-select-all">Select all</button>
      <button id="btn-history-delete-clear">Clear all</button>
      <button id="btn-history-delete-selected" disabled>Delete selected</button>
      <button id="btn-history-delete-all" disabled>Delete all previous work</button>
    </div>
    <p id="history-delete-msg" class="msg hidden" role="status"></p>
  </details>
  <p id="history-msg" class="msg" role="status"></p>
  <p id="results-msg" class="msg" role="status">Choose previous work to view its progress and results.</p>
  <button id="btn-download" class="hidden">Download results ZIP</button>
  <div id="saved-results"></div>
</section>
</main>
<script>
(function(){
  "use strict";
  var API = "/api/magazine-simple";
  var S = { workflow:null, models:[], tools:[], terminals:[], toolIds:[], modelMode:"independent", run:null, timer:null, canImport:false };
  var el = function(id){ return document.getElementById(id); };
  function stored(key){ try { return localStorage.getItem(key); } catch(e){ return null; } }

  // --- appearance (same behaviour as before) ---
  var appearance = "follow", themeMedia = window.matchMedia("(prefers-color-scheme: dark)");
  function applyAppearance(){
    var owui = stored("theme"), root = document.documentElement;
    var followsDark = root.classList.contains("dark") ||
      (!root.classList.contains("light") && (owui === "dark" || (owui !== "light" && themeMedia.matches)));
    root.setAttribute("data-theme", appearance === "follow" ? (followsDark ? "dark" : "light") : appearance);
  }
  (function initAppearance(){
    var saved = stored("magazine.appearance");
    appearance = saved === "light" || saved === "dark" ? saved : "follow";
    el("appearance").value = appearance; applyAppearance();
    el("appearance").addEventListener("change", function(){
      appearance = this.value;
      try { localStorage.setItem("magazine.appearance", appearance); } catch(e){}
      applyAppearance();
    });
    if (themeMedia.addEventListener) themeMedia.addEventListener("change", applyAppearance);
    window.addEventListener("storage", function(e){ if (e.key === "theme") applyAppearance(); });
  })();

  // --- helpers ---
  async function api(path, method, body){
    var opts = { method: method || "GET", headers: {"Content-Type":"application/json"} };
    var token = stored("token"); if (token) opts.headers.Authorization = "Bearer " + token;
    if (body !== undefined) opts.body = JSON.stringify(body);
    var r = await fetch(API + path, opts), data = null;
    var type = r.headers.get("content-type") || "";
    if (type.indexOf("json") >= 0) data = await r.json(); else data = await r.blob();
    if (!r.ok){
      var detail = data && data.detail ? data.detail : r.statusText;
      if (r.status === 401) detail = "Sign in to Open WebUI again, then try again.";
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return data;
  }
  function setBadge(id, text, cls){ var b = el(id); b.textContent = text; b.className = "badge " + (cls || "muted"); }
  function showMsg(id, txt, cls){ var e = el(id); e.textContent = txt; e.className = "msg" + (cls ? " " + cls : ""); }
  function hideMsg(id){ el(id).className = "msg hidden"; }
  function option(value, text){ var o = document.createElement("option"); o.value = value; o.textContent = text; return o; }
  function when(iso){ return iso ? new Date(iso).toLocaleString() : ""; }
  var STATUS = { ready:"Ready to start", running:"Running", completed:"Finished", stopped:"Stopped", halted:"Stopped — needs attention",
                 failed:"Stopped — needs attention", interrupted:"Stopped — Open WebUI restarted", queued:"Waiting in queue" };
  function statusText(s){ return STATUS[s] || s; }

  // --- workflows ---
  async function loadLibrary(selectId){
    setBadge("library-badge", "loading");
    try {
      var d = await api("/workflows"); S.canImport = !!d.can_import;
      var sel = el("library-workflows"); sel.innerHTML = "";
      if (!d.workflows.length){ sel.appendChild(option("", "No workflows yet")); setBadge("library-badge", "empty");
        el("library-guidance").textContent = S.canImport ? "Add a workflow ZIP below to get started." : "An administrator can add a workflow."; }
      else {
        d.workflows.forEach(function(w){ var o = option(w.id, w.name + (w.valid ? " — " + w.cartridges + " steps" : " (can't be loaded)")); o.disabled = !w.valid; sel.appendChild(o); });
        setBadge("library-badge", d.workflows.length + (d.workflows.length === 1 ? " workflow" : " workflows"), "ok");
        el("library-guidance").textContent = "Select a workflow, then choose Load your workflow.";
        if (selectId) sel.value = selectId;
      }
      el("btn-library-select").disabled = !d.workflows.some(function(w){ return w.valid; });
      renderDeleteList(d.workflows);
      el("btn-project-import").disabled = !S.canImport;
      el("import-note").textContent = S.canImport ? "Added workflows are shared; your runs and results remain private." : "Only administrators can add workflows.";
      hideMsg("library-msg");
    } catch(e){ setBadge("library-badge", "unavailable", "err"); showMsg("library-msg", e.message, "err"); }
  }
  el("btn-library-refresh").onclick = function(){ loadLibrary(el("library-workflows").value); };
  el("btn-library-select").onclick = async function(){
    var id = el("library-workflows").value; if (!id) return;
    try { S.workflow = await api("/workflows/" + encodeURIComponent(id)); renderSetup(); showMsg("library-msg", "Workflow loaded. Open Workflow steps below to choose models.", "ok"); }
    catch(e){ showMsg("library-msg", e.message, "err"); }
  };
  // --- delete workflows ---
  function deleteBoxes(){ return Array.prototype.slice.call(el("delete-list").querySelectorAll("input[type=checkbox]")); }
  function updateDeleteButtons(){
    var boxes = deleteBoxes();
    el("btn-delete-selected").disabled = !boxes.some(function(b){ return b.checked; });
    el("btn-delete-all").disabled = !boxes.length;
    el("btn-delete-select-all").disabled = el("btn-delete-clear").disabled = !boxes.length;
  }
  function renderDeleteList(workflows){
    var list = el("delete-list"); list.innerHTML = "";
    if (!workflows.length){ var p = document.createElement("p"); p.className = "muted"; p.textContent = "No workflows to delete."; list.appendChild(p); }
    workflows.forEach(function(w){
      var lab = document.createElement("label"), box = document.createElement("input");
      box.type = "checkbox"; box.value = w.id; box.onchange = updateDeleteButtons;
      lab.appendChild(box); lab.appendChild(document.createTextNode(w.name + (w.name !== w.id ? " (" + w.id + ")" : "")));
      list.appendChild(lab);
    });
    updateDeleteButtons();
  }
  async function deleteWorkflows(body, label){
    if (!confirm("Delete " + label + "? They are removed from the list for everyone. Previous work and its results are kept.")) return;
    try {
      var r = await api("/workflows/delete", "POST", body);
      if (S.workflow && r.deleted.indexOf(S.workflow.id) >= 0){
        S.workflow = null; el("cartridges").innerHTML = ""; el("cartridge-s").classList.add("hidden");
        el("setup-workflow").classList.add("hidden"); el("setup-summary").textContent = "Load a workflow to see its steps."; updatePrepare();
      }
      await loadLibrary();
      var text = "Deleted " + r.deleted.length + (r.deleted.length === 1 ? " workflow." : " workflows.");
      if (r.failed.length) text += " Could not delete: " + r.failed.map(function(f){ return f.id; }).join(", ") + ".";
      showMsg("delete-msg", text, r.failed.length ? "err" : "ok");
    } catch(e){ showMsg("delete-msg", e.message, "err"); }
  }
  el("btn-delete-select-all").onclick = function(){ deleteBoxes().forEach(function(b){ b.checked = true; }); updateDeleteButtons(); };
  el("btn-delete-clear").onclick = function(){ deleteBoxes().forEach(function(b){ b.checked = false; }); updateDeleteButtons(); };
  el("btn-delete-selected").onclick = function(){
    var ids = deleteBoxes().filter(function(b){ return b.checked; }).map(function(b){ return b.value; });
    if (ids.length) deleteWorkflows({ids: ids}, ids.length === 1 ? "the workflow “" + ids[0] + "”" : ids.length + " selected workflows");
  };
  el("btn-delete-all").onclick = function(){ deleteWorkflows({all: true}, "all " + deleteBoxes().length + " workflows"); };
  el("project-zip").onchange = function(){ el("btn-project-import").disabled = !S.canImport || !this.files.length; };
  el("btn-project-import").onclick = async function(){
    var f = el("project-zip").files[0]; if (!f) { showMsg("project-import-msg", "Choose a workflow ZIP first.", "err"); return; }
    showMsg("project-import-msg", "Adding workflow…");
    var b64 = await new Promise(function(resolve){ var r = new FileReader(); r.onload = function(){ resolve(r.result.split(",")[1]); }; r.readAsDataURL(f); });
    try {
      var d = await api("/workflows/import", "POST", { file_name: f.name, data_b64: b64, workflow_id: el("project-id").value.trim() });
      showMsg("project-import-msg", "Added “" + d.name + "” (" + d.cartridges + " steps). Select it and choose Load your workflow.", "ok");
      el("project-zip").value = ""; el("project-id").value = ""; await loadLibrary(d.id);
    } catch(e){ showMsg("project-import-msg", e.message, "err"); }
  };

  // --- set up ---
  function modelOptions(sel, preferred){
    sel.innerHTML = ""; sel.appendChild(option("", "Choose an AI model…"));
    S.models.forEach(function(m){ sel.appendChild(option(m.id, m.name || m.id)); });
    if (preferred && S.models.some(function(m){ return m.id === preferred; })) sel.value = preferred;
  }
  function renderSetup(){
    var w = S.workflow, wrap = el("cartridges"); wrap.innerHTML = "";
    el("setup-workflow").textContent = w.name; el("setup-workflow").classList.remove("hidden");
    el("setup-summary").textContent = w.cartridges.length + " steps run in order, each in a fresh chat.";
    el("cartridge-s").classList.remove("hidden");
    el("cartridges-h").textContent = "Workflow steps — " + w.cartridges.length;
    el("model-mode-control").classList[w.cartridges.length > 1 ? "remove" : "add"]("hidden");
    w.cartridges.forEach(function(c, i){
      var row = document.createElement("div"); row.className = "cartridge";
      var lead = document.createElement("label"); lead.className = "lead";
      var pick = document.createElement("input"); pick.type = "checkbox"; pick.className = "pick"; pick.dataset.index = i;
      pick.setAttribute("aria-label", "Select Step " + (i + 1) + ": " + c.id); pick.onchange = updateBulk;
      var ord = document.createElement("span"); ord.className = "ord"; ord.textContent = i + 1;
      lead.appendChild(pick); lead.appendChild(ord);
      var idb = document.createElement("div"); idb.className = "id"; idb.textContent = c.id;
      var cell = document.createElement("div"); cell.className = "cellm";
      var lab = document.createElement("label"); lab.className = "mk"; lab.textContent = "AI model for Step " + (i + 1);
      var sel = document.createElement("select"); sel.id = "model-" + i; sel.dataset.step = c.id; lab.setAttribute("for", sel.id);
      modelOptions(sel, c.model); sel.onchange = updatePrepare;
      cell.appendChild(lab); cell.appendChild(sel);
      var pr = document.createElement("div"); pr.className = "prompt";
      var extra = [];
      if (c.handover.length) extra.push("Uses values from: " + c.handover.map(function(h){ return h.from + " " + h.select; }).join(", "));
      if (c.inputs.length) extra.push("Input files: " + c.inputs.map(function(f){ return f.path + " (from " + f.from + ")"; }).join(", "));
      if (c.outputs.length) extra.push("Produces: " + c.outputs.join(", "));
      pr.textContent = c.prompt + (extra.length ? "\n\n" + extra.join("\n") : "");
      row.appendChild(lead); row.appendChild(idb); row.appendChild(cell); row.appendChild(pr);
      wrap.appendChild(row);
    });
    modelOptions(el("model-all"), w.cartridges[0] && w.cartridges[0].model);
    modelOptions(el("bulk-model"));
    el("cartridge-s").open = false;   // a loaded workflow starts collapsed
    applyModelMode(); updateBulk(); updatePrepare(); hideMsg("create-msg");
  }
  // --- choose one model for several steps (acts on the same per-step selects) ---
  function pickBoxes(){ return Array.prototype.slice.call(document.querySelectorAll("#cartridges .pick")); }
  function updateBulk(){
    var boxes = pickBoxes(), n = boxes.filter(function(b){ return b.checked; }).length;
    el("bulk-count").textContent = n + " of " + boxes.length + " selected";
    el("btn-bulk-clear").disabled = !n;
    el("btn-bulk-select-all").disabled = !boxes.length;
    el("btn-bulk-apply").disabled = !n || !el("bulk-model").value;
    hideMsg("bulk-msg");
  }
  el("bulk-model").onchange = updateBulk;
  el("btn-bulk-select-all").onclick = function(){ pickBoxes().forEach(function(b){ b.checked = true; }); updateBulk(); };
  el("btn-bulk-clear").onclick = function(){ pickBoxes().forEach(function(b){ b.checked = false; }); updateBulk(); };
  el("btn-bulk-apply").onclick = function(){
    var bulk = el("bulk-model"), model = bulk.value;
    var chosen = pickBoxes().filter(function(b){ return b.checked; });
    if (S.modelMode !== "independent" || !model || !chosen.length) return;
    var done = 0;
    chosen.forEach(function(b){ var sel = el("model-" + b.dataset.index); sel.value = model; if (sel.value === model) done++; });
    updatePrepare(); updateBulk();
    showMsg("bulk-msg", done === chosen.length ? "Set " + bulk.options[bulk.selectedIndex].textContent + " for " + done + (done === 1 ? " step." : " steps.")
                                               : "Could not set the model for " + (chosen.length - done) + " of the selected steps.", done === chosen.length ? "ok" : "err");
  };
  function applyModelMode(){
    var shared = S.modelMode === "shared";
    el("bulk-control").classList[!shared && S.workflow && S.workflow.cartridges.length > 1 ? "remove" : "add"]("hidden");
    el("shared-model-control").classList[shared ? "remove" : "add"]("hidden");
    Array.prototype.forEach.call(document.querySelectorAll(".cartridge"), function(r){ r.classList[shared ? "add" : "remove"]("shared"); });
  }
  el("model-mode").onchange = function(){ S.modelMode = this.value; applyModelMode(); updatePrepare(); };
  el("model-all").onchange = updatePrepare;
  function chosenModels(){
    var map = {};
    Array.prototype.forEach.call(document.querySelectorAll("#cartridges select"), function(s){
      map[s.dataset.step] = S.modelMode === "shared" ? el("model-all").value : s.value; });
    return map;
  }
  function updatePrepare(){
    var ok = !!S.workflow && Object.values(chosenModels()).every(Boolean) && Object.keys(chosenModels()).length > 0;
    el("btn-create").disabled = !ok;
    el("prepare-help").textContent = !S.workflow ? "Load a workflow to set it up." : (ok ? "Ready to prepare." :
      (S.modelMode === "shared" ? "Open Workflow steps and choose an AI model for the workflow before continuing." : "Open Workflow steps and choose an AI model for each step before continuing."));
  }
  async function loadOptions(){
    try {
      var d = await api("/options");
      S.models = d.models || []; S.tools = d.tools || []; S.terminals = d.terminals || [];
      setBadge("models-badge", S.models.length + " available", S.models.length ? "ok" : "err");
      el("models").textContent = S.models.map(function(m){ return m.name && m.name !== m.id ? m.name + " (" + m.id + ")" : m.id; }).join("\n") || (d.models_error || "No models found.");
      var t = el("terminal-select"); t.innerHTML = ""; t.appendChild(option("", "No terminal"));
      S.terminals.forEach(function(x){ t.appendChild(option(x.id, x.name)); });
      if (S.terminals.length === 1) t.value = S.terminals[0].id;
      el("terminal-help").textContent = S.terminals.length ? "Only connections available to your account are listed." : (d.terminals_error || "No terminals are available to your account.");
      renderTools(d.tools_error);
    } catch(e){
      setBadge("models-badge", "unavailable", "err"); setBadge("tools-badge", "unavailable", "err");
      el("models").textContent = e.message; el("terminal-help").textContent = e.message; el("tools-empty").textContent = e.message;
    }
  }
  function renderTools(error){
    var grid = el("tools"); grid.innerHTML = "";
    setBadge("tools-badge", S.tools.length + " found", S.tools.length ? "ok" : "muted");
    el("tools-empty").textContent = S.tools.length ? "" : (error || "No tools are available to your account.");
    el("tools-empty").classList[S.tools.length ? "add" : "remove"]("hidden");
    S.tools.forEach(function(t){
      var box = document.createElement("div"); box.className = "tool";
      var lab = document.createElement("label"); var cb = document.createElement("input"); cb.type = "checkbox"; cb.value = t.id;
      cb.checked = S.toolIds.indexOf(t.id) >= 0; cb.onchange = syncTools;
      var txt = document.createElement("span"); txt.className = "tool-text";
      var tn = document.createElement("span"); tn.className = "tn"; tn.textContent = t.name;
      var td = document.createElement("span"); td.className = "td"; td.textContent = t.description || "";
      txt.appendChild(tn); txt.appendChild(td); lab.appendChild(cb); lab.appendChild(txt); box.appendChild(lab); grid.appendChild(box);
    });
    el("btn-tools-select-all").disabled = el("btn-tools-clear-all").disabled = !S.tools.length;
    syncTools();
  }
  function syncTools(){
    S.toolIds = Array.prototype.map.call(document.querySelectorAll("#tools input:checked"), function(c){ return c.value; });
    el("tool-count").textContent = "Tools selected: " + S.toolIds.length + " of " + S.tools.length;
  }
  el("btn-tools-select-all").onclick = function(){ document.querySelectorAll("#tools input").forEach(function(c){ c.checked = true; }); syncTools(); };
  el("btn-tools-clear-all").onclick = function(){ document.querySelectorAll("#tools input").forEach(function(c){ c.checked = false; }); syncTools(); };
  el("btn-create").onclick = async function(){
    if (!S.workflow) return;
    this.disabled = true; showMsg("create-msg", "Preparing workflow…");
    try {
      var run = await api("/runs", "POST", { workflow_id: S.workflow.id, model_map: chosenModels(), tool_ids: S.toolIds,
                                             terminal_id: el("terminal-select").value || null });
      showMsg("create-msg", "Your workflow is ready. Nothing has started.", "ok");
      await loadHistory(run.id); await openRun(run.id);
      el("control-heading").scrollIntoView({behavior:"smooth"});
    } catch(e){ showMsg("create-msg", e.message, "err"); }
    updatePrepare();
  };

  // --- run ---
  async function openRun(id){
    clearTimeout(S.timer);
    try { S.run = await api("/runs/" + encodeURIComponent(id)); }
    catch(e){ showMsg("history-msg", e.message, "err"); return; }
    renderRun(); renderResults(); refreshQueue();
    if (S.run.status === "running" || S.run.status === "queued") S.timer = setTimeout(function(){ openRun(id); }, 3000);
    updateHistoryLabel(S.run);
  }
  function renderRun(){
    var r = S.run; el("run-empty").classList.add("hidden"); el("run-result").classList.remove("hidden");
    el("run-title").textContent = r.workflow_name;
    var bad = ["halted", "failed", "interrupted"].indexOf(r.status) >= 0;
    setBadge("run-badge", r.status === "queued" && r.queue_position ? "Waiting — number " + r.queue_position + " in the queue" : statusText(r.status),
             bad ? "err" : (r.status === "completed" ? "ok" : "muted"));
    el("run-reference").textContent = "Prepared " + when(r.created) + " · Reference " + r.id + " · Work folder " + r.work_folder;
    el("run-terminal").textContent = "Terminal: " + (r.terminal_id || "none") + " · Tools: " + ((r.tool_ids || []).join(", ") || "none");
    var done = r.cartridges.filter(function(c){ return c.status === "done"; }).length;
    el("run-progress").max = r.cartridges.length; el("run-progress").value = done;
    var current = r.cartridges.find(function(c){ return c.status === "running"; });
    el("run-summary").textContent = done + " of " + r.cartridges.length + " steps finished" + (current ? ". Working on " + current.id + "." : ".");
    var list = el("run-steps"); list.innerHTML = "";
    r.cartridges.forEach(function(c, i){
      var li = document.createElement("li"), last = c.attempts[c.attempts.length - 1] || {};
      li.className = c.status === "done" ? "finished" : (c.status === "running" ? "working" : (["halted","failed","stopped","interrupted"].indexOf(c.status) >= 0 ? "problem" : ""));
      var label = { done:"finished", running:"working…", pending:"waiting", halted:"stopped (HALT)", failed:"failed", stopped:"stopped", interrupted:"interrupted" }[c.status] || c.status;
      li.appendChild(document.createTextNode(c.id + " — " + label + " · " + c.model + " "));
      if (last.chat_id){ var a = document.createElement("a"); a.href = "/c/" + last.chat_id; a.target = "_blank"; a.rel = "noopener"; a.textContent = "open chat"; li.appendChild(a); }
      var prevDone = i === 0 || r.cartridges[i - 1].status === "done";
      if (r.status !== "running" && r.status !== "ready" && r.status !== "queued" && prevDone && c.status !== "done"){
        var b = document.createElement("button"); b.className = "small"; b.textContent = "Continue from this step"; b.style.marginLeft = "8px";
        b.onclick = function(){ restartFrom(c.id); }; li.appendChild(b);
      }
      if (last.note){ var n = document.createElement("span"); n.className = "note"; n.textContent = last.note; li.appendChild(n); }
      if (c.attempts.length > 1){ var m = document.createElement("span"); m.className = "note"; m.textContent = "Attempt " + c.attempts.length + "; earlier attempts are in the ledger."; li.appendChild(m); }
      list.appendChild(li);
    });
    el("btn-start").disabled = r.status !== "ready";
    el("btn-stop").disabled = r.status !== "running" && r.status !== "queued";
    el("btn-stop").textContent = r.status === "queued" ? "Remove from queue" : "Stop workflow";
    if (r.status === "queued" && r.queue_position) showMsg("control-msg", queuedText(r.queue_position), "ok");
    el("btn-refresh-run").disabled = false;
    renderLedger(r);
  }
  function renderLedger(r){
    var rows = "<table class='ledger'><tr><th>#</th><th>Step</th><th>Attempt</th><th>Model</th><th>Status</th><th>Started</th><th>Finished</th><th>Chat</th><th>Files produced</th></tr>";
    function esc(s){ return String(s == null ? "" : s).replace(/[&<>"]/g, function(c){ return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]; }); }
    r.cartridges.forEach(function(c, i){
      var attempts = c.attempts.length ? c.attempts : [{ status: c.status, model: c.model }];
      attempts.forEach(function(a, n){
        rows += "<tr><td>" + (i + 1) + "</td><td>" + esc(c.id) + "</td><td>" + (c.attempts.length ? n + 1 : "–") + "</td><td>" + esc(a.model) + "</td><td>" + esc(a.status) +
          "</td><td>" + esc(when(a.started)) + "</td><td>" + esc(when(a.finished)) + "</td><td>" + (a.chat_id ? "<a target='_blank' rel='noopener' href='/c/" + esc(a.chat_id) + "'>open</a>" : "") +
          "</td><td>" + (a.outputs || []).map(function(f){ return esc(f.path) + " <span class='muted'>(" + f.bytes + " B)</span>"; }).join("<br>") + "</td></tr>";
      });
    });
    el("run-ledger").innerHTML = rows + "</table><p class='muted'>The same ledger is saved as LEDGER.md in the work folder and in the download.</p>";
  }
  async function restartFrom(cid){
    if (!S.run) return;
    if (!confirm("Continue from " + cid + " in a fresh chat? Earlier work and chats are kept.")) return;
    try { var d = await api("/runs/" + S.run.id + "/restart", "POST", { cartridge_id: cid });
      showMsg("control-msg", d.queue_position ? queuedText(d.queue_position) : "Continuing from " + cid + ".", "ok"); openRun(S.run.id); }
    catch(e){ showMsg("control-msg", e.message, "err"); }
  }
  el("btn-start").onclick = async function(){
    if (!S.run) return; this.disabled = true;
    try { var d = await api("/runs/" + S.run.id + "/start", "POST");
      showMsg("control-msg", d.queue_position ? queuedText(d.queue_position) : "Workflow started.", "ok"); openRun(S.run.id); loadHistory(S.run.id); }
    catch(e){ showMsg("control-msg", e.message, "err"); this.disabled = false; }
  };
  el("btn-stop").onclick = async function(){
    if (!S.run) return; this.disabled = true;
    try { var d = await api("/runs/" + S.run.id + "/stop", "POST");
      if (d.removed_from_queue){ showMsg("control-msg", "Removed from the queue. You can start it again later.", "ok"); openRun(S.run.id); loadHistory(S.run.id); }
      else showMsg("control-msg", "Stopping after the active chat is cancelled…"); }
    catch(e){ showMsg("control-msg", e.message, "err"); }
  };
  el("btn-refresh-run").onclick = function(){ if (S.run) openRun(S.run.id); };

  // --- queue ---
  function queuedText(n){ return "Another workflow is using the model, so this one is number " + n + " in the queue. It will start on its own."; }
  async function refreshQueue(){
    var d; try { d = await api("/queue"); } catch(e){ return; }
    var list = el("queue-list"); list.innerHTML = "";
    var items = d.running.map(function(q){ return ["Running", q]; }).concat(d.waiting.map(function(q, i){ return ["Waiting " + (i + 1), q]; }));
    el("queue-box").classList[items.length ? "remove" : "add"]("hidden");
    items.forEach(function(item){
      var li = document.createElement("li"), q = item[1];
      li.appendChild(document.createTextNode(item[0] + ": " + q.workflow_name + " "));
      if (q.mine && q.id){ var b = document.createElement("button"); b.className = "small"; b.textContent = "Open";
        b.onclick = function(){ openRun(q.id); el("control-heading").scrollIntoView({behavior:"smooth"}); }; li.appendChild(b); }
      list.appendChild(li);
    });
  }
  setInterval(function(){ if (!document.hidden) refreshQueue(); }, 5000);
  refreshQueue();

  // --- results and previous work ---
  function runLabel(r){ return r.workflow_name + " · " + when(r.created) + " · " + statusText(r.status) + (r.queue_position ? " (" + r.queue_position + ")" : ""); }
  async function loadHistory(select){
    try {
      var d = await api("/runs"), sel = el("history-runs"), keep = select || sel.value;
      sel.innerHTML = "";
      if (!d.runs.length) sel.appendChild(option("", "No previous work yet"));
      d.runs.forEach(function(r){ sel.appendChild(option(r.id, runLabel(r))); });
      if (keep && d.runs.some(function(r){ return r.id === keep; })) sel.value = keep;
      el("btn-history-open").disabled = !d.runs.length; hideMsg("history-msg");
      renderHistoryDeleteList(d.runs);
    } catch(e){ showMsg("history-msg", e.message, "err"); }
  }
  function updateHistoryLabel(r){
    Array.prototype.forEach.call(el("history-runs").options, function(o){ if (o.value === r.id) o.textContent = runLabel(r); });
  }
  el("btn-history-refresh").onclick = function(){ loadHistory(); };

  // --- delete previous work ---
  function historyBoxes(){ return Array.prototype.slice.call(el("history-delete-list").querySelectorAll("input[type=checkbox]")); }
  function updateHistoryDeleteButtons(){
    var boxes = historyBoxes();
    el("btn-history-delete-selected").disabled = !boxes.some(function(b){ return b.checked; });
    el("btn-history-delete-all").disabled = !boxes.length;
    el("btn-history-delete-select-all").disabled = el("btn-history-delete-clear").disabled = !boxes.length;
  }
  function renderHistoryDeleteList(runs){
    var list = el("history-delete-list");
    var ticked = historyBoxes().filter(function(b){ return b.checked; }).map(function(b){ return b.value; });
    list.innerHTML = "";
    if (!runs.length){ var p = document.createElement("p"); p.className = "muted"; p.textContent = "No previous work to delete."; list.appendChild(p); }
    runs.forEach(function(r){
      var lab = document.createElement("label"), box = document.createElement("input");
      box.type = "checkbox"; box.value = r.id; box.checked = ticked.indexOf(r.id) >= 0; box.onchange = updateHistoryDeleteButtons;
      lab.appendChild(box); lab.appendChild(document.createTextNode(runLabel(r)));
      list.appendChild(lab);
    });
    updateHistoryDeleteButtons();
  }
  async function deleteRuns(body, label){
    if (!confirm("Delete " + label + "? The ledger, saved replies, results and work files are removed for good. The chats stay in Open WebUI.")) return;
    try {
      var r = await api("/runs/delete", "POST", body);
      if (S.run && r.deleted.indexOf(S.run.id) >= 0){
        clearTimeout(S.timer); S.run = null;
        el("run-result").classList.add("hidden"); el("run-empty").classList.remove("hidden");
        el("saved-results").innerHTML = ""; el("btn-download").classList.add("hidden");
        el("results-msg").textContent = "Choose previous work to view its progress and results.";
      }
      await loadHistory();
      var text = "Deleted " + r.deleted.length + (r.deleted.length === 1 ? " item" : " items") + " of previous work.";
      if (r.failed.length) text += " Could not delete " + r.failed.length + ": " + r.failed.map(function(f){ return f.error; }).join(" ");
      showMsg("history-delete-msg", text, r.failed.length ? "err" : "ok");
    } catch(e){ showMsg("history-delete-msg", e.message, "err"); }
  }
  el("btn-history-delete-select-all").onclick = function(){ historyBoxes().forEach(function(b){ b.checked = true; }); updateHistoryDeleteButtons(); };
  el("btn-history-delete-clear").onclick = function(){ historyBoxes().forEach(function(b){ b.checked = false; }); updateHistoryDeleteButtons(); };
  el("btn-history-delete-selected").onclick = function(){
    var ids = historyBoxes().filter(function(b){ return b.checked; }).map(function(b){ return b.value; });
    if (ids.length) deleteRuns({ids: ids}, ids.length === 1 ? "the selected previous work" : ids.length + " selected items of previous work");
  };
  el("btn-history-delete-all").onclick = function(){ deleteRuns({all: true}, "all " + historyBoxes().length + " items of previous work"); };
  el("btn-history-open").onclick = function(){ var id = el("history-runs").value; if (id) openRun(id); };
  function renderResults(){
    var r = S.run, box = el("saved-results"); box.innerHTML = "";
    el("results-msg").textContent = "Results for " + r.workflow_name + " (" + statusText(r.status) + ").";
    el("btn-download").classList.remove("hidden");
    r.cartridges.forEach(function(c, i){
      if (!c.reply) return;
      var div = document.createElement("div"); div.className = "saved-result";
      var h = document.createElement("h3"); h.textContent = "Step " + (i + 1) + ": " + c.id;
      var pre = document.createElement("pre"); pre.className = "output"; pre.textContent = c.reply;
      div.appendChild(h); div.appendChild(pre); box.appendChild(div);
    });
  }
  el("btn-download").onclick = async function(){
    if (!S.run) return;
    try { var blob = await api("/runs/" + S.run.id + "/download"); var a = document.createElement("a");
      a.href = URL.createObjectURL(blob); a.download = "Magazine-" + S.run.id + ".zip"; a.click();
      setTimeout(function(){ URL.revokeObjectURL(a.href); }, 5000); }
    catch(e){ showMsg("results-msg", e.message, "err"); }
  };

  loadOptions().then(function(){ loadLibrary(); });
  loadHistory();
})();
</script>
</body>
</html>
"""
