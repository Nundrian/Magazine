"""Dedicated storage: real canonical core, Linux no-follow storage, no inference."""
import asyncio
import base64
import ctypes
import errno
from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
from pathlib import Path
import zipfile
from types import SimpleNamespace

import pytest
import magazine_ef as runner
from test_magazine_execution import mag, _create_run
from test_magazine_library import client, library, A, B


@pytest.fixture(autouse=True)
def storage(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "_runs_root", lambda: str(tmp_path / "magazine/runs"))
    monkeypatch.setattr(runner, "_STORAGE_LIBRARY_ROOT_GETTER", lambda: str(tmp_path / "library"))
    monkeypatch.delenv("MAGAZINE_INPUT_ZIPS_DIR", raising=False)
    monkeypatch.delenv("MAGAZINE_FINISHED_WORK_DIR", raising=False)
    return tmp_path / "magazine"


def package(name="Storage workflow"):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("magazine.yaml", 'schema_version: "1.0"\nname: ' + name + '\ncartridges:\n  - id: first\n    model: research\n    prompt_file: first.md\n')
        archive.writestr("first.md", "PROMPT\r\n")
        archive.comment = b"Retain original ZIP bytes, not a reconstruction"
    return buffer.getvalue()


def upload(client, raw, project="Demo"):
    return client.post("/api/magazine/library/import", headers=A,
                       json={"file_name": "workflow.zip", "file_data_b64": base64.b64encode(raw).decode(), "project_id": project})


def completed(mag, ids=("first",), owner="test-a", rid="run-1"):
    _create_run(mag, owner, rid, list(ids), status="COMPLETED")
    return owner, rid


def snapshot(storage, record):
    return storage / "finished-work" / record["directory"]


def test_successful_import_retains_exact_original(client, storage, library):
    raw = package()
    response = upload(client, raw)
    assert response.status_code == 201
    retained = response.json()["input_zip"]
    assert retained["retained"] is True
    assert (storage.parent / "input-zips" / retained["file"]).read_bytes() == raw
    assert (library / "Demo").is_dir()
    assert not list((storage.parent / "input-zips").glob(".zip-*"))


def test_retention_failure_does_not_undo_import_or_claim_retention(client, monkeypatch, library):
    monkeypatch.setattr(runner, "_storage_write", lambda *args: (_ for _ in ()).throw(OSError("disk full")))
    response = upload(client, package())
    assert response.status_code == 201
    assert response.json()["imported"] is True
    assert response.json()["input_zip"]["retained"] is False
    assert "WARNING" in response.json()["message"]
    assert (library / "Demo").is_dir()


def test_failed_validation_or_conflict_never_retains(client, storage):
    assert upload(client, b"not a ZIP").status_code == 400
    assert not Path(runner._storage_root("input")).exists()
    assert upload(client, package()).status_code == 201
    before = sorted(Path(runner._storage_root("input")).iterdir())
    assert upload(client, package()).status_code == 409
    assert sorted(Path(runner._storage_root("input")).iterdir()) == before


def test_unrelated_zip_is_not_imported(client, library):
    root = Path(runner._storage_root("input"))
    root.mkdir()
    (root / "unrelated.zip").write_bytes(package())
    assert client.get("/api/magazine/library", headers=A).json()["workflows"] == []
    assert list(library.iterdir()) == []


def test_unique_zip_names_and_cleanup(storage):
    first = runner._retain_input_zip("../CON", package())
    second = runner._retain_input_zip("../CON", package())
    assert first["retained"] and second["retained"]
    assert first["file"] != second["file"]
    assert "/" not in first["file"] and "\\" not in first["file"]
    assert len(list((storage / "input-zips").iterdir())) == 2


def test_zip_name_collision_never_overwrites_user_work(storage, monkeypatch):
    monkeypatch.setattr(runner.uuid, "uuid4", lambda: SimpleNamespace(hex="1" * 32))
    first = runner._retain_input_zip("Demo", package("Original"))
    assert first["retained"]
    target = storage / "input-zips" / first["file"]
    before = target.read_bytes()
    assert not runner._retain_input_zip("Demo", package("Different"))["retained"]
    assert target.read_bytes() == before
    assert not list((storage / "input-zips").glob(".zip-*"))


