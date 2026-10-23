"""Local library acceptance: real embedded loader and HTTP admission, no models.

The OWUI auth/discovery adapters are replaced locally; package validation and
run persistence are real. Never run this suite in the live OWUI container.
"""
import base64
import asyncio
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import types
import zipfile
from concurrent.futures import ThreadPoolExecutor
import threading
import stat
import zlib
import fcntl
import subprocess

import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

import magazine_ef as runner


def workflow(root, folder="Demo", name="Demonstration"):
    target = root / folder
    (target / "cartridges").mkdir(parents=True)
    (target / "magazine.yaml").write_text(
        'schema_version: "1.0"\nname: ' + name + '\ncartridges:\n'
        '  - id: first\n    model: research\n'
        '    prompt_file: cartridges/99-first.md\n'
        '  - id: second\n    model: research\n'
        '    prompt_file: cartridges/01-second.md\n', encoding="utf-8")
    (target / "cartridges/99-first.md").write_text("FIRST prompt\n", encoding="utf-8")
    (target / "cartridges/01-second.md").write_text("SECOND prompt\n", encoding="utf-8")
    return target


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    return root


@pytest.fixture
def client(monkeypatch, library, tmp_path):
    async def auth(authorization: str | None = Header(default=None)):
        if authorization not in ("Bearer test-a", "Bearer test-b"):
            raise HTTPException(status_code=401, detail="Authentication required")
        return types.SimpleNamespace(id=authorization.removeprefix("Bearer "),
                                     role="admin" if authorization == "Bearer test-a" else "user")

    for name in ("open_webui", "open_webui.utils", "open_webui.utils.auth"):
        module = types.ModuleType(name)
        monkeypatch.setitem(sys.modules, name, module)
    sys.modules["open_webui.utils.auth"].get_current_user = auth
    monkeypatch.setattr(runner, "_runs_root", lambda: str(tmp_path / "runs"))

    async def models(app, user):
        return [{"id": "ornith:9b-q8_0"}] if user.id == "test-a" else []

    async def tools(app, user):
        return []

    monkeypatch.setattr(runner, "_fetch_models", models)
    monkeypatch.setattr(runner, "_fetch_tools", tools)
    app = FastAPI()
    runner._install_routes(app, library_root=lambda: str(library))
    with TestClient(app) as http:
        yield http


A = {"Authorization": "Bearer test-a"}
B = {"Authorization": "Bearer test-b"}


