#!/usr/bin/env python3
"""Build or verify the canonical Magazine core embedded in the Event Function."""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import re
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORE_ROOT = ROOT / "magazine"
FUNCTION_PATH = ROOT / "integrations" / "open_webui" / "function" / "magazine_ef.py"
EXPORT_PATH = FUNCTION_PATH.with_name("magazine-function.json")
MANIFEST_PATH = ROOT / "release" / "embedded-core-manifest.json"
ARCHIVE_PATTERN = re.compile(
    r'(?P<prefix>_MAG_ZIP_B64 = """\n)(?P<body>.*?)(?P<suffix>\n""")',
    re.DOTALL,
)
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)


def core_files() -> list[Path]:
    files = sorted(path for path in CORE_ROOT.rglob("*.py") if "__pycache__" not in path.parts)
    if not files:
        raise RuntimeError(f"no Python core files found below {CORE_ROOT}")
    return files


def build_archive(files: list[Path]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            relative = path.relative_to(ROOT).as_posix()
            info = zipfile.ZipInfo(relative, FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return output.getvalue()


def encoded_archive(source: str) -> bytes:
    match = ARCHIVE_PATTERN.search(source)
    if match is None:
        raise RuntimeError(f"embedded archive marker not found in {FUNCTION_PATH}")
    return base64.b64decode("".join(match.group("body").split()), validate=True)


def manifest(files: list[Path], archive_bytes: bytes) -> dict:
    return {
        "format": 1,
        "archive_sha256": hashlib.sha256(archive_bytes).hexdigest(),
        "file_count": len(files),
        "files": [
            {
                "path": path.relative_to(ROOT).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size": path.stat().st_size,
            }
            for path in files
        ],
    }


def render_manifest(value: dict) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def render_function_export(source: str) -> str:
    version = re.search(r'^_FUNCTION_VERSION = "([^"]+)"', source, re.MULTILINE)
    if version is None:
        raise RuntimeError("Function version marker not found")
    return json.dumps([{
        "id": "magazine", "name": "Magazine", "content": source,
        "meta": {"description": f"Magazine {version.group(1)} — workflow runner"},
    }], indent=2, ensure_ascii=False) + "\n"


def write_release(files: list[Path], archive_bytes: bytes) -> None:
    source = FUNCTION_PATH.read_text(encoding="utf-8")
    encoded = base64.b64encode(archive_bytes).decode("ascii")
    wrapped = "\n".join(encoded[index:index + 76] for index in range(0, len(encoded), 76))
    updated, count = ARCHIVE_PATTERN.subn(
        lambda match: match.group("prefix") + wrapped + match.group("suffix"),
        source,
        count=1,
    )
    if count != 1:
        raise RuntimeError("expected exactly one embedded archive")
    FUNCTION_PATH.write_text(updated, encoding="utf-8", newline="\n")
    EXPORT_PATH.write_text(render_function_export(updated), encoding="utf-8", newline="\n")
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(render_manifest(manifest(files, archive_bytes)), encoding="utf-8", newline="\n")


def verify_release(files: list[Path], archive_bytes: bytes) -> list[str]:
    errors: list[str] = []
    source = FUNCTION_PATH.read_text(encoding="utf-8")
    if not EXPORT_PATH.exists():
        errors.append("missing native JSON Function import")
    elif EXPORT_PATH.read_text(encoding="utf-8") != render_function_export(source):
        errors.append("native JSON Function import is stale")
    embedded = encoded_archive(source)
    if embedded != archive_bytes:
        errors.append("Event Function embedded archive is not the deterministic canonical-core build")
    expected_manifest = render_manifest(manifest(files, archive_bytes))
    if not MANIFEST_PATH.exists():
        errors.append(f"missing {MANIFEST_PATH.relative_to(ROOT)}")
    elif MANIFEST_PATH.read_text(encoding="utf-8") != expected_manifest:
        errors.append("embedded-core manifest is stale")
    with zipfile.ZipFile(io.BytesIO(embedded)) as archive:
        actual_names = sorted(archive.namelist())
        expected_names = [path.relative_to(ROOT).as_posix() for path in files]
        if actual_names != expected_names:
            errors.append("embedded archive file list does not match the canonical core")
        else:
            for path, name in zip(files, expected_names):
                if archive.read(name) != path.read_bytes():
                    errors.append(f"embedded bytes differ: {name}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="regenerate the embedded archive and manifest")
    mode.add_argument("--check", action="store_true", help="verify the generated files without modifying them")
    args = parser.parse_args()

    files = core_files()
    archive_bytes = build_archive(files)
    if args.write:
        write_release(files, archive_bytes)
        print(f"embedded {len(files)} files; sha256={hashlib.sha256(archive_bytes).hexdigest()}")
        return 0
    errors = verify_release(files, archive_bytes)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"verified {len(files)} embedded files; sha256={hashlib.sha256(archive_bytes).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