def test_completed_export_exact_bytes_manifest_and_zip(mag, storage):
    owner, rid = completed(mag, ("first", "a/b", "a b", "CON", "../evil"))
    path = Path(mag["rs"].result_path(runner._state_dir(owner, rid), "first"))
    path.write_bytes("Unicode: Ω\r\n\r\n".encode())
    before = Path(runner._state_path(owner, rid)).read_bytes()
    record = runner._export_run(owner, rid)
    directory = snapshot(storage, record)
    manifest = json.loads((directory / "manifest.json").read_bytes())
    assert manifest["run_id"] == rid and manifest["owner_id"] == owner
    assert manifest["status"] == "COMPLETED"
    assert [c["id"] for c in manifest["cartridges"]] == ["first", "a/b", "a b", "CON", "../evil"]
    assert len({c["file"] for c in manifest["cartridges"]}) == 5
    assert (directory / manifest["cartridges"][0]["file"]).read_bytes() == path.read_bytes()
    with zipfile.ZipFile(directory / "results.zip") as archive:
        assert set(archive.namelist()) == {"manifest.json", *(c["file"] for c in manifest["cartridges"])}
        for name in archive.namelist():
            assert archive.read(name) == (directory / name).read_bytes()
    assert before == Path(runner._state_path(owner, rid)).read_bytes()


def test_duplicate_and_concurrent_export_has_one_snapshot(mag, storage):
    owner, rid = completed(mag)
    with ThreadPoolExecutor(max_workers=4) as pool:
        records = list(pool.map(lambda _: runner._export_run(owner, rid), range(4)))
    assert all(record == records[0] for record in records)
    directory = snapshot(storage, records[0])
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in directory.iterdir()}
    assert runner._export_run(owner, rid) == records[0]
    assert before == {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in directory.iterdir()}
    assert len(list((storage / "finished-work").rglob("results.zip"))) == 1
    assert not list((storage / "finished-work").rglob(".export-*"))


def test_destination_owned_work_never_overwritten(mag, storage):
    owner, rid = completed(mag)
    record = runner._export_run(owner, rid)
    target = snapshot(storage, record) / "results.zip"
    target.write_bytes(b"USER OWNED WORK")
    result = runner._attempt_export(owner, rid)
    assert result["status"] == "FAILED"
    assert target.read_bytes() == b"USER OWNED WORK"
    assert runner._status_payload(owner, rid)["status"] == "COMPLETED"


def test_halted_partial_export_and_later_completed_have_separate_snapshots(mag, storage):
    owner, rid = completed(mag, ("first",))
    state = mag["ss"].load_run_state(runner._state_path(owner, rid))
    state.update(status="HALTED", todo=["second"], revision=3)
    mag["ss"].save_run_state(runner._state_path(owner, rid), state)
    halted = runner._export_run(owner, rid)
    assert halted["run_status"] == "HALTED" and halted["cartridge_count"] == 1
    assert runner._status_payload(owner, rid)["status"] == "HALTED"
    state.update(status="COMPLETED", todo=[], revision=4)
    mag["ss"].save_run_state(runner._state_path(owner, rid), state)
    final = runner._export_run(owner, rid)
    assert final["directory"] != halted["directory"]
    assert snapshot(storage, halted).exists() and snapshot(storage, final).exists()


@pytest.mark.parametrize("status", ["READY", "RUNNING"])
def test_nonterminal_export_refused(mag, status, storage):
    _create_run(mag, "test-a", "run-1", ["first"], status=status)
    with pytest.raises(runner._LibraryError, match="Only COMPLETED or HALTED"):
        runner._export_run("test-a", "run-1")
    assert not (storage / "finished-work").exists()


def test_export_ownership_and_api(client, mag):
    owner, rid = completed(mag)
    assert client.post(f"/api/magazine/runs/{rid}/export").status_code == 401
    assert client.post(f"/api/magazine/runs/{rid}/export", headers=B).status_code == 404
    assert client.post(f"/api/magazine/runs/{rid}/export", headers=A).status_code == 200
    assert client.get(f"/api/magazine/runs/{rid}/results", headers=A).json()["export"]["status"] == "EXPORTED"
    assert client.get(f"/api/magazine/runs/{rid}/results", headers=B).status_code == 404


@pytest.mark.parametrize("owner,rid", [("../test-a", "run-1"), ("test-a", "../run-1"), ("test-a", "C:\\escape"), ("test-a", ".")])
def test_unsafe_run_identifiers_refused(owner, rid):
    with pytest.raises(runner._LibraryError, match="Run not found"):
        runner._export_run(owner, rid)


