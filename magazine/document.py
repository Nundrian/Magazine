"""Single Magazine document-loading boundary.

All Magazine execution entry points load a Magazine document through this
one function, so that source-format changes (the current JSON file; later an
unpacked Magazine folder and ``.magazine`` ZIP packages using the same
internal structure) only change *this* module and never the downstream
orchestration or runtime.

The function reads the source, delegates all JSON parsing and schema rules to
the existing :mod:`magazine.schema` implementation (strict JSON loader +
schema validation), and hands back the canonical validated document:

  {
      "schema_version": "1.0",
      "cartridges": [...]
  }

The same object is already consumed unchanged by
:mod:`magazine.run_preparation` (preparation / admission) and
:mod:`magazine.execute` (single-cartridge dispatch), so the refactored call
sites preserve behaviour exactly.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from magazine import schema


__all__ = ["load_document"]


def load_document(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Load and validate a Magazine document from *path*.

    The document is read as UTF-8 text, parsed by the strict JSON loader
    (:func:`magazine.schema.load_magazine_json`), and schema-validated
    (:func:`magazine.schema.validate_magazine`).  The same two-step
    composition is preserved, so the existing JSON loading behaviour is
    unchanged.

    Args:
        path: Path to the Magazine document (JSON file for now; any source
            supported by this boundary).

    Returns:
        The canonical validated Magazine document dict: ``{"schema_version":
        "1.0", "cartridges": [...]}``, with the exact parsed and validated
        object (cartridge order and content preserved).

    Raises:
        FileNotFoundError: If *path* does not exist.
        magazine.schema.MagazineSchemaError: If the JSON is malformed
            (invalid JSON, disallowed numeric literals, duplicate keys,
            non-object top level) or the document fails schema validation
            (missing/unknown fields, invalid ``schema_version``, malformed
            cartridges, duplicate cartridge ids).
            All exceptions propagate unchanged; nothing is caught, converted,
            or retried.
    """
    text = Path(path).read_text(encoding="utf-8")
    document = schema.load_magazine_json(text)
    schema.validate_magazine(document)
    return document
