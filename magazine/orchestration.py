"""One-cartridge orchestration: dispatch -> durable result -> completion."""

from __future__ import annotations

import os
from dataclasses import dataclass
from magazine import complete, execute, result_store, run_control
from magazine.execution_meta import load_execution_metadata
from magazine.paths import execution_file_path
from magazine.run_lock import acquire_run_lock, release_run_lock
from magazine.run_transition import RunTransitionError
from magazine.runtime_identity import RuntimeIdentity
from magazine.schema import ExecutionOwnershipError
from magazine.state import STATUS_RUNNING
from magazine.state_store import load_run_state

__all__ = ["OneCartridgeResult", "execute_one_cartridge"]


@dataclass(frozen=True)
class OneCartridgeResult:
    cartridge_id: str
    dispatch_result: str
    state: dict


async def execute_one_cartridge(
    magazine_path: str,
    state_path: str,
    lock_path: str,
    owner_id: str,
    writer_attempt: int,
    writer_identity: RuntimeIdentity,
    model_map: dict,
    dispatch,
) -> OneCartridgeResult:
    """Dispatch one current cartridge, save its result, then complete it.

    Slice 2 execution-ownership fence: before any dispatch, a fresh run-lock
    hold re-loads the execution claim from ``execution.json`` and verifies it
    matches this writer's exact ownership identity (``writer_attempt`` +
    ``writer_identity``) and that the run status is ``RUNNING``.  A
    stale/non-owner generation (same ``owner_id``, a different ``attempt``
    or ``runtime_id``) is refused with
    :class:`magazine.schema.ExecutionOwnershipError` before dispatching.  The
    result store and the completion stage are likewise fenced under the same
    ownership rule.
    """
    run_dir = os.path.dirname(state_path) or os.getcwd()
    exec_path = execution_file_path(run_dir)

    # Slice 2 entry check (TOCTOU): under a fresh run-lock hold, verify the
    # execution claim matches this writer's exact ownership identity and that
    # the run is RUNNING before any dispatch can occur.  A stale/non-owner
    # generation (same owner_id, a new process generation) is refused here
    # before it can dispatch a cartridge.
    acquire_run_lock(
        lock_path,
        owner_id,
        writer_identity.runtime_id,
        writer_identity.pid,
        writer_identity.start_ticks,
    )
    try:
        existing_claim = load_execution_metadata(exec_path)
        if existing_claim is None:
            raise ExecutionOwnershipError(
                "cannot verify execution ownership: no claim present in "
                "execution.json"
            )
        if (
            existing_claim.attempt != writer_attempt
            or existing_claim.runtime_id != writer_identity.runtime_id
        ):
            raise ExecutionOwnershipError(
                f"execution ownership claim mismatch: claim "
                f"attempt={existing_claim.attempt!r} "
                f"runtime_id={existing_claim.runtime_id!r} "
                f"does not match writer attempt={writer_attempt!r} "
                f"runtime_id={writer_identity.runtime_id!r}"
            )

        state = load_run_state(state_path)
        if state["status"] != STATUS_RUNNING:
            raise RunTransitionError(
                f"cannot dispatch: run status {state['status']!r} is not "
                f"{STATUS_RUNNING!r}; only an in-progress run can be "
                f"dispatched"
            )
        if run_control.load_stop_control(state_path) is not None:
            # The caller's pre-dispatch check and this fence deliberately
            # overlap. This in-lock check closes the request/check race: once
            # Stop is durable, no new cartridge can pass this point.
            stop_seen = True
        else:
            stop_seen = False
    finally:
        release_run_lock(
            lock_path,
            owner_id,
            writer_identity.runtime_id,
            writer_identity.pid,
            writer_identity.start_ticks,
        )

    if stop_seen:
        stopped = run_control.persist_stop_if_requested(
            state_path, lock_path, owner_id, writer_attempt, writer_identity,
            method="boundary-before-dispatch",
        )
        raise run_control.StopRequested(stopped)

    dispatched = await execute.dispatch_current_cartridge(
        magazine_path, state_path, model_map, dispatch
    )

    result_store.save_cartridge_result(
        run_dir,
        dispatched.cartridge_id,
        dispatched.dispatch_result,
        lock_path,
        owner_id,
        writer_attempt,
        writer_identity,
    )

    # The result is now durably on disk.  Only now attempt to remove the
    # temporary Open WebUI chat this dispatch created.  This is best-effort
    # and deliberately non-fatal: the result is already persisted, and a
    # cleanup failure must never invalidate it or corrupt the run state.  The
    # generic dispatch boundary is otherwise untouched - non-Open-WebUI dispatch
    # callables simply lack the ``cleanup`` attribute and are skipped.
    cleanup = getattr(dispatch, "cleanup", None)
    if cleanup is not None:
        try:
            await cleanup()
        except Exception:
            # A failed cleanup must not abort this cartridge's completion.
            pass

    completed_state = complete.complete_current_cartridge(
        state_path,
        lock_path,
        owner_id,
        writer_attempt,
        writer_identity,
        expected_current_id=dispatched.cartridge_id,
    )
    return OneCartridgeResult(
        cartridge_id=dispatched.cartridge_id,
        dispatch_result=dispatched.dispatch_result,
        state=completed_state,
    )
