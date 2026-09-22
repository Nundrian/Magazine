"""Strict JSON loader for Magazine files.

Parses UTF-8 JSON text and returns a Python dict.  The loader is strict:

* Rejects duplicate object keys.
* Rejects NaN, Infinity and -Infinity.
* Requires the top-level value to be a JSON object (dict).

No routing, role maps, environment loading, or Pydantic models are included.
"""

from __future__ import annotations

import json
from typing import Any


MAGAZINE_SCHEMA_VERSION = "1.0"


class MagazineSchemaError(Exception):
    """Raised when JSON validation fails."""


def _reject_inf(partial: str) -> str:
    """parse_constant callback: reject NaN / Infinity before they become strings."""
    raise MagazineSchemaError(f"JSON contains disallowed numeric value: {partial}")


def _detect_duplicate_keys(pairs: list[tuple[Any, Any]]) -> dict:
    """Object-pairs hook that raises on duplicate keys."""
    seen_keys: set[str] = set()
    result: dict = {}
    for key, value in pairs:
        if key in seen_keys:
            raise MagazineSchemaError(f"Duplicate key: {key}")
        seen_keys.add(key)
        result[key] = value
    return result


def load_magazine_json(text: str) -> dict[str, Any]:
    """Load *text* as UTF-8 JSON and return the decoded dict.

    Raises ``MagazineSchemaError`` when:

    * The text contains duplicate object keys.
    * The text contains NaN, Infinity, or -Infinity.
    * The top-level value is not a JSON object.
    """
    try:
        data = json.loads(
            text,
            parse_constant=_reject_inf,
            object_pairs_hook=_detect_duplicate_keys,
        )
    except json.JSONDecodeError as exc:
        raise MagazineSchemaError(f"Invalid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise MagazineSchemaError(
            "Top-level JSON value must be an object (dict), "
            f"got {type(data).__name__}"
        )

    return data


def validate_schema_version(data: dict[str, Any]) -> None:
    """Validate that *data* has ``"schema_version": "1.0"``.

    Raises ``MagazineSchemaError`` when:

    * ``schema_version`` is missing.
    * ``schema_version`` is not exactly the string ``"1.0"``.
    * ``schema_version`` is a non-string value (e.g. numeric ``1.0``).
    """
    if "schema_version" not in data:
        raise MagazineSchemaError("Missing required field: schema_version")

    version = data["schema_version"]
    if not isinstance(version, str):
        raise MagazineSchemaError(
            f"schema_version must be a string, got {type(version).__name__}"
        )
    if version != MAGAZINE_SCHEMA_VERSION:
        raise MagazineSchemaError(
            f"schema_version must be {MAGAZINE_SCHEMA_VERSION!r}, got {version!r}"
        )


# ------------------------------------------------------------------
# Cartridge validation
# ------------------------------------------------------------------

_REQUIRED_CARTRIDGE_FIELDS: set[str] = {"id", "job_type", "prompt"}


def validate_cartridge(data: Any) -> dict[str, str]:
    """Validate a Magazine cartridge dict.

    Requires exactly the fields ``id``, ``job_type``, and ``prompt``,
    each a non-empty string (after stripping whitespace).

    Raises ``MagazineSchemaError`` when:

    * *data* is not a dict.
    * Any required field is missing.
    * Any field value is not a string.
    * Any field value is empty or whitespace-only.
    * Any unknown extra fields are present.

    Returns the validated cartridge dict unchanged on success.
    """
    if not isinstance(data, dict):
        raise MagazineSchemaError(
            f"Cartridge must be an object (dict), got {type(data).__name__}"
        )

    # Reject unknown fields
    unknown = set(data.keys()) - _REQUIRED_CARTRIDGE_FIELDS
    if unknown:
        raise MagazineSchemaError(f"Unknown cartridge fields: {sorted(unknown)}")

    # Check required fields
    missing = _REQUIRED_CARTRIDGE_FIELDS - set(data.keys())
    if missing:
        raise MagazineSchemaError(f"Missing required cartridge fields: {sorted(missing)}")

    # Validate each field
    for field in _REQUIRED_CARTRIDGE_FIELDS:
        value = data[field]
        if not isinstance(value, str):
            raise MagazineSchemaError(
                f"Cartridge field {field!r} must be a string, "
                f"got {type(value).__name__}"
            )
        if not value.strip():
            raise MagazineSchemaError(
                f"Cartridge field {field!r} must not be empty or whitespace-only"
            )

    return data


# ------------------------------------------------------------------
# Magazine validation
# ------------------------------------------------------------------

_REQUIRED_MAGAZINE_FIELDS: set[str] = {"schema_version", "cartridges"}


def validate_magazine(data: dict[str, Any]) -> dict[str, Any]:
    """Validate a top-level Magazine document.

    Requires exactly the fields ``schema_version`` and ``cartridges``.

    Raises ``MagazineSchemaError`` when:

    * ``schema_version`` is missing or invalid.
    * ``cartridges`` is missing, not a list, or empty.
    * Any cartridge item fails ``validate_cartridge()``.
    * Any unknown top-level fields are present.

    Returns the original *data* dict unchanged on success.
    """
    # Reject unknown top-level fields
    unknown = set(data.keys()) - _REQUIRED_MAGAZINE_FIELDS
    if unknown:
        raise MagazineSchemaError(f"Unknown top-level fields: {sorted(unknown)}")

    # Check required fields
    missing = _REQUIRED_MAGAZINE_FIELDS - set(data.keys())
    if missing:
        raise MagazineSchemaError(f"Missing required fields: {sorted(missing)}")

    # Validate schema_version
    validate_schema_version(data)

    # Validate cartridges
    cartridges = data["cartridges"]
    if not isinstance(cartridges, list):
        raise MagazineSchemaError(
            f"cartridges must be a list, got {type(cartridges).__name__}"
        )
    if len(cartridges) == 0:
        raise MagazineSchemaError("cartridges must not be empty")

    for item in cartridges:
        validate_cartridge(item)

    # Enforce unique cartridge IDs
    seen_ids: set[str] = set()
    for item in cartridges:
        cid = item["id"]
        if cid in seen_ids:
            raise MagazineSchemaError(f"Duplicate cartridge id: {cid!r}")
        seen_ids.add(cid)

    return data