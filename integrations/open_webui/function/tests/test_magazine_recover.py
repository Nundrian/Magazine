"""Slice 4 recovery regressions at the runner boundary.

The tests use the runner's real embedded Magazine core.  Only the two
TOCTOU outcomes patch lock acquisition, so the state/claim change happens at
the exact deterministic boundary that recovery must re-check.
"""

from dataclasses import replace
import inspect
import json
import os
import sys
import types

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import magazine_ef as mfr


OWNER = "owner-a"
OTHER_OWNER = "owner-b"
RID = "recover-run"
OLD_RUNTIME = "1" * 32
NEW_RUNTIME = "2" * 32
THIRD_RUNTIME = "3" * 32


@pytest.fixture(scope="module")
def mag():
    embedded = mfr._mag()
    assert embedded.get("ok"), embedded.get("error")
    return embedded


@pytest.fixture
def root(mag, monkeypatch, tmp_path):
    runs_root = os.path.join(str(tmp_path), "magazine", "runs")
    monkeypatch.setattr(mfr, "_runs_root", lambda: runs_root)
    return runs_root


@pytest.fixture
def identities(mag, monkeypatch):
    old = mag["ri"].RuntimeIdentity(OLD_RUNTIME, 111, 222)
    new = mag["ri"].RuntimeIdentity(NEW_RUNTIME, 333, 444)
    monkeypatch.setattr(mag["ri"], "get_runtime_identity", lambda: new)
    return old, new


def _write_claim(mag, owner, rid, identity, attempt=1, **updates):
    claim = mag["em"].ExecutionMetadata(
        attempt=attempt,
        runtime_id=identity.runtime_id,
        pid=identity.pid,
        start_ticks=identity.start_ticks,
        created_at="2026-10-07T00:00:00Z",
        halted_by=None,
        halted_at=None,
        halt_reason=None,
    )
    if updates:
        claim = replace(claim, **updates)
    path = os.path.join(mfr._state_dir(owner, rid), "execution.json")
    mag["em"].save_execution_metadata(path, claim)
    return claim


def _create_run(mag, owner=OWNER, rid=RID, status="RUNNING", claim=None):
    os.makedirs(mfr._state_dir(owner, rid), exist_ok=True)
    state = mag["state"].new_ready_state(["first", "second"])
    if status in ("RUNNING", "HALTED"):
        state.update(
            status=status,
            current="first",
            todo=["second"],
            revision=1 if status == "RUNNING" else 2,
        )
    elif status == "COMPLETED":
        state.update(
            status="COMPLETED", current=None, todo=[],
            done=["first", "second"], revision=3,
        )
    mag["ss"].save_run_state(mfr._state_path(owner, rid), state)
    mfr._save_meta(owner, rid, {
        "owner_id": owner,
        "model_map": {"only": "model"},
        "tool_ids": [],
        "created_at": "2026-10-07T00:00:00+00:00",
    })
    if claim is not None:
        _write_claim(mag, owner, rid, claim)
    return state


def _state(mag, owner=OWNER, rid=RID):
    return mag["ss"].load_run_state(mfr._state_path(owner, rid))


def _claim(mag, owner=OWNER, rid=RID):
    return mag["em"].load_execution_metadata(
        os.path.join(mfr._state_dir(owner, rid), "execution.json"))


def _route_client(monkeypatch):
    """Install the real runner routes with only OWUI auth stubbed.

    Route execution, dependency resolution, response construction, and HTTP
    status/content-type handling remain FastAPI/Starlette behavior.
    """
    open_webui = types.ModuleType("open_webui")
    open_webui.__path__ = []
    utils = types.ModuleType("open_webui.utils")
    utils.__path__ = []
    auth = types.ModuleType("open_webui.utils.auth")

    async def get_current_user():
        return types.SimpleNamespace(id=OWNER)

    auth.get_current_user = get_current_user
    open_webui.utils = utils
    utils.auth = auth
    monkeypatch.setitem(sys.modules, "open_webui", open_webui)
    monkeypatch.setitem(sys.modules, "open_webui.utils", utils)
    monkeypatch.setitem(sys.modules, "open_webui.utils.auth", auth)

    app = FastAPI()
    mfr._install_routes(app)
    matches = [
        route for route in app.router.routes
        if getattr(route, "path", None) == "/api/magazine/runs/{rid}/recover"
    ]
    assert len(matches) == 1
    assert "POST" in matches[0].methods
    return TestClient(app, raise_server_exceptions=False)


