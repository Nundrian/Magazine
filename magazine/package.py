"""Unpacked Magazine package loading.

An unpacked Magazine package is a directory containing ``magazine.yaml`` and
one prompt file per cartridge.  A ``.magazine`` file is a ZIP archive of the
same unpacked layout (``magazine.yaml`` at the root, prompt files under
``cartridges/``); it is extracted into a temporary workspace before loading and
the workspace is always removed, even when loading fails.  Manifest list order
is execution order.

ZIP member names are validated before anything is written: empty names,
absolute paths (POSIX and Windows), ``..`` traversal, backslash-separated
names, and any member that is not a regular file or directory are rejected.

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
import re
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import yaml

from magazine import paths as magazine_paths
from magazine.schema import MagazineSchemaError

__all__ = ["CartridgeRecord", "LoadedPackage", "load_package", "to_document"]

PACKAGE_MANIFEST_NAME = "magazine.yaml"
SCHEMA_VERSION_V1 = "1.0"
TOP_LEVEL_FIELDS = ("schema_version", "name", "cartridges")
CARTRIDGE_FIELDS = ("id", "model", "prompt_file")

# A ``.magazine`` file is a ZIP archive of the unpacked layout.
MAGAZINE_ARCHIVE_EXTENSION = ".magazine"
MAGAZINE_ARCHIVE_WORKSPACE_PREFIX = "magazine-archive-"

# Raw Unix file-type bits.  Raw octal constants are used (rather than the
# ``stat.S_IS*`` accessors) because those accessors are not consistently
# int constants in every runtime build; the values below are the canonical
# low-type bits returned by ``st_mode & 0o170000``.
_S_IFMT = 0o170000  # file-type bits (mask)
_S_ISREG = 0o100000  # regular file
_S_ISDIR = 0o40000  # directory
_S_ISLNK = 0o120000  # symbolic link
_S_ISFIFO = 0o10000  # FIFO / named pipe


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
    """Load a strict minimal v1 Magazine package.

    *root* is either the directory of an unpacked package, or a file ending
    in ``.magazine`` — a ZIP archive containing the same unpacked layout.
    Directory behaviour is unchanged.

    All prompt paths are package-relative and are resolved with
    :func:`magazine.paths.resolve_contained_path` before being read.  Unknown
    fields are rejected so out-of-scope experimental declarations cannot
    silently become runtime behaviour.

    For a ``.magazine`` file the archive is extracted into a temporary
    workspace (validated members, deterministic cleanup) and loaded through
    the same unpacked path; the returned ``root`` is that temporary
    workspace, so ``prompt_path`` is valid only during loading and only the
    verbatim prompt text is retained afterwards.
    """
    root_path = Path(root).resolve()
    if root_path.is_file() and root_path.suffix == MAGAZINE_ARCHIVE_EXTENSION:
        return _load_package_archive(root_path)
    if not root_path.is_dir():
        raise FileNotFoundError(f"package root is not a directory: {root_path}")
    return _load_unpacked_package(root_path)


def _load_unpacked_package(root_path: Path) -> LoadedPackage:
    """Load the unpacked-directory layout, shared by directories and archives."""
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


def _load_package_archive(archive_path: Path) -> LoadedPackage:
    """Load a ``.magazine`` archive by extracting it into a temporary workspace
    and loading that workspace through the unpacked path.

    The workspace is created with :class:`tempfile.TemporaryDirectory`, so
    cleanup is deterministic: it is always removed when the ``with`` block
    exits, including when member validation, manifest loading, or schema
    validation fails mid-way.
    """
    with tempfile.TemporaryDirectory(prefix=MAGAZINE_ARCHIVE_WORKSPACE_PREFIX) as work_dir:
        _extract_package_archive(archive_path, work_dir)
        return _load_unpacked_package(Path(work_dir))


def _extract_package_archive(archive_path: Path, work_dir: str) -> None:
    """Validate every member of a ``.magazine`` archive, then extract it into
    ``work_dir``.

    Members are validated *before* anything is written so no unsafe member
    can be materialized.  A final containment check is kept as defence in
    depth.
    """
    with zipfile.ZipFile(archive_path, "r") as archive:
        members = list(archive.infolist())
        for info in members:
            _validate_member_info(info)
        for info in members:
            archive.extract(info, work_dir)

    workspace = Path(work_dir).resolve()
    for info in members:
        target = (workspace / info.filename).resolve()
        if not target.is_relative_to(workspace):
            raise ValueError(
                f"archive member escaped the extraction workspace: {info.filename!r}"
            )


def _validate_member_info(info: zipfile.ZipInfo) -> None:
    """Validate a single archive member, raising :class:`ValueError` if it is
    not safe to materialize.

    Rejected members (in this order):

    * empty or whitespace-only names;
    * absolute paths (POSIX ``/`` or Windows drive/UNC);
    * ``..`` path components (path traversal);
    * backslash-separated names (ZIP archives are slash-based; a backslash
      would otherwise materialize as a literal character in the file name);
    * members whose type bits indicate a dangerous or non-data file type
      (symlinks, FIFOs, block/character devices, mount points, ...).  Plain
      data members — regular files, directories, and untyped members (the
      common case for ZIP entries created without explicit Unix type bits) —
      are accepted; a name ending in ``/`` is also accepted as a directory.
    """
    name = info.filename
    if not name or not name.strip():
        raise ValueError(f"archive member path must not be empty: {name!r}")
    normalized = name.replace("\\", "/")
    if (
        PurePosixPath(normalized).is_absolute()
        or PureWindowsPath(normalized).is_absolute()
        or re.match(r"^[A-Za-z]:", normalized)
    ):
        raise ValueError(f"archive member path is absolute: {name!r}")
    if ".." in PurePosixPath(normalized).parts:
        raise ValueError(
            f"archive member path escapes the package root: {name!r}"
        )
    if name != normalized:
        raise ValueError(f"archive member name may not contain backslashes: {name!r}")

    file_type = (info.external_attr >> 16) & _S_IFMT
    if file_type == _S_ISLNK:
        raise ValueError(f"archive member is a symbolic link: {name!r}")
    # Directory by Unix type bit, or by the conventional trailing-slash ZIP
    # directory-entry convention.
    is_directory = name.endswith("/") or file_type == _S_ISDIR
    if is_directory or file_type in (0, _S_ISREG):
        return
    raise ValueError(
        f"archive member is not a regular file or directory: {name!r}"
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
