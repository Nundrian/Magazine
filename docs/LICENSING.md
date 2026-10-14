# Licensing and redistribution

## Native Function and Magazine source

Magazine's core, Function, interface and release scripts use the root MIT
`LICENSE`. Retain its copyright and permission notice. The embedded ZIP contains
29 canonical Magazine files and no third-party package source. Native JSON
contains exact Function source and public metadata only.

The Function imports installed Open WebUI APIs and declares external Python
requirements; it distributes neither Open WebUI nor those packages.
HTTPX 0.28.1 uses BSD-3-Clause and PyYAML 6.0.3 uses MIT. Package installers
retain their notices; see [HTTPX](https://github.com/encode/httpx/tree/0.28.1)
and [PyYAML](https://github.com/yaml/pyyaml/tree/6.0.3).

The Function-only archive excludes customized images, frontend patches and
generated Open WebUI assets. Binary corresponding-source obligations therefore
do not hold this source distribution. This conclusion concerns package contents;
it does not clear the operator's separate Open WebUI installation.

## Customized Open WebUI image: HOLD

The recipe derives from Open WebUI 0.11.3 commit
`2a960a59fe1dbbd35282f0556b3666d81102e781` and an exact runtime digest.
Licence/history are retained in `integrations/open_webui/frontend/licenses/`
and copied into the image. Retain upstream copyright, conditions, disclaimer
and branding; do not imply endorsement. The sidebar preserves native branding.
See [pinned licence](https://github.com/open-webui/open-webui/blob/2a960a59fe1dbbd35282f0556b3666d81102e781/LICENSE).
Magazine's MIT licence does not relicense Open WebUI.

ProseMirror is MIT and SheetJS CE 0.20.3 is Apache-2.0. Minification does not
remove attribution obligations. The image also inherits Debian binaries,
Python wheels/native libraries, Node/uv and generated frontend/Pyodide assets.
Its SPDX inventory identifies GPL/LGPL/MPL and other components.

A binary release needs a component-to-artifact matrix, required copyright/
licence/NOTICE texts, and matching source/build material where required.
GPL corresponding source, LGPL source/relinking or replacement conditions and
MPL file-level source must be assessed for actual components. An SBOM, generic
upstream links or an unverified written offer cannot establish compliance.

Specific unresolved gaps hold binary distribution:

- Complete corresponding sources are not matched to all inherited copyleft binaries.
- Frontend/Pyodide notices and source coverage are not reconciled with final assets.
- The final container notices have not been validated against that source bundle.

The source candidate retains pinned recipes, identities and patches for review.
It is not an approved binary release. The publisher must also confirm rights
in contributed Magazine code.
