"""Tests for magazine.schema.load_magazine_json and validate_schema_version."""

from __future__ import annotations

import pytest

from magazine.schema import MagazineSchemaError, load_magazine_json, validate_schema_version, validate_cartridge, validate_magazine


class TestLoadMagazineJson:
    """Strict JSON loader tests."""

    def test_valid_json_object_is_accepted(self) -> None:
        result = load_magazine_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_duplicate_key_is_rejected(self) -> None:
        with pytest.raises(MagazineSchemaError):
            load_magazine_json('{"key": "first", "key": "second"}')

    def test_nan_is_rejected(self) -> None:
        with pytest.raises(MagazineSchemaError):
            load_magazine_json('{"value": NaN}')

    def test_infinity_is_rejected(self) -> None:
        with pytest.raises(MagazineSchemaError):
            load_magazine_json('{"value": Infinity}')

    def test_negative_infinity_is_rejected(self) -> None:
        with pytest.raises(MagazineSchemaError):
            load_magazine_json('{"value": -Infinity}')

    def test_top_level_array_is_rejected(self) -> None:
        with pytest.raises(MagazineSchemaError):
            load_magazine_json('[1, 2, 3]')

    def test_quoted_nan_infinity_are_accepted(self) -> None:
        """Quoted strings "NaN", "Infinity", "-Infinity" are normal strings."""
        result = load_magazine_json(
            '{"a": "NaN", "b": "Infinity", "c": "-Infinity"}'
        )
        assert result == {"a": "NaN", "b": "Infinity", "c": "-Infinity"}


class TestValidateSchemaVersion:
    """Schema version validation tests."""

    def test_schema_version_1_0_accepted(self) -> None:
        validate_schema_version({"schema_version": "1.0"})

    def test_missing_schema_version_rejected(self) -> None:
        with pytest.raises(MagazineSchemaError):
            validate_schema_version({})

    def test_another_version_rejected(self) -> None:
        with pytest.raises(MagazineSchemaError):
            validate_schema_version({"schema_version": "2.0"})

    def test_numeric_1_0_rejected(self) -> None:
        with pytest.raises(MagazineSchemaError):
            validate_schema_version({"schema_version": 1.0})


class TestValidateCartridge:
    """Cartridge validation tests."""

    def test_valid_cartridge_accepted(self) -> None:
        result = validate_cartridge({
            "id": "test-1",
            "job_type": "summarize",
            "prompt": "Summarize the text.",
        })
        assert result == {
            "id": "test-1",
            "job_type": "summarize",
            "prompt": "Summarize the text.",
        }

    def test_missing_required_field_rejected(self) -> None:
        with pytest.raises(MagazineSchemaError):
            validate_cartridge({"id": "test-1", "job_type": "summarize"})

    def test_unknown_field_rejected(self) -> None:
        with pytest.raises(MagazineSchemaError):
            validate_cartridge({
                "id": "test-1",
                "job_type": "summarize",
                "prompt": "Summarize.",
                "extra": "field",
            })

    def test_non_string_field_rejected(self) -> None:
        with pytest.raises(MagazineSchemaError):
            validate_cartridge({
                "id": 123,
                "job_type": "summarize",
                "prompt": "Summarize.",
            })

    def test_whitespace_only_field_rejected(self) -> None:
        with pytest.raises(MagazineSchemaError):
            validate_cartridge({
                "id": "test-1",
                "job_type": "   ",
                "prompt": "Summarize.",
            })


class TestValidateMagazine:
    """Magazine-level validation tests."""

    def test_valid_magazine_accepted(self) -> None:
        data = {
            "schema_version": "1.0",
            "cartridges": [
                {
                    "id": "test-1",
                    "job_type": "summarize",
                    "prompt": "Summarize the text.",
                }
            ],
        }
        result = validate_magazine(data)
        assert result is data

    def test_missing_cartridges_rejected(self) -> None:
        with pytest.raises(MagazineSchemaError):
            validate_magazine({"schema_version": "1.0"})

    def test_non_list_cartridges_rejected(self) -> None:
        with pytest.raises(MagazineSchemaError):
            validate_magazine({
                "schema_version": "1.0",
                "cartridges": "not a list",
            })

    def test_empty_cartridges_rejected(self) -> None:
        with pytest.raises(MagazineSchemaError):
            validate_magazine({
                "schema_version": "1.0",
                "cartridges": [],
            })

    def test_invalid_cartridge_rejected(self) -> None:
        with pytest.raises(MagazineSchemaError):
            validate_magazine({
                "schema_version": "1.0",
                "cartridges": [{"id": "bad"}],
            })

    def test_unknown_top_level_field_rejected(self) -> None:
        with pytest.raises(MagazineSchemaError):
            validate_magazine({
                "schema_version": "1.0",
                "cartridges": [
                    {
                        "id": "test-1",
                        "job_type": "summarize",
                        "prompt": "Summarize the text.",
                    }
                ],
                "extra_field": "oops",
            })

    def test_different_cartridge_ids_accepted(self) -> None:
        data = {
            "schema_version": "1.0",
            "cartridges": [
                {"id": "a", "job_type": "summarize", "prompt": "Summarize."},
                {"id": "b", "job_type": "translate", "prompt": "Translate."},
            ],
        }
        result = validate_magazine(data)
        assert result is data

    def test_duplicate_cartridge_ids_rejected(self) -> None:
        with pytest.raises(MagazineSchemaError):
            validate_magazine({
                "schema_version": "1.0",
                "cartridges": [
                    {"id": "dup", "job_type": "summarize", "prompt": "Summarize."},
                    {"id": "dup", "job_type": "translate", "prompt": "Translate."},
                ],
            })