def inventory(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


def test_discover_multiple_folders_including_invalid_without_writes(library):
    workflow(library, "Beta", "Beta workflow")
    workflow(library, "Alpha", "Alpha workflow")
    (library / "Broken").mkdir()
    (library / "leftover.zip").write_bytes(b"not a workflow")
    (library / "notes.txt").write_text("unrelated", encoding="utf-8")
    (library / "space folder").mkdir()
    before = inventory(library)
    data = runner._library_list(str(library))
    assert data["available"] is True
    assert [w["workflow_id"] for w in data["workflows"]] == ["Alpha", "Beta", "Broken"]
    assert [w["valid"] for w in data["workflows"]] == [True, True, False]
    assert data["workflows"][0]["name"] == "Alpha workflow"
    assert data["workflows"][0]["cartridge_count"] == 2
    assert data["workflows"][0]["order"] == ["first", "second"]
    assert "document" not in data["workflows"][0]
    assert inventory(library) == before
    assert str(library) not in json.dumps(data)


def test_preview_is_normalised_verbatim_snapshot_in_manifest_order(library):
    source = workflow(library)
    data = runner._library_preview(str(library), "Demo")
    assert data["valid"] is True
    assert data["name"] == "Demonstration"
    assert data["document"] == {"schema_version": "1.0", "cartridges": [
        {"id": "first", "job_type": "research", "prompt": "FIRST prompt\n"},
        {"id": "second", "job_type": "research", "prompt": "SECOND prompt\n"}]}
    assert [c["order"] for c in data["cartridges"]] == [1, 2]
    assert [c["prompt"] for c in data["cartridges"]] == ["FIRST prompt\n", "SECOND prompt\n"]
    (source / "cartridges/99-first.md").write_text("changed", encoding="utf-8")
    assert data["document"]["cartridges"][0]["prompt"] == "FIRST prompt\n"


@pytest.mark.parametrize("damage,expected", [
    ("yaml", "YAML"), ("missing", "Missing"), ("duplicate", "Duplicate"),
    ("unknown", "schema"), ("traversal", "path"), ("absolute", "path"),
    ("empty_prompt", "schema"), ("encoding", "UTF-8")])
def test_invalid_packages_preserve_canonical_validation(library, damage, expected):
    source = workflow(library)
    manifest = source / "magazine.yaml"
    text = manifest.read_text(encoding="utf-8")
    if damage == "yaml": manifest.write_text("cartridges: [", encoding="utf-8")
    elif damage == "missing": (source / "cartridges/99-first.md").unlink()
    elif damage == "duplicate": manifest.write_text(text.replace("id: second", "id: first"), encoding="utf-8")
    elif damage == "unknown": manifest.write_text(text + "extra: forbidden\n", encoding="utf-8")
    elif damage == "traversal": manifest.write_text(text.replace("cartridges/99-first.md", "../outside.md"), encoding="utf-8")
    elif damage == "absolute": manifest.write_text(text.replace("cartridges/99-first.md", "/etc/passwd"), encoding="utf-8")
    elif damage == "empty_prompt": (source / "cartridges/99-first.md").write_text("  ", encoding="utf-8")
    elif damage == "encoding": manifest.write_bytes(b"\xff\xfe")
    data = runner._library_preview(str(library), "Demo")
    assert data["valid"] is False
    assert expected.lower() in data["error"].lower()
    assert "document" not in data
    assert str(library) not in json.dumps(data)
    assert "/etc/passwd" not in json.dumps(data)


@pytest.mark.parametrize("identifier", ["..", "../Demo", "/Demo", "C:\\Demo", "Demo/child", "Demo\\child", "", ".Demo", "Demo%2fchild", "a" * 65])
def test_browser_identifier_is_not_a_path(library, identifier):
    with pytest.raises(runner._LibraryError) as exc:
        runner._library_preview(str(library), identifier)
    assert exc.value.status_code == 400
    assert str(library) not in str(exc.value)


@pytest.mark.parametrize("kind", ["workflow", "manifest", "prompt", "directory"])
def test_symlink_escapes_are_rejected_before_loader_reads(library, tmp_path, kind):
    outside = workflow(tmp_path / "outside", "Secret")
    if kind == "workflow":
        (library / "Demo").symlink_to(outside, target_is_directory=True)
    else:
        source = workflow(library)
        if kind == "manifest":
            (source / "magazine.yaml").unlink()
            (source / "magazine.yaml").symlink_to(outside / "magazine.yaml")
        elif kind == "prompt":
            (source / "cartridges/99-first.md").unlink()
            (source / "cartridges/99-first.md").symlink_to(outside / "cartridges/99-first.md")
        else:
            (source / "escape").symlink_to(outside, target_is_directory=True)
    data = runner._library_preview(str(library), "Demo")
    assert data["valid"] is False
    assert "link" in data["error"].lower()
    assert str(outside) not in json.dumps(data)


def test_root_symlink_is_unavailable(library, tmp_path):
    link = tmp_path / "linked-root"
    link.symlink_to(library, target_is_directory=True)
    assert runner._library_list(str(link))["available"] is False


def test_root_symlink_in_an_ancestor_is_also_unavailable(library, tmp_path):
    link = tmp_path / "linked-ancestor"
    link.symlink_to(tmp_path, target_is_directory=True)
    assert runner._library_list(str(link / "library"))["available"] is False


def test_run_storage_cannot_be_exposed_as_shared_library(library, monkeypatch):
    runs = library / "runs"
    runs.mkdir()
    monkeypatch.setattr(runner, "_runs_root", lambda: str(runs))
    for root in (library, runs, runs / "owner"):
        assert runner._library_list(str(root))["available"] is False
        assert runner._library_list("/" + str(root))["available"] is False


def test_file_replaced_by_symlink_between_stat_and_open_is_refused(library, tmp_path, monkeypatch):
    source = workflow(library)
    secret = tmp_path / "secret.md"
    secret.write_text("SECRET MUST NEVER BE READ", encoding="utf-8")
    real_open = os.open
    real_loader = runner._mag()["doc"].load_document
    loaded = []

    def spy_loader(path):
        loaded.append(path)
        return real_loader(path)

    def swap_open(path, flags, *args, **kwargs):
        if path == "99-first.md":
            p = source / "cartridges/99-first.md"
            p.unlink()
            p.symlink_to(secret)
        return real_open(path, flags, *args, **kwargs)

    # Feature detection refers to the current callable as well as os.open.
    monkeypatch.setattr(os, "supports_dir_fd", os.supports_dir_fd | {swap_open})
    monkeypatch.setattr(os, "open", swap_open)
    monkeypatch.setattr(runner._mag()["doc"], "load_document", spy_loader)
    preview = runner._library_preview(str(library), "Demo")
    assert preview["valid"] is False and "link" in preview["error"].lower()
    assert loaded == []
    assert "SECRET" not in json.dumps(preview)


def test_fifo_is_refused_without_opening_or_blocking(library):
    source = workflow(library)
    os.mkfifo(source / "cartridges/fifo")
    preview = runner._library_preview(str(library), "Demo")
    assert preview["valid"] is False
    assert "non-regular" in preview["error"]


def test_root_entry_and_directory_depth_limits(library, monkeypatch):
    workflow(library)
    monkeypatch.setattr(runner, "_LIBRARY_MAX_ROOT_ENTRIES", 0)
    assert runner._library_list(str(library))["truncated"] is True
    monkeypatch.setattr(runner, "_LIBRARY_MAX_DEPTH", 0)
    assert "depth limit" in runner._library_preview(str(library), "Demo")["error"]


@pytest.mark.parametrize("kind", ["missing", "empty", "blank", "file"])
def test_unavailable_or_empty_library_is_nonfatal(library, kind):
    root = str(library)
    if kind == "missing": root += "/not-mounted"
    elif kind == "blank": root = ""
    elif kind == "file":
        p = library / "file"; p.write_text("x", encoding="utf-8"); root = str(p)
    data = runner._library_list(root)
    assert data["available"] is (kind == "empty")
    assert data["workflows"] == []
    assert data["message"]
    assert root not in data["message"] if root else True


def test_nested_workflows_are_not_discovered(library):
    workflow(library / "Outer", "Nested")
    data = runner._library_list(str(library))
    assert [w["workflow_id"] for w in data["workflows"]] == ["Outer"]
    assert data["workflows"][0]["valid"] is False


def test_bounded_scan_and_file_size(library, monkeypatch):
    for folder in ("A", "B", "C"): workflow(library, folder)
    monkeypatch.setattr(runner, "_LIBRARY_MAX_WORKFLOWS", 2)
    assert runner._library_list(str(library))["truncated"] is True
    assert len(runner._library_list(str(library))["workflows"]) == 2
    monkeypatch.setattr(runner, "_LIBRARY_MAX_FILE_BYTES", 8)
    data = runner._library_preview(str(library), "A")
    assert data["valid"] is False and "limit" in data["error"].lower()


def test_file_count_and_total_byte_limits(library, monkeypatch):
    workflow(library)
    monkeypatch.setattr(runner, "_LIBRARY_MAX_FILES", 2)
    assert runner._library_preview(str(library), "Demo")["valid"] is False
    monkeypatch.setattr(runner, "_LIBRARY_MAX_FILES", 100)
    monkeypatch.setattr(runner, "_LIBRARY_MAX_TOTAL_BYTES", 10)
    assert runner._library_preview(str(library), "Demo")["valid"] is False


def test_preview_calls_the_real_canonical_loader_on_a_bounded_snapshot(library, monkeypatch):
    workflow(library)
    core = runner._mag()
    real = core["doc"].load_document
    seen = []

    def spy(path):
        seen.append(Path(path))
        assert Path(path).is_dir()
        assert not Path(path).is_relative_to(library)
        return real(path)

    monkeypatch.setattr(core["doc"], "load_document", spy)
    data = runner._library_preview(str(library), "Demo")
    assert data["valid"] is True
    assert len(seen) == 1
    assert not seen[0].exists()  # only our disposable snapshot is cleaned up


@pytest.mark.parametrize("path", ["/api/magazine/library", "/api/magazine/library/Demo"])
def test_library_routes_require_existing_authentication(client, library, path):
    workflow(library)
    assert client.get(path).status_code == 401
    assert client.get(path, headers=A).status_code == 200
    assert client.get(path, headers=B).status_code == 200


def test_shared_catalogue_and_user_owned_immutable_ready_run(client, library):
    source = workflow(library)
    preview = client.get("/api/magazine/library/Demo", headers=A).json()
    assert preview == client.get("/api/magazine/library/Demo", headers=B).json()
    body = {"magazine": json.dumps(preview["document"]),
            "model_map": {"research": "ornith:9b-q8_0"}, "tool_ids": [], "owner_id": "test-b"}
    response = client.post("/api/magazine/runs", json=body, headers=A)
    assert response.status_code == 200
    created = response.json(); rid = created["run_id"]
    assert created["status"] == "READY" and created["owner_id"] == "test-a"
    state = client.get(f"/api/magazine/runs/{rid}", headers=A).json()
    assert state["todo"] == ["first", "second"] and state["done"] == []
    assert state["current_cartridge"] is None and state["revision"] == 0
    assert client.get(f"/api/magazine/runs/{rid}", headers=B).status_code == 404
    assert client.get(f"/api/magazine/runs/{rid}/results", headers=B).status_code == 404
    assert not any(r["run_id"] == rid for r in client.get("/api/magazine/runs", headers=B).json()["runs"])
    persisted = Path(runner._mag_path("test-a", rid))
    before = persisted.read_bytes()
    (source / "cartridges/99-first.md").write_text("replaced", encoding="utf-8")
    (source / "magazine.yaml").unlink()
    assert persisted.read_bytes() == before
    assert runner._mag()["doc"].load_document(persisted).document == preview["document"]
    assert client.get(f"/api/magazine/runs/{rid}", headers=A).json() == state
    # Test B may preview the catalogue without permission to use Test A's model.
    assert client.post("/api/magazine/runs", json=body, headers=B).status_code == 400


def test_source_deleted_before_admission_does_not_change_selected_snapshot(client, library):
    source = workflow(library)
    preview = client.get("/api/magazine/library/Demo", headers=A).json()
    (source / "magazine.yaml").unlink()
    response = client.post("/api/magazine/runs", headers=A, json={
        "magazine": json.dumps(preview["document"]),
        "model_map": {"research": "ornith:9b-q8_0"}, "tool_ids": []})
    assert response.status_code == 200
    path = runner._mag_path("test-a", response.json()["run_id"])
    assert runner._mag()["doc"].load_document(path).document == preview["document"]


def test_existing_model_and_tool_admission_still_enforced(client, library):
    workflow(library)
    doc = client.get("/api/magazine/library/Demo", headers=A).json()["document"]
    for models, tools in (({}, []), ({"research": "unavailable"}, []),
                          ({"research": "ornith:9b-q8_0"}, ["unavailable"])):
        assert client.post("/api/magazine/runs", headers=A, json={
            "magazine": json.dumps(doc), "model_map": models, "tool_ids": tools}).status_code == 400


def test_library_missing_does_not_break_upload_or_json(client, library):
    source = workflow(library)
    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w") as archive:
        for p in source.rglob("*"):
            if p.is_file(): archive.write(p, str(p.relative_to(source)))
    library.rename(library.with_name("not-mounted"))
    assert client.get("/api/magazine/library", headers=A).json()["available"] is False
    document = {"schema_version": "1.0", "cartridges": [
        {"id": "one", "job_type": "research", "prompt": "inline prompt"}]}
    cases = [{"file_type": "json", "document_json": json.dumps(document)},
             {"file_type": "magazine", "file_name": "demo.magazine",
              "file_data_b64": base64.b64encode(archive_bytes.getvalue()).decode()}]
    for body in cases:
        response = client.post("/api/magazine/packages", json=body, headers=A)
        assert response.status_code == 200 and response.json()["valid"] is True
        assert client.post("/api/magazine/runs", headers=A, json={
            "magazine": json.dumps(response.json()["document"]),
            "model_map": {"research": "ornith:9b-q8_0"}, "tool_ids": []}).status_code == 200


def test_function_valve_defaults_and_routes_are_idempotent(client):
    event = runner.Event()
    assert event.valves.library_root == "/app/backend/data/magazine/library"
    runner._install_routes(client.app, library_root=lambda: "")
    paths = [r.path for r in client.app.routes]
    assert paths.count("/api/magazine/library") == 1
    assert paths.count("/api/magazine/library/{workflow_id}") == 1
    assert client.get("/api/magazine/library", headers=A).json()["available"] is False


def test_event_initializes_missing_library_without_starting_a_run(client, tmp_path):
    library = tmp_path / "clean-data" / "magazine" / "library"
    event = runner.Event()
    event.valves.library_root = str(library)
    asyncio.run(event.event(__app__=client.app))
    assert library.is_dir()
    response = client.get("/api/magazine/library", headers=A)
    assert response.status_code == 200
    assert response.json()["available"] is True
    assert client.get("/api/magazine/runs", headers=A).json()["count"] == 0


def test_valve_changes_are_used_by_already_installed_routes(client, library):
    workflow(library)
    event = runner.Event()
    asyncio.run(event.event(__app__=client.app))
    event.valves.library_root = str(library)
    assert client.get("/api/magazine/library", headers=A).json()["available"] is True
    event.valves.library_root = ""
    assert client.get("/api/magazine/library", headers=A).json()["available"] is False
    assert client.get("/api/magazine/library/Demo", headers=A).status_code == 503
    assert client.get("/api/magazine/library/C:outside", headers=A).status_code == 400


def test_concurrent_json_upload_validation_keeps_distinct_snapshots(monkeypatch):
    core = runner._mag()
    real = core["doc"].load_document
    barrier = threading.Barrier(2, timeout=5)
    paths = []

    def delayed_loader(path):
        paths.append(Path(path))
        barrier.wait()
        return real(path)

    monkeypatch.setattr(core["doc"], "load_document", delayed_loader)
    def validate(prompt):
        raw = json.dumps({"schema_version": "1.0", "cartridges": [
            {"id": "one", "job_type": "research", "prompt": prompt}]})
        return runner._load_package_from_upload("", "", "json", raw)[0]
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(validate, ["USER A PROMPT", "USER B PROMPT"]))
    assert [d["cartridges"][0]["prompt"] for d in results] == ["USER A PROMPT", "USER B PROMPT"]
    assert paths[0] != paths[1]
    assert all(not p.exists() for p in paths)


