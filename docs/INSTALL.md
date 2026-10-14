# Install Magazine public beta

Magazine beta `0.1.0-beta.5` supports Open WebUI `0.11.3` only. Back up the
Open WebUI data volume and export existing Functions before changing an
installation. Never test an upgrade against the only copy of production data.

## Option A: standard Open WebUI

Use this compatibility option when replacing the Open WebUI image is not
acceptable.

1. Confirm the server is Open WebUI 0.11.3.
2. Sign in as an administrator and open **Admin Panel → Functions**.
3. Import `integrations/open_webui/function/magazine-function.json` and enable
   **Magazine**.
4. Open `https://your-open-webui.example/magazine` directly and sign in if
   required.

This installs the complete Magazine interface and runner, but unmodified Open
WebUI 0.11.3 has no supported extension point for a permanent arbitrary
sidebar link. This option therefore does **not** provide the Magazine sidebar
button.

## Option B: Magazine-enabled Open WebUI (local review; distribution HOLD)

The Function-only package is the independently prepared installation option;
see `FUNCTION_INSTALL.md`. This optional build requires the separate full source
archive. Its parser corrections are under acceptance, and binary redistribution
remains blocked by remaining runtime review and matching notices/sources. Do not
publish the image or deploy it to an existing installation without approval.

This is the reproducible option that meets the launcher requirement. The
image is Open WebUI 0.11.3 with the sidebar and narrow parser security patches. The
Magazine Function is still imported separately, so frontend customization and
runtime state remain independent.

Build locally:

```text
docker build --file integrations/open_webui/frontend/Dockerfile --tag magazine-open-webui:0.1.0-beta.5 integrations/open_webui/frontend
```

For a new installation, copy `integrations/open_webui/frontend/compose.example.yaml`
to `compose.yaml` in that same frontend directory. Create a private `.env`
beside it containing `WEBUI_SECRET_KEY=<a new long random secret>`; do not commit
that file. Review the host port, model-server address and volume name, then run
`docker compose up -d` from the frontend directory. Compose refuses to start
without the secret. Import and enable the Function as described in Option A.

For an existing installation, retain its exact environment, ports and
`/app/backend/data` volume mapping and change only the image to
`magazine-open-webui:0.1.0-beta.5`. Do not copy the example Compose file over
an existing deployment. Recreate that container only after taking a backup.

The sidebar's **Magazine** anchor is `href="/magazine"`, `target="_blank"`,
and `rel="noopener noreferrer"`. A normal click leaves the conversation tab
untouched and opens the standalone authenticated page in a new full tab. The
click performs no API request and cannot prepare or start a run.

## Configuration

### Storage setup

Original imported ZIPs default to `DATA_DIR/magazine/input-zips/`; canonical
finished text exports and their ZIPs to `DATA_DIR/magazine/finished-work/`.
These are deliverables, not internal history/checkpoints in `runs/`.
See [storage setup](STORAGE.md) for optional bind mounts, Windows/Linux examples,
permissions, backups, retrieval and preserving an existing Docker installation.

No callback host is hard-coded. In the supported single-container layout the
Function calls the backend at `http://127.0.0.1:${PORT}`. See the Function
README before changing the `open_webui_base_url` valve.

The custom image does not contain credentials, a Function database record, or
Magazine data. Open WebUI stores the imported Function, accounts, configuration
and `DATA_DIR/magazine` in the existing persistent data volume.

For normal users, configure model read access through Open WebUI's native model
permissions. Magazine shows only models that the account can access; importing
the Function does not grant access to an external model. Keep the configured
data directory mounted persistently. A changed backend `PORT` is supported by
the default container-local callback; it does not require the development host
port, a special Docker network, or a separate Magazine service.

## Stop behavior

**Stop workflow** is available only for an admitted execution. The request is
durably recorded before Magazine attempts Open WebUI task cancellation. A
confirmed Stop prevents later cartridge dispatch and preserves every completed
result. If Open WebUI cannot confirm active-task cancellation, Magazine says
`stop_requested`, not `stopped`; the current step may still finish, after which
the safe boundary halts the run. After a backend process restart, Magazine can
halt an abandoned run but cannot prove that a previously remote inference was
cancelled, so cancellation remains unconfirmed. A stopped run cannot be
recovered with **Prepare to continue**. Pause/Resume are deferred for this beta.

## Backup

At minimum, preserve all of `/app/backend/data`, not only the Magazine
subdirectory. That captures Open WebUI's database, the imported Function and
Magazine data together. Use the backup procedure already established for the
deployment; for a filesystem or volume copy, stop only the target container
first so SQLite and files are consistent. Also export the Magazine Function
from **Admin Panel → Functions** as a convenient independent copy. Verify the
backup by restoring it into a disposable volume.

## Upgrade

1. Read the Magazine release notes and confirm the supported Open WebUI base.
2. Back up and verify the current data volume.
3. Build the new image under a new immutable tag.
4. Run `scripts/acceptance_open_webui.py --image <new-tag>`; it creates and
   deletes only uniquely named, labelled test resources.
5. Recreate the target Open WebUI container with the new image while retaining
   its volume and settings.
6. Import/update the matching Function, confirm it is enabled, open Magazine,
   and inspect existing runs without starting them.

Do not upgrade Open WebUI independently of Magazine's pinned supported
version. Open WebUI database migrations may make an image downgrade unsafe.

## Rollback

If the new container has not migrated or changed persistent data, recreate it
with the previous image tag and the same volume. If it may have migrated the
database, restore the pre-upgrade volume backup and use the matching prior
image and Function. A frontend-only fault can be rolled back to the official
Open WebUI 0.11.3 image; `/magazine` remains usable if the Function is active.

## Uninstall

1. Disable, then delete, the Magazine Function in **Admin Panel → Functions**.
2. Replace the custom image with the official pinned Open WebUI image (or the
   deployment's previously verified image), retaining the data volume.
3. Leave `DATA_DIR/magazine` in place for a reversible uninstall.
4. Only after a verified backup, an administrator may separately delete the
   exact `DATA_DIR/magazine` directory to remove all Magazine projects, runs
   and results. This final deletion is irreversible and is intentionally not
   automated by the package.
