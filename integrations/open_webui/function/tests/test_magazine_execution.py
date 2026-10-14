"""Runner execution-boundary tests (native OWUI event-function boundary).

Covers the START gate (READY -> accepted) and the native owner-scoped RESULTS
retrieval path, using the REAL embedded core's own persistence — no mocks.
``magazine.state_store`` persists the run state and ``magazine.result_store``
persists the canonical per-cartridge result files; both are exercised under a
monkeypatched per-test DATA_DIR so every test is isolated and deterministic.

This lives in a separate file from ``test_magazine_runner.py`` on purpose:
``test_magazine_runner.py`` is a fast, core-free suite (admission / validation
helpers), whereas the execution boundary necessarily loads the embedded core
via ``_mag()`` and touches real files.
"""

import asyncio
import os

import pytest
from unittest.mock import patch

import magazine_ef as mfr

OWNER_A = "owner-a"
OWNER_B = "owner-b"
RID = "rid123"


@pytest.fixture(scope="module")
def mag():
    """Load the embedded core once for the module (idempotent)."""
    m = mfr._mag()
    assert m.get("ok"), "failed to load embedded core: %r" % m.get("error")
    return m


@pytest.fixture
def root(mag, monkeypatch, tmp_path):
    """Isolated DATA_DIR/magazine/runs per test via monkeypatched _runs_root."""
    runs_root = os.path.join(str(tmp_path), "magazine", "runs")
    monkeypatch.setattr(mfr, "_runs_root", lambda: runs_root)
    return runs_root


_TEST_RUNTIME_ID = "0" * 32
_TEST_PID = 12345
_TEST_START_TICKS = 999
_TEST_WRITER_ATTEMPT = 1


def _make_writer(mag, owner_id, rid):
    """Create the deterministic execution claim this test's writes need.

    Writes ``execution.json`` (attempt + runtime_id) into the run dir and
    returns the lock path, writer attempt, and writer identity that the fenced
    result writes require.  Deterministic values keep tests independent of any
    live process identity (no ``get_runtime_identity``).
    """
    import magazine.execution_meta as em
    import magazine.runtime_identity as ri

    run_dir = mfr._state_dir(owner_id, rid)
    meta = em.ExecutionMetadata(
        attempt=_TEST_WRITER_ATTEMPT,
        runtime_id=_TEST_RUNTIME_ID,
        pid=_TEST_PID,
        start_ticks=_TEST_START_TICKS,
        created_at="2026-01-01T00:00:00Z",
        halted_by=None,
        halted_at=None,
        halt_reason=None,
    )
    em.save_execution_metadata(os.path.join(run_dir, "execution.json"), meta)
    identity = ri.RuntimeIdentity(
        runtime_id=_TEST_RUNTIME_ID,
        pid=_TEST_PID,
        start_ticks=_TEST_START_TICKS,
    )
    return mfr._lock_path(owner_id, rid), _TEST_WRITER_ATTEMPT, identity


def _create_run(mag, owner_id, rid, cartridges, status="READY"):
    """Create a REAL run using the canonical core state + result stores.

    READY is produced by the core's ``new_ready_state``.  COMPLETED / RUNNING
    are produced by mutating that validated state to a valid schema and saving
    it through ``magazine.state_store``.  COMPLETED runs also get the canonical
    per-cartridge result files via ``magazine.result_store``.
    """
    m = mag
    base = mfr._run_base(owner_id, rid)
    run_dir = mfr._state_dir(owner_id, rid)
    os.makedirs(run_dir, exist_ok=True)

    # The write fence requires a matching execution.json claim; always create a
    # deterministic one so the fenced result writes accept this run's writer.
    lock_path, writer_attempt, writer_identity = _make_writer(mag, owner_id, rid)

    state = m["state"].new_ready_state(list(cartridges))
    if status == "COMPLETED":
        # save_cartridge_result is fenced: it only writes while the run is
        # RUNNING.  Commit to RUNNING, persist the canonical per-cartridge
        # result files, then advance to the final COMPLETED state.
        state["status"] = "RUNNING"
        state["current"] = list(cartridges)[0] if cartridges else None
        state["todo"] = []
        state["revision"] = 1
        m["ss"].save_run_state(mfr._state_path(owner_id, rid), state)
        for cid in cartridges:
            m["rs"].save_cartridge_result(
                run_dir, cid,
                "RESULT-FOR-" + cid.upper(),
                lock_path, owner_id,
                writer_attempt, writer_identity)
        state["status"] = "COMPLETED"
        state["done"] = list(cartridges)
        state["todo"] = []
        state["current"] = None
        state["revision"] = 1 + len(cartridges)
    elif status == "RUNNING":
        state["status"] = "RUNNING"
        state["current"] = list(cartridges)[0] if cartridges else None
        state["todo"] = list(cartridges[1:]) if cartridges else []
        state["revision"] = 1

    m["ss"].save_run_state(mfr._state_path(owner_id, rid), state)
    mfr._save_meta(owner_id, rid, {
        "model_map": {c: "Bonsai 2 27B" for c in cartridges},
        "tool_ids": [],
        "owner_id": owner_id,
        "created_at": "2026-01-01T00:00:00+00:00",
        "name": "test-run",
        "schema_version": 1,
    })
    return base


