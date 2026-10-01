"""Focused tests for the unpacked Magazine-folder loading slice.

Covers ``magazine.package`` (v1 manifest parsing, strict validation, path
resolution, prompt composition, immutable plan, capability gate, and the
runtime projection) and the directory branch of ``magazine.document.load_document``.
"""

from __future__ import annotations

import dataclasses
import json

from pathlib import Path
from typing import Any

import yaml
import pytest

from magazine import document
from magazine import package as pkg
from magazine.package import (
    CartridgeRecord,
    LoadedPackage,
    UnsupportedCapabilityError,
)
from magazine.schema import MagazineSchemaError, validate_magazine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BASE_MANIFEST: dict[str, Any] = {
    "schema_version": "1.0",
    "name": "smoke-project",
    "master_prompt": False,
    "cartridges": [
        {
            "id": "c1",
            "model": "Bonsai 2 27B",
            "reasoning": False,
            "master_context": False,
            "prompt_file": "cartridges/001-c1.md",
            "tools": [],
            "inputs": [],
            "artifact_inputs": [],
            "expected_output": "output1",
        },
        {
            "id": "c2",
            "model": "llama-3.1-8b",
            "reasoning": False,
            "master_context": False,
            "prompt_file": "cartridges/002-c2.md",
            "tools": [],
            "inputs": [],
            "artifact_inputs": [],
            "expected_output": "output2",
        },
    ],
}

PROMPTS: dict[str, str] = {
    "cartridges/001-c1.md": "C1 own.\n",
    "cartridges/002-c2.md": "C2 own.\n",
}


def _write_yaml(path: Path, obj: object) -> None:
    path.write_text(yaml.safe_dump(obj), encoding="utf-8")


def _cartridge(id_: str, model: str = "M", **overrides: Any) -> dict[str, Any]:
    return {
        "id": id_,
        "model": model,
        "reasoning": False,
        "master_context": False,
        "prompt_file": "cartridges/001-c1.md",
        "tools": [],
        "inputs": [],
        "artifact_inputs": [],
        "expected_output": "output1",
        **overrides,
    }


def _make_package(
    tmp_path: Path,
    manifest: dict[str, Any] | None = None,
    master_text: str = "",
) -> Path:
    """Create a valid unpacked package under *tmp_path* and return the root.

    Prompt files are written to match every declared ``prompt_file`` (default
    content from PROMPTS, or ``""`` if none declared). A ``master-prompt.md``
    file is written only when the manifest declares ``master_prompt: True``.
    """
    manifest = DEFAULT if manifest is None else manifest
    root = tmp_path
    (root / "cartridges").mkdir(exist_ok=True)
    for rel, text in PROMPTS.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    for cartridge in manifest["cartridges"]:
        p = root / cartridge["prompt_file"]
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            p.write_text(PROMPTS.get(str(cartridge["prompt_file"]), ""), encoding="utf-8")
    if manifest.get("master_prompt"):
        (root / "master-prompt.md").write_text(master_text, encoding="utf-8")
    _write_yaml(root / "magazine.yaml", manifest)
    return root


DEFAULT = dict(BASE_MANIFEST)


@pytest.fixture
def valid_package(tmp_path) -> Path:
    """A minimal, fully valid package with two clean cartridges."""
    return _make_package(tmp_path)


# ---------------------------------------------------------------------------
# Loading (magazine.package.load_package)
# ---------------------------------------------------------------------------

def test_load_package_minimal_valid(valid_package):
    package = pkg.load_package(valid_package)
    assert isinstance(package, LoadedPackage)
    assert package.schema_version == "1.0"
    assert package.name == "smoke-project"
    assert package.master_prompt is None
    assert len(package.cartridges) == 2
    assert [c.id for c in package.cartridges] == ["c1", "c2"]
    c0 = package.cartridges[0]
    assert c0.model == "Bonsai 2 27B"
    assert c0.reasoning is False
    assert c0.master_context is False
    assert c0.prompt_file == "cartridges/001-c1.md"
    assert c0.prompt == "C1 own.\n"
    assert c0.tools == ()
    assert c0.inputs == ()
    assert c0.artifact_inputs == ()
    assert c0.expected_output == "output1"


