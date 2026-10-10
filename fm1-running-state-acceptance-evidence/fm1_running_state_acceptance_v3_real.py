#!/usr/bin/env python3
"""FM-1 (RUNNING-state fencing) acceptance test — corrected v3.

This is the corrected version of ``fm1_running_state_acceptance_v2.py`` (v2).
It carries over v2's three corrections and additionally fixes two confirmed
v2 defects that made the script unable to act as a meaningful *live* acceptance
check, plus the fail-closed snapshot handling the v3 mock-HTTP test suite
exercises.

  * **Defect A — HTTP response handling.**  In v2 every ``req()`` call returned
    the response body as a *text string*, yet the run-state snapshot code did
    ``run if isinstance(run, dict) else {}``.  A ``str`` is not a ``dict``, so
    every observed run state collapsed to ``{}`` and the evaluator never saw
    real data (in the live ``main()`` / ``observe_g1()`` path it always failed
    with ``attempt != 1 (None)``).  v3 adds ``req_json()``, which parses the
    JSON body and returns the real dict (or ``None`` when the body is empty,
    invalid JSON, a non-object JSON value, or a connection error).  ``main()``
    and ``observe_g1()`` now use ``req_json()`` for the run-state GETs.  The
    ``/magazine`` marker (raw text) still uses the text-returning ``req()``.

  * **Defect B — G1 outcome classification.**  v2 treated
    ``RUNNING-at-the-observation-boundary`` and a ``HALTED`` run with a
    "own-execution" halt reason as *legitimate* (PASS).  The FM-1 resolution
    requires that only a confirmed ``COMPLETED`` (or a pre-specified authorised
    termination) may yield PASS.  v3:
        * ``COMPLETED``              ->  PASS candidate
        * ``HALTED`` lost-owner     ->  hard FAIL (ownership/stale-generation)
        * ``HALTED`` own-execution  ->  INCONCLUSIVE (no confirmed outcome;
                                        no authorised termination specified)
        * ``RUNNING`` at boundary   ->  INCONCLUSIVE (timeout, not failure)
        * ``unknown``               ->  INCONCLUSIVE

  * **Fail-closed snapshot availability.**  A required run-state snapshot that
    is ``None`` (HTTP 404, empty body, malformed JSON, or a non-object body)
    is treated as an *unavailable observation*, never as a valid empty run
    state.  ``evaluate()`` records an explicit INCONCLUSIVE diagnostic and
    never yields PASS.

It implements the three corrections (field partition / write attribution
(correction A), reload+source-identity verification (correction B), and bounded
G1 outcome observation (correction C)) and uses the runner's REAL HTTP payload
field names (``current_cartridge`` / ``execution``.``attempt`` / ``runtime_id`` /
``owner_alive`` / ``halted`` / ``halt_reason``).

The acceptance sequence (per the FM-1 resolution, "REVISE" decision):

  1. G1 = an 8-cartridge multi-run, started by the ORIGINAL generation
     (pre-reload). Record the pre-reload state.
  2. In-process reload of the magazine event function. Verify (correction B)
     that the reload actually happened AND the post-reload source identity
     matches the committed runner source, and that the run's ``runtime_id``
     is unchanged (same process, not a replacement — the expected in-process
     semantics, NOT a failure).
  3. G2 = a second ``/start`` on the same run, issued by the POST-reload
     generation. It must be rejected with 409 ``"cannot start a RUNNING run"``.
  4. Observe G1 through its outcome (correction C) and confirm the run was
     not disturbed by the reload/G2.

Classification (no false PASS; no invented write-audit):
  * FAIL        — a hard fencing invariant is violated.
  * INCONCLUSIVE— a required piece of evidence is missing/undemonstrable, OR
                  a required snapshot is unavailable, OR the run was not RUNNING
                  at G2 (Slice-8 failure mode), OR G1's outcome was not
                  confirmed.
  * PASS        — every hard invariant held and the reload was verified AND
                  G1 reached a confirmed ``COMPLETED``.

Credential policy (preserved):
  * OWUI_TEST_A_API_KEY and OWUI_DEPLOY_TOKEN are read ONLY from the
    environment at runtime. Never hard-coded; never echoed in full.
  * The script is importable without side effects (no live HTTP at import).
    The v3 test suite injects a *mock* HTTP transport and *mock* credentials;
    it never reaches a live service.

Field partition (correction A — write attribution, two classes):
  * AUTHORISED G1 progress (may change while G1 legitimately executes):
      status (RUNNING->COMPLETED/HALTED), current_cartridge, done, todo,
      revision.
  * UNAUTHORISED stale writes (must NOT change):
      attempt, runtime_id, owner_alive, halted, halt_reason, model_map,
      tool_ids, owner_id.
  There is no per-write audit log in the runner; the strongest available
  evidence is ownership/admission invariance + 409 rejection + no re-admission
  (status not reset to READY) + no owner_alive flip + attempt == 1."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import time

import httpx

# ---------------------------------------------------------------------------
# Configuration (env-driven; nothing networked at import time)
# ---------------------------------------------------------------------------
OWUI_BASE_URL = os.getenv("OWUI_BASE_URL", "http://host.docker.internal:3000")
OWUI_TEST_A_API_KEY = os.getenv("OWUI_TEST_A_API_KEY")
OWUI_DEPLOY_TOKEN = os.getenv("OWUI_DEPLOY_TOKEN")
OWUI_TEST_A_MODEL_ID = os.getenv("OWUI_TEST_A_MODEL_ID", "Bonsai 2 27B")

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "magazine-webui-runner-01", "magazine_ef.py")
OUT = os.path.join(HERE, "fm1_running_state_acceptance_v3.json")

# Multi-run size: 8 cartridges x ~2.8s ≈ 22s. G2 lands ~10-12s after G1 start
# (at cartridge 3-4), so G1 is legitimately RUNNING when G2 is issued.
N_CARTRIDGES = 8

# Bounded observation window for G1's outcome (correction C).
OBS_MAX_SECONDS = 90.0
OBS_INTERVAL = 1.5

MARKER = "Run Builder"
FUNCTION_ID = "magazine"

# ---------------------------------------------------------------------------
# Field partition (correction A)
# ---------------------------------------------------------------------------
# Authorised G1 progress fields (may change during legitimate G1 execution).
PROGRESS_FIELDS = ("status", "current_cartridge", "done", "todo", "revision")
# Unauthorised stale-write fields (must NOT change). A change is a fencing
# violation.
OWNERSHIP_FIELDS = (
    "attempt",
    "runtime_id",
    "owner_alive",
    "halted",
    "halt_reason",
    "model_map",
    "tool_ids",
    "owner_id",
)
# Halt reasons that are NOT "G1's own execution" (treated as anomalous in
# an in-process reload, where a stale generation cannot claim the run).
HALT_REASON_LOST_OWNER = "lost-owner"
HALT_REASON_OPERATOR_RECOVERED = "operator-recovered"


def _lost_owner_reason(reason):
    """True if the halt reason indicates a lost-runtime / stale-generation claim."""
    if not reason:
        return False
    r = str(reason).lower()
    return (HALT_REASON_LOST_OWNER in r) or ("lost owner" in r) or (
        "lost_owner" in r
    )


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
def req(path, method="GET", headers=None, json_body=None, data=None,
       timeout=60):
    """Authenticated OWUI request. Returns (status_code, text)."""
    h = dict(headers or {})
    h["Authorization"] = "Bearer " + (OWUI_TEST_A_API_KEY or "")
    url = OWUI_BASE_URL + path
    try:
        r = httpx.request(method, url, headers=h, json=json_body,
                          data=data, timeout=timeout)
    except httpx.RequestException as e:
        return -1, repr(e)
    return r.status_code, r.text


def req_json(path, method="GET", headers=None, json_body=None, data=None,
             timeout=60):
    """Authenticated OWUI request that parses the response body as JSON.

    Returns (status_code, payload).  ``payload`` is the decoded JSON object when
    the response body is a JSON *object*; it is ``None`` in any other situation
    — a connection error (status -1), an empty/whitespace body, an invalid / non-
    JSON body, or a JSON body that is not an object (e.g. a bare string, number,
    or list).  Callers must treat a ``None`` payload as an *unavailable / badly
    malformed observation*, never as a valid empty run state.

    This is the v3 fix for Defect A: v2 read the body as text and the downstream
    ``isinstance(run, dict) else {}`` collapsed valid JSON run-states to ``{}``.
    """
    status, text = req(path, method, headers=headers, json_body=json_body,
                       data=data, timeout=timeout)
    if status < 0:
        return status, None
    if not text or not text.strip():
        return status, None
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        return status, None
    if not isinstance(payload, dict):
        return status, None
    return status, payload


def deploy_req(path, method="GET", json_body=None, data=None,
               timeout=60):
    """Deploy-token request. Returns (status_code, text)."""
    token = OWUI_DEPLOY_TOKEN or ""
    h = {"Authorization": "Bearer " + token}
    if method in ("POST", "PUT", "PATCH", "DELETE"):
        h["Content-Type"] = "application/json"
    url = OWUI_BASE_URL + path
    try:
        r = httpx.request(method, url, headers=h, json=json_body,
                          data=data, timeout=timeout)
    except httpx.RequestException as e:
        return -1, repr(e)
    return r.status_code, r.text


def _json_detail(raw):
    """Extract the ``detail`` from a JSON error body."""
    if not raw:
        return None
    try:
        d = json.loads(raw)
        if isinstance(d, dict):
            return d.get("detail")
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Source-identity (correction B)
# ---------------------------------------------------------------------------
def _sha256_text(text):
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def _hash_pair(text):
    """Return (raw_sha256, normalized_sha256). Normalized collapses CRLF->LF."""
    if text is None:
        return None, None
    return _sha256_text(text), _sha256_text(text.replace("\r\n", "\n"))


def _content_identity(post_content, source_text):
    """Classify how well the post-reload content matches the local source.

    Returns "exact" (strongest), "normalized" (CRLF-normalised match), or
    None (no match).
    """
    if post_content is None or source_text is None:
        return None
    if post_content == source_text:
        return "exact"
    if post_content.replace("\r\n", "\n") == source_text.replace("\r\n", "\n"):
        return "normalized"
    # Permissive fallback: both stripped of trailing whitespace per line and
    # overall.  This is the weakest tier; still requires full match.
    if post_content.rstrip() == source_text.rstrip():
        return "normalized"
    return None


# ---------------------------------------------------------------------------
# Function metadata capture (for reload verification)
# ---------------------------------------------------------------------------
def _function_record(base_list_body, single_record, single_status):
    """Best-effort extraction of the ``magazine`` function record."""
    # single_record endpoint wins if present.
    if single_status is not None and 200 <= single_status < 300:
        try:
            d = json.loads(single_record)
        except Exception:
            d = None
        if isinstance(d, dict) and d.get("id") == "magazine":
            return d
    # Fall back to the list endpoint (used by deploy.py).
    if base_list_body:
        try:
            data = json.loads(base_list_body)
        except Exception:
            return None
        if isinstance(data, dict):
            for key in ("record", "function", "data"):
                if key in data:
                    data = data[key]
        if isinstance(data, dict) and data.get("id") == "magazine":
            return data
        if isinstance(data, list):
            for fn in data:
                if isinstance(fn, dict) and fn.get("id") == "magazine":
                    return fn
    return None


def get_function_meta():
    """Fetch the ``magazine`` function record via the deploy token.

    Tries the single-record endpoints first, then the list endpoint.
    Returns (status, record).
    """
    # single-record attempts (best effort; first *valid JSON function record* wins).
    # The SPA HTML fallback also returns HTTP 200, so a bare 2xx check is
    # insufficient: require the body to parse to a dict with id=="magazine".
    single_status = None
    single_record = None
    for pth in ("/api/v1/functions/magazine/",
                "/api/v1/functions/magazine",
                "/api/v1/functions/id/magazine"):
        s, b = deploy_req(pth, method="GET")
        if single_status is None:
            single_status, single_record = s, b
        if 200 <= s < 300:
            try:
                rec = json.loads(b)
            except ValueError:
                rec = None
            if isinstance(rec, dict) and rec.get("id") == "magazine":
                single_status, single_record = s, b
                break
    # list endpoint.
    list_status, list_body = deploy_req("/api/v1/functions/", method="GET")
    rec = _function_record(list_body, single_record, single_status)
    status = single_status if single_status is not None else list_status
    return status, rec


# ---------------------------------------------------------------------------
# Reload + verification (correction B)
# ---------------------------------------------------------------------------
def deploy_reload(source_text):
    """Perform the in-process reload and verify it.

    Returns a dict consumed by ``evaluate`` / ``_reload_verdict``.
    """
    pre_status, pre_record = get_function_meta()
    op_statuses = {"delete": None, "create": None, "toggle": None}

    # 1. DELETE  (real OWUI endpoint: /api/v1/functions/id/{id}/delete)
    s, b = deploy_req("/api/v1/functions/id/" + FUNCTION_ID + "/delete", method="DELETE")
    op_statuses["delete"] = s

    # 2. CREATE (full current source)
    create_body = {
        "id": "magazine",
        "name": "Magazine",
        "content": source_text,
        "meta": {
            "description": "Magazine runner — authenticated, DATA_DIR-persistent, "
                           "server-side admission validation; upload .magazine, "
                           "preview cartridges, map models/tools, create a "
                           "durable READY run.",
            "manifest": {},
        },
    }
    s, b = deploy_req("/api/v1/functions/create", method="POST", json_body=create_body)
    op_statuses["create"] = s

    # 3. TOGGLE ON (triggers FUNCTION_ENABLED -> _install_routes -> in-process
    #    module re-import)  (real OWUI endpoint: POST /api/v1/functions/id/{id}/toggle)
    s, b = deploy_req("/api/v1/functions/id/" + FUNCTION_ID + "/toggle", method="POST",
                      json_body={})
    op_statuses["toggle"] = s

    time.sleep(5)

    # 4. VERIFY GET /magazine marker.
    marker_status, marker_body = req("/magazine")
    marker = (MARKER in marker_body) and (200 <= marker_status < 300)

    # 5. Post-reload function metadata.
    post_status, post_record = get_function_meta()

    source_identity = _content_identity(post_record.get("content"), source_text)
    pre_updated = (pre_record or {}).get("updated_at")
    post_updated = (post_record or {}).get("updated_at")
    updated_at_changed = (pre_updated is not None and post_updated is not None
                          and pre_updated != post_updated)

    return {
        "op_statuses": op_statuses,
        "pre_status": pre_status,
        "post_status": post_status,
        "pre_record": pre_record,
        "post_record": post_record,
        "marker": bool(marker),
        "marker_status": marker_status,
        "pre_updated_at": pre_updated,
        "post_updated_at": post_updated,
        "updated_at_changed": updated_at_changed,
        "source_identity": source_identity,
        "post_is_active": (post_record or {}).get("is_active"),
        "pre_is_active": (pre_record or {}).get("is_active"),
        "source_sha256_raw": _sha256_text(source_text) if source_text else None,
        "source_sha256_norm": _sha256_text(source_text.replace("\r\n", "\n"))
        if source_text else None,
        "post_content_sha256_raw": _sha256_text((post_record or {}).get("content"))
        if (post_record or {}).get("content") else None,
        "error": None,
    }


# ---------------------------------------------------------------------------
# G1 run creation + start
# ---------------------------------------------------------------------------
def create_probe_run():
    """Create a multi-cartridge (N_CARTRIDGES) probe run and return (status, record)."""
    N = N_CARTRIDGES
    # 8 cartridges cycling across 5 distinct logical model keys.
    model_keys = tuple("key-%02d" % (i % 5) for i in range(N))
    prompts = (
        "Summarize this passage in two sentences.",
        "List the main causes, ranked.",
        "Rewrite this in plain language for a non-expert.",
        "What would a skeptic object to, and reply briefly.",
        "Turn this into a three-point recommendation.",
    )
    cartridges = []
    for cid in range(N):
        cartridges.append({
            "id": str(cid),
            "job_type": model_keys[cid],
            "prompt": prompts[cid % len(prompts)],
        })
    magazine = json.dumps({"schema_version": "1.0", "cartridges": cartridges})
    model_map = {k: OWUI_TEST_A_MODEL_ID for k in model_keys}
    body = {"magazine": magazine, "model_map": model_map, "tool_ids": []}
    st, ct = req("/api/magazine/runs", method="POST", json_body=body)
    try:
        record = json.loads(ct) if ct else {}
    except Exception:
        record = {}
    return st, record


# ---------------------------------------------------------------------------
# G1 outcome observation (correction C)
# ---------------------------------------------------------------------------
def observe_g1(run_id, first_snapshot, max_seconds=OBS_MAX_SECONDS,
               interval=OBS_INTERVAL):
    """Poll the run from the first (post-G2) snapshot for a bounded period.

    Returns a dict: final_status, halt_reason, history, observed_seconds.
    Terminates early if the run reaches a terminal status (COMPLETED/HALTED).
    """
    def snap_status(s):
        return (s or {}).get("status")

    first = first_snapshot or {}
    final_status = snap_status(first)
    history = [{"t": 0.0, "status": final_status,
                "current_cartridge": first.get("current_cartridge"),
                "revision": first.get("revision")}]
    elapsed = 0.0
    cur = first
    terminal = {"COMPLETED", "HALTED", "error"}
    while final_status not in terminal and elapsed < max_seconds:
        time.sleep(interval)
        elapsed += interval
        _, run = req_json("/api/magazine/runs/" + run_id)
        cur = run if isinstance(run, dict) else {}
        final_status = cur.get("status")
        history.append({"t": round(elapsed, 1),
                        "status": final_status,
                        "current_cartridge": cur.get("current_cartridge"),
                        "revision": cur.get("revision")})
    halt_reason = None
    if final_status == "HALTED":
        halt_reason = (cur.get("execution") or {}).get("halt_reason")
    return {
        "final_status": final_status,
        "halt_reason": halt_reason,
        "history": history,
        "observed_seconds": round(elapsed, 1),
    }


# ---------------------------------------------------------------------------
# Pure evaluation (correction A/C; the logic the live test AND the offline
# tests share, so the offline tests verify exactly what the live run uses).
# ---------------------------------------------------------------------------
def _field(snap, name):
    """Get a field from a run snapshot; nested execution fields come from ``execution``."""
    if snap is None:
        return None
    if name in ("attempt", "runtime_id", "owner_alive", "halted", "halt_reason"):
        return (snap.get("execution") or {}).get(name)
    return snap.get(name)


def _analyze_g1_outcome(post_g2, outcome):
    """Classify G1's post-G2 outcome (correction C)."""
    final_status = (outcome.get("final_status") or (post_g2 or {}).get("status"))
    halt_reason = outcome.get("halt_reason")
    history = outcome.get("history") or []
    if final_status == "COMPLETED":
        return {"kind": "completed", "status": final_status,
                "halt_reason": halt_reason, "history": history}
    if final_status == "HALTED":
        if _lost_owner_reason(halt_reason):
            return {"kind": "halted_lost_owner", "status": final_status,
                    "halt_reason": halt_reason, "history": history}
        return {"kind": "halted_own_execution", "status": final_status,
                "halt_reason": halt_reason, "history": history}
    if final_status == "RUNNING":
        return {"kind": "running_at_boundary", "status": final_status,
                "halt_reason": halt_reason, "history": history}
    return {"kind": "unknown", "status": final_status,
            "halt_reason": halt_reason, "history": history}


