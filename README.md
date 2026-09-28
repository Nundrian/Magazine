# Magazine

Magazine is a cartridge-based orchestration system for running bounded model tasks through controlled, isolated execution contexts.

Its purpose is to make multi-step model work predictable and recoverable rather than relying on one long conversational context.

The project separates orchestration from model execution:

- Magazine controls the run.
- A cartridge defines a bounded unit of work.
- Runtime-specific code performs the actual model request.
- State is persisted explicitly so interrupted runs can be inspected and recovered.

The current development priority is to complete the **Open WebUI implementation of Magazine**.

A Pi-native Magazine implementation may be developed later, but it is a separate future direction and should not influence the current Open WebUI architecture unless there is a concrete requirement to do so.

---

## Current Direction

Magazine is currently being completed for **Open WebUI**.

Open WebUI is therefore an active runtime target, not merely legacy code awaiting replacement.

The immediate objectives are:

1. preserve the proven Stage 1–6 orchestration core;
2. complete and verify the Open WebUI execution path;
3. ensure each cartridge's selected model actually controls the model used for that cartridge's Open WebUI request;
4. add focused tests for the completed runtime behaviour;
5. keep the stable orchestration layer independent of unnecessary runtime-specific complexity.

Pi Web UI and Bonsai 2 27B are currently being used as a **development environment for working on Magazine**.

They are not presently the production runtime for the Open WebUI Magazine implementation.

---

# Architecture

Magazine's core is a Python orchestration system built around explicit persisted state and bounded cartridge execution.

The main public async entry point is:

```python
run_magazine(...)
```

The execution flow conceptually follows:

```text
run_magazine(...)
    ↓
prepare/admit run
    ↓
run cartridge loop
    ↓
prepare one cartridge
    ↓
execute one cartridge
    ↓
dispatch cartridge prompt
    ↓
runtime-specific model execution
    ↓
persist resulting state
```

Relevant core modules include:

```text
magazine/
    async_runner.py
    run_loop.py
    run_preparation.py
    orchestration.py
    execute.py
    run_action.py
    complete.py
    state_store.py
    state.py
    recovery.py
    run_lock.py
    schema.py
```

Runtime-facing code is kept separate from the state and concurrency machinery wherever practical.

The orchestration layer should not need to know the internal implementation details of a particular model host.

---

# Run State

Magazine uses an explicit persisted state machine.

The normal lifecycle is:

```text
READY
  ↓
RUNNING
  ↓
READY
```

between cartridges, followed eventually by:

```text
RUNNING
  ↓
COMPLETED
```

when the Magazine run finishes.

Failure or interrupted-execution recovery uses:

```text
RUNNING
  ↓
HALTED
```

as a separate recovery path.

State contains a revision value so transitions can be validated against the expected persisted version.

State updates are written atomically.

Magazine does not rely on conversational memory to determine whether work has completed.

---

# Locking and Concurrency

Magazine uses short-duration locking around state-sensitive operations.

A lock is **not held while the model is executing**.

The intended pattern is:

```text
acquire lock
    ↓
read/validate/update persisted state
    ↓
release lock
    ↓
perform potentially long model execution
    ↓
reacquire lock when required
    ↓
commit resulting state transition
```

This avoids holding a filesystem lock for the potentially long duration of a model request.

Stage 6 added atomic single-runner admission so two processes cannot both successfully claim a run that is initially `READY`.

The concurrency design deliberately remains small.

The proven core does not currently depend on:

- distributed leases;
- heartbeats;
- automatic retry machinery;
- long-held model-execution locks.

Those mechanisms should only be introduced if a demonstrated requirement appears.

---

# Recovery

Recovery is explicit.

Magazine does not silently guess whether an interrupted `RUNNING` state represents completed or incomplete model work.

A run left in an unexpected execution state can be inspected and moved through the defined recovery path.

The recovery system is part of the stable orchestration core and should not be redesigned while completing the Open WebUI runtime unless a genuine defect is demonstrated.

---

# Cartridge Execution

A cartridge represents one bounded unit of model work.

Magazine is responsible for:

- loading the current run;
- identifying the current cartridge;
- resolving the information required to execute it;
- preparing its prompt;
- invoking the supplied dispatch mechanism;
- handling the result;
- updating persisted state;
- proceeding to the next cartridge;
- completing the run when no cartridges remain.

Cartridges are executed sequentially by the current orchestration design.

The dispatch operation is intentionally supplied as a dependency rather than being embedded throughout the state-management code.

Conceptually:

```python
result = await dispatch(prompt)
```

The runtime implementation behind `dispatch` may perform additional runtime-specific work, but the orchestration core should remain concerned with the cartridge operation rather than the details of Open WebUI networking.

---

# Model Selection and Open WebUI Dispatch

Per-cartridge model selection is an important current verification target.

Magazine may resolve a model for a particular cartridge, but resolution alone is not sufficient.

The actual execution path must guarantee:

