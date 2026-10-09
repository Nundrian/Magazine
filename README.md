# Magazine

**Reliable LLM workflows, one task at a time.**

Magazine is a **model-independent, sequential workflow runner** for organising and executing multi-stage work with large language models (LLMs). It separates the management of a workflow from the AI models carrying out its individual tasks.

> **Project status:** Under development. This repository is an introductory project page, not an installable release.

## Why Magazine exists

A complicated request sent to an LLM as one long prompt can become difficult to control, inspect and repeat. Instructions can be lost, the model can drift from the task, and it can be unclear which stage succeeded or failed.

Magazine takes a different approach: **define the work as a sequence of bounded tasks, then let Magazine manage the process while selected models do the work.**

## What Magazine is designed to do

- **Validate workflows before execution.** Check the structure and required fields of submitted `.magazine` workflows, and prevent invalid tasks from being dispatched.
- **Organise work into cartridges.** Each cartridge defines one job using an `id`, a `job_type` and a non-empty `prompt`.
- **Execute work in a controlled sequence.** Keep task order and workflow progression under the runner's control, rather than relying on an LLM to manage its own orchestration.
- **Route tasks to selected models.** Resolve the requested model to an available model at execution time, while preserving the cartridge's task instructions.
- **Track execution state.** Maintain durable records of workflow preparation, progress and outcomes so a run can be inspected and failures can be diagnosed.
- **Preserve results.** Make completed task outputs available for inspection and subsequent workflow stages, subject to the workflow's defined contracts.
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

Magazine's orchestration core and initial Open WebUI runner have been implemented and tested through workflow submission, validation, preparation and durable `READY` creation. At the last documented development checkpoint, **integrated execution through to `COMPLETED`, and retrieval of finished outputs, had not yet been verified end to end**.

The immediate priorities are:

1. Complete the authenticated hand-off from a prepared run to real execution.
2. Demonstrate a full workflow from submission through execution to `COMPLETED`.
3. Verify the output and failure paths with end-to-end tests.
4. Publish installation, configuration and usage instructions once a working release is verified.

**No source code or installation procedure is provided in this introductory repository yet.** Future capabilities such as multi-agent coordination, worker pools or multi-Magazine orchestration are not part of the initial release claim.