def _reload_verdict(reload):
    """Return (verified, detail) for correction B."""
    if reload is None:
        return False, "reload evidence missing"
    pre = reload.get("pre_record")
    post = reload.get("post_record")
    if pre is None or post is None:
        return False, ("function metadata fetch failed "
                       "(pre_status=%r post_status=%r)"
                       % (reload.get("pre_status"), reload.get("post_status")))
    ops = reload.get("op_statuses") or {}
    bad_ops = {k: v for k, v in ops.items()
               if not isinstance(v, int) or not (200 <= v < 300)}
    if bad_ops:
        return False, "reload ops not all 2xx: %r" % bad_ops
    if not reload.get("marker"):
        return False, "GET /magazine marker missing after reload "
        "(status=%r)" % reload.get("marker_status")
    if not reload.get("updated_at_changed"):
        return False, ("updated_at did not change across reload "
                       "(pre=%r post=%r)"
                       % (reload.get("pre_updated_at"), reload.get("post_updated_at")))
    if reload.get("source_identity") is None:
        return False, "source identity not verified "
        "(post content differs from local runner source)"
    if not reload.get("post_is_active"):
        return False, "function is_active is False after reload"
    return True, "reload verified (marker, updated_at change, source identity "
    "=%s, is_active)" % reload.get("source_identity")


