# FM-1 RUNNING-state Acceptance — Release-Evidence Closure Report

**Milestone:** FM-1 — RUNNING-state fencing / acceptance (Open WebUI runner, `magazine` function).
**Status:** **CLOSED — PASS**.
**Date:** 2026-10-10.
**Scope:** Release-evidence closure for the FM-1 RUNNING-state acceptance milestone:
(1) baseline verification, (2) regression tests protecting the corrected acceptance script
against the five identified defects, (3) reconciliation of live evidence from the two
successful FM-1 runs, (4) source-identity resolution, (5) local validation, and (6) this
closure classification.

No live FM-1 execution was performed for this closure work. All evidence is drawn from the
already-captured live run artifacts and from fully-offline, mocked test execution.

---

## 1. Executive Summary

The FM-1 RUNNING-state acceptance milestone is **PASS**. A genuine live run of the
**corrected** acceptance script
(`fm1_running_state_acceptance_v3_real.py`, run_id `d540d9a8cfdd`, started
`2026-10-09T19:01:20Z`, elapsed 26.3 s) returned `verdict.result == "PASS"` with
`hard_failures == []` and `inconclusive == []`. Every one of the five identified defects
in the original `v3.py` acceptance script is (a) fixed in the corrected script, (b) protected
by a newly-authored offline regression-test suite (`fm1_running_state_acceptance_v3_real_test.py`,
11 tests, **11/11 pass**), and (c) corroborated by the live JSON. The existing FM-1 suite
(22 tests, **22/22 pass**) was confirmed free of regression. Broader validation passed:
Magazine 560/560, runner 45/45, embedded-core parity 28/28 byte-identical.

The one non-trivial evidentiary finding — the **source-identity resolution** — is fully
closed in §5: the live-run source (`cead0088…`, LF-only) and the current working tree
(`f8929a4e…`, CRLF) contain **byte-identical text content**; the SHA-256 difference is
entirely attributable to 1,390 `\r` line-ending bytes and carries no content or behavioural
change.

---

## 2. Live Run Provenance

The live run that produced the accepted verdict:

| Field | Value |
|---|---|
| `run_id` | `d540d9a8cfdd` |
| `started_at` | `2026-10-09T19:01:20Z` |
| `elapsed_seconds` | 26.3 |
| `base_url` | `http://host.docker.internal:3000` |
| `model_id` | `Bonsai 2 27B` |
| acceptance script | `fm1_running_state_acceptance_v3_real.py` |
| source path (as recorded) | `/workspace/evidence/../magazine-webui-runner-01/magazine_ef.py` |
| source SHA-256 (raw & normalized) | `cead0088094f5480e6c83ee1df1d75734c44e2c9a61cb6ba9fae1294cd26ffbd` |
| source length (chars) | 148,693 |

**Run timeline (corrections made en route):**

1. `fm1_v3_real_run_stdout.log` (17:51, Oct 9): earlier attempt — "wrote …json" then
   `FAIL: G1 create failed (status=500)` (server-side bug at the time).
2. ~18:03:47Z (Slice 8B.4): FAIL — G1 create 500. Deployed sha256 was the stale
   `3ff5400a…` while local was `cead0088…`.
3. **18:55:12Z (Slice 8B.5): PASS**, `run_id=216529b318f3`, elapsed 27.7 s.
4. **19:01:20Z: repeat PASS**, `run_id=d540d9a8cfdd`, elapsed 26.3 s.
   **This second run's JSON overwrote the first run's** — the JSON on disk reflects run
   `d540d9a8cfdd` only. (See §7 data-integrity caveat.)

Both successful runs used the corrected script; the live JSON is a valid PASS from the
corrected script.

---

## 3. Evidence Artifacts and SHA-256

