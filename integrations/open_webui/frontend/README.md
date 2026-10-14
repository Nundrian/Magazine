# Magazine launcher image

This optional local-review image derives from the official Open WebUI 0.11.3 runtime with a sidebar patch and narrowly pinned frontend parser corrections. Binary distribution remains HOLD; see the repository security and licensing notes. It adds a permanent **Magazine** link to expanded/collapsed sidebars. The link is root-relative (`/magazine`), uses normal browser navigation with `target="_blank"`, and has no workflow or API handler.

The image does not contain or install the Magazine Function. Import `../function/magazine-function.json` in **Admin Panel → Functions** and enable it first; without that Function, `/magazine` has no route.

Build from the repository root:

```text
docker build -f integrations/open_webui/frontend/Dockerfile -t magazine-open-webui:0.1.0-beta.5 integrations/open_webui/frontend
```

The Dockerfile pins Open WebUI source commit `2a960a59fe1dbbd35282f0556b3666d81102e781`, the v0.11.3 multi-platform runtime manifest, and the Node builder manifest. Rebase and revalidate the patch for every Open WebUI version; it is intentionally not claimed compatible with other versions.

Open WebUI retains its own license and branding. See `licenses/` and the repository licensing notes before redistribution.
