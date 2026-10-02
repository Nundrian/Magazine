from pathlib import Path

import os
import zipfile

import pytest

from magazine.package import CartridgeRecord, load_package, to_document
from magazine.schema import MagazineSchemaError, validate_magazine


def _write_package(tmp_path: Path, manifest: str, prompts: dict[str, str] | None = None):
    tmp_path = Path(tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "magazine.yaml").write_text(manifest, encoding="utf-8")
    for rel, text in (prompts or {}).items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


def _valid_manifest(extra_cartridge: str = "") -> str:
    return f'''schema_version: "1.0"
name: Demo
cartridges:
  - id: first
    model: research
    prompt_file: cartridges/001.md
{extra_cartridge}'''


def _write_package_archive(
    path: Path,
    manifest: str,
    prompts: dict[str, str] | None = None,
) -> Path:
    """Build a ``.magazine`` archive with the normal package layout.

    The archive is a ZIP containing ``magazine.yaml`` at the root and one
    prompt file per cartridge, mirroring the unpacked directory layout.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("cartridges/", "")
        archive.writestr("magazine.yaml", manifest)
        for rel, text in (prompts or {}).items():
            archive.writestr(rel, text)
    return path


def _write_member_archive(
    path: Path, name: str, data: str, external_attr: int | None = None
) -> Path:
    """Build a ``.magazine`` archive containing a single member with a given
    name and optional Unix ``external_attr`` (used to inject symlink / FIFO
    / other file-type members).

    ``zipfile.writestr`` creates its own ``ZipInfo`` and ignores any external
    ``ZipInfo`` we might hand it, so the custom ``external_attr`` is applied
    to the stored ``ZipInfo`` after the member is written; that attribute is
    what persists in the central directory and is what is read back on
    ``infolist()``.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(name, data)
        if external_attr is not None:
            archive.infolist()[0].external_attr = external_attr
    return path


def test_load_minimal_package(tmp_path):
    _write_package(tmp_path, _valid_manifest(), {"cartridges/001.md": "Prompt one\n"})
    plan = load_package(tmp_path)
    assert plan.schema_version == "1.0"
    assert plan.name == "Demo"
    assert plan.root == tmp_path.resolve()
    assert len(plan.cartridges) == 1
    rec = plan.cartridges[0]
    assert isinstance(rec, CartridgeRecord)
    assert (rec.id, rec.model, rec.prompt_file, rec.prompt) == (
        "first", "research", "cartridges/001.md", "Prompt one\n"
    )
    assert rec.prompt_path == (tmp_path / "cartridges/001.md").resolve()


def test_manifest_order_is_preserved(tmp_path):
    manifest = _valid_manifest('''  - id: second
    model: synthesis
    prompt_file: cartridges/002.md
''')
    _write_package(tmp_path, manifest, {
        "cartridges/001.md": "one", "cartridges/002.md": "two"
    })
    assert [c.id for c in load_package(tmp_path).cartridges] == ["first", "second"]


def test_to_document_matches_strict_runtime_shape(tmp_path):
    _write_package(tmp_path, _valid_manifest(), {"cartridges/001.md": "Prompt"})
    doc = to_document(load_package(tmp_path))
    assert doc == {
        "schema_version": "1.0",
        "cartridges": [{"id": "first", "job_type": "research", "prompt": "Prompt"}],
    }
    assert validate_magazine(doc) is doc


@pytest.mark.parametrize("field", ["tools", "inputs", "artifact_inputs", "expected_output", "reasoning", "master_context"])
def test_extended_cartridge_fields_are_rejected(tmp_path, field):
    values = {
        "tools": "[]", "inputs": "[]", "artifact_inputs": "[]",
        "expected_output": '"text"', "reasoning": "false", "master_context": "false",
    }
    manifest = _valid_manifest().replace(
        "    prompt_file: cartridges/001.md\n",
        f"    prompt_file: cartridges/001.md\n    {field}: {values[field]}\n",
    )
    _write_package(tmp_path, manifest, {"cartridges/001.md": "Prompt"})
    with pytest.raises(MagazineSchemaError, match="unknown fields"):
        load_package(tmp_path)


def test_master_prompt_top_level_is_rejected(tmp_path):
    manifest = _valid_manifest().replace("name: Demo\n", "name: Demo\nmaster_prompt: false\n")
    _write_package(tmp_path, manifest, {"cartridges/001.md": "Prompt"})
    with pytest.raises(MagazineSchemaError, match="unknown fields"):
        load_package(tmp_path)


