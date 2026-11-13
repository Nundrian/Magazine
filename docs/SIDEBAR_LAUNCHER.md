# Magazine Simple Sidebar Launcher 0.1.0

**Source provided by maintainer:** 13 November 2026.  
**Required host version declared in Function metadata:** Open WebUI **0.12.5**.  
**Related runner:** locally installed Magazine Simple **0.2.0-exec.5** at `/magazine-simple`.

[Back to the README](../README.md) · [Source file](../integrations/open_webui/function/magazine_simple_sidebar_launcher.py) · [Visual interface guide](guide/index.html)

## What it does

This is a **separate Open WebUI Function for navigation**. It inserts a Magazine link into Open WebUI's sidebar using the existing static loader. The link opens `/magazine-simple` in another browser tab (`target="_blank"`, `rel="noopener noreferrer"`). It is not the execution engine and does not trigger any Magazine workflow.

The embedded JavaScript is labelled **"No tokens, API calls or workflow actions"**. It locates an existing sidebar anchor, clones its surrounding classes, draws a book-like SVG icon and inserts the new link. It watches sidebar DOM changes to remount after rerenders and exposes a small `refresh`/`destroy` browser helper keyed by `__magazineSimpleSidebarLauncher`.

### Important differences from earlier versions

| Integration | Route | Status |
| --- | --- | --- |
| **Magazine Simple runner** | `/magazine-simple` | Installed locally as 0.2.0-exec.5; source not included in the public beta.5 installer |
| **Magazine Simple Sidebar Launcher 0.1.0** | Adds a link to `/magazine-simple` | Supplied source in this repository; separate from the runner |
| **Older beta.5 Function** | `/magazine` | Public downloadable legacy build, accepted for Open WebUI 0.11.3 |
| **Older patched-Docker sidebar** | `/magazine` | Historical, independent integration; custom image distribution remains on hold |

The Simple Launcher is **not an alternative installation of Magazine itself**. You must already have an appropriate runner installed for its link to work.

## How the supplied Python Function publishes the link

1. Imports `STATIC_DIR` and `DATA_DIR` from Open WebUI's configuration when triggered.
2. Reads `STATIC_DIR/loader.js` (or treats it as empty if absent); rejects a symlink target.
3. Removes an existing uniquely marked launcher block if present; rejects inconsistent or duplicate block markers.
4. Adds or removes only the uniquely marked launcher JavaScript block.
5. Before changing it, saves a content-addressed copy of the previous loader in `DATA_DIR/magazine-simple/sidebar-launcher/`.
6. Writes a temporary file in the loader directory, flushes, fsyncs and replaces `loader.js`; stores installation hashes and metadata in `installation.json`.
7. Uses the Function `Event` hook to attempt initial publication and to remove the block when its own Function is disabled or deleted.

The marked block is delimited by:

```text
// BEGIN MAGAZINE SIMPLE SIDEBAR LAUNCHER 0.1.0
...
// END MAGAZINE SIMPLE SIDEBAR LAUNCHER 0.1.0
```

## Risks and limits

- **Server-side modification:** This Function edits Open WebUI's static `loader.js`. Review its Python and JavaScript before enabling it, back up the host and use it only on a version/deployment where the static loader mechanism is known to work.
- **Writable static directory required:** `STATIC_DIR` must permit atomic file replacement. In read-only or ephemeral deployments the injection may fail or disappear after image updates. A failed publication needs log inspection.
- **Host/version dependence:** The source metadata requires Open WebUI 0.12.5. DOM anchors and the Function lifecycle may change in other Open WebUI versions.
- **Not a permissions bypass:** Navigation does not grant access to the runner, models, tools, accounts or stored runs.
- **Disabling is not a guarantee of rollback in every failure mode:** The code attempts to remove its marked block on recognised disable/delete events; an interrupted lifecycle or changed host implementation may require inspection and restoration from the saved loader backup.
- **No new live tests claimed here:** The maintainer has provided the installed launcher source and reports a working local version. This documentation update did not re-run installation, disable/uninstall or cross-browser tests.

## Source and verification

The [tracked source file](../integrations/open_webui/function/magazine_simple_sidebar_launcher.py) contains the Python Function and its embedded JavaScript exactly as supplied in this documentation update. It is not part of the older beta.5 package, and should not be confused with the legacy Docker sidebar files under `integrations/open_webui/frontend/`.

Before attempting to publish on another installation, confirm that the target page `/magazine-simple` already exists, the loader is writable, the Function events are supported, and the host has a tested rollback procedure.
