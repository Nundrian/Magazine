# Optional GPT / Claude model assignments

Candidate feature for **Magazine Simple 0.3.0-scheduler.rc1** (Open WebUI). This is a feature-branch proposal, **not an installed release**.

## Why

Magazine already maps exact models per cartridge. This adds an optional way for GPT, Claude or a person to suggest those mappings while authoring a workflow, without adding another agent or model call to the executor.

## How it works

1. Import or load a normal `magazine.yaml` workflow.
2. Expand **GPT / Claude model assignments (optional)**.
3. Choose **Copy planning brief**; review the task excerpts for confidential information before sharing them with an AI assistant.
4. Have GPT or Claude return a version 1.0 model-plan JSON object using *exact* model IDs visible in the live host catalogue.
5. Paste the result or include a file named `model-assignments.json` at the root of the ZIP.
6. Click **Validate and apply suggested models**, review each per-cartridge selector and the tool/Terminal choices, then **Prepare**. Starting execution remains a separate explicit action.

## Model-plan contract

```json
{
  "schema_version": "1.0",
  "workflow_name": "Example project",
  "cartridge_ids": ["c01", "c02"],
  "cartridge_models": {
    "c01": "exact-model-id-from-current-host",
    "c02": "second-exact-model-id-from-current-host"
  }
}
```

`default_model` is optional to cover cartridge IDs omitted from `cartridge_models`.

The workflow name and ordered cartridge IDs must match the loaded workflow. All resolved model IDs must exist in the host's current catalogue; missing assignments, unknown cartridge IDs, unavailable models and malformed JSON are rejected. The sidecar is bounded to 256 KiB. A rejected suggestion does **not** invalidate an otherwise working workflow.

The model plan is **data, not executable instructions**. It cannot modify the workflow prompts, grant tools, change the result protocol, adjust retry/Antiloop/scheduling behaviour, or start/continue an existing run. Once prepared, the run already contains its fixed concrete model choices, as it did before this feature.

## Evidence and classification

Model-card capabilities are not interchangeable with **local success rates**. Historical user trials showed intermittent missed tool calls with Qwen3-Coder. Qwen3-Coder-30B-A3B is a **non-thinking coding specialist**, not a reasoning model; it may fit bounded implementation work, but reasoning-heavy design, security review and independent audits merit a more suitable reasoning-capable model. GPT/Claude should distinguish known measurements, public claims, and estimates, and not invent benchmark numbers.

Exact model identifiers are installation-specific: a plan made for Pi WebUI cannot be assumed to work in Open WebUI without checking its catalogue.

## Verification scope and release gate

The feature branch's browser script was syntax-checked offline, and one valid plus five invalid assignment cases were tested without dispatching inference. The existing scheduler/engine was not intentionally modified.

**Not yet tested:** deployed Open WebUI browser interactions on the owner's installation, or a real-model run using the updated UI. Do not replace an active installed Function. Test on a separate/idle candidate installation first, retain a complete rollback copy, and preserve the existing Function ID, valves, run state, scheduler journal and Sidebar Launcher.