def project_zip(root, wrapper=""):
    source = workflow(root)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                archive.write(path, wrapper + str(path.relative_to(source)))
    return output.getvalue()


def encoded_zip(members):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in members:
            archive.writestr(name, value)
    return output.getvalue()


def import_project(root, raw, identifier="Imported", file_name="Imported.zip"):
    return runner._library_import(str(root), file_name, base64.b64encode(raw).decode(), identifier)


def assert_no_staging(library):
    assert not any(p.name.startswith(".import-") for p in library.iterdir())


@pytest.mark.parametrize("wrapper", ["", "Workflow/"])
def test_zip_import_root_or_single_wrapper_reuses_canonical_preview(library, tmp_path, wrapper):
    raw = project_zip(tmp_path / "input", wrapper)
    imported = import_project(library, raw)
    assert imported["imported"] is True and imported["project_id"] == "Imported"
    assert imported["name"] == "Demonstration"
    assert imported["order"] == ["first", "second"]
    assert (library / "Imported/magazine.yaml").is_file()
    preview = runner._library_preview(str(library), "Imported")
    assert [c["prompt"] for c in preview["cartridges"]] == ["FIRST prompt\n", "SECOND prompt\n"]
    assert_no_staging(library)


def test_import_derives_safe_identifier_from_zip_filename(library, tmp_path):
    imported = import_project(library, project_zip(tmp_path / "input"), "", "My project.zip")
    assert imported["project_id"] == "My-project"


