# Changelog

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