# ---------------------------------------------------------------------------
# The START gate ( _start_precheck )
# ---------------------------------------------------------------------------

def test_owner_can_start_ready_run(mag, root):
    _create_run(mag, OWNER_A, RID, ["concise", "reasoning"], status="READY")
    payload = mfr._status_payload(OWNER_A, RID)
    ok, code, detail = mfr._start_precheck(payload, "sksome-token")
    assert payload["status"] == "READY"
    assert ok is True
    assert code == 202
    assert "ready" in detail.lower()


def test_unauthenticated_start_rejected(mag, root):
    # A non-READY run is used on purpose: it proves AUTH is checked BEFORE the
    # state gate.  A COMPLETED run with no bearer token must yield 401, NOT 409
    # (the old ordering would have leaked the state as 409 to an unauthenticated
    # caller).  Ownership is still resolved first (404 for unowned runs).
    _create_run(mag, OWNER_A, RID, ["concise"], status="COMPLETED")
    payload = mfr._status_payload(OWNER_A, RID)
    assert payload["status"] == "COMPLETED"
    ok, code, detail = mfr._start_precheck(payload, "")
    assert ok is False
    assert code == 401
    assert "bearer token" in detail.lower()


def test_non_ready_run_cannot_be_started(mag, root):
    _create_run(mag, OWNER_A, RID, ["concise"], status="COMPLETED")
    payload = mfr._status_payload(OWNER_A, RID)
    assert payload["status"] == "COMPLETED"
    ok, code, detail = mfr._start_precheck(payload, "sksome-token")
    assert ok is False
    assert code == 409
    assert "cannot start" in detail.lower()
    assert "completed" in detail.lower()


def test_other_user_cannot_start_owner_run(mag, root):
    _create_run(mag, OWNER_A, RID, ["concise"], status="READY")
    payload = mfr._status_payload(OWNER_B, RID)
    assert payload is None  # unowned -> 404 before any state is disclosed
    ok, code, detail = mfr._start_precheck(payload, "sksome-token")
    assert ok is False
    assert code == 404
    assert "not found" in detail.lower()


# ---------------------------------------------------------------------------
# The native RESULTS path ( _results_payload )
# ---------------------------------------------------------------------------

def test_other_user_cannot_retrieve_owner_results(mag, root):
    _create_run(mag, OWNER_A, RID, ["concise"], status="COMPLETED")
    payload = mfr._results_payload(OWNER_B, RID)
    assert payload is None  # unowned -> 404


def test_owner_can_retrieve_completed_results(mag, root):
    _create_run(mag, OWNER_A, RID, ["concise", "reasoning"], status="COMPLETED")
    payload = mfr._results_payload(OWNER_A, RID)
    assert payload is not None
    assert payload["status"] == "COMPLETED"
    assert payload["owner_id"] == OWNER_A
    assert payload["run_id"] == RID
    assert payload["done"] == ["concise", "reasoning"]
    # Verbatim canonical result text, keyed by the finished cartridge IDs.
    assert payload["results"]["concise"] == "RESULT-FOR-CONCISE"
    assert payload["results"]["reasoning"] == "RESULT-FOR-REASONING"


