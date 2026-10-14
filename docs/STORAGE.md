# Storage setup

Magazine beta.5 adds two export destinations without moving or migrating
Open WebUI's existing data volume. Supported runtime: Open WebUI 0.11.3,
single Linux backend (including Docker Desktop on Windows).

| Purpose | Default under `DATA_DIR/magazine/` | Optional environment variable |
| --- | --- | --- |
| Original successfully imported workflow ZIPs | `input-zips/` | `MAGAZINE_INPUT_ZIPS_DIR` |
| Canonical saved cartridge text exports | `finished-work/` | `MAGAZINE_FINISHED_WORK_DIR` |

These are absolute **container/server** paths, not browser upload folders or
Windows paths in environment variables. They must not overlap each other,
the configured library, or `runs/`. Symbolic links in paths are refused.
Directories are created on first use. The backend must have write permissions
and filesystem support for no-follow directory access, locks, atomic no-replace
publication and file/directory synchronization. A storage error is reported,
not silently bypassed. No new dependencies or scheduled folder scanning.

## What is retained and exported

An administrator's successful library ZIP import retains the exact uploaded
bytes under a collision-resistant project-labelled filename. Validation or
duplicate-import rejection retains nothing. Copy failure leaves the imported
project available but returns a prominent warning and `input_zip.retained=false`.
Keep the original upload and correct storage; do not re-import the same project.
No ZIP is automatically imported just because it appears in `input-zips/`.
Old imports are not reconstructed or backfilled.

On COMPLETED, the Function exports the canonical saved text in completion order:

```text
finished-work/
  owner-<hashed-user-id>/
    m-<project-id-or-name>--<identity-hash>/
      run-<run-id>/
        snapshot-<revision>-completed/
          0001-m-<cartridge-id>--<identity-hash>.txt
          manifest.json
          results.zip
```

Library runs prepared in beta.5 carry a server-verified project ID and name;
legacy/direct-JSON runs use their existing name (possibly `magazine`/`Workflow`).
Full logical IDs, owner, run, status, revision, creation time, output lengths and
SHA256 fingerprints are in the manifest. Folder hashes use 128 bits to keep
Windows paths manageable; manifest content hashes use the complete SHA256.
The ZIP contains exactly the text files and manifest, not a copy of itself.
Text is original UTF-8 bytes, including empty outputs and CRLF. No files from
Development Toolkit or other tools, and no reconstructed chat JSON, are collected.

On ordinary Linux filesystems publication uses atomic no-replace directory
rename. Docker Desktop Windows bind mounts reject that primitive (EINVAL);
there Magazine reserves a new directory exclusively and links already-fsynced
files into it without overwrite, publishing `manifest.json` last. Only a
snapshot with a manifest and successful export record is confirmed complete.

Retrying the same snapshot checks identical contents and creates no duplicate.
Different existing contents, extra files or links are refused, never overwritten.
A HALTED snapshot is separate from a later completed snapshot. Editing exported
files makes a retry refuse that destination; keep edits elsewhere. Exports are
deliverable copies, **not** recovery checkpoints or execution evidence of semantic
correctness. Internal run history, state, claims, STOP records and canonical
results remain in `DATA_DIR/magazine/runs/<user-id>/<run-id>/`.

## Retrieving results and retrying an export

Open `/magazine`, select previous work and use **Check progress**. Canonical
saved results remain available, including completed cartridges of HALTED runs.
An export success shows the relative Finished Work ZIP path; failures show a
warning without changing COMPLETED/HALTED. Copy/open `results.zip` in the host
folder, or use `docker cp` from the default directory:

```text
docker cp open-webui:/app/backend/data/magazine/finished-work/. ./Finished-Work-backup
docker cp open-webui:/app/backend/data/magazine/input-zips/. ./Input-ZIPs-backup
```

Use an authenticated owner request to export a HALTED run, an older completed
run, or retry a failed export. This endpoint does not start/recover/stop anything:

```text
POST /api/magazine/runs/<run-id>/export
Authorization: Bearer <your Open WebUI token>
```

Returns 200 with `directory` and `zip`; write failure returns 503 with `FAILED`.
An unowned run returns 404, unauthenticated access 401, and READY/RUNNING 409.
GET `/api/magazine/runs/<run-id>/results` also returns the independent `export`
record alongside canonical results. The UI exposes existing text and warnings;
a new ZIP download button is deferred. HALTED exports are explicit, not automatic.

