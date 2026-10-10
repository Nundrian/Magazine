# FM-1 RUNNING-state Acceptance — Evidence

Preservation copy of the FM-1 RUNNING-state acceptance release evidence, placed in the
Magazine repository for versioned retention.

**Closure status: PASS** — see `fm1_closure_report.md`.

## Contents

| File | SHA-256 (see `SHA256SUMS`) | What it is |
|---|---|---|
| `fm1_closure_report.md` | — | This milestone's release-evidence closure report (classification, provenance, defect-coverage matrix, validation). |
| `fm1_running_state_acceptance_v3.py` | — | Original (uncorrected) FM-1 acceptance script. |
| `fm1_running_state_acceptance_v3_real.py` | — | Corrected FM-1 acceptance script actually executed by the live run. |
| `fm1_running_state_acceptance_v3_test.py` | — | Existing FM-1 test suite (22 tests). |
| `fm1_running_state_acceptance_v3_real_test.py` | — | New offline regression suite protecting the corrected script (11 tests). |
| `fm1_running_state_acceptance_v3.json` | — | Live run verdict JSON (`run_id=d540d9a8cfdd`, `2026-10-09T19:01:20Z`). |
| `_local_src_backup.txt` | — | The live-run source capture (normalized SHA-256 = `cead0088…`). |
| `fm1_v3_real_run_stdout.log` | — | 99-byte stdout of the earlier failed attempt (G1 create 500). |

## Provenance

- **Original location:** `/workspace/evidence/` (kept intact and unmodified by this commit).
- This directory is a **byte-for-byte copy** of the original files. Verify with:
  `cd fm1-running-state-acceptance-evidence && sha256sum -c SHA256SUMS`
- **Live run:** corrected script `fm1_running_state_acceptance_v3_real.py`,
  `run_id=d540d9a8cfdd`, started `2026-10-09T19:01:20Z`, elapsed 26.3 s,
  `base_url=http://host.docker.internal:3000`, `model_id=Bonsai 2 27B`.
  `verdict.result = PASS`, `hard_failures = []`, `inconclusive = []`.
- **Source identity:** `_local_src_backup.txt` (LF) and the current working tree
  (`f8929a4e…`, CRLF) are textually byte-identical; the SHA-256 difference is exactly
  1,390 `\r` line-ending bytes. Normalized SHA-256 = `cead0088…`, matching
  `source.sha256_raw` in the live JSON.
- **Regression results at closure:** new suite 11/11, existing suite 22/22,
  Magazine core 560/560, runner 45/45, embedded-core parity 28/28 byte-identical.

## Verification commands (offline)

```sh
# Checksums
cd fm1-running-state-acceptance-evidence && sha256sum -c SHA256SUMS

# Regression + existing suites (from /workspace/evidence)
PYTHONPATH=/workspace/.venvs/magazine-runner . /workspace/.venvs/magazine-runner/bin/python \
  -m unittest fm1_running_state_acceptance_v3_real_test -v
PYTHONPATH=/workspace/.venvs/magazine-runner . /workspace/.venvs/magazine-runner/bin/python \
  -m unittest fm1_running_state_acceptance_v3_test -v

# Embedded-core parity
python /workspace/evidence/parity_check.py
```

## Notes

- This directory is **not** the repository's ignored `evidence/` directory (which is
  reserved for chat/user/model content per `.gitignore`). It is a clearly-identified
  milestone-evidence directory.
- No credentials, secrets, API keys, or tokens are present. The only credential-related
  values are mock placeholders injected by the test suites.
- The preserved live JSON is from the **repeat** successful run (`d540d9a8cfdd`); the
  first successful run's JSON (`216529b318f3`) was overwritten and is not preserved
  (documented in `fm1_closure_report.md` §8).