def evaluate(ev):
    """Pure classification of the collected FM-1 evidence.

    Returns:
        {
            "result": "PASS" | "FAIL" | "INCONCLUSIVE",
            "hard_failures": [...],
            "inconclusive": [...],
            "checks": {check_name: value},
        }
    """
    pre = ev.get("pre")
    post_rel = ev.get("post_rel")
    post_g2 = ev.get("post_g2")
    g2 = ev.get("g2") or {}
    reload = ev.get("reload") or {}
    outcome = ev.get("g1_outcome") or {}
    g2_status = g2.get("status")
    g2_detail = g2.get("detail") or ""

    checks = {}
    hard = []
    inconc = []
    notes = []

    # v3: fail-closed snapshot availability.  A required run-state snapshot
    # that is None (HTTP 404, empty body, malformed JSON, or a non-object
    # body) is an *unavailable observation*, never a valid empty run state.
    # We fail closed: an explicit INCONCLUSIVE, independent of the G2 / reload
    # outcome (never a false PASS).
    missing_snaps = [
        name for name, snap in (("pre", pre), ("post_rel", post_rel),
                                 ("post_g2", post_g2))
        if not isinstance(snap, dict)
    ]
    if missing_snaps:
        return {
            "result": "INCONCLUSIVE",
            "hard_failures": [],
            "inconclusive": [
                "required run-state snapshot(s) unavailable (HTTP 404 / empty "
                "/ malformed / non-object response): %s \u2014 fail-closed, "
                "non-PASS" % ", ".join(missing_snaps),
            ],
            "notes": [],
            "checks": {"snapshot_available": False},
        }
    checks["snapshot_available"] = True

    # (a) G2 rejected with 409 on a RUNNING run.
    if g2_status == 409:
        checks["g2_rejected_409"] = True
        if "RUNNING" in str(g2_detail).upper():
            checks["g2_on_running"] = True
        else:
            checks["g2_on_running"] = False
            inconc.append(
                "run was not RUNNING at G2 (409 detail=%r); cannot demonstrate "
                "RUNNING-state fencing (Slice-8 failure mode)" % g2_detail)
    else:
        checks["g2_rejected_409"] = False
        hard.append("G2 /start must be rejected 409; got status=%s "
                    "(detail=%r)" % (g2_status, g2_detail))

    # (b) zero writes to ownership/dispatch fields (post_rel -> post_g2).
    changed = [f for f in OWNERSHIP_FIELDS
               if _field(post_rel, f) != _field(post_g2, f)]
    checks["ownership_fields_unchanged"] = len(changed) == 0
    for f in changed:
        hard.append("unauthorised ownership/dispatch change after G2: %r "
                    "(pre=%r post=%r)"
                    % (f, _field(post_rel, f), _field(post_g2, f)))

    # (b1) status must never reset to READY (re-admission = unauthorised).
    s_rel = _field(post_rel, "status")
    s_g2 = _field(post_g2, "status")
    checks["status_not_reset_ready"] = not (s_g2 == "READY" and s_rel != "READY")
    if s_g2 == "READY" and s_rel != "READY":
        hard.append("status reset to READY after G2 (unauthorised re-admission; "
                    "pre=%r post=%r)" % (s_rel, s_g2))

    # (b2) owner_alive must not flip (ownership lost/changed).
    checks["owner_alive_stable"] = _field(post_rel, "owner_alive") == _field(post_g2, "owner_alive")
    if _field(post_rel, "owner_alive") != _field(post_g2, "owner_alive"):
        hard.append("owner_alive changed after G2 (ownership lost/changed; "
                    "pre=%r post=%r)" % (_field(post_rel, "owner_alive"),
                                         _field(post_g2, "owner_alive")))

    # (c) G1 legitimate progress (informational: record, not a failure).
    progress_changed = [f for f in PROGRESS_FIELDS
                        if _field(post_rel, f) != _field(post_g2, f)]
    checks["g1_progress_fields_changed"] = progress_changed

    # (d) original generation sole owner: attempt == 1.
    attempt_rel = _field(post_rel, "attempt")
    checks["original_attempt_single"] = attempt_rel == 1
    if attempt_rel != 1:
        hard.append("attempt != 1 (%r) — run was not admitted by a single "
                    "original generation" % attempt_rel)

    # (e) runtime_id unchanged across the in-process reload (pre -> post_rel).
    rid_pre = _field(pre, "runtime_id")
    rid_rel = _field(post_rel, "runtime_id")
    checks["runtime_id_stable_across_reload"] = (rid_pre == rid_rel
                                                 and rid_rel is not None)
    if rid_pre != rid_rel:
        hard.append("runtime_id changed across in-process reload "
                    "(pre=%r post_rel=%r)" % (rid_pre, rid_rel))

    # (f) G1 outcome (correction C, v3).
    # A confirmed COMPLETED is the ONLY outcome that counts as legitimate
    # continuation for a PASS.  A run still RUNNING at the observation deadline
    # is a timeout (INCONCLUSIVE); a HALTED run is classified by reason:
    # LOST_OWNER => hard FAIL (stale-generation claim), own-execution =>
    # INCONCLUSIVE (no confirmed outcome; this test specifies no pre-authorised
    # termination).  An indeterminate outcome is INCONCLUSIVE.
    outcome_analysis = _analyze_g1_outcome(post_g2, outcome)
    checks["g1_outcome"] = outcome_analysis
    kind = outcome_analysis.get("kind")
    legitimate = kind == "completed"
    checks["g1_continued_legitimately"] = legitimate
    if kind == "completed":
        checks["g1_completed_via_g1"] = True
    if kind == "running_at_boundary":
        checks["g1_left_running_at_boundary"] = True
    if kind == "halted_own_execution":
        checks["g1_controlled_termination"] = True
        notes.append("G1 terminated by its OWN execution (controlled "
                     "termination, not G2): halt_reason=%r" %
                     outcome_analysis.get("halt_reason"))
    if kind == "halted_lost_owner":
        hard.append("G1 halted with LOST_OWNER reason=%r \u2014 stale-generation "
                    "claim, infeasible in an in-process reload"
                    % outcome_analysis.get("halt_reason"))
    elif not legitimate:
        inconc.append("G1 outcome not confirmed as COMPLETED (kind=%r)" % kind)

    # (B) reload verification (correction B, v3).  A verified in-process reload
    # with matching source identity is a REQUIRED part of the acceptance.  If it
    # is not verified, the acceptance is a definitive no-go (hard FAIL), not
    # merely undemonstrable: we cannot confirm a genuine reload of the runner.
    reload_ok, reload_detail = _reload_verdict(reload)
    checks["reload_verified"] = reload_ok
    if not reload_ok:
        hard.append("in-process reload not verified (correction B): "
                    + reload_detail)

    # Final classification.
    if hard:
        result = "FAIL"
    elif inconc:
        result = "INCONCLUSIVE"
    else:
        result = "PASS"

    return {
        "result": result,
        "hard_failures": hard,
        "inconclusive": inconc,
        "notes": notes,
        "checks": checks,
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def main():
    t0 = time.time()
    if not OWUI_DEPLOY_TOKEN:
        print("ERROR: OWUI_DEPLOY_TOKEN is not set.", file=sys.stderr)
        return 1
    if not OWUI_TEST_A_API_KEY:
        print("ERROR: OWUI_TEST_A_API_KEY is not set.", file=sys.stderr)
        return 1
    if not os.path.isfile(SRC):
        print("ERROR: runner source not found at %s" % SRC, file=sys.stderr)
        return 1
    ev = {
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "base_url": OWUI_BASE_URL,
        "model_id": OWUI_TEST_A_MODEL_ID,
        "cartridges": N_CARTRIDGES,
        "field_partition": {
            "progress": list(PROGRESS_FIELDS),
            "ownership": list(OWNERSHIP_FIELDS),
        },
        "timestamps": {},
    }

    # Read the local source ONCE (source-identity reference).
    with open(SRC, encoding="utf-8") as f:
        source_text = f.read()
    ev["source"] = {
        "path": SRC,
        "length": len(source_text),
        "sha256_raw": _sha256_text(source_text),
        "sha256_norm": _sha256_text(source_text.replace("\r\n", "\n")),
    }

    # --- G1: create + start (original generation) ---------------------------
    st, body = create_probe_run()
    run_id = (body or {}).get("run_id")
    ev["g1_create"] = st
    ev["run_id"] = run_id
    if not run_id or st >= 400:
        ev["g1_create_error"] = "no run_id or create failed (status=%s)" % st
        ev["result"] = "FAIL"
        ev["reason"] = "G1 create failed: %s" % st
        _dump(ev)
        print("FAIL: G1 create failed (status=%s)" % st)
        return 1

    ev["timestamps"]["g1_start"] = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                 time.gmtime())
    st, body = req("/api/magazine/runs/" + run_id + "/start", method="POST")
    ev["g1_start"] = st
    # Let G1 make real progress before the snapshot.
    time.sleep(3)
    ev["timestamps"]["pre_snapshot"] = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # --- pre-reload snapshot (record original generation's runtime_id, attempt)
    _, pre = req_json("/api/magazine/runs/" + run_id)
    ev["pre"] = pre

    # --- in-process reload + verification (B) ---------------------------------
    ev["timestamps"]["reload_start"] = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    ev["reload"] = deploy_reload(source_text)
    time.sleep(2)

    # --- post-reload snapshot ---------------------------------------------
    _, post_rel = req_json("/api/magazine/runs/" + run_id)
    ev["post_rel"] = post_rel
    ev["timestamps"]["post_rel_snapshot"] = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # --- G2 (post-reload generation) ------------------------------------------
    ev["timestamps"]["g2"] = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                            time.gmtime())
    st, body = req("/api/magazine/runs/" + run_id + "/start", method="POST")
    ev["g2"] = {"status": st, "detail": _json_detail(body)}

    # --- immediate post-G2 snapshot (the "no writes" boundary) ---------------
    _, post_g2 = req_json("/api/magazine/runs/" + run_id)
    ev["post_g2"] = post_g2
    ev["timestamps"]["post_g2_snapshot"] = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # --- observe G1 through its outcome (C) -----------------------------------
    outcome = observe_g1(run_id, post_g2)
    ev["g1_outcome"] = outcome
    ev["timestamps"]["observed_until"] = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # --- classify (pure) ------------------------------------------------------
    verdict = evaluate(ev)
    ev["verdict"] = verdict
    ev["elapsed_seconds"] = round(time.time() - t0, 1)

    _dump(ev)

    result = verdict["result"]
    print("=" * 60)
    print("FM-1 RUNNING-state fencing (corrected v3) -> %s" % result)
    print("=" * 60)
    if verdict["hard_failures"]:
        print("HARD FAILURES:")
        for r in verdict["hard_failures"]:
            print("  * %s" % r)
    if verdict["inconclusive"]:
        print("INCONCLUSIVE:")
        for r in verdict["inconclusive"]:
            print("  * %s" % r)
    if verdict["notes"]:
        print("NOTES:")
        for r in verdict["notes"]:
            print("  - %s" % r)
    if not verdict["hard_failures"] and not verdict["inconclusive"]:
        print("All fencing invariants held; reload verified; "
              "G1 continued legitimately.")
    for k, v in sorted(verdict["checks"].items()):
        if k in ("g1_progress_fields_changed", "g1_outcome"):
            continue
        print("  %s = %s" % (k, v))
    print("  g1_progress_fields_changed = %r" % verdict["checks"].get("g1_progress_fields_changed"))
    g1o = verdict["checks"].get("g1_outcome")
    if isinstance(g1o, dict):
        print("  g1_outcome.kind = %s (final_status=%s halt_reason=%r)"
              % (g1o.get("kind"), g1o.get("status"), g1o.get("halt_reason")))
    else:
        print("  g1_outcome = (unavailable: snapshot fail-closed)")
    return 0 if result == "PASS" else 1


def _dump(ev):
    with open(OUT, "w") as f:
        json.dump(ev, f, indent=2, default=str)
    print("wrote %s" % OUT, file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
