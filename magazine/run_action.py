"""Guarded persistence of the run-cartridge transitions.

Explicit run actions must safely persist:

    READY -> RUNNING        (begin_next_cartridge)
    RUNNING -> READY        (complete_current_cartridge, when work remains)
    RUNNING -> COMPLETED    (complete_current_cartridge, when no work remains)

and, separately, must admit a first run atomically: when no state file exists
yet, create one as a fresh ``READY`` and transition that same ``READY`` to
``RUNNING`` — without racing another Magazine process.  Each action acquires
the existing run lock **before** loading or changing persisted state, so the
state is re-read under the lock and never trusted from an earlier
startup-inspection result.

The exact high-level flow is the same for the first two actions:

    acquire_run_lock(
        lock_path,
        owner_id,
        writer_identity.runtime_id,
        writer_identity.pid,
        writer_identity.start_ticks,
    )
    try:
        state = load_run_state(state_path)
        next_state = <transition>(state)
        save_run_state(state_path, next_state)
        return next_state
    finally:
        release_run_lock(
            lock_path,
            owner_id,
            writer_identity.runtime_id,
            writer_identity.pid,
            writer_identity.start_ticks,
        )

Nothing here implements stale-lock deletion, stale-lock detection, PID or
process checks, lock stealing, retries, polling, resume behaviour, dispatch,
dispatch intent, queue advancement, automatic continuation, timestamps, or
backup rotation.
"""

from __future__ import annotations

import os

from dataclasses import replace

from magazine.execution_meta import (
    ExecutionMetadata,
    HALT_REASON_OPERATOR_RECOVERED,
    HALT_REASON_PRE_START_ABORTED,
    load_execution_metadata,
    save_execution_metadata,
    utc_now_iso,
)
from magazine.paths import execution_file_path
from magazine.schema import ExecutionOwnershipError
from magazine.runtime_identity import RuntimeIdentity, get_runtime_identity
from magazine.run_lock import acquire_run_lock, release_run_lock
from magazine.run_transition import (
    RunTransitionError,
    begin_next_cartridge,
    complete_current_cartridge,
)
from magazine.state import (
    STATUS_READY,
    STATUS_RUNNING,
    STATUS_COMPLETED,
    STATUS_HALTED,
    new_ready_state,
)
from magazine.state_store import load_run_state, save_run_state

__all__ = [
    "RunAdmissionError",
    "RunIdentityError",
    "resolve_runtime_identity",
    "persist_admit_run",
    "persist_begin_next_cartridge",
    "persist_complete_current_cartridge",
]


class RunIdentityError(RunTransitionError):
    """Raised when the run's current cartridge, freshly loaded under the run
    lock, is not the cartridge the caller expected to complete (i.e. the ID
    whose dispatch just succeeded).

    The persisted state is left untouched and the lock is released, so the
    caller sees a visible failure instead of a silently-wrong completion.
    """


class RunAdmissionError(RunTransitionError):
    """Raised when admission finds an already-RUNNING persisted run."""


def resolve_runtime_identity(
    runtime_id: str | None,
    pid: int | None,
    start_ticks: int | None,
) -> RuntimeIdentity:
    """Resolve the process identity used for the execution claim.

    All-or-nothing: when every one of ``runtime_id``, ``pid`` and ``start_ticks``
    is supplied the caller's identity is used verbatim (a full ``RuntimeIdentity``);
    otherwise the live process identity from ``magazine.runtime_identity`` is
    used.  A *partial* set is not accepted; the identity is resolved exactly
    once and applied to the whole run.

    Exposed so the shared preparation boundary (:mod:`magazine.run_preparation`)
    can hand the *same* identity to :func:`persist_admit_run` and record it in
    the :class:`PreparedRun` it returns.
    """
    if runtime_id is not None and pid is not None and start_ticks is not None:
        return RuntimeIdentity(
            runtime_id=runtime_id,
            pid=pid,
            start_ticks=start_ticks,
        )
    return get_runtime_identity()