@pytest.mark.parametrize("member", ["../outside.md", "/absolute.md", "C:/absolute.md", "C:relative.md",
    "cartridges\\evil.md", "//server/share.md", "a/../evil.md", "a//evil.md", "./evil.md",
    "cartridges/evil.md:stream", "cartridges/trailing. ", "cartridges/CON", "cartridges/evil\x01.md"])
def test_zip_unsafe_path_is_rejected_before_any_publication(library, member):
    raw = encoded_zip([(member, "unsafe")])
    with pytest.raises(runner._LibraryError): import_project(library, raw)
    assert not (library / "Imported").exists()
    assert_no_staging(library)


@pytest.mark.parametrize("file_type", [stat.S_IFLNK, stat.S_IFIFO, stat.S_IFCHR, stat.S_IFSOCK])
def test_zip_symlink_and_special_entries_are_rejected(library, file_type):
    info = zipfile.ZipInfo("unsafe")
    info.create_system = 3
    info.external_attr = (file_type | 0o777) << 16
    raw = encoded_zip([(info, "target")])
    with pytest.raises(runner._LibraryError): import_project(library, raw)
    assert_no_staging(library)


@pytest.mark.parametrize("members", [
    [("same.md", "A"), ("same.md", "B")],
    [("Prompt.md", "A"), ("prompt.md", "B")],
    [("Cartridges/a.md", "A"), ("cartridges/b.md", "B")],
    [("a", "file"), ("a/prompt.md", "nested")]])
