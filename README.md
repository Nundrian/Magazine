# Magazine

Magazine is an Open WebUI orchestration tool for running bounded jobs in isolated, fresh model contexts. The repository contains a read-only API connectivity probe and a single fresh-chat proof. It does not implement orchestration, queues, cartridges, checkpoints, model routing, or multi-job verification.

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

## Single fresh-chat proof

With `MAGAZINE_OPEN_WEBUI_API_KEY` set in the environment or in the gitignored `.env.local`, run this once from the project directory:

```powershell
py -3 -m magazine.fresh_chat_proof
```

The base URL defaults to `http://localhost:3000`; override it with `MAGAZINE_OPEN_WEBUI_BASE_URL` if needed. Each invocation creates one new saved chat, sends one fixed test prompt to `hf.co/ggml-org/Qwen3.6-35B-A3B-GGUF:Q4_K_M`, polls the documented server task endpoint, retrieves the completed chat, and saves the raw response in `evidence/chat-<id>.json`. It requests no tools or terminal, turns off optional features and background title/tag tasks, and never retries a chat-creating request. Run it only when you intend to create a new chat.

This proof follows Open WebUI's [Server-Side Tool Calling](https://docs.openwebui.com/reference/server-side-tool-calling/) and [Backend-Controlled API Flow](https://docs.openwebui.com/reference/api-flow/) documentation.
