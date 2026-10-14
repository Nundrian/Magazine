# Sidebar image distribution blockers

The two named parser corrections are implemented and the local image works.
Binary publication is still HOLD. The following findings have distinct evidence
and closure requirements; they do not hold Magazine's independent source-only
Function archive.

## Runtime security

| Component | Production relationship | Remaining decision |
| --- | --- | --- |
| aiohttp 3.13.5, CVE-2026-69244 | Native `routers/openai.py` creates client sessions for provider responses; malformed upstream responses can reach its C parser | Supported patch to 3.14.3 or vendor workaround needs dependency/runtime/STOP acceptance. No exploit was run. |
| pypdf 6.7.5; multiple parser DoS findings | Retrieval `loaders/main.py` selects `PyPDFLoader` for PDF inputs | Validate patched supported parser and document-loading compatibility, or establish an enforced feature restriction. |
| Pillow 12.2.0; native parser findings | Inherited image/document features carry the package | Format/path-specific reachability and supported correction remain unverified. |
| Tiptap core 3.20.2, GHSA-cp6q-959q-f8rh | Custom rich-text image extension calls `mergeAttributes` when building DOM attributes | Establish whether attacker-controlled attribute objects survive the schema. Vendor patch is 3.30.4; no Open WebUI exploit is established. |
| Tiptap core GHSA-j95f-988m-3j2f | Advisory concerns Markdown helper functions, not all editor input | Source search found no direct calls to the named helpers. Transitive/configuration reachability remains to be proven; do not label every text input vulnerable. |
| ChromaDB, NLTK, unstructured, remote loaders | Optional retrieval/vector/document features in the runtime | Distinguish local client use from exposed services and assess enabled configurations. Installed packages alone do not prove exposure. |

Sources: [aiohttp vendor advisory](https://github.com/aio-libs/aiohttp/security/advisories/GHSA-cq5v-8q36-5273),
[Tiptap attributes](https://github.com/ueberdosis/tiptap/security/advisories/GHSA-cp6q-959q-f8rh),
[Tiptap Markdown](https://github.com/ueberdosis/tiptap/security/advisories/GHSA-j95f-988m-3j2f).
Package versions/findings come from the retained scanner inventories. The full
inventory also contains OS/native library findings that have not all received
feature-specific review. This table is not an exhaustive security clearance.

Kernel-header matches do not describe a running container kernel. Tested amd64
does not execute the flagged 32-bit Perl path. Vitest and Node SSR/Vite server
advisories concern paths absent from the static FastAPI deployment. Build-time
dependencies still need supply-chain review. Duplicate uv/uvx inventories and
package/CVE aliases must not be counted as independent exploits.

## Binary licences and corresponding sources

The inherited licence inventory includes concrete examples below. Mixed scanner
expressions need checking against actual component notices before deciding exact
obligations.

| Examples in pinned runtime | Inventory licence | Missing release material |
| --- | --- | --- |
| bash 5.2.15-2+b13; coreutils 9.1-1; git 1:2.39.5-0+deb12u3 | GPL families and mixed notices | Exact Debian source package revisions, downstream patches and build materials paired with distributed binaries |
| ffmpeg 7:5.1.9-0+deb12u1; native LGPL libraries | LGPL/GPL mixed expressions | Actual configured codecs/linkage, applicable corresponding sources and replacement/relinking conditions |
| fpdf2 2.8.7; ldap3 2.9.1; autocommand 2.2.2 | LGPL-3.0 | Exact source distributions and required notices matched to installed contents |
| bidict 0.24.1; certifi 2026.7.22 | MPL-2.0 | Matching covered source, any modifications and notice describing how recipients obtain it |
| Generated frontend and Pyodide assets | Multiple dependency licences | Reconciled asset inventory, third-party copyright/NOTICE collection and required sources |

Preserved upstream Open WebUI licence/history and branding cover only that
component's requirements. Magazine MIT and an SPDX SBOM cannot replace the
remaining obligations. Relevant licence texts include the installed Debian
common licences and [MPL 2.0 §3.2–3.4](https://www.mozilla.org/en-US/MPL/2.0/).
No unverified written source offer is supplied by this candidate.

## Release decisions

The native package distributes none of the binaries or frontend code above.
Its host risks are disclosed in `SECURITY.md`. The complete source candidate
retains the launcher, pins and tested parser patch for continued review.
Do not distribute a customized image until both the actual runtime security
decision and complete notice/source package are validated. New installations
requiring the permanent sidebar remain blocked by that gate.
