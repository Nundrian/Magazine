# Security and distribution gates

The beta.4 core is byte-identical to accepted beta.3; Function changes are version
metadata and regenerated embedded ZIP compression. Executable logic is unchanged.
Authenticated ownership, model/tool permissions, strict package
validation, filesystem boundaries and terminal STOP remain enforced.
Functions run server-side Python; review trusted source before installation.
Dispatch credentials stay in memory. Installation starts no inference.

## Function distribution

The native archive contains Magazine source/JSON and no Open WebUI frontend,
binary image, wheels or third-party tarballs. Embedded bytes match canonical
source. Direct requirements are HTTPX 0.28.1/PyYAML 6.0.3.
Beta.3's historical offline scan found no direct matches; this is not proof of
absence of vulnerabilities. Fresh verification is recorded in acceptance.

The supported stock Open WebUI 0.11.3 host retains the parser advisories below.
Installing Magazine alone does not patch it. Operators should review upstream
security and avoid untrusted clipboard HTML/workbooks on an affected host.
Magazine's standalone text interface does not load those parsers.
This package does not certify the entire host against hostile inputs.

## Optional frontend correction

| Affected production path | Pinned correction |
| --- | --- |
| Tiptap HTML clipboard paste, prosemirror-view 1.41.6 | view 1.42.3 and required model dependency |
| FileNav/TerminalOutputFile workbook preview, ODS text extraction, xlsx 0.18.5 | vendor SheetJS CE 0.20.3 with lockfile integrity |

[ProseMirror's advisory](https://github.com/ProseMirror/prosemirror-view/security/advisories/GHSA-c8x8-7fp4-3x9w)
fixes paste XSS at 1.42.3.
SheetJS fixes [prototype pollution](https://cdn.sheetjs.com/advisories/CVE-2023-30533)
at 0.19.3 and [ReDoS](https://cdn.sheetjs.com/advisories/CVE-2024-22363) at 0.20.2.
The [official install guide](https://docs.sheetjs.com/docs/getting-started/installation/nodejs/)
documents the authoritative CDN 0.20.3 URL. Parser compatibility tests run
before bundling. They do not constitute exhaustive exploit testing.

Inspected upstream 0.11.4 still locks view 1.41.6 and xlsx 0.18.5; a rebase does
not close these blockers. The correction changes only those parsers, the model
dependency required by view, and obsolete SheetJS children.

## Customized image HOLD

Beta.3's raw 5,316 matches include duplicates, headers and tooling; they are not
exploit counts. Kernel headers do not establish a running guest kernel risk.
32-bit Perl is inapplicable to tested amd64. Vitest test servers and Node
SSR/Vite servers are absent from the static production path. Observed HS256
sessions do not use flagged public-key JWT paths. Other configurations are not
cleared by those observations.

Remaining findings need feature/path review, especially native document/image
parsers, retrieval/vector services and remote data loaders. Do not dismiss them
as a group or apply broad automatic upgrades. Actual candidate inventory and
outstanding source/notice obligations are recorded in acceptance/licensing.
Binary distribution remains HOLD until both gates close independently.