Exports run outside the execution engine. Canonical state/results are unchanged
on failure. If the backend dies just after committing COMPLETED, the automatic
export may not finish: retry this endpoint after restart. No startup auto-resume,
replanning or background retry is added. A post-publication fsync failure may
leave a complete ZIP/snapshot present but is still reported as unconfirmed;
export retry verifies the snapshot and synchronization. A process/power loss can
leave a hidden `.zip-*`/`.export-*` staging item. Nothing automatically removes
unknown files; a Windows crash during link publication can also leave an
incomplete final snapshot directory, which retries safely refuse. Preserve and
move that incomplete directory aside manually before retrying; do not delete
canonical results. Inspect leftovers after backing up and stopping
the service. Ordinary caught write failures clean their own partial staging.

## Optional Docker bind mounts

Default storage is already persistent if `/app/backend/data` is persistent.
Bind mounts make deliverables directly visible outside Docker. Add only these
entries to your **existing** service; leave its data volume and other settings:

Windows Docker Desktop recommended host layout:

```text
C:\AI_Workflows\Magazine\
    Input-ZIPs\
    Finished-Work\
```

```yaml
services:
  open-webui:
    environment:
      MAGAZINE_INPUT_ZIPS_DIR: /magazine-input-zips
      MAGAZINE_FINISHED_WORK_DIR: /magazine-finished-work
    volumes:
      # Keep your existing /app/backend/data mapping and all other mounts.
      - type: bind
        source: 'C:\AI_Workflows\Magazine\Input-ZIPs'
        target: /magazine-input-zips
      - type: bind
        source: 'C:\AI_Workflows\Magazine\Finished-Work'
        target: /magazine-finished-work
```

Linux: create dedicated directories with ownership matching the backend UID/GID,
then use `/srv/magazine/Input-ZIPs` and `/srv/magazine/Finished-Work` as the two
bind sources. Environment variables and container targets stay the same.
For example, with a verified backend UID/GID of 1000:1000:

```sh
sudo install -d -m 0700 -o 1000 -g 1000 /srv/magazine/Input-ZIPs /srv/magazine/Finished-Work
```

Do not assume that UID for your installation. Check it first, and apply local
SELinux/AppArmor policy where applicable. Do not use `chmod 777`. On Windows,
restrict NTFS access to your account, trusted administrators and Docker Desktop.
The backend creates directories/files as 0700/0600 on Linux; Windows bind-mount
ACLs govern host access. Host access bypasses Magazine authentication, so these
folders must not be public network shares. Back up both bind directories
**separately** as well as the complete Open WebUI volume, database and Function.
They may contain sensitive prompts/results. Removing a container must never
remove the named data volume. No cleanup/retention policy is automated.

## Safely upgrading the existing Windows installation

These are proposed administrator actions, not actions performed by this review.
No new custom image is necessary. Keep the installed beta.4 image and sidebar;
update only the Magazine Function to beta.5. Binary distribution HOLD is unchanged.

1. Finish or explicitly stop runs. Export the installed `magazine` Function and
   record its valves/settings. Keep the beta.4 JSON/source for rollback.
2. If no bind mounts are wanted, retain the existing container and volume.
   Replace the existing Function's code with beta.5 `magazine_ef.py`, keep ID
   `magazine` and valves, save/enable. Restart the backend only in an agreed
   maintenance window if old routes remain. Defaults need no Docker change.
3. For optional mounts, first save the current Docker configuration privately,
   stop only `open-webui`, and take a consistent full backup of the `open-webui`
   volume/database plus current bind data. Rehearse restoration using a separate
   disposable volume. Never mount the live volume in an acceptance server.
4. Prefer editing the original deployment definition: append the two mounts and
   variables, retaining **all** environment values, `WEBUI_SECRET_KEY`, image,
   network, port, restart/logging settings and existing mounts. Do not replace
   it with the example Compose file. `docker compose up -d --no-deps open-webui`
   recreates only that service. Do not use `down -v` or volume removal.

