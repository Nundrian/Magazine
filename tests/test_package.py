from pathlib import Path

import pytest

from magazine.package import CartridgeRecord, load_package, to_document
from magazine.schema import MagazineSchemaError, validate_magazine


def _write_package(tmp_path: Path, manifest: str, prompts: dict[str, str] | None = None):
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