def test_non_running_state_is_not_intercepted(mag, root, identities, monkeypatch):
    _create_run(mag, status="READY")
    monkeypatch.setattr(
        mag["racton"], "recover_lost_runtime",
        lambda *a, **k: pytest.fail("non-RUNNING state reached recovery"),
    )
    payload = mfr._status_payload(OWNER, RID)
    assert mfr._lost_runtime_interception(payload, OWNER, RID) is None
    assert mfr._start_precheck(payload, "token")[:2] == (True, 202)


def test_legacy_running_without_claim_stays_conservative(mag, root, identities):
    before = _create_run(mag, claim=None)
    decision = mfr._lost_runtime_interception(
        mfr._status_payload(OWNER, RID), OWNER, RID)
    assert decision["status_code"] == 409
    assert decision["status"] == "RUNNING"
    assert "no ownership evidence" in decision["detail"]
    assert _state(mag) == before
    assert _claim(mag) is None


def test_same_runtime_falls_through_to_duplicate_start_rejection(
        mag, root, identities, monkeypatch):
    old, _new = identities
    _create_run(mag, claim=old)
    monkeypatch.setattr(mag["ri"], "get_runtime_identity", lambda: old)
    payload = mfr._status_payload(OWNER, RID)
    assert mfr._lost_runtime_interception(payload, OWNER, RID) == "same_runtime"
    ok, code, detail = mfr._start_precheck(payload, "token")
    assert (ok, code) == (False, 409)
    assert "RUNNING" in detail
    assert _state(mag)["status"] == "RUNNING"


def test_live_different_runtime_owner_is_not_auto_halted(
        mag, root, identities, monkeypatch):
    old, _new = identities
    before = _create_run(mag, claim=old)
    monkeypatch.setattr(
        mag["racton"], "process_is_extinct", lambda *args: False,
    )

    decision = mfr._lost_runtime_interception(
        mfr._status_payload(OWNER, RID), OWNER, RID)

    assert decision["status_code"] == 409
    assert decision["status"] == "RUNNING"
    assert "still alive" in decision["detail"]
    assert _state(mag) == before
    claim = _claim(mag)
    assert claim.attempt == 1
    assert claim.runtime_id == OLD_RUNTIME
    assert claim.halted_by is None
    assert claim.halt_reason is None


