"""Focused tests for magazine.entrypoint (stage-1 composition).

  1. A valid Magazine run is prepared end-to-end (fresh state -> RUNNING).
  2. An invalid Magazine follows the validation path with no side effects.
  3. A valid .magazine archive prepares a run end-to-end.
  4. A bad .magazine archive fails validation with no side effects.
  5. Startup/recovery states map to the correct exit codes via the existing
     inspection API, with no state mutation in the non-STARTABLE branches.
"""

from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path

from magazine import entrypoint

MAGAZINE_IDS = ("a", "b", "c")


def _write_magazine(path: Path, document: dict) -> None:
    path.write_text(json.dumps(document), encoding="utf-8")


def _valid_magazine(ids: tuple[str, ...]) -> dict:
    return {
        "schema_version": "1.0",
        "cartridges": [
            {"id": i, "job_type": "research", "prompt": f"prompt for {i}"}
            for i in ids
        ],
    }


def _state_dir(path: Path, **state: object) -> Path:
    """Create a fresh run/state.json under ``path``."""
    state_path = path / "run" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state), encoding="utf-8")
    return state_path


def _build_unpacked_package(package_dir: Path) -> None:
    """Write the minimal unpacked package used by the archive tests."""
    (package_dir / "cartridges").mkdir(parents=True, exist_ok=True)
    (package_dir / "cartridges" / "a.md").write_text(
        "prompt for a", encoding="utf-8"
    )
    (package_dir / "magazine.yaml").write_text(
        '''schema_version: "1.0"
name: Demo
cartridges:
  - id: a
    model: research
    prompt_file: cartridges/a.md
''',
        encoding="utf-8",
    )


def _package_directory_to_archive(
    package_dir: Path, archive_path: Path
) -> Path:
    """Zip the unpacked package directory into a ``.magazine`` file."""
    with zipfile.ZipFile(archive_path, "w") as archive:
        for root, dirs, files in os.walk(package_dir):
            for f in files:
                p = Path(root) / f
                archive.write(str(p), os.path.relpath(p, package_dir))
            for d in dirs:
                p = Path(root) / d
                archive.writestr(os.path.relpath(p, package_dir) + "/", "")
    return archive_path


def test_prepare_valid_run_end_to_end(tmp_path: Path) -> None:
    """Valid magazine + fresh state dir -> run prepared in RUNNING state."""
    magazine_path = tmp_path / "magazine.json"
    _write_magazine(magazine_path, _valid_magazine(MAGAZINE_IDS))
    state_dir = tmp_path / "state"

    rc = entrypoint.main([
        "--magazine", str(magazine_path),
        "--state-dir", str(state_dir),
    ])

    assert rc == 0, "valid run preparation must exit 0"
    state = json.loads((state_dir / "run" / "state.json").read_text())
    assert state == {
        "done": [],
        "current": "a",
        "todo": ["b", "c"],
        "status": "RUNNING",
        "revision": 1,
    }
    # persist_begin_next_cartridge releases the lock on every path.
    assert not (state_dir / "run" / ".lock").exists()


def test_invalid_magazine_follows_validation_path(
    tmp_path: Path, capsys: object
) -> None:
    """Invalid cartridge -> schema error, no state dir ever created."""
    magazine_path = tmp_path / "bad_magazine.json"
    # Missing required field "prompt" on one cartridge.
    _write_magazine(
        magazine_path,
        {
            "schema_version": "1.0",
            "cartridges": [
                {"id": "a", "job_type": "research"},
            ],
        },
    )
    state_dir = tmp_path / "state"

    rc = entrypoint.main([
        "--magazine", str(magazine_path),
        "--state-dir", str(state_dir),
    ])

    assert rc == 1, "invalid magazine must exit 1"
    # Validation runs before any filesystem side effect: the state
    # directory must not exist at all.
    assert not (state_dir / "run").exists(), (
        "state dir must not be created when magazine validation fails"
    )
    err = capsys.readouterr().err
    assert "Missing required cartridge fields" in err
    assert "Traceback" not in err, "CLI must not show a traceback"


def test_prepare_archive_run_end_to_end(tmp_path) -> None:
    """A valid ``.magazine`` archive prepares a run end-to-end."""
    package_dir = tmp_path / "pkg"
    _build_unpacked_package(package_dir)
    archive = _package_directory_to_archive(
        package_dir, tmp_path / "magazine.magazine"
    )
    state_dir = tmp_path / "state"

    rc = entrypoint.main([
        "--magazine", str(archive),
        "--state-dir", str(state_dir),
    ])

    assert rc == 0, "valid archive run preparation must exit 0"
    state = json.loads((state_dir / "run" / "state.json").read_text())
    assert state == {
        "done": [],
        "current": "a",
        "todo": [],
        "status": "RUNNING",
        "revision": 1,
    }
    # persist_begin_next_cartridge releases the lock on all paths.
    assert not (state_dir / "run" / ".lock").exists()


def test_prepare_invalid_archive_has_no_side_effects(tmp_path, capsys):
    """A corrupted ``.magazine`` archive fails before state creation."""
    (tmp_path / "bad.magazine").write_text("not a zip", encoding="utf-8")
    state_dir = tmp_path / "state"

    rc = entrypoint.main([
        "--magazine", str(tmp_path / "bad.magazine"),
        "--state-dir", str(state_dir),
    ])

    assert rc == 1, "corrupt archive run preparation must exit 1"
    assert not (state_dir / "run").exists(), (
        "state directory must not be created when the archive fails validation"
    )
    err = capsys.readouterr().err
    assert "Traceback" not in err, "CLI must not display a traceback"


def test_startup_recovery_branches(tmp_path: Path) -> None:
    """Pre-existing states map to the recovery API's classifications.

    For each branch, the persisted state must be byte-identical after the
    call except where STARTABLE legitimately transitions to RUNNING.
    """
    magazine_path = tmp_path / "magazine.json"
    _write_magazine(magazine_path, _valid_magazine(MAGAZINE_IDS))

    scenarios = [
        # (name, pre-existing state, expected exit code)
        ("locked", {
            "done": [], "current": None, "todo": ["a", "b", "c"],
            "status": "READY", "revision": 0,
        }, 1),
        ("running_uncertain", {
            "done": [], "current": "b", "todo": ["c"],
            "status": "RUNNING", "revision": 1,
        }, 1),
        ("completed", {
            "done": ["a", "b", "c"], "current": None, "todo": [],
            "status": "COMPLETED", "revision": 3,
        }, 0),
        ("halted", {
            "done": [], "current": "b", "todo": ["c"],
            "status": "HALTED", "revision": 1,
        }, 0),
    ]

    for name, initial_state, expected_rc in scenarios:
        state_dir = tmp_path / name
        state_path = _state_dir(state_dir, **initial_state)
        if name == "locked":
            (state_path.parent / ".lock").write_text(
                "someone-else", encoding="utf-8"
            )

        rc = entrypoint.main([
            "--magazine", str(magazine_path),
            "--state-dir", str(state_dir),
        ])

        assert rc == expected_rc, (
            f"{name}: expected exit {expected_rc}, got {rc}"
        )
        # Non-STARTABLE branches must not mutate the persisted state.
        assert json.loads(state_path.read_text()) == initial_state, (
            f"{name}: state must be unchanged by the {name} branch"
        )
