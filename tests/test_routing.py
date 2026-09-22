"""Tests for magazine.routing.resolve_model and resolve_tool."""

import pytest

from magazine.routing import resolve_model, resolve_tool


ROLE_MAP = {
    "edit": "gpt-4o",
    "proof": "claude-3-5-sonnet",
    "layout": "gemini-pro",
}

TOOL_MAP = {
    "terminal": "open_terminal",
    "file_read": "dev_read_file",
    "file_write": "dev_write_file",
}


class TestResolveModel:
    """Deterministic model routing primitive tests."""

    def test_known_job_type_returns_exact_mapped_model(self):
        for job_type, expected in ROLE_MAP.items():
            assert resolve_model(job_type, ROLE_MAP) == expected

    def test_unknown_job_type_is_rejected(self):
        with pytest.raises(KeyError):
            resolve_model("unknown_job", ROLE_MAP)

    def test_empty_job_type_is_rejected(self):
        with pytest.raises(TypeError):
            resolve_model("", ROLE_MAP)

    def test_non_dict_role_map_is_rejected(self):
        with pytest.raises(TypeError):
            resolve_model("edit", "not_a_dict")

    def test_whitespace_only_job_type_is_rejected(self):
        with pytest.raises(TypeError):
            resolve_model("   ", ROLE_MAP)

    def test_whitespace_only_mapped_model_id_is_rejected(self):
        bad_map = {"edit": "   "}
        with pytest.raises(ValueError):
            resolve_model("edit", bad_map)

    def test_empty_mapped_model_id_is_rejected(self):
        bad_map = {"edit": ""}
        with pytest.raises(ValueError):
            resolve_model("edit", bad_map)


class TestResolveTool:
    """Deterministic tool routing primitive tests."""

    def test_known_tool_role_returns_exact_mapped_tool_id(self):
        for tool_role, expected in TOOL_MAP.items():
            assert resolve_tool(tool_role, TOOL_MAP) == expected

    def test_unknown_tool_role_is_rejected(self):
        with pytest.raises(KeyError):
            resolve_tool("unknown_role", TOOL_MAP)

    def test_empty_tool_role_is_rejected(self):
        with pytest.raises(TypeError):
            resolve_tool("", TOOL_MAP)

    def test_whitespace_only_tool_role_is_rejected(self):
        with pytest.raises(TypeError):
            resolve_tool("   ", TOOL_MAP)

    def test_non_dict_tool_map_is_rejected(self):
        with pytest.raises(TypeError):
            resolve_tool("terminal", "not_a_dict")

    def test_empty_mapped_tool_id_is_rejected(self):
        bad_map = {"terminal": ""}
        with pytest.raises(ValueError):
            resolve_tool("terminal", bad_map)

    def test_whitespace_only_mapped_tool_id_is_rejected(self):
        bad_map = {"terminal": "   "}
        with pytest.raises(ValueError):
            resolve_tool("terminal", bad_map)
