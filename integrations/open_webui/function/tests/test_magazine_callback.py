"""Regression tests for the portable same-server callback configuration."""

import inspect

import pytest

import magazine_ef as runner


def test_default_callback_is_same_container_loopback():
    assert runner._default_open_webui_base_url({}) == "http://127.0.0.1:8080"


@pytest.mark.parametrize(
    ("port", "expected"),
    [
        ("9090", "http://127.0.0.1:9090"),
        ("0", "http://127.0.0.1:8080"),
        ("65536", "http://127.0.0.1:8080"),
        ("not-a-port", "http://127.0.0.1:8080"),
    ],
)
def test_default_callback_honours_only_valid_open_webui_port(port, expected):
    assert runner._default_open_webui_base_url({"PORT": port}) == expected


def test_explicit_callback_override_wins_over_port():
    env = {
        "PORT": "9090",
        "MAGAZINE_OPEN_WEBUI_BASE_URL": "https://open-webui.internal:8443/",
    }
    assert runner._default_open_webui_base_url(env) == env["MAGAZINE_OPEN_WEBUI_BASE_URL"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("http://127.0.0.1:8080", "http://127.0.0.1:8080"),
        ("https://open-webui.internal/", "https://open-webui.internal"),
        (" HTTP://localhost:8080/ ", "http://localhost:8080"),
        ("http://[::1]:8080", "http://[::1]:8080"),
    ],
)
def test_callback_normalization_accepts_absolute_http_urls(raw, expected):
    assert runner._normalize_open_webui_base_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "/relative",
        "ftp://localhost:8080",
        "http://user:secret@localhost:8080",
        "http://localhost:8080/subpath",
        "http://localhost:8080?query=yes",
        "http://localhost:8080/#fragment",
        "http://localhost:99999",
    ],
)
def test_callback_normalization_rejects_unsafe_or_ambiguous_values(raw):
    with pytest.raises(ValueError):
        runner._normalize_open_webui_base_url(raw)


def test_route_validates_callback_before_durable_admission():
    source = inspect.getsource(runner._install_routes)
    validation = source.index("base_url = configured_open_webui_base_url()")
    admission = source.index('prepared = m["rp"].prepare_run')
    assert validation < admission


def test_event_exposes_and_passes_callback_valve():
    event = runner.Event()
    assert event.valves.open_webui_base_url == runner._default_open_webui_base_url()
    source = inspect.getsource(runner.Event.event)
    assert "open_webui_base_url=lambda: self.valves.open_webui_base_url" in source