def test_caller_supplied_ownership_cannot_override_auth_identity(mag, root):
    """The 7th execution boundary: caller-supplied ownership data cannot
    override the authenticated identity.

    The runner admits a run using ``owner_id = str(user.id)`` (the identity
    fixed by the Open WebUI auth middleware) and the request body
    ``_CreateRunBody`` has no ``owner_id`` field (Pydantic ``extra='ignore'``).
    So a spoofed ``"owner_id"`` sent in the request is dropped, and the
    admitted run is bound to the server-persisted owner, never to any value
    the caller could supply.
    """
    # 1) Structural: the request body has no owner_id field to capture.
    assert "owner_id" not in mfr._CreateRunBody.model_fields

    # 2) Behavioral: a spoofed owner_id in the request body is dropped by
    #    validation (extra='ignore') without raising.
    body = mfr._CreateRunBody.model_validate({
        "magazine": "{}", "model_map": {}, "tool_ids": [],
        "owner_id": OWNER_B,  # spoofed -- must be ignored
    })
    dumped = body.model_dump()
    assert "owner_id" not in dumped
    assert dumped.get("owner_id") is None

    # 3) Behavioral anchor: retrieval is matched against the server-persisted
    #    owner, never against a caller-supplied identity.  Create a completed
    #    run under OWNER_A (the server-persisted owner); a DIFFERENT identity
    #    (OWNER_B) cannot retrieve it.
    _create_run(mag, OWNER_A, RID, ["concise"], status="COMPLETED")
    owner_payload = mfr._results_payload(OWNER_A, RID)
    assert owner_payload is not None
    assert owner_payload["owner_id"] == OWNER_A
    assert owner_payload["done"] == ["concise"]
    assert owner_payload["results"] == {"concise": "RESULT-FOR-CONCISE"}
    assert mfr._results_payload(OWNER_B, RID) is None  # 404: not owned by caller


# ---------------------------------------------------------------------------
# Durable Stop: ownership, idempotence, restart and cancellation evidence
# ---------------------------------------------------------------------------

def _writer_identity(mag):
    return mag["ri"].RuntimeIdentity(
        runtime_id=_TEST_RUNTIME_ID,
        pid=_TEST_PID,
        start_ticks=_TEST_START_TICKS,
    )


def test_stop_is_owner_scoped_and_repeated_requests_are_idempotent(mag, root):
    _create_run(mag, OWNER_A, RID, ["concise"], status="READY")
    identity = _writer_identity(mag)
    with patch.object(mag["ri"], "get_runtime_identity", return_value=identity):
        code, body = asyncio.run(mfr._stop_run_decision(OWNER_B, RID))
        assert code == 404
        assert body["detail"] == "Run not found"

        first_code, first = asyncio.run(mfr._stop_run_decision(OWNER_A, RID))
        second_code, second = asyncio.run(mfr._stop_run_decision(OWNER_A, RID))

    assert first_code == second_code == 200
    assert first["status"] == second["status"] == "stopped"
    assert first["control"] == second["control"]
    assert first["control"]["termination_status"] == "CONFIRMED"
    assert mag["ss"].load_run_state(mfr._state_path(OWNER_A, RID))["status"] == "HALTED"


def test_restart_keeps_stop_requested_unconfirmed_and_blocks_recovery(mag, root):
    _create_run(mag, OWNER_A, RID, ["concise"], status="RUNNING")
    identity = _writer_identity(mag)
    control = mag["rc"].persist_request_stop(
        mfr._state_path(OWNER_A, RID), mfr._lock_path(OWNER_A, RID),
        OWNER_A, _TEST_WRITER_ATTEMPT, identity,
    )
    replacement = mag["ri"].RuntimeIdentity(
        runtime_id="2" * 32, pid=54321, start_ticks=1001
    )
    with patch.object(mag["ri"], "get_runtime_identity", return_value=replacement):
        with patch.object(mag["racton"], "process_is_extinct", return_value=True):
            code, body = asyncio.run(mfr._stop_run_decision(OWNER_A, RID))
        recover_code, recover = mfr._recover_run_decision(OWNER_A, RID)

    assert code == 202
    assert body["status"] == "stop_requested"
    assert "could not be confirmed" in body["message"]
    assert recover_code == 409
    assert "Stop request" in recover["detail"]
    reloaded = mag["rc"].load_stop_control(mfr._state_path(OWNER_A, RID))
    assert reloaded == control
    assert reloaded["termination_status"] == "REQUESTED"
    assert reloaded["cancellation_status"] == "NOT_ATTEMPTED"
    assert mag["ss"].load_run_state(mfr._state_path(OWNER_A, RID))["status"] == "HALTED"



def test_failed_dispatch_after_stop_is_never_reported_as_successful_stop(mag, root):
    _create_run(mag, OWNER_A, RID, ["concise"], status="RUNNING")
    identity = _writer_identity(mag)
    state_path = mfr._state_path(OWNER_A, RID)
    lock_path = mfr._lock_path(OWNER_A, RID)
    mag["rc"].persist_request_stop(
        state_path, lock_path, OWNER_A, _TEST_WRITER_ATTEMPT, identity,
    )
    mag["racton"].persist_error_halt(
        state_path, lock_path, OWNER_A, _TEST_WRITER_ATTEMPT, identity,
        error=RuntimeError("Bonsai inference failed"),
    )
    with patch.object(mag["ri"], "get_runtime_identity", return_value=identity):
        code, body = asyncio.run(mfr._stop_run_decision(OWNER_A, RID))
    assert code == 409
    assert body["status"] == "stop_not_applied"
    assert body["control"]["termination_status"] == "NOT_APPLIED"
    assert "independently" in body["detail"]
    assert mag["ss"].load_run_state(state_path)["status"] == "HALTED"


