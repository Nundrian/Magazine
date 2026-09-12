# Magazine

Magazine is an Open WebUI orchestration tool for running bounded jobs in isolated, fresh model contexts. This initial repository contains only a read-only API connectivity probe. It does not yet implement orchestration, queues, cartridges, checkpoints, model routing, chat creation, or verification.

## Run the probe

Python 3.10 or newer is required. The probe uses only the Python standard library, so installation is optional. From `C:\Codex\Magazine`, set the base URL and API key in your shell, then run:

```powershell
$env:MAGAZINE_OPEN_WEBUI_BASE_URL = "http://localhost:3000"
$env:MAGAZINE_OPEN_WEBUI_API_KEY = "<your-api-key>"
py -3 -m magazine.probe
```

The values above are examples. Use your instance URL and a key created in Open WebUI. Environment variables keep the key out of source code. Do not commit keys or local `.env` files.

The probe makes three read-only requests:

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | Check that the Open WebUI service responds. |
| `GET /api/models` | List models visible to the API key. |
| `GET /api/v1/tools/` | List Workspace tools visible to the API key. |

It prints the result of each request, distinguishes service health from authenticated API connectivity, and exits with `0` only when all three checks succeed. Exit code `1` means at least one request or response failed; `2` means configuration is missing or invalid. A `404`, `401`, or unexpected response from a particular installed version is reported as-is; the probe does not try alternate endpoints.

The paths come from the Open WebUI [Monitoring](https://docs.openwebui.com/reference/monitoring/) and [Server-Side Tool Calling](https://docs.openwebui.com/reference/server-side-tool-calling/) documentation. API keys use the documented Bearer token header for authenticated requests.
