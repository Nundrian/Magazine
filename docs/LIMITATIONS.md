# Current limitations

**Magazine v0.1.0-beta.5 is a proof of concept.** These are limits of the current software, not a roadmap or a commitment to specific future features.

## Workflows and results

- **You prepare the tasks.** Magazine executes supplied cartridge prompts; it does not automatically plan or decompose a project. Cartridge quality depends on the author of the workflow.
- **No automatic result handoff.** Cartridge prompts should be independently usable. Magazine preserves earlier results but does not insert them into later prompts automatically.
- **Completion is not correctness.** A `COMPLETED` run confirms the execution sequence finished and results were saved, not that a model's answer was accurate or the intended work succeeded.
- **Only canonical text is exported.** Finished Work includes saved cartridge responses, a manifest and `results.zip`. Separate files created by external tools are not collected automatically. These exports aren't reconstructed chat archives.

## Execution and recovery

- **STOP is not Pause/Resume.** A requested STOP prevents later cartridges once applied. The in-flight inference may finish before Magazine halts at a safe boundary. Durable STOP runs cannot take the separate failure-recovery path.
- **Recovery is explicit.** Eligible failure-halted runs can be prepared to continue, but recovery doesn't automatically start them.
- **Exports can need attention after interruption.** A process interruption may prevent automatic export even if the run reaches `COMPLETED`. Follow the documented owner retry or manual inspection procedure; old runs aren't automatically backfilled.

## Deployment and security

- **Accepted deployment scope is narrow.** The documented runtime is a single Linux backend, including Docker Desktop on Windows. Multi-worker/Redis setups and deployments using a proxy path prefix haven't been accepted.
- **Open WebUI permissions still apply.** Magazine doesn't provision additional tools, models or user permissions. Only enable tools and workflows you trust.
- **The customised Docker binary remains on hold.** Known inherited security/licensing issues prevent approval of its public binary redistribution. The public installation route is the Function-only integration with its documented host requirements.
- **Back up persistent data.** Installation, updates and storage path changes should preserve the existing Open WebUI data volume and deployment configuration.

Further features are planned, but details will be published when they are ready.

See [installation](FUNCTION_INSTALL.md), [storage](STORAGE.md), [STOP behaviour](STOP.md), [security](SECURITY.md) and [licensing](LICENSING.md).