def test_load_package_cartridges_tuple_of_records(valid_package):
    package = pkg.load_package(valid_package)
    assert isinstance(package.cartridges, tuple)
    assert all(isinstance(c, CartridgeRecord) for c in package.cartridges)
    assert [c.id for c in package.cartridges] == ["c1", "c2"]


def test_load_package_root_is_resolved_absolute(valid_package):
    package = pkg.load_package(valid_package)
    assert package.root.is_absolute()
    assert (package.root / "magazine.yaml").is_file()


def test_load_package_root_is_input_path(valid_package):
    package = pkg.load_package(valid_package)
    # the root is the dir we passed (resolved)
    assert str(package.root) == str(Path(valid_package).resolve())


# ---------------------------------------------------------------------------
# Immutability (frozen dataclasses)
# ---------------------------------------------------------------------------

def test_loaded_package_is_frozen_dataclass(valid_package):
    package = pkg.load_package(valid_package)
    assert dataclasses.is_dataclass(package)
    assert {f.name for f in dataclasses.fields(package)} == {
        "schema_version", "root", "name", "master_prompt", "cartridges"
    }


def test_loaded_package_attribute_assignment_forbidden(valid_package):
    package = pkg.load_package(valid_package)
    with pytest.raises(dataclasses.FrozenInstanceError):
        package.name = "other"
    with pytest.raises(dataclasses.FrozenInstanceError):
        package.schema_version = "9.9"
    with pytest.raises(dataclasses.FrozenInstanceError):
        package.master_prompt = "nope"
    with pytest.raises(dataclasses.FrozenInstanceError):
        package.cartridges = None


def test_loaded_package_cartridges_tuple_immutable(valid_package):
    package = pkg.load_package(valid_package)
    # the tuple itself is immutable
    with pytest.raises(TypeError):
        package.cartridges[0] = None
    # tuples expose no mutation methods
    with pytest.raises(AttributeError):
        package.cartridges.append(None)  # type: ignore[attr-defined]


def test_loaded_package_cartridge_record_immutable(valid_package):
    package = pkg.load_package(valid_package)
    record = package.cartridges[0]
    assert dataclasses.is_dataclass(record)
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.model = "hacked"
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.prompt = "hacked"


# ---------------------------------------------------------------------------
# Prompt composition + master-prompt handling
# ---------------------------------------------------------------------------

def test_composed_prompt_master_context_true(tmp_path):
    root = _make_package(
        tmp_path,
        manifest={**DEFAULT, "master_prompt": True, "cartridges": [
            {**_cartridge("c1", "Bonsai 2 27B"), "master_context": True,
             "prompt_file": "cartridges/001-c1.md"}
        ]},
        master_text="Master: stay on topic.\n",
    )
    package = pkg.load_package(root)
    assert package.master_prompt == "Master: stay on topic.\n"
    c0 = package.cartridges[0]
    assert c0.master_context is True
    assert c0.prompt == "C1 own.\n\n\nMaster: stay on topic.\n"


def test_composed_prompt_master_context_false(valid_package):
    package = pkg.load_package(valid_package)
    assert package.cartridges[0].master_context is False
    assert package.cartridges[0].prompt == "C1 own.\n"
    assert "Master" not in package.cartridges[0].prompt


def test_master_prompt_false_no_master_file_needed(valid_package):
    package = pkg.load_package(valid_package)
    assert not (valid_package / "master-prompt.md").exists()
    assert package.master_prompt is None


def test_master_prompt_true_master_file_missing_raises(tmp_path):
    root = _make_package(
        tmp_path, manifest={**DEFAULT, "master_prompt": True}
    )
    (root / "master-prompt.md").unlink(missing_ok=True)
    with pytest.raises(FileNotFoundError):
        pkg.load_package(root)


