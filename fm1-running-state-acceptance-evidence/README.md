# FM-1 RUNNING-state Acceptance — Evidence

Preservation copy of the FM-1 RUNNING-state acceptance release evidence, placed in
the Magazine repository for versioned retention.

**Closure status: PASS** — see `fm1_closure_report.md`.

## Contents

| File | SHA-256 (see `SHA256SUMS`) | What it is |
|---|---|---|
| `fm1_closure_report.md` | — | Release-evidence closure report (classification, provenance, defect-coverage matrix, validation). |
| `fm1_running_state_acceptance_v3.py` | — | Original (uncorrected) FM-1 acceptance script. |
| `fm1_running_state_acceptance_v3_real.py` | — | Corrected FM-1 acceptance script actually executed by the live run. |
| `fm1_running_state_acceptance_v3_test.py` | — | Existing FM-1 regression suite (22 tests, offline). |
| `fm1_running_state_acceptance_v3_real_test.py` | — | Corrected-script regression suite (11 tests, offline). |
| `fm1_running_state_acceptance_v3.json` | — | Live-run verdict JSON (`run_id=d540d9a8cfdd`, `2026-10-09T19:01:20Z`). |
| `_local_src_backup.txt` | — | Live-run source capture (normalized SHA-256 = `cead0088…`). |
| `fm1_v3_real_run_stdout.log` | — | 99-byte stdout of the earlier failed attempt (G1 create 500). |

## What the archived tests verify

- **`fm1_running_state_acceptance_v3_test.py` — existing FM-1 suite (22 tests).**
  Offline `evaluate()` unit tests plus mock-HTTP full-path tests covering the
  RUNNING-state fencing classification (legitimate progress, G2 rejection,
  unauthorised writes, reload verification, premature completion, G1 outcomes).
- **`fm1_running_state_acceptance_v3_real_test.py` — corrected-script suite (11 tests).**
  Protects the five defects the corrected `fm1_running_state_acceptance_v3_real.py`
  fixed: canonical schema payload, id-based function-identity routes, `POST` HTTP
  method for `/start`, metadata validation, and an end-to-end genuine PASS.

Both suites are **offline reproductions**: the only transport (`fm1.httpx.request`)
and the clock are mocked; no live Open WebUI is contacted and no Function is
created, deployed, toggled, or run.

## Running from a fresh checkout

The suites are self-contained: every path is resolved relative to `__file__`, so they
work from any fresh checkout, any current working directory, and with no dependency
on the original `/workspace/evidence/` layout or a specially-named parent directory.

```sh
# one-time on a fresh checkout: install the package (pulls httpx + pyyaml)
pip install -e .

# then, from the repository root
cd Magazine/fm1-running-state-acceptance-evidence
python -m unittest fm1_running_state_acceptance_v3_real_test -v   # corrected (11)
python -m unittest fm1_running_state_acceptance_v3_test -v        # existing  (22)
```

Run from any other working directory by putting the suite directory on `PYTHONPATH`:

```sh
PYTHONPATH=/path/to/Magazine/fm1-running-state-acceptance-evidence \
  python -m unittest fm1_running_state_acceptance_v3_real_test
```

## Required local Python dependencies

- **Python >= 3.10**
- **`httpx`** (>= 0.27) — imported by the scripts; patched in the tests, but must be
  importable.
- **`pyyaml`** (>= 6) — declared package dependency of Magazine (not exercised by
  these tests).

`pip install -e .` (shown above) satisfies all of these.

## Core tests vs archived acceptance tests

`pyproject.toml` sets `testpaths = ["tests"]`, so an ordinary `python -m pytest` at
the repository root discovers **only** the 560 core tests in `tests/` and never
auto-collects the evidence directory's scripts. The archived acceptance scripts are
release evidence (regression suites + scripts + live JSON + source) and are run
**explicitly** via `python -m unittest` from this directory — not as part of the core
suite. The two are deliberately kept separate.

## Historical live evidence vs offline reproductions

- **Live evidence (do not modify):** `fm1_running_state_acceptance_v3.json`
  (machine-generated verdict from the repeat run `run_id=d540d9a8cfdd`),
  `fm1_v3_real_run_stdout.log`, `_local_src_backup.txt`, and the two scripts
  (`..._v3.py` original / `..._v3_real.py` corrected, which the live run executed).
- **Offline reproductions:** the two `*_test.py` unittest suites above.

### Known limitation

The **first** successful run's verdict JSON (`run_id=216529b318f3`) was overwritten
by the repeat run and is not preserved; only the repeat run's JSON is available.
It is not reconstructed. See `fm1_closure_report.md` §8.

## Verification commands (offline)

```sh
# Checksums — verify all preserved evidence
cd Magazine/fm1-running-state-acceptance-evidence && sha256sum -c SHA256SUMS

# Regression + existing suites (fresh checkout, see "Running from a fresh checkout")
python -m unittest fm1_running_state_acceptance_v3_real_test -v
python -m unittest fm1_running_state_acceptance_v3_test -v

# Core suite (unaffected, from the repository root)
cd /path/to/Magazine && python -m pytest
```

## Provenance

- **Original location:** `/workspace/evidence/` (the files were copied here intact).
- This directory is a byte-for-byte copy of the original files (checksums in
  `SHA256SUMS`), except for the two deliberately-maintained documents
  (`fm1_running_state_acceptance_v3_real_test.py` path repair and this `README.md`).
- **Live run:** corrected script `fm1_running_state_acceptance_v3_real.py`,
  `run_id=d540d9a8cfdd`, started `2026-10-09T19:01:20Z`, elapsed 26.3 s,
  `base_url=http://host.docker.internal:3000`, `model_id=Bonsai 2 27B`.
  `verdict.result = PASS`, `hard_failures = []`, `inconclusive = []`.
- **Source identity:** `_local_src_backup.txt` (LF) and the working tree
  (`f8929a4e…`, CRLF) are textually byte-identical; the SHA-256 difference is exactly
  1,390 `\r` line-ending bytes. Normalized SHA-256 = `cead0088…`, matching
  `source.sha256_raw` in the live JSON.
- **Regression results at closure:** new suite 11/11, existing suite 22/22,
  Magazine core 560/560, runner 45/45, embedded-core parity 28/28 byte-identical.

## Notes

- This directory is **not** the repository's ignored `evidence/` directory (which is
  reserved for chat/user/model content per `.gitignore`). It is a clearly-identified
  milestone-evidence directory.
- No credentials, secrets, API keys, or tokens are present. The only credential-related
  values are mock placeholders injected by the test suites.
