"""Async runner composition layer for Magazine.

The single public composition entry point for an async run.  It performs the
run preparation / atomic admission exactly once via
:func:`magazine.run_preparation.prepare_run`, branches on the authoritative
status carried back from that admission, and — only when the run is
``RUNNING`` — delegates the full bounded multi-cartridge loop to
:func:`magazine.run_loop.run_all_cartridges`.

Sequence (identical to the required concurrency contract):

  prepare_run
  -> persist_admit_run acquires the admission lock
  -> authoritative state load/create
  -> READY -> RUNNING persisted when required
  -> admission lock released
  -> PreparedRun returned
  -> run_magazine sees authoritative RUNNING state
  -> run_all_cartridges begins external dispatch

Invariants:

* :func:`prepare_run` is called exactly once.  No state is re-read and no
  ``state_path`` / ``lock_path`` is independently derived; only
  :class:`~magazine.run_preparation.PreparedRun.state` and the two paths
  returned by preparation are used.
* This module acquires no lock.  The admission lock is released by
  :func:`magazine.run_preparation.prepare_run` before it returns, so no lock
  spans the external dispatch.
* ``COMPLETED`` and ``HALTED`` runs are returned immediately, with zero
  dispatch and no state saved again.
* An unexpected status fails explicitly with ``RuntimeError``; no dispatch
  happens and nothing is treated as success.
* No exceptions are caught: admission errors (``RunAdmissionError`` /
  ``MagazineLockError``), dispatch failures, completion and ``begin-next``
  failures all propagate unchanged.  No retry, recovery, polling, lock
  stealing, lock deletion, or exception translation is added.
* :func:`magazine.run_loop.run_all_cartridges` keeps its Stage-5 single-runner
  precondition.  This layer provides the admission boundary; it does NOT make
  direct callers of :func:`magazine.run_loop.run_all_cartridges` concurrency
  safe.
* This module is composition only: it is not wired into ``main()`` and does
  not touch the CLI.
"""

from __future__ import annotations

from magazine import run_loop
from magazine import run_preparation
from magazine.state import STATUS_COMPLETED, STATUS_HALTED, STATUS_RUNNING

__all__ = ["run_magazine"]


async def run_magazine(
    magazine_path: str,
    state_dir: str,
    owner_id: str,
    base_url: str,
    api_key: str,
    tool_role: str,
    model_map: dict,
    tool_map: dict,
    dispatch,
) -> dict:
    """Prepare, admit, and run one async Magazine run; return the terminal state.

    :func:`magazine.run_preparation.prepare_run` is called exactly once.  It
    validates the magazine, derives ``state_path`` / ``lock_path`` once,
    performs the single atomic first-run admission, and returns a
    :class:`~magazine.run_preparation.PreparedRun`.  The admission lock is
    released before preparation returns.

    If the admitted run is ``RUNNING`` the existing
    :func:`magazine.run_loop.run_all_cartridges` loop is awaited and its
    ``final_state`` returned.  If the run is already ``COMPLETED`` or
    ``HALTED`` that terminal state is returned immediately, with no dispatch
    and no state saved.  Any other status fails with ``RuntimeError``.

    Args:
        magazine_path: Path to the Magazine JSON document (passed to both
            preparation and the run loop).
        state_dir: Directory containing the run/ state subdirectory (used by
            preparation to derive the once-derived paths).
        owner_id: Owner ID written into the run lock.
        base_url: Base API URL forwarded unchanged to the existing run loop.
        api_key: Bearer API key (forwarded to the run loop).
        tool_role: Key into *tool_map* to look up the Tool ID (forwarded to the
            run loop).
        model_map: Mapping of job types to model IDs (forwarded to the run
            loop).
        tool_map: Mapping of tool roles to Tool IDs (forwarded to the run
            loop).
        dispatch: Async callable accepting a *prompt* string and the resolved
            *model_id* string (forwarded to the run loop, unchanged).

    Returns:
        The authoritative final/terminal run state dict: ``final_state`` from
        the run loop when the run finished through dispatch, or the admitted
        state when it was already ``COMPLETED`` / ``HALTED``.

    Raises:
        RuntimeError: If the admitted status is not ``RUNNING``,
            ``COMPLETED``, or ``HALTED``; no dispatch happens in that case.
        Exception: Every exception from preparation (schema errors,
            :class:`magazine.run_lock.MagazineLockError`,
            :class:`magazine.run_action.RunAdmissionError`) and from
            :func:`magazine.run_loop.run_all_cartridges` (dispatch failures,
            completion transitions, ``begin-next`` transitions, lock failures)
            propagates immediately and unchanged.
    """
    # Atomic preparation / admission exactly once.  No lock is acquired here;
    # the admission lock is released inside prepare_run before it returns.
    prepared = run_preparation.prepare_run(
        magazine_path,
        state_dir,
        owner_id,
    )

    # Branch on the authoritative status carried back from admission.  No
    # state is re-read from disk and no path is re-derived.
    status = prepared.state["status"]

    if status == STATUS_RUNNING:
        result = await run_loop.run_all_cartridges(
            magazine_path,
            prepared.state_path,
            prepared.lock_path,
            owner_id,
            base_url,
            api_key,
            tool_role,
            model_map,
            tool_map,
            dispatch,
        )
        # Return the exact final state the loop returned; nothing is re-read.
        return result.final_state

    if status == STATUS_COMPLETED or status == STATUS_HALTED:
        # Terminal run: zero dispatch, no state saved again.
        return prepared.state

    # Unexpected status: fail explicitly.  No dispatch, not treated as
    # success.  RuntimeError matches the existing directly applicable pattern
    # in magazine/run_loop.py.
    raise RuntimeError(
        f"unexpected run status {status!r}; expected "
        f"{STATUS_RUNNING!r}, {STATUS_COMPLETED!r}, or {STATUS_HALTED!r}"
    )
