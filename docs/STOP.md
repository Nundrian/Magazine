# Workflow Stop design and Open WebUI 0.11.3 compatibility

This implementation is pinned to Open WebUI source commit
`2a960a59fe1dbbd35282f0556b3666d81102e781` (release 0.11.3).

## Verified Open WebUI cancellation path

Magazine's completion request includes both `chat_id` and `session_id` and
receives Open WebUI task IDs. Open WebUI 0.11.3 exposes these authenticated,
chat-owner-checked endpoints in `backend/open_webui/main.py`:

- `GET /api/tasks/chat/{chat_id}` lists active task IDs only when the saved
  chat belongs to the authenticated user (administrators are also accepted by
  Open WebUI).
- `POST /api/tasks/chat/{chat_id}/stop` applies the same ownership check and
  calls `stop_item_tasks`.

In `backend/open_webui/tasks.py`, the normal single-process path cancels each
registered asyncio task and awaits its cancellation before returning success.
The Redis/multi-worker path publishes a stop command and removes the shared
task-registry entry without independently waiting for an upstream model
provider. Magazine therefore confirms exactly what this supported Open WebUI
API can prove: an acknowledged stop response followed by an empty owner-scoped
task list. It does not claim independent provider-level cancellation in a
distributed deployment.

## Durable Magazine behavior

The existing five-field `state.json` schema is unchanged. A strict
`run/control.json` sidecar records:

- the durable `STOP` request and request timestamp;
- cancellation status (`NOT_ATTEMPTED`, `CONFIRMED`, or `FAILED`) and detail;
- actual workflow termination status, method, and timestamp.

All changes use the existing run lock and execution-attempt/runtime fence. The
request is saved before calling Open WebUI. The runner checks for it under the
same fence before dispatch and after each successful cartridge is checkpointed.
At a safe boundary, `state.json` is durably changed to `HALTED`, the execution
claim is retired, and only then is actual termination marked confirmed.

If active cancellation is confirmed, the local Magazine task is cancelled and
the same fenced HALT is completed before the API says `stopped`. If cancellation
fails or times out, the local task is not abandoned: the current cartridge may
finish and its output may be checkpointed, then Stop is applied before any
later cartridge. Repeated requests are idempotent and do not duplicate a
failed or in-progress cancellation request.

After a process restart, a previously durable request is retained. Once the
old runtime is positively extinct, the new runtime can HALT the abandoned run;
active inference cancellation remains unconfirmed. A run with a Stop request
cannot use ordinary HALTED recovery, preventing replay through that path.

## Terminal races and outcome reporting

An owner requesting Stop after an independent failure has already left the
workflow HALTED receives HTTP 409 `already_halted`. No new Stop sidecar is
created, and the original failure and durable records remain unchanged.
This refusal does not alter repeated confirmed Stop or `NOT_APPLIED` handling.

If the last cartridge finishes after Stop was requested, the run remains
`COMPLETED` with all results preserved. The control sidecar records
`termination_status: NOT_APPLIED` and `termination_method: workflow-completed`;
Magazine never reports this as a successful Stop. If execution fails after a
Stop request but before its application, the original error HALTs the run and
Stop is likewise recorded as `NOT_APPLIED` with `execution-failed` (or
`task-cancelled-without-confirmed-stop`). Confirmed task cancellation followed
by task cancellation remains `REQUESTED` until the Stop endpoint confirms the
HALTED state. These values change only control.json; state.json remains frozen.

## Pause and Resume

Pause/Resume are deferred from this beta. A safe implementation needs a new
durable non-terminal paused lifecycle, explicit claim retirement/re-admission,
and UI/API semantics that cannot replay completed cartridges or bypass Stop and
lost-runtime safeguards. Reusing HALTED recovery would conflate operator Stop,
failure recovery, and pause, so it was not added as a shortcut.
