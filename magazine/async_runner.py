"""Top-level async composition for Magazine."""

from __future__ import annotations

import asyncio

from magazine import run_loop, run_preparation, recovery_action
from magazine.execution_meta import (
    HALT_REASON_TASK_CANCELLED_BETWEEN_CARTRIDGES,
    HALT_REASON_TASK_FAILED_BETWEEN_CARTRIDGES,
)
from magazine.state import STATUS_COMPLETED, STATUS_HALTED, STATUS_RUNNING
from magazine.runtime_identity import RuntimeIdentity
from magazine.schema import ExecutionOwnershipError
from magazine.webui_dispatch import make_open_webui_dispatch
from magazine.run_preparation import PreparedRun

__all__ = [
    "run_magazine",
    "run_magazine_open_webui",
    "run_dispatch",
    "run_dispatch_open_webui",
]


async def _run_cartridges_or_halt(
    magazine_path: str,
    state_path: str,
    lock_path: str,
    owner_id: str,
    writer_attempt: int,
    writer_identity: RuntimeIdentity,
    model_map: dict,
    dispatch,
) -> dict:
    """Run all remaining cartridges; on an authoritative-execution error,
    fence-persist a ``HALTED`` state and then re-raise.

    This is the execution-entrypoint boundary (Slice 2, design item 5).  It
    wraps :func:`magazine.run_loop.run_all_cartridges` with a ``try`` /
    ``except``.  When a cartridge dispatch (or the fenced result/completion
    stages) raises an authoritative execution failure, the run is transitioned
    ``RUNNING -> HALTED`` under a fenced
    :func:`magazine.recovery_action.persist_error_halt` (which verifies the
    writer's exact ownership identity) and the HALTED state is durably
    recorded before the original exception is re-raised.

    A :class:`magazine.schema.ExecutionOwnershipError` raised by the fence
    itself is a *stale, non-owner* generation: the current execution is no
    longer the owner of this run.  That case must NOT transition the run to
    ``HALTED`` -- it would wrongly mark a run the successor now owns as failed.
    So the error-halt is skipped for ``ExecutionOwnershipError`` only, and the
    original exception is re-raised unchanged.

    Args:
        magazine_path: Path to the Magazine document.
        state_path: Path to the persisted run state file.
        lock_path: Path to the run lock file.
        owner_id: Owner ID for the lock and for the claim.
        writer_attempt: The execution-attempt number the run's claim must carry.
        writer_identity: The runtime identity whose ``runtime_id`` the run's
            claim must carry.
        model_map: Model map handed to the loop.
        dispatch: The runtime dispatch callable.

    Returns:
        The exact ``COMPLETED`` state the loop persisted when the run
        completes cleanly.

    Raises:
        The original execution exception (after a successful, fenced error
        halt) when the run raises mid-execution.  All non-ownership errors
        propagate unchanged; ``ExecutionOwnershipError`` from the fence is
        re-raised after the original failure (the one case where the error-
        halt must be skipped).
    """
    def persist_failure(error: BaseException, ready_retire_reason: str) -> None:
        """Persist the owner's safe failure state without replacing *error*."""
        try:
            recovery_action.persist_error_halt(
                state_path,
                lock_path,
                owner_id,
                writer_attempt,
                writer_identity,
                error=error,
                ready_retire_reason=ready_retire_reason,
            )
        except ExecutionOwnershipError:
            # Non-owner: do not halt or retire a run this generation no longer
            # owns.  The original execution failure/cancellation remains the
            # primary exception.
            pass
        except Exception as persistence_error:
            # Preserve the execution exception and its traceback as primary,
            # while keeping the failed safety-persistence attempt inspectable.
            # ``add_note`` exists on Python 3.11+; the explicit attribute keeps
            # equivalent evidence available on the supported Python 3.10.
            error.magazine_persistence_error = persistence_error
            add_note = getattr(error, "add_note", None)
            if add_note is not None:
                add_note(
                    "Magazine could not persist the execution failure state: "
                    f"{persistence_error!r}"
                )

    try:
        result = await run_loop.run_all_cartridges(
            magazine_path,
            state_path,
            lock_path,
            owner_id,
            writer_attempt,
            writer_identity,
            model_map,
            dispatch,
        )
        return result.final_state
    except asyncio.CancelledError as exc:
        persist_failure(
            exc, HALT_REASON_TASK_CANCELLED_BETWEEN_CARTRIDGES
        )
        raise
    except Exception as exc:
        # Fence-persist a HALTED state for a genuine (authoritative) execution
        # failure.  Only the *current owner's* error may transition the run to
        # HALTED; a stale/non-owner generation (ExecutionOwnershipError) must
        # never mark a run it does not own as failed, so that case is skipped.
        persist_failure(exc, HALT_REASON_TASK_FAILED_BETWEEN_CARTRIDGES)
        raise