def test_prompt_path_must_remain_inside_package(tmp_path):
    _write_package(tmp_path, _valid_manifest().replace("cartridges/001.md", "../outside.md"))
    (tmp_path.parent / "outside.md").write_text("outside", encoding="utf-8")
    with pytest.raises(ValueError):
        load_package(tmp_path)


def test_absolute_prompt_path_rejected(tmp_path):
    manifest = _valid_manifest().replace("cartridges/001.md", "/tmp/outside.md")
    _write_package(tmp_path, manifest)
    with pytest.raises(ValueError):
        load_package(tmp_path)


def test_missing_prompt_file_rejected(tmp_path):
    _write_package(tmp_path, _valid_manifest())
    with pytest.raises(FileNotFoundError, match="missing prompt file"):
        load_package(tmp_path)


def test_empty_prompt_rejected(tmp_path):
    _write_package(tmp_path, _valid_manifest(), {"cartridges/001.md": "  \n"})
    with pytest.raises(MagazineSchemaError, match="empty prompt"):
        load_package(tmp_path)


def test_duplicate_cartridge_id_rejected(tmp_path):
    manifest = _valid_manifest('''  - id: first
    model: synthesis
    prompt_file: cartridges/002.md
''')
    _write_package(tmp_path, manifest, {"cartridges/001.md": "one", "cartridges/002.md": "two"})
    with pytest.raises(MagazineSchemaError, match="duplicate cartridge id"):
        load_package(tmp_path)


def test_missing_manifest_rejected(tmp_path):
    with pytest.raises(FileNotFoundError, match="missing package manifest"):
        load_package(tmp_path)


def test_duplicate_yaml_keys_rejected(tmp_path):
    _write_package(tmp_path, '''schema_version: "1.0"
name: A
name: B
cartridges: []
''')
    with pytest.raises(MagazineSchemaError, match="Duplicate key"):
        load_package(tmp_path)


def test_unknown_top_level_field_rejected(tmp_path):
    manifest = _valid_manifest().replace("name: Demo\n", "name: Demo\nfuture: true\n")
    _write_package(tmp_path, manifest, {"cartridges/001.md": "Prompt"})
    with pytest.raises(MagazineSchemaError, match="unknown fields"):
        load_package(tmp_path)


# ----------------------------------------------------------------------
# .magazine archive loading
# ----------------------------------------------------------------------


def test_load_minimal_archive_package(tmp_path):
    archive = _write_package_archive(
        tmp_path / "example.magazine",
        _valid_manifest(),
        {"cartridges/001.md": "Prompt one\n"},
    )
    plan = load_package(archive)
    assert plan.schema_version == "1.0"
    assert plan.name == "Demo"
    assert len(plan.cartridges) == 1
    rec = plan.cartridges[0]
    assert (rec.id, rec.model, rec.prompt_file, rec.prompt) == (
        "first", "research", "cartridges/001.md", "Prompt one\n"
    )
    # The archive's extraction workspace is cleaned up on the success path
    # (see ``test_archive_root_workspace_is_cleaned_up_after_load``), so
    # ``plan.root`` no longer exists after loading returns.  We therefore do
    # not assert ``plan.root.is_dir()`` here; instead we verify the plan's
    # paths are structurally rooted at ``plan.root`` (a path-level invariant
    # that is independent of the temp workspace's lifecycle).
    assert rec.prompt_path.is_relative_to(plan.root)


def test_archive_manifest_order_is_preserved(tmp_path):
    manifest = _valid_manifest('''  - id: second
    model: synthesis
    prompt_file: cartridges/002.md
''')
    archive = _write_package_archive(
        tmp_path / "order.magazine", manifest,
        {"cartridges/001.md": "one", "cartridges/002.md": "two"},
    )
    assert [c.id for c in load_package(archive).cartridges] == ["first", "second"]


def test_archive_root_workspace_is_cleaned_up_after_load(tmp_path):
    archive = _write_package_archive(
        tmp_path / "p.magazine", _valid_manifest(), {"cartridges/001.md": "one"}
    )
    archive_bytes = archive.read_bytes()
    plan = load_package(archive)
    # The temporary extraction workspace must be gone once loading returns,
    # including on the success path.
    assert not plan.root.exists()
    # The archive itself must be left untouched.
    assert archive.read_bytes() == archive_bytes


