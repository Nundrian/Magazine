"""Atomic persistence for Magazine run state.

Provides two public functions for saving and loading a validated run state
as UTF-8 JSON on disk.  Saves are atomic: a temporary file is written in
the target's directory, flushed, then replaced via ``os.replace``.  On any
failure the temporary file is removed and the target is left untouched.
"""

from __future__ import annotations

import json
import os
import tempfile

from magazine.state import validate_run_state

__all__ = ["save_run_state", "load_run_state"]


def save_run_state(path: str, state: dict) -> None:
    """Atomically save *state* to *path* as UTF-8 JSON.

    *state* is validated first via :func:`magazine.state.validate_run_state`.
    The write uses a temporary file in the same directory as *path*,
    flushed before an atomic ``os.replace``.  On failure the temporary
    file is removed and *path* is left unchanged.  Missing parent
    directories are not created.  The supplied *state* is not mutated.
    """
    validate_run_state(state)

    directory = os.path.dirname(path)
    fd, tmp_path = tempfile.mkstemp(
        dir=directory if directory else os.getcwd(),
        prefix=".magazine_state_",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
            f.flush()
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def load_run_state(path: str) -> dict:
    """Load a run state from *path* and return the validated state dict.

    The file is opened as UTF-8, parsed as JSON, and passed through
    :func:`magazine.state.validate_run_state`.  Malformed JSON or an
    invalid state raises an exception; no repair or normalisation is
    performed.
    """
    with open(path, "r", encoding="utf-8") as f:
        state = json.load(f)
    return validate_run_state(state)
