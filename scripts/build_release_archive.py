#!/usr/bin/env python3
"""Validate the publication allowlist and optionally build a deterministic ZIP."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import re
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "release" / "publication-manifest.json"
if not MANIFEST_PATH.exists():
    MANIFEST_PATH = ROOT / "release" / "function-only-manifest.json"
FIXED_TIME = (2026, 1, 1, 0, 0, 0)
REQUIRED = {
    "LICENSE",
    "docs/INSTALL.md",
    "docs/LICENSING.md",
    "integrations/open_webui/function/magazine_ef.py",
    "integrations/open_webui/function/magazine-function.json",
    "release/embedded-core-manifest.json",
}
SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"(?i)(?:api[_-]?key|access[_-]?token)\s*[:=]\s*['\"][A-Za-z0-9_\-]{24,}"),
    re.compile(rb"\bsk-[A-Za-z0-9]{20,}\b"),
)


def matches(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def selected_files(manifest: dict) -> list[Path]:
    selected: set[Path] = set()
    for pattern in manifest["include"]:
        for path in ROOT.glob(pattern):
            if path.is_symlink():
                raise SystemExit(f"refusing symlink in release selection: {path.relative_to(ROOT)}")
            if path.is_file():
                selected.add(path)
    files = sorted(
        (path for path in selected
         if not matches(path.relative_to(ROOT).as_posix(), manifest["exclude"])),
        key=lambda item: item.relative_to(ROOT).as_posix(),
    )
    relative = {path.relative_to(ROOT).as_posix() for path in files}
    if manifest.get("distribution_kind") == "function-only":
        forbidden = [p for p in relative if p.startswith("integrations/open_webui/frontend/")
                     or p.endswith((".tar", ".tgz", ".whl"))]
        if forbidden:
            raise SystemExit("Function-only profile contains a bundled host/dependency artifact: " + ", ".join(sorted(forbidden)))
    required = REQUIRED | set(manifest.get("required", []))
    missing = sorted(required - relative)
    if missing:
        raise SystemExit("publication manifest omitted required files: " + ", ".join(missing))
    return files


def verify_content(files: list[Path], manifest: dict) -> None:
    for path in files:
        data = path.read_bytes()
        for pattern in SECRET_PATTERNS:
            if pattern.search(data):
                raise SystemExit(f"possible credential in publication file: {path.relative_to(ROOT)}")
    function = (ROOT / "integrations/open_webui/function/magazine_ef.py").read_text(encoding="utf-8")
    version = manifest["release"].removeprefix("Magazine-").removesuffix("-FunctionOnly")
    if f'_FUNCTION_VERSION = "{version}"' not in function or f'version: {version}\n' not in function:
        raise SystemExit("publication version does not match Function metadata")
    python_version = re.sub(r"-beta\.(\d+)$", r"b\1", version)
    metadata = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    if f'version = "{python_version}"' not in metadata:
        raise SystemExit("publication version does not match Python metadata")
    exported = json.loads((ROOT / "integrations/open_webui/function/magazine-function.json").read_text(encoding="utf-8"))
    if len(exported) != 1 or exported[0]["content"] != function:
        raise SystemExit("native Function JSON does not match source")
    if "host.docker.internal:3000" in function:
        raise SystemExit("legacy hard-coded callback remains in release Function")
    embedded = json.loads((ROOT / "release/embedded-core-manifest.json").read_text(encoding="utf-8"))
    expected = manifest["pins"]["embedded_core_sha256"]
    if embedded["archive_sha256"] != expected:
        raise SystemExit("publication manifest embedded-core hash does not match generated manifest")
    pinned_files = {
        "function_source_sha256": "integrations/open_webui/function/magazine_ef.py",
        "function_export_sha256": "integrations/open_webui/function/magazine-function.json",
        "sidebar_patch_sha256": "integrations/open_webui/frontend/patches/0001-magazine-sidebar-launcher.patch",
        "frontend_security_patch_sha256": "integrations/open_webui/frontend/patches/0002-frontend-parser-security.patch",
        "dockerfile_sha256": "integrations/open_webui/frontend/Dockerfile",
        "open_webui_license_sha256": "integrations/open_webui/frontend/licenses/OPEN_WEBUI_LICENSE.txt",
        "open_webui_license_history_sha256": "integrations/open_webui/frontend/licenses/OPEN_WEBUI_LICENSE_HISTORY.txt",
    }
    for key, relative in pinned_files.items():
        if key not in manifest["pins"]:
            continue
        actual = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        if actual != manifest["pins"][key]:
            raise SystemExit(f"publication hash mismatch for {relative}")


def build(files: list[Path], release: str) -> Path:
    output = ROOT / "dist" / f"{release}.zip"
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            relative = path.relative_to(ROOT).as_posix()
            info = zipfile.ZipInfo(f"{release}/{relative}", FIXED_TIME)
            info.create_system = 3
            info.external_attr = (0o100644 & 0xFFFF) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="write dist/<release>.zip")
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH,
                        help="publication profile manifest")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    files = selected_files(manifest)
    verify_content(files, manifest)
    total = sum(path.stat().st_size for path in files)
    print(f"publication allowlist: PASS ({len(files)} files, {total} bytes)")
    if args.write:
        output = build(files, manifest["release"])
        digest = hashlib.sha256(output.read_bytes()).hexdigest()
        print(f"archive: {output}")
        print(f"sha256: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
