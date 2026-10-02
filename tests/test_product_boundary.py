"""End-to-end fresh-context assertions adapted to version 1's generic runner."""

import asyncio
import json

import httpx
import yaml

from magazine import async_runner, webui_dispatch


def test_two_models_use_distinct_fresh_chats_and_save_before_advancing(tmp_path, monkeypatch):
    workflow = tmp_path / "workflow"
    workflow.mkdir()
    prompts = {"one": "First prompt\n", "two": "Second prompt\n"}
    for cartridge_id, prompt in prompts.items():
        (workflow / f"{cartridge_id}.md").write_text(prompt, encoding="utf-8")
    manifest = {
        "schema_version": "1.0",
        "name": "Two models",
        "cartridges": [
            {"id": "one", "model": "first-role", "prompt_file": "one.md"},
            {"id": "two", "model": "second-role", "prompt_file": "two.md"},
        ],
    }
    (workflow / "magazine.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")

    chats = {}
    creations = []
    completions = []
    outputs = {"model-a": "  First raw result\n", "model-b": "Second raw result\n\n"}
    state_dir = tmp_path / "state"
    run_dir = state_dir / "run"

    def handle(request):
        path = request.url.path
        body = json.loads(request.content) if request.content else None
        if path == "/api/v1/chats/new":
            chat_id = f"chat-{len(chats) + 1}"
            chat = body["chat"]
            creations.append(json.loads(json.dumps(chat)))
            chats[chat_id] = chat
            if len(chats) == 2:
                assert (run_dir / "results/one.txt").read_bytes() == outputs["model-a"].encode("utf-8")
                persisted = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
                assert persisted["status"] == "RUNNING"
                assert persisted["done"] == ["one"] and persisted["current"] == "two"
                assert not (run_dir / "results/two.txt").exists()
            return httpx.Response(201, json={"id": chat_id})

        if path == "/api/chat/completions":
            completions.append(body)
            chat = chats[body["chat_id"]]
            message = chat["history"]["messages"][body["id"]]
            message.update(content=outputs[body["model"]], done=True)
            return httpx.Response(200, json={
                "status": True, "chat_id": body["chat_id"], "task_ids": ["task"],
            })

        if path.startswith("/api/tasks/chat/"):
            return httpx.Response(200, json={"task_ids": []})
        if path.startswith("/api/v1/chats/"):
            chat_id = path.rsplit("/", 1)[-1]
            return httpx.Response(200, json={"id": chat_id, "chat": chats[chat_id]})
        raise AssertionError(f"Unexpected endpoint or prerequisite: {path}")

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        webui_dispatch.httpx, "AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handle), **kwargs),
    )
    final = asyncio.run(async_runner.run_magazine_open_webui(
        magazine_path=str(workflow),
        state_dir=str(state_dir),
        owner_id="operator",
        base_url="https://example.invalid",
        api_key="test-key",
        model_map={"first-role": "model-a", "second-role": "model-b"},
    ))

    assert final["status"] == "COMPLETED" and final["done"] == ["one", "two"]
    assert len(chats) == len(creations) == len(completions) == 2
    assert [c["model"] for c in completions] == ["model-a", "model-b"]
    for field in ("chat_id", "session_id", "id"):
        assert len({c[field] for c in completions}) == 2

    message_id_sets = [set(chat["history"]["messages"]) for chat in creations]
    assert message_id_sets[0].isdisjoint(message_id_sets[1])
    for chat, completion, (cartridge_id, prompt) in zip(creations, completions, prompts.items()):
        assert chat["models"] == [completion["model"]]
        assert len(chat["messages"]) == len(chat["history"]["messages"]) == 2
        assert chat["messages"][0]["role"] == "user"
        assert chat["messages"][0]["content"] == prompt
        assert chat["messages"][1]["role"] == "assistant"
        assert chat["messages"][1]["content"] == ""
        assert completion["messages"] == [{"role": "user", "content": prompt}]
        assert completion["features"] == {"memory": False}
        for key in ("tools", "tool_ids", "terminal_id"):
            assert key not in completion and key not in chat
        assert completion["background_tasks"] == {
            "title_generation": False, "tags_generation": False, "follow_up_generation": False,
        }
        assert (run_dir / "results" / f"{cartridge_id}.txt").read_bytes() == outputs[completion["model"]].encode("utf-8")
