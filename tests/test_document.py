import json
from pathlib import Path

import pytest

from magazine.document import LoadedMagazine, load_document
from magazine.package import LoadedPackage
from magazine.schema import MagazineSchemaError


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
