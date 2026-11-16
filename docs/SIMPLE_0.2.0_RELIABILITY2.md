# Magazine Simple 0.2.0-reliability.2

This build extends the verified 0.2.0-reliability.1 runner with Antiloop interface/configuration integration. It is installed in the existing Open WebUI Function; see the public installation and test notes at the end of this document. Existing workflows, cartridges, creation templates and application code are unchanged.

## Configuration

The existing `magazine.yaml` format accepts:

```yaml
recovery:
  attempt_seconds: 1800
  max_attempts: 5
  io_retries: 2
loop_police:
  enabled: true
  warn_repeats: 3
  stop_repeats: 5
  error_repeats: 3
  cycle_repeats: 3
  history_size: 64
```

Every new chat receives the whole attempt allowance. It covers saved-chat creation, completion startup, task polling and final save wait. File preparation and commit run outside it. HTTP requests retain their independent timeout. Recovery admission no longer tests a shared cartridge deadline. Timeout is `ATTEMPT_TIMEOUT`; confirmed repetition is `LOOP_DETECTED`. Both enter the existing recovery selector. A failed stop confirmation is a hard `STOP_UNCONFIRMED` failure, preventing another model attempt.

`seconds` remains an alias for `attempt_seconds`; `max_retries` remains an alias for initial attempt plus retries, capped at five total. Conflicting aliases fail validation. Previously accepted `max_retries: 5` is capped at five attempts, rather than six. Integer attempt allowances from 1 to 7,200 seconds support bounded live diagnostics. The default is 1,800. `max_attempts` bounds each automatic chain; the hard total ceiling is five saved attempts per cartridge, including manual Continue. Continue still works for old runs whose automatic retries were disabled, consuming the remaining hard ceiling. Old recorded shorter explicit allowances remain respected when resumed; this update never rewrites historical run files.

Antiloop can be disabled with `loop_police: {enabled: false}` in a disposable/new workflow's configuration. It requires no additional model/runtime dependency or Filter. Existing recorded runs without the field use the defaults when explicitly started. It never changes the production workflow files to apply a new configuration.

## Observation and safeguards

Magazine polls `/api/tasks/chat/{id}` and then reads the same original assistant message from `/api/v1/chats/{id}` while the task is active. It pairs structured `function_call` and completed/failed `function_call_output` items by call ID. Arguments are parsed and canonicalised; full result contents are hashed. IDs deduplicate successive saved-chat snapshots. A parallel batch contributes one representative event, avoiding five simultaneous identical calls being classified as five serial repetitions.

The watchdog records warnings at three identical call/result events and stops at five, or three identical errors. It detects periods of two through four events repeated three times. It stores a bounded 64-event signature history, 16 warnings and at most 4,096 seen call IDs per attempt. Reaching the identity bound disables further counting for that attempt and records the limitation, rather than recounting old events. The ordinary attempt timeout remains active.

Changing polling results and intervening observed progress break exact repetition. Silence, reasoning and token-generation speed never trigger it. Successful file-edit acknowledgements can conceal real changes; recognised edit/write/patch tools reset repetition history unless they explicitly report `changed: false`. That explicit no-op case can be counted. The runner cannot infer arbitrary project changes from opaque shell output. It does not scan arbitrary project roots or claim semantic detection of every ineffective edit/test. Volatile IDs/timestamps in a tool's result prevent an exact match; unknown or unfinished output shapes are ignored. These choices favour avoiding false positives.

Completed-only snapshots supply accounting, not live watchdog enforcement. Warnings and event signatures are saved to the attempt record during active execution. Detection records supporting call IDs/signatures, stops through the supported task API, waits for no task IDs, and passes concise evidence into the existing fresh-chat recovery. No steering messages are injected into the running model.

Stop/cancellation cleanup is bounded separately (10 seconds) and confirmed by task polling. Cleanup can therefore extend the elapsed wall time beyond the attempt's configured generation allowance. Cancellation records an interrupted attempt and incomplete usage. Stopping a chat cannot generally roll back a tool's already committed side effects or promise to terminate independent background shell processes; the disposable probe has a fixed safety cap and bounded work.

## Token accounting

Each attempt owns a replaceable snapshot keyed by its attempt and original assistant message identities. `input_tokens`, `output_tokens` and `total_tokens` take precedence over latest-round `prompt_tokens`/`completion_tokens`. Latest-round fallback is used only if cumulative input/output fields are absent and the saved output has no structured tool call indicating multiple rounds. Missing cumulative dimensions are never filled from the latest round. A total can be derived from known input plus output. Missing values remain null and render as `unavailable`. Errors, unfinished generation, reported incomplete usage and inconsistent arithmetic mark accounting incomplete.

