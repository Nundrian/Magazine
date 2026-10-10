#!/usr/bin/env python3
"""Offline FM-1 RUNNING-state fencing acceptance tests — corrected v3.

Two layers, both exercising the IDENTICAL logic the live run uses:

1) Offline evaluate() unit tests (11): synthetic evidence dicts fed to the pure
   evaluate()/_reload_verdict/_analyze_g1_outcome logic. No HTTP involved.
   v3 changes vs v2 expectations:
     - test (RUNNING at boundary): PASS -> INCONCLUSIVE
     - test (HALTED own-execution): PASS -> INCONCLUSIVE
     - reload-not-demonstrated (updated_at / source_identity): INCONCLUSIVE -> FAIL
       (v3 correction B: a missing verified reload is a hard no-go, not inconclusive)

2) Mock-HTTP full-path tests (11): run the REAL main() end-to-end
   (create_probe_run -> pre snap -> deploy_reload -> post_rel -> G2 -> post_g2
   -> observe_g1 -> evaluate).  Only the transport (fm1.httpx.request), the
   clock (time.sleep), the observation bounds, SRC/OUT and credentials are
   patched.  A scripted _FakeTransport dispatches on the URL path with a FIFO
   queue per path and records every call.

No live API is touched.  Run with the runner venv:
    .venvs/magazine-runner/bin/python -m unittest fm1_running_state_acceptance_v3_test -v
"""

import importlib.util
import json
import os
import tempfile
import unittest
from unittest import mock
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
CTRL_SRC = "def magazine_event():\n    return {'ok': True}\n"
RID = "run-123"
RID_RUNTIME = "runtime-9cc72219-abcd"
RID_OTHER = "runtime-0deadbeef-dead"
MARKER = "Run Builder"

