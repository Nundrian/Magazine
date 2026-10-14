"""Synchronous CLI adapter for Magazine run preparation.

The CLI loads and validates a Magazine JSON document, an unpacked package, or
a ``.magazine`` archive package, creates the run directory when needed and
performs atomic first run admission.  It does not dispatch cartridges; full
execution is provided by ``magazine.async_runner.run_magazine`` /
``run_magazine_open_webui``.

Exit codes: 0 on successful preparation or an already terminal run; 1 on any
error.
"""

from __future__ import annotations

import argparse
import sys

from magazine import run_action
from magazine import run_preparation
from magazine import state

EXIT_OK = 0
EXIT_ERROR = 1


def _err(message: str) -> None:
    print(f"[magazine] {message}", file=sys.stderr)


def _prepare(magazine_path: str, state_dir: str, owner_id: str) -> int:
    """Execute the composition sequence; return the exit code.

    Magazine load, schema validation, run-directory creation, and the atomic
    first-run admission (including the one-time derivation of ``state_path``
    / ``lock_path``) are delegated to the shared
    :mod:`magazine.run_preparation` module so the async runner does not
    depend on a private CLI helper and so paths are derived exactly once.
    This layer only maps the returned
    :class:`magazine.run_preparation.PreparedRun` onto the existing CLI output
    and exit codes.  Every failure is left as the original exception;
    :func:`main` converts them to EXIT_ERROR so the CLI never shows a traceback.
    """
    try:
        prepared = run_preparation.prepare_run(
            magazine_path, state_dir, owner_id
        )
    except run_action.RunAdmissionError:
        # Already-RUNNING with a free lock: this replaces the previous
        # RUNNING + no lock -> UNCERTAIN path.  It is NOT success: no join,
        # no resume, no dispatch — the existing explicit recovery path via
        # the recovery action API is required.
        _err(
            "run is RUNNING without a lock; needs explicit recovery via the "
            "recovery action API, exiting"
        )
        return EXIT_ERROR

    # Branch only on the freshly persisted status returned by admission.  A
    # MagazineLockError (an existing lock held by another process) is not
    # caught here: it propagates to :func:`main` and produces the existing
    # locked/error behaviour — no retry, no polling, no lock
    # deletion/stealing.
    status = prepared.state["status"]

    if status == state.STATUS_RUNNING:
        # RUNNING: admission succeeded. Preserve the previous successful
        # preparation behaviour (STARTABLE -> RUNNING) and do NOT dispatch
        # anything here.
        print(
            f"run prepared: status={status} "
            f"current={prepared.state['current']!r}",
        )
        return EXIT_OK
    if status in (state.STATUS_COMPLETED, state.STATUS_HALTED):
        # COMPLETED / HALTED: admission returned the existing terminal state
        # unchanged; preserve the existing terminal behaviour.
        # Successful outcome, nothing to do, no dispatch, no re-save.
        _err(f"run {status}: nothing to do")
        return EXIT_OK

    # prepare_run returns only RUNNING, COMPLETED or HALTED (an already-RUNNING
    # state raises RunAdmissionError instead); this branch is unreachable and
    # kept for explicitness, as before.
    _err(f"unknown status after admission: {status!r}")
    return EXIT_ERROR


def main(argv: list[str] | None = None) -> int:
    """Parse CLI args, run the composition sequence, return the exit code."""
    parser = argparse.ArgumentParser(
        description="Magazine stage-1 entrypoint: load, validate, and "
        "prepare a run for cartridge execution"
    )
    parser.add_argument(
        "--magazine",
        required=True,
        help="Path to a Magazine document (JSON file, unpacked folder, or ``.magazine`` archive package)",
    )
    parser.add_argument(
        "--state-dir",
        required=True,
        help="Directory containing the run/ state subdirectory",
    )
    parser.add_argument(
        "--owner-id",
        default="magazine",
        help="Owner ID written into the run lock (default: magazine)",
    )
    args = parser.parse_args(argv)

    try:
        return _prepare(args.magazine, args.state_dir, args.owner_id)
    except Exception as exc:
        # Top-level envelope: the CLI reports one clean error line,
        # never a traceback.
        _err(f"{type(exc).__name__}: {exc}")
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