def test_zip_duplicate_case_collision_and_file_directory_conflict(library, members):
    with pytest.raises(runner._LibraryError): import_project(library, encoded_zip(members))
    assert_no_staging(library)


@pytest.mark.parametrize("kind", ["invalid_zip", "missing_manifest", "invalid_manifest", "ambiguous_wrapper"])
def test_failed_import_validation_leaves_existing_projects_intact(library, kind):
    source = workflow(library, "Existing")
    before = inventory(source)
    if kind == "invalid_zip": raw = b"not a zip"
    elif kind == "missing_manifest": raw = encoded_zip([("prompt.md", "x")])
    elif kind == "invalid_manifest": raw = encoded_zip([("magazine.yaml", "cartridges: [")])
    else: raw = encoded_zip([("a/magazine.yaml", "x"), ("b/magazine.yaml", "y")])
    with pytest.raises(runner._LibraryError): import_project(library, raw)
    assert inventory(source) == before
    assert not (library / "Imported").exists()
    assert_no_staging(library)


@pytest.mark.parametrize("limit", ["compressed", "expanded", "file_count", "depth", "path_length", "ratio"])
def test_zip_bounded_limits(library, tmp_path, monkeypatch, limit):
    raw = project_zip(tmp_path / "input")
    if limit == "compressed": monkeypatch.setattr(runner, "_LIBRARY_MAX_ARCHIVE_BYTES", 10)
    elif limit == "expanded": monkeypatch.setattr(runner, "_LIBRARY_MAX_TOTAL_BYTES", 10)
    elif limit == "file_count": monkeypatch.setattr(runner, "_LIBRARY_MAX_FILES", 1)
    elif limit == "depth": raw = encoded_zip([("/".join(["a"] * 10) + "/prompt.md", "x")])
    elif limit == "path_length": raw = encoded_zip([("a" * 250 + ".md", "x")])
    else: raw = encoded_zip([("prompt.md", "x" * 300000)])
    with pytest.raises(runner._LibraryError): import_project(library, raw)
    assert not (library / "Imported").exists()
    assert_no_staging(library)


