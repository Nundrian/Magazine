"""Execution metadata model and validation for Magazine.

Defines the ``execution.json`` schema — the per-run execution-ownership claim —
and provides strict validation and loading.

This is a standalone *model + validator*. In this slice (Slice 0) no
execution-flow code reads or writes ``execution.json``; the module is only the
data model and its strict checks. It is deliberately kept separate from the
frozen five-field ``state.json`` contract, which is not touched here.

``execution.json`` carries, in addition to the frozen ``state.json``:

    {
      "attempt": 1,
      "runtime_id": "0a1b...",
      "pid": 1234,
      "start_ticks": 123456,
      "created_at": "2026-...",
      "halted_by": null,
      "halted_at": null,
      "halt_reason": null
    }

The ``attempt``/``runtime_id`` pair is the fencing claim; the ``halt_*`` fields
are augmented (never deleted) at a lost-owner HALT.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from magazine.durability import atomic_write
from magazine.runtime_identity import RUNTIME_ID_HEX_LENGTH
from magazine.schema import (ExecutionOwnershipError, MagazineSchemaError,
    load_magazine_json)

__all__ = [
    "EXECUTION_METADATA_FIELDS",
    "HALT_REASON_OPERATOR_RECOVERED",
    "HALT_REASON_PRE_START_ABORTED",
    "ExecutionMetadata",
    "utc_now_iso",
    "validate_execution_metadata",
    "load_execution_metadata",
    "save_execution_metadata",
]

#: The exact set of fields permitted in ``execution.json``.
#: Unknown (unsupported) fields are rejected.
EXECUTION_METADATA_FIELDS: frozenset[str] = frozenset(
    (
        "attempt",
        "runtime_id",
        "pid",
        "start_ticks",
        "created_at",
        "halted_by",
        "halted_at",
        "halt_reason",
    )
)

#: Halt reason recorded when an operator explicitly recovers a previously halted
#: run.  The recovery retires the run's previous claim with this reason and
#: leaves the run re-admissible; it is not a lost-owner / fencing reason.
HALT_REASON_OPERATOR_RECOVERED = "operator-recovered"

#: Halt reason recorded when a fresh admission's ``READY -> RUNNING`` commit
#: fails before the run starts (the pre-start-abort case).  The admission path
#: is the only place that retires a claim with this reason and re-admits the
#: run (with ``attempt + 1``).
HALT_REASON_PRE_START_ABORTED = "pre-start-aborted"


def _is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _is_strict_positive_int(value: Any) -> bool:
    """True for a real ``int`` > 0 (bools are rejected)."""
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_non_negative_int(value: Any) -> bool:
    """True for a real ``int`` >= 0 (bools are rejected)."""
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value >= 0
    )


def _is_runtime_id(value: Any) -> bool:
    """True for a 128-bit nonce rendered as a lowercase hex string.

    That is, exactly ``RUNTIME_ID_HEX_LENGTH`` characters, all in
    ``0-9a-f``.
    """
    return (
        isinstance(value, str)
        and len(value) == RUNTIME_ID_HEX_LENGTH
        and all(c in "0123456789abcdef" for c in value)
    )


def utc_now_iso() -> str:
    """UTC wall-clock timestamp, ``YYYY-MM-DDTHH:MM:SSZ`` (no microseconds).

    Shared timestamp format for the durable execution claim's ``created_at``
    and ``halted_at``.  Reused by :mod:`magazine.run_action` and
    :mod:`magazine.recovery_action` so that every timestamp on a claim is
    formatted identically.
    """
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


@dataclass(frozen=True)
class ExecutionMetadata:
    """A validated execution-ownership claim (the parsed ``execution.json``).

    Attributes
    ----------
    attempt:
        Monotonic per-run integer assigned under the run lock at admission.
    runtime_id:
        128-bit process nonce (32-char lowercase hex) that authored this claim.
    pid:
        The ``pid`` of the process that authored the claim.
    start_ticks:
        The process start time in ticks since boot (from ``/proc/<pid>/stat``).
    created_at:
        Timestamp string recorded at admission.
    halted_by:
        ``runtime_id`` of the process that HALTed the run, or ``None``.
    halted_at:
        Timestamp of the HALT, or ``None``.
    halt_reason:
        Human-machinable reason for the HALT (e.g. ``"lost-owner"``), or
        ``None``.
    """

    attempt: int
    runtime_id: str
    pid: int
    start_ticks: int
    created_at: str
    halted_by: str | None
    halted_at: str | None
    halt_reason: str | None


def _field_error(message: str) -> None:
    """Convenience: raise ``ExecutionOwnershipError`` (never returns)."""
    raise ExecutionOwnershipError(message)



def validate_execution_metadata(data: Any) -> ExecutionMetadata:
    """Validate an ``execution.json`` dict and return an ``ExecutionMetadata``.

    Requires exactly the fields in :attr:`EXECUTION_METADATA_FIELDS` (no unknown /
    unsupported fields) and rejects malformed or semantically invalid values:

    * ``attempt`` — a strict positive ``int`` (``bool`` / ``float`` rejected).
    * ``runtime_id`` — a 32-character lowercase-hex 128-bit nonce.
    * ``pid`` — a strict positive ``int``.
    * ``start_ticks`` — a non-negative ``int``.
    * ``created_at`` — a non-empty ``str``.
    * ``halted_by`` — ``None`` or a valid ``runtime_id``.
    * ``halted_at`` — ``None`` or a non-empty ``str``.
    * ``halt_reason`` — ``None`` or a non-empty ``str``.

    Raises
    ------
    ExecutionOwnershipError
        If *data* is not a dict, has unknown fields, is missing required fields,
        or any field fails validation.
    """
    if not isinstance(data, dict):
        _field_error(
            f"execution metadata must be an object (dict), got {type(data).__name__}"
        )

    unknown = set(data.keys()) - EXECUTION_METADATA_FIELDS
    if unknown:
        _field_error(f"execution metadata has unsupported fields: {sorted(unknown)}")

    missing = EXECUTION_METADATA_FIELDS - set(data.keys())
    if missing:
        _field_error(
            f"execution metadata is missing required fields: {sorted(missing)}"
        )

    if not _is_non_negative_int(data["attempt"]) or data["attempt"] <= 0:
        _field_error(f"execution metadata field 'attempt' must be a positive integer")

    if not _is_runtime_id(data["runtime_id"]):
        _field_error(
            f"execution metadata field 'runtime_id' must be a "
            f"{RUNTIME_ID_HEX_LENGTH}-character lowercase-hex nonce"
        )

    if not _is_non_negative_int(data["pid"]) or data["pid"] <= 0:
        _field_error("execution metadata field 'pid' must be a positive integer")

    if not _is_non_negative_int(data["start_ticks"]):
        _field_error("execution metadata field 'start_ticks' must be a non-negative integer")

    if not _is_nonempty_string(data["created_at"]):
        _field_error("execution metadata field 'created_at' must be a non-empty string")

    if data["halted_by"] is not None and not _is_runtime_id(data["halted_by"]):
        _field_error(
            f"execution metadata field 'halted_by' must be "
            f"a {RUNTIME_ID_HEX_LENGTH}-character lowercase-hex nonce or null"
        )

    if data["halted_at"] is not None and not _is_nonempty_string(data["halted_at"]):
        _field_error("execution metadata field 'halted_at' must be a non-empty string or null")

    if data["halt_reason"] is not None and not _is_nonempty_string(data["halt_reason"]):
        _field_error("execution metadata field 'halt_reason' must be a non-empty string or null")

    return ExecutionMetadata(
        attempt=data["attempt"],
        runtime_id=data["runtime_id"],
        pid=data["pid"],
        start_ticks=data["start_ticks"],
        created_at=data["created_at"],
        halted_by=data["halted_by"],
        halted_at=data["halted_at"],
        halt_reason=data["halt_reason"],
    )


def load_execution_metadata(path: Path | str) -> ExecutionMetadata | None:
    """Load and validate the ``execution.json`` at *path*.

    Returns
    -------
    ExecutionMetadata or None
        ``None`` when *path* does not exist (a run with no claim yet). Otherwise
        the strict JSON is parsed (via :func:`load_magazine_json`) and validated
        via :func:`validate_execution_metadata`; the resulting
        ``ExecutionMetadata`` is returned.

    Raises
    ------
    ExecutionOwnershipError
        When the file is present but the JSON is malformed, the top-level value
        is not an object, or validation of the fields fails.

    Notes
    -----
    Returning ``None`` (rather than raising) on a missing file matches the
    read-only usage (e.g. the status payload). Fenced writers that *require* a
    claim (the ``RUNNING`` invariant) must treat ``None`` as a fatal
    ``ExecutionOwnershipError`` themselves.
    """
    path = Path(path)
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        _field_error(f"cannot read execution metadata file {path}: {exc}")
    try:
        data = load_magazine_json(text)
    except MagazineSchemaError as exc:
        # JSON parse / shape errors come from the strict loader as a base
        # ``MagazineSchemaError``; the load contract re-casts them to the
        # execution-ownership error so callers get a uniform exception.
        raise ExecutionOwnershipError(
            f"cannot parse execution metadata JSON in {path}: {exc}"
        ) from exc
    return validate_execution_metadata(data)


def save_execution_metadata(
    path: Path | str,
    metadata: ExecutionMetadata,
) -> None:
    """Atomically and crash-durably save a validated execution claim to *path*.

    The frozen ``ExecutionMetadata`` is serialised to a dict that is re-validated
    via :func:`validate_execution_metadata` before it ever reaches the disk, so a
    claim constructed with ``dataclasses.replace`` cannot smuggle in an invalid
    value (e.g. a ``halted_by`` that is not a valid runtime id, or a missing
    ``halt_reason``).  The write reuses :func:`magazine.durability.atomic_write`:
    a temporary file in the target's directory, fsync'd, then an atomic
    ``os.replace`` (the containing directory is fsync'd afterwards where the
    platform supports it).  On any failure before the swap the temporary file
    is unlinked and *path* is left untouched; a failed directory fsync raises.
    Missing parent directories are not created.

    This is the *only* durable writer of ``execution.json`` on the admission
    path; it is invoked only while the run lock is held, and the file is never
    deleted (the claim is augmented, never replaced, after the run has started).
    """
    data: dict[str, Any] = {
        "attempt": metadata.attempt,
        "runtime_id": metadata.runtime_id,
        "pid": metadata.pid,
        "start_ticks": metadata.start_ticks,
        "created_at": metadata.created_at,
        "halted_by": metadata.halted_by,
        "halted_at": metadata.halted_at,
        "halt_reason": metadata.halt_reason,
    }
    # Fail closed: re-validate the exact serialised object before writing, so
    # a claim whose fields have been altered (e.g. via ``dataclasses.replace``)
    # cannot reach the durable fence with a malformed value.
    validate_execution_metadata(data)
    atomic_write(
        str(path),
        json.dumps(data, ensure_ascii=False).encode("utf-8"),
        prefix=".magazine_execution_",
        suffix=".tmp",
    )