def _load_fm1():
    spec = importlib.util.spec_from_file_location(
        "fm1_running_state_acceptance_v3",
        os.path.join(HERE, "fm1_running_state_acceptance_v3.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

FM1 = _load_fm1()


# ============================================================================
# Offline evaluate() helpers (ported from v2; expectations updated for v3)
# ============================================================================
def make_snap(status="RUNNING", current=0, done=0, todo=0, revision=0,
              attempt=1, runtime_id=RID_RUNTIME, owner_alive=True, halted=False,
              halt_reason=None, owner_id="test-a", created_at="1791213325"):
    return {
        "run_id": RID, "status": status, "current_cartridge": current,
        "done": done, "todo": todo, "revision": revision,
        "model_map": {}, "tool_ids": [], "owner_id": owner_id,
        "created_at": created_at,
        "execution": {"attempt": attempt, "runtime_id": runtime_id,
                      "owner_alive": owner_alive, "halted": halted,
                      "halt_reason": halt_reason},
    }

def make_reload(ops=None, marker=True, updated_changed=True, source_identity="exact",
                post_is_active=True, pre_updated=1791213325, post_updated=1791404326,
                pre_record=None, post_record=None, pre_status=200, post_status=200,
                marker_status=200):
    if ops is None:
        ops = {"delete": 200, "create": 200, "toggle": 200}
    pre_rec = pre_record if pre_record is not None else {
        "id": "magazine", "is_active": True, "updated_at": pre_updated,
        "content": "source..."}
    post_rec = post_record if post_record is not None else {
        "id": "magazine", "is_active": post_is_active, "updated_at": post_updated,
        "content": "source..."}
    return {
        "op_statuses": ops, "pre_status": pre_status, "post_status": post_status,
        "pre_record": pre_rec, "post_record": post_rec, "marker": marker,
        "marker_status": marker_status, "pre_updated_at": pre_updated,
        "post_updated_at": post_updated, "updated_at_changed": updated_changed,
        "source_identity": source_identity, "post_is_active": post_is_active,
        "error": None,
    }

def make_outcome(final_status, halt_reason=None, history=None, observed_seconds=30.0):
    return {
        "final_status": final_status, "halt_reason": halt_reason,
        "history": history if history is not None else [
            {"t": 0.0, "status": final_status, "current_cartridge": 4, "revision": 4}],
        "observed_seconds": observed_seconds,
    }

def ev(pre, post_rel, post_g2, g2_status, g2_detail, reload, outcome):
    return {"run_id": RID, "pre": pre, "post_rel": post_rel, "post_g2": post_g2,
            "g2": {"status": g2_status, "detail": g2_detail}, "reload": reload,
            "g1_outcome": outcome}

def _g2(status=409, detail="cannot start a RUNNING run"):
    return status, detail


class TestFM1LegitimateProgress(unittest.TestCase):
    """(1) Legitimate G1 progress: fields advance, invariants hold -> PASS."""
    def test_pass(self):
        pre = make_snap("RUNNING", 0, 0, 0, 0)
        post_rel = make_snap("RUNNING", 3, 3, 1, 3)
        post_g2 = make_snap("COMPLETED", 8, 8, 0, 8)
        outcome = make_outcome("COMPLETED")
        r = FM1.evaluate(ev(pre, post_rel, post_g2, *(_g2()), make_reload(), outcome))
        self.assertEqual(r["result"], "PASS")
        for c in ("g2_rejected_409", "g2_on_running", "ownership_fields_unchanged",
                  "status_not_reset_ready", "owner_alive_stable",
                  "original_attempt_single", "runtime_id_stable_across_reload",
                  "reload_verified", "g1_continued_legitimately",
                  "g1_completed_via_g1", "g1_progress_fields_changed"):
            self.assertTrue(r["checks"][c])
        self.assertEqual(r["hard_failures"], [])
        self.assertEqual(r["inconclusive"], [])


class TestFM1G2RejectionLeftRunning(unittest.TestCase):
    """(2) G1 left RUNNING at the boundary -> INCONCLUSIVE (v3: no false PASS)."""
    def test_inconclusive_running_at_boundary(self):
        pre = make_snap("RUNNING", 0, 0, 0, 0)
        post_rel = make_snap("RUNNING", 3, 3, 1, 3)
        post_g2 = make_snap("RUNNING", 4, 4, 0, 4)
        outcome = make_outcome("RUNNING")
        r = FM1.evaluate(ev(pre, post_rel, post_g2, *(_g2()), make_reload(), outcome))
        self.assertEqual(r["result"], "INCONCLUSIVE")
        self.assertTrue(r["checks"]["g2_on_running"])
        self.assertTrue(r["checks"]["runtime_id_stable_across_reload"])
        self.assertTrue(r["checks"]["g1_left_running_at_boundary"])
        self.assertEqual(r["checks"]["g1_continued_legitimately"], False)
        self.assertEqual(r["hard_failures"], [])
        self.assertTrue(any("COMPLETED" in m for m in r["inconclusive"]))


class TestFM1UnauthorisedWrites(unittest.TestCase):
    """(3) Unauthorised ownership/dispatch -> hard FAIL."""
    def test_fail_attempt_runtime_changed(self):
        pre = make_snap("RUNNING", 0, 0, 0, 0)
        post_rel = make_snap("RUNNING", 3, 3, 1, 3)
        post_g2 = make_snap("COMPLETED", 4, 4, 0, 4, attempt=2, runtime_id=RID_OTHER,
                            owner_alive=False)
        outcome = make_outcome("COMPLETED")
        r = FM1.evaluate(ev(pre, post_rel, post_g2, *(_g2()), make_reload(), outcome))
        self.assertEqual(r["result"], "FAIL")
        self.assertTrue(any("attempt" in m for m in r["hard_failures"]))
        self.assertTrue(any("runtime_id" in m for m in r["hard_failures"]))
        self.assertTrue(any("owner_alive" in m for m in r["hard_failures"]))
        self.assertFalse(r["checks"]["ownership_fields_unchanged"])
        self.assertTrue(r["checks"]["original_attempt_single"])
        self.assertTrue(r["checks"]["runtime_id_stable_across_reload"])
        self.assertFalse(r["checks"]["owner_alive_stable"])

    def test_fail_status_reset_ready(self):
        pre = make_snap("RUNNING", 0, 0, 0, 0)
        post_rel = make_snap("RUNNING", 3, 3, 1, 3)
        post_g2 = make_snap("READY", 0, 0, 0, 0, attempt=1)
        outcome = make_outcome("COMPLETED")
        r = FM1.evaluate(ev(pre, post_rel, post_g2, *(_g2()), make_reload(), outcome))
        self.assertEqual(r["result"], "FAIL")
        self.assertTrue(r["checks"]["g2_rejected_409"])
        self.assertFalse(r["checks"]["status_not_reset_ready"])
        self.assertTrue(any("reset to READY" in m for m in r["hard_failures"]))

    def test_fail_runtime_id_changed_across_reload(self):
        pre = make_snap("RUNNING", 0, 0, 0, 0, runtime_id=RID_RUNTIME)
        post_rel = make_snap("RUNNING", 3, 3, 1, 3, runtime_id=RID_OTHER)
        post_g2 = make_snap("RUNNING", 4, 4, 0, 4, runtime_id=RID_OTHER)
        outcome = make_outcome("COMPLETED")
        r = FM1.evaluate(ev(pre, post_rel, post_g2, *(_g2()), make_reload(), outcome))
        self.assertEqual(r["result"], "FAIL")
        self.assertFalse(r["checks"]["runtime_id_stable_across_reload"])
        self.assertTrue(any("changed across in-process reload" in m
                            for m in r["hard_failures"]))


class TestFM1ReloadNotDemonstrated(unittest.TestCase):
    """(4) Reload not demonstrated -> v3: hard FAIL (not inconclusive)."""
    def test_fail_updated_at_unchanged(self):
        pre = make_snap("RUNNING", 0, 0, 0, 0)
        post_rel = make_snap("RUNNING", 3, 3, 1, 3)
        post_g2 = make_snap("RUNNING", 4, 4, 0, 4)
        reload = make_reload(updated_changed=False,
                             pre_updated=1791213325, post_updated=1791213325)
        r = FM1.evaluate(ev(pre, post_rel, post_g2, *(_g2()), reload,
                            make_outcome("COMPLETED")))
        self.assertEqual(r["result"], "FAIL")
        self.assertFalse(r["checks"]["reload_verified"])
        self.assertTrue(any("updated_at" in m for m in r["hard_failures"]))
        self.assertEqual(r["inconclusive"], [])

    def test_fail_source_identity_missing(self):
        pre = make_snap("RUNNING", 0, 0, 0, 0)
        post_rel = make_snap("RUNNING", 3, 3, 1, 3)
        post_g2 = make_snap("RUNNING", 4, 4, 0, 4)
        reload = make_reload(source_identity=None)
        r = FM1.evaluate(ev(pre, post_rel, post_g2, *(_g2()), reload,
                            make_outcome("COMPLETED")))
        self.assertEqual(r["result"], "FAIL")
        self.assertFalse(r["checks"]["reload_verified"])
        self.assertTrue(any("source identity" in m for m in r["hard_failures"]))
        self.assertEqual(r["inconclusive"], [])


class TestFM1PrematureCompletion(unittest.TestCase):
    """(5) G1 COMPLETED before G2 -> INCONCLUSIVE (Slice-8 failure mode)."""
    def test_inconclusive_not_running_at_g2(self):
        pre = make_snap("RUNNING", 0, 0, 0, 0)
        post_rel = make_snap("COMPLETED", 8, 8, 0, 8)
        post_g2 = make_snap("COMPLETED", 8, 8, 0, 8)
        outcome = make_outcome("COMPLETED")
        r = FM1.evaluate(ev(pre, post_rel, post_g2, 409,
                            "cannot start a COMPLETED run", make_reload(), outcome))
        self.assertEqual(r["result"], "INCONCLUSIVE")
        self.assertTrue(r["checks"]["g2_rejected_409"])
        self.assertFalse(r["checks"]["g2_on_running"])
        self.assertEqual(r["hard_failures"], [])
        self.assertTrue(any("RUNNING-state fencing" in m for m in r["inconclusive"]))


class TestFM1OutcomeVariations(unittest.TestCase):
    """(6) G1 outcome variations: success / timeout / LOST_OWNER."""
    def base(self, outcome_kind, halt_reason=None):
        pre = make_snap("RUNNING", 0, 0, 0, 0)
        post_rel = make_snap("RUNNING", 3, 3, 1, 3)
        post_g2 = make_snap("RUNNING", 4, 4, 0, 4)
        reload = make_reload()
        if outcome_kind == "success":
            outcome = make_outcome("COMPLETED")
        elif outcome_kind == "timeout":
            outcome = make_outcome("HALTED", halt_reason="model request timed out after 120 seconds")
        else:
            outcome = make_outcome("HALTED", halt_reason=FM1.HALT_REASON_LOST_OWNER)
        return ev(pre, post_rel, post_g2, *(_g2()), reload, outcome)

    def test_success_pass(self):
        r = FM1.evaluate(self.base("success"))
        self.assertEqual(r["result"], "PASS")
        self.assertTrue(r["checks"]["g1_completed_via_g1"])
        self.assertEqual(r["hard_failures"], [])
        self.assertEqual(r["inconclusive"], [])

    def test_inconclusive_own_execution_timeout(self):
        r = FM1.evaluate(self.base("timeout"))
        # Fencing held; G1 halted by its OWN execution -> v3: INCONCLUSIVE.
        self.assertEqual(r["result"], "INCONCLUSIVE")
        self.assertTrue(r["checks"]["g1_controlled_termination"])
        self.assertFalse(r["checks"]["g1_continued_legitimately"])
        self.assertEqual(r["hard_failures"], [])
        self.assertTrue(r["inconclusive"])
        self.assertTrue(r["notes"])
        self.assertEqual(r["checks"]["g1_outcome"]["kind"], "halted_own_execution")

    def test_lost_owner_halt_fail(self):
        r = FM1.evaluate(self.base("lost_owner"))
        self.assertEqual(r["result"], "FAIL")
        self.assertTrue(any("LOST_OWNER" in m for m in r["hard_failures"]))
        self.assertFalse(r["checks"]["g1_continued_legitimately"])


# ============================================================================
# Mock-HTTP full-path helpers
# ============================================================================
class _FakeResponse:
    def __init__(self, status, text):
        self.status_code = status
        self.text = text

class _FakeTransport:
    def __init__(self):
        self._queues = {}
        self.calls = []

    def add(self, path, status, body):
        self._queues.setdefault(path, []).append((status, body))

    def __call__(self, method, url, headers=None, json=None, data=None, timeout=60):
        path = urlparse(url).path
        self.calls.append({"method": method, "url": url, "path": path})
        q = self._queues.get(path, [])
        return _FakeResponse(*q.pop(0) if q else (404, ""))


def _snap(status, current=0, done=None, todo=None, revision=0, attempt=1,
          runtime_id=RID_RUNTIME, owner_alive=True, halted=False, halt_reason=None):
    return {
        "run_id": RID, "status": status, "current_cartridge": current,
        "done": done if done is not None else [], "todo": todo if todo is not None else [],
        "revision": revision, "model_map": {}, "tool_ids": [], "owner_id": "test-a",
        "created_at": "2026-01-01T00:00:00Z",
        "execution": {"attempt": attempt, "runtime_id": runtime_id,
                      "owner_alive": owner_alive, "halted": halted,
                      "halt_reason": halt_reason},
    }

def _r(status, value):
    return (status, json.dumps(value) if isinstance(value, (dict, list)) else value)

def _verified_reload(t):
    t.add("/api/v1/functions/magazine/", 200,
          json.dumps({"id": "magazine", "is_active": True, "updated_at": 100,
                      "content": "old source content\n"}))
    t.add("/api/v1/functions/", 200, json.dumps({"data": []}))
    t.add("/api/v1/functions/magazine/delete", 200, json.dumps({"ok": True}))
    t.add("/api/v1/functions/create", 200, json.dumps({"id": "magazine", "status": "created"}))
    t.add("/api/v1/functions/magazine/toggle", 200, json.dumps({"ok": True}))
    t.add("/magazine", 200, MARKER + " — magazine loaded")
    t.add("/api/v1/functions/magazine/", 200,
          json.dumps({"id": "magazine", "is_active": True, "updated_at": 200,
                      "content": CTRL_SRC}))
    t.add("/api/v1/functions/", 200, json.dumps({"data": []}))

def _failed_reload(t, failure="marker"):
    t.add("/api/v1/functions/magazine/", 200,
          json.dumps({"id": "magazine", "is_active": True, "updated_at": 100,
                      "content": "old source content\n"}))
    t.add("/api/v1/functions/", 200, json.dumps({"data": []}))
    t.add("/api/v1/functions/magazine/delete", 200, json.dumps({"ok": True}))
    t.add("/api/v1/functions/create", 200, json.dumps({"id": "magazine", "status": "created"}))
    t.add("/api/v1/functions/magazine/toggle", 200, json.dumps({"ok": True}))
    if failure == "marker":
        t.add("/magazine", 404, "")
        # post-reload metadata still returns the UNCHANGED pre-record
        # (the reload never took effect), a valid dict.
        t.add("/api/v1/functions/magazine/", 200,
              json.dumps({"id": "magazine", "is_active": True, "updated_at": 100,
                          "content": "old source content\n"}))
    elif failure == "updated_at":
        t.add("/magazine", 200, MARKER + " — magazine loaded")
        t.add("/api/v1/functions/magazine/", 200,
              json.dumps({"id": "magazine", "is_active": True, "updated_at": 100,
                          "content": CTRL_SRC}))
    else:  # source mismatch
        t.add("/magazine", 200, MARKER + " — magazine loaded")
        t.add("/api/v1/functions/magazine/", 200,
              json.dumps({"id": "magazine", "is_active": True, "updated_at": 200,
                          "content": "tampered source\n"}))
    t.add("/api/v1/functions/", 200, json.dumps({"data": []}))


class TestFM1MockHTTP(unittest.TestCase):
    """Run the real main() against a scripted transport (11 full-path scenarios)."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmp_src = os.path.join(self.tmp.name, "runner_source.py")
        with open(self.tmp_src, "w") as fh:
            fh.write(CTRL_SRC)
        self.tmp_out = os.path.join(self.tmp.name, "verdict.json")
        self.p_src = mock.patch.object(FM1, "SRC", self.tmp_src)
        self.p_out = mock.patch.object(FM1, "OUT", self.tmp_out)
        self.p_base = mock.patch.object(FM1, "OWUI_BASE_URL", "http://127.0.0.1:9000")
        self.p_deploy = mock.patch.object(FM1, "OWUI_DEPLOY_TOKEN", "mock-deploy-token")
        self.p_apikey = mock.patch.object(FM1, "OWUI_TEST_A_API_KEY", "mock-api-key")
        self.p_model = mock.patch.object(FM1, "OWUI_TEST_A_MODEL_ID", "Bonsai 2 27B")
        self.p_time = mock.patch("time.sleep")
        self.p_max = mock.patch.object(FM1, "OBS_MAX_SECONDS", 3.0)
        self.p_int = mock.patch.object(FM1, "OBS_INTERVAL", 1.0)
        self.p_src.start(); self.p_out.start(); self.p_base.start()
        self.p_deploy.start(); self.p_apikey.start(); self.p_model.start()
        self.p_time.start(); self.p_max.start(); self.p_int.start()

    def tearDown(self):
        for p in (self.p_src, self.p_out, self.p_base, self.p_deploy,
                  self.p_apikey, self.p_model, self.p_time, self.p_max, self.p_int):
            p.stop()

    def _build_transport(self, run_responses, reload_ok=True, reload_failure="marker",
                         g2_status=409, g2_detail="cannot start: lock not owned (RUNNING)"):
        t = _FakeTransport()
        t.add("/api/magazine/runs", 200, json.dumps({"run_id": RID, "status": "READY", "revision": 0}))
        t.add("/api/magazine/runs/" + RID + "/start", 202,
              json.dumps({"run_id": RID, "status": "accepted", "attempt": 1, "message": "accepted"}))
        for s, b in run_responses:
            t.add("/api/magazine/runs/" + RID, s, b)
        if reload_ok:
            _verified_reload(t)
        else:
            _failed_reload(t, reload_failure)
        t.add("/api/magazine/runs/" + RID + "/start", g2_status, json.dumps({"detail": g2_detail}))
        return t

    def _run(self, run_responses, reload_ok=True, reload_failure="marker",
             g2_status=409, g2_detail="cannot start a RUNNING run"):
        t = self._build_transport(run_responses, reload_ok, reload_failure, g2_status, g2_detail)
        with mock.patch.object(FM1.httpx, "request", t):
            rc = FM1.main()
        with open(self.tmp_out) as fh:
            ev = json.load(fh)
        return rc, ev.get("verdict"), ev, t

    # -- 11 full-path scenarios --
    def test_01_pass_completed(self):
        # G1 is still RUNNING at the post-G2 boundary (so G2 is rejected
        # 409-RUNNING); it then COMPLETES during observe_g1 polling.
        pre = _snap("RUNNING", 0, [], list(range(8)), 0)
        post_rel = _snap("RUNNING", 2, [0, 1], list(range(2, 8)), 1)
        post_g2 = _snap("RUNNING", 2, [0, 1], list(range(2, 8)), 1)
        completed = _snap("COMPLETED", 8, list(range(8)), [], 2)
        rc, v, ev, _ = self._run([_r(200, pre), _r(200, post_rel),
                                 _r(200, post_g2), _r(200, completed)])
        self.assertEqual(v["result"], "PASS")
        self.assertEqual(v["checks"]["snapshot_available"], True)
        self.assertEqual(v["checks"]["g1_outcome"]["kind"], "completed")
        self.assertEqual(v["checks"]["g2_rejected_409"], True)
        self.assertEqual(v["checks"]["g2_on_running"], True)
        self.assertEqual(v["checks"]["ownership_fields_unchanged"], True)
        self.assertEqual(v["hard_failures"], [])
        self.assertEqual(v["inconclusive"], [])

    def test_02_running_at_boundary_inconclusive(self):
        pre = _snap("RUNNING")
        post_rel = _snap("RUNNING")
        post_g2 = _snap("RUNNING")
        run = [_r(200, pre), _r(200, post_rel), _r(200, post_g2)] + [
            _r(200, post_g2), _r(200, post_g2), _r(200, post_g2)]
        rc, v, ev, _ = self._run(run)
        self.assertEqual(v["result"], "INCONCLUSIVE")
        self.assertEqual(v["checks"]["g1_outcome"]["kind"], "running_at_boundary")
        self.assertEqual(v["hard_failures"], [])
        self.assertTrue(v["inconclusive"])

    def test_03_halted_lost_owner_fail(self):
        # G1 halts on its own during observation with a LOST_OWNER reason.
        # The post-G2 boundary itself is still the pre-halt RUNNING state.
        pre = _snap("RUNNING")
        post_rel = _snap("RUNNING")
        post_g2 = _snap("RUNNING")
        halted = _snap("HALTED", halted=True, halt_reason="lost-owner; stale generation")
        rc, v, ev, _ = self._run([_r(200, pre), _r(200, post_rel),
                                 _r(200, post_g2), _r(200, halted)])
        self.assertEqual(v["result"], "FAIL")
        self.assertEqual(v["checks"]["g1_outcome"]["kind"], "halted_lost_owner")
        self.assertTrue(any("LOST_OWNER" in m for m in v["hard_failures"]))

    def test_04_halted_own_execution_inconclusive(self):
        # G1 halts by its OWN execution (controlled timeout) during observation.
        # No unauthorised writes -> INCONCLUSIVE (v3: not a false PASS).
        pre = _snap("RUNNING")
        post_rel = _snap("RUNNING")
        post_g2 = _snap("RUNNING")
        halted = _snap("HALTED", halted=True,
                       halt_reason="model request timed out after 120 seconds")
        rc, v, ev, _ = self._run([_r(200, pre), _r(200, post_rel),
                                 _r(200, post_g2), _r(200, halted)])
        self.assertEqual(v["result"], "INCONCLUSIVE")
        self.assertEqual(v["checks"]["g1_outcome"]["kind"], "halted_own_execution")
        self.assertEqual(v["checks"]["ownership_fields_unchanged"], True)
        self.assertEqual(v["hard_failures"], [])
        self.assertTrue(v["inconclusive"])

    def test_05_status_reset_ready_fail(self):
        pre = _snap("RUNNING")
        post_rel = _snap("RUNNING")
        post_g2 = _snap("READY")
        run = [_r(200, pre), _r(200, post_rel), _r(200, post_g2)] + [
            _r(200, post_g2), _r(200, post_g2), _r(200, post_g2)]
        rc, v, ev, _ = self._run(run)
        self.assertEqual(v["result"], "FAIL")
        self.assertFalse(v["checks"]["status_not_reset_ready"])
        self.assertTrue(any("reset to READY" in m for m in v["hard_failures"]))

    def test_06_attempt_not_one_fail(self):
        pre = _snap("RUNNING")
        post_rel = _snap("COMPLETED", attempt=2)
        post_g2 = _snap("COMPLETED", attempt=2)
        rc, v, ev, _ = self._run([_r(200, pre), _r(200, post_rel), _r(200, post_g2)])
        self.assertEqual(v["result"], "FAIL")
        self.assertFalse(v["checks"]["original_attempt_single"])
        self.assertTrue(any("attempt != 1" in m for m in v["hard_failures"]))

    def test_07_runtime_id_changed_fail(self):
        pre = _snap("RUNNING", runtime_id=RID_RUNTIME)
        post_rel = _snap("COMPLETED", runtime_id=RID_OTHER)
        post_g2 = _snap("COMPLETED", runtime_id=RID_OTHER)
        rc, v, ev, _ = self._run([_r(200, pre), _r(200, post_rel), _r(200, post_g2)])
        self.assertEqual(v["result"], "FAIL")
        self.assertFalse(v["checks"]["runtime_id_stable_across_reload"])
        self.assertTrue(any("changed across in-process reload" in m for m in v["hard_failures"]))

    def test_08_snapshot_404_inconclusive(self):
        pre = None
        post_rel = _snap("COMPLETED")
        post_g2 = _snap("COMPLETED")
        rc, v, ev, _ = self._run([(404, ""), _r(200, post_rel), _r(200, post_g2)])
        self.assertEqual(v["result"], "INCONCLUSIVE")
        self.assertEqual(v["checks"]["snapshot_available"], False)
        # fail-closed message: "required run-state snapshot(s) unavailable ..."
        self.assertTrue(any("snapshot(s) unavailable" in m for m in v["inconclusive"]))

    def test_09_snapshot_malformed_inconclusive(self):
        pre = _snap("RUNNING")
        post_rel = None
        post_g2 = _snap("COMPLETED")
        rc, v, ev, _ = self._run([_r(200, pre), (200, "this is not json {{{"), _r(200, post_g2)])
        self.assertEqual(v["result"], "INCONCLUSIVE")
        self.assertEqual(v["checks"]["snapshot_available"], False)
        self.assertTrue(any("snapshot(s) unavailable" in m for m in v["inconclusive"]))

    def test_10_snapshot_non_object_inconclusive(self):
        pre = _snap("RUNNING")
        post_rel = _snap("RUNNING")
        # post_g2 is a bare JSON array -> req_json() -> None -> unavailable
        run = [_r(200, pre), _r(200, post_rel), (200, "[]")] + [
            (200, "[]"), (200, "[]"), (200, "[]")]
        rc, v, ev, _ = self._run(run)
        self.assertEqual(v["result"], "INCONCLUSIVE")
        self.assertEqual(v["checks"]["snapshot_available"], False)
        self.assertTrue(any("snapshot(s) unavailable" in m for m in v["inconclusive"]))

    def test_11_reload_verification_fail(self):
        pre = _snap("RUNNING")
        post_rel = _snap("RUNNING")
        post_g2 = _snap("COMPLETED")
        rc, v, ev, _ = self._run([_r(200, pre), _r(200, post_rel), _r(200, post_g2)],
                             reload_ok=False, reload_failure="marker")
        self.assertEqual(v["result"], "FAIL")
        self.assertFalse(v["checks"]["reload_verified"])
        # v3 correction B: a missing verified reload is a hard no-go.
        self.assertTrue(any("reload not verified" in m for m in v["hard_failures"]))
        self.assertTrue(any("marker missing" in m for m in v["hard_failures"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
