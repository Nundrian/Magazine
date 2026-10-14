# Magazine 0.1.0-beta.4 acceptance

Date: 13 October 2026. **Functional readiness: PASS. Native Function source
distribution: ready for publication approval with disclosed host risks.
Customized image distribution: HOLD. Actual publication: none.**

## Candidate and baseline

The beta.3 acceptance report and ZIP fingerprints match
`RELEASE_REVIEW_PROVENANCE.json`; its ZIP passes CRC and exact source parity.
Its 944-test/live acceptance remains historical evidence. Beta.3 source,
archives and evidence are preserved.

All 29 canonical core files in beta.4 are byte-identical to beta.3.
Function logic is unchanged: an AST comparison excludes only the version/header
and embedded archive constants. The compressed embedded ZIP was regenerated
using Linux Python 3.11.9 because different Python/zlib builds produced
different compressed bytes from identical files. Core contents remain identical.
Version, native JSON and manifests identify beta.4 consistently.

The optional frontend retains the sidebar and Open WebUI 0.11.3 source commit
`2a960a59fe1dbbd35282f0556b3666d81102e781`. It adds narrowly pinned ProseMirror
view 1.42.3, its required model dependency, and vendor SheetJS CE 0.20.3.
Upstream 0.11.4's inspected lockfile retains the two affected parser versions,
so no rebase was made.

## Fresh acceptance

| Check | Result |
| --- | --- |
| Canonical core, Linux Python 3.11.9 | 611 passed |
| Function/runner, same toolchain | 201 passed; two expected test warnings |
| Browser logic plus launcher, Node 22.16.0 | 132 passed |
| Total source regressions | **944 passed** |
| Added XLSX/XLS/ODS, ESM and dependency provenance checks | **5 passed** |
| Patched frontend and runtime image builds | PASS locally |
| Native stock-image install/activation/authentication/admin-only import/restart | PASS in disposable volume |
| Native browser JSON import from extracted Function-only archive | PASS; exact final Function identity, enabled, zero runs |
| Actual expanded sidebar click/new tab/draft preservation | PASS; original saved chat and unsent draft retained |
| Bonsai execution/STOP/result preservation/owner isolation | PASS |
| Backup/restore/beta.3-to-beta.4 upgrade/restart/removal/reinstallation/rollback | PASS on disposable copies |
| Both publication manifests, credential patterns and matching metadata | PASS |
| Repeated Linux archive builds, CRC and exact source parity | PASS |
| Compose configuration and missing-secret refusal | PASS; no new Compose live deployment claimed |

Ten initial fixture setup errors were repaired by the existing capture generator
after the Function identity changed. They were packaging fixture mismatches;
the final complete suite passes. All acceptance resources used new test data.
The original Git checkout is clean on its existing repair branch.

## Real STOP evidence

The actual interface started run `abedd8156de2` as a normal user using
**Bonsai 2 27B**, verified `Ternary-Bonsai-2-27B-PQ2_0.gguf`.
Cartridge 1 was dispatched at 19:38:15 UTC and completed; a fresh Cartridge 2
request followed at 19:38:17. Provider processing was observed during Cartridge 2.
Browser STOP led to owner-scoped Open WebUI cancellation at 19:38:41.

Final state is HALTED, done `[cartridge_1]`, current `cartridge_2`,
todo `[cartridge_3]`. Cancellation and termination are separately CONFIRMED.
Only two completion requests occurred. The completed first worker chat was
deleted as designed; the interrupted worker chat remains in the stopped database.
The exact 20-byte result `CARTRIDGE_1_COMPLETE` retains SHA-256
`87de37d1974ab3c83484cb463c1a2efeb33213a51815d4e5b87560571380e889`.
Another account received 404 for this run. Provider slots were idle afterward.

This confirms the implemented Magazine/Open WebUI contract. It does not supply
a provider cancellation receipt or a token-level cancellation latency benchmark.

## Maintenance and isolation

A consistent whole-data backup of the stopped disposable live service was
restored into a new volume. The copy ran the prior beta.3 image/Function,
upgraded to beta.4, restarted, removed the Function, switched to stock Open
WebUI, reinstalled beta.4 and recovered its results. A separate pre-upgrade
backup restored beta.3 completely. Every Magazine file hash stayed unchanged.

Seven new acceptance/maintenance containers are stopped. Dedicated volumes
and private backups are retained. The normal `open-webui` service retains its
observed start timestamp `2026-10-13T09:57:52.266375831Z`; its volume and Function
were not used or changed. No production deployment or remote publication occurred.

## Security and licence decisions

Fresh offline lockfile analysis uses Trivy 0.75.0 and the preserved advisory
database updated 13 October at 06:41 UTC. Runtime lockfile matches decreased
from 79 to 76; neither patched parser has a remaining match.
Counts are advisory matches, not exploit counts.

The fresh full candidate-image scan completed after a Windows-mounted attempt
timed out; retrying with isolated Linux storage succeeded. It records 5,316
runtime advisory matches (the pinned runtime is unchanged), and the current
SPDX inventory contains 266 entries with GPL/LGPL/MPL expressions. Those are
inventory entries, including mixed/duplicate records, not independently proven
exploits or a definitive legal component count. No direct HTTPX/PyYAML match
was detected in the actual image/lockfile scans. Existing exact-version/advisory
triage is retained only for unchanged source/base/configuration paths; new or
unresolved paths stay unreviewed.

Remaining production findings and corresponding-source/
notice gaps keep binary redistribution on HOLD; see
`DISTRIBUTION_BLOCKERS.md`, `SECURITY.md` and `LICENSING.md`.
Parser compatibility/build/browser checks are not exhaustive adversarial fuzzing.

The Function-only archive carries Magazine MIT source/native JSON, no customized
Open WebUI frontend/image and no third-party binaries or source. Package selection
enforces that boundary. Its installation is independently accepted; image
corresponding-source gaps do not hold it. Stock Open WebUI 0.11.3 still has
inherited host risks, clearly disclosed for operators. This is source-distribution
readiness, not certification of the whole host's security.

Accepted configuration is Linux/amd64 Docker Desktop, single backend process,
Open WebUI 0.11.3, default server-local callback and persistent data.
Other Open WebUI versions/architectures, Redis/multiple processes, proxy prefixes,
mobile and other browser engines are unaccepted. No additional provider/tool
capabilities or semantic output verifier were introduced.

## Proposed publication action

After explicit approval, publish the reviewed beta.4 Magazine source,
`Magazine-0.1.0-beta.4-FunctionOnly.zip`, matching `magazine-function.json`
and checksums to the existing Magazine GitHub repository as a prerelease.
Describe native installation as the accepted option, including its bookmark
and host-security limitations. Retain the sidebar recipe as a local review
candidate and do not publish a binary image.

Final archive/source/Function hashes are recorded outside the self-contained
archives in `SHA256SUMS.txt` and `RELEASE_CANDIDATE_PROVENANCE.json`.
Publish only the allowlisted material, never the complete review directory:
private backups, local histories, images, caches and credentials are excluded.
