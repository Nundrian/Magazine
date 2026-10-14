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
