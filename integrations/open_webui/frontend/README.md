# Magazine launcher image

> **This is the legacy Docker-image sidebar, not the newer standalone launcher.** It rebuilds the Open WebUI **0.11.3** frontend with a patch; it does not install the Magazine Function. The maintainer also has a separate **Magazine Sidebar Launcher 0.1.0 Event Function**, tested locally with Open WebUI **0.12.5**, which adds a sidebar link without replacing the Docker image. That newer Function is **not yet published here**. Both approaches are retained, but the Docker image is on a security/licensing **distribution HOLD** and has not been validated for 0.12.5. See the [comparison in the main README](../../../README.md#open-webui-sidebar-two-different-implementations).

This optional local-review image derives from the official Open WebUI 0.11.3 runtime with a sidebar patch and narrowly pinned frontend parser corrections. Binary distribution remains HOLD; see the repository security and licensing notes. It adds a permanent **Magazine** link to expanded/collapsed sidebars. The link is root-relative (`/magazine`), uses normal browser navigation with `target="_blank"`, and has no workflow or API handler.

The image does not contain or install the Magazine Function. Import `../function/magazine-function.json` in **Admin Panel → Functions** and enable it first; without that Function, `/magazine` has no route.

Build from the repository root:

```text
docker build -f integrations/open_webui/frontend/Dockerfile -t magazine-open-webui:0.1.0-beta.5 integrations/open_webui/frontend
```

The Dockerfile pins Open WebUI source commit `2a960a59fe1dbbd35282f0556b3666d81102e781`, the v0.11.3 multi-platform runtime manifest, and the Node builder manifest. Rebase and revalidate the patch for every Open WebUI version; it is intentionally not claimed compatible with other versions.

Open WebUI retains its own license and branding. See `licenses/` and the repository licensing notes before redistribution.
