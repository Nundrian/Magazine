# Validation

Use Linux Python 3.11.9, `requirements-test.txt`, an editable Magazine install
and Node 22, then run `python scripts/verify_release.py`. It runs core,
Function, browser-logic and launcher suites, embedded identity and both
publication profiles without inference.

Generate and verify the compressed embedded archive and release ZIPs with that
same Linux Python 3.11.9 toolchain. Other zlib/Python builds can produce different
compressed bytes from identical sources. Per-file canonical hashes establish
source parity; repeat builds with the documented toolchain establish archive
reproducibility. Binary image reproducibility is not established because upstream
Pyodide preparation resolves additional packages from external registries.

Build archives with `python scripts/build_release_archive.py --write`.
For native installation add `--manifest release/function-only-manifest.json`.

Clean stock-image acceptance:

```text
python scripts/acceptance_open_webui.py --function-only --image ghcr.io/open-webui/open-webui:v0.11.3@sha256:41daa0cf2561a5d4c8d1ff31ee2a98d93ab4d3ac2605cac69366ff6a3374a933
```

For the optional local image omit `--function-only` and use
`--image magazine-open-webui:0.1.0-beta.5`. The script creates uniquely named,
labelled resources, imports through the API, checks authentication/admin
boundaries and restart persistence. `--keep` preserves diagnostics.
It never uses pre-existing deployments.

Actual native browser import, launcher navigation, inference/STOP and
maintenance require live checks. Parser tests run in patched upstream source
after npm ci, before bundling; they verify versions/provenance, XLSX/XLS/ODS
array parsing and ESM APIs. They are not exhaustive malicious-input fuzzing.

Beta.3 historically passed 944 regressions and live Bonsai/browser STOP and
maintenance. Those are not beta.4 reruns. Current `ACCEPTANCE.md` records fresh
tests and unverified areas. Tests alone do not clear image security or licensing.
