# Magazine

Magazine is a deterministic sequential executor for pre-designed, capability-sized workflow cartridges. A stronger model or a human designs the workflow in advance. Magazine runs its cartridges in list order, resolves each assigned model, sends its prompt into a fresh execution context and saves the raw result before advancing.

The harness provides its normal toolkit: browser, files, coding tools, terminals, Fresh Worker, Ledger or other installed tools. A cartridge prompt can ask the model to use them. Magazine does not provision, select or check their availability. Fresh Worker and Ledger are harness tools, not Magazine subsystems.

## Install and test

Requires Python 3.10 or later. From this project directory:

```bash
python -m pip install -e .
python -m pip install pytest
python -m pytest -q
```

The runtime dependencies are httpx for the Open WebUI adapter and PyYAML for unpacked workflow loading. The generic runner requires no Open WebUI credentials, discovery service, tool map or terminal configuration.

## Minimal workflow package

An unpacked package contains `magazine.yaml` and one prompt file per cartridge:

```yaml
schema_version: "1.0"
name: Simple workflow
cartridges:
  - id: first
    model: concise
    prompt_file: cartridges/first.md
  - id: second
    model: reasoning
    prompt_file: cartridges/second.md
```

`examples/simple` is a complete example. `concise` and `reasoning` are routing keys resolved through the caller's `model_map`; they are not feature settings. Use identity mappings if your manifest already names concrete model IDs.

The top-level fields are exactly `schema_version`, `name` and `cartridges`. Cartridge fields are exactly `id`, `model` and `prompt_file`. Unknown fields, duplicate keys, duplicate IDs, missing fields, empty workflows and empty prompts are rejected. Prompt paths must stay within the package root, including after symlink resolution, and prompt text is read verbatim. List order determines execution order. There is no runtime master-context composition.

The loader accepts unpacked folders and direct compressed `.magazine` ZIP archives of the same layout (`magazine.yaml` at the root and prompt files under `cartridges/`). Archive members are validated before extraction — empty names, absolute paths, `..` traversal, backslash names, and non-data file types are rejected — then the archive is unpacked into a private temporary workspace that is removed in all cases, including load failure.

## Generic execution

The orchestration boundary is:

```python
from magazine.async_runner import run_magazine

final_state = await run_magazine(
    magazine_path="examples/simple",
    state_dir="local-state",
    owner_id="operator",
    model_map={"concise": "model-a", "reasoning": "model-b"},
    dispatch=my_dispatch,
)
```

`my_dispatch` must be an async callable with the contract `dispatch(prompt, model_id)`. It creates a fresh isolated harness context for each call, executes the current prompt and returns the raw result. It must raise on execution or transport failure. Harness credentials and toolkit configuration belong inside that adapter, outside generic orchestration.

Use a fresh state directory for a new workflow. Do not change workflow files during an active run.

## Open WebUI adapter

The existing harness-specific wrapper accepts the credentials:

```python
import asyncio
import os
from magazine.async_runner import run_magazine_open_webui

async def main():
    return await run_magazine_open_webui(
        magazine_path="examples/simple",
        state_dir="local-state",
        owner_id="operator",
        base_url=os.environ["MAGAZINE_OPEN_WEBUI_BASE_URL"],
        api_key=os.environ["MAGAZINE_OPEN_WEBUI_API_KEY"],
        model_map={
            "concise": "YOUR_FIRST_MODEL_ID",
            "reasoning": "YOUR_SECOND_MODEL_ID",
        },
        # Optional: Open WebUI tool DB IDs already chosen by the
        # surrounding harness/operator, e.g. the Development Toolkit:
        # tool_ids=["development_toolkit"],
    )

print(asyncio.run(main()))
```

Supply real credentials at runtime. The adapter creates a distinct saved chat, user/assistant message IDs and session ID for each cartridge. Each completion contains only that cartridge's user prompt and assigned model. It explicitly requests `features.memory = false`; conversational messages and earlier results are never handed to the next cartridge. Chat title, tag and follow-up background tasks remain disabled as in the base version.

