# Install Magazine for Pi Web UI

This guide is for people who **already have Pi Web UI running** and want to add Magazine. You do not need Open WebUI or ASTER.

**Status:** The 0.1.0-pi.3-rc.1 package is being prepared for public release. The commands below apply once the plugin ZIP appears in [GitHub Releases](https://github.com/Nundrian/Magazine/releases). Do not substitute a ZIP from an unknown source.

[Back to Magazine for Pi](README.md)

## Before you begin

You'll need:

- **Pi Web UI 0.96.1** from [xing-shuyin/pi-web-ui](https://github.com/xing-shuyin/pi-web-ui).
- **Pi Coding Agent SDK 1.0.2**, **Node.js 24**, and **Python 3.11**, installed in the Linux environment where Pi runs.
- A working model already configured in Pi.
- Access to the Pi server or Docker container and its persistent data directory.
- A dedicated, writable workspace for Magazine workers.

**Important:** These exact versions form the accepted target. This guide does not certify newer or older host versions. Back up Pi and wait until any Magazine run finishes before installing or updating.

### Which setup do you have?

| Your setup | What to do |
| --- | --- |
| Pi runs directly on Linux | Follow **Install on an existing Linux Pi server** below. |
| Pi runs inside Docker on Windows or Linux | Follow the same steps **inside the Linux container**, and read the Docker notes before changing anything. |
| You use Open WebUI, not Pi | Follow the [Open WebUI guide](../README.md) instead. |

## 1. Download and check the release

When the Pi package is available, download the **plugin ZIP** from the repository's [Releases](https://github.com/Nundrian/Magazine/releases) page. **Choose an asset explicitly named Magazine-Pi; the Open WebUI packages are not interchangeable.** The matching source ZIP is for review and rebuilding, not a substitute for the plugin installer.

For the approved candidate, the expected SHA-256 values are:

```text
597099b606756e5a24f7b23f9b5e7995f864164c93e43a6eca2f235883db3393  Magazine-Pi-0.1.0-pi.3-rc.1-plugin.zip
efd56e6918f7736368928fe7c3985b265ec91347009512086e212a429bada055  Magazine-Pi-0.1.0-pi.3-rc.1-source.zip
```

Check the downloaded file's hash against the **published** checksum file as well. On Linux:

```sh
sha256sum Magazine-Pi-0.1.0-pi.3-rc.1-plugin.zip
```

If it differs, **stop**. Do not install that file.

## 2. Back up your current Pi installation

Before making changes, make a recoverable backup of:

- Pi's persistent data directory, including existing plugins, their configuration and sessions.
- The Pi agent's model/provider settings.
- The existing authentication configuration, stored **privately**.
- Any earlier Magazine runs and owner keys.

If Pi is in Docker, **preserve the existing volumes**. Do not run `docker compose down -v`. A Docker image or container recreation is not a backup.

For safety, begin with **no tools authorised** for Magazine workers.

## 3. Install on an existing Linux Pi server

Run these commands **where the Pi Web UI CLI and Python are installed**. For Docker, that is normally *inside the compatible Linux image/container*, not in Windows PowerShell.

The paths below are **examples**; replace them with real, persistent Linux paths on your server.

```sh
# Extract the verified plugin package.
unzip Magazine-Pi-0.1.0-pi.3-rc.1-plugin.zip -d /opt/magazine-source

# Create a Python environment containing Magazine's dependencies.
/usr/bin/python3 -m venv /opt/magazine-venv
/opt/magazine-venv/bin/pip install -r /opt/magazine-source/magazine-pi/requirements.txt

# Add the plugin to Pi's existing data directory.
pi-web-ui install /opt/magazine-source/magazine-pi --data-dir /path/to/pi-data

# Create an editable configuration from the supplied template.
cp /opt/magazine-source/magazine-pi/config.example.json /path/to/pi-data/plugins/magazine-pi/config.json
```

Edit that final `config.json` file to point at your actual Pi agent configuration, worker workspace and Python environment. A starting example:

```json
{
  "workspace": "/work/magazine-projects",
  "agentDir": "/path/to/pi-agent",
  "python": "/opt/magazine-venv/bin/python",
  "timeoutSeconds": 1200,
  "allowedTools": [],
  "workerExtensionPaths": []
}
```

Create the workspace and ensure the Pi process can write to it. **Don't use a protected source-code project as the worker workspace.** The `agentDir` setting points to your existing Pi model/provider configuration; it does not create a new account.

The Pi data directory and shared Magazine library need a **Linux filesystem supporting atomic file operations**. An ordinary Windows NTFS bind mount is not a supported replacement for that shared library.

If your installation places the Pi SDK in a nonstandard location, consult the packaged documentation for `MAGAZINE_PI_SDK`.

## 4. Special notes for Docker

Before updating the container, verify:

- The Pi data directory and Magazine data use a **persistent Linux volume**.
- The agent directory and model settings remain mounted/configured exactly as before.
- Python and Magazine's Python dependencies will survive a container recreation. Put them in the pinned image or another compatible persistent Linux environment.
- Only the intended Pi service is restarted. Don't remove volumes or restart unrelated Open WebUI services.

**Why this matters:** Installing Python packages into a throwaway container can appear to work today and disappear on the next container recreation.

The packaged `deployment/` example is illustrative; it does **not** replace your own existing Compose configuration. Back up first and review all mounts, ports and authentication before adapting it.

## 5. Open Magazine in Pi

1. Open your usual Pi Web UI address in a normal browser.
2. If Pi has token authentication enabled and you aren't signed in, use **Pi's existing native sign-in process** with your private token. Never share or publish a URL containing the token.
3. In Pi, choose **Plugins → Magazine**. On a narrow display, it may be in the overflow menu.
4. Expand **Your owner key** and keep the key somewhere private before switching browser profiles or clearing site data.

Magazine uses the Pi login you already have. It doesn't add a second host login. Persistent sign-in depends on your browser retaining Pi's cookie; privacy settings that delete cookies on exit will require sign-in again.

## 6. Run your first workflow

In Magazine you'll see three numbered sections:

1. **Load your workflow.** Choose one from the shared library. An administrator can import a trusted Magazine project ZIP; you can also use a Magazine JSON document.
2. **Choose models and tools.** Select a configured model. Leave tools disabled unless the task genuinely needs them.
3. **Execute and review.** Select **Prepare workflow** to create a **READY** run. Check the selected workflow, then choose **Start workflow** to begin inference. Use **Check progress**, **View previous work**, and **Download results ZIP** to inspect the saved results.

**Preparing is not starting.** No inference begins until you explicitly select Start.

If the shared library is empty, an administrator must first import a project ZIP. Magazine's administrator import key is stored privately under the Pi data directory at `magazine/import-key`; handle it like a password and don't publish it.

## 7. Understand Stop and recovery

- **Stop workflow** permanently stops that run's progression at a safe boundary. It isn't Pause, and it cannot simply be resumed.
- If a cartridge fails, an **eligible** halted run may be explicitly prepared for recovery to READY. Starting it again is a separate action.
- Saved responses can be reviewed or exported. If an export fails, use the provided retry control.
- **COMPLETED** means Magazine finished execution and saved the replies; you must still check whether the answers are correct.

The response from one cartridge is **not automatically included** in the next cartridge's prompt.

## 8. Update Magazine later

When a newer **compatible, verified** release is available, first stop or finish active workflows, then back up Pi data, the current plugin directory, and `config.json`.

After extracting the new ZIP, use the Pi Web UI installer’s documented replacement mode:

```sh
pi-web-ui install /path/to/new-source/magazine-pi --data-dir /path/to/pi-data --force
```

Confirm your original `config.json` was preserved and review any new settings without broadening tool permissions. Keep the same Pi data volume, models, owner keys and authentication token.

**Rollback:** Reinstall the previously backed-up plugin version, restore its configuration and retain the complete backed-up Magazine data. Check version compatibility before rolling back. Never delete the live data volume as part of rollback.

## 9. Remove Magazine

The host CLI supports removing the plugin:

```sh
pi-web-ui uninstall magazine-pi
```

Run the uninstall against the intended Pi installation (check `pi-web-ui uninstall --help` if you use a nondefault data directory).

Removing the plugin does **not** automatically delete all separately stored Magazine run history. Keep backups until you've confirmed you no longer need the results or owner keys.

## Security and common problems

| Problem or question | What to check |
| --- | --- |
| **Pi says `PI_WEB_TOKEN required`** | The ordinary URL lacks a valid saved sign-in. Use Pi's native sign-in again and ensure your browser isn't deleting site cookies on exit. Do not turn off authentication as a workaround. |
| **No projects in the library** | An administrator must import a trusted project ZIP, or use a valid Magazine JSON document. |
| **No model available** | First verify that Pi itself can use a model and that the correct Pi agent directory is configured. |
| **Magazine disappears after Docker recreation** | Plugin files, Pi data and Python dependencies must live in the persistent installation/image as appropriate. |
| **Can't run tools** | Start with `allowedTools: []`. Only add tools you've deliberately authorised and whose filesystem/network access you understand. |
| **Lost access to previous runs** | Recover the original private Magazine owner key or restore the relevant backup. Browser profiles do not automatically share ownership. |

**Security warning:** Magazine workers can execute selected tools with the permissions of the Pi server process, and they do not automatically inherit the normal chat's permission approval settings. Treat imported prompts as potentially powerful instructions; use a restricted workspace and minimal tools.

Never publish tokens, owner keys, model API credentials, administrator import keys, personal configuration files or token-bearing URLs in issues or screenshots. For remote access, use appropriately protected HTTPS and review exposure of the host.

---

Need help? [Open an issue](https://github.com/Nundrian/Magazine/issues) and include the Pi version, Magazine version, operating environment, what you tried, and **redacted** error messages. Don't paste secrets or private project data.