@pytest.mark.parametrize("kind", ["input", "finished"])
@pytest.mark.parametrize("invalid", ["relative", "parent", "runs", "library", "other", "symlink"])
def test_unsafe_roots_refused(kind, invalid, storage, monkeypatch, tmp_path):
    values = {"relative": "relative", "parent": str(tmp_path / "parent/../escape"),
              "runs": runner._runs_root(), "library": str(tmp_path / "library"),
              "other": runner._storage_root("finished" if kind == "input" else "input")}
    if invalid == "symlink":
        target = tmp_path / "target"
        target.mkdir()
        link = tmp_path / "linked"
        link.symlink_to(target, target_is_directory=True)
        values["symlink"] = str(link / "storage")
    monkeypatch.setenv("MAGAZINE_INPUT_ZIPS_DIR" if kind == "input" else "MAGAZINE_FINISHED_WORK_DIR", values[invalid])
    with pytest.raises(runner._LibraryError):
        with runner._storage_fd(kind):
            pass


def test_missing_result_failure_preserves_state_and_results(mag, storage):
    owner, rid = completed(mag, ("first", "second"))
    Path(mag["rs"].result_path(runner._state_dir(owner, rid), "second")).unlink()
    before = runner._results_payload(owner, rid)
    assert runner._attempt_export(owner, rid)["status"] == "FAILED"
    assert runner._results_payload(owner, rid) == before
    assert not (storage / "finished-work").exists()


def test_export_write_failure_cleans_temporary_and_retry_works(mag, storage, monkeypatch):
    owner, rid = completed(mag)
    original = runner._storage_write
    def fail(fd, name, data):
        original(fd, name, data)
        raise OSError("disk full after write")
    monkeypatch.setattr(runner, "_storage_write", fail)
    assert runner._attempt_export(owner, rid)["status"] == "FAILED"
    assert not list((storage / "finished-work").rglob(".export-*"))
    assert not list((storage / "finished-work").rglob("results.zip"))
    assert runner._results_payload(owner, rid)["results"]["first"] == "RESULT-FOR-FIRST"
    monkeypatch.setattr(runner, "_storage_write", original)
    assert runner._attempt_export(owner, rid)["status"] == "EXPORTED"


def test_zip_write_failure_removes_partial_temp(monkeypatch, storage):
    original = runner._storage_write
    def fail(fd, name, data):
        original(fd, name, data[:10])
        raise OSError("disk full")
    monkeypatch.setattr(runner, "_storage_write", fail)
    assert runner._retain_input_zip("Demo", package())["retained"] is False
    assert list((storage / "input-zips").iterdir()) == []


def test_canonical_symlink_refused(mag, storage, tmp_path):
    owner, rid = completed(mag)
    result = Path(mag["rs"].result_path(runner._state_dir(owner, rid), "first"))
    outside = tmp_path / "secret"
    outside.write_text("private")
    result.unlink()
    result.symlink_to(outside)
    assert runner._attempt_export(owner, rid)["status"] == "FAILED"
    assert not list((storage / "finished-work").rglob("results.zip"))


def test_auto_export_only_completed(mag, storage, monkeypatch):
    owner, rid = completed(mag)
    asyncio.run(runner._auto_export_completed(owner, rid))
    assert runner._export_record(owner, rid)["status"] == "EXPORTED"
    state = mag["ss"].load_run_state(runner._state_path(owner, rid))
    state.update(status="HALTED", revision=3)
    mag["ss"].save_run_state(runner._state_path(owner, rid), state)
    monkeypatch.setattr(runner, "_attempt_export", lambda *args: pytest.fail("HALTED must not auto-export"))
    asyncio.run(runner._auto_export_completed(owner, rid))


def test_background_completion_hooks_export_without_status_mutation(mag, storage, monkeypatch):
    owner, rid = completed(mag)
    async def done(**kwargs):
        pass
    monkeypatch.setattr(mag["ar"], "run_magazine_open_webui", done)
    before = runner._status_payload(owner, rid)
    asyncio.run(runner._run_bg(rid, "unused", "unused", owner, "unused", "unused", {}, []))
    assert runner._export_record(owner, rid)["status"] == "EXPORTED"
    assert runner._status_payload(owner, rid) == before


def test_export_failure_is_visible_in_api_without_changing_completed(client, mag, monkeypatch):
    owner, rid = completed(mag)
    monkeypatch.setattr(runner, "_storage_write", lambda *args: (_ for _ in ()).throw(OSError("disk full")))
    assert client.post(f"/api/magazine/runs/{rid}/export", headers=A).status_code == 503
    payload = client.get(f"/api/magazine/runs/{rid}/results", headers=A).json()
    assert payload["status"] == "COMPLETED" and payload["export"]["status"] == "FAILED"
    assert payload["results"]["first"] == "RESULT-FOR-FIRST"


