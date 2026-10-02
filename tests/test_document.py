import json
from pathlib import Path

import os
import zipfile

import pytest

from magazine.document import LoadedMagazine, load_document
from magazine.package import LoadedPackage
from magazine.schema import MagazineSchemaError


def _package_directory_to_archive(package_dir: Path, archive_path: Path) -> Path:
    """Zip the contents of an unpacked package directory into a
    ``.magazine`` archive, preserving the directory layout."""
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for root, dirs, files in os.walk(package_dir):
            for f in files:
                p = Path(root) / f
                archive.write(str(p), os.path.relpath(p, package_dir))
            for d in dirs:
                p = Path(root) / d
                archive.writestr(os.path.relpath(p, package_dir) + "/", "")
    return archive_path


def test_load_json_document(tmp_path):
    path = tmp_path / "magazine.json"
    raw = {"schema_version": "1.0", "cartridges": [{"id": "a", "job_type": "research", "prompt": "A"}]}
    path.write_text(json.dumps(raw), encoding="utf-8")
    loaded = load_document(path)
    assert isinstance(loaded, LoadedMagazine)
    assert loaded.document == raw
    assert loaded.plan is None


def test_invalid_json_propagates_schema_error(tmp_path):
    path = tmp_path / "magazine.json"
    path.write_text('{"schema_version":', encoding="utf-8")
    with pytest.raises(MagazineSchemaError):
        load_document(path)


_UNPACKED_LAYOUT = {
    "magazine.yaml": '''schema_version: "1.0"
name: Demo
cartridges:
  - id: a
    model: research
    prompt_file: cartridges/a.md
''',
    "cartridges/a.md": "A prompt",
}


def _build_unpacked_package(package_dir: Path) -> Path:
    for rel, text in _UNPACKED_LAYOUT.items():
        p = package_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return package_dir


def test_load_unpacked_package(tmp_path):
    (tmp_path / "cartridges").mkdir()
    (tmp_path / "cartridges" / "a.md").write_text("A prompt", encoding="utf-8")
    (tmp_path / "magazine.yaml").write_text('''schema_version: "1.0"
name: Demo
cartridges:
  - id: a
    model: research
    prompt_file: cartridges/a.md
''', encoding="utf-8")
    loaded = load_document(tmp_path)
    assert isinstance(loaded.plan, LoadedPackage)
    assert loaded.document["cartridges"] == [{"id": "a", "job_type": "research", "prompt": "A prompt"}]
    assert loaded.plan.cartridges[0].prompt_file == "cartridges/a.md"


def test_load_magazine_archive(tmp_path):
    pkg = tmp_path / "unpacked"
    _build_unpacked_package(pkg)
    archive = _package_directory_to_archive(pkg, tmp_path / "magazine.magazine")
    loaded = load_document(archive)
    assert isinstance(loaded.plan, LoadedPackage)
    assert loaded.document["cartridges"] == [
        {"id": "a", "job_type": "research", "prompt": "A prompt"}
    ]
    assert loaded.plan.cartridges[0].prompt_file == "cartridges/a.md"


def test_unpacked_directory_and_archive_load_equivalently(tmp_path):
    pkg = tmp_path / "unpacked"
    _build_unpacked_package(pkg)
    archive = _package_directory_to_archive(pkg, tmp_path / "magazine.magazine")

    dir_loaded = load_document(pkg)
    archive_loaded = load_document(archive)

    assert dir_loaded.document == archive_loaded.document
    assert dir_loaded.document["schema_version"] == "1.0"
    assert [
        (c.id, c.model, c.prompt_file, c.prompt) for c in dir_loaded.plan.cartridges
    ] == [
        (c.id, c.model, c.prompt_file, c.prompt)
        for c in archive_loaded.plan.cartridges
    ]
    # The archive's extraction workspace is cleaned up once loading returns
    # (see ``magazine.package._load_package_archive``), so ``plan.root`` is
    # not guaranteed to still exist here.  The document/plan equivalence above
    # is the substantive check; we only assert that the archive produced a
    # plan at all (unlike the JSON-document path, where ``plan`` is None).
    assert archive_loaded.plan is not None


def test_package_unknown_future_capability_is_rejected(tmp_path):
    (tmp_path / "cartridges").mkdir()
    (tmp_path / "cartridges" / "a.md").write_text("A", encoding="utf-8")
    (tmp_path / "magazine.yaml").write_text('''schema_version: "1.0"
name: Demo
cartridges:
  - id: a
    model: research
    prompt_file: cartridges/a.md
    inputs: []
''', encoding="utf-8")
    with pytest.raises(MagazineSchemaError, match="unknown fields"):
        load_document(tmp_path)


def test_missing_file_propagates(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_document(tmp_path / "missing.json")
