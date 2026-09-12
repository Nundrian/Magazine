"""Read-only connectivity probe for documented Open WebUI endpoints."""

from __future__ import annotations

import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

BASE_URL_ENV = "MAGAZINE_OPEN_WEBUI_BASE_URL"
API_KEY_ENV = "MAGAZINE_OPEN_WEBUI_API_KEY"
TIMEOUT_SECONDS = 10


class _NoRedirect(HTTPRedirectHandler):
    """Do not forward the API key to a redirect target."""

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _base_url(raw: str) -> str:
    value = raw.strip().rstrip("/")
    try:
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError("must be an absolute http:// or https:// URL")
        if parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("must not contain credentials, a query, or a fragment")
        _ = parts.port  # Reject malformed port values before making a request.
    except ValueError as exc:
        raise ValueError(f"{BASE_URL_ENV} {exc}") from exc
    return value


def _get(base_url: str, path: str, api_key: str | None = None) -> tuple[int | None, bytes | None, str | None]:
    headers = {"Accept": "application/json"}
    if api_key is not None:
        headers["Authorization"] = f"Bearer {api_key}"
    request = Request(base_url + path, headers=headers, method="GET")
    try:
        with build_opener(_NoRedirect()).open(request, timeout=TIMEOUT_SECONDS) as response:
            return response.status, response.read(), None
    except HTTPError as exc:
        return None, None, f"HTTP {exc.code} {exc.reason}"
    except (URLError, TimeoutError, OSError) as exc:
        return None, None, f"{type(exc).__name__}: {exc}"


def _listing(base_url: str, path: str, api_key: str, label: str) -> bool:
    status, body, error = _get(base_url, path, api_key)
    if error:
        print(f"{label} ({path}): FAILED ({error})")
        return False
    try:
        payload = json.loads(body)
    except (TypeError, ValueError) as exc:
        print(f"{label} ({path}): FAILED (invalid JSON: {exc})")
        return False
    items = payload.get("data") if label == "Models" and isinstance(payload, dict) else payload
    if not isinstance(items, list):
        expected = "an object with a data array" if label == "Models" else "an array"
        print(f"{label} ({path}): FAILED (unexpected response shape; expected {expected})")
        return False
    if any(not isinstance(item, dict) or not isinstance(item.get("id"), str) for item in items):
        print(f"{label} ({path}): FAILED (one or more entries lack a string id)")
        return False
    print(f"{label} ({path}): OK (HTTP {status}; {len(items)} available)")
    for item in items:
        name = item.get("name")
        suffix = f" — {name}" if isinstance(name, str) and name != item["id"] else ""
        print(f"  - {item['id']}{suffix}")
    return True


def main() -> int:
    raw_base_url = os.environ.get(BASE_URL_ENV)
    api_key = os.environ.get(API_KEY_ENV)
    missing = [name for name, value in ((BASE_URL_ENV, raw_base_url), (API_KEY_ENV, api_key)) if not value]
    if missing:
        print(f"Configuration missing: {', '.join(missing)}", file=sys.stderr)
        return 2
    try:
        base_url = _base_url(raw_base_url)
    except ValueError as exc:
        print(f"Configuration invalid: {exc}", file=sys.stderr)
        return 2

    print(f"Open WebUI base URL: {base_url}")
    status, _, error = _get(base_url, "/health")
    health_ok = error is None
    print(f"Service health (/health): {'OK (HTTP ' + str(status) + ')' if health_ok else 'FAILED (' + error + ')'}")
    models_ok = _listing(base_url, "/api/models", api_key, "Models")
    tools_ok = _listing(base_url, "/api/v1/tools/", api_key, "Workspace tools")
    print(f"Authenticated API connectivity: {'SUCCEEDED' if models_ok or tools_ok else 'FAILED'}")
    full_success = health_ok and models_ok and tools_ok
    print(f"Full probe: {'SUCCEEDED' if full_success else 'FAILED'}")
    return 0 if full_success else 1


if __name__ == "__main__":
    raise SystemExit(main())
