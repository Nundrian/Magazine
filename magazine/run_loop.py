"""Stage-5 bounded multi-cartridge loop for Magazine.

Repeatedly composes the already-proven Stage-4 single-cartridge orchestration
boundary (:func:`magazine.orchestration.execute_one_cartridge`) with the
existing guarded ``begin-next`` action
(:func:`magazine.run_action.persist_begin_next_cartridge`), advancing an
admitted run's ``todo`` queue one cartridge at a time until the run reaches
``COMPLETED``.

Composition sequence (the exact loop body, repeated until completion):

  1. Await ``magazine.orchestration.execute_one_cartridge``.  This performs
     exactly one ``dispatch -> complete`` pair and returns a
     ``OneCartridgeResult`` bundling the post-completion state.

  2. Inspect *only* that returned ``state``:
       * ``COMPLETED``  -> the run is finished; return
         ``MultiCartridgeResult(final_state=result.state)``, no further action.
       * ``READY``      -> work remains; persist the guarded ``READY ->
         RUNNING`` ``begin-next`` transition via
         ``run_action.persist_begin_next_cartridge`` and repeat the loop.
       * anything else -> raise ``RuntimeError``; the post-completion state
         must never be any status other than ``COMPLETED`` or ``READY``.

  3. Return ``MultiCartridgeResult`` carrying exactly the final ``COMPLETED``
     state.

Because each full iteration permanently moves exactly one cartridge from
``todo`` into ``done``, a run with a finite ``todo`` queue is bounded and the
loop terminates in at most that many iterations, returning once the last
cartridge completes.

Invariants:

* This module performs ZERO independent persisted-state reads.  It never calls
  ``magazine.state_store.load_run_state``, ``magazine.startup_inspection``,
  ``magazine.entrypoint``, or any other state-read helper.  The only state the
  loop observes is ``result.state`` — the exact post-completion state returned
  by :func:`magazine.orchestration.execute_one_cartridge`.  The ``READY ->
  RUNNING`` transition is performed exclusively by the guarded
  :func:`magazine.run_action.persist_begin_next_cartridge`, which acquires the
  run lock, re-reads the persisted state under the lock, validates it, and
  persists atomically.

* This loop itself NEVER acquires the run lock.  The external dispatch happens
  only inside :func:`magazine.orchestration.execute_one_cartridge` (via
  ``magazine.execute.dispatch_current_cartridge``) and the guarded completion
  only inside ``magazine.complete.complete_current_cartridge``; neither holds
  the run lock across the external dispatch.  Lock acquisition/release is
  confined entirely to the existing guarded completion and ``begin-next``
  actions.

* The ``begin-next`` step must remain the authoritative ``READY -> RUNNING``
  transition.  When ``result.state`` is ``READY`` the loop does not assume the
  persisted state is still ``READY`` when ``begin-next`` runs;
  :func:`magazine.run_action.persist_begin_next_cartridge` re-reads the state
  under the run lock and performs the transition, so a state changed by another
  process in the gap raises the existing guarded action's error naturally.
  This failure is propagated, never caught or converted, and no further
  cartridge is dispatched.

* There is no retry and no recovery logic.  Any exception from
  :func:`magazine.orchestration.execute_one_cartridge` (dispatch failure,
  ``RunIdentityError``, completion transition failure, state/schema/routing/
  preflight failure, lock failure) propagates immediately and the ``begin-next``
  step is never reached.  Any exception from
  :func:`magazine.run_action.persist_begin_next_cartridge` propagates immediately
  and no further cartridge is dispatched.  No ``try/except`` is used.

* Long-run memory behaviour: only the latest ``OneCartridgeResult`` is ever
  held in scope for one loop iteration, and it is never appended to any list
  or accumulator.  After each iteration only its post-completion ``state``
  matters, so the loop's memory use is bounded and constant across an
  arbitrarily long run.

Single-runner contract (IMPORTANT — read before use):

* This loop assumes exactly ONE admitted active runner for the run.
* It does NOT provide concurrent-runner admission by itself.
* Two concurrent direct callers against the same ``RUNNING``/``current`` state
  can both dispatch the same external cartridge, because
  ``magazine.execute.dispatch_current_cartridge`` reads ``RUNNING``/``current``
  without the run lock and the external dispatch occurs without the run lock.
* ``expected_current_id`` protects against completing the *wrong* cartridge
  later, but it does NOT prevent the duplicate external dispatch.
* Caller/entrypoint admission will be handled separately after this loop is
  implemented and tested.
* This function must NOT be described as multi-process or concurrent-runner
  safe.

Nothing here dispatches on its own, begins a cartridge without the guarded
action, adds any lock or recovery logic, or changes any transition or locking
API.
"""

from __future__ import annotations

from dataclasses import dataclass

