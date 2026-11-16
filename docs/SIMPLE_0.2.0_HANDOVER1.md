> **Historical record:** Handover.1 was superseded by [reliability.2](SIMPLE_0.2.0_RELIABILITY2.md) on 16 November 2026. The test results and hashes below remain specific to handover.1.

# Magazine Simple 0.2.0-handover.1 — engineering and verification record

**Installed locally:** 15 November 2026 · **Host:** Open WebUI 0.12.5 · **Route:** `/magazine-simple` · **Function:** existing `magazine_simple`, updated in place.

**Distribution boundary:** The [verified handover.1 Function source](../integrations/open_webui/function/magazine_simple_handover1.py) and [standalone publisher helper](../integrations/open_webui/function/magazine_publish_helper_handover1.py) are now published for inspection and source-based use. The helper is embedded in the Function, and is **not** a second Open WebUI Function to install. The maintainer's full release bundle (tests, offline logs and live evidence ZIPs) has **not** been published here. Installation was accepted as an update to an existing local Function; an independently verified clean installation on a fresh host is **not** claimed. The [exec.5 source](../integrations/open_webui/function/magazine_simple_exec5.py) is the historical predecessor.

[Main README](../README.md) · [Testing chronology](TESTING.md) · [Earlier exec.5 engineering reference](SIMPLE_0.2.0_EXEC5.md)

## Change from exec.5

The earlier Simple runner used the saved final chat reply for value handover. **Newly prepared handover.1 runs default to `result_file_v1`:** each cartridge attempt has its own identifier, saved Open WebUI chat, protected descriptor and exact result path. A standard-library Python publisher serialises the logical result so the model does not have to reproduce it perfectly in its final chat.

A producer is marked done only after the runner:
1. Observes the required DONE completion status and declared outputs.
2. Reads and strictly parses the attempt's result file; rejects invalid JSON and duplicate keys, non-finite numbers and unsupported result shapes.
3. Confirms **run, cartridge, attempt and saved-chat identity**, and resolves each declared outgoing JSON selector.
4. Archives the saved Open WebUI chat, including assistant-message identity and actual tool calls/results.
5. Copies the accepted result into Magazine-controlled storage, verifies SHA-256 and a commit marker by read-back, and persists the completed state.
6. Allows the next cartridge to start only after rechecking the committed handover and relevant chat archive hashes.

The original saved chats remain in Open WebUI. An uncommitted or interrupted result is not treated as an accepted handover. **Structural/integrity checks do not establish semantic correctness**: task-authored checks remain necessary.

## Bounded recovery

| Condition | Behaviour |
| --- | --- |
| Missing or invalid published result | A fresh **verification/publication** chat can retry within policy limits. It may publish a valid result but must not silently edit an already completed task. |
| Missing declared staged output or incomplete response | A fresh **repair** chat is eligible when the cartridge declares files in Magazine's staging area and permits repair. |
| `retry_safe: false` on a cartridge | Disables its automatic edit-repair scope. |
| Shared-project work without declared staged outputs | Publication-only recovery; if the task needs edits, it must HALT. |
| Transient storage errors | May be retried without another model invocation. |
| Stop, HALT, integrity/identity failure, incomplete commit, permanent failure or exhausted budget | Stop the affected run, retaining evidence. No bypass retry. |
| Repeated failure without verified progress | Stop early, even if retry allowance remains. |

A fresh model retry has a **fresh chat**, not an extension of the failed chat. Recovery receives the task, precise failure and relevant *unverified* candidate-file information; it does not ingest the entire previous conversation. Useful already-created files are retained and snapshotted. There is **no automatic crash restart**, replanning, model substitution or chat deletion.

### Optional `magazine.yaml` settings

```yaml
schema_version: "1.0"
name: "Example workflow"
handover_mode: result_file_v1   # default for newly prepared runs
result_profile: generic        # or magazine.task.v1
recovery:
  max_retries: 2               # 0..5 fresh model retries
  seconds: 900                 # 30..7200 seconds total per cartridge
  io_retries: 2                # 0..5 transient-storage retries
cartridges:
  - id: c01
    model: writer
    prompt_file: cartridges/01.md
    artifacts:
      outputs: [results/output.json]
    retry_safe: true           # optional; defaults according to declared outputs
```

The common publisher interface accepts `--verified`, `--file`, `--check` and `--note` for a conventional result, and `--payload-file` for complete custom payloads or additional top-level keys. The Function supplies attempt-scoped publisher instructions when preparing new runs; workflow authors need not embed a second manually typed JSON representation.

**DONE is always required in `result_file_v1`, even if the legacy `require_done_line` valve is off.**

## Historical runs and compatibility

Existing saved runs without a handover mode use explicit `legacy_chat` compatibility; they are **not automatically migrated or resumed**. Producer-side outgoing-selector validation was added to the legacy path, so an invalid handover can be caught before a dependent cartridge starts. Previously finished handovers are not retroactively repaired.

