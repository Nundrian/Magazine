"""Evidence and download invariants for Open WebUI chat archival."""

import io
import json
import zipfile

import pytest

from magazine.conversation_archive import (
    ArchiveError,
    ArchiveStore,
    encoded,
    key_for,
    zip_files,
)


def _intent(store, index, cid, prompt="Please complete this step."):
    return store.intent({
        "index": index,
        "cartridge_id": cid,
        "chat_id": f"chat-{index}",
        "user_message_id": f"user-{index}",
        "assistant_message_id": f"answer-{index}",
        "prompt": prompt,
        "model_id": "generic-model",
    })


def _chat(intent, result="Answer saved"):
    uid, aid = intent["user_message_id"], intent["assistant_message_id"]
    return encoded({
        "id": intent["chat_id"],
        "user_id": intent["owner_id"],
        "chat": {
            "models": [intent["model_id"]],
            "messages": [{"id": uid}, {"id": aid}],
            "history": {"messages": {
                uid: {
                    "id": uid, "role": "user", "content": intent["prompt"],
                    "childrenIds": [aid],
                },
                aid: {
                    "id": aid, "role": "assistant", "content": result,
                    "model": intent["model_id"], "done": True,
                    "parentId": uid,
                },
            }},
        },
    })


def test_verified_archives_export_verbatim_chat_jsonl_results_and_manifest(tmp_path):
    store = ArchiveStore(tmp_path / "archive", "owner", "run")
    first = _intent(store, 1, "first")
    second = _intent(store, 2, "second")
    store.save(first, _chat(first, "A"))
    store.save(second, _chat(second, "B"))
    store.status_event(first, "cleanup", "DELETED")

    # The results may be in completion order; names must retain the manifest
    # cartridge position. Partial results must use the same original index.
    manifest, files = store.view(["first", "second"], {"second": "B", "first": "A"})
    assert manifest["status"] == "COMPLETE"
    assert files[f"results/{key_for(1, 'first')}.txt"] == b"A"
    assert files[f"results/{key_for(2, 'second')}.txt"] == b"B"
    assert f"original-chats/{key_for(1, 'first')}.json" in files
    assert f"cartridges/{key_for(2, 'second')}.jsonl" in files
    assert b'"run_sequence":' in files["run.jsonl"]
    assert manifest["cartridges"][0]["cleanup"]["status"] == "DELETED"
    assert store.inspect(["first", "second"])["status"] == "COMPLETE"

    with zipfile.ZipFile(io.BytesIO(zip_files(files))) as bundle:
        assert sorted(bundle.namelist()) == sorted(files)
        for name, original in files.items():
            assert bundle.read(name) == original


def test_missing_or_unverified_chat_remains_partial_and_is_not_exported(tmp_path):
    store = ArchiveStore(tmp_path / "archive", "owner", "run")
    first = _intent(store, 1, "first")
    _intent(store, 2, "second")
    store.save(first, _chat(first))
    manifest, files = store.view(["first", "second"], {"first": "Answer saved"})
    assert manifest["status"] == "PARTIAL"
    assert [entry["status"] for entry in manifest["cartridges"]] == ["VERIFIED", "PENDING"]
    assert f"original-chats/{key_for(2, 'second')}.json" not in files
    assert f"cartridges/{key_for(2, 'second')}.jsonl" not in files


def test_conflicting_evidence_cannot_overwrite_verified_chat(tmp_path):
    store = ArchiveStore(tmp_path / "archive", "owner", "run")
    intent = _intent(store, 1, "first")
    store.save(intent, _chat(intent, "Original"))
    with pytest.raises(ArchiveError, match="Conflicting"):
        store.save(intent, _chat(intent, "Replaced"))
    assert store.verify(key_for(1, "first"))[1] == _chat(intent, "Original")


def test_result_for_unknown_cartridge_is_rejected(tmp_path):
    store = ArchiveStore(tmp_path / "archive", "owner", "run")
    with pytest.raises(ArchiveError, match="not part of this run"):
        store.view(["first"], {"unknown": "unrelated"})
