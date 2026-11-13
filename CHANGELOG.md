# Changelog

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
