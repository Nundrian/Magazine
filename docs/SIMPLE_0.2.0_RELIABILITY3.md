# Magazine Simple 0.2.0-reliability.3 — retry admission and token visibility

**Status:** Installed and accepted on the maintainer's existing Open WebUI **0.12.5** Function (`magazine_simple`) on **16 November 2026**. This is an in-place update of reliability.2, **not** an independently verified clean installation on another machine.

- [Exact installed Python Function source](../integrations/open_webui/function/magazine_simple_reliability3.py)
- [Source-only ZIP](../downloads/Magazine-Simple-0.2.0-reliability.3-source.zip)
- [Earlier reliability.2 engineering record](SIMPLE_0.2.0_RELIABILITY2.md)

**Source SHA-256:** `28a9f3172192efbd7219a017bdfe7e2d5737882d9b0e3684478bdfc367ec730b` (166,397 bytes). The public ZIP contains the same source and a short README. Private chats, evidence exports, backups, credentials and local settings are not included.

## Root cause: three attempts despite a five-attempt policy

A real run's cartridge `c039` stopped after three attempts: `CHAT_FAILED`, then two `DONE_MISSING` failures. Its saved workflow and run copy already configured `max_attempts: 5`, `max_retries: 4` and `attempt_seconds: 1800`; there was no separate three-attempt valve cap.

The previous retry-admission logic treated a repeated fingerprint of the failure code, candidate-file snapshot and payload hash as evidence of no progress. Both unfinished attempts had the same `DONE_MISSING` code and no candidate-file or payload hash, so the third failure vetoed the fourth attempt despite remaining allowance. These snapshots did **not** establish that the model's work in the shared project had stopped progressing.

This was a retry **admission** defect, not a timeout expiry, an Antiloop stop, or a configured three-attempt ceiling. The underlying model generation ended without a final DONE line; why it ended remains unverified.

## What changed in reliability.3

- **Eligible repeated omissions** — incomplete/no-answer chats, missing DONE, missing outputs, and recoverable result/publication/handover omissions — can consume the configured attempt allowance even when their fingerprints repeat. Five attempts means **one initial attempt and up to four retries**, never a sixth, and not a promise that every failure warrants five.
- **Genuine blockers still stop**: confirmed loops, repeated failures of other kinds without verified progress, HALT, operator Stop/cancellation, identity or integrity errors, permanent storage failures, and unconfirmed chat-task termination. A smaller explicit workflow maximum remains respected.
- **Retry decisions are recorded** per new file-mode attempt: attempt number, outcome, failure code/note, action, reason, remaining allowance, repetition classification and fingerprint. Older saved ledgers remain historical, without invented new fields.
- **Reported token usage is now visible on the run page** without opening the ledger. Expanded ledger rows show per-attempt input/output/total and cartridge subtotals. Missing usage is **unavailable**, and mixed known/unknown totals are marked **incomplete**. These are usage records from Open WebUI, not estimates of live token generation.
- **Unchanged:** default 1,800-second allowance **per fresh chat**; five-attempt ceiling; Antiloop detector/settings; chat execution and timeout mechanism; committed file handovers; publisher helper; checkpoint recovery and model configuration. The installation report states that 17 selected backend functions were AST-equivalent to reliability.2.

## Verification reported for the installed build

| Check | Outcome |
| --- | --- |
| Focused regressions | **23 passed** |
| Full automated suite | **328 passed, 0 failed, 19 skipped**, one warning |
| Browser checks | Chromium and Firefox run; 16 optional screenshots, two missing historical fixtures and one Chromium-only accessibility case account for skips |
| Five identical no-answer failures | Five fresh chats made; four retry decisions then `MAX_ATTEMPTS_REACHED`; no sixth. **One** real Bonsai response plus four controlled post-creation failures — *not* five full model completions |
| Missing published handover | Fresh recovery chat with actual Bonsai response; result committed |
| Installed smoke test | Actual Bonsai tool run and verified result commit; no residual task |
| Installed UI | Historical c039 stop reason shown; per-run token view confirmed |

The report describes independent verification of three evidence ZIP exports and their usage sums. The public repository currently publishes the **source and this summary**, not the private test fixtures or exports. These reported results should therefore not be described as independently reproducible solely from this GitHub commit.

The existing failed c039 record is deliberately preserved at three attempts. It was **not automatically resumed**. The operator may select **Continue from this step** to use the two remaining attempts, provided the existing safety checks permit recovery. The complete 187-cartridge production workflow was **not** rerun.

## Install and rollback boundary

This is a **versioned Open WebUI Function source**, not a cartridge workflow ZIP. Back up the existing Function and Open WebUI data before updating. On Open WebUI 0.12.5, update the existing `magazine_simple` Function using the supplied source rather than creating a duplicate. Preserve its valves and permissions, and verify the installed source checksum. The maintainer's own update used the authenticated Function API, kept the original ID and settings, and did not restart the production instance.

The installer/rollback scripts and complete private backups mentioned in the installation report **are not part of this GitHub package**. Do not copy a temporary container rollback command from an unrelated machine and assume it will work.

**Known limits:** a successful recovered publication does not establish semantic correctness of generated work; the model's missing-DONE/truncation cause remains open; no independent clean-install acceptance, general other-model qualification, full 187-cartridge rerun or destructive restart test is claimed.
