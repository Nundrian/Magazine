"""Guarded persistence of an uncertain-run halt.

Explicit recovery actions must safely persist:

    RUNNING -> HALTED
    HALTED  -> READY
    READY   -> READY (claim retirement after owner process loss)

without racing another Magazine process.  Each recovery action acquires the
existing run lock **before** loading or changing persisted state, so the
state is re-read under the lock and never trusted from an earlier
startup-inspection result.

The exact high-level flow is:

    acquire_run_lock(lock_path, owner_id)
    try:
        state = load_run_state(state_path)
        halted = halt_uncertain_run(state)
        save_run_state(state_path, halted)
        return halted
    finally:
        release_run_lock(lock_path, owner_id)

Nothing here implements stale-lock deletion, stale-lock detection, lock
stealing, polling, resume behaviour, dispatch, dispatch intent, queue
advancement, or automatic continuation.  The ``HALTED -> READY`` path retires
the previous claim as ``operator-recovered``.  An eligible extinct owner's
``READY`` claim is retired as ``lost-owner-ready`` without rewriting state.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, replace

from magazine.execution_meta import (
    HALT_REASON_LOST_OWNER,
    HALT_REASON_LOST_OWNER_READY,
    HALT_REASON_OPERATOR_RECOVERED,
    load_execution_metadata,
    save_execution_metadata,
    utc_now_iso,
)
from magazine.paths import execution_file_path
from magazine import run_control
from magazine.recovery_transition import halt_uncertain_run, recover_halted_run
from magazine.run_action import resolve_runtime_identity
from magazine.run_lock import (
    MagazineLockError,
    acquire_run_lock,
    release_run_lock,
)
from magazine.runtime_identity import (
    RuntimeIdentity,
    process_is_extinct,
)
from magazine.schema import ExecutionOwnershipError
from magazine.state import STATUS_READY, STATUS_RUNNING
from magazine.state_store import load_run_state, save_run_state

__all__ = [
    "RecoverOutcome",
    "persist_error_halt",
    "persist_recover_halted",
    "persist_uncertain_halt",
    "recover_lost_runtime",
]


@dataclass(frozen=True)
class RecoverOutcome:
    """Result of attempting to recover a lost runtime.

    ``outcome`` is one of:

    * ``"halted"`` -- the caller is a new generation; the run is now ``HALTED``
      (state.json and execution.json updated under the run lock).  ``state`` is
      the freshly persisted ``HALTED`` state; ``claim`` is the frozen replaced
      claim (the old owner's identity, ``halt_reason = lost-owner``).
    * ``"ready_recovered"`` -- the old owner is positively extinct and its
      READY claim was retired, or an earlier retirement already persisted.
      The READY state was not rewritten; explicit admission may now proceed.
    * ``"claim_retired"`` -- the READY claim was previously retired for a
      different reason and was left untouched.
    * ``"no_claim"`` -- no execution.json.  No ownership evidence exists;
      recovery makes no change and never invents an owner.
      ``state`` / ``claim`` are both ``None``.
    * ``"same_runtime"`` -- the claim's ``runtime_id`` equals the writer's
      ``runtime_id``: the writer *is* the current runtime.  Nothing to recover.
      ``state`` is the freshly-loaded state.json; ``claim`` is the claim.
    * ``"owner_live"`` -- the revalidated claim's recorded process incarnation
      is still alive, or its liveness is ambiguous.  Nothing is changed.
    * ``"locked"`` -- the run lock could not be acquired (a live executor holds
      it).  The run is left completely untouched.  ``state`` / ``claim`` are
      the pre-lock snapshots.
    * ``"state_changed"`` -- the re-check under the lock found an ineligible or
      unexpected status.  ``state`` is the re-read state; ``claim`` is ``None``.
    * ``"claim_changed"`` -- the re-check under the lock found the exact claim
      differs from the pre-lock snapshot.  ``state`` is the re-read state;
      ``claim`` is the re-read claim.
    """
    outcome: str
    state: dict | None
    claim: ExecutionMetadata | None


def persist_uncertain_halt(
    state_path: str,
    lock_path: str,
    owner_id: str,
    runtime_id: str | None = None,
    pid: int | None = None,
    start_ticks: int | None = None,
) -> dict:
    """Persist the ``RUNNING -> HALTED`` transition for an uncertain run.

    The run lock is acquired first and the persisted state is only loaded
    after the lock is held.  The loaded state is transitioned with
    :func:`magazine.recovery_transition.halt_uncertain_run` and the result is
    persisted atomically.  The same lock is released after a successful
    persistence, and is also released if loading, the transition, or the
    persistence raises.

    Args:
        state_path: Path to the persisted run state file.
        lock_path: Path to the run lock file.
        owner_id: Owner ID passed unchanged to lock acquisition and release.
        runtime_id:
            Optional runtime id of the process performing the uncertain halt.
            When all three of ``runtime_id`` / ``pid`` / ``start_ticks`` are
            given they are threaded verbatim to lock acquisition/release;
            otherwise the current process's live identity is used (same
            all-or-nothing rule as :func:`magazine.run_action.resolve_runtime_identity`).
        pid:
            Optional pid for the all-or-nothing identity resolution.
        start_ticks:
            Optional ``/proc`` start tick for the all-or-nothing identity
            resolution.

    Returns:
        The exact ``HALTED`` state that was successfully persisted.

    Raises:
        :class:`magazine.run_lock.MagazineLockError`: if the lock cannot be
            acquired (e.g. it is already held) or cannot be released.
        :class:`magazine.state.MagazineStateError`: if the persisted state is
            malformed (propagated from :func:`load_run_state`).
        :class:`magazine.recovery_transition.RecoveryTransitionError`: if the
            loaded state is valid but not ``RUNNING`` (e.g. ``READY``,
            ``COMPLETED``, or ``HALTED``).

    The lock is always released in a ``finally`` block, so the caller never
    holds the lock after a successful or failed call.  If the lock already
    exists, acquisition fails through the existing lock primitive and the
    persisted state is left untouched.
    """
    # 1. Acquire the existing run lock first, passing owner_id unchanged.
    # Resolve the uncertain-halt writer's identity first (all-or-nothing, mirroring
    # :func:`magazine.run_action.resolve_runtime_identity`).  Identity resolution
    # is cheap and never reads the run state, so it happens before the lock is
    # taken.  It is threaded to lock acquisition/release as the process-incarnation
    # evidence for the run lock.
    identity = resolve_runtime_identity(runtime_id, pid, start_ticks)

    # 1. Acquire the existing run lock first, passing owner_id unchanged and the
    #    resolved identity (runtime_id / pid / start_ticks).
    acquire_run_lock(
        lock_path,
        owner_id,
        identity.runtime_id,
        identity.pid,
        identity.start_ticks,
    )
    try:
        # 2. Only load persisted state after the lock is acquired.
        state = load_run_state(state_path)
        # 3. Transform with the pure recovery transition.
        halted = halt_uncertain_run(state)
        # 4. Persist atomically.
        save_run_state(state_path, halted)
        # 5. Return the exact HALTED state that was successfully persisted.
        return halted
    finally:
        # 6. Release the same lock with the same owner_id and identity, whether
        #    the persistence succeeded or any step raised.
        release_run_lock(
            lock_path,
            owner_id,
            identity.runtime_id,
            identity.pid,
            identity.start_ticks,
        )


def persist_recover_halted(
    state_path: str,
    lock_path: str,
    owner_id: str,
    runtime_id: str | None = None,
    pid: int | None = None,
    start_ticks: int | None = None,
) -> dict:
    """Persist the ``HALTED -> READY`` recovery for an explicitly halted run.

    The run lock is acquired first and the persisted state is only loaded
    after the lock is held.  The loaded state is transitioned with
    :func:`magazine.recovery_transition.recover_halted_run` and the result is
    persisted atomically.  The same lock is released after a successful
    persistence, and is also released if loading, the transition, or the
    persistence raises.

    The recovered run is re-admissible: it is persisted as ``READY`` with
    ``current`` cleared to ``None`` and the interrupted cartridge moved to the
    front of the ``todo`` queue, and the run's previous execution claim is
    retired (a frozen replacement with the recovering runtime's identity and
    ``operator-recovered``) so the normal public runner
    (:func:`magazine.async_runner.run_magazine`) re-admits it through the
    existing admission path (:func:`magazine.run_action.persist_admit_run`),
    which begins the interrupted cartridge.

    Args:
        state_path: Path to the persisted run state file.
        lock_path: Path to the run lock file.
        owner_id: Owner ID passed unchanged to lock acquisition and release.
        runtime_id:
            Optional runtime id of the recovering process.  When all three of
            ``runtime_id`` / ``pid`` / ``start_ticks`` are given the retired
            claim's ``halted_by`` is that id verbatim; otherwise the current
            process's live identity is used (same all-or-nothing rule as
            :func:`magazine.run_action.resolve_runtime_identity`).
        pid:
            Optional pid for the all-or-nothing identity resolution.
        start_ticks:
            Optional ``/proc`` start tick for the all-or-nothing identity
            resolution.

    Returns:
        The exact ``READY`` state that was successfully persisted.

    Raises:
        :class:`magazine.run_lock.MagazineLockError`: if the lock cannot be
            acquired (e.g. it is already held) or cannot be released.
        :class:`magazine.state.MagazineStateError`: if the persisted state is
            malformed (propagated from :func:`load_run_state`).
        :class:`magazine.recovery_transition.RecoveryTransitionError`: if the
            loaded state is valid but not ``HALTED`` (e.g. ``READY``,
            ``COMPLETED`` or ``RUNNING``), or if the HALTED state has no
            ``current`` cartridge.
        :class:`magazine.schema.ExecutionOwnershipError`: if the durable claim
            retirement fails re-validation (never expected for a valid existing
            claim) or the atomic write fails (e.g. an ``OSError`` from
            :func:`magazine.durability.atomic_write`).  On this failure the
            ``READY`` state is never written, so the state remains HALTED and
            operator recovery stays retryable.

    The lock is always released in a ``finally`` block, so the caller never
    holds the lock after a successful or failed call.  If the lock already
    exists, acquisition fails through the existing lock primitive and the
    persisted state is left untouched.
    """
    # Resolve the recovering runtime's identity first (all-or-nothing, mirroring
    # :func:`magazine.run_action.resolve_runtime_identity`).  Identity resolution
    # is cheap and never reads the run state, so it happens before the lock is
    # taken.  It is used only to augment the retired claim below.
    identity = resolve_runtime_identity(runtime_id, pid, start_ticks)

    # 1. Acquire the existing run lock first, passing owner_id unchanged and the
    #    resolved identity (runtime_id / pid / start_ticks).
    acquire_run_lock(
        lock_path,
        owner_id,
        identity.runtime_id,
        identity.pid,
        identity.start_ticks,
    )
    try:
        # 2. Only load persisted state after the lock is acquired.
        state = load_run_state(state_path)
        # 3. Transform with the pure recovery transition.
        recovered = recover_halted_run(state)
        # 4. Load and retire the previous execution claim (if any) BEFORE
        #    writing the state.  Retirement is conditional: a run can halt
        #    without a claim (a HALTED state may be written directly; ``persist_begin_next_cartridge``
        #    never creates one), so ``load_execution_metadata`` can return
        #    ``None`` -- in which case there is simply nothing to retire and
        #    the no-claim recovery proceeds unchanged.  When a claim exists it
        #    is never deleted: we construct a frozen replacement via
        #    ``dataclasses.replace`` that preserves the same ``attempt`` and
        #    ``runtime_id`` and augments the ``halt_*`` fields with the
        #    recovering runtime's identity and ``operator-recovered``.  This is
        #    exactly the shape the admission path's pre-start-abort retirement
        #    uses, and it is what makes the recovered run re-admissible to
        #    :func:`magazine.run_action.persist_admit_run`.
        run_dir = os.path.dirname(state_path)
        exec_path = execution_file_path(run_dir)
        existing_claim = load_execution_metadata(exec_path)
        if existing_claim is not None:
            retired = replace(
                existing_claim,
                halted_by=identity.runtime_id,
                halted_at=utc_now_iso(),
                halt_reason=HALT_REASON_OPERATOR_RECOVERED,
            )
            # Crash-consistency: the retirement MUST be durable before the READY
            # commit, and its failure MUST propagate.  Swallowing a failed
            # retirement would strand the run as READY + unretired claim, which
            # the strict admission gate then refuses, and the HALTED recovery
            # could no longer be retried.  Propagating instead leaves the state
            # durably HALTED, so operator recovery remains retryable.  (On any
            # retirement failure the READY state is never written below.)
            save_execution_metadata(exec_path, retired)
        # 5. Only after a successful claim retirement (or no claim at all)
        #    persist the HALTED -> READY transition atomically.
        save_run_state(state_path, recovered)
        # 6. Return the exact READY state that was successfully persisted.
        return recovered
    finally:
        # 7. Release the same lock with the same owner_id and identity, whether
        #    the persistence succeeded or any step raised.
        release_run_lock(
            lock_path,
            owner_id,
            identity.runtime_id,
            identity.pid,
            identity.start_ticks,
        )


def persist_error_halt(
    state_path: str,
    lock_path: str,
    owner_id: str,
    writer_attempt: int,
    writer_identity: RuntimeIdentity,
    error: BaseException,
    ready_retire_reason: str | None = None,
) -> dict:
    """Persist the fenced failure state for a run owned by this task.

    This is the automated, fenced counterpart to :func:`persist_uncertain_halt`.
    It is invoked by the execution entrypoint (the async runner) after an
    authoritative execution -- i.e. an execution that still holds the current
    execution-ownership claim -- raises or is cancelled.  Under the **same**
    run-lock hold, the execution claim is re-loaded from ``execution.json``
    and must match the writer's exact ownership identity (``attempt`` +
    ``runtime_id``).  A ``RUNNING`` state becomes ``HALTED`` and records the
    error.  When ``ready_retire_reason`` is supplied, a genuine inter-cartridge
    ``READY`` state with ``current=None`` stays byte-for-byte unchanged while
    its claim is retired with that explicit reason.

    A stale/non-owner generation (same ``owner_id`` but a different
    ``attempt`` or ``runtime_id``) fails the claim check and raises
    :class:`magazine.schema.ExecutionOwnershipError` **without** halting --
    it can never mark a run it does not own as HALTED.  The state is left
    untouched in that case.

    Args:
        state_path: Path to the persisted run state file.
        lock_path: Path to the run lock file.
        owner_id: Owner ID passed unchanged to lock acquisition and release.
        writer_attempt: The execution-attempt number the run's claim must
            carry.
        writer_identity: The runtime identity whose ``runtime_id`` the run's
            claim must carry.
        error: The exception raised by the authoritative execution.  Its
            ``repr()`` is recorded as the claim's ``halt_reason``.
        ready_retire_reason: Explicit claim-retirement reason permitted only
            when the state is ``READY`` with no cartridge in flight.

    Returns:
        The exact ``HALTED`` state that was successfully persisted, or the
        unchanged ``READY`` state whose claim was successfully retired.

    Raises:
        :class:`magazine.run_lock.MagazineLockError`: if the lock cannot be
            acquired (e.g. it is already held) or cannot be released.
        :class:`magazine.schema.ExecutionOwnershipError`: if the execution
            claim is missing, malformed, or its ``attempt`` / ``runtime_id``
            do not match the writer.  A stale/non-owner generation is refused
            and the state is left untouched.
        :class:`magazine.state.MagazineStateError`: if the persisted state is
            malformed (propagated from :func:`load_run_state`).
        :class:`magazine.recovery_transition.RecoveryTransitionError`: if the
            loaded state is valid but not ``RUNNING`` (e.g. ``READY``,
            ``COMPLETED`` or ``HALTED``).

    Crash-consistency order: the ``HALTED`` state is committed *before* the
    claim augmentation.  If the claim augmentation fails (and thus the error
    is not recorded), the run is still durably ``HALTED`` and operator
    recovery (:func:`persist_recover_halted`) remains retryable.

    The lock is always released in a ``finally`` block, so the caller never
    holds the lock after a successful or failed call.
    """
    # 1. Acquire the existing run lock first, passing owner_id unchanged.
    # 1. Acquire the existing run lock first, passing owner_id unchanged and the
    #    writer's ownership identity (runtime_id / pid / start_ticks).
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

        # 4a. A task may fail after a completed cartridge was durably committed
        #     as READY but before the next cartridge entered RUNNING.  Only the
        #     verified current owner may retire that claim, and only when no
        #     cartridge is in flight.  The READY state itself is deliberately
        #     not rewritten: done/todo/results/history remain exactly intact.
        if state["status"] == STATUS_READY and ready_retire_reason is not None:
            if state["current"] is not None:
                raise ExecutionOwnershipError(
                    "cannot retire READY execution claim while a cartridge is "
                    "recorded as current"
                )
            retired_claim = replace(
                existing_claim,
                halted_by=writer_identity.runtime_id,
                halted_at=utc_now_iso(),
                halt_reason=ready_retire_reason,
            )
            save_execution_metadata(exec_path, retired_claim)
            return state

        # 4b. Otherwise transition RUNNING -> HALTED.  The pure transition
        #     validates that the state is RUNNING and raises for any other
        #     status.
        halted = halt_uncertain_run(state)

        # 5. Persist the HALTED state atomically FIRST (crash-consistency):
        #    even if the claim augmentation below fails, the run is durably
        #    HALTED so operator recovery stays retryable.
        save_run_state(state_path, halted)

        # 6. Augment the claim with the error information (frozen replace,
        #    preserving attempt and runtime_id).  The claim augmentation MUST
        #    be durable and its failure MUST propagate so the run is not left
        #    in an ambiguous state; on failure the HALTED state remains.
        halted_claim = replace(
            existing_claim,
            halted_by=writer_identity.runtime_id,
            halted_at=utc_now_iso(),
            halt_reason=repr(error),
        )
        save_execution_metadata(exec_path, halted_claim)

        # A failed inference can overtake a pending Stop request. That is an
        # execution failure, not proof that Stop succeeded. The sole exception
        # is an acknowledged cancellation followed by CancelledError: the
        # Stop endpoint must still confirm termination after the task exits.
        control = run_control.load_stop_control(state_path)
        confirmed_cancel = (
            isinstance(error, asyncio.CancelledError)
            and control is not None
            and control["cancellation_status"] == "CONFIRMED"
        )
        if not confirmed_cancel:
            run_control._settle_not_applied_locked(
                state_path,
                ("task-cancelled-without-confirmed-stop"
                 if isinstance(error, asyncio.CancelledError)
                 else "execution-failed"),
            )

        # 7. Return the exact HALTED state that was successfully persisted.
        return halted
    finally:
        # 8. Release the same lock with the same owner_id and identity on every path.
        release_run_lock(
            lock_path,
            owner_id,
            writer_identity.runtime_id,
            writer_identity.pid,
            writer_identity.start_ticks,
        )


def recover_lost_runtime(
    state_path: str,
    lock_path: str,
    owner_id: str,
    runtime_id: str | None = None,
    pid: int | None = None,
    start_ticks: int | None = None,
    *,
    expected_status: str | None = None,
) -> RecoverOutcome:
    """Attempt to recover a run whose owning runtime is no longer alive.

    This is the recovery path used by the runner's ``POST /start`` RUNNING
    interception and by explicit operator recovery of an abandoned READY run.
    The caller pins ``expected_status`` when invoked through either route, so
    a concurrent status change cannot turn one operation into the other.
    It is conservative and never invents ownership evidence:
    a run without an ``execution.json`` claim is left untouched and reported as
    ``no_claim``.  A RUNNING run is transitioned to ``HALTED`` only under the
    run lock.  An eligible READY run stays READY and only its claim is retired.

    The algorithm (per the execution-ownership design, Slice 4):

    1. Read ``execution.json``.  If there is no claim, return
       ``RecoverOutcome("no_claim", None, None)``.
    2. If ``claim.runtime_id`` equals the writer's ``runtime_id`` (the caller
       *is* the owning runtime), return
       ``RecoverOutcome("same_runtime", state, claim)``, where ``state`` is the
       freshly-loaded ``state.json``.
    3. Otherwise the caller is a *new generation*.  Attempt to take the run
       lock:
       a. Acquire the lock with the writer's identity (owner_id, runtime_id,
          pid, start_ticks).  If acquisition raises ``MagazineLockError`` (a
          live executor holds the lock), return
          ``RecoverOutcome("locked", state, claim)``.  Do *not* touch the run
          state.
       b. Re-check ``state.json`` under the lock.  Require RUNNING or READY,
          and require ``expected_status`` when supplied; otherwise return
          ``RecoverOutcome("state_changed", state, None)``.
       c. Re-check the entire ``execution.json`` claim under the lock.  If it
          differs from the pre-lock snapshot, return ``claim_changed``.
       d. Check the revalidated claim's recorded ``pid`` / ``start_ticks``.
          Unless that exact process incarnation is positively extinct, return
          ``RecoverOutcome("owner_live", state, claim)`` without modification.
       e. For READY, durably retire an active claim with the distinct
          ``lost-owner-ready`` reason and leave state.json untouched.
          A previously persisted retirement with the same reason is returned
          unchanged, making retries safe after an ambiguous write outcome.
       f. For RUNNING, halt: persist ``RUNNING -> HALTED`` with
          :func:`magazine.recovery_transition.halt_uncertain_run` and atomically
          save ``state.json``.
       g. Retire the RUNNING claim (frozen replacement via :func:`dataclasses.replace`,
          preserving ``attempt`` and ``runtime_id`` and setting ``halted_by`` =
          the writer's ``runtime_id``, ``halted_at`` = now, ``halt_reason`` =
          ``"lost-owner"``) and durably save ``execution.json``.
       h. Return ``RecoverOutcome("halted", new_state, retired_claim)``.

    The lock is always released in a ``finally`` block, so the caller never
    holds it after this returns.

    Args:
        state_path: Path to the persisted run state file.
        lock_path: Path to the run lock file.
        owner_id: Owner ID passed unchanged to lock acquisition and release.
        runtime_id:
            Optional runtime id of the recovering (calling) process.  When all
            three of ``runtime_id`` / ``pid`` / ``start_ticks`` are given they
            are threaded verbatim to lock acquisition/release and used for the
            same-runtime comparison; otherwise the current process's live
            identity is used (same all-or-nothing rule as
            :func:`magazine.run_action.resolve_runtime_identity`).
        pid:
            Optional pid for the all-or-nothing identity resolution.
        start_ticks:
            Optional ``/proc`` start tick for the all-or-nothing identity
            resolution.
        expected_status:
            Optional route-specific state fence.  ``/start`` supplies RUNNING;
            explicit READY recovery supplies READY.  ``None`` permits either
            state for direct recovery callers.

    Returns:
        A :class:`RecoverOutcome` with ``outcome`` in one of ``halted``,
        ``ready_recovered``, ``claim_retired``, ``no_claim``, ``same_runtime``,
        ``owner_live``, ``locked``, ``state_changed`` or ``claim_changed``,
        plus the relevant ``state`` / ``claim``.
    """
    # Resolve the recovering (writing) runtime's identity first (all-or-nothing,
    # mirrors :func:`magazine.run_action.resolve_runtime_identity`).  Identity
    # resolution is cheap and never reads the run state, so it happens before
    # the lock is taken.  It drives the same-runtime comparison and is threaded
    # to lock acquisition/release.
    writer_identity = resolve_runtime_identity(runtime_id, pid, start_ticks)

    # --- Pre-lock, read-only fast paths (conservative; no state touched) ---
    run_dir = os.path.dirname(state_path)
    exec_path = execution_file_path(run_dir)

    # Step 1. Read the execution claim.  No claim means no ownership evidence:
    #         fail conservatively (no auto-HALT, no invented owner).
    claim_before = load_execution_metadata(exec_path)
    if claim_before is None:
        return RecoverOutcome("no_claim", None, None)

    # Step 2. Load the state and check whether the caller is the owning runtime.
    state_before = load_run_state(state_path)
    if claim_before.runtime_id == writer_identity.runtime_id:
        # The caller *is* the current runtime: nothing to recover.
        return RecoverOutcome("same_runtime", state_before, claim_before)

    # Step 3. Different runtime: the writer is a new generation.  Attempt to
    #         take the run lock with the writer's identity.
    try:
        acquire_run_lock(
            lock_path,
            owner_id,
            writer_identity.runtime_id,
            writer_identity.pid,
            writer_identity.start_ticks,
        )
    except MagazineLockError:
        # A live executor holds the run lock.  Do NOT touch the run.
        return RecoverOutcome("locked", state_before, claim_before)

    try:
        # Step 3b. Re-check the status under the lock (TOCTOU-safe).
        state = load_run_state(state_path)
        if state.get("status") not in (STATUS_RUNNING, STATUS_READY) or (
            expected_status is not None and state["status"] != expected_status
        ):
            # Something else changed the run while we were not holding the lock.
            return RecoverOutcome("state_changed", state, None)

        # Step 3c. Re-check the claim under the lock.
        claim_now = load_execution_metadata(exec_path)
        if claim_now != claim_before:
            return RecoverOutcome("claim_changed", state, claim_now)

        # Step 3d. A different runtime id and an available short-held run lock
        #           are not evidence that the execution owner died: model
        #           dispatch intentionally runs without that lock.  Retire only
        #           with positive extinction evidence for the exact revalidated
        #           process incarnation.  Ambiguous evidence fails closed via
        #           process_is_extinct returning False.
        if not process_is_extinct(claim_now.pid, claim_now.start_ticks):
            return RecoverOutcome("owner_live", state, claim_now)

        if state["status"] == STATUS_READY:
            # A previous READY retirement may have completed its atomic write
            # but failed before confirmation.  Recheck extinction even on a
            # retry; never report successful recovery for a live owner.
            if claim_now.halt_reason is not None:
                if claim_now.halt_reason == HALT_REASON_LOST_OWNER_READY:
                    return RecoverOutcome("ready_recovered", state, claim_now)
                return RecoverOutcome("claim_retired", state, claim_now)
            retired_claim = replace(
                claim_now,
                halted_by=writer_identity.runtime_id,
                halted_at=utc_now_iso(),
                halt_reason=HALT_REASON_LOST_OWNER_READY,
            )
            # No state write is needed.  If this atomic claim write fails before
            # commit, the original active claim remains available for retry.
            # If it lands before a later failure, the reason is re-admissible.
            save_execution_metadata(exec_path, retired_claim)
            return RecoverOutcome("ready_recovered", state, retired_claim)

        # Step 3e. Halt: persist RUNNING -> HALTED (crash-consistency: the state
        #           commit is the first durable step).
        halted = halt_uncertain_run(state)
        save_run_state(state_path, halted)

        # Step 3f. Retire the claim (frozen replacement, preserving attempt and
        #           runtime_id) with the lost-owner reason.  Its failure MUST
        #           propagate so the run stays durably HALTED and the operator
        #           recovery path (:func:`persist_recover_halted`) stays retryable.
        retired_claim = replace(
            claim_now,
            halted_by=writer_identity.runtime_id,
            halted_at=utc_now_iso(),
            halt_reason=HALT_REASON_LOST_OWNER,
        )
        save_execution_metadata(exec_path, retired_claim)

        # Step 3g. Return the freshly persisted HALTED state and the retired
        #           claim.
        return RecoverOutcome("halted", halted, retired_claim)
    finally:
        release_run_lock(
            lock_path,
            owner_id,
            writer_identity.runtime_id,
            writer_identity.pid,
            writer_identity.start_ticks,
        )
