# Magazine

Magazine is a Python orchestration core for running an ordered set of bounded **cartridges** one at a time with explicit persisted run state, guarded state transitions, atomic single-runner admission, and conservative recovery behaviour.

The orchestration, persistence, and concurrency core is implemented and tested. The project is **not yet Pi-native end to end**: the active runtime-dispatch path still contains legacy Open WebUI discovery, model/tool resolution, and preflight assumptions. Those runtime components are retained for now because they contain previously proven behaviour, but Open WebUI is no longer the intended future runtime architecture.

## Current status

The current Stage 6 working-tree baseline provides:

- strict Magazine JSON validation;
- explicit `READY`, `RUNNING`, `COMPLETED`, and `HALTED` run states;
- persisted run position through `done`, `current`, `todo`, `status`, and `revision`;
- atomic state-file replacement;
- short filesystem locks around authoritative persisted-state transitions;
- guarded `READY -> RUNNING`, `RUNNING -> READY`, and `RUNNING -> COMPLETED` transitions;
- an explicit conservative `RUNNING -> HALTED` recovery transition;
- single-cartridge orchestration;
- a bounded multi-cartridge run loop;
- atomic first-run admission;
- shared run preparation;
- the async `run_magazine(...)` public runner;
- duplicate-dispatch protection for normal callers using that public runner.

Current validated test baseline:

```text
410 passed
0 warnings
```

## Magazine document format

Magazine files use schema version `1.0` and contain an ordered, non-empty list of cartridges.

```json
{
  "schema_version": "1.0",
  "cartridges": [
    {
      "id": "cart-a",
      "job_type": "example",
      "prompt": "Perform one bounded task."
    },
    {
      "id": "cart-b",
      "job_type": "example",
      "prompt": "Perform the next bounded task."
    }
  ]
}
```

The schema is intentionally strict:

- the top level contains exactly `schema_version` and `cartridges`;
- each cartridge contains exactly `id`, `job_type`, and `prompt`;
- those cartridge fields must be non-empty strings;
- cartridge IDs must be unique;
- duplicate JSON keys and non-standard numeric values such as `NaN` or `Infinity` are rejected.

## Persisted run state

A run persists its authoritative position in `run/state.json`. A typical active state looks like:

```json
{
  "done": [],
  "current": "cart-a",
  "todo": ["cart-b"],
  "status": "RUNNING",
  "revision": 1
}
```

The state fields are:

| Field | Meaning |
| --- | --- |
| `done` | Cartridge IDs completed successfully, in completion order. |
| `current` | The cartridge currently admitted for execution, or `null`. |
| `todo` | Remaining cartridge IDs in Magazine order. |
| `status` | `READY`, `RUNNING`, `COMPLETED`, or `HALTED`. |
| `revision` | Non-negative integer revision of the persisted work position. |

A new `READY` state starts at revision `0`. Beginning a cartridge increments the revision by one, and completing a cartridge increments it by one again. The conservative `RUNNING -> HALTED` recovery transition preserves the revision because it preserves the work position.

## State transitions

Normal execution uses these explicit transitions:

```text
READY
  -> RUNNING
  -> READY       when more cartridges remain
  -> RUNNING
  -> ...
  -> COMPLETED   after the final cartridge
```

An uncertain interrupted run is not silently resumed. The explicit recovery transition is:

```text
RUNNING -> HALTED
```

That transition preserves `done`, `current`, `todo`, and `revision` so the uncertain work position is not guessed or rewritten.

## Persistence and locking

Magazine uses atomic state-file replacement and a short filesystem run lock created with exclusive creation (`O_EXCL`).

The intended persisted-transition sequence is:

```text
acquire short run lock
-> load authoritative persisted state
-> validate
-> perform one state transition
-> atomically persist
-> release run lock
-> external work
```

The run lock is deliberately **not** held across external model execution. Magazine does not currently add leases, heartbeats, polling, lock stealing, automatic stale-lock deletion, retries, or silent recovery.

## Atomic run admission

`persist_admit_run(...)` provides the first-run admission boundary. It acquires the run lock before checking whether persisted state exists, closing the original first-run time-of-check/time-of-use race.

While holding that one short lock it:

1. creates a fresh `READY` state if no state file exists;
2. loads the authoritative persisted state;
3. transitions `READY -> RUNNING` and persists it;
4. returns `COMPLETED` or `HALTED` unchanged;
5. rejects an already `RUNNING` run with `RunAdmissionError`;
6. releases the lock before any external dispatch occurs.

If another caller arrives while the admission lock itself is still held, lock acquisition fails with `MagazineLockError`. If it arrives after admission has completed and the persisted state is already `RUNNING`, admission fails with `RunAdmissionError` instead of joining or duplicating the active run.

## Shared run preparation

`magazine.run_preparation.prepare_run(...)` is the shared preparation boundary used by the synchronous entrypoint and async runner.

It performs, in order:

1. owner validation;
2. Magazine loading and schema validation;
3. cartridge-ID extraction in Magazine order;
4. creation of the `run/` state directory;
5. one-time derivation of `state.json` and `.lock` paths;
6. exactly one atomic `persist_admit_run(...)` call.

It returns a `PreparedRun` containing the exact admitted state and exact paths used for admission. It does not re-read state after admission, dispatch work, retry, recover, print, or translate exceptions.

## Execution layers

Magazine keeps the orchestration layers deliberately small and explicit.

### `dispatch_current_cartridge(...)`

Loads the Magazine document and authoritative persisted state, selects the persisted `current` cartridge, and delegates the external runtime dispatch. It returns both the selected cartridge ID and the dispatch result.

### `execute_one_cartridge(...)`

Performs one bounded:

```text
dispatch -> guarded completion
```

The dispatched cartridge ID is passed into completion as an identity guard. If the freshly loaded persisted `current` no longer matches that ID, completion fails without mutating state.

There is no internal retry, recovery, or multi-cartridge loop here.

### `run_all_cartridges(...)`

Repeats the one-cartridge operation until the run reaches `COMPLETED`, using guarded begin-next transitions between cartridges.

This function retains a **single-runner precondition**. Direct concurrent callers are not made safe merely by calling `run_all_cartridges(...)` itself.

### `run_magazine(...)`

`magazine.async_runner.run_magazine(...)` is the public async composition boundary for a normal run.

It calls `prepare_run(...)` exactly once and then:

- delegates a `RUNNING` state to `run_all_cartridges(...)`;
- returns an already `COMPLETED` state without dispatch;
- returns an already `HALTED` state without dispatch;
- fails explicitly on an unexpected status.

It does not re-read state, re-derive the paths, acquire another admission lock, add retry/recovery behaviour, or catch and translate underlying exceptions.

The public runner plus atomic admission is the supported normal-runner path that prevents two normal callers from dispatching the same active run.

## Synchronous entrypoint

`magazine.entrypoint` is a synchronous preparation CLI adapter. It validates and admits a run but deliberately does **not** perform external dispatch.

For example:

```powershell
py -3 -m magazine.entrypoint --magazine .\example.json --state-dir .\state --owner-id magazine
```

A successfully admitted `RUNNING` state means the run has been prepared for execution. `COMPLETED` and `HALTED` are treated as terminal states with nothing to dispatch from this CLI adapter.

The CLI is not the final Pi-native Magazine execution interface.

## Explicit recovery

Recovery is conservative by design.

The recovery classifier distinguishes startable, locked, completed, halted, and uncertain states. A persisted `RUNNING` state with no lock is classified as uncertain rather than assumed safe to resume.

The explicit persisted recovery action can transition an uncertain `RUNNING` state to `HALTED` while preserving its exact work position. Magazine does not silently retry, resume, advance the queue, or infer whether an interrupted external dispatch succeeded.

## Current runtime limitation

The orchestration core above is real and tested, but the active external-runtime seam has not yet been migrated to Pi.

The current production call chain still reaches legacy runtime components conceptually as follows:

```text
run_magazine
-> run_all_cartridges
-> execute_one_cartridge
-> dispatch_current_cartridge
-> runtime_dispatch
-> model/tool resolution
-> live runtime-pair validation
-> legacy Open WebUI discovery/preflight assumptions
-> external dispatch callback
```

The concurrency tests isolate the orchestration core by replacing the live runtime-pair validation step. Therefore the 410-test baseline proves the state, persistence, orchestration, and normal-runner concurrency behaviour; it does **not** prove an end-to-end Pi-native dispatch path.

The next runtime integration work must be based on the actual Pi Fresh Worker interface rather than an assumed API. Until that seam is reconciled, Magazine should not be described as fully Pi-native end to end.

## Legacy Open WebUI proof components

Earlier Open WebUI proof and discovery code remains in the repository, including modules such as:

- `magazine/probe.py`
- `magazine/fresh_chat_proof.py`
- `magazine/tool_chat_proof.py`
- `magazine/discovery.py`
- `magazine/runtime_preflight.py`
- parts of `magazine/runtime_dispatch.py`

These files are retained as historical/proven behaviour and should not be interpreted as the desired future runtime architecture.

The legacy read-only connectivity probe can still be invoked when intentionally testing that historical path:

```powershell
$env:MAGAZINE_OPEN_WEBUI_BASE_URL = "http://localhost:3000"
$env:MAGAZINE_OPEN_WEBUI_API_KEY = "<your-api-key>"
py -3 -m magazine.probe
```

Keep API keys out of source code and committed files.

## Tests

With the development test environment available, run:

```powershell
py -3 -m pytest -q
```

Current validated baseline:

```text
410 passed in the full suite
0 warnings
```

Focused tests cover schema validation, state validation, atomic persistence, locking, transitions, recovery, guarded persisted actions, entrypoint preparation, dispatch selection, completion, single-cartridge orchestration, multi-cartridge looping, atomic admission, shared preparation, the async public runner, and the public two-runner concurrency case.