def persist_begin_next_cartridge(
    state_path: str,
    lock_path: str,
    owner_id: str,
    writer_attempt: int,
    writer_identity: RuntimeIdentity,
) -> dict:
    """Persist the ``READY -> RUNNING`` transition for the next cartridge.

    The run lock is acquired first and the persisted state is only loaded
    after the lock is held.  The loaded state is transitioned with
    :func:`magazine.run_transition.begin_next_cartridge` and the result is
    persisted atomically.  The same lock is released after a successful
    persistence, and is also released if loading, the transition, or the
    persistence raises.

    Args:
        state_path: Path to the persisted run state file.
        lock_path: Path to the run lock file.
        owner_id: Owner ID passed unchanged to lock acquisition and release.

    Returns:
        The exact ``RUNNING`` state that was successfully persisted.

    Raises:
        :class:`magazine.run_lock.MagazineLockError`: if the lock cannot be
            acquired (e.g. it is already held) or cannot be released.
        :class:`magazine.schema.ExecutionOwnershipError`: if the execution claim
            is missing, malformed, or its ``attempt`` / ``runtime_id`` do not
            match the writer.  A stale/non-owner generation is refused.
        :class:`magazine.state.MagazineStateError`: if the persisted state is
            malformed (propagated from :func:`load_run_state`).
        :class:`magazine.run_transition.RunTransitionError`: if the loaded
            state is valid but a cartridge cannot be begun (e.g. ``RUNNING``,
            ``COMPLETED``, ``HALTED``, or ``READY`` with an empty ``todo``).

    The lock is always released in a ``finally`` block, so the caller never
    holds the lock after a successful or failed call.  If the lock already
    exists, acquisition fails through the existing lock primitive and the
    persisted state is left untouched.
    """
    # 1. Acquire the existing run lock first, passing owner_id unchanged.
    acquire_run_lock(
        lock_path,
        owner_id,
        writer_identity.runtime_id,
        writer_identity.pid,
        writer_identity.start_ticks,
    )
    try:
        run_dir = os.path.dirname(state_path)
        exec_path = execution_file_path(run_dir)

        # 2. Claim check first (fail closed): the claim must exist and match
        #    the writer's exact ownership identity.  This is the fence; it is
        #    independent of the owner-based lock and rejects a stale
        #    generation even though it shares the same owner_id.
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

        # 3. Only load persisted state after the lock is held.
        state = load_run_state(state_path)
        # 4. Transform with the pure run transition.
        running = begin_next_cartridge(state)
        # 5. Persist atomically.
        save_run_state(state_path, running)
        # 6. Return the exact RUNNING state that was successfully persisted.
        return running
    finally:
        # 7. Release the same lock with the same owner_id, whether the
        #    persistence succeeded or any step raised.
        release_run_lock(
            lock_path,
            owner_id,
            writer_identity.runtime_id,
            writer_identity.pid,
            writer_identity.start_ticks,
        )