async def run_magazine(
    magazine_path: str,
    state_dir: str,
    owner_id: str,
    model_map: dict,
    dispatch,
) -> dict:
    """Admit and run a Magazine workflow using an injected dispatch callable.

    A genuine mid-run execution failure is fence-persisted as ``HALTED``
    (the run transitioned ``RUNNING -> HALTED`` and the execution claim
    augmented) before the original exception is re-raised.  A stale, non-owner
    generation never marks the run it does not own as ``HALTED``.
    """
    prepared = run_preparation.prepare_run(magazine_path, state_dir, owner_id)
    status = prepared.state["status"]

    if status == STATUS_RUNNING:
        return await _run_cartridges_or_halt(
            magazine_path,
            prepared.state_path,
            prepared.lock_path,
            owner_id,
            prepared.attempt,
            prepared.identity,
            model_map,
            dispatch,
        )

    if status in (STATUS_COMPLETED, STATUS_HALTED):
        return prepared.state

    raise RuntimeError(
        f"unexpected run status {status!r}; expected {STATUS_RUNNING!r}, "
        f"{STATUS_COMPLETED!r}, or {STATUS_HALTED!r}"
    )


async def run_magazine_open_webui(
    magazine_path: str,
    state_dir: str,
    owner_id: str,
    base_url: str,
    api_key: str,
    model_map: dict,
    tool_ids: list | tuple | None = None,
) -> dict:
    """Run Magazine using the Open WebUI fresh-chat dispatch adapter.

    *tool_ids* is an optional sequence of Open WebUI tool DB IDs already
    selected by the surrounding harness/operator.  It is forwarded to the
    dispatch factory and appears in each completion request as ``tool_ids``;
    Open WebUI resolves them into the tools available in each fresh chat.
    Magazine does not discover, provision, inspect or manage tools.  When
    omitted, no tool IDs are sent and the previous behaviour is preserved.
    """
    dispatch = make_open_webui_dispatch(base_url, api_key, tool_ids=tool_ids)
    return await run_magazine(
        magazine_path=magazine_path,
        state_dir=state_dir,
        owner_id=owner_id,
        model_map=model_map,
        dispatch=dispatch,
    )


async def run_dispatch(
    magazine_path: str,
    state_path: str,
    lock_path: str,
    owner_id: str,
    prepared: PreparedRun,
    model_map: dict,
    dispatch,
) -> dict:
    """Run an already-admitted Magazine workflow using an injected dispatch
    callable.

    The admission (and the run lock) were performed by the caller; this
    function does no admission and acquires no lock.  It branches only on the
    freshly admitted status carried by ``prepared``: if RUNNING it dispatches
    all remaining cartridges (via :func:`magazine.run_loop.run_all_cartridges`
    against the once-derived ``state_path`` / ``lock_path``); if terminal it
    returns the prepared state unchanged.  On any other status it raises
    ``RuntimeError``.

    A genuine mid-run execution failure is fence-persisted as ``HALTED``
    (the run transitioned ``RUNNING -> HALTED`` and the execution claim
    augmented) before the original exception is re-raised.  A stale, non-owner
    generation never marks the run it does not own as ``HALTED``.
    """
    status = prepared.state["status"]

    if status == STATUS_RUNNING:
        return await _run_cartridges_or_halt(
            magazine_path,
            state_path,
            lock_path,
            owner_id,
            prepared.attempt,
            prepared.identity,
            model_map,
            dispatch,
        )

    if status in (STATUS_COMPLETED, STATUS_HALTED):
        return prepared.state

    raise RuntimeError(
        f"unexpected run status {status!r}; expected {STATUS_RUNNING!r}, "
        f"{STATUS_COMPLETED!r}, or {STATUS_HALTED!r}"
    )


async def run_dispatch_open_webui(
    magazine_path: str,
    state_path: str,
    lock_path: str,
    owner_id: str,
    prepared: PreparedRun,
    base_url: str,
    api_key: str,
    model_map: dict,
    tool_ids: list | tuple | None = None,
) -> dict:
    """Run an already-admitted Magazine workflow using the Open WebUI
    fresh-chat dispatch adapter.

    The admission (and the run lock) were performed by the caller; this
    function does no admission and acquires no lock.  *tool_ids* is an
    optional sequence of Open WebUI tool DB IDs already selected by the
    surrounding harness/operator; it is forwarded to the dispatch factory and
    appears in each completion request as ``tool_ids``.  When omitted, no tool
    IDs are sent.
    """
    dispatch = make_open_webui_dispatch(base_url, api_key, tool_ids=tool_ids)
    return await run_dispatch(
        magazine_path=magazine_path,
        state_path=state_path,
        lock_path=lock_path,
        owner_id=owner_id,
        prepared=prepared,
        model_map=model_map,
        dispatch=dispatch,
    )