```text
cartridge model selection
        ↓
resolved model
        ↓
dispatch configuration
        ↓
Open WebUI request
        ↓
that exact selected model executes the cartridge
```

The dispatch-side half of this path is now in place and verified: each cartridge resolves its own model, the shared dispatch dependency receives `(prompt, model_id)`, and two sequential cartridges with different resolved models produce two dispatch calls carrying their respective model IDs (full regression suite: 410 passed).

The remaining gap is on the Open WebUI side: the supplied `model_id` is not yet used as the model in the actual Open WebUI request. Any implementation addressing this should use the smallest change compatible with the existing dependency-injection design.

The Stage 1–6 state, concurrency and recovery architecture should not require redesign simply to support per-cartridge Open WebUI model selection.

---

# Open WebUI Runtime

Open WebUI remains the runtime currently being completed.

Existing Open WebUI-facing components should therefore be treated as active implementation code unless repository evidence shows otherwise.

Earlier development included runtime/preflight functionality capable of reaching Open WebUI and invoking model execution.

The immediate goal is not to replace that runtime architecture with Pi.

The goal is to make the Open WebUI path complete, deterministic and properly tested.

Where older Open WebUI proof utilities remain in the repository, distinguish between:

- code on the real Magazine execution path;
- development/proof utilities;
- obsolete experiments.

Do not remove older files solely because they are not part of the newest implementation path without first establishing whether they provide useful tests, diagnostics or historical compatibility.

---

# Public Async Runner

`magazine/async_runner.py` provides the public async composition point.

The established design includes a public:

```python
run_magazine(...)
```

interface.

It performs run preparation/admission and then delegates cartridge execution to the existing orchestration/run-loop machinery.

The caller supplies the dispatch dependency required to execute model prompts.

This composition boundary is useful and should be retained unless a concrete requirement demonstrates otherwise.

---

# Proven Stage 1–6 Baseline

The clean Stage 1–6 checkpoint is:

```text
b5e762a704ae752d9e8176dd3381fd9f1c15ac59
```

Commit subject:

```text
Checkpoint Stage 1-6 orchestration and 410-test baseline
```

At this checkpoint:

```bash
python -m pytest -q
```

reported:

```text
410 passed, 0 warnings
```

This is the verified baseline for the completed orchestration work.

It should not automatically be interpreted as the test count or working-tree status of later uncommitted development.

Always use the current repository and test output when assessing work performed after this checkpoint.

---

# Running the Tests

Use:

```bash
python -m pytest -q
```

rather than relying on:

```bash
pytest -q
```

The former has been the reliable invocation in the Magazine environment.

For runtime changes, run focused tests during development and then run the complete suite before creating a new checkpoint.

A successful focused test is not a replacement for the full regression suite.

---

# Pi Web UI and Bonsai

The current development setup uses Pi Web UI with Bonsai 2 27B.

Typical local model configuration:

```text
Model: Bonsai 2 27B
Reasoning: Medium
Temperature: 1.0
```

Pi is being used because it provides a productive coding-agent environment for Bonsai.

This does **not** mean Magazine itself currently targets Pi as its runtime.

Keep these concepts separate:

```text
Magazine
    = the project/product

Open WebUI Magazine
    = the implementation currently being completed

Pi Web UI + Bonsai
    = the current development harness

Pi-native Magazine
    = a possible future implementation
```

This distinction is important.

Development tooling should not accidentally dictate production architecture.

---

# Fresh Worker

Fresh Worker is a development aid for model-assisted development.

It creates a new isolated worker context for one bounded task.

The worker receives the task without inheriting the parent conversation's accumulated context.

This helps reduce:

- context contamination;
- accidental reliance on earlier discussion;
- task drift;
- unnecessary prompt growth.

Fresh Worker is a **pattern rather than one universal Magazine subsystem**.

An Open WebUI Fresh Worker was created when development primarily used Open WebUI.

When development moved to Pi because Pi proved a better harness for Bonsai, a Pi-specific Fresh Worker was created.

If another development harness is used later, an equivalent worker could be implemented for that harness if it is useful.

The principle is:

```text
one bounded task
        ↓
fresh isolated context
        ↓
controlled result
```

Fresh Worker should not be treated as Magazine's runtime architecture.

It is tooling used to help build Magazine.

The same pattern may remain valuable with substantially larger models because isolation also controls context and delegation rather than merely compensating for limited model capability.

---

# Experimental Pi/Python Runtime Work

A later experiment investigated connecting Magazine Python execution directly to Pi.

The experiment included work around:

```text
Pi
 ↓
Python child process
 ↓
JSONL stdin/stdout protocol
 ↓
Fresh Worker
 ↓
result returned to Python
```

and development/proof names such as:

```text
pipe_proof
pipe_proof.py
pipe_proof_verify.py
pipe_proof_node_verify.cjs
```

This work explored one possible Pi-native Magazine runtime boundary.

It is **not the current implementation direction**.

It should not be used as justification for introducing Pi-specific runtime abstractions into the Open WebUI implementation.