Optional `tool_ids` carries the harness-selected Open WebUI tool DB IDs (e.g. `tool_ids=["development_toolkit"]`). Magazine forwards those IDs verbatim into each completion request as `"tool_ids"`; Open WebUI resolves them into the tools available in the fresh chat. Magazine does not discover, provision, inspect or manage tools. When `tool_ids` is omitted (or empty), no tool IDs are sent and the previous behaviour is preserved. Availability of the harness's ordinary tools and enforcement of the memory flag need confirmation on the deployed Open WebUI installation; mocked request tests cannot establish server behaviour.

After each cartridge's result is durably persisted, the Open WebUI adapter issues `DELETE /api/v1/chats/{id}` to remove the temporary saved chat it created for that cartridge. This cleanup is best-effort and deliberately non-fatal: a failed deletion never invalidates the already-persisted result or corrupts run state, so the generic dispatch contract is otherwise unchanged. Each cartridge always gets a fresh chat (contexts are never handed to a later cartridge), so a run that completes normally leaves no net-new chats on the server.

## Legacy JSON compatibility

The base version's strict JSON format is unchanged:

```json
{
  "schema_version": "1.0",
  "cartridges": [
    {"id": "first", "job_type": "concise", "prompt": "Return exactly: OK"}
  ]
}
```

`job_type` resolves through `model_map`, just as YAML `model` does. This compatibility does not introduce richer YAML fields or abandoned experiments.

## State, results and recovery

State is persisted under `<state_dir>/run/state.json`, with READY, RUNNING, HALTED and COMPLETED states and revision validation. Atomic first-run admission and state transitions use short filesystem locks. No lock spans external model execution. Direct lower-level loop calls require one runner.

The exact returned string is atomically written to `<state_dir>/run/results/<safe-cartridge-id>.txt` before completion advances the workflow. Result filenames use the base's existing sanitisation; choose distinct filename-safe IDs. A failed dispatch or result write does not complete the cartridge. The inherited writes use a full crash-durable atomic-replace + fsync protocol: all bytes are written to a same-filesystem temporary file, that file is `fsync`'d, the temporary file is swapped into place with `os.replace`, and the containing directory is then `fsync`'d. On the platforms Magazine targets (file and directory fsync available), a successful return therefore means both the new contents and the directory entry naming them are on stable storage before control returns to the caller. Directory fsync is probed once per process; where it is unsupported (e.g. Windows) the protocol degrades to a documented no-op for that step — the file data is still fsync'd and the replace is still atomic, only directory-entry durability is not then guaranteed.

An interrupted RUNNING state without a lock is uncertain. Startup inspection classifies it, and `recovery_action.persist_uncertain_halt` explicitly moves it to HALTED. Recovery remains operator-driven and conservative: no automatic replay or result reconciliation is added. To resume an explicitly HALTED run, use `recovery_action.persist_recover_halted`, which acquires the run lock, transitions the HALTED state to a re-admissible READY state (completed work preserved, the interrupted cartridge moved to the front of the `todo` queue so the public runner retries it first, `current` cleared to `None`, and `revision` incremented by one), and persists the result under the lock. The operator then runs the existing async runner (`magazine.async_runner.run_magazine`) or Open WebUI wrapper, which re-admits the READY run and continues execution from the interrupted cartridge. A HALTED state with no current cartridge cannot be recovered (there is no interrupted work position to retry).

`python -m magazine.entrypoint` prepares a run; it does not execute it. Use the async runner or Open WebUI wrapper for execution.

## Outside Magazine v1

Generic input delivery, automatic file-to-prompt injection, inter-cartridge artefact passing, output verification, model evaluation, adaptive routing, tool provisioning and special Ledger/Fresh Worker integration are outside this product. They are not unfinished Magazine requirements. Workflow designers prepare self-contained prompts before the runner starts.