def test_inprocess_reload_409_zero_writes_sole_owner(
        mag, root, identities, monkeypatch):
    """Corrected FM-1 (in-process reload, no new generation).

    An in-process OWUI Function reload re-executes the runner module only; it does
    NOT replace the process, so the process identity (``runtime_id``/``pid``/
    ``start_ticks``) is unchanged.  The still-RUNNING run is therefore still owned
    by the original generation (attempt 1, ``OLD_RUNTIME``); a second generation's
    ``POST /start`` is a duplicate start, not a new owner.  Assert:

      (a) the second ``POST /start`` is rejected with HTTP 409 "cannot start a
          RUNNING run" (the original generation is still the owner, so the precheck
          -- not a lost-owner auto-HALT -- rejects it);
      (b) zero stale writes: state.json and execution.json are byte-unchanged and
          no new file (including result artefacts) appears in the run directory and
          no background task is spawned;
      (c) the original generation remains the sole owner: the claim is unchanged
          (attempt 1, original runtime_id, never halted) and the read-only
          ``execution`` block reports ``owner_alive == True``.
    """
    old, _new = identities
    run_dir = mfr._state_dir(OWNER, RID)

    # G1: a RUNNING run owned by generation `old` (attempt 1, OLD_RUNTIME).
    _create_run(mag, claim=old)
    state_path = mfr._state_path(OWNER, RID)
    exec_path = os.path.join(run_dir, "execution.json")
    state_before = open(state_path, "rb").read()
    exec_before = open(exec_path, "rb").read()
    files_before = sorted(os.listdir(run_dir))
    tasks_before = set(mfr._TASKS)

    # In-process reload: the current process identity is UNCHANGED (same runtime_id,
    # same pid, same start_ticks) -- this is what "in-process" means.
    monkeypatch.setattr(mag["ri"], "get_runtime_identity", lambda: old)

    # G2: POST /start on the SAME run_id.  The /start route also gates on a
    # valid bearer token (api_key via Depends(_get_token)); the test supplies
    # one (matching the test_magazine_execution.py convention) so the 409
    # status re-check is reached rather than short-circuiting to 401.
    with _route_client(monkeypatch) as client:
        response = client.post(
            f"/api/magazine/runs/{RID}/start",
            headers={"Authorization": "Bearer sksome-token"})

    # (a) 409 re-start rejection.
    assert response.status_code == 409
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["detail"] == "cannot start a RUNNING run"

    # (b) Zero stale writes: state + claim byte-unchanged, no new files, no task.
    assert open(state_path, "rb").read() == state_before
    assert open(exec_path, "rb").read() == exec_before
    files_after = sorted(os.listdir(run_dir))
    assert files_after == files_before
    assert _state(mag)["status"] == "RUNNING"
    assert set(mfr._TASKS) == tasks_before

    # (c) Original generation remains the sole owner.
    claim = _claim(mag)
    assert claim.runtime_id == OLD_RUNTIME
    assert claim.attempt == 1
    assert claim.pid == old.pid
    assert claim.start_ticks == old.start_ticks
    assert claim.halted_by is None
    assert claim.halt_reason is None
    execution = mfr._status_payload(OWNER, RID)["execution"]
    assert execution["owner_alive"] is True
    assert execution["attempt"] == 1
    assert execution["runtime_id"] == OLD_RUNTIME
    assert execution["halted"] is False
    assert execution["halt_reason"] is None


def test_live_foreign_lock_is_not_stolen(mag, root, identities, monkeypatch):
    old, _new = identities
    before = _create_run(mag, claim=old)
    lock_path = mfr._lock_path(OWNER, RID)
    with open(lock_path, "w", encoding="utf-8") as lock_file:
        lock_file.write(f"{OWNER}|{OLD_RUNTIME}|{old.pid}|{old.start_ticks}")
    monkeypatch.setattr(mag["rl"], "process_is_extinct", lambda *a: False)

    decision = mfr._lost_runtime_interception(
        mfr._status_payload(OWNER, RID), OWNER, RID)

    assert decision["status_code"] == 409
    assert "locked" in decision["detail"]
    assert _state(mag) == before
    with open(lock_path, encoding="utf-8") as lock_file:
        assert lock_file.read() == f"{OWNER}|{OLD_RUNTIME}|{old.pid}|{old.start_ticks}"


def test_positively_lost_owner_halts_with_durable_evidence(
        mag, root, identities):
    old, _new = identities
    _create_run(mag, claim=old)

    decision = mfr._lost_runtime_interception(
        mfr._status_payload(OWNER, RID), OWNER, RID)

    assert decision["status_code"] == 409
    assert decision["status"] == "HALTED"
    assert "recover to READY first" in decision["detail"]
    assert _state(mag)["status"] == "HALTED"
    claim = _claim(mag)
    assert claim.attempt == 1
    assert claim.runtime_id == OLD_RUNTIME
    assert claim.halted_by == NEW_RUNTIME
    assert claim.halt_reason == "lost-owner"


