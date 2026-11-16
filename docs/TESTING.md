# Magazine — Testing history

**Version:** `0.1.0-beta.5`  
**Acceptance evidence:** 14 October 2026  
**Status:** Beta proof of concept; not a production-readiness certification.

Magazine testing has evolved across distinct versions. The current reliability.3 evidence is listed first; later sections retain historical handover.1 and beta.5 testing without transferring their results to newer builds. The beta.5 numbers near the end of this page belong only to beta.5; the separate historical beta.4 live tests are identified as such.

## Current installed runner: 0.2.0-reliability.3 (16 November 2026)

**328 automated passed, 0 failed, 19 skipped**; 23 focused regressions passed. Chromium and Firefox were exercised. Real Bonsai 2 27B integration included a successful source-installed Function smoke, missing-result recovery and five identical failure fingerprints without a sixth attempt. In the latter case one inference response was real, with four deterministic post-chat-creation failures; the five cases were not all full model completions. The report independently verified saved ZIP evidence and on-screen token values, but the evidence bundle is not published. No clean-install qualification or 187-cartridge rerun. [Current detailed record](SIMPLE_0.2.0_RELIABILITY3.md).

## Earlier runner: 0.2.0-reliability.2 (16 November 2026)

**308 automated tests passed, 19 skipped, zero failed** in the final suite. Live Open WebUI 0.12.5 / Bonsai 2 27B checks validated Antiloop On/Off, persistence, repeated tool-result detection/stop, result publication/recovery and known cumulative usage. The five-attempt limit test combined one real stopped chat and four injected startup failures. The installed Function is byte-identical to [published source](../integrations/open_webui/function/magazine_simple_reliability2.py). No fresh clean-install, full 187-cartridge retest, other-model qualification or destructive power-loss test was reported. [Current detailed record](SIMPLE_0.2.0_RELIABILITY2.md).

## Historical runner: 0.2.0-handover.1 (15 November 2026)

This is **new, separate evidence**, not part of the 989 beta.5 checks below. The existing Open WebUI 0.12.5 Function was updated in place without changing other Functions, Workspace Tools or the sidebar launcher.

- **Offline:** 193 passed, **90 skipped** (36 Chromium + 36 Firefox unavailable executables, 16 optional screenshots, two unavailable historical fixtures). These skips are not passes.
- **Real-model acceptance:** Four controlled scenarios using **only Bonsai 2 27B** exercised committed handover despite malformed chat text, missing-result fresh-chat publication recovery, expected rejection of an incorrect saved-chat identity, and fresh-chat staged-output repair after a missing required file. An earlier fifth trial was fixed inside its original chat; it is not proof of automatic runner repair.
- **Saved evidence:** All **11 actual worker chat archives** across five acceptance runs were reopened from UI-downloaded ZIPs and verified against message IDs and hashes, as were **eight** committed result documents and markers. The rejected identity run was expected to stop without launching a consumer; another queued run advanced.
- **Safety boundaries:** No claims of successful cross-model testing, live power-loss durability, destructive production restart, or general semantic correctness. The 72-cartridge game belongs to exec.5. No clean-install validation or standalone public installer has been established for handover.1.

Run IDs and detailed failures/limits: [handover.1 engineering and verification record](SIMPLE_0.2.0_HANDOVER1.md). The [byte-verified handover.1 source](../integrations/open_webui/function/magazine_simple_handover1.py) and [matching standalone publisher helper](../integrations/open_webui/function/magazine_publish_helper_handover1.py) are now public. The older [exec.5 source](../integrations/open_webui/function/magazine_simple_exec5.py) is historical. Publishing code does **not** mean the private test artefacts or a fresh-install acceptance have been published.

## Live development-workflow recovery — 15 November 2026

A separate **187-cartridge 3D Resource Kit** development run provided an observed c012 recovery on **handover.1**: attempt 1 ended with `DONE_MISSING`, a fresh attempt preserved and corrected the earlier work, the result publisher succeeded, and the Magazine ledger shows **c012 attempt 2 done** followed immediately by **c013 running**. This confirms recovery and downstream continuation for this one case; it does not confirm completion of the 187-cartridge workflow. **Caveat:** the publication-only recovery chat made an edit contrary to its instruction; this is documented as a model boundary violation, not a validated safety guarantee.

[Recovery case with cropped ledger screenshot and technical chronology](REAL_WORLD_RECOVERY_2026-11-15.md).

---

## Historical beta.5 evidence (14 October 2026)

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


### Source publication and documentation audit (13 November 2026)

The uploaded exec.5 Python source is byte-identical with the stored local Function identity (111,367 bytes; SHA-256 `400f8bf4739bac012ce5c0036eb0ecbcf098695e2a9726a00eef84df934d1309`). That source has been added to the repository under `integrations/open_webui/function/magazine_simple_exec5.py` for inspection and further testing. Source identity does **not** mean a public clean installation, a complete security audit, or a rerun of the 989 older tests.

The README's visual and technical claims were reviewed against this source, including completion rules, owner-scoped runs versus shared library permissions, explicit SHA-256 file handover, in-memory queue and manual restart conditions. The four diagrams are UI reconstructions based on the supplied screenshots; they are **not evidence of browser interaction testing**.


### Additional local source checks (documentation correction)

The exact uploaded exec.5 source compiled as Python. Its embedded `PAGE_HTML` SHA-256 matched the reported page hash `b1b41a55bb2e5ad3f32582bfc9986357fc16a31850d0b448b709a5a252cc2f97`. A small independent local smoke harness passed **9/9 checks** for own-line and inline DONE parsing, quoted status not being treated as final, HALT, missing-file failure, required-DONE failure, optional-DONE success, path traversal rejection and declared YAML file-handover parsing. These do not replace live model/browser testing or the older beta.5 acceptance suites.
