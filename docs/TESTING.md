# Magazine beta.5 — Testing summary

**Version:** `0.1.0-beta.5`  
**Acceptance evidence:** 14 October 2026  
**Status:** Beta proof of concept; not a production-readiness certification.

Magazine has been tested at several levels. The numbers below are from the beta.5 acceptance record; the live Bonsai tests described separately were performed against the preceding beta.4 baseline.

## Automated regressions

| Test suite | Result | What it exercises |
| --- | ---: | --- |
| Canonical execution core | **611 passed** | Execution, transitions, sequencing, durability and recovery |
| Open WebUI Function | **244 passed** | Integration, authentication, routing, permissions and storage (including 43 dedicated storage cases) |
| Interface and sidebar | **134 passed** | User interface and existing sidebar contract |
| **Total** | **989 passed** | Automated regression checks, not 989 live model sessions |

The acceptance record also reports the embedded-source and native-JSON parity checks, reproducible release archive checks and both publication allowlist checks as passed.

## Beta.5 integration and storage checks

- Native Function import, authentication, library use, ZIP retention and persistence across a restart were checked using an isolated **stock Open WebUI 0.11.3** instance.
- Windows NTFS bind mounts were used to check original ZIP retention and Finished Work export. Two sequential cartridges ran through the real Magazine core with **injected deterministic model responses**, rather than a live LLM.
- Storage tests include output text and ZIP consistency, manifest ordering, collisions, ownership checks, partial failures and explicit export retry behaviour.
- During local installation testing, the new input and finished-work folders were observed to receive their expected files. This is practical confirmation of the feature, not exhaustive deployment coverage.

## Earlier live model checks

The retained **beta.4** evidence includes live Bonsai inference and STOP behaviour, browser interaction checks and upgrade/rollback exercises. These establish a useful prior baseline, but **were not repeated as fresh beta.5 live-model acceptance**.

## Important boundaries

A workflow marked **COMPLETED** has run through its cartridges and saved their canonical responses. That status does not verify that the responses are correct or that the underlying task was successful. Some recovery, storage interruption and deployment conditions remain outside the accepted test scope.

The customised Open WebUI Docker image is **not** an approved public binary release. The source and Function-only path have a separate acceptance and publication scope.

Read [beta.5 acceptance](ACCEPTANCE.md), [validation instructions](VALIDATION.md), [STOP behaviour](STOP.md) and [security notes](SECURITY.md) for technical details and limitations.


---

## Magazine Simple 0.2.0-exec.5 — newer local evidence, 13 November 2026

**This is a different codebase/version from the beta.5 suite above. Do not transfer its 989 passing cases to exec.5.**

The maintainer's installation report records an in-place Function update preserving 178 existing run-folder files by hash, plus one completed **live three-cartridge** acceptance-line workflow with `qwen3-coder-udq4-native:latest`, Open Terminal and no extra tools.

Steps 2 and 3 made real `run_command` calls, the declared handover was verified (`result.json`: `match: true`), and the step-2 inline DONE branch was exercised. This is direct evidence for a specific execution/handover and completion-path scenario — not an all-model or all-workflow success claim.

The previous-work deletion UI was checked in Chromium for placement, selection and cancellation with no delete request. **No live deletion was performed.** Firefox controls and the Open WebUI container logs were not verified.

For detailed run identity, abbreviated source/export hashes and source/release boundaries, read [the exec.5 installed-version record](SIMPLE_0.2.0_EXEC5.md). Its public runner source was not available for a fresh automated regression run in this documentation update. Historical persistent-fault regression scenarios should be retained as test *cases* and specifically rerun on any future published Simple build.