def test_state_changed_wins_under_lock(mag, root, identities, monkeypatch):
    old, _new = identities
    _create_run(mag, claim=old)
    real_acquire = mag["racton"].acquire_run_lock

    def change_state_then_acquire(*args):
        changed = _state(mag)
        changed["status"] = "HALTED"
        changed["revision"] += 1
        mag["ss"].save_run_state(mfr._state_path(OWNER, RID), changed)
        return real_acquire(*args)

    monkeypatch.setattr(mag["racton"], "acquire_run_lock", change_state_then_acquire)
    decision = mfr._lost_runtime_interception(
        mfr._status_payload(OWNER, RID), OWNER, RID)

    assert "state_changed" in decision["detail"]
    assert _state(mag)["status"] == "HALTED"
    assert _claim(mag).halt_reason is None


def test_claim_changed_wins_under_lock(mag, root, identities, monkeypatch):
    old, _new = identities
    _create_run(mag, claim=old)
    real_acquire = mag["racton"].acquire_run_lock

    def change_claim_then_acquire(*args):
        third = mag["ri"].RuntimeIdentity(THIRD_RUNTIME, 555, 666)
        _write_claim(mag, OWNER, RID, third, attempt=2)
        return real_acquire(*args)

    monkeypatch.setattr(mag["racton"], "acquire_run_lock", change_claim_then_acquire)
    decision = mfr._lost_runtime_interception(
        mfr._status_payload(OWNER, RID), OWNER, RID)

    assert "claim_changed" in decision["detail"]
    assert _state(mag)["status"] == "RUNNING"
    assert (_claim(mag).attempt, _claim(mag).runtime_id) == (2, THIRD_RUNTIME)
    assert _claim(mag).halt_reason is None


def test_explicit_recovery_is_halted_to_ready_only(mag, root, identities):
    old, _new = identities
    _create_run(mag, status="HALTED", claim=old)
    tasks_before = set(mfr._TASKS)

    code, body = mfr._recover_run_decision(OWNER, RID)

    assert code == 200
    assert body["status"] == "READY"
    assert _state(mag)["status"] == "READY"
    assert _state(mag)["current"] is None
    assert _state(mag)["todo"] == ["first", "second"]
    assert _claim(mag).attempt == 1
    assert _claim(mag).halt_reason == "operator-recovered"
    assert set(mfr._TASKS) == tasks_before


def test_explicit_recovery_rejects_non_halted_run(mag, root, identities):
    before = _create_run(mag, status="READY")
    code, body = mfr._recover_run_decision(OWNER, RID)
    assert code == 409
    assert "not HALTED" in body["detail"]
    assert _state(mag) == before


@pytest.mark.parametrize("owner,rid", [(OTHER_OWNER, RID), (OWNER, "missing")])
def test_recovery_is_owner_scoped_and_missing_safe(
        mag, root, identities, owner, rid):
    old, _new = identities
    before = _create_run(mag, status="HALTED", claim=old)
    code, body = mfr._recover_run_decision(owner, rid)
    assert code == 404
    assert body == {"detail": "Run not found"}
    assert _state(mag) == before


def test_lost_attempt_recovers_then_normal_admission_uses_n_plus_one(
        mag, root, identities):
    old, new = identities
    _create_run(mag, claim=old)
    assert mfr._lost_runtime_interception(
        mfr._status_payload(OWNER, RID), OWNER, RID)["status"] == "HALTED"
    assert _claim(mag).attempt == 1

    code, body = mfr._recover_run_decision(OWNER, RID)
    assert (code, body["status"]) == (200, "READY")
    admitted, attempt = mag["ra"].persist_admit_run(
        mfr._state_path(OWNER, RID), mfr._lock_path(OWNER, RID), OWNER,
        ["first", "second"], new.runtime_id, new.pid, new.start_ticks,
    )
    assert admitted["status"] == "RUNNING"
    assert attempt == 2
    assert _claim(mag).attempt == 2
    assert _claim(mag).runtime_id == NEW_RUNTIME
    assert _claim(mag).halt_reason is None


