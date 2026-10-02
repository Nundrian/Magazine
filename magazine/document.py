"""Single Magazine document-loading boundary.

Supported sources are the existing strict JSON document, an unpacked
Magazine folder containing ``magazine.yaml``, and a ``.magazine`` ZIP archive
package.  Downstream orchestration always receives the same compatibility
document shape; package sources additionally retain their immutable
``LoadedPackage`` plan so path/runtime metadata is not lost.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from magazine import package, schema
from magazine.package import LoadedPackage, MAGAZINE_ARCHIVE_EXTENSION

__all__ = ["LoadedMagazine", "load_document"]


@dataclass(frozen=True)
class LoadedMagazine:
    document: dict[str, Any]
    plan: LoadedPackage | None


def load_document(path: str | os.PathLike[str]) -> LoadedMagazine:
    """Load and validate a Magazine JSON document, unpacked package, or a
    ``.magazine`` archive package.

    A directory is treated as an unpacked package root; a ``.magazine`` file
    as a ZIP archive package; anything else as a JSON document.  Directory
    behaviour is unchanged.
    """
    source = Path(path)
    if source.is_dir():
        plan = package.load_package(source)
        document = package.to_document(plan)
        schema.validate_magazine(document)
        return LoadedMagazine(document=document, plan=plan)

    if source.suffix == MAGAZINE_ARCHIVE_EXTENSION:
        plan = package.load_package(source)
        document = package.to_document(plan)
        schema.validate_magazine(document)
        return LoadedMagazine(document=document, plan=plan)

    text = source.read_text(encoding="utf-8")
    document = schema.load_magazine_json(text)
    schema.validate_magazine(document)
    return LoadedMagazine(document=document, plan=None)