| Artifact (path) | SHA-256 | Size |
|---|---|---|
| `fm1_running_state_acceptance_v3.json` — live verdict JSON (run `d540d9a8cfdd`) | `07e87d3e937308e5d16305a9ab9a9072c74e1212fac3a06b9735dee619ac8fc2` | 311,803 B |
| `fm1_v3_real_run_stdout.log` — earlier failed attempt log | `b194476789040d9373a022d428135392284301b6b87d176151881019d500f500` | 99 B |
| `fm1_running_state_acceptance_v3_real.py` — corrected acceptance script | `f1aa492b8c58e12c6a1b6924aaa0d15ec6536c3ed3415b74ed06368c7a64e2a0` | 35,554 B |
| `fm1_running_state_acceptance_v3.py` — original (uncorrected) script | `637c26010d01213ab069da2fe00013e31abff2ad802f0262672070fa93393f0e` | 34,978 B |
| `fm1_running_state_acceptance_v3_real_test.py` — new regression-test suite | `ec4e80a64f7df0f49f67c0b4280430772468348a17a02abfa3540b7eafd5945c` | 17,680 B |
| `_local_src_backup.txt` — live-run source capture (LF-only) | `cead0088094f5480e6c83ee1df1d75734c44e2c9a61cb6ba9fae1294cd26ffbd` | 148,715 B |
| `magazine-webui-runner-01/magazine_ef.py` — current working tree | `f8929a4eb715f8b9a43b29c8053ff3280e75ae3048d8bf9837e2dcaa116c1a26` | 150,105 B |
| `f663e0a` committed `magazine_ef.py` | `89c96e39b5e13d9a816f1a396756fc973f6a64748a39f01b603741fa16d08e7f` | 150,218 B |

