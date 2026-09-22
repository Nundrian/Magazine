"""Model-ID discovery for documented Open WebUI endpoints."""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from magazine.preflight import (
    extract_chat_terminal_ids,
    extract_model_ids,
    extract_tool_ids,
    validate_model_runtime_meta,
)

TIMEOUT_SECONDS = 10


class _NoRedirect(HTTPRedirectHandler):
    """Do not forward the API key to a redirect target."""

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None

def _base_url(raw: str) -> str:
    """Validate and normalise *raw* to an absolute http/https base URL."""
    value = raw.strip().rstrip("/")
    try:
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError("must be an absolute http:// or https:// URL")
        if parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("must not contain credentials, a query, or a fragment")
        _ = parts.port  # Reject malformed port values before making a request.
    except ValueError as exc:
        raise ValueError(f"base_url {exc}") from exc
    return value

def discover_model_ids(base_url: str, api_key: str) -> list[str]:
    """Discover model IDs from the Open WebUI ``/api/models`` endpoint.

    Parameters
    ----------
    base_url : str
        Absolute http:// or https:// base URL of the Open WebUI instance.
    api_key : str
        Bearer API key used for authentication.

    Returns
    -------
    list of str
        The model IDs exactly as provided by the server.

    Raises
    ------
    ValueError
        If *base_url* or *api_key* is invalid, the request fails, the
        response body is not valid JSON, or the response shape is
        malformed (validated via :func:`extract_model_ids`).
    """
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError("api_key must be a non-empty string")

    url = _base_url(base_url) + "/api/models"

    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    request = Request(url, headers=headers, method="GET")

    try:
        with build_opener(_NoRedirect()).open(request, timeout=TIMEOUT_SECONDS) as response:
            body = response.read()
    except HTTPError as exc:
        raise ValueError(f"request failed: HTTP {exc.code} {exc.reason}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise ValueError(f"request failed: {type(exc).__name__}: {exc}") from exc

    try:
        payload = json.loads(body)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc

    return extract_model_ids(payload)

def discover_tool_ids(base_url: str, api_key: str) -> list[str]:
    """Discover tool IDs from the Open WebUI ``/api/v1/tools`` endpoint.

    Parameters
    ----------
    base_url : str
        Absolute http:// or https:// base URL of the Open WebUI instance.
    api_key : str
        Bearer API key used for authentication.

    Returns
    -------
    list of str
        The tool IDs exactly as provided by the server.

    Raises
    ------
    ValueError
        If *base_url* or *api_key* is invalid, the request fails, the
        response body is not valid JSON, or the response shape is
        malformed (validated via :func:`extract_tool_ids`).
    """
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError("api_key must be a non-empty string")

    url = _base_url(base_url) + "/api/v1/tools/"

    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    request = Request(url, headers=headers, method="GET")

    try:
        with build_opener(_NoRedirect()).open(request, timeout=TIMEOUT_SECONDS) as response:
            body = response.read()
    except HTTPError as exc:
        raise ValueError(f"request failed: HTTP {exc.code} {exc.reason}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise ValueError(f"request failed: {type(exc).__name__}: {exc}") from exc

    try:
        payload = json.loads(body)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc

    return extract_tool_ids(payload)

def discover_terminal_ids(base_url: str, api_key: str) -> list[str]:
    """Discover terminal IDs from the Open WebUI ``/api/v1/terminals/`` endpoint.

    Parameters
    ----------
    base_url : str
        Absolute http:// or https:// base URL of the Open WebUI instance.
    api_key : str
        Bearer API key used for authentication.

    Returns
    -------
    list of str
        The terminal IDs where ``contexts.chat`` is ``True``, exactly as
        provided by the server.

    Raises
    ------
    ValueError
        If *base_url* or *api_key* is invalid, the request fails, the
        response body is not valid JSON, or the response shape is
        malformed (validated via :func:`extract_chat_terminal_ids`).
    """
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError("api_key must be a non-empty string")

    url = _base_url(base_url) + "/api/v1/terminals/"

    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    request = Request(url, headers=headers, method="GET")

    try:
        with build_opener(_NoRedirect()).open(request, timeout=TIMEOUT_SECONDS) as response:
            body = response.read()
    except HTTPError as exc:
        raise ValueError(f"request failed: HTTP {exc.code} {exc.reason}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise ValueError(f"request failed: {type(exc).__name__}: {exc}") from exc

    try:
        payload = json.loads(body)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc

    return extract_chat_terminal_ids(payload)

def discover_model_runtime_meta(base_url: str, api_key: str, model_id: str, required_tool_id: str) -> dict:
    """Fetch and validate a model's runtime metadata from the Open WebUI
    ``/api/v1/models/model`` endpoint.

    Parameters
    ----------
    base_url : str
        Absolute http:// or https:// base URL of the Open WebUI instance.
    api_key : str
        Bearer API key used for authentication.
    model_id : str
        The model ID whose metadata is to be fetched.
    required_tool_id : str
        A tool ID that must be present exactly in the returned
        ``meta["toolIds"]``.

    Returns
    -------
    dict
        The validated payload unchanged.

    Raises
    ------
    ValueError
        If *base_url* or *api_key* is invalid, the request fails, the
        response body is not valid JSON, or the payload is invalid for
        *required_tool_id* (validated via :func:`validate_model_runtime_meta`).
    """
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError("api_key must be a non-empty string")
    if not isinstance(model_id, str) and not isinstance(model_id, bytes):
        raise ValueError("model_id must be a string or bytes")
    if not isinstance(required_tool_id, str) and not isinstance(required_tool_id, bytes):
        raise ValueError("required_tool_id must be a string or bytes")

    url = _base_url(base_url) + "/api/v1/models/model"
    params = urlencode({"id": model_id})
    request_url = url + "?" + params

    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    request = Request(request_url, headers=headers, method="GET")

    try:
        with build_opener(_NoRedirect()).open(request, timeout=TIMEOUT_SECONDS) as response:
            body = response.read()
    except HTTPError as exc:
        raise ValueError(f"request failed: HTTP {exc.code} {exc.reason}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise ValueError(f"request failed: {type(exc).__name__}: {exc}") from exc

    try:
        payload = json.loads(body)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc

    validate_model_runtime_meta(payload, required_tool_id)
    return payload