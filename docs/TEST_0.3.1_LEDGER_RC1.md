# Qualification and regression report

## Final automated results

| Suite | Passed | Skipped | Failures / errors |
| --- | ---: | ---: | ---: |
| Linux engine, security, scheduler, recovery, API and export regression | 281 | 4 | 0 / 0 |
| New single-cartridge acceptance, Firefox and Chromium | 38 | 0 | 0 / 0 |
| Existing dashboard/restoration regression, both browsers | 48 | 0 | 0 / 0 |
| Existing setup, token and deletion UI regression, both browsers | 77 | 17 | 0 / 0 |
| Real candidate API/scheduler integration, both browsers and fake providers | 2 | 0 | 0 / 0 |
| Total | **446** | **21** | **0 / 0** |

Final JUnit files and logs are in `evidence/`. `final-verification.json` verifies source synchronization, engine AST equality, JavaScript syntax and the installed Function source hash.

The four Linux skips were: historical 0.2.0 UI reproduction source absent, historical Run B workflow absent, and two unavailable Linux browser-driver cases. Those browser-driver skips are not counted as acceptance; the separate Windows browser suites ran both engines. The 17 Windows UI skips were 16 opt-in screenshot-generation cases with SHOTS_DIR unset and one Chromium-only accessibility-tree case unsupported in Firefox. Screenshots were separately captured by the active browser acceptance suites. One existing Starlette TestClient deprecation warning was non-fatal.

## Required coverage

| Scenario | Evidence |
| --- | --- |
| ID selection, Previous/Next, original numeric workflow order | New suite; unpadded c1…c250 identifiers |
| Actual and unknown status values; excluded selection | New filter-state test, including live status transition pinning |
| Expand/collapse and all five attempts | New suite and existing dashboard layout cases |
| Missing, zero, cached and partial usage | New browser tests, token UI tests, server aggregation/export regression |
| Aggregate totals after cartridge updates | Updated recorded run snapshots and existing server reconstruction tests |
| Attempt and cumulative duration, retry/queue gaps, active elapsed | Exact fixture intervals; missing starts, unfinished stops and duplicate snapshots |
| Long errors, final-failure messages, JSON, copy and HTML injection | Escaped script/image payloads, bounded-scroll assertions, lazy evidence checks |
| Historical and active chat controls | Exact recorded IDs, encoding, new-tab behavior and missing-ID cases |
| Live updates and deliberate following | Poll/update tests and rapid Chromium native-toggle cases |
| Refresh, run switching, browser reopen | New view-preference tests plus existing persistent-profile process restart |
| Interrupted restoration without inference | GET-only restore checks and engine checkpoint/recovery regression |
| 1, 10, 54, 187, 250 cartridges | Both browsers; one rendered summary, bounded selected details, selection under two seconds in these fixtures |
| Exports, Start/Stop, Sequential/Round Robin, recovery | Linux regression and isolated real API/browser integration |
| Responsive/light/dark appearance | 390/768/1280 new layout tests; existing 1024/1280/1440/1920 light/dark cases |

## Installed-host acceptance

Open WebUI 0.12.5 was inspected read-only before installation. Installed source was `0.3.0-scheduler.rc1`, normalized LF SHA-256 `acb55b3421bf5ea23510dd1b3f3372d1f19183a7bd6dec8da9940c89bea3ceb3`. The live queue and active task list were empty. The existing saved run was stopped, 2 of 43 cartridges complete.

After the guarded source update, Firefox 159.0 and Chromium 159.0.8154.6 each rendered one cartridge summary, exposed all recorded attempts for the selected cartridge, restored the selected cartridge and Ledger expansion after refresh and continued GET-only polling without browser errors. Before/after images and JSON results are in `evidence/screenshots/` and `host-browser-*.json`. Installation-time file inventories proved Magazine state/workspace unchanged; Function identity, activation, global state, non-manifest metadata and valves were preserved. No services restarted.

The recorded historical chat existed and its native `/api/v1/chats/{id}` response contained two saved messages. Both browsers opened the exact `/c/{recorded-id}` route in a new tab without changing the ledger selection. However, these disposable profiles used an API key, and native Open WebUI required a human session. They displayed sign-in, not the conversation. A subsequent attempt to inspect the existing human Firefox session failed in desktop computer-use transport before the app could be read. Full native conversation/tool-interaction rendering is therefore **unverified**, not a pass. Native navigation requires the user's ordinary signed-in Open WebUI session. There is no second chat store and no auth bypass. Missing IDs show unavailable; stale/deleted recorded chats are left to native Open WebUI's normal response.

No active production attempt was created to test Open current chat. That control is qualified with an active fixture and a recorded ID; real active-model behavior remains unverified under the user's no-LLM-testing instruction. No real models, current projects or existing workflows were run or modified by tests.

## Defects and harness corrections

Review and browser testing corrected missing-timestamp zero duration, first-versus-latest duplicate duration snapshots, unbounded last-failure notes, ambiguous completion labels, deferred native disclosure persistence on rapid selection/refresh and a null-run race during delayed run switching. Relevant tests were repeated successfully.

Initial Windows runs of the Linux permission suite failed on platform-specific symlink/FIFO/inode/newline semantics; the production Python code was unchanged. Requalification on a native disposable Linux filesystem passed. Copied legacy test text was corrected to UTF-8. Legacy selectors were narrowed to Ledger, the old six-column assertions were adapted to the new summary, a pre-scheduler absolute vertical-position assumption was replaced with its retained collapse-height assertion, and Firefox's fractional outline rounding was accounted for. No engine checks were weakened or engine changes made to satisfy tests.
