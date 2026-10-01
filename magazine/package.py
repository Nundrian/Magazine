"""Unpacked Magazine-folder packages: v1 manifest parsing, validation, and the
immutable execution plan.

This module owns the unpacked-folder package format:

  my-project/
  ├── magazine.yaml
  ├── master-prompt.md            (only when ``master_prompt`` is true)
  ├── cartridges/                 (cartridge prompt files)
  │   ├── 001-research.md
  │   └── ...
  └── inputs/                     (package input files)
      └── ...

A ``.magazine`` ZIP will later contain the same structure.  The loader is
format-level only: it parses and strictly validates the v1 manifest
(:func:`load_package`), resolves every package-relative path through the
existing containment primitive (:func:`magazine.paths.resolve_contained_path`),
reads the prompt / master-prompt / input files, and returns the canonical
immutable execution plan (:class:`LoadedPackage` / :class:`CartridgeRecord`).

Every manifest declaration is preserved structurally in the plan -- including
declarations the current runtime cannot yet deliver -- so later runtime work
can implement them without changing the package schema.  Admitting a package to
the existing execution path is a separate gate: :func:`check_executable`
raises :class:`UnsupportedCapabilityError` when a cartridge requires a capability
the current runtime does not support.  A package requiring an unsupported
capability still *loads* successfully; it is rejected only at admission time.

The compatibility projection (:func:`to_document`) derives the existing runtime
document shape (``{"schema_version": "1.0", "cartridges": [...]}``) from the
plan.  The plan is the source of truth; the projection is only the current
runtime's view of it.

Manifest v1 contract (``magazine.yaml``, all fields required, unknown fields
rejected):

  top-level
  ───────
  schema_version: string, must be exactly ``"1.0"``
  name:           non-empty string (display name; not part of execution)
  master_prompt:  bool; ``true`` requires ``master-prompt.md`` at the package
                   root
  cartridges:     non-empty list of cartridge objects, manifest order =
                   execution order

  per cartridge (all required)
  ─────────────────────────
  id:                 non-empty string, unique within the package
  model:              non-empty string; the concrete model declaration.  The
                      current runtime resolves it to a model ID through the
                      existing job-type ``model_map`` (routing) at dispatch.
  reasoning:          bool
  master_context:     bool; when true the master-prompt text is composed into
                      the cartridge prompt
  prompt_file:        non-empty package-relative path to the cartridge prompt
                      file (read verbatim)
  tools:              list of non-empty strings (reserved for a future tooling
                      runtime)
  inputs:             list of non-empty package-relative paths to input files
  artifact_inputs:    list of two-item lists
                      ``[[earlier_cartridge_id, artifact_name]]``; the dependency
                      cartridge must appear earlier in the manifest
  expected_output:    non-empty string (expected artifact declaration)

The current runtime supports exactly these capabilities:

  model             -- concrete model selection (declared ``model`` -> model
                       ID via ``model_map``)
  prompt            -- the cartridge's own prompt text
  master_context    -- master-prompt.md text composed into the prompt

Everything else (``tools``, ``inputs``, ``artifact_inputs``, ``reasoning``)
is declared in the manifest and preserved in the plan but is rejected by
:func:`check_executable` before execution.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from magazine import paths as magazine_paths
from magazine.schema import MagazineSchemaError

__all__ = [
    "CartridgeRecord",
    "LoadedPackage",
    "UnsupportedCapabilityError",
    "SUPPORTED_EXECUTION_CAPABILITIES",
    "load_package",
    "to_document",
    "check_executable",
]

PACKAGE_MANIFEST_NAME = "magazine.yaml"
MASTER_PROMPT_NAME = "master-prompt.md"

SCHEMA_VERSION_V1 = "1.0"

TOP_LEVEL_FIELDS = ("schema_version", "name", "master_prompt", "cartridges")
CARTRIDGE_FIELDS = (
    "id",
    "model",
    "reasoning",
    "master_context",
    "prompt_file",
    "tools",
    "inputs",
    "artifact_inputs",
    "expected_output",
)

# Capabilities the current runtime can deliver.  Everything the manifest schema
# knows about that is not in this set is unsupported and blocked by
# :func:`check_executable`.
SUPPORTED_EXECUTION_CAPABILITIES = frozenset({"model", "prompt", "master_context"})


class UnsupportedCapabilityError(Exception):
    """A cartridge requires a capability the current runtime cannot deliver.

    Carrying :attr:`capability` and :attr:`cartridge_id` makes the gate message
    unambiguous; it is raised before run admission (``prepare_run``) and before
    model dispatch (``dispatch_current_cartridge``), both of which go through
    ``magazine.document.load_document``.
    """

    def __init__(self, capability: str, cartridge_id: str) -> None:
        self.capability = capability
        self.cartridge_id = cartridge_id
        super().__init__(
            f"cartridge {cartridge_id!r} requires unsupported capability "
            f"{capability!r}; cannot admit the package to execution"
        )


@dataclass(frozen=True)
class CartridgeRecord:
    """One cartridge as declared in the manifest, fully materialized."""

    id: str
    model: str
    reasoning: bool
    master_context: bool
    prompt_file: str
    prompt_path: Path
    prompt: str
    inputs: tuple[Path, ...]
    tools: tuple[str, ...]
    artifact_inputs: tuple[tuple[str, str], ...]
    expected_output: str


@dataclass(frozen=True)
class LoadedPackage:
    """Immutable execution plan for one unpacked Magazine folder."""

    schema_version: str
    root: Path
    name: str
    master_prompt: str | None
    cartridges: tuple[CartridgeRecord, ...]


class _UniqueSafeLoader(yaml.SafeLoader):
    """Safe loader that rejects duplicate mapping keys."""

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
        raise MagazineSchemaError(f"Invalid YAML in {PACKAGE_MANIFEST_NAME}: {exc}") from exc


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
    """Validate that a *path* field is a string.

    Empty / whitespace-only / absolute / root-escaping paths are handled by
    :func:`magazine.paths.resolve_contained_path` (which raises ValueError), so
    they surface as ValueError rather than a schema error.
    """
    if not isinstance(value, str):
        raise MagazineSchemaError(
            f"{context}: field {field!r} must be a string, got {type(value).__name__}"
        )
    return value


def _require_path_list(value: Any, context: str, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise MagazineSchemaError(
            f"{context}: field {field!r} must be a list, got {type(value).__name__}"
        )
    return tuple(
        _require_path_string(item, context, f"{field!r}[{i}]")
        for i, item in enumerate(value)
    )


def _require_bool(value: Any, context: str, field: str) -> bool:
    if not isinstance(value, bool):
        raise MagazineSchemaError(
            f"{context}: field {field!r} must be a boolean, got {type(value).__name__}"
        )
    return value


def _require_string_list(value: Any, context: str, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise MagazineSchemaError(
            f"{context}: field {field!r} must be a list, got {type(value).__name__}"
        )
    return tuple(_require_nonempty_string(item, context, f"{field!r}[{i}]") for i, item in enumerate(value))


def _require_artifact_pairs(value: Any, context: str, field: str) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, list):
        raise MagazineSchemaError(
            f"{context}: field {field!r} must be a list, got {type(value).__name__}"
        )
    pairs: list[tuple[str, str]] = []
    for index, entry in enumerate(value):
        if not isinstance(entry, list) or len(entry) != 2:
            raise MagazineSchemaError(
                f"{context}: {field!r}[{index}] must be a two-item list "
                f"[[cartridge_id, artifact_name]], got {entry!r}"
            )
        cid = _require_nonempty_string(entry[0], context, f"{field!r}[{index}][0]")
        name = _require_nonempty_string(entry[1], context, f"{field!r}[{index}][1]")
        pairs.append((cid, name))
    return tuple(pairs)


def load_package(root: str | os.PathLike[str]) -> LoadedPackage:
    """Load and validate an unpacked Magazine folder.

    Parses the v1 manifest (``magazine.yaml``), strictly validates it, resolves
    every package-relative path (master prompt, cartridge prompts, package
    inputs) through :func:`magazine.paths.resolve_contained_path`, and reads the
    declared files into an immutable :class:`LoadedPackage` plan.

    Returns:
        The canonical, immutable execution plan for the package.

    Raises:
        FileNotFoundError: The root is not a directory, the manifest, a
            declared prompt file, an input file, or a required
            ``master-prompt.md`` is missing.
        ValueError: A declared path is absolute, whitespace-only, or escapes
            the package root (propagated from
            :func:`magazine.paths.resolve_contained_path`).
        MagazineSchemaError: The manifest is malformed YAML (including duplicate
            keys), is not an object, has an unknown field, a missing required
            field, a wrong value, a duplicate cartridge id, an invalid schema
            version, an unsupported manifest contradiction
            (``master_context`` without ``master_prompt``), or an artifact
            input that does not reference a known earlier cartridge.
    """
    root_path = Path(root).resolve()
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
            f"{manifest_path.name}: unknown top-level fields: {unknown_top}"
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
    master_prompt_enabled = _require_bool(manifest["master_prompt"], manifest_path.name, "master_prompt")

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

    # ---- structural validation (first pass) ----
    known_ids: set[str] = set()
    validated: list[tuple[Any, ...]] = []
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

        cid = _require_nonempty_string(item["id"], context, "id")
        if cid in known_ids:
            raise MagazineSchemaError(
                f"{manifest_path.name}: duplicate cartridge id: {cid!r}"
            )
        known_ids.add(cid)
        model = _require_nonempty_string(item["model"], context, "model")
        reasoning = _require_bool(item["reasoning"], context, "reasoning")
        master_context = _require_bool(item["master_context"], context, "master_context")
        if master_context and not master_prompt_enabled:
            raise MagazineSchemaError(
                f"{context}: cartridge {cid!r} declares master_context but the "
                f"manifest does not declare master_prompt"
            )
        prompt_file = _require_path_string(item["prompt_file"], context, "prompt_file")
        tools = _require_string_list(item["tools"], context, "tools")
        inputs = _require_path_list(item["inputs"], context, "inputs")
        artifact_inputs = _require_artifact_pairs(
            item["artifact_inputs"], context, "artifact_inputs"
        )
        expected_output = _require_nonempty_string(
            item["expected_output"], context, "expected_output"
        )
        validated.append(
            (
                cid,
                model,
                reasoning,
                master_context,
                prompt_file,
                tools,
                inputs,
                artifact_inputs,
                expected_output,
            )
        )

    # ---- artifact-dependency ordering (second pass, over the full manifest) ----
    for index, (cid, _model, _reasoning, _master_ctx, _prompt_file, _tools, _inputs, artifact_inputs, _out) in enumerate(
        validated
    ):
        earlier_ids = {rec[0] for rec in validated[:index]}
        for dep_index, (dep_id, _dep_name) in enumerate(artifact_inputs):
            if dep_id not in known_ids:
                raise MagazineSchemaError(
                    f"{manifest_path.name}: cartridges[{index}] "
                    f"({cid!r}) has an artifact input for unknown cartridge "
                    f"{dep_id!r}"
                )
            if dep_id not in earlier_ids:
                raise MagazineSchemaError(
                    f"{manifest_path.name}: cartridges[{index}] "
                    f"({cid!r}) 'artifact_inputs' must reference an earlier "
                    f"cartridge in manifest order, got {dep_id!r}"
                )

    # ---- file reads (second pass) ----
    master_text: str | None = None
    if master_prompt_enabled:
        master_path = magazine_paths.resolve_contained_path(str(root_path), MASTER_PROMPT_NAME)
        if not master_path.is_file():
            raise FileNotFoundError(f"missing required master prompt file: {master_path}")
        master_text = master_path.read_text(encoding="utf-8")

    records: list[CartridgeRecord] = []
    for (cid, model, reasoning, master_context, prompt_file, tools, inputs, artifact_inputs, expected_output) in validated:
        prompt_path = magazine_paths.resolve_contained_path(str(root_path), prompt_file)
        if not prompt_path.is_file():
            raise FileNotFoundError(f"missing prompt file for cartridge {cid!r}: {prompt_path}")
        own_prompt = prompt_path.read_text(encoding="utf-8")
        composed = own_prompt if not master_context else own_prompt + "\n\n" + (master_text or "")
        if not composed.strip():
            raise MagazineSchemaError(
                f"{manifest_path.name}: cartridge {cid!r} has an empty composed "
                f"prompt (prompt file {prompt_file!r} and master context are both empty)"
            )
        resolved_inputs: list[Path] = []
        for input_ref in inputs:
            resolved_input = magazine_paths.resolve_contained_path(str(root_path), input_ref)
            if not resolved_input.is_file():
                raise FileNotFoundError(f"missing input file for cartridge {cid!r}: {resolved_input}")
            resolved_inputs.append(resolved_input)
        records.append(
            CartridgeRecord(
                id=cid,
                model=model,
                reasoning=reasoning,
                master_context=master_context,
                prompt_file=prompt_file,
                prompt_path=prompt_path,
                prompt=composed,
                inputs=tuple(resolved_inputs),
                tools=tuple(tools),
                artifact_inputs=artifact_inputs,
                expected_output=expected_output,
            )
        )

    return LoadedPackage(
        schema_version=SCHEMA_VERSION_V1,
        root=root_path,
        name=name,
        master_prompt=master_text,
        cartridges=tuple(records),
    )


def to_document(plan: LoadedPackage) -> dict[str, Any]:
    """Pure compatibility projection of a plan onto the existing runtime document
    shape.

    The runtime document is ``{"schema_version": "1.0", "cartridges": [{"id",
    "job_type", "prompt"}], ...}``; the declared ``model`` is projected to the
    internal ``job_type`` slot, and the composed ``prompt`` is the text the
    runtime would run.  This projection passes
    ``magazine.schema.validate_magazine`` for every plan that loads cleanly.
    """
    return {
        "schema_version": plan.schema_version,
        "cartridges": [
            {"id": cartridge.id, "job_type": cartridge.model, "prompt": cartridge.prompt}
            for cartridge in plan.cartridges
        ],
    }


def _first_unsupported_capability(cartridge: CartridgeRecord) -> str | None:
    if cartridge.tools and "tools" not in SUPPORTED_EXECUTION_CAPABILITIES:
        return "tools"
    if cartridge.inputs and "inputs" not in SUPPORTED_EXECUTION_CAPABILITIES:
        return "inputs"
    if cartridge.artifact_inputs and "artifact_inputs" not in SUPPORTED_EXECUTION_CAPABILITIES:
        return "artifact_inputs"
    if cartridge.reasoning and "reasoning" not in SUPPORTED_EXECUTION_CAPABILITIES:
        return "reasoning"
    return None


def check_executable(plan: LoadedPackage) -> None:
    """Reject a plan for execution if any cartridge requires an unsupported
    capability.

    Supported by the current runtime: ``model`` (concrete model selection via
    the existing model_map path), ``prompt`` (own prompt text), and
    ``master_context`` (master text composition).  Any other declared
    capability (``tools``, ``inputs``, ``artifact_inputs``, ``reasoning``) is
    raised as :class:`UnsupportedCapabilityError`, naming the first offending
    capability and its cartridge id in manifest order.

    The :class:`LoadedPackage` itself is left intact: the gate is separate from
    the loader so the package schema can keep evolving while the runtime catches
    up.
    """
    for cartridge in plan.cartridges:
        capability = _first_unsupported_capability(cartridge)
        if capability is not None:
            raise UnsupportedCapabilityError(capability, cartridge.id)