def persist_complete_current_cartridge(
    state_path: str,
    lock_path: str,
    owner_id: str,
    writer_attempt: int,
    writer_identity: RuntimeIdentity,
    expected_current_id: str | None = None,
) -> dict:
    """Persist the ``RUNNING -> READY`` (or ``COMPLETED``) transition for the
    current cartridge.

    The run lock is acquired first and the persisted state is only loaded
    after the lock is held.  The loaded state is transitioned with
    :func:`magazine.run_transition.complete_current_cartridge` and the result
    is persisted atomically.  The same lock is released after a successful
    persistence, and is also released if loading, the transition, or the
    persistence raises.

    Args:
        state_path: Path to the persisted run state file.
        lock_path: Path to the run lock file.
        owner_id: Owner ID passed unchanged to lock acquisition and release.
        writer_attempt: The execution-attempt number the run's claim must carry.
        writer_identity: The runtime identity whose ``runtime_id`` the run's
            claim must carry.
        expected_current_id: Optional ID of the cartridge that was actually
            dispatched before this completion is recorded.  When ``None``
            (the default) no identity check is performed and behaviour is
            identical to before.  When set, the freshly-loaded ``current``
            is compared against it under the lock before the transition; if
            they differ, :class:`RunIdentityError` is raised and the state is
            left untouched.

    Returns:
        The exact ``READY`` (or ``COMPLETED``) state that was successfully
        persisted.

    Raises:
        :class:`magazine.run_lock.MagazineLockError`: if the lock cannot be
            acquired (e.g. it is already held) or cannot be released.
        :class:`magazine.schema.ExecutionOwnershipError`: if the execution claim
            is missing, malformed, or its ``attempt`` / ``runtime_id`` do not
            match the writer.  A stale/non-owner generation is refused.
        :class:`magazine.state.MagazineStateError`: if the persisted state is
            malformed (propagated from :func:`load_run_state`).
        :class:`magazine.run_action.RunIdentityError`: if a non-``None``
            ``expected_current_id`` differs from the freshly-loaded
            ``current``.  The persisted state is left untouched and the lock
            is released.
        :class:`magazine.run_transition.RunTransitionError`: if the loaded
            state is valid but a cartridge cannot be completed (e.g. any
            non-``RUNNING`` status such as ``READY``, ``COMPLETED``, or
            ``HALTED``).

    The lock is always released in a ``finally`` block, so the caller never
    holds the lock after a successful or failed call.  If the lock already
    exists, acquisition fails through the existing lock primitive and the
    persisted state is left untouched.
    """
    # 1. Acquire the existing run lock first, passing owner_id unchanged.
    acquire_run_lock(
        lock_path,
        owner_id,
        writer_identity.runtime_id,
        writer_identity.pid,
        writer_identity.start_ticks,
    )
    try:
        run_dir = os.path.dirname(state_path)
        exec_path = execution_file_path(run_dir)

        # 2. Claim check first (fail closed): the claim must exist and match
        #    the writer's exact ownership identity.  This is the fence; it is
        #    independent of the owner-based lock and rejects a stale
        #    generation even though it shares the same owner_id.
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

        # 3. Only load persisted state after the lock is held.
        state = load_run_state(state_path)
        # 4. Identity guard: the freshly-loaded ``current`` must match the
        #    caller's expected cartridge ID before anything is completed.
        #    A mismatch raises before the transition and before any save, so
        #    the state is left untouched and no wrong cartridge is marked done.
        if expected_current_id is not None and state["current"] != expected_current_id:
            raise RunIdentityError(
                f"cannot complete: expected current "
                f"{expected_current_id!r} but the run's current is "
                f"{state['current']!r}"
            )
        # 4. Transform with the pure run transition.
        completed = complete_current_cartridge(state)
        # 5. Persist atomically.
        save_run_state(state_path, completed)
        # 6. Return the exact completed state that was successfully persisted.
        return completed
    finally:
        # 7. Release the same lock with the same owner_id, whether the
        #    persistence succeeded or any step raised.
        release_run_lock(
            lock_path,
            owner_id,
            writer_identity.runtime_id,
            writer_identity.pid,
            writer_identity.start_ticks,
        )