def test_stop_against_already_halted_run_returns_conflict_without_mutation(mag, root):
    _create_run(mag, OWNER_A, RID, ["concise"], status="RUNNING")
    identity = _writer_identity(mag)
    state_path = mfr._state_path(OWNER_A, RID)
    mag["racton"].persist_error_halt(
        state_path, mfr._lock_path(OWNER_A, RID), OWNER_A,
        _TEST_WRITER_ATTEMPT, identity, error=RuntimeError("original inference failure"),
    )
    from pathlib import Path
    directory = Path(mfr._state_dir(OWNER_A, RID))
    before = {path.name: path.read_bytes() for path in directory.iterdir() if path.is_file()}
    with patch.object(mag["ri"], "get_runtime_identity", return_value=identity):
        code, body = asyncio.run(mfr._stop_run_decision(OWNER_A, RID))
    assert code == 409
    assert body["status"] == "already_halted"
    assert "already halted" in body["detail"].lower()
    assert {path.name: path.read_bytes() for path in directory.iterdir() if path.is_file()} == before
    assert mag["rc"].load_stop_control(state_path) is None


def test_cancellation_failure_stays_requested_and_does_not_cancel_local_task(mag, root):
    _create_run(mag, OWNER_A, RID, ["concise"], status="RUNNING")
    identity = _writer_identity(mag)

    class FailedDispatch:
        calls = 0

        async def cancel_active(self):
            self.calls += 1
            return mag["wd"].CancellationResult(
                confirmed=False,
                active=True,
                chat_id="chat-1",
                task_ids=("task-1",),
                detail="POST stop: HTTP 500",
            )

    async def scenario():
        local_task = asyncio.create_task(asyncio.Event().wait())
        mfr._RUN_HANDLES[(OWNER_A, RID)] = {
            "task": local_task, "dispatch": FailedDispatch(), "prepared": None
        }
        try:
            with patch.object(mag["ri"], "get_runtime_identity", return_value=identity):
                code, body = await mfr._stop_run_decision(OWNER_A, RID)
                repeated_code, repeated = await mfr._stop_run_decision(OWNER_A, RID)
            assert local_task.cancelled() is False
            return code, body, repeated_code, repeated, mfr._RUN_HANDLES[(OWNER_A, RID)]["dispatch"].calls
        finally:
            local_task.cancel()
            try:
                await local_task
            except asyncio.CancelledError:
                pass
            mfr._RUN_HANDLES.pop((OWNER_A, RID), None)

    code, body, repeated_code, repeated, cancel_calls = asyncio.run(scenario())
    assert code == repeated_code == 202
    assert body["status"] == "stop_requested"
    assert repeated["control"] == body["control"]
    assert cancel_calls == 1
    assert body["control"]["cancellation_status"] == "FAILED"
    assert body["control"]["termination_status"] == "REQUESTED"
    assert mag["ss"].load_run_state(mfr._state_path(OWNER_A, RID))["status"] == "RUNNING"


def test_confirmed_active_cancellation_halts_before_reporting_stopped(mag, root):
    _create_run(mag, OWNER_A, RID, ["concise"], status="RUNNING")
    identity = _writer_identity(mag)

    class ConfirmedDispatch:
        async def cancel_active(self):
            return mag["wd"].CancellationResult(
                confirmed=True,
                active=True,
                chat_id="chat-1",
                task_ids=("task-1",),
                detail="stop acknowledged and task list empty",
            )

    async def scenario():
        local_task = asyncio.create_task(asyncio.Event().wait())
        mfr._RUN_HANDLES[(OWNER_A, RID)] = {
            "task": local_task, "dispatch": ConfirmedDispatch(), "prepared": None
        }
        try:
            with patch.object(mag["ri"], "get_runtime_identity", return_value=identity):
                return await mfr._stop_run_decision(OWNER_A, RID)
        finally:
            mfr._RUN_HANDLES.pop((OWNER_A, RID), None)

    code, body = asyncio.run(scenario())
    assert code == 200
    assert body["status"] == "stopped"
    assert body["control"]["cancellation_status"] == "CONFIRMED"
    assert body["control"]["termination_status"] == "CONFIRMED"
    assert body["control"]["termination_method"] == "active-inference-cancelled"
    assert mag["ss"].load_run_state(mfr._state_path(OWNER_A, RID))["status"] == "HALTED"