@pytest.mark.parametrize("failure", [False, True])
def test_real_engine_completion_auto_exports_even_when_export_fails(client, mag, monkeypatch, failure):
    assert upload(client, package()).status_code == 201
    preview = client.get("/api/magazine/library/Demo", headers=A).json()
    body = {"magazine": json.dumps(preview["document"]), "project_id": "Demo",
            "model_map": {"research": "ornith:9b-q8_0"}, "tool_ids": []}
    ready = client.post("/api/magazine/runs", headers=A, json=body)
    assert ready.status_code == 200
    rid = ready.json()["run_id"]
    owner = "test-a"
    metadata = runner._load_meta(owner, rid)
    assert metadata["project_id"] == "Demo" and metadata["name"] == "Storage workflow"
    path = runner._mag_path(owner, rid)
    prepared = mag["rp"].prepare_run(path, runner._run_base(owner, rid), owner)
    if failure:
        monkeypatch.setattr(runner, "_storage_write", lambda *args: (_ for _ in ()).throw(OSError("disk full")))
    async def dispatch(prompt, model_id):
        assert model_id == "ornith:9b-q8_0"
        return "EXACT OUTPUT Ω\r\n"
    asyncio.run(runner._run_bg_dispatch(rid, path, runner._run_base(owner, rid), owner,
                                       body["model_map"], prepared, dispatch))
    assert runner._status_payload(owner, rid)["status"] == "COMPLETED"
    assert runner._canonical_result_bytes(owner, rid, "first") == "EXACT OUTPUT Ω\r\n".encode()
    assert runner._export_record(owner, rid)["status"] == ("FAILED" if failure else "EXPORTED")


def test_project_metadata_cannot_label_different_document(client):
    assert upload(client, package()).status_code == 201
    preview = client.get("/api/magazine/library/Demo", headers=A).json()
    document = preview["document"]
    document["cartridges"][0]["prompt"] = "CHANGED"
    response = client.post("/api/magazine/runs", headers=A,
                           json={"magazine": json.dumps(document), "project_id": "Demo",
                                 "model_map": {"research": "ornith:9b-q8_0"}})
    assert response.status_code == 409


def test_post_publish_fsync_failure_is_not_false_success(mag, storage, monkeypatch):
    owner, rid = completed(mag)
    publish = runner._library_publish
    sync = os.fsync
    published_fd = []
    def track(fd, staging, name):
        publish(fd, staging, name)
        published_fd.append(fd)
    def fail(fd):
        if fd in published_fd:
            raise OSError("durability failure")
        return sync(fd)
    monkeypatch.setattr(runner, "_library_publish", track)
    monkeypatch.setattr(os, "fsync", fail)
    assert runner._attempt_export(owner, rid)["status"] == "FAILED"
    assert runner._status_payload(owner, rid)["status"] == "COMPLETED"
    assert len(list((storage / "finished-work").rglob("results.zip"))) == 1
    monkeypatch.setattr(os, "fsync", sync)
    assert runner._attempt_export(owner, rid)["status"] == "EXPORTED"


def test_destination_parent_symlink_does_not_escape(mag, storage, tmp_path):
    owner, rid = completed(mag)
    _, _, parts = runner._export_files(owner, rid)
    root = storage / "finished-work"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / parts[0]).symlink_to(outside, target_is_directory=True)
    assert runner._attempt_export(owner, rid)["status"] == "FAILED"
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("fail_link", [False, True])
def test_portable_publication_on_windows_style_filesystem(mag, storage, monkeypatch, fail_link):
    owner, rid = completed(mag)
    def unsupported(*args):
        ctypes.set_errno(errno.EINVAL)
        raise runner._LibraryError("Atomic project publication failed or is unsupported; existing projects were preserved.", 503)
    monkeypatch.setattr(runner, "_library_publish", unsupported)
    if fail_link:
        monkeypatch.setattr(os, "link", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("link failed")))
        assert runner._attempt_export(owner, rid)["status"] == "FAILED"
        assert not list((storage / "finished-work").rglob(".export-*"))
        assert not list((storage / "finished-work").rglob("manifest.json"))
    else:
        record = runner._export_run(owner, rid)
        assert runner._export_run(owner, rid) == record
        assert snapshot(storage, record).joinpath("manifest.json").is_file()
        assert not list((storage / "finished-work").rglob(".export-*"))
