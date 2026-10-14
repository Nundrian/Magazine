# Native Function installation

Magazine beta.5 supports Open WebUI 0.11.3. Other versions need testing.
This package distributes Magazine source/native JSON and no Open WebUI image.

## Install and models

Use administrator access, authentication and persistent DATA_DIR storage.
Back up the complete data volume and export the current Function before updates.
Functions execute Python with server privileges; review `magazine_ef.py`.

1. Extract `Magazine-0.1.0-beta.5-FunctionOnly.zip`.
2. Open **Admin Panel → Functions → Create menu → Import JSON**.
3. Select `integrations/open_webui/function/magazine-function.json`, review,
   import and enable **Magazine**.
4. Open your server's `/magazine`, for example
   `http://localhost:3000/magazine`, and bookmark it.

Open WebUI installs HTTPX 0.28.1/PyYAML 6.0.3 if needed. Activation creates an
empty library/routes, not a run. Stock Open WebUI has no tested permanent
custom sidebar link; retain your existing image and use the bookmark.

Configure Ollama or an OpenAI-compatible provider in native connection settings.
Confirm the model works in an ordinary chat. Grant normal users native model
read permissions. Magazine lists accessible models, validates all routing keys
and leaves tool permission enforcement to Open WebUI.

Import a workflow ZIP as administrator, select it in the run builder, map
models/tools and prepare a READY run. Start explicitly and inspect results.

## Storage and configuration

See [Storage setup](STORAGE.md) for dedicated Input ZIPs/Finished Work defaults,
optional Windows/Linux bind mounts, permissions, backups, export retrieval and
the exact proposed upgrade sequence for the inspected Windows installation.
No image upgrade or migration is needed for beta.5.

`DATA_DIR/magazine/runs/<user-id>/` holds runs/results;
`DATA_DIR/magazine/library/` holds the shared catalogue.
Persist the complete data directory. The default callback is server-local
`http://127.0.0.1:${PORT}`, default 8080. Docker host ports do not change it.
Consult the Function README for nonstandard callback configuration.
Expose `/magazine` and `/api/magazine/*` at the proxy origin root.
Multi-process/Redis, other architectures and proxy path prefixes are unaccepted.

## Update, backup and rollback

Finish or stop active runs. Export the installed Function and take a consistent
whole-data backup including the database. Stop only your target service while
copying its filesystem, then rehearse restoration into a disposable volume.

Review compatibility and hashes. Replace the existing `magazine` Function
using the editor with the reviewed matching source, or import JSON after
exporting the previous record. Keep ID `magazine`, enable it, and restart
your backend if routes still reflect old code. Inspect retained runs/results.
Updating Magazine alone does not require a new Open WebUI image.

Rollback pairs the previous Function with its supported Open WebUI version.
If data changed or database migrations occurred, restore the complete prior
backup into a separate volume and pair it with the prior Function/image.
An image-only downgrade does not reverse database migrations.

## Remove

Disable/delete the `magazine` Function and restart your backend to unload routes.
Retain `DATA_DIR/magazine` for reversible removal; ordinary accounts/chats
remain. Reimport to restore access. A stock SPA can return HTTP 200 after
removal, so inspect page content and API absence. Deleting Magazine data is a
separate irreversible action requiring a verified backup and is not automated.
