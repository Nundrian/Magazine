# 0.1.0-beta.5 candidate

Dedicated original Input ZIP retention after successful library import, with
explicit partial-success warnings if storage cannot retain the upload.
No automatic directory imports or backfill of historical uploads.

Automatic owner/project/run-specific Finished Work export on COMPLETED:
canonical UTF-8 cartridge text, machine-readable manifest and ZIP. Explicit
owner-only export/retry endpoint also supports completed portions of HALTED runs.
Repeat export verifies identical files; it never overwrites unrelated work.
Storage failures have their own record/warning and cannot change execution state.
Linux and Docker Desktop Windows no-overwrite publication paths are tested.

New optional container environment variables: `MAGAZINE_INPUT_ZIPS_DIR` and
`MAGAZINE_FINISHED_WORK_DIR`. Persistent defaults stay under `DATA_DIR/magazine/`.
See `STORAGE.md` for Windows/Linux mounts, ownership/backup/retrieval and the
proposed safe upgrade retaining the actual existing container configuration.

Project ID/name metadata is verified against the selected library's ordered
IDs/prompts, preserving existing configured model-routing keys and permission
gate order. No new dependency, UI redesign or download button. External tool
files and chat archives are not falsely claimed as exported artifacts.

All 29 core files and the embedded core ZIP are byte-identical to beta.4.
Execution, five-field canonical state, persistence, STOP, recovery and tool/model
permission mechanisms are unchanged. The full source package now includes its
Function-only publication manifest so both packaged profile checks are runnable.

Custom frontend security/licensing binary distribution HOLD remains unchanged.
This release review did not perform publication or a live deployment. See
`ACCEPTANCE.md` for fresh test results versus inherited beta.4 evidence.