Store saves recompute cartridge and run totals from distinct attempt snapshots, including failures and recoveries. Repeated fetches/saves do not increment totals. Partial totals show known reported consumption with an incomplete flag and unavailable-attempt count; they are not an estimate of unreported consumption. Old records without usage remain readable and unavailable. `run.json`, `LEDGER.md` and the existing download ZIP carry these values.

The deterministic c025 regression fixture uses the supplied values 205,867 input + 6,413 output = 212,280 total, with latest-round 29,827 / 457 deliberately different. The original c025 chat was not independently retrieved for that fixture. Live evidence separately compares actual saved Bonsai usage against the attempt snapshots, cartridge subtotal and run total.

## Preserved mechanisms and references

AST comparison in `preserved-mechanisms.json` verifies unchanged publisher source, envelope and outgoing-selector validation, durable commit, committed-result lookup, input preparation, sealing, candidate snapshots and recovery prompt builder. The publication-versus-repair selector and cross-attempt failure fingerprint remain in place. Regression fake clients now cache a generated saved reply, so observational GETs do not execute the scripted model again; stop requests also make their task list empty.

The loop detector is an independently written Python implementation using the installed Open WebUI saved-chat and task APIs. No external extension or chat tool is required. Test and installation evidence is summarised in the final section of this document; the original maintainer report and private backups are deliberately not distributed.

## Antiloop control and persistence

The Antiloop switch is in the existing Tools section and does not enter `tool_ids`. The existing Store's durable JSON writer saves a separate per-owner/per-workflow boolean override. That override wins over manifest `loop_police.enabled`; absent either, On is the default. The manifest's other watchdog thresholds are retained. No original workflow file is rewritten.

Preparing a run shows the effective choice. Start captures the latest persisted choice and freezes it for queued execution, recovery and Continue. Historical runs retain their recorded choice (or their copied manifest's setting when absent). Active/queued settings changes are rejected, and the switch is disabled for that work. Off passes no live observation callback to `run_chat`, while final/failure usage capture and all ordinary execution safeguards remain active.

Actual production On/Off, persistence, publication, recovery and accounting checks, including rejected diagnostic trials and verified rollback history, are summarised in the public installation and test notes below. The installed Function's source equals the delivered source.


---

# Verification and scope — 16 November 2026

The maintainer's production Open WebUI `magazine_simple` Function was updated from **0.2.0-handover.1** to **0.2.0-reliability.2**, preserving its Function ID and enabled state. Open WebUI remained 0.12.5. Source SHA-256: `2112da6c358ebfd2b153e26dea1583cbace98714ff11a21bea31af5b44814377`. The installed source matched the delivered source byte for byte.

**Automated tests:** 308 passed, 19 skipped, zero failed. Skips: 16 optional screenshot cases, 2 unavailable historical fixtures and 1 Chromium-only case omitted in Firefox. Browser checks ran in Chromium and Firefox. The original first full attempt exposed a browser fixture race; after correcting test waiting rather than application code, the final suite passed.

**Actual Bonsai 2 27B tests:** On mode stopped a repeated tool-result sequence with `LOOP_DETECTED` and recovered; Off persisted through page reload and allowed eight repeated successful calls without false stopping; normal tool use published and committed a valid result; On/Off settings were frozen in active/queued work; five-attempt enforcement was checked with one real stopped chat plus four controlled startup failures. The final installed On/Off/normal workflows were successfully checked. Earlier rejected diagnostic trials triggered verified automatic rollbacks; the final installation succeeded with no pending rollback.

**Accounting:** A final Off run retained 148,258 input + 3,620 output = 151,878 cumulative known tokens rather than the smaller latest-round token count. The final normal run reported 24,686 input + 7,467 output = 32,153 total. Stopped/incomplete attempts with missing usage stay explicitly incomplete, not zero.

**Preservation:** 647 original Magazine/history files and 2,898 existing project files retained their hashes in the installation report. The 187-cartridge project was not rerun. No unrelated Open WebUI Functions, Tools, model configuration or workflows were changed by the installation.

**Not established:** clean installation on another Open WebUI host, behaviour for other models, full 187-cartridge acceptance, every arbitrary tool integration, destructive power-loss durability, cancellation of tool side effects already committed, or complete accounting for stopped chats that supply no usage. Detection relies on the saved structured tool-call/result history: volatile tool output, ongoing silent reasoning, or opaque side effects may not be recognised as a loop.

The full original report, logs, screenshots, saved chats, backup metadata, and database records remain in the maintainer's private release/evidence bundle and are intentionally not included here.
