# Changelog

## Magazine Simple 0.3.1-ledger.rc1 — installed ledger update, 21 November 2026

**Latest source-verified release candidate for Open WebUI 0.12.5.** Exact Function source SHA-256: `da550dbc5e659c411c77395550d9ff59124663f48f20dc6b594f8f61840e0eec`. Updated in place with Function identity and configuration preserved; no inference was started.

- **Compact ledger:** one selected cartridge, Previous/Next, workflow-order position, dynamic status filtering and explicit Follow current.
- **Complete attempt history:** expandable records, native chat links, full lazy-loaded evidence and copy controls.
- **Timing:** per-attempt and cumulative execution duration separated from overall execution span; server-reported tokens retain missing/partial/zero distinctions.
- **Persistence:** selection and disclosures retained on refresh; restores are read-only.
- **No engine change:** scheduler, retry policy, checkpoints, Antiloop, STOP, publication and exports remain AST-identical aside from version/UI.
- **Validation:** 446 passed, 21 skipped, zero reported failures. Firefox and Chromium UI acceptance and installed-host read-only checks passed. A signed-in native chat view and active-model execution were not verified.

[Exact updated source](integrations/open_webui/function/magazine_simple_scheduler_rc1.py) · [Release qualification](docs/SIMPLE_0.3.1_LEDGER_RC1.md) · [Full test report](docs/TEST_0.3.1_LEDGER_RC1.md) · [Install/rollback notes](docs/INSTALL_0.3.1_LEDGER_RC1.md)

---

## Magazine Simple 0.3.0-scheduler.rc1 — locally installed, 18 November 2026

**Latest release candidate for Open WebUI 0.12.5.** The supplied exact Function source matches the installed source SHA-256 `acb55b3421bf5ea23510dd1b3f3372d1f19183a7bd6dec8da9940c89bea3ceb3`. The installed Function and original user data were preserved; installation did not restart the previous failed 52/191-cartridge run.

- **Project scheduler:** Sequential (default) or Round Robin. Round Robin rotates projects after a verified cartridge while still allowing **one cartridge at a time**. Active attempts/recovery are not interrupted by a mode change.
- **Persistent queue/checkpoints:** the journal keeps ordering and recovery points across process restart; it excludes credentials and does **not** automatically dispatch work after a restart.
- **Dashboard:** a unified multiple-project view, current status, next queued work, selected project details and restoration after page refresh.
- **Ledger:** six overview columns, expandable attempt details, filters, reported input/output/total tokens and duration with missing data clearly labelled.
- **Preserved safeguards:** existing bounded recovery, Antiloop, STOP/HALT behaviour, committed SHA-256 file handovers, exports and older runs.
- **Verification:** existing core suite 611 passed; frozen baseline 249 passed, 2 skipped (plus 2 browser cases deselected); full candidate 280 passed and **98 skipped**; 48 separate Windows Firefox/Chromium page cases passed; two candidate API/engine/browser integrations passed using mocked model/auth services. Read-only checks on the **installed** Firefox and Chromium browser views passed. Real concurrent-project model execution, deliberate installed-service restart and some historical Linux browser checks remain unverified. Isolated automated tests are not a live multi-model endorsement.

**Public files:** [exact Function source](integrations/open_webui/function/magazine_simple_scheduler_rc1.py), [source-only ZIP](downloads/Magazine-Simple-0.3.0-scheduler.rc1-source.zip), [technical record](docs/TECHNICAL_0.3.0_SCHEDULER_RC1.md), [illustrated guide](docs/USING_MAGAZINE.md). The source ZIP (SHA-256 `495bfd266dc07d1c6ecc89c9c4d9e36fcb242c78c2b01993e64d6294b9b4290e`) is a distribution wrapper containing the exact source and installation notes, not the private regression candidate, account backup or a cartridge workflow.

---

## Magazine Simple 0.2.0-reliability.3 — verified local update, 16 November 2026

**Current installed Open WebUI 0.12.5 Function.** The versioned source exactly matches the maintainer's installed source; not an independent public clean-install certification.

