"""Single Magazine document-loading boundary.

All Magazine execution entry points load a Magazine document through this
one function, so that source-format changes (the current JSON file; unpacked
Magazine folders; ``.magazine`` ZIP packages using the same internal structure)
only change *this* module and never the downstream orchestration or runtime.

Supported sources:

* a Magazine JSON document -- read as UTF-8, parsed by the strict JSON loader
  (:func:`magazine.schema.load_magazine_json`), and schema-validated
  (:func:`magazine.schema.validate_magazine`); the existing JSON behaviour is
  unchanged;
* an unpacked Magazine folder (a directory containing ``magazine.yaml``) --
  loaded via :mod:`magazine.package` (``load_package``), gated by
  ``check_executable`` (unsupported capability declarations are rejected here,
  *before* run admission or model dispatch), projected via ``to_document``
  onto the existing runtime shape, and re-validated by the existing schema.

The function reads the source, delegates parsing/validation to the existing
modules, and hands back the canonical validated document:

  {
      "schema_version": "1.0",
      "cartridges": [...]
  }

The same object is consumed unchanged by :mod:`magazine.run_preparation`
(preparation / admission) and :mod:`magazine.execute` (single-cartridge
dispatch), so the refactored call sites preserve behaviour exactly.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from magazine import package
from magazine import schema


__all__ = ["load_document"]


def load_document(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Load and validate a Magazine document from *path*.

    The document is read as UTF-8 text, parsed by the strict JSON loader
    (:func:`magazine.schema.load_magazine_json`), and schema-validated
    (:func:`magazine.schema.validate_magazine`).  The same two-step
    composition is preserved, so the existing JSON loading behaviour is
    unchanged.

    In addition, an unpacked Magazine folder (a directory containing
    ``magazine.yaml``) is loaded through :mod:`magazine.package`: the package
    loader validates the v1 manifest and reads all declared files into an
    immutable execution plan; :func:`magazine.package.check_executable` gates
    the package for execution (raising
    :class:`magazine.package.UnsupportedCapabilityError` before run admission
    or model dispatch, since both go through this boundary); the plan is then
    projected via :func:`magazine.package.to_document` and re-validated with
    the existing schema.

    Args:
        path: Path to a Magazine JSON document, or to an unpacked Magazine
            folder (directory containing ``magazine.yaml``).

    Returns:
        The canonical validated Magazine document dict: ``{"schema_version":
        "1.0", "cartridges": [...]}``, with the exact parsed and validated
        object (cartridge order and content preserved).

    Raises:
        FileNotFoundError: If *path* does not exist, the folder lacks a
            ``magazine.yaml`` manifest, or a declared prompt / input / master
            prompt file is missing.
        ValueError: If a package declares an absolute or root-escaping path
            (propagated from :func:`magazine.paths.resolve_contained_path`).
        magazine.schema.MagazineSchemaError: If the JSON is malformed or the
            package manifest is invalid.
        magazine.package.UnsupportedCapabilityError: If the folder is a package
            that declares capabilities the current runtime cannot deliver
            (tools, inputs, artifact_inputs, reasoning).
            All exceptions propagate unchanged; nothing is caught, converted,
            or retried.
    """
    path = Path(path)
    if path.is_dir():
        plan = package.load_package(path)
        package.check_executable(plan)
        document = package.to_document(plan)
        schema.validate_magazine(document)
        return document
    text = path.read_text(encoding="utf-8")
    document = schema.load_magazine_json(text)
    schema.validate_magazine(document)
    return document