The failed 3D Resource Kit run and its files were preserved during the update. Restarting or reworking that historical run requires a separately reviewed decision. Do not resume a newly created file-protocol run under old exec.5 code.

## Live acceptance — 15 November 2026

All live inference for this update used **`bonsai-2:27b` only** in the existing Open WebUI installation. The controlled tests created new runs; the original user project was not fault-injected.

| Test | Saved run | Verified result |
| --- | --- | --- |
| Valid result file despite deliberately malformed JSON in final chat text | `20261115-165037-82b075` | Producer committed and consumer received exact declared value/file |
| Original attempt's published result removed | `20261115-165217-62a43c` | Result-missing failure followed by fresh publication chat; consumer then completed; useful candidate file hash retained |
| Result supplied with an incorrect chat identity | `20261115-165341-7dc264` | Expected **rejection** with `RESULT_IDENTITY_MISMATCH`; consumer not started; queue advanced to other work |
| Required staged output removed after original reply was saved done | `20261115-170237-5d2784` | Fresh **repair** chat recreated and verified output; committed value/file consumed successfully |

A fifth recorded trial (`20261115-165355-55ec2d`) also completed, but the model repaired the deletion **within the original chat**. It is not evidence for runner-level fresh-chat repair.

Across the five runs, the report records **11 real saved Bonsai chats** (eight successful committed attempts, three deliberately failed producer attempts); all 11 chat archives were reopened from downloaded result ZIPs and checked against original message IDs and hashes. All eight committed result documents and markers were also checked. The wrong-identity rejection is an expected pass for that safety test, not a failed deployment.

## Offline validation and limitations

**193 passed, 90 skipped** in isolated offline tests. The skips comprise **72 browser cases** (36 Chromium, 36 Firefox; executables unavailable), **16 optional screenshot cases**, and **two unavailable legacy fixture cases**. Do not count skipped cases as successes.

Tests covered strict result parsing, identity checks, pointer resolution, missing DONE/output/result failures, stop/HALT, repeated failures, IO and commit interruptions, path types, changing files, tampering, idempotent commits, useful-file retention and inherited engine/file-integrity cases. The Python publisher was also run under an unprivileged Linux account; it could publish but not replace the protected descriptor.

**Not established by this evidence:** other local-model behaviour, clean installation on a new Open WebUI host, Firefox/Chromium suite passes in the isolated image, destructive production crash/restart recovery, host power-loss durability, broad semantic output correctness or a production security certification. The reported real Open WebUI UI/Bonsai acceptance is separate from the skipped isolated browser suite.

### Source identity

| File/state | SHA-256 |
| --- | --- |
| [Tested `magazine_simple.py` source, now published](../integrations/open_webui/function/magazine_simple_handover1.py) | `55e7a7da26e0bea0ab4c0b485ac345d9f5330400a49eb379a0b8591a1f1e782c` |
| Locally installed, Open WebUI-formatted Function | `47137786e731ff4bf081e407340b69a15c75637407acc1dbec23f07ee9d33b50` |

The installer's report records an **AST match**, rather than byte identity, between the tested source and Open WebUI's formatted source. GitHub's tracked Function file is **byte-identical to the tested release source**, verified by its Git blob SHA-1 `df14e2df3e006176b3b0eec01a0ee725d26eba29`. The separately published helper is byte-identical to the bundle's `publish_helper.py` (SHA-256 `3468e35a2d78164e0d496ed8305f658255dcc2f11029eda6be9fe9ab080b0eed`). The old exec.5 Function source and Function record were preserved for rollback. The sidebar launcher, other Magazine Functions and existing Workspace Tools were not changed; no production container or volume was recreated.

The supplied report also notes that the identity fault injector's log has a **display-reference discrepancy**; its original byte hash and intended chat identity are separately recorded, and the log was preserved without rewriting it.

## Usage and rollback

On an existing Open WebUI 0.12.5 deployment, the published Function source is intended for the Open WebUI Function editor. Back up the active Function first and update only while the Magazine queue is idle; do not replace the Sidebar Launcher with the publisher helper. **The published source is not accompanied by a separately accepted fresh-install procedure or a public installer ZIP.** New workflows are prepared and started as before, using a workspace-capable Terminal/tools selection. Existing handover declarations and selectors are unchanged. `/api/magazine-simple` and `/magazine-simple` remain the integration paths.

If a rollback is required, the maintainer's procedure is to wait until the Magazine queue is idle, restore the preserved **exec.5 Function source** in the Open WebUI Function editor, save and refresh the page. Keep new runs and evidence archived: **exec.5 does not understand the new committed-result protocol**. No rollback execution was claimed in this update.

**Provenance:** supplied 15 November 2026 `UPDATE-REPORT.md` and `Magazine-Simple-0.2.0-handover.1-verified-20261115.zip` (source, tests, machine-readable verification, logs and five live result archives). These test artefacts are not all reproduced in this public repository. The earlier [72-cartridge game run](REAL_WORLD_GAME_RUN_2026-11-14.md) belongs to exec.5, while the [989 historical regressions](TESTING.md) belong to beta.5.
