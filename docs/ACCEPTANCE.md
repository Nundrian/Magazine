# Magazine 0.1.0-beta.5 acceptance

Date: 14 October 2026. Focus: dedicated original Input ZIPs and Finished Work.
Candidate prepared locally; no GitHub publication or live deployment authorized.
Customized frontend binary security/licensing distribution remains **HOLD**.

## Source identity and preservation

The verified beta.4 full source ZIP is the immutable starting point (SHA256
`ebb862eb5fdebb6f2d54ade4366a0b95d5440093340ee6aa4ae314c63d05fce5`).
Its archive was extracted into a separate beta.5 candidate; beta.4 was not edited.
The 29 canonical core Python files and embedded core ZIP are byte-identical
to beta.4. Embedded ZIP SHA256 remains
`82fbb7b41b00142c9ffbb4fb1266aeddb981c3b5b9cb38dc8bed99fbfdff4dd5`.
No execution engine, five-field state schema, result store, STOP, recovery,
model dispatch or tool permission change. No new dependency.

Changes are at the self-contained Function boundary: ZIP retention after import,
separate terminal snapshot exporter and export-status sidecar, owner-only retry
endpoint, verified project labels, minimal existing-message UI additions. Library
metadata binding preserves the UI's already-supported shared/independent routing
keys and existing admission/permission gate order. JSON-only clients still work.

Other changes: new storage tests and UI warnings assertions; refreshed version,
native JSON, source-fingerprinted UI captures and release hashes; storage/upgrade
documentation and acceptance helper. Full source packaging now includes the
Function-only manifest omitted by beta.4, so packaged checks can find both profiles.
Frontend recipes have only release-label changes. Security patches, dependencies,
license documents, and SECURITY/LICENSING/DISTRIBUTION_BLOCKERS are unchanged.

## Fresh verification

Final run evidence and exact artifact hashes are retained outside publication
archives in `evidence/` and `RELEASE_CANDIDATE_PROVENANCE.json`.

| Check | Result |
| --- | --- |
| Canonical core regressions, Linux Python 3.11.9 | 611 PASS |
| Function regressions, same pinned test dependencies | 244 PASS, including 43 dedicated storage cases |
| UI and sidebar regressions, pinned Node 22.16.0 | 134 PASS (130 interface + 4 sidebar) |
| Total regression cases | 989 PASS |
| Embedded source/ZIP/native JSON parity | PASS, unchanged 29-file core |
| Both publication allowlists/version/pins/credential checks | PASS |
| Both ZIPs built twice, exact source parity and CRC | PASS |
| Extracted Function-only source tests and release checks | PASS |
| Native import/auth/library/default ZIP retention/restart from Function-only ZIP | PASS on isolated stock Open WebUI 0.11.3 |
| Actual Windows NTFS bind-mount ZIP retention/export/duplicate smoke | PASS, two sequential real-core cartridges with injected deterministic dispatch |

Storage cases cover exact original ZIP bytes, no retention after invalid/conflict
imports, unrelated ZIPs not imported, unique names/collision refusal, atomic writes
and cleanup, complete/HALTED snapshots, exact Unicode/CRLF output, ordered manifest,
ZIP parity, duplicate/concurrent exports, ownership/authentication, unsafe IDs,
links/overlapping roots, missing results, write and post-publication fsync failures,
retry, existing user work preserved, verified project identities, both publication
paths, and automatic export hooks without altering core status/results.

Failures found during development were investigated: missing descriptor binding,
stale capture hashes/synthetic catalogue identities, metadata validation rejecting
existing routing-key customization, and unsupported NTFS no-replace directory
rename. All were repaired and regressions rerun; failed checks are not counted
as acceptance. On NTFS the fallback reserves a directory exclusively, atomically
links complete files without replacement and publishes the manifest last. The
original library importer and its publication mechanism are not modified.

Known warnings: existing FastAPI/Starlette HTTPX test-client deprecation and the
intentional duplicate-member ZIP fixture warning. No dependency changes made.

## Inherited evidence and scope limits

`BETA4_ACCEPTANCE.md` is historical beta.4 evidence, not fresh beta.5 testing.
Its live Bonsai inference/STOP, browser/native UI and upgrade/rollback tests remain
the independently verified baseline. Beta.5 reruns source regressions and native
installation/restart checks plus actual host-filesystem storage testing.
No fresh Bonsai inference, browser visual QA, frontend rebuild/security scan or
full live-installation upgrade is claimed. Windows production recreation commands
are proposed, not executed; the live installation is inspected read-only.

Only Magazine's canonical text is exported. No external Development Toolkit
files, arbitrary output directories, inferred tool artifacts or reconstructed
chat archives. HALTED exports are explicit owner requests; existing result
retrieval/STOP/recovery behavior remains unchanged. Historical ZIPs are not
backfilled. Backend death after COMPLETED can miss automatic export; use the
retry API. Interrupted publication can require manual preservation/moving-aside
of incomplete export directories; unknown user files are never auto-cleaned.
Export-sidecar persistence failure can leave only a server-log warning; canonical
results remain available. Full storage behavior/permissions/backup limitations
are documented in `STORAGE.md`.

## Release boundaries

Function/source candidate: functionally accepted for review and publication
approval with inherited host-security risks disclosed. Customized Open WebUI
binary: **HOLD unchanged** pending runtime-security and matching corresponding
source/notice closure in the beta.4 security/licensing reports. An SBOM is not
license clearance. No image distribution, production data migration, historical
run deletion, GitHub push/release or deployment was performed.

Use the matching Function-only ZIP for the proposed native update; keep the
existing beta.4 sidebar image and the original Open WebUI volume/environment/
secret/network/ports. `STORAGE.md` gives exact optional Windows mount/recreation,
backup stop-point and rollback instructions for the read-only-inspected install.
Private review folders, credentials, caches, test runs, volumes and evidence are
not publication material. Publish only allowlisted archives after approval.