# ---------------------------------------------------------------------------
# The background task boundary ( _run_bg_dispatch ): dispatch-only after commit
# ---------------------------------------------------------------------------

def test_background_dispatch_is_dispatch_only_after_commit(mag, root):
    """The detached background task is *dispatch-only* — the "background-task-
    only-after-commit" boundary.

    ``_start`` performs the *commit* first (``prepare_run`` -> ``persist_admit_run``
    writes the execution claim and flips ``READY -> RUNNING``) and *only then*
    creates the detached task.  ``_run_bg_dispatch`` receives that ALREADY-COMMIT-
    TED ``PreparedRun`` and hands it verbatim to the single dispatch API
    ``run_dispatch`` with the already-created cancellable adapter. It never re-derives paths from ``state_dir``
    (the argument is accepted but unused) and never re-admits the run (no
    ``prepare_run`` / ``persist_admit_run`` inside the task).  The dispatch API
    is spied (not mocked away) so the assertion is structural + behavioral: the
    exact committed ``state_path``/``lock_path``/``prepared`` reach the
    dispatcher, and the run remains at its committed state.
    """
    # 1) An already-committed run: state READY -> RUNNING (what the caller's
    #    prepare_run/persist_admit_run produces before scheduling the task).
    _create_run(mag, OWNER_A, RID, ["concise", "reasoning"], status="READY")
    state_path = mfr._state_path(OWNER_A, RID)
    state = mag["ss"].load_run_state(state_path)
    state["status"] = "RUNNING"
    state["current"] = "concise"
    state["todo"] = ["reasoning"]
    state["revision"] = 1
    mag["ss"].save_run_state(state_path, state)
    # The committed PreparedRun the background task will receive.
    lock_path = mfr._lock_path(OWNER_A, RID)
    prepared = mag["rp"].PreparedRun(
        state=state,
        state_path=state_path,
        lock_path=lock_path,
        attempt=2,
        identity=None,
    )
    assert mag["ss"].load_run_state(state_path)["status"] == "RUNNING"

    # 2) Spy on every core API _run_bg_dispatch could plausibly touch.
    dispatch_calls, prepare_calls, admit_calls = [], [], []

    async def _dispatch_spy(**kw):
        dispatch_calls.append(kw)
        return None

    def _prepare_spy(*a, **k):
        prepare_calls.append((a, k))

    def _admit_spy(*a, **k):
        admit_calls.append((a, k))

    dispatch_adapter = object()
    with patch.object(mag["ar"], "run_dispatch", _dispatch_spy), \
         patch.object(mag["rp"], "prepare_run", _prepare_spy), \
         patch.object(mag["ra"], "persist_admit_run", _admit_spy):
        asyncio.run(mfr._run_bg_dispatch(
            RID,
            mfr._mag_path(OWNER_A, RID),          # magazine_path
            mfr._state_dir(OWNER_A, RID),         # state_dir (unused by dispatch)
            OWNER_A,
            {"concise": "Bonsai 2 27B", "reasoning": "Bonsai 2 27B"},
            prepared,
            dispatch_adapter,
        ))

    # 3) Exactly one dispatch, using the committed PreparedRun verbatim.
    assert len(dispatch_calls) == 1
    kw = dispatch_calls[0]
    assert kw["state_path"] == prepared.state_path
    assert kw["lock_path"] == prepared.lock_path
    assert kw["prepared"] is prepared            # same object; no re-derivation
    assert kw["magazine_path"] == mfr._mag_path(OWNER_A, RID)
    assert kw["owner_id"] == OWNER_A
    assert kw["model_map"] == {"concise": "Bonsai 2 27B", "reasoning": "Bonsai 2 27B"}
    assert kw["dispatch"] is dispatch_adapter

    # 4) No re-admission happened inside the background task.
    assert not prepare_calls, "prepare_run must not be called during dispatch"
    assert not admit_calls, "persist_admit_run must not be called during dispatch"

    # 5) Dispatch-only: the run remains at its committed state (no attempt+1).
    assert mag["ss"].load_run_state(state_path)["status"] == "RUNNING"
    assert mag["ss"].load_run_state(state_path)["current"] == "concise"
    assert mag["ss"].load_run_state(state_path)["todo"] == ["reasoning"]
