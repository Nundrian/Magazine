"""Stage-4 single-cartridge orchestration boundary for Magazine.

Performs exactly ONE safe ``dispatch -> complete`` pair and stops. It is the
smallest composition a Stage-4 caller (or a future execution loop) can use to
advance the run by a single cartridge, with no loop, no retry, no recovery and
no advancement to a second cartridge.

Composition sequence:

  A. Await ``magazine.execute.dispatch_current_cartridge``.  It reads the
     authoritative persisted state, validates ``RUNNING``, selects the cartridge
     matching the persisted ``current`` ID and dispatches it.  This step is
     the *only* state read in this module; the result carries the exact
     ``cartridge_id`` that was selected and dispatched.

  B. *Only after* the dispatch has returned without raising, complete the
     SAME cartridge by calling
     ``magazine.complete.complete_current_cartridge`` with
     ``expected_current_id`` set to ``dispatched.cartridge_id``.  The guarded
     completion action acquires the run lock, re-reads the state under the
     lock, verifies the identity of the current cartridge, applies the
     ``RUNNING -> READY`` / ``RUNNING -> COMPLETED`` transition, increments the
     revision, atomically saves, and releases the lock on every path.  It
     returns the exact persisted post-completion state.

  C. Return a ``OneCartridgeResult`` bundling the dispatched cartridge ID, the
     runtime dispatch result (unchanged) and the post-completion state.

Invariants:

* This module performs ZERO independent state reads.  It never determines the
  ``current`` cartridge itself; it relies entirely on
  ``dispatch_current_cartridge`` to select the cartridge and hand back the
  authoritative ``cartridge_id``.

* ``cartridge_id`` identifies the cartridge that was actually selected and
  dispatched.  It is also the ``expected_current_id`` forwarded to the guarded
  completion boundary, which compares it against the freshly-loaded ``current``
  under the run lock.

* After a successful completion the returned ``state`` is the *post-completion*
  state.  Because completion clears the in-flight cartridge, ``state["current"]``
  will normally be ``None`` (and the run status is ``READY`` when work remains
  or ``COMPLETED`` when this was the last cartridge).

* ``lock_path`` and ``owner_id`` are merely forwarded to the guarded completion
  boundary.  This orchestration function itself holds NO run lock.  Lock
  acquisition/release is confined entirely inside
  :func:`magazine.complete.complete_current_cartridge`.

* A dispatch failure (transport or semantic) raises and naturally prevents
  completion from being called at all.  A completion failure propagates
  unchanged.  No ``try/except``, no retry, no automatic recovery, no
  ``begin_next_cartridge`` and no second cartridge are involved.

Nothing here dispatches, begins the next cartridge, adds a loop, or changes
any transition or locking API.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from magazine import complete
from magazine import execute

__all__ = ["OneCartridgeResult", "execute_one_cartridge"]


@dataclass(frozen=True)
class OneCartridgeResult:
    """Result of dispatching and completing exactly one cartridge.

    Fields:
        cartridge_id: The exact cartridge ID actually selected and dispatched
            by ``magazine.execute.dispatch_current_cartridge`` (and used as the
            ``expected_current_id`` for its guarded completion).
        dispatch_result: The runtime dispatch result, returned unchanged.
        state: The exact post-completion state dict returned by
            ``magazine.complete.complete_current_cartridge``.
    """

    cartridge_id: str
    dispatch_result: Any
    state: dict


async def execute_one_cartridge(
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
) -> OneCartridgeResult:
    """Dispatch and complete exactly one cartridge.

    Composes the Stage-2 dispatch boundary and the Stage-3 guarded
    completion boundary into a single atomic-from-the-caller's-view pair:
    dispatch, then complete the *same* cartridge, then return.  It performs
    no independent state read of its own and never determines the current
    cartridge — ``dispatch_current_cartridge`` selects it authoritatively and
    hands back the ``cartridge_id``, which is then used as the completion
    identity check.

    Args:
        magazine_path: Path to the Magazine JSON document (forwarded to the
            dispatch boundary).
        state_path: Path to the persisted run state file.  Read once, inside
            the dispatch boundary, to select the cartridge; re-read *only*
            under the lock inside the guarded completion boundary.
        lock_path: Path to the run lock file (forwarded to the guarded
            completion boundary; this function holds no lock).
        owner_id: Owner ID written into the run lock (forwarded unchanged to
            the guarded completion boundary).
        base_url: Base URL of the Open WebUI instance.
        api_key: Bearer API key.
        tool_role: Key into *tool_map* to look up the Tool ID.
        model_map: Mapping of job types to model IDs.
        tool_map: Mapping of tool roles to Tool IDs.
        dispatch: Async callable accepting a *prompt* string.

    Returns:
        A :class:`OneCartridgeResult` bundling:
        * ``cartridge_id`` — the exact ID selected and dispatched,
        * ``dispatch_result`` — the runtime dispatch result, unchanged,
        * ``state`` — the exact post-completion state persisted by
          :func:`magazine.complete.complete_current_cartridge` (after a
          successful completion, ``state["current"]`` is normally ``None``
          because the in-flight cartridge has been cleared).

    Raises:
        ValueError: If the persisted status is not ``RUNNING`` at dispatch
            time, or if no cartridge in the Magazine document has an ``id``
            equal to the persisted ``current`` (from
            :func:`magazine.execute.dispatch_current_cartridge`).
        FileNotFoundError: If the Magazine document cannot be read (from the
            dispatch boundary).
        magazine.state.MagazineStateError: If the persisted state is malformed
            (propagated from either boundary).
        magazine.schema.MagazineSchemaError: If the Magazine document is
            invalid (from the dispatch boundary).
        KeyError / TypeError: If routing (model or tool lookup) fails for the
            dispatched cartridge's ``job_type`` / *tool_role*.
        ValueError: If the runtime pair preflight fails (from the dispatch
            boundary); dispatch must raise on transport/semantic failure and
            any normal return is treated as a successful dispatch.
        magazine.run_lock.MagazineLockError: If the run lock cannot be
            acquired or released (from the completion boundary).
        magazine.run_transition.RunTransitionError: If the state loaded under
            the lock is valid but not ``RUNNING`` (from the completion
            boundary).

    Note:
        The dispatch must raise on transport/semantic failure; a normal
        return is the signal that the dispatch succeeded and the run may
        proceed to complete.  ``cartridge_id`` identifies the cartridge that
        was dispatched, and it is forwarded verbatim as
        ``expected_current_id`` so the guarded completion verifies it against
        the freshly-loaded ``current`` under the lock.  After a successful
        completion, ``state["current"]`` is normally ``None`` because ``state``
        is the post-completion state.  ``lock_path`` and ``owner_id`` are
        merely forwarded to the guarded completion boundary; this orchestration
        function itself does not hold the run lock.
    """
    # A. Dispatch the run's current cartridge (the sole state read in this
    #    module; it also selects the cartridge and returns its ID).
    dispatched = await execute.dispatch_current_cartridge(
        magazine_path,
        state_path,
        base_url,
        api_key,
        tool_role,
        model_map,
        tool_map,
        dispatch,
    )

    # B. Complete the SAME cartridge.  Only reached if the dispatch returned
    #    without raising.  Locking and state re-read are confined entirely
    #    inside this guarded action; expected_current_id is the exact ID that
    #    dispatch selected.
    completed_state = complete.complete_current_cartridge(
        state_path,
        lock_path,
        owner_id,
        expected_current_id=dispatched.cartridge_id,
    )

    # C. Bundle and return the single-cartridge result.
    return OneCartridgeResult(
        cartridge_id=dispatched.cartridge_id,
        dispatch_result=dispatched.dispatch_result,
        state=completed_state,
    )
