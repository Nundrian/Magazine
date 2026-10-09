# Magazine

**Reliable LLM workflows, one task at a time.**

Magazine is a **model-independent, sequential workflow runner** for organising and executing multi-stage work with large language models (LLMs). It separates the management of a workflow from the AI models carrying out its individual tasks.

> **Project status:** Live end-to-end execution demonstrated in a development environment; the public repository is currently a project overview, **not an installable release**.

## Why Magazine exists

A complicated request sent to an LLM as one long prompt can become difficult to control, inspect and repeat. Instructions can be lost, the model can drift from the task, and it can be unclear which stage succeeded or failed.

Magazine takes a different approach: **define the work as a sequence of bounded tasks, then let Magazine manage the process while selected models do the work.**

## What Magazine is designed to do

- **Validate workflows before execution.** Check the structure and required fields of submitted `.magazine` workflows, and prevent invalid tasks from being dispatched.
- **Organise work into cartridges.** A cartridge defines one bounded task. The package format uses `id`, `model` and `prompt_file`; the legacy JSON format uses `id`, `job_type` and `prompt`.
- **Execute work in a controlled sequence.** Keep task order and workflow progression under the runner's control, rather than relying on an LLM to manage its own orchestration.
- **Route tasks to selected models.** Resolve the requested model to an available model at execution time, while preserving the cartridge's task instructions.
- **Track execution state.** Maintain durable records of workflow preparation, progress and outcomes so a run can be inspected and failures can be diagnosed.
- **Preserve results.** Store each completed task's output durably for retrieval and inspection. Automatic passing of one cartridge's output to the next is outside Magazine's initial scope.
- **Support different model providers.** Keep the core workflow logic independent of any one LLM. The initial integration target is Open WebUI.

## The basic idea

```text
User submits a .magazine workflow
                |
                v
      Validate the cartridges
                |
                v
       Prepare and preview
                |
                v
    Resolve each task's model
                |
                v
     Execute tasks in sequence
                |
                v
     Record status and results
```

Magazine controls **what runs, in what order and under what conditions**. The selected LLM is responsible for completing the content of each assigned task.

## Architecture

Magazine is deliberately focused on **reliable workflow execution**, not on becoming an all-purpose AI agent platform.

Its principal parts are:

- **Magazine core:** the canonical workflow validation and orchestration logic.
- **Host runner:** the integration layer that accepts workflows, performs the necessary access and model checks, and invokes the core. Open WebUI is the initial host.
- **Cartridges:** individual, bounded units of work that the core can validate and dispatch.

Related tools such as **Fresh Worker** (isolated task execution) and the **research evidence ledger** (evidence tracking) are separate projects. They may complement Magazine, but are not described here as integrated Magazine features.

## Intended uses

Magazine is intended for jobs that benefit from a dependable sequence of distinct LLM tasks, including:

- Research processes with separate collection, analysis and reporting stages.
- Multi-stage writing, editing and quality review.
- Structured software inspection, implementation and testing workflows.
- Repeatable technical or analytical tasks where execution history matters.

These are **intended applications**, not claims that packaged workflows for each use case are already available.

## Development status

Magazine's core and native Open WebUI runner have passed local regressions and live development-environment acceptance tests, including sequential model execution, durable results retrieval, in-process reload fencing and lost-runtime recovery. **This does not establish a packaged public release or production readiness.**

### Recorded test milestones (2–8 October 2026)

These findings are documented in the supplied project's development reports and test artefacts; they have **not been independently rerun in this introductory repository**.

- **Core regression:** **560/560 tests passed** on the canonical core at commit `381b361` (8 October), including workflow, state, execution and recovery behaviour.
- **Native runner regression:** **45/45 tests passed** at runner commit `f663e0a` (8 October), covering native Open WebUI integration and execution/recovery cases.
- **Embedded-code integrity:** **28/28 Python source files** in the runner's embedded Magazine core matched the canonical source; the deployed Event Function also matched the committed runner by SHA-256.
- **Authenticated preparation:** Live Open WebUI checks demonstrated workflow validation, cartridge preview, user-scoped ownership, model/tool admission and durable creation of `READY` runs.
- **Native end-to-end execution:** A live **two-cartridge** Bonsai 2 27B run progressed `READY → RUNNING → COMPLETED` and returned the expected results (`MAGAZINE_FIRST_OK` and `323`) through the results API.
- **Sequential isolation and cleanup:** A separate live **three-cartridge** audit completed in order with distinct model chats, three persisted results and no newly created chats left behind.
- **Tool forwarding:** A live audit recorded forwarding of the `development_toolkit` selection and a tool call, but **did not verify the intended on-disk command marker**; full external side-effect success was not established.
- **FM-1 reload fencing:** A live in-process Event Function reload preserved runtime identity and ownership, with no stale writes; a second start was rejected with HTTP **409** after the run had already completed.
- **FM-2 lost-runtime recovery:** Following a genuine Open WebUI process replacement, a lost-owner run halted safely, was recovered explicitly and ultimately completed all three long cartridges over subsequent attempts, with stale-owner writes fenced off.

The immediate priorities are:

1. Consolidate the verified development code and deployment procedure into a reproducible release.
2. Test installation, authentication, recovery and error handling across supported environments.
3. Resolve known model/tool compatibility limitations and improve operational diagnostics.
4. Publish installation, configuration and usage instructions once release acceptance is complete.

**No source code or installation procedure is provided in this introductory repository yet.** Future capabilities such as multi-agent coordination, worker pools or multi-Magazine orchestration are not part of the initial release claim.
