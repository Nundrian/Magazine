# Magazine Simple

**Make bigger projects manageable for your local AI.**

Magazine is a workflow runner for [Open WebUI](https://github.com/open-webui/open-webui). It splits a project into smaller jobs called **cartridges**. Each cartridge runs in a fresh chat with the model you choose. Magazine keeps the order, saves the work, and passes verified results to the next job.

You plan the jobs; Magazine runs them. It does not invent a plan or guarantee the AI's work is correct.

**Latest installed and source-verified version:** **0.3.1-ledger.rc1** (21 November 2026), tested with **Open WebUI 0.12.5**. This is a **release candidate**, not a general production certification.

[**Latest exact Function source**](integrations/open_webui/function/magazine_simple_scheduler_rc1.py) · [**Release notes and test report**](docs/SIMPLE_0.3.1_LEDGER_RC1.md) · [**Full repository ZIP**](https://github.com/Nundrian/Magazine/archive/refs/heads/main.zip) · [**How to use Magazine**](docs/USING_MAGAZINE.md) · [**Technical details**](docs/SIMPLE_0.3.1_LEDGER_RC1.md) · [**Changelog**](CHANGELOG.md)

## The interface

**The picture below shows the previous 0.3.0 layout**; the latest release replaces the long six-column ledger with a compact single-cartridge view. The other dashboard controls remain.

![Annotated continuous walkthrough of Magazine's dashboard, workflow selection, model setup, tools, running view, ledger and previous results](docs/images/magazine-simple-0.3.0-walkthrough.webp)

*Built from the owner's actual 18 November 2026 screenshots, with arrows and short explanations. Different expanded/collapsed states are arranged vertically to make one readable walkthrough; it is **not** an unedited single browser capture.*

## What's new in 0.3.1

- **One selected cartridge at a time:** cartridge picker, Previous/Next, position, status filtering and optional following of the running cartridge.
- **Full evidence, less clutter:** compact four-field summary with model and duration; expandable cartridge and attempt records, including tools, errors, handovers and verification evidence.
- **Access saved chats:** attempt-specific native Open WebUI links; opening the full conversation in a human-authenticated browser still needs independent verification.
- **Accurate timing:** cumulative timed attempts distinct from the full execution span. Token accounting remains server-reported, with unknown/incomplete usage identified.
- **Restores what you were viewing:** remembers the selected cartridge and expanded sections without starting inference.

**Verification:** the supplied release reports 446 passing checks and 21 skips. Firefox/Chromium installed-host UI checks passed; native signed-in chat rendering and live-model interaction were not tested. [Read the qualification and limits](docs/SIMPLE_0.3.1_LEDGER_RC1.md).

## What changed in 0.3.0

- **More than one project:** the dashboard shows each project's status, current cartridge, model, token reporting and position in the queue.
- **Two scheduling choices:** **Sequential** finishes one project before starting the next. **Round Robin** gives projects turns, switching after each completed and verified cartridge. Only **one cartridge runs at a time**.
- **Better return to previous work:** reopening the page restores saved run details instead of looking like an empty, new session. Interrupted work is retained, but it does **not restart itself**.
- **Clearer ledger:** six overview columns, expandable attempt details, status filters, reported tokens and duration.
- **Safer stopping and recovery:** a stopped project stays stopped, earlier verified work is preserved and failures are recorded without silently replaying completed cartridges.

The existing **Antiloop** protection, **five-attempt maximum**, **1,800 seconds per fresh chat**, checked file handovers and results ZIP remain part of Magazine. The new scheduler has passed isolated tests; several live multi-project and service-restart scenarios still need real-world acceptance.

[See exactly what changed and what was tested](docs/TECHNICAL_0.3.0_SCHEDULER_RC1.md).

## Get started

1. **Import a workflow ZIP** using **Add a workflow**. This ZIP contains the job instructions, not the Magazine software.
2. **Load the workflow** and choose a model for every cartridge (or one model for all).
3. **Choose a Terminal and any tools** that the work actually needs. Tools are unticked by default.
4. Select **Prepare workflow** to create a ready-to-run job. This **does not start** your AI.
5. Select **Start workflow**. Watch progress in the dashboard and ledger, then download the results ZIP.

If a run stops, inspect the recorded reason. You can continue from an eligible cartridge without deleting earlier results. This is not a guarantee that unfinished code is correct.

[Step-by-step guide](docs/USING_MAGAZINE.md)

## Install or update Magazine Simple

The repository includes the exact source of the version installed on Open WebUI 0.12.5:

**Read the latest [Function source](integrations/open_webui/function/magazine_simple_scheduler_rc1.py)** (SHA-256 `da550dbc5e659c411c77395550d9ff59124663f48f20dc6b594f8f61840e0eec`) or [download the full repository as a ZIP](https://github.com/Nundrian/Magazine/archive/refs/heads/main.zip). The previous 0.3.0 source-only release remains [available here](downloads/Magazine-Simple-0.3.0-scheduler.rc1-source.zip). Do not confuse a repository archive with a cartridge workflow ZIP. See [installation notes](docs/INSTALL_0.3.1_LEDGER_RC1.md). The [versioned Function source](integrations/open_webui/function/magazine_simple_scheduler_rc1.py) is maintained at the same path used by the installed Open WebUI Function.

Make a backup and ensure the scheduler has no active or queued work before updating. In **Open WebUI → Admin Panel → Functions**, edit the existing **Magazine Simple** Function and replace its source, keeping its Function identity and settings. Save, then open **`/magazine-simple`**.

**Do not** upload the Function source or a source-release ZIP using **Add a workflow**: that control is for cartridge workflows only. The **Sidebar Launcher 0.1.0** is separate and unchanged.

For the latest source checksum, installation evidence and limitations, see the [0.3.1 ledger technical record](docs/SIMPLE_0.3.1_LEDGER_RC1.md). The [0.3.0 technical guide](docs/TECHNICAL_0.3.0_SCHEDULER_RC1.md) remains available for the unchanged engine.

## What Magazine can — and cannot — promise

Magazine can run and record a planned sequence, check declared handover files, retain evidence, make limited recovery attempts and prevent several known failure modes. It **cannot** guarantee that a model wrote correct code, that a service will survive every outage, or that two models can safely edit the same project folder at once. Review workflows before giving them a Terminal or powerful tools.

Older Function versions, experiments and evidence remain in the repository for reference. For an earlier visual demonstration, see the [historical November interface guide](docs/guide/index.html), which is not a screenshot of the latest release.

## Licence

Magazine is released under the [MIT licence](LICENSE). Open WebUI, models and connected tools have their own licences.
