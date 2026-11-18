# Magazine Simple 0.3.0-scheduler.rc1 — technical guide

**Release status:** locally installed and source-matched on Open WebUI 0.12.5, 18 November 2026. This is a release candidate. This document separates observed installation results from isolated automated tests and work that remains unverified.

[Plain-English README](../README.md) · [Using Magazine](USING_MAGAZINE.md) · [Function source](../integrations/open_webui/function/magazine_simple_scheduler_rc1.py)

## 1. Overview and compatibility

Magazine Simple is a self-contained Open WebUI Function. It exposes the same-origin page `/magazine-simple` and authenticated API routes under `/api/magazine-simple`. Open WebUI supplies chats, models, Terminal connections and selected tools; the Function supplies workflow import, execution scheduling, state, recovery, handover validation and exports. The separate Sidebar Launcher 0.1.0 opens the page; it does not run workflows.

The published Python source declares Open WebUI **0.12.5** and the runtime requirements `httpx==0.28.1` and `PyYAML==6.0.3`. The maintained installation kept the existing `magazine_simple` Function identity and settings. No other local version or clean public installation is certified by these records.

**Source identity:** SHA-256 of the supplied installed Function source (LF line endings): `acb55b3421bf5ea23510dd1b3f3372d1f19183a7bd6dec8da9940c89bea3ceb3`. The independently packaged [source-only ZIP](../downloads/Magazine-Simple-0.3.0-scheduler.rc1-source.zip) contains that exact source plus brief installation notes and a checksum file; its SHA-256 is `495bfd266dc07d1c6ecc89c9c4d9e36fcb242c78c2b01993e64d6294b9b4290e`. This ZIP is **not** the private qualified candidate ZIP.

## 2. Workflow format and execution

A workflow is a ZIP containing `magazine.yaml` (or `magazine.yml`), prompt files, and optionally declared artifact inputs/outputs and JSON handover selectors. Each cartridge names a model key; during setup that key or individual cartridge is mapped to an Open WebUI model ID. A newly prepared run makes a copy of the workflow definition so subsequent edits or deletion of the library entry do not rewrite the run.

Magazine runs a **fresh saved Open WebUI chat per attempt**, with the chosen model, Terminal and explicitly selected tools. It does not automatically plan jobs, substitute models or validate the meaning of generated code.

Minimal `magazine.yaml` example:

```yaml
schema_version: "1.0"
name: Example project
handover_mode: result_file_v1
result_profile: generic
recovery:
  max_attempts: 5
  attempt_seconds: 1800
  io_retries: 2
loop_police:
  enabled: true
  warn_repeats: 3
  stop_repeats: 5
  error_repeats: 3
  cycle_repeats: 3
  history_size: 64
cartridges:
  - id: c01
    model: worker
    prompt_file: cartridges/01.md
  - id: c02
    model: worker
    prompt_file: cartridges/02.md
```

No file handover is declared in this example; real workflows should declare output paths and selectors where later cartridges depend on them. The published parser also recognises legacy recovery aliases `max_retries` and `seconds`; it rejects conflicting values. **Five means five total attempts**, not five retries plus an initial attempt.

## 3. Scheduler: Sequential and Round Robin

The scheduler runs **one cartridge at a time** in the active server process. A project's running cartridge and its permitted recovery attempts remain together for a scheduler turn.

- **Sequential (default):** finish the current project before dispatching another waiting project.
- **Round Robin:** after a cartridge is successfully committed, a project with remaining work can yield its place so another queued project gets a turn. It is **not parallel execution**.
- Schedule changes are applied at a safe boundary after the active cartridge and recovery complete, not midway through a chat.
- The persistent scheduler journal records mode, queue order, project identities and checkpoints; it **does not persist credentials** or automatically dispatch work after service restart.
- After interruption, explicit recovery is required. The design preserves committed predecessors and attempts to avoid duplicated cartridges, while storage corruption is designed to fail closed.

**Limits:** this is per-process scheduling, not a distributed cross-worker lock. Do not run multiple independent server workers against the same scheduler storage expecting coordinated dispatch. The installation preflight checked a single server worker. Production multi-project inference and deliberate service-restart recovery have not yet been exercised on the installed system.

## 4. Result handover, retries and Antiloop

In `result_file_v1` mode, each attempt is given a scoped publisher helper. The model publishes its declared `/result` payload to an attempt-specific file and closes with a final `{"status":"DONE"}` line. The Function checks the ending status, publisher identity, declared outgoing values, expected files, SHA-256 hashes and read-back of the committed result before permitting a dependent step to begin. Earlier work, replies and evidence are preserved on eligible failure.

Default recovery is **five total saved attempts**, each with **1,800 seconds** for its own fresh chat; there are two I/O retries for transient result-publication operations. These are limits, not promises to use all attempts: explicit HALT, operator Stop, integrity errors, unconfirmed stops and certain repeated-no-progress conditions can terminate sooner. Continuing a run cannot bypass the attempt ceiling.

**Antiloop**, enabled by default, watches completed structured tool calls/results (not private reasoning). It warns after three repeated calls, stops after five identical results or three identical errors, and also looks for short repeating cycles. Productive file changes are treated differently from confirmed no-change repetitions. It can miss loops; it is not a correctness checker. The user-specific switch is saved per workflow and frozen when the run starts.

## 5. Data, permissions and API

