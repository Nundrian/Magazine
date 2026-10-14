# Magazine Event Function

`magazine_ef.py` is the versioned, self-contained Open WebUI Event Function.
It embeds the canonical `magazine/` package as a deterministic ZIP and exposes
the authenticated `/magazine` page plus `/api/magazine/*` routes.

## Install

In Open WebUI 0.11.3, sign in as an administrator, open **Admin Panel →
Functions**, import `magazine-function.json`, and enable **Magazine**. Then visit
`/magazine` on the same Open WebUI origin. Importing and enabling the Function
installs routes and creates the empty persistent project-library directory; it
does not prepare or execute a workflow.

The default callback is `http://127.0.0.1:${PORT}` (port 8080 when `PORT` is
unset), which is correct because the runner and Open WebUI backend share one
container. For a nonstandard in-process deployment, set the Function valve
`open_webui_base_url` or the container environment variable
`MAGAZINE_OPEN_WEBUI_BASE_URL` to an absolute HTTP(S) origin with no path,
query, fragment, or embedded credentials. Explicit valve values take
precedence. The value is validated before a READY run is claimed.

Persistent state is stored below Open WebUI's `DATA_DIR`:

- `magazine/runs/<authenticated-user-id>/...` for user-scoped runs and results;
- `magazine/library/...` for the shared, authenticated project catalogue.

Only administrators may import into the catalogue. Open WebUI authentication
protects every Magazine route. Dispatch tokens are held in memory for the
active request and are not written to Magazine metadata or state.

**Stop workflow** writes `run/control.json` under the existing run lock before
attempting cancellation. The active adapter uses Open WebUI 0.11.3's
owner-checked chat-task stop API and confirms the task list is empty before
recording cancellation as confirmed. Workflow termination is recorded
separately only after `state.json` is durably HALTED. Cancellation failure does
not cancel the local runner; the current result may finish and is preserved,
then the next safe boundary stops without dispatching another cartridge.
Stopped runs are terminal in this beta. Pause/Resume are not implemented.

## Rebuild and verify

From the repository root:

```text
python scripts/embed_core.py --write
python scripts/embed_core.py --check
```

`--write` regenerates the embedded core, manifest and native JSON import from
the canonical core and Function source. The JSON contains the exact Function
source and public metadata only, not credentials, owners or valve settings.
The raw Python file remains available for source review or editor/API use;
Open WebUI's native Import button accepts JSON, not a Python file.
CI and release verification use `--check` and fail if generated files differ.