If these experimental files remain in the working tree, treat them as experimental until they are deliberately retained, archived or removed.

Do not continue the Pi/Python transport experiment as part of completing Open WebUI Magazine.

---

# Why Pi Magazine Is Deferred

A future Pi-native version of Magazine may be useful.

However, it does not need to share the exact runtime architecture of the Open WebUI implementation.

Trying to design both implementations simultaneously risks:

- unnecessary abstraction;
- duplicated runtime assumptions;
- coupling Magazine to development tooling;
- delaying completion of the working Open WebUI version.

The preferred sequence is therefore:

```text
finish Open WebUI Magazine
        ↓
stabilise and validate behaviour
        ↓
understand which abstractions genuinely proved reusable
        ↓
design Pi-native Magazine separately
```

Reuse should come from demonstrated commonality rather than prediction.

---

# Stable vs Runtime-Specific Code

A useful architectural distinction is:

## Stable Magazine core

Expected to remain largely runtime-independent:

- state model;
- state persistence;
- atomic transitions;
- locking;
- admission;
- recovery;
- cartridge sequencing;
- run completion;
- orchestration contracts.

## Runtime-specific layer

May differ between integrations:

- authentication;
- server discovery;
- model enumeration;
- model identifiers;
- API request format;
- response extraction;
- runtime error handling;
- connection/preflight checks.

The Open WebUI implementation should complete its runtime-specific layer without forcing Pi-specific concerns into the stable core.

---

# Current Development Priority

Passing the resolved per-cartridge `model_id` into the shared dispatch is complete and verified (410 tests passed). The immediate priority is now:

> **Make the real Open WebUI dispatch implementation use the supplied `model_id` as the model in the actual Open WebUI request.**

For a cartridge that resolves model `A`, the actual Open WebUI request must execute model `A`.

For a subsequent cartridge that resolves model `B`, its request must execute model `B`.

This behaviour should be demonstrated through focused tests covering the actual execution/composition path rather than merely testing the model resolver in isolation.

Before changing code, trace:

```text
cartridge
→ model resolution
→ preparation
→ run loop
→ execution
→ dispatch creation
→ Open WebUI request
```

Identify the exact point where model information is preserved or lost.

Then make the smallest necessary implementation change.

Do not redesign the Stage 1–6 orchestration core to solve a runtime dispatch problem.

---

# Development Principles

When continuing Magazine development:

1. **Treat tested code as evidence.**
   Do not infer behaviour from filenames or architectural intent when the execution path can be inspected.

2. **Keep tasks bounded.**
   Prefer one coherent objective over repository-wide redesign.

3. **Preserve the proven core.**
   State, locking, recovery and admission have already undergone focused development and testing.

4. **Use positive implementation instructions.**
   Describe the desired path clearly. Add restrictions where a realistic failure mode needs guarding against.

5. **Separate product architecture from development harnesses.**
   Pi, Open WebUI and future tools may all be useful development environments without becoming part of Magazine's core architecture.

6. **Avoid speculative abstractions.**
   Build what the current Open WebUI implementation requires.

7. **Verify before checkpointing.**
   Focused tests first, then the complete suite.

8. **Use fresh development contexts at meaningful boundaries.**
   A clean agent context is particularly useful when beginning a new architectural or implementation phase.

---

# Repository Hygiene

Before starting a new implementation task:

```bash
git status --short
```

should be inspected.

Do not automatically stage, reset or delete unfamiliar changes.

Magazine development has included intentional experiments and older proof files, so an unexpected file should be classified before being modified.

When a meaningful development phase is complete:

1. run focused tests;
2. run the complete regression suite;
3. inspect the diff;
4. inspect Git status;
5. create a clean checkpoint;
6. archive/ZIP the project when the checkpoint is especially important.

---

# Current Known Checkpoint

```text
Commit:
b5e762a704ae752d9e8176dd3381fd9f1c15ac59

Subject:
Checkpoint Stage 1-6 orchestration and 410-test baseline

Verified regression:
410 passed, 0 warnings
```

Development after this checkpoint must be assessed from the current working tree rather than assumed to be part of the checkpoint.

---

# Near-Term Roadmap

The intended order of work is:

```text
1. Reconcile current repository state
        ↓
2. Trace per-cartridge model selection through Open WebUI dispatch
        ↓
3. Implement the smallest required runtime fix
        ↓
4. Add focused model-dispatch tests
        ↓
5. Run complete regression suite
        ↓
6. Exercise the real Open WebUI Magazine path
        ↓
7. Resolve remaining Open WebUI runtime gaps
        ↓
8. Create the next clean checkpoint
```

Only after the Open WebUI implementation is complete and stable should a separate Pi-native Magazine implementation become an active architectural task.

---

# Project Principle

Magazine should remain boring where boring is valuable.

Persist state explicitly.

Make transitions explicit.

Keep model execution outside long-held locks.

Make recovery deliberate.

Give each cartridge the model and prompt it is actually supposed to run.

Keep runtime-specific complexity at the edge.

Finish one working implementation before designing the next one.