The Function keeps user-scoped run data below Open WebUI's data directory and working folders below the configured Magazine workspace (commonly `/workspace/magazine-simple`). Saved runs contain cartridge statuses, attempt information, tokens, declared results and ledger exports. The browser API serves the user-facing dashboard and per-run detail; the user must authenticate through Open WebUI.

Selected routes in the supplied source:

| Path | Method | Purpose |
| --- | --- | --- |
| `/magazine-simple` | GET | Magazine page |
| `/api/magazine-simple/workflows` | GET | Workflow library |
| `/api/magazine-simple/workflows/import` | POST | Import workflow ZIP |
| `/api/magazine-simple/runs` | GET / POST | Saved runs / prepare a run |
| `/api/magazine-simple/scheduler` | GET / PUT | View / set scheduling mode |
| `/api/magazine-simple/queue` | GET | Scheduler queue |
| `/api/magazine-simple/runs/{run_id}/start` | POST | Start a prepared run |
| `/api/magazine-simple/runs/{run_id}/stop` | POST | Request a stop |
| `/api/magazine-simple/runs/{run_id}/recover` | POST | Explicit interrupted-run recovery |
| `/api/magazine-simple/runs/{run_id}/restart` | POST | Continue from an eligible cartridge |
| `/api/magazine-simple/runs/{run_id}/download` | GET | Export results ZIP |

There are also endpoints for run details, deletion, options and Antiloop settings. Use the source for the full route signatures and current authentication rules.

**Security boundaries:** imported ZIPs have path, size and member-count checks. Saved results have file-identity and hash checks; the scheduler journal excludes credentials. These are useful protections, **not** a general sandbox for untrusted prompts or powerful Terminal commands. Only run trusted workflows, select only necessary tools, back up local data, and do not run two independent writers on the same project workspace.

## 6. Browser and export behaviour

The current browser has a project dashboard, workflow import/selection, per-step or shared model assignment, Terminal/tool selection and Antiloop switch, explicit Prepare then Start, a run/queue view, previous-work access and export.

The ledger presents **six overview columns**: Cartridge, Status, Model, Attempts, Reported tokens and Duration. Expand a cartridge for per-attempt details. Token values come from actual reported Open WebUI usage; missing or partial data stay labelled **unavailable/incomplete**, and zero is distinct from missing.

The output ZIP retains the work and ledger as well as eligible run records. A completed step means its protocol and file checks passed; it does **not** certify the correctness of the resulting software.

## 7. Verification — what passed and what did not

The supplied `REGRESSION-REPORT.md` states that execution tests used disposable directories, mocked model/Open WebUI services and an integration server, **not** the installed production service.

| Test / observation | Recorded result |
| --- | --- |
| Existing GitHub core suite | 611 passed, 0 failed |
| Frozen Simple baseline | 249 passed, 2 skipped, 2 browser cases deselected |
| Final candidate suite | 280 passed, 0 failed, **98 skipped** |
| Separate real Firefox + Chromium page tests | 48 passed, 0 failed |
| Candidate API/engine plus two browsers (mocked auth/models) | 2 passed, 0 failed |
| Scheduler/recovery/API checks included in candidate | 32 checks |
| Installed app, read-only browser acceptance | Firefox 159 and Chromium 159; no observed browser errors |
| Real multi-project inference | **Not tested** |
| Deliberate installed-service restart recovery | **Not tested** |
| All legacy browser cases under a fully provisioned Linux driver | **Not requalified** |

The 98 skips are not passes: 96 historical browser checks were blocked by missing Linux `libatomic.so.1` browser-driver support, and two historical fixtures were absent. A separate Windows browser suite ran both browsers. The isolated candidate tests cover Round Robin with different project counts/lengths, waiting arrivals, STOP behaviour, file integrity, scheduler journal faults and process-loss detection. The installed browser acceptance was **read-only**; it did not dispatch model jobs.

The installation report dated 18 November records a preflight with an empty queue, no active Open WebUI tasks and one server worker, a matched source checksum, successful backups/inventory checks, and preservation of the existing failed 52/191-cartridge historical run. No restart or GitHub publication happened during that installation.

## 8. Updating and rollback

1. Wait for no running or queued Magazine work. Keep a backup of the Function record, data, workspace, workflow library and SQLite state.
2. Review the [versioned Function source](../integrations/open_webui/function/magazine_simple_scheduler_rc1.py), then update the **existing** Open WebUI Function in place rather than creating a second runner.
3. Preserve valves, activation status, Function ID, Sidebar Launcher and user data. Confirm that the dashboard, scheduler mode, historical run and ledger render correctly.
4. If rollback is required, stop new work and restore the matched original Function and data backups as a set. A source-only downgrade is **not** proven safe against newer journal state. Preserve any evidence created since the backup.

The private backup described in the installation report includes account data and **is not published** here. This public repository supplies the versioned source and documented verification boundaries, not a live recovery guarantee.

## 9. Historical compatibility and source

The earlier `0.2.0-reliability.3`, `reliability.2`, `handover.1` and `exec.5` sources remain available as historical versions. Older saved runs are not automatically converted or restarted. The original Magazine beta.5 integration is a **different** Function lineage and its old installation steps must not be used as instructions for Magazine Simple.

See [Changelog](../CHANGELOG.md), [older reliability.3 record](SIMPLE_0.2.0_RELIABILITY3.md) and [sidebar launcher notes](SIDEBAR_LAUNCHER.md).
