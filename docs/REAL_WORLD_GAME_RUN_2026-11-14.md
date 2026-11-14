# Live game-building run — 14 November 2026

**Scope:** one completed, real local-model workflow on Magazine Simple **0.2.0-exec.5**, hosted by Open WebUI **0.12.5**. This is evidence of a substantial end-to-end execution, **not** a general certification of game quality or of every Magazine feature.

## Result

| Field | Recorded result |
| --- | --- |
| Workflow | `Nebula Run v3 - Stage 1 Cinder Reach - 72 cartridges` |
| Run ID | `20261114-002347-2aad63` |
| Executor | `bonsai-2:27b` across all cartridges |
| Magazine status | `completed` |
| Completion | **72/72 cartridges `done`; 72 attempts total, no cartridge retries recorded** |
| Overall duration (operator-reported) | **Over 8 hours**; exact start and end times not independently established from the export | 
| Captured Magazine run interval | 14 November 2026, 00:23:49–02:23:28 UTC (**1 hour 59 minutes 39 seconds**) |
| Terminal | Open Terminal |
| Additional tools recorded | Browser Bridge, Magazine Auto Approval Standard, Fresh Worker, Development Toolkit |
| Final output | `Nebula-Run-Stage1-Playable.zip` — 7,306,112 bytes, 67 packaged files |
| Final archive SHA-256 | `c6b5585b1da3ce031358d60b4b83e4bd4fbc37bb59d1d7ca4a9b09ebb29d4367` |

The retained Magazine export contains `LEDGER.md`, `run.json`, 72 saved cartridge responses and prompts, declared inputs/outputs, per-cartridge SHA-256 records and the final game package. The last cartridge produced both the playable game ZIP and `final-acceptance.json`.

**Duration reconciliation:** The operator clarified that the overall game run took **over eight hours**. The exported `run.json` and the individual cartridge `started`/`finished` fields cover only **1:59:39**, with 72 sequential attempts logged between 00:23:52 and 02:23:28 UTC. The records do not explain the difference; neither elapsed-time figure should be silently substituted for the other. The shorter interval is **the captured Magazine ledger interval**, not an independently verified total for all game-development activity.

### What the game evidence shows

- The project contains a browser-based, horizontally scrolling arcade-shooter level, including ship controls, hazards, enemies, upgrades, a boss and an end-of-level state.
- The generated Node.js test suite reported **64 passing tests, 0 failures**.
- A software-canvas visual-slice test reported **PASS**; generated scene captures and metrics are present in the output.
- A scripted, simulated playthrough reached the set-piece, boss and stage-clear state. This is **not** proof that a human completed the level unaided.
- The operator subsequently **confirmed that the game ran in Firefox** on their desktop. That confirmation is distinct from the automated test suite.

### Important limits and anomalies

- The runner's own `game/reports/browser.json` says `"firefox": "NOT TESTED"`: Firefox was not exercised **inside the run environment**. The operator's subsequent Firefox execution is a separate, user-reported observation, not a recorded cross-resolution browser acceptance suite.
- Human gameplay quality, difficulty balance, audio loudness/listening and broader browser compatibility were not independently verified by the run.
- The final audit recorded **one hash mismatch**, `js/art-enemies.js`, against an expected-file digest. The integration cartridge disclosed it and the available syntax, test and visual-slice checks passed; the result must not be described as a zero-discrepancy file-fidelity audit.
- The 64 passing tests were executed as part of the model-created project's acceptance workflow. They are useful test evidence, not independent third-party coverage or proof that all possible defects are absent.
- Magazine status `completed` establishes that its execution and declared output checks finished; it is not, by itself, evidence that the resulting program functions correctly. The test reports and Firefox observation are **additional** evidence.
- This is one observed run on one host/model configuration. It does not establish reliability percentages, unattended success across diverse projects, or that an Unreal Engine game was created. **This game was browser-based, not Unreal Engine.**

## Source and reproducibility boundary

The facts above were checked against the retained `Magazine-20261114-002347-2aad63(8).zip` run export, particularly `run.json`, `LEDGER.md`, `results/70-c070-integration.md`, `results/71-c071-browser-check.md`, `results/72-c072-final-package.md`, the final `reports/final-tests.txt`, `reports/browser.json`, `reports/audit.json` and `outputs/final-acceptance.json`.

The large exported archive and game bundle are **not published in this repository**. The evidence summary is therefore inspectable as a documented result, but the full underlying run is not independently reproducible from this page alone. Any later public release should review third-party assets, especially the soundtrack, before sharing binaries.

This live game run is separate from the historical beta.5 regression totals and from the earlier, shorter exec.5 acceptance workflow. Do not add their test counts together.