def test_status_execution_block_is_read_only(mag, root, identities):
    old, _new = identities
    _create_run(mag, claim=old)
    state_path = mfr._state_path(OWNER, RID)
    execution_path = os.path.join(mfr._state_dir(OWNER, RID), "execution.json")
    before_state = open(state_path, "rb").read()
    before_execution = open(execution_path, "rb").read()

    payload = mfr._status_payload(OWNER, RID)

    assert payload["execution"] == {
        "attempt": 1,
        "runtime_id": OLD_RUNTIME,
        "owner_alive": False,
        "halted": False,
        "halt_reason": None,
    }
    assert open(state_path, "rb").read() == before_state
    assert open(execution_path, "rb").read() == before_execution


def test_recover_route_uses_authenticated_owner_id():
    source = inspect.getsource(mfr._install_routes)
    recover_source = source[source.index("async def recover_run"):]
    recover_source = recover_source[:recover_source.index("def _is_mine")]
    assert "owner_id = str(user.id)" in recover_source


def test_recover_route_missing_and_unowned_are_json_404(
        mag, root, identities, monkeypatch):
    old, _new = identities
    other_before = _create_run(
        mag, owner=OTHER_OWNER, rid="other-run", status="HALTED", claim=old)
    with _route_client(monkeypatch) as client:
        for rid in ("missing", "other-run"):
            response = client.post(f"/api/magazine/runs/{rid}/recover")
            assert response.status_code == 404
            assert response.headers["content-type"].startswith("application/json")
            assert response.json() == {"detail": "Run not found"}
    assert _state(mag, OTHER_OWNER, "other-run") == other_before


def test_recover_route_ready_is_json_409_without_mutation(
        mag, root, identities, monkeypatch):
    before = _create_run(mag, status="READY")
    with _route_client(monkeypatch) as client:
        response = client.post(f"/api/magazine/runs/{RID}/recover")
    assert response.status_code == 409
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"detail": "Run is READY, not HALTED"}
    assert _state(mag) == before


def test_recover_route_ready_extinct_owner_retires_claim_without_start(
        mag, root, identities, monkeypatch):
    old, new = identities
    ready = _create_run(mag, status="READY", claim=old)
    ready.update(done=["first"], todo=["second"], revision=2)
    mag["ss"].save_run_state(mfr._state_path(OWNER, RID), ready)
    state_path = mfr._state_path(OWNER, RID)
    result_path = os.path.join(mfr._state_dir(OWNER, RID), "results", "first.txt")
    os.makedirs(os.path.dirname(result_path), exist_ok=True)
    with open(result_path, "w", encoding="utf-8") as result_file:
        result_file.write("committed result")
    with open(mfr._mag_path(OWNER, RID), "w", encoding="utf-8") as document_file:
        json.dump({
            "schema_version": "1.0",
            "cartridges": [
                {"id": "first", "job_type": "only", "prompt": "first"},
                {"id": "second", "job_type": "only", "prompt": "second"},
            ],
        }, document_file)
    state_before = open(state_path, "rb").read()
    result_before = open(result_path, "rb").read()
    tasks_before = set(mfr._TASKS)

    async def no_dispatch(*args):
        pass

    monkeypatch.setattr(mfr, "_run_bg_dispatch", no_dispatch)

    with _route_client(monkeypatch) as client:
        # A normal /start must not silently perform READY recovery.
        refused = client.post(
            f"/api/magazine/runs/{RID}/start",
            headers={"Authorization": "Bearer sksome-token"})
        assert refused.status_code == 409, refused.text
        assert set(mfr._TASKS) == tasks_before
        response = client.post(f"/api/magazine/runs/{RID}/recover")
        assert response.status_code == 200
        assert response.json()["status"] == "READY"
        assert open(state_path, "rb").read() == state_before
        assert open(result_path, "rb").read() == result_before
        assert _claim(mag).halt_reason == "lost-owner-ready"
        assert set(mfr._TASKS) == tasks_before

        started = client.post(
            f"/api/magazine/runs/{RID}/start",
            headers={"Authorization": "Bearer sksome-token"})
        assert started.status_code == 202, started.text

    assert started.json()["attempt"] == 2
    assert _state(mag)["current"] == "second"
    assert _state(mag)["done"] == ["first"]
    assert _claim(mag).attempt == 2
    assert open(result_path, "rb").read() == result_before