The inspected installation on 14 October 2026 is a non-Compose `open-webui`
container: beta.4 image, network `openwebui-net`, port `3000:8080`, restart
`unless-stopped`, log driver `local`, extra host `host.docker.internal:host-gateway`,
data volume `open-webui:/app/backend/data`, and an additional evidence bind.
For that exact configuration, the following PowerShell sequence preserves every
existing mount using `--volumes-from`, every environment value via a private
env file, the exact current image ID, command/user and labels. It prepares a
replacement without starting a second server against the volume. If your
configuration has changed, adjust the command to match its inspected options
before proceeding. This script does **not** perform your required backup.

```powershell
$ErrorActionPreference = 'Stop'
$old = (docker inspect open-webui | ConvertFrom-Json)[0]
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect original container' }
# This JSON/env file contains secrets: keep it private, never upload it.
$backupDir = 'C:\AI_Workflows\Magazine\Backups\before-beta5'
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
docker inspect open-webui | Set-Content -LiteralPath "$backupDir\container-private.json"
$envPath = "$backupDir\environment-private.env"
$savedEnv = @($old.Config.Env | Where-Object {
    $_ -notmatch '^MAGAZINE_(INPUT_ZIPS|FINISHED_WORK)_DIR='
}) + @('MAGAZINE_INPUT_ZIPS_DIR=/magazine-input-zips',
       'MAGAZINE_FINISHED_WORK_DIR=/magazine-finished-work')
if ($savedEnv | Where-Object { $_ -match '[\r\n]' }) { throw 'Multiline environment requires original deployment tooling' }
[IO.File]::WriteAllLines($envPath, [string[]]$savedEnv, [Text.UTF8Encoding]::new($false))
New-Item -ItemType Directory -Force -Path 'C:\AI_Workflows\Magazine\Input-ZIPs','C:\AI_Workflows\Magazine\Finished-Work' | Out-Null
$argsForDocker = @('create','--name','open-webui-beta5-prepared',
    '--env-file',$envPath,'--volumes-from','open-webui',
    '--mount','type=bind,source=C:\AI_Workflows\Magazine\Input-ZIPs,target=/magazine-input-zips',
    '--mount','type=bind,source=C:\AI_Workflows\Magazine\Finished-Work,target=/magazine-finished-work',
    '--network','openwebui-net','--publish','3000:8080',
    '--restart','unless-stopped','--log-driver','local',
    '--add-host','host.docker.internal:host-gateway','--user',$old.Config.User)
foreach ($property in $old.Config.Labels.PSObject.Properties) {
    $argsForDocker += @('--label', "$($property.Name)=$($property.Value)")
}
$argsForDocker += @($old.Image) + @($old.Config.Cmd)
docker @argsForDocker
if ($LASTEXITCODE -ne 0) { throw 'Create failed; original container is unchanged' }
# Maintenance window: stop, BACK UP the volume and bind data, verify backup.
docker stop open-webui
if ($LASTEXITCODE -ne 0) { throw 'Stop failed; do not continue' }
# STOP HERE until your consistent backup has been completed and checked.
```

Only after that backup is verified:

```powershell
docker rename open-webui open-webui-beta4-backup
if ($LASTEXITCODE -ne 0) { throw 'Original rename failed' }
docker rename open-webui-beta5-prepared open-webui
if ($LASTEXITCODE -ne 0) { throw 'Replacement rename failed; retain original backup container' }
docker start open-webui
if ($LASTEXITCODE -ne 0) { throw 'Replacement start failed; follow rollback below' }
```

Now replace the existing Function code as in step 2. Confirm authentication,
model access, old history/results and the existing sidebar. Import one new
small ZIP and verify its exact retained bytes in `Input-ZIPs`; run a bounded
workflow and verify the manifest/text/ZIP in `Finished-Work`. Check warnings
and STOP behavior before ordinary use. A backend restart does not auto-resume
work. Existing ZIPs/exports in default folders are not moved when mounts are
added; copy them separately only after inspecting and backing them up.

Rollback: stop the replacement, restore the saved beta.4 Function if it changed,
rename the stopped replacement to a distinct name, rename
`open-webui-beta4-backup` back to `open-webui`, and start it. Never start both
against the shared volume. The retained backup container is not a data snapshot:
both use the same volume. Restore the verified whole-data backup into a separate
volume if needed; no engine schema migration is introduced by beta.5. Keep the
new host folders and exported files. Do not remove the volume or historical runs.
