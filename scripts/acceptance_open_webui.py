#!/usr/bin/env python3
"""Clean-install acceptance test for the Magazine-enabled Open WebUI image.

This script creates its own labelled container and named volume. It never
discovers, stops, restarts, or modifies any pre-existing Open WebUI container.
No Magazine run is created and no model endpoint is called.
"""

from __future__ import annotations

import argparse
import base64
import io
import hashlib
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FUNCTION_SOURCE = ROOT / "integrations" / "open_webui" / "function" / "magazine_ef.py"
LABEL = "io.magazine.acceptance=true"


def docker(*args: str, capture: bool = True) -> str:
    result = subprocess.run(
        ["docker", *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )
    return result.stdout.strip() if capture else ""


def request(base_url: str, path: str, *, token: str | None = None,
            method: str = "GET", body: object | None = None,
            expected: tuple[int, ...] = (200,)) -> tuple[int, object | str]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(base_url + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            status = response.status
            raw = response.read().decode("utf-8")
            content_type = response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        status = exc.code
        raw = exc.read().decode("utf-8")
        content_type = exc.headers.get("Content-Type", "")
    if status not in expected:
        raise AssertionError(f"{method} {path}: expected {expected}, got {status}: {raw[:500]}")
    if "json" in content_type:
        return status, json.loads(raw)
    return status, raw


def wait_healthy(base_url: str, timeout: int = 240) -> None:
    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        try:
            status, _ = request(base_url, "/health", expected=(200, 503))
            if status == 200:
                return
        except Exception as exc:  # service may not yet have bound the port
            last_error = str(exc)
        time.sleep(2)
    raise RuntimeError(f"Open WebUI did not become healthy: {last_error}")


def fixture_zip_b64() -> str:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "magazine.yaml",
            'schema_version: "1.0"\nname: Acceptance Fixture\ncartridges:\n'
            '  - id: first\n    model: acceptance\n    prompt_file: cartridges/first.md\n',
        )
        archive.writestr("cartridges/first.md", "This fixture is never executed.\n")
    return base64.b64encode(stream.getvalue()).decode("ascii")


def assert_launcher_assets(container: str) -> None:
    code = (
        "from pathlib import Path; "
        "b=b''.join(p.read_bytes() for p in Path('/app/build').rglob('*.js') if p.is_file()); "
        "need=[b'sidebar-magazine-button',b'/magazine',b'_blank']; "
        "missing=[x.decode() for x in need if x not in b]; "
        "assert not missing, 'missing compiled launcher markers: '+repr(missing)"
    )
    docker("exec", container, "python", "-c", code)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default="magazine-open-webui:0.1.0-beta.5")
    parser.add_argument("--keep", action="store_true", help="Keep the disposable container and volume")
    parser.add_argument("--function-only", action="store_true", help="Accept a stock Open WebUI image without a launcher")
    args = parser.parse_args()

    suffix = uuid.uuid4().hex[:10]
    container = f"magazine-acceptance-{suffix}"
    volume = f"magazine-acceptance-data-{suffix}"
    created_container = False
    created_volume = False

    try:
        docker("image", "inspect", args.image)
        docker("volume", "create", "--label", LABEL, volume)
        created_volume = True
        docker(
            "run", "-d", "--name", container, "--label", LABEL,
            "-p", "127.0.0.1::8080",
            "-e", "WEBUI_SECRET_KEY=magazine-acceptance-only-not-for-production",
            "-e", "ENABLE_SIGNUP=true",
            "-e", "OLLAMA_BASE_URL=",
            # Avoid unrelated Hugging Face update checks during an install
            # test. The official runtime image already contains its default
            # embedding assets; Magazine does not use them here.
            "-e", "RAG_EMBEDDING_MODEL_AUTO_UPDATE=false",
            "-v", f"{volume}:/app/backend/data",
            args.image,
        )
        created_container = True
        port = docker("port", container, "8080/tcp").rsplit(":", 1)[-1]
        base_url = f"http://127.0.0.1:{port}"
        wait_healthy(base_url)

        _, admin = request(base_url, "/api/v1/auths/signup", method="POST", body={
            "name": "Magazine Acceptance Admin",
            "email": "admin@example.invalid",
            "password": "Acceptance-Only-Password-1!",
            "profile_image_url": "/user.png",
        })
        admin_token = admin["token"]
        assert admin["role"] == "admin"

        _, member = request(
            base_url, "/api/v1/auths/add", token=admin_token, method="POST", body={
                "name": "Magazine Acceptance User",
                "email": "user@example.invalid",
                "password": "Acceptance-Only-Password-2!",
                "profile_image_url": "/user.png",
                "role": "user",
            },
        )
        member_token = member["token"]
        assert member["role"] == "user"

        source = FUNCTION_SOURCE.read_text(encoding="utf-8")
        _, created = request(
            base_url, "/api/v1/functions/create", token=admin_token, method="POST", body={
                "id": "magazine",
                "name": "Magazine",
                "content": source,
                "meta": {"description": "Magazine public beta acceptance installation"},
            },
        )
        assert created["type"] == "event" and created["is_active"] is False
        _, enabled = request(
            base_url, "/api/v1/functions/id/magazine/toggle",
            token=admin_token, method="POST", body={},
        )
        assert enabled["is_active"] is True

        request(base_url, "/magazine", expected=(401, 403))
        for token in (admin_token, member_token):
            _, page = request(base_url, "/magazine", token=token)
            assert isinstance(page, str) and "<h1>Magazine" in page
            assert "sidebar-new-chat-button" not in page

        _, admin_library = request(base_url, "/api/magazine/library", token=admin_token)
        _, member_library = request(base_url, "/api/magazine/library", token=member_token)
        assert admin_library["can_import"] is True
        assert member_library["can_import"] is False
        request(
            base_url, "/api/magazine/library/import", token=member_token,
            method="POST", body={"file_name": "denied.zip", "file_data_b64": "", "project_id": "denied"},
            expected=(403,),
        )

        original_zip_b64 = fixture_zip_b64()
        original_zip_sha256 = hashlib.sha256(base64.b64decode(original_zip_b64)).hexdigest()
        _, imported = request(
            base_url, "/api/magazine/library/import", token=admin_token, method="POST", body={
                "file_name": "acceptance-fixture.zip",
                "file_data_b64": original_zip_b64,
                "project_id": "acceptance-fixture",
            },
            expected=(201,),
        )
        assert imported["imported"] is True
        assert imported["input_zip"]["retained"] is True
        assert imported["input_zip"]["sha256"] == original_zip_sha256
        retained_name = imported["input_zip"]["file"]
        assert Path(retained_name).name == retained_name and "/" not in retained_name
        def assert_original_zip():
            docker("exec", container, "python", "-c",
                   "from open_webui.config import DATA_DIR; from pathlib import Path; import hashlib; "
                   "data=(Path(DATA_DIR)/'magazine'/'input-zips'/" + repr(retained_name) + ").read_bytes(); "
                   "assert hashlib.sha256(data).hexdigest()==" + repr(original_zip_sha256))
        assert_original_zip()
        _, listed = request(base_url, "/api/magazine/library", token=member_token)
        assert any(item["workflow_id"] == "acceptance-fixture" for item in listed["workflows"])

        if not args.function_only:
            assert_launcher_assets(container)

        # Restart only the uniquely named, labelled acceptance container. The
        # same volume and secret prove database, Function, and library survival.
        docker("restart", container)
        # Docker Desktop may reassign an ephemeral published port on restart.
        port = docker("port", container, "8080/tcp").rsplit(":", 1)[-1]
        base_url = f"http://127.0.0.1:{port}"
        wait_healthy(base_url)
        deadline = time.monotonic() + 90
        while True:
            try:
                _, page = request(base_url, "/magazine", token=member_token)
                if isinstance(page, str) and "<h1>Magazine" in page:
                    break
            except AssertionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(2)
        _, persisted = request(base_url, "/api/magazine/library", token=member_token)
        assert any(item["workflow_id"] == "acceptance-fixture" for item in persisted["workflows"])
        _, installed = request(base_url, "/api/v1/functions/id/magazine", token=admin_token)
        assert installed["is_active"] is True
        assert installed["meta"]["manifest"]["version"] == "0.1.0-beta.5"
        assert_original_zip()

        print(json.dumps({
            "result": "PASS",
            "image": args.image,
            "container": container,
            "volume": volume,
            "checks": [
                "clean database startup", "Function import and activation",
                "anonymous authentication rejection", "authenticated standalone page",
                "admin-only library import", "persistent library and Function after restart",
                "exact original ZIP retention in default persistent DATA_DIR before/after restart",
                "Function-only stock image" if args.function_only else "compiled new-tab launcher markers",
                "no run creation or inference",
            ],
        }, indent=2))
        return 0
    finally:
        if args.keep:
            print(f"Kept acceptance resources: {container}, {volume}", file=sys.stderr)
        else:
            if created_container:
                # Refuse cleanup if the exact object lost its acceptance label.
                if docker("inspect", container, "--format", "{{ index .Config.Labels \"io.magazine.acceptance\" }}") == "true":
                    docker("rm", "-f", container)
            if created_volume:
                if docker("volume", "inspect", volume, "--format", "{{ index .Labels \"io.magazine.acceptance\" }}") == "true":
                    docker("volume", "rm", volume)


if __name__ == "__main__":
    raise SystemExit(main())