@pytest.mark.parametrize("identifier", ["../elsewhere", "/path", "C:\\path", ".hidden"])
def test_import_identifier_cannot_be_a_filesystem_path(library, tmp_path, identifier):
    with pytest.raises(runner._LibraryError) as exc:
        import_project(library, project_zip(tmp_path / "input"), identifier)
    assert exc.value.status_code == 400
    assert_no_staging(library)


def test_duplicate_identifier_case_or_manifest_name_is_a_conflict(library, tmp_path):
    raw = project_zip(tmp_path / "input")
    import_project(library, raw, "Original")
    before = inventory(library / "Original")
    for identifier in ("Original", "original", "Different-ID"):
        with pytest.raises(runner._LibraryError) as exc: import_project(library, raw, identifier)
        assert exc.value.status_code == 409
    assert inventory(library / "Original") == before
    assert_no_staging(library)


def test_atomic_publication_has_no_visible_partial_project(library, tmp_path, monkeypatch):
    raw = project_zip(tmp_path / "input")
    real = runner._library_publish
    observations = []

    def spy(root_fd, staging, project_id):
        listed = runner._library_list(str(library))
        observations.append(listed)
        assert listed["workflows"] == []
        assert not (library / project_id).exists()
        return real(root_fd, staging, project_id)

    monkeypatch.setattr(runner, "_library_publish", spy)
    import_project(library, raw)
    assert len(observations) == 1
    assert runner._library_list(str(library))["workflows"][0]["valid"] is True
    assert_no_staging(library)


@pytest.mark.parametrize("identifiers", [("Imported", "Imported"), ("Left", "Right")])
def test_concurrent_conflicting_imports_publish_exactly_once(library, tmp_path, identifiers):
    raw = project_zip(tmp_path / "input")
    runner._mag()  # Initialize the real embedded loader before concurrent work.
    def attempt(identifier):
        try: return import_project(library, raw, identifier)["imported"]
        except runner._LibraryError as exc: return exc.status_code
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(attempt, identifiers))
    assert sorted(outcomes, key=str) == sorted([True, 409], key=str)
    listed = runner._library_list(str(library))["workflows"]
    assert len(listed) == 1 and listed[0]["valid"] is True
    assert_no_staging(library)


def test_publication_never_overwrites_even_an_empty_existing_directory(library, tmp_path):
    (library / "Imported").mkdir()
    with pytest.raises(runner._LibraryError) as exc:
        import_project(library, project_zip(tmp_path / "input"))
    assert exc.value.status_code == 409
    assert list((library / "Imported").iterdir()) == []
    assert_no_staging(library)


