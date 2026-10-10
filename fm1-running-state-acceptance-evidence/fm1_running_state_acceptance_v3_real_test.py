#!/usr/bin/env python3
"""Regression tests for the *corrected* FM-1 RUNNING-state acceptance script
``fm1_running_state_acceptance_v3_real.py``.

These tests protect the five defects the uncorrected
``fm1_running_state_acceptance_v3.py`` (v3) had, which the corrected
``_real.py`` fixed:

  Defect A — schema payload: ``schema_version`` emitted as int ``1`` instead of the
             canonical string ``"1.0"``; cartridge ``id`` emitted as int ``0..7``
             instead of string ``"0".."7"``.
  Defect B — function identity: no ``FUNCTION_ID`` constant, so reload used the
             stale hard-coded paths ``/api/v1/functions/magazine/{delete|toggle}``
             (raised ``NameError`` / hit wrong routes) instead of the real OWUI
             ``/api/v1/functions/id/<id>/{delete|toggle}``.
  Defect C — HTTP method: G1/G2 ``/start`` calls omitted ``method=`` so they
             defaulted to GET instead of POST.
  Defect D — metadata validation: ``get_function_meta()`` accepted any 2xx (incl.
             the SPA HTML shell) without requiring a JSON dict ``id == "magazine"``.
  Defect E — end-to-end: the corrected ``main()`` must reach a genuine PASS through
             the REAL corrected payload/method/path/metadata logic.

The shared ``evaluate()`` classification logic is byte-identical in v3 and _real
and is already covered by the 22 tests in ``fm1_running_state_acceptance_v3_test.py``
— these tests deliberately do NOT duplicate that logic.  Only the corrected-script
behaviour (payloads, methods, routes, metadata validation, and the live path) is
asserted.  No live API is touched: a scripted _FakeTransport dispatches on the URL
path with a FIFO queue per path and records every (method, path, json_body).

Run with the runner venv:
    .venvs/magazine-runner/bin/python -m unittest fm1_running_state_acceptance_v3_real_test -v
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
MARKER = "Run Builder"

def _load_fm1():
    spec = importlib.util.spec_from_file_location(
        "fm1_running_state_acceptance_v3_real",
        os.path.join(HERE, "fm1_running_state_acceptance_v3_real.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

FM1 = _load_fm1()

# ============================================================================
# Canonical schema module (magazine/schema.py).  Self-contained (json/typing only),
# loadable by file path without polluting sys.path.
#
# This test ships inside the Magazine repository (fm1-running-state-acceptance-
# evidence/).  The canonical schema package sits at <repo-root>/magazine/schema.py,
# i.e. one level above this directory.  The path is resolved from __file__ so the
# suite works from any current working directory and any fresh checkout, with no
# dependence on the original /workspace/evidence/ sibling layout.
# ============================================================================
def _load_schema():
    path = os.path.join(HERE, "..", "magazine", "schema.py")
    if not os.path.isfile(path):
        raise FileNotFoundError(
            "cannot locate the canonical magazine schema; the FM-1 evidence suite "
            "must be run from a Magazine checkout where magazine/schema.py is one "
            "level above fm1-running-state-acceptance-evidence/ (path=%s)" % path
        )
    spec = importlib.util.spec_from_file_location("mag_schema", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

SCHEMA = _load_schema()

# ============================================================================
# Scripted transport (extends the existing-test pattern): FIFO per path and
# records method/url/path/JSON body so regressions can assert the corrected
# method, route and payload.
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
        if isinstance(body, (dict, list)):
            body = json.dumps(body)
        self._queues.setdefault(path, []).append((status, body))

    def __call__(self, method, url, headers=None, json=None, data=None, timeout=60):
        path = urlparse(url).path
        self.calls.append({"method": method, "url": url, "path": path,
                           "json": json, "data": data})
        q = self._queues.get(path, [])
        return _FakeResponse(*q.pop(0) if q else (404, ""))

    def calls_on(self, path):
        return [c for c in self.calls if c["path"] == path]

# ============================================================================
# Snapshot helpers (match the existing full-path tests; ownership lives under
# the nested "execution" dict, as evaluate() / _analyze_g1_outcome expect).
# ============================================================================
def _snap(status, current=0, done=None, todo=None, revision=0, attempt=1,
          runtime_id=RID_RUNTIME, owner_alive=True, halted=False, halt_reason=None):
    return {
        "run_id": RID, "status": status, "current_cartridge": current,
        "done": done if done is not None else [],
        "todo": todo if todo is not None else [],
        "revision": revision, "model_map": {}, "tool_ids": [], "owner_id": "test-a",
        "created_at": "2026-01-01T00:00:00Z",
        "execution": {"attempt": attempt, "runtime_id": runtime_id,
                      "owner_alive": owner_alive, "halted": halted,
                      "halt_reason": halt_reason},
    }

def _r(status, value):
    return (status, json.dumps(value) if isinstance(value, (dict, list)) else value)

# Corrected reload: id-based OWUI routes (the defect-B fix).  The single
# "/api/v1/functions/magazine/" response carries a valid magazine record so
# get_function_meta() breaks out of the single-endpoint loop early; the list
# "/api/v1/functions/" entry is still consumed (always queried).
def _verified_reload_real(t):
    t.add("/api/v1/functions/magazine/", 200,
          json.dumps({"id": "magazine", "is_active": True, "updated_at": 100,
                      "content": "old source content\n"}))
    t.add("/api/v1/functions/", 200, json.dumps({"data": []}))
    # CORRECTED id-based routes
    t.add("/api/v1/functions/id/magazine/delete", 200, json.dumps({"ok": True}))
    t.add("/api/v1/functions/create", 200,
          json.dumps({"id": "magazine", "status": "created"}))
    t.add("/api/v1/functions/id/magazine/toggle", 200, json.dumps({"ok": True}))
    t.add("/magazine", 200, MARKER + " — magazine loaded")
    # post: valid magazine, updated_at advanced, content == local source (exact)
    t.add("/api/v1/functions/magazine/", 200,
          json.dumps({"id": "magazine", "is_active": True, "updated_at": 200,
                      "content": CTRL_SRC}))
    t.add("/api/v1/functions/", 200, json.dumps({"data": []}))

# ============================================================================
# Base: start all the static patches (SRC/OUT/credentials/clock/observe bounds)
# once; each test then patches only the transport it needs.
# ============================================================================
class Base(unittest.TestCase):
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
        for p in (self.p_src, self.p_out, self.p_base, self.p_deploy,
                  self.p_apikey, self.p_model, self.p_time, self.p_max, self.p_int):
            p.start()

    def tearDown(self):
        for p in (self.p_src, self.p_out, self.p_base, self.p_deploy,
                  self.p_apikey, self.p_model, self.p_time, self.p_max, self.p_int):
            p.stop()

    def _wire_full_pass(self, t):
        # create probe run (string schema + string ids)
        t.add("/api/magazine/runs", 200,
              json.dumps({"run_id": RID, "status": "READY", "revision": 0}))
        # G1 start (202)
        t.add("/api/magazine/runs/" + RID + "/start",
              202, json.dumps({"status": "accepted", "attempt": 1}))
        # pre / post_rel / post_g2 snapshots (RUNNING) + observe poll (COMPLETED)
        pre = _snap("RUNNING", 0, [], list(range(8)), 0)
        post_rel = _snap("RUNNING", 2, [0, 1], list(range(2, 8)), 1)
        post_g2 = _snap("RUNNING", 2, [0, 1], list(range(2, 8)), 1)
        completed = _snap("COMPLETED", 8, list(range(8)), [], 2)
        t.add("/api/magazine/runs/" + RID, 200, json.dumps(pre))
        t.add("/api/magazine/runs/" + RID, 200, json.dumps(post_rel))
        t.add("/api/magazine/runs/" + RID, 200, json.dumps(post_g2))
        t.add("/api/magazine/runs/" + RID, 200, json.dumps(completed))
        # corrected reload (module-level helper)
        _verified_reload_real(t)
        # G2 start (409 rejected while G1 RUNNING)
        t.add("/api/magazine/runs/" + RID + "/start", 409,
              json.dumps({"detail": "cannot start a RUNNING run"}))

# ============================================================================
# Defect A — schema payload: create_probe_run must emit canonical strings
# ============================================================================
class TestFM1SchemaPayload(Base):

    def _create_payload(self):
        t = _FakeTransport()
        t.add("/api/magazine/runs", 200, json.dumps({"run_id": RID, "status": "READY"}))
        with mock.patch.object(FM1.httpx, "request", t):
            FM1.create_probe_run()
        return json.loads(t.calls[0]["json"]["magazine"])

    def test_schema_version_is_canonical_string(self):
        mag = self._create_payload()
        self.assertEqual(mag["schema_version"], "1.0")
        self.assertIsInstance(mag["schema_version"], str)

    def test_cartridge_ids_are_strings(self):
        mag = self._create_payload()
        ids = [c["id"] for c in mag["cartridges"]]
        self.assertEqual(len(ids), FM1.N_CARTRIDGES)
        for i, cid in enumerate(ids):
            self.assertEqual(cid, str(i))
            self.assertIsInstance(cid, str)
            self.assertNotIsInstance(cid, int)

# ============================================================================
# Defect A — canonical loader rejects the OLD int payloads, accepts the fixed one
# ============================================================================
class TestFM1CanonicalSchema(unittest.TestCase):

    def test_rejects_int_schema_version(self):
        doc = {"schema_version": 1,
               "cartridges": [{"id": "0", "job_type": "k", "prompt": "p"}]}
        with self.assertRaises(SCHEMA.MagazineSchemaError):
            SCHEMA.validate_magazine(doc)

    def test_rejects_int_cartridge_id(self):
        with self.assertRaises(SCHEMA.MagazineSchemaError):
            SCHEMA.validate_cartridge({"id": 0, "job_type": "k", "prompt": "p"})

    def test_accepts_corrected_payload(self):
        doc = {"schema_version": "1.0",
               "cartridges": [{"id": "0", "job_type": "k", "prompt": "p"},
                              {"id": "1", "job_type": "k", "prompt": "p"}]}
        self.assertIs(SCHEMA.validate_magazine(doc), doc)
        self.assertEqual(doc["schema_version"], "1.0")

# ============================================================================
# Defect B — FUNCTION_ID defined; reload uses id-based routes
# ============================================================================
class TestFM1FunctionIdentity(Base):

    def test_function_id_defined(self):
        self.assertEqual(FM1.FUNCTION_ID, "magazine")

    def test_reload_uses_function_id_routes(self):
        t = _FakeTransport()
        # pre: single valid + list
        t.add("/api/v1/functions/magazine/", 200,
              json.dumps({"id": "magazine", "is_active": True, "updated_at": 100,
                          "content": "old"}))
        t.add("/api/v1/functions/", 200, json.dumps({"data": []}))
        t.add("/api/v1/functions/id/magazine/delete", 200, json.dumps({"ok": True}))
        t.add("/api/v1/functions/create", 200, json.dumps({"id": "magazine"}))
        t.add("/api/v1/functions/id/magazine/toggle", 200, json.dumps({"ok": True}))
        t.add("/magazine", 200, MARKER + " — loaded")
        t.add("/api/v1/functions/magazine/", 200,
              json.dumps({"id": "magazine", "is_active": True, "updated_at": 200,
                          "content": CTRL_SRC}))
        t.add("/api/v1/functions/", 200, json.dumps({"data": []}))
        with mock.patch.object(FM1.httpx, "request", t):
            reload_info = FM1.deploy_reload(CTRL_SRC)
        self.assertEqual(len(t.calls_on("/api/v1/functions/id/magazine/delete")), 1)
        self.assertEqual(t.calls_on("/api/v1/functions/id/magazine/delete")[0]["method"], "DELETE")
        self.assertEqual(len(t.calls_on("/api/v1/functions/id/magazine/toggle")), 1)
        self.assertEqual(t.calls_on("/api/v1/functions/id/magazine/toggle")[0]["method"], "POST")
        # the stale v3 routes must NOT be hit
        self.assertFalse(
            any(c["path"] == "/api/v1/functions/magazine/delete" for c in t.calls))
        self.assertFalse(
            any(c["path"] == "/api/v1/functions/magazine/toggle" for c in t.calls))
        self.assertEqual(reload_info["source_identity"], "exact")
        self.assertTrue(reload_info["marker"])

# ============================================================================
# Defect D — get_function_meta requires JSON dict id=="magazine"; rejects SPA 200
# ============================================================================
class TestFM1MetadataValidation(Base):

    def _meta(self, singles, list_body):
        t = _FakeTransport()
        for pth, (status, body) in singles:
            t.add(pth, status, body)
        t.add("/api/v1/functions/", *list_body)
        with mock.patch.object(FM1.httpx, "request", t):
            status, rec = FM1.get_function_meta()
        return status, rec

    def test_rejects_sp_html_200(self):
        html = "<html><head><title>Run Builder</title></head></html>"
        status, rec = self._meta(
            [("/api/v1/functions/magazine/", (200, html)),
             ("/api/v1/functions/magazine", (200, html)),
             ("/api/v1/functions/id/magazine", (200, html))],
            (200, json.dumps({"data": []})))
        self.assertIsNone(rec)

    def test_accepts_valid_record(self):
        status, rec = self._meta(
            [("/api/v1/functions/magazine/",
              (200, json.dumps({"id": "magazine", "is_active": True,
                                "updated_at": 100, "content": "x"})))],
            (200, json.dumps({"data": []})))
        self.assertEqual(rec["id"], "magazine")
        self.assertTrue(rec["is_active"])

    def test_rejects_wrong_function_id(self):
        status, rec = self._meta(
            [("/api/v1/functions/magazine/", (200, json.dumps({"id": "other-fn"}))),
             ("/api/v1/functions/magazine", (200, json.dumps({"id": "other-fn"}))),
             ("/api/v1/functions/id/magazine", (200, json.dumps({"id": "other-fn"})))],
            (200, json.dumps({"data": []})))
        self.assertIsNone(rec)

class TestFM1CorrectedMainPath(Base):

    def test_full_main_path_pass_corrected(self):
        t = _FakeTransport()
        self._wire_full_pass(t)
        with mock.patch.object(FM1.httpx, "request", t):
            code = FM1.main()
        with open(self.tmp_out) as fh:
            ev = json.load(fh)
        # Defect E: genuine PASS through the CORRECTED script
        self.assertEqual(code, 0)
        self.assertEqual(ev["verdict"]["result"], "PASS")
        self.assertEqual(ev["verdict"]["hard_failures"], [])
        self.assertEqual(ev["verdict"]["inconclusive"], [])
        self.assertTrue(ev["verdict"]["checks"]["reload_verified"])
        self.assertTrue(ev["verdict"]["checks"]["g2_rejected_409"])
        # Defect C: G1 and G2 /start were POST (old v3 defaulted to GET)
        starts = t.calls_on("/api/magazine/runs/" + RID + "/start")
        self.assertEqual(len(starts), 2)
        for s in starts:
            self.assertEqual(s["method"], "POST")
        # Defect B: reload used id-based routes end-to-end
        self.assertEqual(len(t.calls_on("/api/v1/functions/id/magazine/delete")), 1)
        self.assertEqual(len(t.calls_on("/api/v1/functions/id/magazine/toggle")), 1)
        self.assertEqual(len(t.calls_on("/api/v1/functions/create")), 1)
        self.assertFalse(
            any(c["path"] == "/api/v1/functions/magazine/delete" for c in t.calls))
        self.assertFalse(
            any(c["path"] == "/api/v1/functions/magazine/toggle" for c in t.calls))
        # Defect A: create payload used string schema_version + string cartridge ids
        create_call = next(c for c in t.calls if c["path"] == "/api/magazine/runs")
        mag = json.loads(create_call["json"]["magazine"])
        self.assertEqual(mag["schema_version"], "1.0")
        self.assertIsInstance(mag["schema_version"], str)
        self.assertTrue(all(isinstance(c["id"], str) for c in mag["cartridges"]))


if __name__ == "__main__":
    unittest.main(argv=["", "-v"])