def test_master_context_without_master_prompt_raises(valid_package):
    manifest = {**DEFAULT, "master_prompt": False, "cartridges": [
        {**_cartridge("c1", "Bonsai 2 27B"), "master_context": True}
    ]}
    valid_package.write_text  # no-op
    _write_yaml(valid_package / "magazine.yaml", manifest)
    with pytest.raises(MagazineSchemaError, match="master_context"):
        pkg.load_package(valid_package)


def test_composed_prompt_nonempty_required(tmp_path):
    (tmp_path / "cartridges").mkdir()
    (tmp_path / "master-prompt.md").write_text("", encoding="utf-8")
    (tmp_path / "cartridges" / "001-c1.md").write_text("", encoding="utf-8")
    manifest = {
        **DEFAULT,
        "master_prompt": True,
        "cartridges": [
            {**_cartridge("c1", "Bonsai 2 27B"), "master_context": True}
        ],
    }
    _write_yaml(tmp_path / "magazine.yaml", manifest)
    with pytest.raises(MagazineSchemaError, match="empty"):
        pkg.load_package(tmp_path)


# ---------------------------------------------------------------------------
# Strict manifest validation
# ---------------------------------------------------------------------------

def test_schema_version_must_be_exactly_1_0(valid_package):
    _write_yaml(valid_package / "magazine.yaml", {**DEFAULT, "schema_version": "2.0"})
    with pytest.raises(MagazineSchemaError, match="schema_version"):
        pkg.load_package(valid_package)


def test_schema_version_must_be_string(valid_package):
    _write_yaml(valid_package / "magazine.yaml", {**DEFAULT, "schema_version": 1})
    with pytest.raises(MagazineSchemaError, match="schema_version"):
        pkg.load_package(valid_package)


def test_unknown_top_level_field_rejected(valid_package):
    _write_yaml(valid_package / "magazine.yaml", {**DEFAULT, "extra_key": "oops"})
    with pytest.raises(MagazineSchemaError, match="extra_key"):
        pkg.load_package(valid_package)


def test_unknown_cartridge_field_rejected(valid_package):
    manifest = {**DEFAULT, "cartridges": [
        {**DEFAULT["cartridges"][0], "extra": "oops"}
    ]}
    _write_yaml(valid_package / "magazine.yaml", manifest)
    with pytest.raises(MagazineSchemaError, match="extra"):
        pkg.load_package(valid_package)


def test_missing_required_field_rejected(valid_package):
    manifest = {**DEFAULT, "cartridges": [dict(DEFAULT["cartridges"][0])]}
    del manifest["cartridges"][0]["expected_output"]
    _write_yaml(valid_package / "magazine.yaml", manifest)
    with pytest.raises(MagazineSchemaError):
        pkg.load_package(valid_package)


def test_missing_top_level_field_rejected(valid_package):
    manifest = {**DEFAULT}
    del manifest["name"]
    _write_yaml(valid_package / "magazine.yaml", manifest)
    with pytest.raises(MagazineSchemaError, match="name"):
        pkg.load_package(valid_package)


def test_duplicate_cartridge_id_rejected(valid_package):
    manifest = {**DEFAULT, "cartridges": [
        DEFAULT["cartridges"][0],
        dict(DEFAULT["cartridges"][1]),
    ]}
    manifest["cartridges"][1]["id"] = "c1"
    _write_yaml(valid_package / "magazine.yaml", manifest)
    with pytest.raises(MagazineSchemaError, match="duplicate cartridge id"):
        pkg.load_package(valid_package)