def test_archive_loads_equivalently_to_unpacked_directory(tmp_path):
    manifest = _valid_manifest('''  - id: second
    model: synthesis
    prompt_file: cartridges/002.md
''')
    prompts = {"cartridges/001.md": "Prompt one\n", "cartridges/002.md": "Prompt two\n"}
    unpacked = tmp_path / "unpacked"
    _write_package(unpacked, manifest, prompts)
    archive = _write_package_archive(tmp_path / "pkg.magazine", manifest, prompts)

    dir_plan = load_package(unpacked)
    archive_plan = load_package(archive)

    assert to_document(dir_plan) == to_document(archive_plan)
    assert [
        (c.id, c.model, c.prompt_file, c.prompt) for c in dir_plan.cartridges
    ] == [
        (c.id, c.model, c.prompt_file, c.prompt) for c in archive_plan.cartridges
    ]


def test_archive_repeat_loads_are_stable_and_leak_free(tmp_path):
    archive = _write_package_archive(
        tmp_path / "repeat.magazine", _valid_manifest(), {"cartridges/001.md": "one"}
    )
    first = load_package(archive)
    second = load_package(archive)
    assert to_document(first) == to_document(second)
    assert not first.root.exists() and not second.root.exists()


def test_package_file_without_archive_extension_rejected(tmp_path):
    (tmp_path / "note.txt").write_text("hello", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="not a directory"):
        load_package(tmp_path / "note.txt")


def test_archive_rejects_absolute_member_posix(tmp_path):
    archive = _write_member_archive(
        tmp_path / "abs.magazine", "/magazine.yaml", "x"
    )
    with pytest.raises(ValueError, match="absolute"):
        load_package(archive)


def test_archive_rejects_absolute_member_windows(tmp_path):
    archive = _write_member_archive(
        tmp_path / "abs.magazine", "C:/Windows/system32/cmd.exe", "x"
    )
    with pytest.raises(ValueError, match="absolute"):
        load_package(archive)


def test_archive_rejects_traversal_member(tmp_path):
    archive = _write_member_archive(
        tmp_path / "trav.magazine", "../../outside.md", "x"
    )
    with pytest.raises(ValueError, match="escapes the package root"):
        load_package(archive)
    archive = _write_member_archive(
        tmp_path / "trav2.magazine", "cartridges/../../../etc/passwd", "x"
    )
    with pytest.raises(ValueError, match="escapes the package root"):
        load_package(archive)


def test_archive_rejects_backslash_traversal(tmp_path):
    archive = _write_member_archive(
        tmp_path / "bs.magazine", "..\\outside.md", "x"
    )
    with pytest.raises(ValueError, match="escapes the package root"):
        load_package(archive)


def test_archive_rejects_symlink_member(tmp_path):
    archive = _write_member_archive(
        tmp_path / "symlink.magazine",
        "cartridges/001.md",
        "x",
        external_attr=(0o120000 | 0o755) << 16,  # S_ISLNK + 755
    )
    with pytest.raises(ValueError, match="symbolic link"):
        load_package(archive)


def test_archive_rejects_fifo_member(tmp_path):
    archive = _write_member_archive(
        tmp_path / "fifo.magazine",
        "cartridges/001.md",
        "x",
        external_attr=(0o10000 | 0o644) << 16,  # S_ISFIFO + 644
    )
    with pytest.raises(ValueError, match="not a regular file"):
        load_package(archive)


def test_archive_must_be_zip(tmp_path):
    (tmp_path / "bad.magazine").write_text("not a zip", encoding="utf-8")
    with pytest.raises(zipfile.BadZipFile):
        load_package(tmp_path / "bad.magazine")


def test_archive_rejects_missing_manifest(tmp_path):
    with zipfile.ZipFile(tmp_path / "p.magazine", "w") as archive:
        archive.writestr("cartridges/", "")
        archive.writestr("cartridges/001.md", "Prompt")
    with pytest.raises(FileNotFoundError, match="missing package manifest"):
        load_package(tmp_path / "p.magazine")


def test_archive_rejects_schema_version(tmp_path):
    manifest = _valid_manifest().replace('"1.0"', '"9.9"')
    archive = _write_package_archive(
        tmp_path / "v.magazine", manifest, {"cartridges/001.md": "Prompt"}
    )
    with pytest.raises(MagazineSchemaError, match="schema_version"):
        load_package(archive)


def test_archive_rejects_empty_prompt(tmp_path):
    archive = _write_package_archive(
        tmp_path / "ep.magazine", _valid_manifest(), {"cartridges/001.md": "  \n"}
    )
    with pytest.raises(MagazineSchemaError, match="empty prompt"):
        load_package(archive)


def test_archive_rejects_prompt_traversal(tmp_path):
    manifest = _valid_manifest().replace("cartridges/001.md", "../outside.md")
    archive = _write_package_archive(tmp_path / "pt.magazine", manifest)
    with pytest.raises(ValueError, match="path escapes root"):
        load_package(archive)
