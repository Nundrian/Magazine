"""Guarded persistence of an uncertain-run halt.

Explicit recovery actions must safely persist:

    RUNNING -> HALTED
    HALTED  -> READY

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
stealing, retries, polling, resume behaviour, dispatch, dispatch intent, queue
advancement, or automatic continuation.  Beyond the two recovery transitions this
module also retires the run's previous execution claim on the ``HALTED -> READY``
path (a frozen replacement with the recovering runtime's identity and
``operator-recovered``); the ``RUNNING -> HALTED`` path does not touch the claim.
"""

from __future__ import annotations

import os
from dataclasses import replace

from magazine.execution_meta import (
    HALT_REASON_OPERATOR_RECOVERED,
    load_execution_metadata,
    save_execution_metadata,
    utc_now_iso,
)
from magazine.paths import execution_file_path
from magazine.recovery_transition import halt_uncertain_run, recover_halted_run
from magazine.run_action import resolve_runtime_identity
from magazine.run_lock import acquire_run_lock, release_run_lock
from magazine.runtime_identity import RuntimeIdentity
from magazine.schema import ExecutionOwnershipError
from magazine.state_store import load_run_state, save_run_state

__all__ = [
    "persist_error_halt",
    "persist_recover_halted",
    "persist_uncertain_halt",
]


def persist_uncertain_halt(state_path: str, lock_path: str, owner_id: str) -> dict:
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
    acquire_run_lock(lock_path, owner_id)
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
        # 6. Release the same lock with the same owner_id, whether the
        #    persistence succeeded or any step raised.
        release_run_lock(lock_path, owner_id)


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

    # 1. Acquire the existing run lock first, passing owner_id unchanged.
    acquire_run_lock(lock_path, owner_id)
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
        # 7. Release the same lock with the same owner_id, whether the
        #    persistence succeeded or any step raised.
        release_run_lock(lock_path, owner_id)


def persist_error_halt(
    state_path: str,
    lock_path: str,
    owner_id: str,
    writer_attempt: int,
    writer_identity: RuntimeIdentity,
    error: BaseException,
) -> dict:
    """Persist the ``RUNNING -> HALTED`` transition for a run that raised.

    This is the automated, fenced counterpart to :func:`persist_uncertain_halt`.
    It is invoked by the execution entrypoint (the async runner) after an
    authoritative execution -- i.e. an execution that still holds the current
    execution-ownership claim -- raises during a cartridge dispatch.  Under
    the **same** run-lock hold: the execution claim is re-loaded from
    ``execution.json`` and must match the writer's exact ownership identity
    (``attempt`` + ``runtime_id``); the run state must be ``RUNNING``; the
    ``RUNNING -> HALTED`` transition is applied; and the claim is augmented
    with ``halted_by``, ``halted_at`` and ``halt_reason`` (the ``repr()`` of
    the error).

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

    Returns:
        The exact ``HALTED`` state that was successfully persisted.

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
    acquire_run_lock(lock_path, owner_id)
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

        # 4. Transition RUNNING -> HALTED.  The pure transition validates that
        #    the state is RUNNING and raises RecoveryTransitionError otherwise.
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

        # 7. Return the exact HALTED state that was successfully persisted.
        return halted
    finally:
        # 8. Release the same lock with the same owner_id on every path.
        release_run_lock(lock_path, owner_id)