- **Retry admission corrected:** repeated `CHAT_FAILED`, `DONE_MISSING`, missing outputs, and recoverable publication/handover omissions no longer get a blanket no-progress veto merely because their fingerprints match. Up to **five total attempts**, with explicit smaller workflow limits respected and no sixth attempt.
- **Safety preserved:** genuine repeated loops/no-progress failures, HALT, operator Stop, cancellation, identity/integrity faults, permanent storage errors and unconfirmed task stops still block automatic retries.
- **Visible usage:** Reported tokens outside the collapsed ledger; structured per-attempt retry/stop decisions, and attempt/cartridge/run token subtotals with unavailable and incomplete usage indicated.
- **Preserved:** 1,800 seconds per fresh chat, Antiloop settings/detection, publisher, committed handover, checkpoints, historical ledgers and other Function settings.
- **Verification:** 23 focused passes; full suite **328 passed, 0 failed, 19 skipped**. Chromium and Firefox checked. Real Bonsai 2 27B installed-function smoke and missing-result recovery passed. The five-failure integration used one real model response and four deliberate post-chat-creation failures, **not** five full model completions. The 187-cartridge run was not resumed or retested.
- **Files:** [exact Function source](integrations/open_webui/function/magazine_simple_reliability3.py) SHA-256 `28a9f3172192efbd7219a017bdfe7e2d5737882d9b0e3684478bdfc367ec730b`; [public source-only ZIP](downloads/Magazine-Simple-0.2.0-reliability.3-source.zip) SHA-256 `f77aa71637adb29f44983e5600aa168f09011001e868be90d001fb890ec9ca65`. The private evidence exports, local database and backups are not published.

[Engineering/audit summary](docs/SIMPLE_0.2.0_RELIABILITY3.md) · [Testing chronology](docs/TESTING.md)

---

## Magazine Simple 0.2.0-reliability.2 — local installation accepted, 16 November 2026

**Current installed Open WebUI 0.12.5 Function.** Its exact source and a clean public source ZIP are published. An independently accepted public clean install is not claimed.

- **Recovery:** five total saved attempts, including the initial attempt, with **1,800 seconds per fresh chat**. Older explicit settings stay in force when applicable; Continue cannot exceed five attempts.
- **Antiloop:** integrated monitoring of structured tool-call results, repeated errors and short cycles. The default is On; an accessible On/Off switch saves per user and Magazine, freezes at Start and remains fixed for recovery. Off does not disable normal timeouts, Stop/HALT or token accounting.
- **Token accounting:** reports known cumulative usage per attempt, cartridge and run. Missing stopped-chat usage remains incomplete rather than zero or latest-round-only.
- **Compatibility:** keeps verified result-file handover, SHA-256 commits, saved chat archives and old run records without silently migrating historical work.
- **Verification:** 308 automated passed, 19 skipped, zero failed in the final suite, with Chromium and Firefox checks. Real Bonsai 2 27B testing on installed Open WebUI 0.12.5 covered Antiloop On/Off, persistence, bounded recovery, normal publication and token usage. Two earlier rejected diagnostic installations were rolled back. The 187-cartridge workload, other models and fresh public installation were **not** retested.
- **Files:** [verified Function source](integrations/open_webui/function/magazine_simple_reliability2.py) SHA-256 `2112da6c358ebfd2b153e26dea1583cbace98714ff11a21bea31af5b44814377`, and [clean source ZIP](downloads/Magazine-Simple-0.2.0-reliability.2-source.zip) SHA-256 `01f9ef9a8bde75c6b16702a5d440229e6bcab3b1d83dcfa76650ded58abe4404`. The public package excludes private backup, database and chat evidence.

[Current technical/testing record](docs/SIMPLE_0.2.0_RELIABILITY2.md) · [Testing chronology](docs/TESTING.md)

---

## Magazine Simple 0.2.0-handover.1 — installed and acceptance-tested, 15 November 2026

**Local Open WebUI 0.12.5 Function update; not a public clean-install release.** This was installed before reliability.2. New runs use attempt-scoped result files and verified commits rather than relying on final chat prose as the outgoing handover. Previously saved runs retain legacy compatibility; no historical run was migrated or resumed.

