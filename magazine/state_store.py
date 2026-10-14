"""Atomic and crash-durable persistence for Magazine run state.

Provides two public functions for saving and loading a validated run state
as UTF-8 JSON on disk.  Saves are atomic *and* crash-durable: a temporary
file is written in the target's directory, fsync'd before being replaced
via ``os.replace``, and the containing directory is fsync'd afterwards where
the platform supports directory fsync (best-effort no-op elsewhere, e.g.
Windows).  On any failure before the swap the temporary file is removed and
the target is left untouched; a failed directory fsync on a supported
platform raises and is never reported as success.

The shared durable-write protocol lives in :mod:`magazine.durability` and is
reused by :mod:`magazine.result_store` for cartridge results.
"""

from __future__ import annotations

import json

from magazine.durability import atomic_write
from magazine.state import validate_run_state

__all__ = ["save_run_state", "load_run_state"]


def save_run_state(path: str, state: dict) -> None:
    """Atomically and crash-durably save *state* to *path* as UTF-8 JSON.

    *state* is validated first via :func:`magazine.state.validate_run_state`.
    The write uses :mod:`magazine.durability`'s durable atomic write: a
    temporary file in the target's directory, fsync'd so the new contents
    reach stable storage before the atomic ``os.replace`` (the directory is
    fsync'd afterwards where supported, so the swap is durable too).  On
    any failure before the swap the temporary file is removed and *path*
    is left unchanged; a failed directory fsync on a supported platform
    raises.  Missing parent directories are not created.  The supplied
    *state* is not mutated.
    """
    validate_run_state(state)
    atomic_write(
        path,
        json.dumps(state, ensure_ascii=False).encode("utf-8"),
        prefix=".magazine_state_",
        suffix=".tmp",
    )


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
