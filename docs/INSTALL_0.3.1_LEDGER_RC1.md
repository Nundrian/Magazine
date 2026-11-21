# Installation and rollback

## Actual installation

The existing `magazine_simple` Function was updated in place from `0.3.0-scheduler.rc1` to `0.3.1-ledger.rc1` on Open WebUI 0.12.5. The installer checked that Magazine had no running/queued work, no saved active records and no active Open WebUI tasks immediately before updating. It used the native authenticated Function administrative update route.

No second runner was created. Identity, name, type, activation/global flags, owner, valves and non-manifest metadata were verified unchanged. `/magazine-simple`, authenticated APIs, Sidebar Launcher and saved state remain. No services restarted and no workflow, recovery or inference was triggered.

Installed source normalized LF SHA-256:

```text
da550dbc5e659c411c77395550d9ff59124663f48f20dc6b594f8f61840e0eec
```

The installation created a verified private local backup directory. Its machine-specific location is intentionally omitted from this public document.

It contains the API Function record, exact database Function row/settings, previous Function source, Magazine data/workspace inventory, matched Magazine state ZIP and checksums. Backup access inherits the private parent ACL limited to the user, SYSTEM and administrators. ZIP read-back and every inventoried file hash were verified. Private data is excluded from the distributable source ZIP.

An initial broad database-copy request was rejected by automatic approval review because it could include unrelated credentials/chats. No full database copy was made. The completed narrower backup covers the Function and relevant Magazine state without copying unrelated account/chat data.

## Applying the source to an existing compatible installation

1. Confirm the actual installed source/version, Function identity and valves; compare with this qualified baseline. Inspect newer local changes before replacing anything.
2. Wait for a safe idle point. Check running/waiting Magazine queues, saved active statuses and active Open WebUI tasks. Do not stop work to force installation.
3. Export the current Function source/settings and back up relevant Magazine data, library and workspace as a matched set. Verify backup hashes and preserve saved chats/evidence.
4. In the existing Function's supported administrative editor/update workflow, replace its content with `integrations/open_webui/function/magazine_simple_scheduler_rc1.py`. Retain the existing ID `magazine_simple`, valves and activation; do not create another Function.
5. Verify the source checksum and page version. Open Magazine in the normal signed-in browser, refresh it, inspect saved work, select cartridges, expand attempts and check native chat links. Viewing must not prepare/start/recover a workflow. Native chat pages require the human browser session; an API-key-only profile may be redirected to sign-in.

`install_idle.py` records the source-only guarded procedure for this machine, including idle rechecks, narrow backup, state/valve comparison and source rollback on installation verification failure. Its inspected Windows/Docker paths and expected previous-source hash intentionally prevent blind use on another host. It imports `live_readonly.py`; credentials are read into memory from the existing local API-key record and never printed or placed in the source package. Do not rerun installation over a newer version or overwrite its backup.

## Source-only rollback on this machine

1. Preserve any newer work. Verify an empty Magazine queue, no saved running/queued state and no active Open WebUI tasks. Do not reload a Function under active execution.
2. Use the original locally retained, qualified `install_idle.py rollback` procedure after reviewing the exact installed version and its expected source hash. The machine-specific script and private backup are not included in the public distribution.

3. The script refuses unexpected installed-source drift, restores only the previous Function source through the native update API and verifies source, current settings and current Magazine file hashes. It does not restore an old database, run checkpoint or queue snapshot.
4. Refresh Magazine and confirm the old version and saved run visibility. Previous normalized LF source hash is `acb55b3421bf5ea23510dd1b3f3372d1f19183a7bd6dec8da9940c89bea3ceb3`.

No run schema migration occurred, so source rollback preserves new evidence. The new browser preference key `magazine.ledger.v1` can remain; the old frontend ignores it. Actual rollback was not executed because installed verification passed. In an unexpected data-recovery situation, first preserve newer evidence and inspect the matched backup; do not automatically overwrite current state, delete runs/chats or recreate Docker volumes.