def persist_admit_run(
    state_path: str,
    lock_path: str,
    owner_id: str,
    cartridge_ids: list[str],
    runtime_id: str | None = None,
    pid: int | None = None,
    start_ticks: int | None = None,
) -> tuple[dict, int]:
    """Admit a first run atomically under one run-lock hold (Slice 1 execution
    ownership: claim-first ordering).

    The run lock is acquired **before** checking whether the state file
    exists.  While that single lock is held the function:

    * creates the state file as a fresh ``READY`` (via
      :func:`magazine.state.new_ready_state`) and persists it, only when the
      file is missing, then continues with the ``READY -> RUNNING`` admission
      below; or
    * transitions an already-existing ``READY`` file to ``RUNNING`` and
      persists it; or
    * returns an existing ``COMPLETED`` or ``HALTED`` file unchanged (no
      re-save, no transition); or
    * raises :class:`RunAdmissionError` when the freshly-loaded state is
      already ``RUNNING``.  The caller must never join or resume a run that
      is already owned; the existing explicit recovery path is the only way
      to restart an interrupted run.

    Slice 1 claim-first ordering, under the single held lock:

      1. Create a fresh ``READY`` state if the state file is missing.
      2. Load the authoritative run state.
      3. Require ``READY``; on ``READY`` run the claim-existence gate:
           - no claim (fresh unowned READY) -> attempt 1;
           - an exact pre-start-aborted claim (halt_reason ==
             "pre-start-aborted") -> attempt == claim.attempt + 1;
           - any *other* existing execution claim -> raise
             :class:`RunAdmissionError` conservatively (never take over an
             already-owned run).
      4. Persist ``execution.json`` (the durable execution claim) **before**
         the ``READY -> RUNNING`` commit.
      5. Commit ``READY -> RUNNING`` (:func:`magazine.run_transition.
         begin_next_cartridge` + state save) and return the exact ``RUNNING``
         state with the persisted attempt number.
      6. On a commit failure, re-read the state while still holding the lock
         and retire the claim (construct a frozen replacement with the aborting
         runtime's identity and ``halt_reason == "pre-start-aborted"``)
         **only if** the state is positively still ``READY`` (the commit truly
         did not persist).  A valid execution claim is never silently deleted;
         on any other outcome the (still valid) claim is left in place so no
         state is ever left ``RUNNING`` without a durable owner.
      7. Release the lock (always, on every path).

    The same lock spans the initial ``READY`` creation, the execution claim
    persistence, and the ``READY -> RUNNING`` commit, so a second normal
    runner arriving after the first admitted and released its short admission
    lock is refused instead of silently overwriting the running state.  This
    function performs no dispatch, no retry, no polling, no stale-lock or
    recovery behaviour, and holds no lock across any external work; the lock
    is always released in a ``finally`` block on every path.

    Args:
        state_path: Path to the persisted run state file.
        lock_path: Path to the run lock file.
        owner_id: Owner ID passed unchanged to lock acquisition and release.
        cartridge_ids: Cartridge ID sequence used to build the fresh ``READY``
            state when the state file does not yet exist.
        runtime_id: The runtime's process identity, or ``None``.  When every
            one of ``runtime_id``, ``pid`` and ``start_ticks`` is supplied the
            caller's identity is used verbatim as a ``RuntimeIdentity``;
            otherwise the live process identity from ``magazine.runtime_identity``
            is used.  Defaults to the live process identity (all three omitted).

    Returns:
        A ``(state, attempt)`` tuple.  ``state`` is the exact persisted run
        state (fresh/existing ``READY`` advanced to ``RUNNING``, or a terminal
        ``COMPLETED`` / ``HALTED`` state returned unchanged).  ``attempt`` is
        the execution attempt persisted to ``execution.json`` for a freshly
        admitted run, or ``0`` when no claim was created by this call (terminal
        ``COMPLETED`` / ``HALTED`` run).

    Raises:
        :class:`magazine.run_lock.MagazineLockError`: if the lock cannot be
            acquired (e.g. it is already held) or cannot be released.
        :class:`magazine.state.MagazineStateError`: if the persisted state is
            malformed (propagated from :func:`load_run_state`).
        :class:`magazine.run_transition.RunTransitionError`: if the loaded
            ``READY`` state cannot be begun (e.g. an empty ``todo``).
        :class:`RunAdmissionError`: if the freshly-loaded state is already
            ``RUNNING``; or if the freshly-loaded ``READY`` state carries an
            existing claim that is *not* an exact pre-start-aborted one (a
            live/owned claim must never be re-claimed).  A fresh unowned
            ``READY`` (no claim) and a ``READY`` carrying an exact
            pre-start-aborted claim are both admitted.  The state is left
            untouched and the lock is released.

    The lock is always released in a ``finally`` block, so the caller never
    holds the lock after a successful or failed call.  If the lock already
    exists, acquisition fails through the existing lock primitive and the
    persisted state is left untouched.
    """
    identity = resolve_runtime_identity(runtime_id, pid, start_ticks)

    # 1. Acquire the existing run lock first, passing owner_id unchanged --
    #    BEFORE checking whether the state file exists.
    acquire_run_lock(
        lock_path,
        owner_id,
        identity.runtime_id,
        identity.pid,
        identity.start_ticks,
    )
    try:
        # 2. First-run admission: create the state file as a fresh ``READY``
        #    only when it does not yet exist.
        if not os.path.exists(state_path):
            ready = new_ready_state(cartridge_ids)
            save_run_state(state_path, ready)

        # 3. Load (and validate) the persisted state while still holding the
        #    SAME lock, then branch on its freshly-loaded status.
        persisted = load_run_state(state_path)
        status = persisted["status"]

        if status == STATUS_READY:
            # 4. Existing/fresh READY admission: run the claim-existence gate
            #    and create the durable execution claim **before** the
            #    READY -> RUNNING commit (claim-first ordering).
            run_dir = os.path.dirname(state_path)
            exec_path = execution_file_path(run_dir)
            existing_claim = load_execution_metadata(exec_path)

            # Claim-existence gate (authoritative Slice 1 invariant):
            #   * fresh unowned READY (no claim)              -> attempt 1.
            #   * a re-admissible retired claim (pre-start-ABORTED
            #     or operator-recovered)                     -> attempt N+1.
            #   * any *other* existing execution claim        -> REJECT: never
            #     take over a run that is already owned (e.g. the live claim
            #     of an in-progress run seen during an inter-cartridge READY
            #     window, or a claim that was never retired).
            if existing_claim is None:
                next_attempt = 1
            elif existing_claim.halt_reason in (
                HALT_REASON_PRE_START_ABORTED,
                HALT_REASON_OPERATOR_RECOVERED,
            ):
                next_attempt = existing_claim.attempt + 1
            else:
                raise RunAdmissionError(
                    f"run is already claimed (previous attempt "
                    f"{existing_claim.attempt!r}, halt_reason="
                    f"{existing_claim.halt_reason!r}); only the "
                    "pre-start-aborted and operator-recovered reasons may be "
                    "re-claimed"
                )

            # 5. Persist the execution claim durably BEFORE the RUNNING commit.
            claim = ExecutionMetadata(
                attempt=next_attempt,
                runtime_id=identity.runtime_id,
                pid=identity.pid,
                start_ticks=identity.start_ticks,
                created_at=utc_now_iso(),
                halted_by=None,
                halted_at=None,
                halt_reason=None,
            )
            save_execution_metadata(exec_path, claim)

            try:
                # 6. Commit READY -> RUNNING under the same lock and return the
                #    exact RUNNING state with the persisted attempt number.
                running = begin_next_cartridge(persisted)
                save_run_state(state_path, running)
                return running, next_attempt
            except Exception:
                # The RUNNING commit did not land (or the state is no longer
                # readable).  Re-read while still holding the lock and retire
                # the claim only if the state is positively still READY (the
                # commit truly did not persist).  A valid execution claim is
                # never silently deleted; the abort is recorded with the
                # aborting runtime's identity and the abort reason (never a
                # free-form label).
                is_still_ready = False
                try:
                    reread = load_run_state(state_path)
                    is_still_ready = reread["status"] == STATUS_READY
                except Exception:
                    # Unreadable/missing state cannot be positively confirmed as
                    # READY; leave the (still valid) claim in place (safe) and
                    # re-raise the original commit failure below.
                    is_still_ready = False
                if is_still_ready:
                    aborted = replace(
                        claim,
                        halted_by=identity.runtime_id,
                        halted_at=utc_now_iso(),
                        halt_reason=HALT_REASON_PRE_START_ABORTED,
                    )
                    try:
                        save_execution_metadata(exec_path, aborted)
                    except Exception:
                        # Abort-marking failed: leave the (still valid) claim in
                        # place so no state is left RUNNING without a durable
                        # owner.  Re-raise the original commit failure below.
                        pass
                raise

        if status == STATUS_COMPLETED:
            # Terminal: return the loaded state unchanged; no claim is created
            # (no admission, no re-save, no transition).
            return persisted, 0

        if status == STATUS_HALTED:
            # Terminal: return the loaded state unchanged; no claim is created
            # (no admission, no re-save, no transition).
            return persisted, 0

        if status == STATUS_RUNNING:
            # A second normal runner arriving after the first admitted and
            # released its short admission lock: refuse to join or resume,
            # and require the existing explicit recovery path.
            raise RunAdmissionError(
                "run is already RUNNING; use the existing explicit recovery "
                "path rather than joining or resuming it"
            )

    finally:
        # 7. Release the same lock with the same owner_id on every path.
        release_run_lock(
            lock_path,
            owner_id,
            identity.runtime_id,
            identity.pid,
            identity.start_ticks,
        )

