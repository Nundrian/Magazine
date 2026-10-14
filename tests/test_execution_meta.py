"""Tests for magazine.execution_meta.

Covers the ``execution.json`` model and its strict validation: valid records,
missing / malformed / unsupported fields, and invalid ``runtime_id`` / ``attempt``
values. All failures raise ``ExecutionOwnershipError`` (a subclass of
``MagazineSchemaError``), reusing the existing exception convention.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from magazine.execution_meta import (
    EXECUTION_METADATA_FIELDS,
    ExecutionMetadata,
    load_execution_metadata,
    validate_execution_metadata,
)
from magazine.schema import ExecutionOwnershipError
from magazine.runtime_identity import RUNTIME_ID_HEX_LENGTH


def _valid_record() -> dict:
    """A fully valid execution-ownership claim."""
    return {
        "attempt": 1,
        "runtime_id": RUNTIME_ID_HEX_LENGTH * "a",  # 32 lowercase hex chars
        "pid": 1234,
        "start_ticks": 123456,
        "created_at": "2026-01-01T00:00:00Z",
        "halted_by": None,
        "halted_at": None,
        "halt_reason": None,
    }


def _halted_record() -> dict:
    """A record with the halt evidence populated."""
    r = _valid_record()
    r["halted_by"] = RUNTIME_ID_HEX_LENGTH * "b"
    r["halted_at"] = "2026-01-02T00:00:00Z"
    r["halt_reason"] = "lost-owner"
    return r


class TestValidateExecutionMetadata:
    def test_valid_record_accepted(self) -> None:
        result = validate_execution_metadata(_valid_record())
        assert isinstance(result, ExecutionMetadata)
        assert result.attempt == 1
        assert result.runtime_id == RUNTIME_ID_HEX_LENGTH * "a"
        assert result.pid == 1234
        assert result.start_ticks == 123456
        assert result.created_at == "2026-01-01T00:00:00Z"
        assert result.halted_by is None
        assert result.halted_at is None
        assert result.halt_reason is None

    def test_valid_record_is_frozen_dataclass(self) -> None:
        result = validate_execution_metadata(_valid_record())
        with pytest.raises(AttributeError):
            result.attempt = 2  # frozen

    def test_missing_field_rejected(self) -> None:
        for field in EXECUTION_METADATA_FIELDS:
            data = _valid_record()
            del data[field]
            with pytest.raises(ExecutionOwnershipError):
                validate_execution_metadata(data)

    def test_unknown_field_rejected(self) -> None:
        data = _valid_record()
        data["extra"] = "not allowed"
        with pytest.raises(ExecutionOwnershipError, match="unsupported fields"):
            validate_execution_metadata(data)

    def test_non_dict_rejected(self) -> None:
        for bad in ["not a dict", [1, 2], None, 42, {"a": 1}]:
            if isinstance(bad, dict):
                continue
            with pytest.raises(ExecutionOwnershipError):
                validate_execution_metadata(bad)

    # --- attempt -------------------------------------------------------------
    def test_attempt_zero_rejected(self) -> None:
        with pytest.raises(ExecutionOwnershipError):
            validate_execution_metadata({**_valid_record(), "attempt": 0})

    def test_attempt_negative_rejected(self) -> None:
        with pytest.raises(ExecutionOwnershipError):
            validate_execution_metadata({**_valid_record(), "attempt": -1})

    def test_attempt_float_rejected(self) -> None:
        with pytest.raises(ExecutionOwnershipError):
            validate_execution_metadata({**_valid_record(), "attempt": 1.0})

    def test_attempt_bool_rejected(self) -> None:
        with pytest.raises(ExecutionOwnershipError):
            validate_execution_metadata({**_valid_record(), "attempt": True})

    def test_attempt_string_rejected(self) -> None:
        with pytest.raises(ExecutionOwnershipError):
            validate_execution_metadata({**_valid_record(), "attempt": "1"})

    def test_attempt_positive_int_accepted(self) -> None:
        assert validate_execution_metadata({**_valid_record(), "attempt": 9999}).attempt == 9999

    # --- runtime_id -----------------------------------------------------------
    def test_runtime_id_wrong_length_rejected(self) -> None:
        with pytest.raises(ExecutionOwnershipError):
            validate_execution_metadata({**_valid_record(), "runtime_id": "short"})

    def test_runtime_id_too_long_rejected(self) -> None:
        with pytest.raises(ExecutionOwnershipError):
            validate_execution_metadata(
                {**_valid_record(), "runtime_id": "a" * (RUNTIME_ID_HEX_LENGTH + 1)}
            )

    def test_runtime_id_uppercase_rejected(self) -> None:
        with pytest.raises(ExecutionOwnershipError):
            validate_execution_metadata({**_valid_record(), "runtime_id": "A" * RUNTIME_ID_HEX_LENGTH})

    def test_runtime_id_non_hex_rejected(self) -> None:
        with pytest.raises(ExecutionOwnershipError):
            validate_execution_metadata({**_valid_record(), "runtime_id": "g" * RUNTIME_ID_HEX_LENGTH})

    def test_runtime_id_non_string_rejected(self) -> None:
        with pytest.raises(ExecutionOwnershipError):
            validate_execution_metadata({**_valid_record(), "runtime_id": 123456})

    def test_runtime_id_mixed_case_rejected(self) -> None:
        with pytest.raises(ExecutionOwnershipError):
            validate_execution_metadata({**_valid_record(), "runtime_id": "aA" * (RUNTIME_ID_HEX_LENGTH // 2)})

    # --- pid ------------------------------------------------------------------
    def test_pid_zero_rejected(self) -> None:
        with pytest.raises(ExecutionOwnershipError):
            validate_execution_metadata({**_valid_record(), "pid": 0})

    def test_pid_negative_rejected(self) -> None:
        with pytest.raises(ExecutionOwnershipError):
            validate_execution_metadata({**_valid_record(), "pid": -1})

    def test_pid_float_rejected(self) -> None:
        with pytest.raises(ExecutionOwnershipError):
            validate_execution_metadata({**_valid_record(), "pid": 1234.0})

    def test_pid_bool_rejected(self) -> None:
        with pytest.raises(ExecutionOwnershipError):
            validate_execution_metadata({**_valid_record(), "pid": True})

    # --- start_ticks -----------------------------------------------------------
    def test_start_ticks_negative_rejected(self) -> None:
        with pytest.raises(ExecutionOwnershipError):
            validate_execution_metadata({**_valid_record(), "start_ticks": -1})

    def test_start_ticks_float_rejected(self) -> None:
        with pytest.raises(ExecutionOwnershipError):
            validate_execution_metadata({**_valid_record(), "start_ticks": 123456.5})

    def test_start_ticks_bool_rejected(self) -> None:
        with pytest.raises(ExecutionOwnershipError):
            validate_execution_metadata({**_valid_record(), "start_ticks": True})

    def test_start_ticks_zero_accepted(self) -> None:
        assert validate_execution_metadata({**_valid_record(), "start_ticks": 0}).start_ticks == 0

    # --- created_at -----------------------------------------------------------
    def test_created_at_empty_rejected(self) -> None:
        with pytest.raises(ExecutionOwnershipError):
            validate_execution_metadata({**_valid_record(), "created_at": ""})

    def test_created_at_whitespace_only_rejected(self) -> None:
        with pytest.raises(ExecutionOwnershipError):
            validate_execution_metadata({**_valid_record(), "created_at": "   "})

    def test_created_at_non_string_rejected(self) -> None:
        with pytest.raises(ExecutionOwnershipError):
            validate_execution_metadata({**_valid_record(), "created_at": 12345})

    # --- halted_by / halted_at / halt_reason ---------------------------------
    def test_halted_by_valid_nonce_accepted(self) -> None:
        result = validate_execution_metadata(_halted_record())
        assert result.halted_by == RUNTIME_ID_HEX_LENGTH * "b"
        assert result.halted_at == "2026-01-02T00:00:00Z"
        assert result.halt_reason == "lost-owner"

    def test_halted_by_invalid_nonce_rejected(self) -> None:
        with pytest.raises(ExecutionOwnershipError):
            validate_execution_metadata({**_valid_record(), "halted_by": "too-short"})

    def test_halted_at_whitespace_only_rejected(self) -> None:
        with pytest.raises(ExecutionOwnershipError):
            validate_execution_metadata({**_valid_record(), "halted_at": "   "})

    def test_halt_reason_whitespace_only_rejected(self) -> None:
        with pytest.raises(ExecutionOwnershipError):
            validate_execution_metadata({**_valid_record(), "halt_reason": "   "})

    def test_all_null_halt_fields_accepted(self) -> None:
        result = validate_execution_metadata(_valid_record())
        assert result.halted_by is None
        assert result.halted_at is None
        assert result.halt_reason is None


class TestLoadExecutionMetadata:
    def test_missing_file_returns_none(self, tmp_path) -> None:
        assert load_execution_metadata(tmp_path / "nope.json") is None

    def test_loads_and_validates_a_valid_file(self, tmp_path) -> None:
        p = tmp_path / "execution.json"
        p.write_text(json.dumps(_valid_record()))
        result = load_execution_metadata(p)
        assert isinstance(result, ExecutionMetadata)
        assert result.attempt == 1

    def test_malformed_json_raises(self, tmp_path) -> None:
        p = tmp_path / "execution.json"
        p.write_text("not json {")
        with pytest.raises(ExecutionOwnershipError):
            load_execution_metadata(p)

    def test_non_object_json_raises(self, tmp_path) -> None:
        p = tmp_path / "execution.json"
        p.write_text("[1, 2, 3]")
        with pytest.raises(ExecutionOwnershipError):
            load_execution_metadata(p)

    def test_invalid_field_raises(self, tmp_path) -> None:
        p = tmp_path / "execution.json"
        p.write_text(json.dumps({**_valid_record(), "attempt": 0}))
        with pytest.raises(ExecutionOwnershipError):
            load_execution_metadata(p)

    def test_duplicate_key_raises(self, tmp_path) -> None:
        p = tmp_path / "execution.json"
        p.write_text('{"attempt": 1, "attempt": 2}')
        with pytest.raises(ExecutionOwnershipError):
            load_execution_metadata(p)

    def test_accepts_string_path(self, tmp_path) -> None:
        p = tmp_path / "execution.json"
        p.write_text(json.dumps(_valid_record()))
        assert load_execution_metadata(str(p)).attempt == 1
