"""Minimal unpacked Magazine package loading.

An unpacked Magazine package is a directory containing ``magazine.yaml`` and
one prompt file per cartridge.  Manifest list order is execution order.

The v1 active schema deliberately contains only orchestration data Magazine
needs to run a cartridge:

Top level::

    schema_version: "1.0"
    name: "Example workflow"
    cartridges: [...]

Per cartridge::

    id: research-1
    model: research
    prompt_file: cartridges/001-research.md

``model`` is the cartridge's model-routing key.  ``prompt_file`` is resolved
through the package containment primitive and read verbatim.  Richer concepts
such as tool declarations, arbitrary resource inputs, artefact dependencies,
master-context composition, reasoning flags and expected-output verification
are intentionally not part of the active Magazine v1 package contract.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from magazine import paths as magazine_paths
from magazine.schema import MagazineSchemaError

__all__ = ["CartridgeRecord", "LoadedPackage", "load_package", "to_document"]

PACKAGE_MANIFEST_NAME = "magazine.yaml"
SCHEMA_VERSION_V1 = "1.0"
TOP_LEVEL_FIELDS = ("schema_version", "name", "cartridges")
CARTRIDGE_FIELDS = ("id", "model", "prompt_file")


@dataclass(frozen=True)
class CartridgeRecord:
    """One fully materialised cartridge from an unpacked package."""

    id: str
    model: str
    prompt_file: str
    prompt_path: Path
    prompt: str


@dataclass(frozen=True)
class LoadedPackage:
    """Immutable execution plan for one unpacked Magazine package."""

    schema_version: str
    root: Path
    name: str
    cartridges: tuple[CartridgeRecord, ...]


class _UniqueSafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys."""

    def construct_mapping(self, node, deep: bool = False):
        seen: set[str] = set()
        mapping: dict[str, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str):
                raise MagazineSchemaError(
                    f"YAML mapping keys must be strings, got {type(key).__name__}"
                )
            if key in seen:
                raise MagazineSchemaError(f"Duplicate key: {key!r}")
            seen.add(key)
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


def _parse_yaml(text: str) -> Any:
    try:
        return yaml.load(text, Loader=_UniqueSafeLoader)
    except yaml.YAMLError as exc:
        raise MagazineSchemaError(
            f"Invalid YAML in {PACKAGE_MANIFEST_NAME}: {exc}"
        ) from exc


def _require_nonempty_string(value: Any, context: str, field: str) -> str:
    if not isinstance(value, str):
        raise MagazineSchemaError(
            f"{context}: field {field!r} must be a string, got {type(value).__name__}"
        )
    if not value.strip():
        raise MagazineSchemaError(
            f"{context}: field {field!r} must not be empty or whitespace-only"
        )
    return value


def _require_path_string(value: Any, context: str, field: str) -> str:
    if not isinstance(value, str):
        raise MagazineSchemaError(
            f"{context}: field {field!r} must be a string, got {type(value).__name__}"
        )
    return value


def load_package(root: str | os.PathLike[str]) -> LoadedPackage:
    """Load a strict minimal v1 unpacked Magazine package.

    All prompt paths are package-relative and are resolved with
    :func:`magazine.paths.resolve_contained_path` before being read.  Unknown
    fields are rejected so out-of-scope experimental declarations cannot
    silently become runtime behaviour.
    """
    root_path = Path(root).resolve()
    if not root_path.is_dir():
        raise FileNotFoundError(f"package root is not a directory: {root_path}")

    manifest_path = root_path / PACKAGE_MANIFEST_NAME
    if not manifest_path.is_file():
        raise FileNotFoundError(f"missing package manifest: {manifest_path}")

    manifest = _parse_yaml(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise MagazineSchemaError(
            f"{manifest_path.name} must be a YAML mapping (object), got "
            f"{type(manifest).__name__}"
        )

    unknown_top = sorted(set(manifest) - set(TOP_LEVEL_FIELDS))
    if unknown_top:
        raise MagazineSchemaError(
            f"{manifest_path.name}: unknown fields: {unknown_top}"
        )
    missing_top = sorted(set(TOP_LEVEL_FIELDS) - set(manifest))
    if missing_top:
        raise MagazineSchemaError(
            f"{manifest_path.name}: missing required fields: {missing_top}"
        )

    schema_version = manifest["schema_version"]
    if not isinstance(schema_version, str):
        raise MagazineSchemaError(
            f"{manifest_path.name}: field 'schema_version' must be a string, "
            f"got {type(schema_version).__name__}"
        )
    if schema_version != SCHEMA_VERSION_V1:
        raise MagazineSchemaError(
            f"{manifest_path.name}: field 'schema_version' must be "
            f"{SCHEMA_VERSION_V1!r}, got {schema_version!r}"
        )

    name = _require_nonempty_string(manifest["name"], manifest_path.name, "name")
    cartridges = manifest["cartridges"]
    if not isinstance(cartridges, list):
        raise MagazineSchemaError(
            f"{manifest_path.name}: field 'cartridges' must be a list, got "
            f"{type(cartridges).__name__}"
        )
    if not cartridges:
        raise MagazineSchemaError(
            f"{manifest_path.name}: field 'cartridges' must not be empty"
        )

    records: list[CartridgeRecord] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(cartridges):
        context = f"{manifest_path.name}: cartridges[{index}]"
        if not isinstance(item, dict):
            raise MagazineSchemaError(
                f"{context}: cartridge must be a YAML mapping (object), got "
                f"{type(item).__name__}"
            )

        unknown = sorted(set(item) - set(CARTRIDGE_FIELDS))
        if unknown:
            raise MagazineSchemaError(f"{context}: unknown fields: {unknown}")
        missing = sorted(set(CARTRIDGE_FIELDS) - set(item))
        if missing:
            raise MagazineSchemaError(f"{context}: missing required fields: {missing}")

        cartridge_id = _require_nonempty_string(item["id"], context, "id")
        if cartridge_id in seen_ids:
            raise MagazineSchemaError(
                f"{manifest_path.name}: duplicate cartridge id: {cartridge_id!r}"
            )
        seen_ids.add(cartridge_id)

        model = _require_nonempty_string(item["model"], context, "model")
        prompt_file = _require_path_string(item["prompt_file"], context, "prompt_file")
        prompt_path = magazine_paths.resolve_contained_path(str(root_path), prompt_file)
        if not prompt_path.is_file():
            raise FileNotFoundError(
                f"missing prompt file for cartridge {cartridge_id!r}: {prompt_path}"
            )
        prompt = prompt_path.read_text(encoding="utf-8")
        if not prompt.strip():
            raise MagazineSchemaError(
                f"{manifest_path.name}: cartridge {cartridge_id!r} has an empty prompt "
                f"file {prompt_file!r}"
            )

        records.append(
            CartridgeRecord(
                id=cartridge_id,
                model=model,
                prompt_file=prompt_file,
                prompt_path=prompt_path,
                prompt=prompt,
            )
        )

    return LoadedPackage(
        schema_version=SCHEMA_VERSION_V1,
        root=root_path,
        name=name,
        cartridges=tuple(records),
    )


def to_document(plan: LoadedPackage) -> dict[str, Any]:
    """Project a package plan onto the existing strict runtime document shape."""
    return {
        "schema_version": plan.schema_version,
        "cartridges": [
            {"id": cartridge.id, "job_type": cartridge.model, "prompt": cartridge.prompt}
            for cartridge in plan.cartridges
        ],
    }
