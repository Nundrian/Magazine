# Magazine beta.10 RC2: frozen local baseline and optimisation brief

> **Archived development snapshot — 2 November 2026.** References below to the "active" beta.10 RC2 installation and the repository's contents describe their status **at that date**, not the current release. The source-matched Magazine Simple **0.2.0-exec.5** implementation is now documented separately. For the current Open WebUI implementation, see [Magazine Simple exec.5](SIMPLE_0.2.0_EXEC5.md) and the [main README](../README.md).

Status: **planning / not yet implemented**. Recorded 2 November 2026.

## Release boundary

The maintainer's active local Open WebUI installation is **Magazine 0.1.0-beta.10-rc.2** on **Open WebUI 0.12.5**. The separate **Magazine Sidebar Launcher 0.1.0** is also installed. This is the functional baseline for the next optimisation pass.

**This repository's `main` branch still contains the older public 0.1.0-beta.5 source and installation instructions.** The RC2 source archives have been supplied and verified separately. Do not start the optimisation from the public beta.5 tree or describe RC2 as a published GitHub release until its entire source is imported, reviewed and checked.

Preserve the live installation, its Function and Valves, current runs, exports, database, tool permissions, native model settings, Open WebUI configuration and sidebar launcher. The baseline is **frozen for the purpose of the optimisation project**: changes are candidates only, not deployed fixes.

### Verified source package fingerprints

The two independently supplied ZIPs were checked against the handover's SHA256SUMS record:

| Package | SHA-256 |
| --- | --- |
| `Magazine-0.1.0-beta.10-rc.2-FunctionOnly.zip` (also supplied as `Magazine-Tool-Selection-Fix(1).zip`) | `beb4e47bd2c258504e9a5c7ebb1e8b0f0e68800bf44dc48a81580ce8fd70b32a` |
| `Magazine-Sidebar-Launcher-0.1.0.zip` (also supplied as `Magazine-Sidebar-Launcher-0.1.0(2).zip`) | `91399f3b7d85ea8f76aa27dfe64106d0fd526dcc0f0ce7cf26ecbcfdb23b9bbb` |

The recorded installed RC2 Function **Python source** SHA-256 is `48e024eb6a97deed24e95331b363ccc5d69cfd90d60f4138c50b250ca919b226`. It is different from the ZIP and Function JSON hashes because they cover different bytes. The installed sidebar Python source SHA-256 is `9d15bdd95c4b0849fe498e072e58835c88cf6703da9b7e339d5758d93de0d30d`.

Do not expose or commit private backup files, run journals, chat archives, credentials, model settings or user data. Preserve their original secure locations.

## Task for Codex

### 1. Establish an immutable, reproducible baseline

1. Obtain the two exact verified source ZIPs from the maintainer's handover. Check the above hashes and validate the ZIP members before extraction; reject unexpected symlinks, traversal and duplicate destination names.
2. Extract into a **separate development workspace**. Keep a byte-identical frozen copy, its hash manifest, version metadata and read-only test fixtures.
3. Compare ZIP contents against the handover documents and current public repository. Explicitly identify all RC2-only source, tests and schema changes; do not overwrite them with beta.5.
4. Once the complete RC2 tree is reconciled in version control, record a stable, protected baseline reference or tag. Perform all optimisation on a separate branch based on that exact commit.

### 2. Create a fully isolated test environment

**The maintainer is using the live Magazine installation. Local LLMs and the user's GPU inference backend are unavailable.**

Use a dedicated, disposable test environment: its own Open WebUI 0.12.5 image/container where needed, separate ports, volumes, database, test credentials, network and logs. Never bind-mount the live Open WebUI data paths, contact its localhost services, modify its Function records or rely on the active Ollama/llama-server.

Use deterministic simulated model providers for plain responses, JSON-field handover, native-style tool continuations, failures, delays, timeouts, stale authorisation, concurrent admission, native compaction events and quality-review responses. Simulate API semantics faithfully and report what *cannot* be established without real inference. Do not claim a stub test is a live LLM test.

### 3. Measure before editing

Build repeatable baseline benchmarks with captured environment, fixtures and per-scenario statistics. Include at least:

- CPU time, peak RSS, allocation/retention and import/startup time;
- Function/UI payload size, browser load, repeated polling overhead and API response latency;
- per-cartridge preparation/dispatch/handover overhead and persistent FIFO queue behaviour;
- filesystem I/O, archive/checksum/export work, journal writes and repeated reads;
- token/context overhead **Magazine itself introduces** (not native model computation);
- number/size of files, duplicated code or generated artifacts and dependency footprint.

Use fixed representative workloads (single cartridge, multi-cartridge, file handover and QC, HALTED/recovery, STOP, large evidence export, concurrent owners). Compare against the frozen build with the same fixtures, host and settings. Separate one-time costs from per-run costs.

### 4. Optimise conservatively

Investigate the largest **measured** sources of overhead first: duplicate scans and serialisation, redundant state/API reads, excessive polling or payloads, repeated file hashing, avoidable journal I/O, unnecessary context assembly, large embedded UI duplication and dead/unreachable code. Remove bloat only after proving it is unused and tests cover the removed path. Prefer simple, maintainable changes with narrow diffs and measured benefit. Preserve the public contract and stable on-disk formats; use explicit migrations only if unavoidable.

No hard-coded preferred model, no external paid service and no requirement for a working local LLM.

### 5. Behaviour that must remain unchanged

- READY is non-executing; explicit owner Start; exact selected model IDs and Tools, default **zero Tools** on each newly loaded workflow; preserve previously prepared selections.
- Owner authentication/scope; STOP semantics; HALTED state, recovery safety and uncertain Tool call non-replay.
- Persistent FIFO inference queue, native Open WebUI Tool dispatch and compaction handoff; no parallel inference bypass.
- File and JSON handover, declared path safety, checksums, content-addressed manifests, original verified chat archive and bounded exports.
- Review defaults Off; advisory quality checks, cached review results, no Tools for review, explicit pause/continue policy.
- Accurate event/JSONL history, run durability across restarts, standalone sidebar lifecycle and ordinary native Open WebUI chat.

Current recorded acceptance includes beta.10 RC1 **1,091 Python tests and 138 JavaScript tests**, plus RC2 **49 UI and 28 API/export/execution checks** and sidebar lifecycle checks. These are historical claims, not proof that the new isolated environment has passed. Recreate and rerun the relevant suites; report skipped/unavailable environment tests separately.

### Preserve both sidebar routes

The public repository already contains the **legacy patched-Docker-image sidebar** under `integrations/open_webui/frontend/`, including `Dockerfile`, `patches/0001-magazine-sidebar-launcher.patch`, Compose example and build notes. It is pinned to Open WebUI **0.11.3** and remains under its documented security/licensing **distribution HOLD**. **Retain this recipe, patch and tests as a historical/future optional integration, even if it is not used by the current installation.** Do not silently delete it as dead code during bloat removal or treat it as validated for 0.12.5.

Also preserve the **independent Magazine Sidebar Launcher 0.1.0 Function** from the second verified ZIP, validated against Open WebUI **0.12.5**. Keep its lifecycle, loader preservation, ownership and security tests separate from the historical Docker-image checks. Future Docker integration should be a separately version-pinned, tested candidate, not a change to the currently running container.

### 6. Acceptance and handoff

For every optimisation: record baseline and candidate numbers, repeat count, test conditions, absolute and percentage differences, failures, and a source-level explanation. Performance improvements must be practically significant and repeatable; do not trade correctness or recovery safety for speed. Report regressions and confidence limits even when a change is retained.

Deliver:

1. Frozen source manifest and proof of archive/installed-source correspondence where evidence allows.
2. Fully scripted disposable environment and deterministic model/tool stubs.
3. Profiling results and ranked bloat inventory with **keep / simplify / remove** decisions.
4. Small reviewed optimisation commits, complete regression results and before/after performance table.
5. Proposed upgrade, rollback and data-compatibility instructions.
6. A candidate release archive and checksums **without installing it**.

Stop after producing the tested candidate. Wait for the maintainer to finish using Magazine and explicitly authorise any live installation, inference tests or publication of a newer code release.