`f663e0a` is the last commit in the runner repo ("Fix embedded-core loader cache identity;
correct FM-1 test auth", Oct 8 22:02:42 UTC). The live-run source `cead0088…` is
**not a committed git blob** — it was an uncommitted working-tree state (see §5).

---

## 4. Live Verdict (from `fm1_running_state_acceptance_v3.json`)

```
verdict.result             = PASS
verdict.hard_failures      = []
verdict.inconclusive       = []
```

Every check `true`:

| Check | Value |
|---|---|
| g1_completed_via_g1 | true |
| g1_continued_legitimately | true |
| g1_progress_fields_changed | [] (no spurious changes) |
| g2_on_running | true |
| g2_rejected_409 | true |
| original_attempt_single | true |
| owner_alive_stable | true |
| ownership_fields_unchanged | true |
| reload_verified | true |
| runtime_id_stable_across_reload | true |
| snapshot_available | true |
| status_not_reset_ready | true |

**G1 outcome:** `final_status=COMPLETED`, `halt_reason=null`, `observed_seconds=15.0`,
11-entry history progressing through **string-typed** cartridge IDs
`'3' → '4' → '5' → '6' → '7' → COMPLETED` (revisions 7→9→11→13→15→16),
owner_id `05e9c8a7-15bf-4cea-8ed3-3674f43969fd` stable across all snapshots.

**HTTP sequence:** `g1_create=200`, `g1_start=202`, `g2={status:409,
detail:"cannot start a RUNNING run"}` (correct fencing of the second start while G1 is
RUNNING).

**Reload verification:** `op_statuses={delete:200, create:200, toggle:200}`,
`pre_record`/`post_record` both `id=magazine`, `is_active=true`, `updated_at` changed
(`1791572115 → 1791572484`), `source_identity=exact`,
`source_sha256_raw = source_sha256_norm = post_content_sha256_raw =
cead0088…` (identical to §3 live source), `marker=true`, `marker_status=200`,
`error=null`.

**Field partition:** progress = `[status, current_cartridge, done, todo, revision]`;
ownership = `[attempt, runtime_id, owner_alive, halted, halt_reason, model_map,
tool_ids, owner_id]`.

---

## 5. Source-Identity Resolution (key finding)

The three candidate sources of the embedded `magazine` core differ only by line endings:

| Source | SHA-256 (file) | Bytes | CRLF | LF | chars |
|---|---|---|---|---|---|
| Live-run source (`_local_src_backup.txt`) | `cead0088…` | 148,715 | 0 | 1,396 | 148,693 |
| Current working tree (`magazine_ef.py`) | `f8929a4e…` | 150,105 | 1,390 | 6 | 150,083 |
| Committed `f663e0a` | `89c96e39…` | 150,218 | — | — | — |

**Verification (normalized line endings → `CRLF` and lone `CR` → `LF`):**

```
normalized byte-identical : True
normalized sha-256 (WT)   : cead0088094f5480e6c83ee1df1d75734c44e2c9a61cb6ba9fae1294cd26ffbd
normalized sha-256 (src)  : cead0088094f5480e6c83ee1df1d75734c44e2c9a61cb6ba9fae1294cd26ffbd
line count                : 1,396 (identical)
```

**Resolution:**

- The live-run source `cead0088…` is an **uncommitted working-tree state**, not a git
  blob (no commit blob in `f663e0a`, `d21e9aad`, `75ecd6b1`, etc. matches it).
- The current working tree (`f8929a4e…`) and the live-run source (`cead0088…`) contain
  **byte-identical text content**; they differ **only** by line endings.
- The byte delta is exactly the line-ending delta:
  150,105 − 148,715 = **1,390 bytes** = the 1,390 `\r` characters of the CRLF lines
  (1,390 CRLF + 6 LF in the WT vs. 1,396 LF in the evidence source).
- After line-ending normalization, both hash to the identical `cead0088…`, which equals
  the `source.sha256_raw` / `source.sha256_norm` / `reload.post_content_sha256_raw`
  recorded in the live JSON.

**Conclusion:** there is **no content or behavioural difference** between the source the
live run executed and the current working tree. The SHA-256 discrepancy is purely a
line-ending conversion (LF→CRLF), not a code change. The "19-line wording cleanup"
was therefore already present in the live-run source; it is not a post-run modification.

---

## 6. The Five Defects and Regression Coverage

`fm1_running_state_acceptance_v3.py` (original, `637c2601…`) contained the following
five defects; each is fixed in `fm1_running_state_acceptance_v3_real.py`
(`f1aa492b…`) and protected by the new suite:

| # | Defect (original `v3.py`) | Fix in `_real.py` | Regression test(s) | Result |
|---|---|---|---|---|
| **A** | `schema_version: 1` (int) in probe payload; `cartridges[].id` ints | `schema_version: "1.0"` (str); `str(cid)` IDs; canonical loader enforces strings | `TestFM1SchemaPayload` (2 tests) + `TestFM1CanonicalSchema` (3 tests, via `schema.py` `load_magazine_json`/`validate_*`) | pass |
| **B** | No `FUNCTION_ID`; reload used stale `DELETE`/`TOGGLE` routes | `FUNCTION_ID="magazine"`; id-based routes `/api/v1/functions/id/magazine/{delete, toggle}` | `TestFM1FunctionIdentity` (2 tests) | pass |
| **C** | G1/G2 `/start` called with no `method` (default **GET**) | `method="POST"` on both starts | asserted in `TestFM1CorrectedMainPath` (both `/start` calls logged as POST; no GET) | pass |
| **D** | `get_function_meta` accepted any 2xx (incl. SPA HTML) as metadata | requires JSON dict with `id == "magazine"`; HTML/200 fallback → `None` | `TestFM1MetadataValidation` (3 tests: valid record accepted, HTML-200 rejected, wrong-id rejected) | pass |
| **E** | Full main path could not be verified against the corrected script | corrected end-to-end `main()` flow | `TestFM1CorrectedMainPath` (1 test): genuine `PASS` verdict through corrected `main()` + all of A/B/C/D asserted on the same run | pass |

Design note: the new 11 tests target the corrected script's **unique** behaviour
(paid correctness, route/method correctness, metadata validation, full corrected main
path). They **do not duplicate** the existing 22 `evaluate()`-logic unit tests
(`fm1_running_state_acceptance_v3_test.py`), whose logic is byte-identical across
both scripts and is already covered.

Both suites import the corrected script (`_real.py`) via `importlib` and patch the
transport (`fm1.httpx.request`), `time.sleep`, and the source/output paths — fully
offline, no live Open WebUI contact.

---

## 7. Test and Validation Results

| Suite | Command | Result |
|---|---|---|
| New FM-1 regression (corrected `_real.py`) | `python -m unittest fm1_running_state_acceptance_v3_real_test -v` | **11 / 11 pass** |
| Existing FM-1 (original `v3.py`) | `python -m unittest fm1_running_state_acceptance_v3_test -v` | **22 / 22 pass** |
| Magazine core | `python -m pytest tests/` (in `/workspace/Magazine`) | **560 / 560 pass** |
| Runner | `python -m pytest test_magazine_execution.py test_magazine_recover.py test_magazine_runner.py` (in runner repo) | **45 / 45 pass** |
| Embedded-core parity | `python evidence/parity_check.py` | **28 / 28 byte-identical** (mismatch=0 missing=0 extra=0) |

Environment: `/workspace/.venvs/magazine-runner`, Python 3.11.2, httpx 0.28.1.
(The trailing "FAIL" text observed when running the FM-1 mock suites is **expected** output
from the intentional FAIL-scenario tests; the pytest/unittest summary is OK.)

---

## 8. Data-Integrity Caveat (overwrite)

The JSON on disk (`fm1_running_state_acceptance_v3.json`, `run_id=d540d9a8cfdd`) is
from the **second** (repeat) successful run at 19:01:20Z. The first successful run
(`run_id=216529b318f3`, 18:55:12Z, elapsed 27.7 s) wrote to the same path and was
**overwritten** by the repeat run; its JSON is not preserved. The closure relies on the
second run's JSON, which is itself a valid, independently generated PASS of the corrected
script. Both runs shared the same corrected script and the same source identity
(`cead0088…`).

---

## 9. Repository / Git State at Closure

**`/workspace/Magazine`** — branch `experiment/one-shot-magazine`, HEAD `381b361`:
- Uncommitted (Codex / standalone-tool reference cleanup, 5 files, 17 ins / 17 del):
  `README.md`, `magazine/runtime_dispatch.py`, `magazine/webui_dispatch.py`,
  `tests/test_run_magazine_open_webui.py`, `tests/test_webui_dispatch.py`.

**`/workspace/magazine-webui-runner-01`** — branch `master`, HEAD `f663e0a`:
- Uncommitted (standalone-tool reference cleanup, 2 files, 19 ins / 19 del):
  `magazine_ef.py` (tool ids `fresh_worker`→`host_files`,
  `development_toolkit`→`host_terminal`, `edit_office_files`→`host_browser`),
  `test_magazine_runner.py`.

No push to remote and no history rewrite are part of this closure; the local commits
(Codex cleanup; standalone-tool cleanup; new regression tests + this report) are kept
in **separate** commits per the release plan.

---

## 10. Classification and Conclusion

**Classification: PASS — milestone closed.**

The FM-1 RUNNING-state acceptance milestone is satisfied by the conjunction of:

1. A genuine live PASS of the corrected acceptance script
   (`run_id=d540d9a8cfdd`, `2026-10-09T19:01:20Z`), `hard_failures=[]`, `inconclusive=[]`,
   all 12 checks true, G2 correctly fenced with 409 while G1 ran to `COMPLETED`.
2. Five identified defects in the original acceptance script — schema payload (A),
   function-identity routes (B), HTTP method (C), metadata validation (D), and corrected
   end-to-end path (E) — each fixed in the corrected script and protected by a dedicated
   offline regression suite (**11/11 pass**), with no regression in the existing suite
   (**22/22 pass**).
3. Source-identity resolution: the live-run source and the current working tree are
   **textually byte-identical** (differ only by 1,390 `\r` line-ending bytes; normalized
   SHA-256 = `cead0088…`, matching the live JSON).
4. Broader local validation: Magazine 560/560, runner 45/45, embedded-core 28/28
   byte-identical.

**Residual (documented, non-blocking):** the first successful run's JSON was overwritten
by the repeat run (§8). This does not affect the closure, as the preserved JSON is
itself a valid, independently generated PASS from the corrected script and the same source.

**Recommended next actions (per release plan, out of scope for this closure):**
(a) commit the three local change sets in separate commits; (b) no remote push.
