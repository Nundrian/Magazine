"""Unit tests for the magazine runner's pure admission-validation logic.

These tests exercise `_validate_admission` and its helpers without any OWUI
or core dependency, so they run in a bare pytest environment.
"""
import pytest

import magazine_ef as mfr


# ---------------------------------------------------------------------------
# Fixture documents
# ---------------------------------------------------------------------------
@pytest.fixture
def two_cartridge_doc():
    """A document with two cartridges using distinct logical model keys."""
    return {
        "schema_version": "1.0",
        "name": "Model-agnostic two-cartridge doc",
        "cartridges": [
            {"id": "concise", "job_type": "concise", "prompt": "Say hi briefly."},
            {"id": "reasoning", "job_type": "reasoning", "prompt": "Think carefully."},
        ],
    }

@pytest.fixture
def single_cartridge_doc():
    return {
        "schema_version": "1.0",
        "name": "One cartridge",
        "cartridges": [
            {"id": "first", "job_type": "only", "prompt": "Do one thing."},
        ],
    }

@pytest.fixture
def no_model_key_cartridge():
    """A cartridge whose job_type/model is empty (no logical key)."""
    return {
        "schema_version": "1.0",
        "cartridges": [
            {"id": "blank", "job_type": "", "prompt": ""},
        ],
    }


# ---------------------------------------------------------------------------
# _document_logical_keys
# ---------------------------------------------------------------------------
def test_logical_keys_job_type(two_cartridge_doc):
    keys = mfr._document_logical_keys(two_cartridge_doc)
    assert keys == {"concise", "reasoning"}


def test_logical_keys_empty_cartridge(no_model_key_cartridge):
    # A cartridge with blank job_type contributes no logical key.
    keys = mfr._document_logical_keys(no_model_key_cartridge)
    assert keys == set()


def test_logical_keys_missing_document():
    assert mfr._document_logical_keys(None) == set()
    assert mfr._document_logical_keys([]) == set()


# ---------------------------------------------------------------------------
# _available_model_ids / _available_tool_ids
# ---------------------------------------------------------------------------
def test_available_model_ids_dict_list():
    models = [
        {"id": "diaga", "name": "diaga"},
        {"id": "Bonsai 2 27B", "name": "Bonsai 2 27B"},
    ]
    assert mfr._available_model_ids(models) == {"diaga", "Bonsai 2 27B"}


def test_available_model_ids_object():
    class M:
        pass
    m = M()
    m.id = "diaga"
    assert mfr._available_model_ids([m]) == {"diaga"}


def test_available_tool_ids_dict_list():
    tools = [
        {"id": "host_files"},
        {"id": "host_terminal"},
        {"id": "host_browser"},
    ]
    assert mfr._available_tool_ids(tools) == {"host_files", "host_terminal", "host_browser"}


def test_available_ids_empty():
    assert mfr._available_model_ids([]) == set()
    assert mfr._available_model_ids(None) == set()
    assert mfr._available_tool_ids(None) == set()


# ---------------------------------------------------------------------------
# _validate_admission
# ---------------------------------------------------------------------------
def test_admission_ok_distinct_models(two_cartridge_doc):
    available_models = ["diaga", "Bonsai 2 27B"]
    available_tools = ["host_files", "host_terminal", "host_browser"]
    model_map = {"concise": "diaga", "reasoning": "Bonsai 2 27B"}
    tool_ids = ["host_files"]
    errors = mfr._validate_admission(
        two_cartridge_doc, model_map, tool_ids, available_models, available_tools)
    assert errors == []


def test_admission_ok_without_tools(two_cartridge_doc):
    available_models = ["diaga", "Bonsai 2 27B"]
    available_tools = ["host_files", "host_terminal", "host_browser"]
    model_map = {"concise": "diaga", "reasoning": "Bonsai 2 27B"}
    tool_ids = []  # no tools required
    errors = mfr._validate_admission(
        two_cartridge_doc, model_map, tool_ids, available_models, available_tools)
    assert errors == []


def test_admission_rejects_missing_model_map_entry(single_cartridge_doc):
    available_models = ["diaga"]
    available_tools = ["host_files"]
    model_map = {}  # missing mapping for logical key 'only'
    errors = mfr._validate_admission(single_cartridge_doc, model_map, [],
                                     available_models, available_tools)
    assert any("only" in e for e in errors)
    assert len(errors) >= 1


def test_admission_rejects_stale_model(single_cartridge_doc):
    available_models = ["diaga"]
    available_tools = ["host_files"]
    model_map = {"only": "Bonsai 2 27B"}  # not available
    errors = mfr._validate_admission(single_cartridge_doc, model_map, [],
                                     available_models, available_tools)
    assert any("Bonsai 2 27B" in e for e in errors)


def test_admission_rejects_stale_tool(single_cartridge_doc):
    available_models = ["diaga"]
    available_tools = ["host_files"]
    model_map = {"only": "diaga"}
    tool_ids = ["some_unknown_tool"]
    errors = mfr._validate_admission(single_cartridge_doc, model_map, tool_ids,
                                     available_models, available_tools)
    assert any("some_unknown_tool" in e for e in errors)


def test_admission_rejects_both_missing_and_stale(single_cartridge_doc):
    available_models = ["diaga"]
    available_tools = ["host_files"]
    model_map = {"only": "diaga", "extra_key": "Bonsai 2 27B"}
    tool_ids = ["bad_tool"]
    errors = mfr._validate_admission(single_cartridge_doc, model_map, tool_ids,
                                     available_models, available_tools)
    assert any("Bonsai 2 27B" in e for e in errors)
    assert any("bad_tool" in e for e in errors)


def test_admission_model_map_must_be_dict(single_cartridge_doc):
    available_models = ["diaga"]
    available_tools = []
    errors = mfr._validate_admission(single_cartridge_doc, "not-a-dict", [],
                                     available_models, available_tools)
    # model_map coerced to {} -> missing logical key 'only'
    assert any("only" in e for e in errors)


def test_admission_tool_ids_none(single_cartridge_doc):
    available_models = ["diaga"]
    available_tools = []
    model_map = {"only": "diaga"}
    errors = mfr._validate_admission(single_cartridge_doc, model_map, None,
                                     available_models, available_tools)
    assert errors == []


def test_admission_no_cartridges():
    doc = {"schema_version": "1.0", "cartridges": []}
    available_models = ["diaga"]
    available_tools = []
    model_map = {}
    errors = mfr._validate_admission(doc, model_map, [], available_models, available_tools)
    assert errors == []


# ---------------------------------------------------------------------------
# Helper extraction (robust to dicts / objects)
# ---------------------------------------------------------------------------
def test_extract_model_id_dict():
    assert mfr._extract_model_id({"id": "diaga"}) == "diaga"
    assert mfr._extract_model_id({"model": "Bonsai 2 27B"}) == "Bonsai 2 27B"
    assert mfr._extract_model_id({"name": "diaga"}) == "diaga"
    assert mfr._extract_model_id({}) == ""
    assert mfr._extract_model_id(None) == ""


def test_extract_tool_id_dict():
    assert mfr._extract_tool_id({"id": "host_files"}) == "host_files"
    assert mfr._extract_tool_id({"name": "host_files"}) == "host_files"
    assert mfr._extract_tool_id(None) == ""
