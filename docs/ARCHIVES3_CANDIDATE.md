# Open WebUI archives.3 stabilization candidate (review branch)

This branch contains a stabilization candidate for the Open WebUI Event Function.
It must be tested separately before replacing a running installation.

The bundled function remains compatible with Open WebUI 0.11.3; runtime compatibility
and long-run behavior require live verification.

It is based on the user-provided `0.1.0-beta.5+archives.2` Function and its
matching embedded 30-module Magazine core. It does not modify an installed
Function or any existing runs. The new build identifies itself as `archives.3`
so the active `archives.2` run can be diagnosed separately.

The targeted repairs include atomic metadata before READY publication, safer
dispatch admission, retained explicit tool grants, redacted catalogue responses,
bounded automatic poll retries, less synchronous disk activity during polling,
and an export error that cannot clobber an existing completed export record.
Shared-library import is administrator-only; authenticated non-admin users can
still browse the library and prepare their own work from its workflows. This
changes the permissive `archives.2` import policy and should be checked with
both an administrator and a non-admin test account.

The conversation archive's chat-deletion protection remains deliberately
fail-closed: only a verified, persisted conversation whose canonical assistant
output matches the completed cartridge result is eligible for deletion.

Live checks still required before deployment: a three-cartridge run, all three
download endpoints, interruption/Stop/recovery, multi-run isolation, and one
run lasting beyond the configured user-token lifetime. Do not hot-reload the
Event Function in the middle of an active Magazine run.

## Verification caveat

The embedded archive consistency check, UI tests, focused archive/dispatch
regressions and durable Stop tests pass in the isolated checkout. The full
Python suite cannot be reported green in this execution environment: its
Linux process-identity code intentionally rejects the environment's missing or
unreliable `/proc/<pid>/stat` data. Tests requiring a real identity (including
runtime/admission integration) must be repeated in the actual Linux Open WebUI
container before this candidate replaces a live installation. No production
identity fallback was introduced to mask this limitation.
