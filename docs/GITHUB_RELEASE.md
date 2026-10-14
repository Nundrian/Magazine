# Magazine v0.1.0-beta.5 — Beta proof of concept

Magazine runs prewritten, bounded tasks called **cartridges** through models connected to Open WebUI. It manages the sequence outside the model and preserves each cartridge's result before moving on. The idea is to help smaller, locally hosted models tackle more complicated projects one manageable task at a time.

### In beta.5

- Original workflow ZIP retention in a dedicated **Input ZIPs** directory after a successful import.
- Saved results exported into **Finished Work** as individual text files, a manifest and `results.zip`.
- Owner-authorised export/retry, including completed results from eligible halted runs.
- Existing workflow preparation, sequential execution, model/tool selection, STOP and recovery behaviour retained.

### Verification

The beta.5 acceptance record reports **989 passing automated regression tests** (611 core, 244 Function, 134 interface/sidebar), plus isolated stock Open WebUI installation and deterministic Windows NTFS storage checks. Live Bonsai and browser/STOP tests come from the earlier beta.4 baseline, not a fresh beta.5 inference run.

### Installation

Use the **Function-only package** with the supported **Open WebUI 0.11.3** setup. Begin with the [Function installation guide](FUNCTION_INSTALL.md) and [storage guide](STORAGE.md). The customised Docker image remains on security/licensing distribution hold and is **not included as a publicly approved binary**.

Magazine is an evolving **beta prototype**, not a production-certified system. More features are planned; they'll be described when they're ready.

Read [current limitations](LIMITATIONS.md), [testing](TESTING.md) and [security notes](SECURITY.md) before using it with important or untrusted data.
