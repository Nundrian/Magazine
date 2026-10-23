"""Regression checks for the archives.2 correctness/performance repairs."""

import errno
from types import SimpleNamespace

import pytest

import magazine_ef as function


def test_library_root_does_not_mask_body_io_error(tmp_path):
    with pytest.raises(OSError) as caught:
        with function._library_root_fd(str(tmp_path)):
            raise OSError(errno.ENOSPC, "disk full")
    assert caught.value.errno == errno.ENOSPC


def test_catalogue_drops_source_valves_and_secret_fields():
    safe = function._public_catalog_item(
        {
            "id": "tool-a",
            "name": "Tool A",
            "description": "Does a task",
            "content": "private source",
            "valves": {"secret": "hidden"},
        }
    )
    assert safe == {"id": "tool-a", "name": "Tool A", "description": "Does a task"}


def test_empty_model_mapping_is_not_admitted():
    document = {
        "schema_version": "1.0",
        "cartridges": [{"id": "a", "job_type": "worker", "prompt": "Do a task"}],
    }
    errors = function._validate_admission(document, {"worker": ""}, [], ["any-model"], [])
    assert errors and "No AI model selected" in errors[0]


def test_failed_core_bootstrap_can_retry(monkeypatch):
    saved = function._MAG.copy()
    function._MAG.clear()
    calls = []

    def bootstrap():
        calls.append(True)
        if len(calls) == 1:
            raise OSError("transient")
        return {"ok": True, "version": "recovered"}

    monkeypatch.setattr(function, "_ensure_magazine", bootstrap)
    try:
        assert function._mag()["ok"] is False
        assert function._mag()["version"] == "recovered"
        assert len(calls) == 2
    finally:
        function._MAG.clear()
        function._MAG.update(saved)


def test_running_archive_status_does_not_read_saved_results_or_verify(monkeypatch):
    calls = []

    class Store:
        def inspect(self, ids):
            raise AssertionError("RUNNING status must not scan full archive")

    monkeypatch.setattr(
        function,
        "_archive_context",
        lambda owner, run, include_results=True: (
            calls.append(include_results) or Store(),
            [{"id": "a"}, {"id": "b"}],
            {"status": "RUNNING"},
        ),
    )
    result = function._archive_status("owner", "run")
    assert result["status"] == "IN_PROGRESS"
    assert result["expected"] == 2
    assert calls == [False]


def test_failed_export_does_not_overwrite_a_completed_record(monkeypatch):
    exported = {"status": "EXPORTED", "revision": 4}
    writes = []

    def fail(*_args):
        raise TimeoutError("another exporter owns the lock")

    monkeypatch.setattr(function, "_export_run", fail)
    monkeypatch.setattr(function, "_export_record", lambda *_args: exported)
    monkeypatch.setattr(function, "_save_export_record", lambda *_args: writes.append(True))
    assert function._attempt_export("owner", "run") == exported
    assert not writes