def test_permission_failure_and_publication_failure_cleanup_only_owned_stage(library, tmp_path, monkeypatch):
    sentinel = library / ".import-unrelated"
    sentinel.mkdir(); (sentinel / "keep").write_text("existing temporary artifact", encoding="utf-8")
    raw = project_zip(tmp_path / "input")
    real_mkdir = os.mkdir
    def deny(path, *args, **kwargs):
        if isinstance(path, str) and path.startswith(".import-"):
            raise PermissionError("sensitive host path")
        return real_mkdir(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(os, "mkdir", deny)
        with pytest.raises(runner._LibraryError) as exc: import_project(library, raw)
        assert "sensitive host path" not in str(exc.value)
    def fail(*args): raise OSError("private path")
    monkeypatch.setattr(runner, "_library_publish", fail)
    with pytest.raises(runner._LibraryError): import_project(library, raw)
    assert [p.name for p in library.iterdir()] == [".import-unrelated"]
    assert (sentinel / "keep").read_text() == "existing temporary artifact"


def test_import_http_admin_only_and_project_to_owned_ready_snapshot(client, library, tmp_path):
    raw = project_zip(tmp_path / "input")
    body = {"file_name": "Uploaded.zip", "file_data_b64": base64.b64encode(raw).decode(), "project_id": "Uploaded"}
    assert client.post("/api/magazine/library/import", json=body).status_code == 401
    assert client.post("/api/magazine/library/import", json=body, headers=B).status_code == 403
    assert list(library.iterdir()) == []
    response = client.post("/api/magazine/library/import", json=body, headers=A)
    assert response.status_code == 201 and response.json()["project_id"] == "Uploaded"
    assert client.post("/api/magazine/library/import", json=body, headers=A).status_code == 409
    assert client.get("/api/magazine/library", headers=A).json()["can_import"] is True
    assert client.get("/api/magazine/library", headers=B).json()["can_import"] is False
    preview = client.get("/api/magazine/library/Uploaded", headers=B)
    assert preview.status_code == 200 and preview.json()["valid"] is True
    created = client.post("/api/magazine/runs", headers=A, json={
        "magazine": json.dumps(preview.json()["document"]),
        "model_map": {"research": "ornith:9b-q8_0"}, "tool_ids": []}).json()
    assert created["status"] == "READY" and created["owner_id"] == "test-a"
    rid = created["run_id"]; before = Path(runner._mag_path("test-a", rid)).read_bytes()
    (library / "Uploaded/magazine.yaml").unlink()
    assert Path(runner._mag_path("test-a", rid)).read_bytes() == before
    assert client.get(f"/api/magazine/runs/{rid}", headers=B).status_code == 404
    assert client.get(f"/api/magazine/runs/{rid}/results", headers=B).status_code == 404


def test_import_into_missing_or_inaccessible_library_is_nonfatal(client, library, tmp_path, monkeypatch):
    raw = project_zip(tmp_path / "input")
    body = {"file_name": "demo.zip", "file_data_b64": base64.b64encode(raw).decode()}
    library.rename(library.with_name("missing"))
    assert client.post("/api/magazine/library/import", json=body, headers=A).status_code == 503
    assert client.get("/api/magazine/library", headers=B).status_code == 200
    library.with_name("missing").rename(library)
    real_open = os.open
    def denied(path, *args, **kwargs):
        if path == "library": raise PermissionError("host private path")
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr(os, "supports_dir_fd", os.supports_dir_fd | {denied})
    monkeypatch.setattr(os, "open", denied)
    assert client.get("/api/magazine/library", headers=A).json()["available"] is False
    response = client.post("/api/magazine/library/import", json=body, headers=A)
    assert response.status_code == 503 and "host private path" not in response.text


def test_decompression_failure_is_a_safe_archive_error_and_cleans_stage(library, tmp_path, monkeypatch):
    def corrupted(*args): raise zlib.error("private decompressor details")
    monkeypatch.setattr(runner, "_library_extract_zip", corrupted)
    with pytest.raises(runner._LibraryError) as exc:
        import_project(library, project_zip(tmp_path / "input"))
    assert exc.value.status_code == 400
    assert "private decompressor details" not in str(exc.value)
    assert_no_staging(library)


def test_atomic_publication_rejects_destination_created_after_conflict_scan(library, tmp_path, monkeypatch):
    real = runner._library_publish
    def race(root_fd, staging, project_id):
        (library / project_id).mkdir()
        return real(root_fd, staging, project_id)
    monkeypatch.setattr(runner, "_library_publish", race)
    with pytest.raises(runner._LibraryError) as exc:
        import_project(library, project_zip(tmp_path / "input"))
    assert exc.value.status_code == 409
    assert list((library / "Imported").iterdir()) == []
    assert_no_staging(library)


def test_unsupported_rename_uses_exclusive_portable_publication_without_overwrite(library, tmp_path, monkeypatch):
    monkeypatch.setattr(runner.ctypes, "CDLL", lambda *args, **kwargs: types.SimpleNamespace())
    archive = project_zip(tmp_path / "input")
    response = import_project(library, archive)
    assert response["imported"] is True
    published = (library / "Imported/magazine.yaml").read_bytes()
    with pytest.raises(runner._LibraryError) as exc:
        import_project(library, archive)
    assert exc.value.status_code == 409
    assert (library / "Imported/magazine.yaml").read_bytes() == published
    assert_no_staging(library)


def test_import_lock_wait_is_bounded(library, tmp_path, monkeypatch):
    def busy(*args): raise BlockingIOError()
    monkeypatch.setattr(fcntl, "flock", busy)
    monkeypatch.setattr(runner, "_LIBRARY_IMPORT_LOCK_SECONDS", 0)
    with pytest.raises(runner._LibraryError) as exc:
        import_project(library, project_zip(tmp_path / "input"))
    assert exc.value.status_code == 409
    assert list(library.iterdir()) == []


@pytest.mark.parametrize("phase", ["file", "published_directory"])
def test_fsync_failure_preserves_prior_projects_and_reports_publication_state(library, tmp_path, monkeypatch, phase):
    raw = project_zip(tmp_path / "input")
    real = os.fsync; root_inode = library.stat().st_ino
    def fail(fd):
        st = os.fstat(fd)
        if (phase == "file" and stat.S_ISREG(st.st_mode)) or (phase == "published_directory" and st.st_ino == root_inode):
            raise OSError("private path")
        return real(fd)
    monkeypatch.setattr(os, "fsync", fail)
    with pytest.raises(runner._LibraryError) as exc: import_project(library, raw)
    assert exc.value.status_code == 503
    if phase == "published_directory":
        assert "published" in str(exc.value)
        assert runner._library_preview(str(library), "Imported")["valid"] is True
    else: assert not (library / "Imported").exists()
    assert_no_staging(library)


def test_cleanup_refuses_a_replaced_staging_identity(library, tmp_path, monkeypatch):
    observed = []
    def swap(root_fd, staging, project_id):
        original = library / staging
        original.rename(library / "SavedOriginal")
        original.mkdir(); (original / "keep").write_text("unrelated replacement", encoding="utf-8")
        observed.append(original)
        raise OSError("publication interrupted")
    monkeypatch.setattr(runner, "_library_publish", swap)
    with pytest.raises(runner._LibraryError) as exc:
        import_project(library, project_zip(tmp_path / "input"))
    assert exc.value.status_code == 500 and "cleanup" in str(exc.value)
    assert (observed[0] / "keep").read_text() == "unrelated replacement"
    assert (library / "SavedOriginal/magazine.yaml").exists()


def test_invalid_base64_and_corrupted_zip_crc_are_refused(library, tmp_path):
    with pytest.raises(runner._LibraryError) as exc:
        runner._library_import(str(library), "demo.zip", "!not-base64!")
    assert exc.value.status_code == 400
    raw = bytearray(project_zip(tmp_path / "input"))
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        info = archive.infolist()[0]
        offset = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
    raw[offset + 2] ^= 0x55
    with pytest.raises(runner._LibraryError): import_project(library, bytes(raw))
    assert_no_staging(library)


def test_development_workflow_fixture_import_and_order(library):
    sample = Path(__file__).parent / "fixtures/Projects-Smoke"
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(sample.rglob("*")):
            if path.is_file(): archive.write(path, path.relative_to(sample).as_posix())
    imported = import_project(library, output.getvalue(), "Projects-Smoke")
    assert imported["order"] == ["first", "second"]
    preview = runner._library_preview(str(library), "Projects-Smoke")
    assert preview["document"]["cartridges"][0]["job_type"] == "acceptance"
    assert preview["cartridges"][0]["prompt"].strip() == "Reply with exactly ZIP_PROJECT_FIRST"


def test_atomic_publication_excludes_two_independent_python_processes(library):
    for label in ("A", "B"):
        stage = library / (".import-" + label)
        stage.mkdir(); (stage / "origin").write_text(label, encoding="utf-8")
    script = '''import os,sys
import magazine_ef as runner
root_fd=os.open(sys.argv[1],os.O_RDONLY|os.O_DIRECTORY)
try:
    runner._library_publish(root_fd,sys.argv[2],"Winner")
    print(201)
except runner._LibraryError as exc:
    print(exc.status_code)
finally:
    os.close(root_fd)
'''
    def publish(label):
        response = subprocess.run([sys.executable, "-c", script, str(library), ".import-" + label],
            capture_output=True, text=True, timeout=10, check=True, cwd=Path(__file__).parent.parent)
        return int(response.stdout.strip())
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(publish, ["A", "B"]))
    assert sorted(outcomes) == [201, 409]
    winner = (library / "Winner/origin").read_text()
    assert winner in ("A", "B")
    loser = "B" if winner == "A" else "A"
    assert (library / (".import-" + loser) / "origin").read_text() == loser
