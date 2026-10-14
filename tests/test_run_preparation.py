"""Focused tests for magazine.run_preparation (shared run-preparation boundary).

Exactly three tests:
  1. A real fresh-run preparation: valid Magazine -> once-derived paths ->
     atomic admission via the REAL persist_admit_run -> persisted RUNNING ->
     admission lock released -> PreparedRun with the exact state and paths.
  2. A narrow composition check: persist_admit_run is patched (and only it),
     called exactly once with the exact once-derived paths and
     Magazine-ordered cartridge IDs, and its exact returned object is handed
     through untouched (identity, no state reload).
  3. An existing RUNNING persisted state makes prepare_run propagate
     RunAdmissionError without converting, swallowing, or touching the
     persisted state (byte-identity) or the lock.

COMPLETED / HALTED, held-lock, async, run_all_cartridges, dispatch, retries,
and recovery are intentionally not covered here; they live in their owning
layers or in a later stage.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from unittest.mock import patch

from magazine import run_action
from magazine import run_preparation
from magazine.run_preparation import PreparedRun
from magazine import state
from magazine.runtime_identity import get_runtime_identity
from magazine.state_store import load_run_state, save_run_state

CARTRIDGE_IDS = ("cart-a", "cart-b")


def _valid_magazine(ids: tuple[str, ...]) -> dict:
    """Same valid Magazine structure used by tests/test_entrypoint.py."""
    return {
        "schema_version": "1.0",
        "cartridges": [
            {"id": i, "job_type": "research", "prompt": f"prompt for {i}"}
            for i in ids
        ],
    }


def _write_magazine(path: Path, document: dict) -> None:
    path.write_text(json.dumps(document), encoding="utf-8")


def test_prepare_fresh_run_real_filesystem(tmp_path: Path) -> None:
    """Real fresh-run preparation with the REAL filesystem and REAL
    persist_admit_run: valid Magazine -> derived paths -> atomic admission ->
    persisted RUNNING -> lock released -> PreparedRun returned."""
    magazine_path = tmp_path / "magazine.json"
    _write_magazine(magazine_path, _valid_magazine(CARTRIDGE_IDS))
    state_dir = tmp_path / "state"  # run/ subdirectory does not exist yet

    prepared = run_preparation.prepare_run(
        str(magazine_path), str(state_dir), "owner-a"
    )

    # 1. prepared is a PreparedRun.
    assert isinstance(prepared, PreparedRun)

    # 2. prepared.state is exactly the freshly admitted RUNNING state.
    expected_state = {
        "done": [],
        "current": "cart-a",
        "todo": ["cart-b"],
        "status": state.STATUS_RUNNING,
        "revision": 1,
    }
    assert prepared.state == expected_state

    # 3. / 4. exact once-derived paths.
    expected_state_path = str(Path(state_dir) / "run" / "state.json")
    expected_lock_path = str(Path(state_dir) / "run" / ".lock")
    assert prepared.state_path == expected_state_path
    assert prepared.lock_path == expected_lock_path

    # 5. state_path exists; 6. lock_path does NOT exist after return (admission
    #    lock released by persist_admit_run).
    state_path = Path(prepared.state_path)
    lock_path = Path(prepared.lock_path)
    assert state_path.exists()
    assert not lock_path.exists()

    # 7. the real state store returns exactly prepared.state.
    assert load_run_state(prepared.state_path) == prepared.state


def test_persist_admit_run_invoked_once_with_exact_args_and_identity(
    tmp_path: Path
) -> None:
    """Narrow composition test: patch ONLY run_action.persist_admit_run.  It
    is invoked exactly once with the once-derived paths, the supplied owner,
    and Magazine-ordered cartridge IDs; prepare_run returns its exact returned
    object and paths (identity, no reload)."""
    magazine_path = tmp_path / "magazine.json"
    _write_magazine(magazine_path, _valid_magazine(CARTRIDGE_IDS))
    state_dir = tmp_path / "state"

    admitted = {
        "done": [],
        "current": "sentinel",
        "todo": [],
        "status": state.STATUS_RUNNING,
        "revision": 99,
    }
    attempted = 7  # distinct sentinel attempt number handed back
    calls: list[tuple] = []

    def _fake_persist_admit_run(
        state_path: str,
        lock_path: str,
        owner_id: str,
        cartridge_ids: list[str],
        runtime_id: str,
        pid: int,
        start_ticks: int,
    ):
        # Record the once-derived paths, owner, cartridges, and identity args;
        # hand the single unique sentinel (state, attempt) back.
        calls.append(
            (
                state_path,
                lock_path,
                owner_id,
                cartridge_ids,
                runtime_id,
                pid,
                start_ticks,
            )
        )
        return admitted, attempted

    expected_state_path = str(Path(state_dir) / "run" / "state.json")
    expected_lock_path = str(Path(state_dir) / "run" / ".lock")

    # Only run_preparation's admission is patched: filesystem path handling,
    # schema validation, and Magazine loading stay real.
    with patch.object(
        run_preparation.run_action,
        "persist_admit_run",
        _fake_persist_admit_run,
    ):
        prepared = run_preparation.prepare_run(
            str(magazine_path), str(state_dir), "owner-a"
        )

    # 1. persist_admit_run was called exactly once.
    assert len(calls) == 1
    (
        state_arg,
        lock_arg,
        owner_arg,
        cartridges,
        rid_arg,
        pid_arg,
        ticks_arg,
    ) = calls[0]

    # 2. / 3. its state_path / lock_path args equal the once-derived paths.
    assert state_arg == expected_state_path
    assert lock_arg == expected_lock_path

    # 4. owner_id is exactly the supplied owner.
    assert owner_arg == "owner-a"

    # 5. cartridge_ids are exactly Magazine-ordered.
    assert cartridges == ["cart-a", "cart-b"]

    # 6. the identity args are the live process identity (no explicit identity
    #    was supplied to prepare_run, so it resolves the live process identity).
    live = get_runtime_identity()
    assert (rid_arg, pid_arg, ticks_arg) == (
        live.runtime_id,
        live.pid,
        live.start_ticks,
    )

    # 7. prepared.state IS the admitted object (identity, not equality).
    assert prepared.state is admitted

    # 8. prepared.attempt equals the attempt number the admission returned.
    assert prepared.attempt == attempted

    # 9. prepared.identity is the same identity that was passed to admission.
    assert prepared.identity is live

    # 10. prepared's paths are the same paths passed to persist_admit_run.
    assert prepared.state_path == state_arg
    assert prepared.lock_path == lock_arg


def test_run_admission_error_propagates_state_untouched(tmp_path: Path) -> None:
    """A pre-existing RUNNING persisted state makes prepare_run propagate
    RunAdmissionError (no exit-code mapping, no swallowing); the persisted
    state stays byte-identical and the lock never exists."""
    magazine_path = tmp_path / "magazine.json"
    _write_magazine(magazine_path, _valid_magazine(CARTRIDGE_IDS))
    state_dir = tmp_path / "state"
    run_dir = Path(state_dir) / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    state_path = run_dir / "state.json"
    lock_path = run_dir / ".lock"

    existing_state = {
        "done": [],
        "current": "cart-a",
        "todo": ["cart-b"],
        "status": state.STATUS_RUNNING,
        "revision": 1,
    }
    save_run_state(str(state_path), existing_state)
    assert not lock_path.exists()

    bytes_before = state_path.read_bytes()

    # 1./2./3. RunAdmissionError propagates from prepare_run: it is neither
    # converted to an exit code nor swallowed here.
    with pytest.raises(run_action.RunAdmissionError):
        run_preparation.prepare_run(
            str(magazine_path), str(state_dir), "owner-a"
        )

    # 4. persisted state bytes remain exactly equal to bytes_before.
    assert state_path.read_bytes() == bytes_before

    # 5. the loaded state remains exactly the original RUNNING state.
    assert load_run_state(str(state_path)) == existing_state

    # 6. lock_path does not exist after the exception.
    assert not lock_path.exists()