def test_yaml_duplicate_key_rejected(valid_package):
    text = (
        "schema_version: 1.0\n"
        "name: a\n"
        "name: b\n"
        "master_prompt: false\n"
        "cartridges: []\n"
    )
    (valid_package / "magazine.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(MagazineSchemaError, match="Duplicate key"):
        pkg.load_package(valid_package)


def test_malformed_yaml_rejected(valid_package):
    (valid_package / "magazine.yaml").write_text("this is not yaml: [ {", encoding="utf-8")
    with pytest.raises(MagazineSchemaError):
        pkg.load_package(valid_package)


def test_manifest_not_an_object_rejected(valid_package):
    (valid_package / "magazine.yaml").write_text("- item\n- other\n", encoding="utf-8")
    with pytest.raises(MagazineSchemaError, match="mapping"):
        pkg.load_package(valid_package)


def test_manifest_missing_raises(valid_package):
    (valid_package / "magazine.yaml").unlink()
    with pytest.raises(FileNotFoundError, match="magazine.yaml"):
        pkg.load_package(valid_package)


def test_root_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        pkg.load_package(tmp_path / "does-not-exist")


# ---------------------------------------------------------------------------
# Path resolution + file reads
# ---------------------------------------------------------------------------

def test_missing_prompt_file_raises(valid_package):
    (valid_package / "cartridges" / "002-c2.md").unlink()
    with pytest.raises(FileNotFoundError):
        pkg.load_package(valid_package)


def test_missing_input_file_raises(valid_package):
    manifest = {**DEFAULT, "cartridges": [
        {**DEFAULT["cartridges"][0], "inputs": ["inputs/missing.csv"]}
    ]}
    (valid_package / "inputs").mkdir(exist_ok=True)
    _write_yaml(valid_package / "magazine.yaml", manifest)
    with pytest.raises(FileNotFoundError):
        pkg.load_package(valid_package)


def test_input_files_resolved_to_absolute_paths(valid_package):
    manifest = {**DEFAULT, "cartridges": [
        {**DEFAULT["cartridges"][0], "inputs": ["inputs/data.csv"]}
    ]}
    (valid_package / "inputs").mkdir(exist_ok=True)
    (valid_package / "inputs" / "data.csv").write_text("x,y\n", encoding="utf-8")
    _write_yaml(valid_package / "magazine.yaml", manifest)
    package = pkg.load_package(valid_package)
    input_path = package.cartridges[0].inputs[0]
    assert input_path.is_absolute()
    assert input_path == (valid_package / "inputs" / "data.csv").resolve()


def test_escape_path_rejected(valid_package):
    manifest = {**DEFAULT, "cartridges": [
        {**DEFAULT["cartridges"][0], "prompt_file": "../outside.md"}
    ]}
    _write_yaml(valid_package / "magazine.yaml", manifest)
    with pytest.raises(ValueError):
        pkg.load_package(valid_package)


def test_absolute_path_rejected(valid_package):
    manifest = {**DEFAULT, "cartridges": [
        {**DEFAULT["cartridges"][0], "prompt_file": "/tmp/elsewhere/abs.md"}
    ]}
    _write_yaml(valid_package / "magazine.yaml", manifest)
    with pytest.raises(ValueError):
        pkg.load_package(valid_package)


def test_whitespace_path_rejected(valid_package):
    manifest = {**DEFAULT, "cartridges": [
        {**DEFAULT["cartridges"][0], "prompt_file": "   "}
    ]}
    _write_yaml(valid_package / "magazine.yaml", manifest)
    with pytest.raises(ValueError):
        pkg.load_package(valid_package)


# ---------------------------------------------------------------------------
# Artifact-input dependency ordering
# ---------------------------------------------------------------------------

def test_artifact_input_referencing_earlier_cartridge_ok(valid_package):
    manifest = {**DEFAULT, "cartridges": [
        {**DEFAULT["cartridges"][0]},
        {**DEFAULT["cartridges"][1], "artifact_inputs": [["c1", "report"]]},
    ]}
    _write_yaml(valid_package / "magazine.yaml", manifest)
    package = pkg.load_package(valid_package)
    assert package.cartridges[1].artifact_inputs == (("c1", "report"),)


def test_artifact_input_unknown_cartridge_rejected(valid_package):
    manifest = {**DEFAULT, "cartridges": [
        DEFAULT["cartridges"][0],
        dict(DEFAULT["cartridges"][1]),
    ]}
    manifest["cartridges"][1]["artifact_inputs"] = [["does-not-exist", "x"]]
    _write_yaml(valid_package / "magazine.yaml", manifest)
    with pytest.raises(MagazineSchemaError, match="unknown cartridge"):
        pkg.load_package(valid_package)


def test_artifact_input_later_cartridge_rejected(valid_package):
    manifest = {**DEFAULT, "cartridges": [
        {**DEFAULT["cartridges"][0], "artifact_inputs": [["c2", "x"]]},
        DEFAULT["cartridges"][1],
    ]}
    _write_yaml(valid_package / "magazine.yaml", manifest)
    with pytest.raises(MagazineSchemaError, match="earlier cartridge"):
        pkg.load_package(valid_package)


def test_artifact_inputs_must_be_pairs(valid_package):
    manifest = {**DEFAULT, "cartridges": [
        {**DEFAULT["cartridges"][0], "artifact_inputs": ["just-a-string"]}
    ]}
    _write_yaml(valid_package / "magazine.yaml", manifest)
    with pytest.raises(MagazineSchemaError, match="two-item list"):
        pkg.load_package(valid_package)


# ---------------------------------------------------------------------------
# Type strictness
# ---------------------------------------------------------------------------

def test_bool_fields_must_be_bool(valid_package):
    manifest = {**DEFAULT, "cartridges": [
        {**DEFAULT["cartridges"][0], "reasoning": 1}
    ]}
    _write_yaml(valid_package / "magazine.yaml", manifest)
    with pytest.raises(MagazineSchemaError):
        pkg.load_package(valid_package)


def test_non_string_field_rejected(valid_package):
    manifest = {**DEFAULT, "cartridges": [
        {**DEFAULT["cartridges"][0], "expected_output": 42}
    ]}
    _write_yaml(valid_package / "magazine.yaml", manifest)
    with pytest.raises(MagazineSchemaError):
        pkg.load_package(valid_package)


# ---------------------------------------------------------------------------
# Capability gate (magazine.package.check_executable)
# ---------------------------------------------------------------------------

def test_check_executable_blocks_each_unsupported_capability(tmp_path):
    for cap in ("tools", "inputs", "artifact_inputs", "reasoning"):
        root = tmp_path / f"bad_{cap}"
        root.mkdir()
        (root / "cartridges").mkdir()
        (root / "cartridges" / "p.md").write_text("p.", encoding="utf-8")
        manifest = {
            "schema_version": "1.0",
            "name": "p",
            "master_prompt": False,
            "cartridges": [_cartridge("c1", "M", prompt_file="cartridges/p.md",
                                        expected_output="o")],
        }
        manifest["cartridges"][0]["tools"] = ["t"] if cap == "tools" else []
        manifest["cartridges"][0]["inputs"] = ["x"] if cap == "inputs" else []
        manifest["cartridges"][0]["artifact_inputs"] = []
        manifest["cartridges"][0]["reasoning"] = cap == "reasoning"
        if cap == "inputs":
            (root / "x").write_text("in.", encoding="utf-8")
        if cap == "artifact_inputs":
            manifest["cartridges"].insert(0, _cartridge("c0", "M0",
                                                        prompt_file="cartridges/p.md",
                                                        expected_output="o0"))
            manifest["cartridges"][1]["artifact_inputs"] = [["c0", "a"]]
        _write_yaml(root / "magazine.yaml", manifest)
        plan = pkg.load_package(root)
        with pytest.raises(UnsupportedCapabilityError) as excinfo:
            pkg.check_executable(plan)
        assert excinfo.value.capability == cap
        assert excinfo.value.cartridge_id == "c1"


def test_check_executable_clean_plan_no_raise(valid_package):
    package = pkg.load_package(valid_package)
    pkg.check_executable(package)
    c0 = package.cartridges[0]
    assert c0.tools == () and c0.inputs == () and c0.artifact_inputs == ()
    assert c0.reasoning is False


def test_check_executable_reports_first_offending_cartridge(valid_package):
    manifest = {**DEFAULT, "cartridges": [
        {**DEFAULT["cartridges"][0], "reasoning": True},
        {**DEFAULT["cartridges"][1], "tools": ["t"]},
    ]}
    _write_yaml(valid_package / "magazine.yaml", manifest)
    plan = pkg.load_package(valid_package)
    with pytest.raises(UnsupportedCapabilityError) as excinfo:
        pkg.check_executable(plan)
    assert excinfo.value.cartridge_id == "c1"
    assert excinfo.value.capability == "reasoning"


def test_unsupported_capability_error_attributes_and_message(valid_package):
    manifest = {**DEFAULT, "cartridges": [
        {**DEFAULT["cartridges"][0], "tools": ["bash"]}
    ]}
    _write_yaml(valid_package / "magazine.yaml", manifest)
    plan = pkg.load_package(valid_package)
    with pytest.raises(UnsupportedCapabilityError) as excinfo:
        pkg.check_executable(plan)
    err = excinfo.value
    assert err.capability == "tools"
    assert err.cartridge_id == "c1"
    assert "tools" in str(err)
    assert "c1" in str(err)
    assert isinstance(err, Exception)


# ---------------------------------------------------------------------------
# Compatibility projection (magazine.package.to_document)
# ---------------------------------------------------------------------------

def test_to_document_projection_matches_runtime_shape(valid_package):
    package = pkg.load_package(valid_package)
    doc = pkg.to_document(package)
    assert isinstance(doc, dict)
    assert doc["schema_version"] == "1.0"
    assert isinstance(doc["cartridges"], list)
    for c in doc["cartridges"]:
        assert set(c) == {"id", "job_type", "prompt"}
    assert doc["cartridges"][0]["job_type"] == package.cartridges[0].model
    assert doc["cartridges"][0]["prompt"] == package.cartridges[0].prompt
    assert doc["cartridges"][1]["job_type"] == package.cartridges[1].model
    assert doc["cartridges"][1]["prompt"] == package.cartridges[1].prompt


def test_to_document_passes_existing_schema_validation(valid_package):
    package = pkg.load_package(valid_package)
    doc = pkg.to_document(package)
    validate_magazine(doc)


def test_to_document_preserves_order_and_count(valid_package):
    package = pkg.load_package(valid_package)
    doc = pkg.to_document(package)
    assert [c["id"] for c in doc["cartridges"]] == [c.id for c in package.cartridges]
    assert len(doc["cartridges"]) == len(package.cartridges)


# ---------------------------------------------------------------------------
# document.load_document directory branch
# ---------------------------------------------------------------------------

def test_load_document_dir_returns_validated_doc(valid_package):
    doc = document.load_document(valid_package)
    assert isinstance(doc, dict)
    assert doc["schema_version"] == "1.0"
    assert [c["id"] for c in doc["cartridges"]] == ["c1", "c2"]
    assert doc["cartridges"][0]["job_type"] == "Bonsai 2 27B"
    assert doc["cartridges"][0]["prompt"] == "C1 own.\n"


def test_load_document_dir_with_unsupported_capability_raises(valid_package):
    manifest = {**DEFAULT, "cartridges": [
        {**DEFAULT["cartridges"][0], "tools": ["bash"]}
    ]}
    _write_yaml(valid_package / "magazine.yaml", manifest)
    with pytest.raises(UnsupportedCapabilityError, match="tools"):
        document.load_document(valid_package)


def test_load_document_dir_missing_prompt_raises_file_not_found(valid_package):
    (valid_package / "cartridges" / "002-c2.md").unlink()
    with pytest.raises(FileNotFoundError):
        document.load_document(valid_package)


def test_load_document_dir_escape_path_raises_value_error(valid_package):
    manifest = {**DEFAULT, "cartridges": [
        {**DEFAULT["cartridges"][0], "prompt_file": "../outside.md"}
    ]}
    _write_yaml(valid_package / "magazine.yaml", manifest)
    with pytest.raises(ValueError):
        document.load_document(valid_package)


def test_load_document_json_path_still_works(tmp_path):
    (tmp_path / "magazine.json").write_text(
        json.dumps({
            "schema_version": "1.0",
            "cartridges": [{"id": "c1", "job_type": "M", "prompt": "hi\n"}],
        }),
        encoding="utf-8",
    )
    doc = document.load_document(tmp_path / "magazine.json")
    assert doc["cartridges"][0]["job_type"] == "M"


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