- Each fresh attempt now has a separate protected identity/descriptor, exact result-file path and standard-library Python publisher.
- DONE triggers strict result parsing, run/cartridge/attempt/chat identity checks, declared-file and outgoing-selector validation, SHA-256 committed-result checks and read-back before the next cartridge runs.
- Complete saved Open WebUI chats (including the original assistant message and tool calls/results) are archived and exported with run evidence.
- Bounded automatic verification/publication retries and eligible staged-output repair retries are supported. Default: two fresh model retries within a **900-second total cartridge budget**, and two transient-storage retries. HALT, Stop, identity/integrity failure, incomplete commit and exhausted budgets are stop conditions. No general replanning, automatic crash resume or model substitution.
- Live acceptance used **Bonsai 2 27B only**. Four controlled scenarios proved a successful handover despite malformed final chat JSON, fresh-chat result-publication recovery, wrong-chat identity rejection, and fresh-chat staged-file repair. A fifth earlier run repaired its injected problem inside the original chat and is not mislabelled as runner recovery.
- **193 offline passed, 90 skipped.** Skips were 72 unavailable browser cases, 16 optional screenshots and two missing legacy fixtures. Eleven real saved Bonsai chats and eight committed results were reopened/verified from evidence ZIPs.
- The original failed 3D Resource Kit run and historical files were preserved. The sidebar launcher and unrelated Functions/Workspace Tools were unchanged.

**Publication scope:** The exact verified [handover.1 Function source](integrations/open_webui/function/magazine_simple_handover1.py) and [standalone publisher helper](integrations/open_webui/function/magazine_publish_helper_handover1.py) are now tracked in this repository. The publisher is already embedded in the Function. The full verification/evidence release bundle remains unpublished; no separately verified public clean-install package is claimed. The existing `magazine_simple_exec5.py` is historical. The older 72-cartridge game test belongs to exec.5 and the 989 regressions belong to beta.5. See [the handover.1 engineering record](docs/SIMPLE_0.2.0_HANDOVER1.md) and [test chronology](docs/TESTING.md).

---

## Magazine Simple 0.2.0-exec.5 — installed, source-matched, 13 November 2026

**Local installation and documentation update — not a public runner release.** The newer Open WebUI Simple Function was updated in place, keeping its Function ID and existing run data. This repository now documents its streamlined library, model-assignment, Terminal/tool selection, READY/start separation, ledger, run history and deletion controls.

- A live three-cartridge acceptance-line run finished using Qwen3 Coder, real Open Terminal calls in steps 2 and 3 and verified declared file handover (`result.json`: `match: true`).
- The inline DONE completion branch was exercised live.
- The in-place update preserved 178 pre-existing run-folder files with matching hashes.
- Previous-work deletion controls were examined in Chromium; the live deletion was not executed. Firefox interactions and container logs remain unchecked.
- Added a complete [illustrated HTML interface guide](docs/guide/index.html), split into four annotated sections; updated the workflow/handover diagrams.
- Added the supplied **Magazine Simple Sidebar Launcher 0.1.0** source for Open WebUI 0.12.5. The launcher points to `/magazine-simple` and remains separate from the runner.

**Now published for code review:** the exact 111,367-byte exec.5 Function source (SHA-256 `400f8bf4…1309`) at [`integrations/open_webui/function/magazine_simple_exec5.py`](integrations/open_webui/function/magazine_simple_exec5.py). **Not independently verified as a clean-install release:** an exec.5 installer ZIP or fresh-host acceptance. The separate historical beta.5 download is unchanged. See [exec.5 evidence](docs/SIMPLE_0.2.0_EXEC5.md) and [launcher notes](docs/SIDEBAR_LAUNCHER.md).

---

Magazine is a beta proof of concept. This changelog records the main demonstrated changes and avoids treating proposed features as completed work.

## 0.1.0-beta.5 — 14 October 2026

- Retain an exact copy of a successfully imported workflow ZIP in a dedicated Input ZIPs directory. Import remains an explicit action; placing a file in the directory does not import it.
- Export saved cartridge text to Finished Work after completed runs, with an ordered `manifest.json` and `results.zip`, grouped by owner, workflow and run.
- Add an owner-only explicit export/retry path for eligible runs, including saved portions of halted runs.
- Add storage checks for Windows NTFS and Linux paths, collisions, ownership, partial failures and output consistency.
- Keep the canonical 29-file execution core, model/tool routing, STOP and recovery semantics unchanged from beta.4.
- Record **989 passing regressions** across core, Function and interface/sidebar checks; live Bonsai acceptance from beta.4 is historical evidence rather than a fresh beta.5 test.

The public install route is the Open WebUI Function-only integration. The customised Open WebUI Docker binary remains on distribution hold.

## Earlier beta milestones

Previous beta releases established the bounded-cartridge execution core, the native Open WebUI integration, durable run history and STOP/recovery behaviour. For the detailed previous acceptance evidence, see [beta.4 acceptance](docs/BETA4_ACCEPTANCE.md) and the [beta.5 acceptance record](docs/ACCEPTANCE.md).

Further features are planned; their details will be announced when ready.