from magazine import orchestration
from magazine import run_action
from magazine.state import STATUS_COMPLETED, STATUS_READY

__all__ = ["MultiCartridgeResult", "run_all_cartridges"]


@dataclass(frozen=True)
class MultiCartridgeResult:
    """Result of running the full bounded multi-cartridge loop.

    Fields:
        final_state: The exact ``COMPLETED`` state persisted when the run
            reached its final cartridge.  Only this state is retained; the
            loop never accumulates per-cartridge ``OneCartridgeResult``
            objects, ``dispatch_result`` objects, or a list of cartridge
            results.
    """

    final_state: dict


async def run_all_cartridges(
    magazine_path: str,
    state_path: str,
    lock_path: str,
    owner_id: str,
    base_url: str,
    api_key: str,
    tool_role: str,
    model_map: dict,
    tool_map: dict,
    dispatch,
) -> MultiCartridgeResult:
    """Advance an admitted run through its remaining cartridges until ``COMPLETED``.

    Repeatedly composes :func:`magazine.orchestration.execute_one_cartridge`
    (one ``dispatch -> complete`` pair) with :func:`magazine.run_action
    .persist_begin_next_cartridge` (guarded ``READY -> RUNNING``), one cartridge
    at a time, and returns :class:`MultiCartridgeResult` carrying the final
    ``COMPLETED`` state.

    Precondition:
        The persisted run is already ``RUNNING`` with a ``current`` cartridge.
        This function does NOT perform startup recovery or admission; it assumes
        the caller has already admitted the run (single active runner).

    Args:
        magazine_path: Path to the Magazine JSON document (forwarded to the
            single-cartridge orchestration).
        state_path: Path to the persisted run state file.
        lock_path: Path to the run lock file (forwarded to the guarded
            completion and ``begin-next`` actions; this loop holds no lock).
        owner_id: Owner ID written into the run lock (forwarded unchanged to the
            guarded actions).
        base_url: Base URL of the Open WebUI instance.
        api_key: Bearer API key.
        tool_role: Key into *tool_map* to look up the Tool ID.
        model_map: Mapping of job types to model IDs.
        tool_map: Mapping of tool roles to Tool IDs.
        dispatch: Async callable accepting a *prompt* string.

    Returns:
        A :class:`MultiCartridgeResult` whose ``final_state`` is the exact
        ``COMPLETED`` state persisted when the last cartridge was completed.

    Raises:
        RuntimeError: If any post-completion state returned by
            :func:`magazine.orchestration.execute_one_cartridge` has a status
            that is neither ``COMPLETED`` nor ``READY`` (an invalid post-
            completion state).
        Exception: Any exception raised by
            :func:`magazine.orchestration.execute_one_cartridge` (dispatch
            failure, ``RunIdentityError``, completion transition failure,
            state/schema/routing/preflight failure, lock failure) or by
            :func:`magazine.run_action.persist_begin_next_cartridge` propagates
            immediately and unchanged.  No ``begin-next`` or further cartridge
            is dispatched after a failure.

    Note:
        This loop performs ZERO independent persisted-state reads and NEVER
        acquires the run lock.  It observes only ``result.state`` returned by
        :func:`magazine.orchestration.execute_one_cartridge` and delegates the
        ``READY -> RUNNING`` transition entirely to the guarded
        :func:`magazine.run_action.persist_begin_next_cartridge`, which re-reads
        the state under the run lock.  The external dispatch occurs only inside
        :func:`magazine.orchestration.execute_one_cartridge` and is never
        performed while the run lock is held.  See the module docstring for the
        single-runner contract.
    """
    while True:
        # One bounded step: exactly one dispatch -> complete pair.
        result = await orchestration.execute_one_cartridge(
            magazine_path,
            state_path,
            lock_path,
            owner_id,
            base_url,
            api_key,
            tool_role,
            model_map,
            tool_map,
            dispatch,
        )

        # Observe ONLY the post-completion state carried by the result.
        status = result.state["status"]

        if status == STATUS_COMPLETED:
            return MultiCartridgeResult(final_state=result.state)

        if status != STATUS_READY:
            raise RuntimeError(
                f"unexpected post-completion run status {status!r}; expected "
                f"{STATUS_COMPLETED!r} (final run) or "
                f"{STATUS_READY!r} (work remains)"
            )

        # Work remains (READY).  Persist the guarded READY -> RUNNING transition
        # for the next cartridge.  This guarded action acquires the run lock,
        # re-reads the persisted state under the lock, validates it, and
        # persists atomically.  If another process changed the state in the gap
        # since the completion, this action raises its natural error, which is
        # propagated (not caught or converted) and no further cartridge is
        # dispatched.
        run_action.persist_begin_next_cartridge(state_path, lock_path, owner_id)