def test_recover_route_ready_live_owner_returns_conflict(
        mag, root, identities, monkeypatch):
    old, _new = identities
    before = _create_run(mag, status="READY", claim=old)
    claim_before = _claim(mag)
    monkeypatch.setattr(mag["racton"], "process_is_extinct", lambda *args: False)

    with _route_client(monkeypatch) as client:
        response = client.post(f"/api/magazine/runs/{RID}/recover")

    assert response.status_code == 409
    assert "owner_live" in response.json()["detail"]
    assert _state(mag) == before
    assert _claim(mag) == claim_before


def test_recover_route_ready_ambiguous_liveness_returns_conflict(
        mag, root, identities, monkeypatch, tmp_path):
    old, _new = identities
    before = _create_run(mag, status="READY", claim=old)
    claim_before = _claim(mag)
    monkeypatch.setattr(mag["ri"], "_PROC_ROOT", tmp_path / "missing-proc")
    with _route_client(monkeypatch) as client:
        response = client.post(f"/api/magazine/runs/{RID}/recover")
    assert response.status_code == 409
    assert "owner_live" in response.json()["detail"]
    assert _state(mag) == before
    assert _claim(mag) == claim_before


def test_recover_route_ready_changed_claim_returns_conflict(
        mag, root, identities, monkeypatch):
    old, _new = identities
    before = _create_run(mag, status="READY", claim=old)
    real_acquire = mag["racton"].acquire_run_lock
    successor = mag["ri"].RuntimeIdentity(THIRD_RUNTIME, 555, 666)

    def change_claim_then_acquire(*args):
        _write_claim(mag, OWNER, RID, successor, attempt=2)
        return real_acquire(*args)

    monkeypatch.setattr(
        mag["racton"], "acquire_run_lock", change_claim_then_acquire)
    with _route_client(monkeypatch) as client:
        response = client.post(f"/api/magazine/runs/{RID}/recover")
    assert response.status_code == 409
    assert "claim_changed" in response.json()["detail"]
    assert _state(mag) == before
    assert _claim(mag).attempt == 2
    assert _claim(mag).halt_reason is None


def test_recover_route_ready_lock_conflict_returns_409(
        mag, root, identities, monkeypatch):
    old, _new = identities
    before = _create_run(mag, status="READY", claim=old)
    lock_path = mfr._lock_path(OWNER, RID)
    with open(lock_path, "w", encoding="utf-8") as lock_file:
        lock_file.write(f"{OWNER}|{OLD_RUNTIME}|{old.pid}|{old.start_ticks}")
    monkeypatch.setattr(mag["rl"], "process_is_extinct", lambda *args: False)
    with _route_client(monkeypatch) as client:
        response = client.post(f"/api/magazine/runs/{RID}/recover")
    assert response.status_code == 409
    assert "locked" in response.json()["detail"]
    assert _state(mag) == before
    assert _claim(mag).halt_reason is None


