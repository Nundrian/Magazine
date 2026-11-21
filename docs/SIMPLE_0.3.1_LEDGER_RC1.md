# Magazine Simple 0.3.1-ledger.rc1 — compact ledger

**Status:** source-verified and installed on Open WebUI 0.12.5 on 21 November 2026; release candidate, not a general clean-install certification.

**Exact updated Function source:** [magazine_simple_scheduler_rc1.py](../integrations/open_webui/function/magazine_simple_scheduler_rc1.py)

**Source SHA-256:** `da550dbc5e659c411c77395550d9ff59124663f48f20dc6b594f8f61840e0eec`

The originally supplied full candidate ZIP had SHA-256 `be2c8a4fa2f3f18152c1fecf7b52b7e1216a55f6e623be7e31868dc8421d37ef`. That private-host acceptance archive is **not** stored in this public repository; instead the repository publishes the exact source and sanitised documentation. Use the GitHub-generated repository archive for source access, not as a byte-identical replacement for the original candidate ZIP.

## What changed

- Ledger shows **one selected cartridge** instead of the previous multi-row listing.
- Cartridge selector, Previous/Next, workflow-order position, status filtering and Follow current.
- Compact Cartridge / Status / Attempts / Tokens overview with exact model and execution duration.
- Expandable cartridge and individual attempt evidence, scrollable long errors and lazy-loaded full JSON.
- Native saved-chat links for each recorded attempt and an active-chat link when a chat ID exists.
- Saved browser selection/expansion state; read-only reopening never restarts inference.
- Cumulative timed attempts and the full execution span are separately displayed. Token values remain server-reported.
- The Python execution engine, authenticated APIs, scheduler, Antiloop, STOP/HALT, file handovers, result publication and exported run schemas are unchanged according to AST comparison.

## Verification and limitations

The uploaded candidate's report records **446 passing automated checks, 21 skips and no failures**, plus installed-host read-only Firefox/Chromium checks. The update was reported installed in place while idle, with existing Function identity, valves and Magazine data unchanged. See the [detailed test report](TEST_0.3.1_LEDGER_RC1.md).

The native `/c/{recorded-id}` route opened and the backing chat record existed, but complete native chat rendering was **not verified in an authenticated human Firefox session**. No real model calls or production active-cartridge run were performed as part of this ledger change.

The previous 0.3.0 screenshot and technical record remain as historical references; the new ledger intentionally differs.

[Installation and rollback](INSTALL_0.3.1_LEDGER_RC1.md) · [Test report](TEST_0.3.1_LEDGER_RC1.md) · [Changelog](../CHANGELOG.md)
