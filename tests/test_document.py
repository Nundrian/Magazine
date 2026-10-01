"""Focused tests for magazine.document.load_document (single Magazine
document-loading boundary) and its two consumers.

Covered:
  1. A valid existing JSON magazine loads successfully through the boundary.
  2. The returned document preserves cartridge order and content exactly
     (exact value equality with the canonical document, full dict identity
     preserved through the boundary).
  3. Malformed JSON (truncated text, NaN / Infinity literals, duplicate
     keys, single-quoted strings) produces the existing
     :class:`magazine.schema.MagazineSchemaError` loading behaviour.
  4. Structurally invalid magazine data is rejected through the existing
     validation layer (same error types and messages as before).
  5. A missing source file propagates ``FileNotFoundError`` (unchanged).
  6. ``magazine.run_preparation.prepare_run`` loads through the new boundary
     (spy verifies the single boundary call with the supplied path) and
     completes a real admission; its invalid-magazine error also propagates
     unchanged before any filesystem side effect.
  7. ``magazine.execute.dispatch_current_cartridge`` loads through the new
     boundary (spy verifies the single boundary call with the supplied path)
     and continues to dispatch the exact cartridge prompt + resolved model
     ID once, leaving the run untouched; its invalid-magazine error also
     propagates unchanged before any dispatch.

COMPLETED / HALTED, held-lock, async runner, multi-cartridge loop, dispatch
failure, and recovery are not covered here; they live in their owning layers
(tests/test_run_preparation.py / tests/test_execute.py /
tests/test_run_loop.py).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from unittest.mock import AsyncMock, patch

from magazine import execute
from magazine import run_preparation
from magazine import state
from magazine.schema import MagazineSchemaError

from magazine.run_preparation import PreparedRun


BASE_URL = "http://localhost:8080"
API_KEY = "test-key"
CURRENT_ID = "cart-b"
MODEL_ID = "m1"
TOOL_ID = "bash"
MODEL_MAP = {"research": MODEL_ID}
TOOL_MAP = {"terminal": TOOL_ID}
TOOL_ROLE = "terminal"


# ---------------------------------------------------------------------------
# Helpers (same conventions as test_run_preparation.py / test_execute.py)
# ---------------------------------------------------------------------------

def _valid_magazine(ids: tuple[str, ...]) -> dict:
    """Valid Magazine structure with distinctive content per cartridge."""
    return {
        "schema_version": "1.0",
        "cartridges": [
            {"id": i, "job_type": "research", "prompt": f"prompt for {i}"}
            for i in ids
        ],
    }


def _write_magazine(path: Path, document) -> None:
    path.write_text(json.dumps(document), encoding="utf-8")


def _write_state(path: Path, state_data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state_data), encoding="utf-8")


def _running_state() -> dict:
    return {
        "done": ["cart-a"],
        "current": CURRENT_ID,
        "todo": ["cart-c"],
        "status": state.STATUS_RUNNING,
        "revision": 1,
    }


# ---------------------------------------------------------------------------
# 1. valid JSON magazine loads successfully
# ---------------------------------------------------------------------------

def test_load_document_valid_json_loads_successfully(tmp_path: Path) -> None:
    """A valid existing JSON magazine loads successfully; the returned
    document is a canonical dict with the required fields."""
    magazine_path = tmp_path / "magazine.json"
    _write_magazine(magazine_path, _valid_magazine(("cart-a", "cart-b")))

    document = execute.document.load_document(str(magazine_path))

    # 1. canonical top-level fields, correct schema version.
    assert isinstance(document, dict)
    assert document["schema_version"] == "1.0"
    assert "cartridges" in document
    assert isinstance(document["cartridges"], list)
    assert len(document["cartridges"]) == 2


# ---------------------------------------------------------------------------
# 2. returned document preserves cartridge order and content
# ---------------------------------------------------------------------------

def test_load_document_preserves_cartridge_order_and_content(tmp_path: Path) -> None:
    """The returned document preserves cartridge order and full cartridge
    content (id / job_type / prompt) exactly (exact value equality with the
    canonical document)."""
    magazine_path = tmp_path / "magazine.json"
    expected = {
        "schema_version": "1.0",
        "cartridges": [
            {"id": "cart-a", "job_type": "research", "prompt": "prompt for cart-a"},
            {"id": "cart-b", "job_type": "research", "prompt": "prompt for cart-b"},
            {"id": "cart-c", "job_type": "research", "prompt": "prompt for cart-c"},
        ],
    }
    _write_magazine(magazine_path, expected)

    document = execute.document.load_document(str(magazine_path))

    # 1. full content and order preserved (exact list equality).
    assert document == expected
    assert [c["id"] for c in document["cartridges"]] == ["cart-a", "cart-b", "cart-c"]
    assert document["cartridges"][1]["prompt"] == "prompt for cart-b"


# ---------------------------------------------------------------------------
# 3. malformed JSON -> existing schema/loading error behaviour
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "bad_json",
    [
        "{",  # truncated
        '{"a": NaN}',
        '{"a": Infinity}',
        '{"a": -Infinity}',
        '{"schema_version": "1.0", "cartridges": [',  # truncated list
        "{'single': 'quoted'}",  # single quotes are invalid JSON
        '{"schema_version": "1.0", "schema_version": "1.0"}',  # duplicate key
        "1",  # top-level scalar (also fails the object check)
    ],
)
def test_load_document_malformed_json_raises_schema_error(
    tmp_path: Path, bad_json: str
) -> None:
    """Malformed JSON produces the existing
    :class:`magazine.schema.MagazineSchemaError` loading behaviour (strict
    JSON loader errors), propagated unchanged."""
    magazine_path = tmp_path / "magazine.json"
    magazine_path.write_text(bad_json, encoding="utf-8")

    with pytest.raises(MagazineSchemaError):
        execute.document.load_document(str(magazine_path))


# ---------------------------------------------------------------------------
# 4. structurally invalid magazine -> rejected through the existing
#    validation layer
# ---------------------------------------------------------------------------

def _single_valid_cartridges():
    return _valid_magazine(("cart-a",))["cartridges"]


def _single_valid_cartridge_dict():
    return {"schema_version": "1.0", "cartridges": _single_valid_cartridges()}


@pytest.mark.parametrize(
    (
        "document",
        "error_substring",
    ),
    [
        # missing schema_version (and cartridges)
        ({}, "Missing required fields"),
        # empty cartridges
        ({"schema_version": "1.0", "cartridges": []}, "cartridges must not be empty"),
        # missing cartridges
        ({"schema_version": "1.0"}, "Missing required fields"),
        # unknown top-level field
        (
            {**_single_valid_cartridge_dict(), "extra": 1},
            "Unknown top-level fields",
        ),
        # wrong schema_version
        (
            {"schema_version": "2.0", "cartridges": _single_valid_cartridges()},
            "schema_version must be '1.0'",
        ),
        # non-string schema_version
        (
            {"schema_version": 1.0, "cartridges": _single_valid_cartridges()},
            "schema_version must be a string",
        ),
        # duplicate cartridge id
        (
            {
                "schema_version": "1.0",
                "cartridges": _single_valid_cartridges()
                + [
                    {
                        "id": "cart-a",
                        "job_type": "research",
                        "prompt": "prompt for cart-a",
                    }
                ],
            },
            "Duplicate cartridge id",
        ),
        # missing cartridge field
        (
            {
                "schema_version": "1.0",
                "cartridges": [{"id": "cart-a", "job_type": "research"}],
            },
            "Missing required cartridge fields",
        ),
        # unknown cartridge field
        (
            {
                "schema_version": "1.0",
                "cartridges": [
                    {"id": "cart-a", "job_type": "research", "prompt": "p",
                     "extra": "x"}
                ],
            },
            "Unknown cartridge fields",
        ),
        # whitespace-only prompt
        (
            {
                "schema_version": "1.0",
                "cartridges": [
                    {"id": "cart-a", "job_type": "research", "prompt": "   "}
                ],
            },
            "must not be empty or whitespace-only",
        ),
        # non-dict cartridge item
        ({"schema_version": "1.0", "cartridges": [1]}, "Cartridge must be an object"),
        # null cartridge item
        (
            {"schema_version": "1.0", "cartridges": [None]},
            "Cartridge must be an object",
        ),
        # cartridges not a list
        (
            {"schema_version": "1.0", "cartridges": {"id": "cart-a"}},
            "cartridges must be a list",
        ),
        # top-level JSON array
        ([1, 2, 3], "Top-level JSON value must be an object"),
        # top-level JSON string (written as raw text; valid JSON, wrong type)
        ("just a string", "Top-level JSON value must be an object"),
    ],
)
def test_load_document_structurally_invalid_rejected_by_validation_layer(
    tmp_path: Path, document, error_substring
) -> None:
    """Structurally invalid magazine data is rejected through the existing
    validation layer (same error types and messages as
    ``magazine.schema``), via the boundary.

    Non-dict *document* entries (the raw JSON-string case) are encoded by
    ``json.dumps`` (via :func:`_write_magazine`) so the file is *valid* JSON
    that fails the top-level object check, rather than raw text failing the
    JSON parse.
    """
    magazine_path = tmp_path / "magazine.json"
    _write_magazine(magazine_path, document)

    with pytest.raises(MagazineSchemaError) as excinfo:
        execute.document.load_document(str(magazine_path))

    # Same error type and message as the validation layer directly.
    assert isinstance(excinfo.value, MagazineSchemaError)
    assert error_substring in str(excinfo.value)


def test_load_document_missing_file_propagates_file_not_found(tmp_path: Path) -> None:
    """A missing source file propagates ``FileNotFoundError`` unchanged
    (the boundary only centralises reading; it does not swallow or wrap
    it)."""
    with pytest.raises(FileNotFoundError):
        execute.document.load_document(str(tmp_path / "no-such.json"))


# ---------------------------------------------------------------------------
# 5. run_preparation uses the new loader successfully (and propagates its
#    errors)
# ---------------------------------------------------------------------------

def test_prepare_run_loads_via_boundary_and_admits(tmp_path: Path) -> None:
    """prepare_run loads the magazine through
    ``magazine.document.load_document`` (spy verifies the single boundary call
    with the supplied path), the boundary's returned document drives the
    once-derived cartridge IDs, and the REAL admission completes on the
    tmp_path filesystem, leaving an admitted RUNNING state with the
    boundary-derived paths and the lock released."""
    magazine_path = tmp_path / "magazine.json"
    _write_magazine(magazine_path, _valid_magazine(("cart-a", "cart-b")))
    state_dir = tmp_path / "state"

    spy_calls: list[tuple] = []

    def _spy(path: str) -> dict:
        spy_calls.append(path)
        return _valid_magazine(("cart-a", "cart-b"))

    with patch.object(run_preparation.document, "load_document", _spy):
        prepared = run_preparation.prepare_run(
            str(magazine_path), str(state_dir), "owner-a"
        )

    # 1. the boundary is called exactly once with the supplied path.
    assert spy_calls == [str(magazine_path)]

    # 2. prepared is a PreparedRun with the REAL admitted RUNNING state
    #    built from the boundary-supplied cartridges.
    assert isinstance(prepared, PreparedRun)
    assert prepared.state == {
        "done": [],
        "current": "cart-a",
        "todo": ["cart-b"],
        "status": state.STATUS_RUNNING,
        "revision": 1,
    }

    # 3. once-derived paths; admission lock released by persist_admit_run.
    assert prepared.state_path == str(Path(state_dir) / "run" / "state.json")
    assert prepared.lock_path == str(Path(state_dir) / "run" / ".lock")
    assert not Path(prepared.lock_path).exists()


def test_prepare_run_invalid_magazine_error_propagates_before_admission(
    tmp_path: Path
) -> None:
    """An invalid magazine file makes prepare_run propagate
    :class:`magazine.schema.MagazineSchemaError` unchanged (via the boundary),
    with no run directory created and nothing admitted (ordering: owner check
    -> document load -> state-dir mkdir -> admission)."""
    magazine_path = tmp_path / "magazine.json"
    magazine_path.write_text('{"schema_version": "1.0", "cartridges": [',
                              encoding="utf-8")
    state_dir = tmp_path / "state"

    with pytest.raises(MagazineSchemaError):
        run_preparation.prepare_run(
            str(magazine_path), str(state_dir), "owner-a"
        )

    # Nothing was admitted: the state dir's run/ subdirectory was never
    # created.
    assert not (Path(state_dir) / "run").exists()
    assert not (Path(state_dir) / "run" / "state.json").exists()
    assert not (Path(state_dir) / "run" / ".lock").exists()


def test_prepare_run_valid_file_real_pathway_success(tmp_path: Path) -> None:
    """prepare_run succeeds end-to-end with a REAL valid magazine file read
    through the unpatched boundary (real load_document, real admission),
    proving the refactored path is behaviourally unchanged."""
    magazine_path = tmp_path / "magazine.json"
    _write_magazine(magazine_path, _valid_magazine(("cart-a", "cart-b")))
    state_dir = tmp_path / "state"

    prepared = run_preparation.prepare_run(
        str(magazine_path), str(state_dir), "owner-a"
    )

    assert isinstance(prepared, PreparedRun)
    assert prepared.state["current"] == "cart-a"
    assert prepared.state["todo"] == ["cart-b"]
    assert prepared.state["status"] == state.STATUS_RUNNING
    assert Path(prepared.state_path).exists()
    assert not Path(prepared.lock_path).exists()


# ---------------------------------------------------------------------------
# 6. dispatch_current_cartridge continues to work through the new loader
# ---------------------------------------------------------------------------

@patch("magazine.runtime_dispatch.validate_live_runtime_pair")
def test_dispatch_current_cartridge_loads_via_boundary_and_dispatches(
    mock_preflight, tmp_path: Path
) -> None:
    """dispatch_current_cartridge loads the magazine through
    ``magazine.document.load_document`` (spy verifies the single boundary call
    with the supplied path) and dispatches the exact cartridge prompt +
    resolved model ID once, bundling the dispatched cartridge ID with the
    runtime result, leaving the persisted state byte-identical and creating
    no lock file."""
    # Preflight succeeds without any live discovery (no HTTP).
    mock_preflight.return_value = None

    magazine_path = tmp_path / "magazine.json"
    _write_magazine(
        magazine_path,
        _valid_magazine(("cart-a", "cart-b", "cart-c")),
    )
    state_path = tmp_path / "state.json"
    _write_state(state_path, _running_state())
    original_bytes = state_path.read_bytes()

    spy_calls: list[tuple] = []

    def _spy(path: str) -> dict:
        spy_calls.append(path)
        return _valid_magazine(("cart-a", "cart-b", "cart-c"))

    sentinel = object()
    mock_dispatch = AsyncMock(return_value=sentinel)

    with patch.object(execute.document, "load_document", _spy):
        result = asyncio.run(
            execute.dispatch_current_cartridge(
                magazine_path=str(magazine_path),
                state_path=str(state_path),
                base_url=BASE_URL,
                api_key=API_KEY,
                tool_role=TOOL_ROLE,
                model_map=MODEL_MAP,
                tool_map=TOOL_MAP,
                dispatch=mock_dispatch,
            )
        )

    # 1. the boundary is called exactly once with the supplied path.
    assert spy_calls == [str(magazine_path)]

    # 2. routing + preflight ran with the resolved model/tool IDs.
    mock_preflight.assert_called_once_with(BASE_URL, API_KEY, MODEL_ID, TOOL_ID)

    # 3. the exact cartridge prompt + resolved model ID were dispatched once.
    mock_dispatch.assert_awaited_once_with("prompt for cart-b", MODEL_ID)

    # 4. returns a CartridgeDispatch bundling the dispatched cartridge ID with
    #    the exact runtime result object.
    assert result.cartridge_id == CURRENT_ID
    assert result.dispatch_result is sentinel

    # 5. leaves the persisted state byte-identical and creates no lock file.
    assert state_path.read_bytes() == original_bytes
    assert not (state_path.parent / ".lock").exists()


def test_dispatch_current_cartridge_invalid_magazine_propagates_before_dispatch(
    tmp_path: Path
) -> None:
    """An invalid magazine file makes dispatch_current_cartridge propagate
    :class:`magazine.schema.MagazineSchemaError` unchanged (via the boundary)
    before any routing, preflight, or dispatch: state byte-identical, no
    lock file."""
    magazine_path = tmp_path / "magazine.json"
    magazine_path.write_text('{"schema_version": "1.0", "cartridges": [',
                              encoding="utf-8")
    state_path = tmp_path / "state.json"
    _write_state(state_path, _running_state())
    original_bytes = state_path.read_bytes()

    mock_dispatch = AsyncMock(return_value=None)

    with pytest.raises(MagazineSchemaError):
        asyncio.run(
            execute.dispatch_current_cartridge(
                magazine_path=str(magazine_path),
                state_path=str(state_path),
                base_url=BASE_URL,
                api_key=API_KEY,
                tool_role=TOOL_ROLE,
                model_map=MODEL_MAP,
                tool_map=TOOL_MAP,
                dispatch=mock_dispatch,
            )
        )

    # 1. dispatch callback never awaited.
    mock_dispatch.assert_not_awaited()

    # 2. run untouched.
    assert state_path.read_bytes() == original_bytes
    assert not (state_path.parent / ".lock").exists()