def test_recover_route_ready_persistence_failure_returns_500(
        mag, root, identities, monkeypatch):
    old, _new = identities
    before = _create_run(mag, status="READY", claim=old)
    claim_before = _claim(mag)

    def fail_write(*args):
        raise OSError("claim write failed")

    monkeypatch.setattr(mag["racton"], "save_execution_metadata", fail_write)
    with _route_client(monkeypatch) as client:
        response = client.post(f"/api/magazine/runs/{RID}/recover")
    assert response.status_code == 500
    assert "claim write failed" in response.json()["detail"]
    assert _state(mag) == before
    assert _claim(mag) == claim_before


def test_recover_route_halted_is_json_200_ready_without_start(
        mag, root, identities, monkeypatch):
    old, _new = identities
    _create_run(mag, status="HALTED", claim=old)
    tasks_before = set(mfr._TASKS)
    with _route_client(monkeypatch) as client:
        response = client.post(f"/api/magazine/runs/{RID}/recover")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["status"] == "READY"
    assert _state(mag)["status"] == "READY"
    assert _state(mag)["current"] is None
    assert _claim(mag).attempt == 1
    assert _claim(mag).halt_reason == "operator-recovered"
    assert set(mfr._TASKS) == tasks_before


# ---------------------------------------------------------------------------
# Loader cache-identity regression.
#
# The embedded-core loader (_ensure_magazine) must not silently import a
# stale / incomplete on-disk extraction.  If the `magazine/` directory in the
# cache belongs to a *different* embedded-package revision (or is incomplete),
# the loader must detect the mismatch and re-extract the genuine embedded
# package before importing.
# ---------------------------------------------------------------------------

def test_loader_replaces_stale_extraction(mag, monkeypatch, tmp_path):
    """Regression: a stale on-disk extraction must NOT be imported.

    We plant a *complete* but *distinguishable* `magazine/` extraction in a
    fresh cache dir (the genuine core with `__init__.py` tampered so the
    revision is detectable) and point the loader at it.  A correct loader
    detects the mismatch (missing manifest / wrong hash) and re-extracts the
    genuine embedded package; the pre-fix loader instead imports the planted
    stale core.  This test must FAIL before the loader fix and PASS after.
    """
    import shutil
    cache = tmp_path / "_mag_pkg"
    # Source = the default cache's genuine core (capture before monkeypatching
    # _PKG_DIR).  Copy it into a fresh cache dir, then mark it STALE so the
    # revision differs from the embedded package.  No manifest/hash is present,
    # so the existing extraction is untrustworthy.
    default_pkg = mfr._PKG_DIR
    shutil.copytree(os.path.join(default_pkg, "magazine"), cache / "magazine")
    (cache / "magazine" / "__init__.py").write_text("# STALE-MARKER old revision\n")

    monkeypatch.setattr(mfr, "_PKG_DIR", str(cache))
    monkeypatch.setattr(mfr, "_MAG", {})

    # Drop any magazine module objects so a re-extract yields a fresh module
    # (in-process reload path: the stale module was already imported).  The
    # fixture's default-cache modules currently live in sys.modules.
    saved = [k for k in list(sys.modules)
             if k == "magazine" or k.startswith("magazine.")]
    for k in saved:
        del sys.modules[k]

    try:
        result = mfr._mag()
        assert result.get("ok"), result.get("error")
        loaded_init = result.get("file")
        assert loaded_init, "no package file reported"
        with open(loaded_init, "r", encoding="utf-8") as f:
            init_text = f.read()
        # Must be the genuine embedded core, NOT the planted stale fake.
        assert "STALE-MARKER" not in init_text
        assert "Magazine: an Open WebUI orchestration tool" in init_text
        # The loaded module must come from the (re-extracted) embedded package
        # under our cache dir, not from the fake file we planted.
        assert loaded_init.startswith(str(cache))
    finally:
        # Restore the default-cache core for the rest of the module.
        for k in list(sys.modules):
            if k == "magazine" or k.startswith("magazine."):
                del sys.modules[k]
        if str(cache) in sys.path:
            sys.path.remove(str(cache))
        import magazine  # noqa: F401  # reload the genuine core (default cache)

