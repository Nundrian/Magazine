"""Durable operator Stop control, separate from the frozen run-state schema.

``control.json`` is stored beside ``state.json`` and is changed only while the
existing run lock is held.  A Stop request is durable before cancellation is
attempted.  Cancellation evidence and actual workflow termination are tracked
separately so a failed or timed-out server cancellation can never be reported
as a successful stop.
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from typing import Any

from magazine.durability import atomic_write
from magazine.execution_meta import (
    load_execution_metadata,
    save_execution_metadata,
    utc_now_iso,
)
from magazine.paths import execution_file_path
from magazine.run_lock import acquire_run_lock, release_run_lock
from magazine.runtime_identity import RuntimeIdentity
from magazine.schema import ExecutionOwnershipError, MagazineSchemaError
from magazine.state import STATUS_HALTED, STATUS_READY, STATUS_RUNNING
from magazine.state_store import load_run_state, save_run_state

__all__ = [
    "StopControlError",
    "StopRequested",
    "control_file_path",
    "load_stop_control",
    "persist_request_stop",
    "persist_cancellation_result",
    "persist_confirm_termination",
    "persist_stop_if_requested",
]

_FIELDS = frozenset({
    "version",
    "request",
    "requested_at",
    "cancellation_status",
    "cancellation_detail",
    "termination_status",
    "termination_method",
    "actual_at",
})
_CANCELLATION_STATUSES = frozenset({"NOT_ATTEMPTED", "CONFIRMED", "FAILED"})
_TERMINATION_STATUSES = frozenset({"REQUESTED", "CONFIRMED", "NOT_APPLIED"})


class StopControlError(MagazineSchemaError):
    """Raised when durable Stop metadata is malformed or inapplicable."""


class StopRequested(Exception):
    """Internal control-flow signal: the run was durably stopped pre-dispatch."""

    def __init__(self, state: dict):
        super().__init__("workflow Stop was requested")
        self.state = state


def control_file_path(state_path: str) -> str:
    return os.path.join(os.path.dirname(state_path) or os.getcwd(), "control.json")


def _validate(value: Any) -> dict:
    if not isinstance(value, dict):
        raise StopControlError("control.json must contain an object")
    if set(value) != _FIELDS:
        raise StopControlError("control.json fields do not match the supported schema")
    if value["version"] != 1 or value["request"] != "STOP":
        raise StopControlError("control.json has an unsupported version or request")
    if not isinstance(value["requested_at"], str) or not value["requested_at"]:
        raise StopControlError("control.json requested_at must be a non-empty string")
    if value["cancellation_status"] not in _CANCELLATION_STATUSES:
        raise StopControlError("control.json has an invalid cancellation_status")
    if value["termination_status"] not in _TERMINATION_STATUSES:
        raise StopControlError("control.json has an invalid termination_status")
    for name in ("cancellation_detail", "termination_method", "actual_at"):
        if value[name] is not None and not isinstance(value[name], str):
            raise StopControlError(f"control.json {name} must be a string or null")
    if value["termination_status"] != "REQUESTED":
        if not value["termination_method"] or not value["actual_at"]:
            raise StopControlError("resolved Stop requires method and actual_at")
    elif value["termination_method"] is not None or value["actual_at"] is not None:
        raise StopControlError("unresolved Stop cannot record method or actual_at")
    return value


def load_stop_control(state_path: str) -> dict | None:
    path = control_file_path(state_path)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        raise StopControlError(f"cannot load control.json: {exc}") from exc
    return _validate(value)


def _save(state_path: str, value: dict) -> dict:
    validated = _validate(value)
    data = (json.dumps(validated, indent=2, sort_keys=True) + "\n").encode("utf-8")
    atomic_write(
        control_file_path(state_path), data,
        prefix=".magazine_control_", suffix=".tmp",
    )
    return validated


def _claim(state_path: str, writer_attempt: int, writer_identity: RuntimeIdentity):
    claim = load_execution_metadata(execution_file_path(os.path.dirname(state_path)))
    if claim is None:
        raise ExecutionOwnershipError("cannot verify Stop ownership: no execution claim")
    if (
        claim.attempt != writer_attempt
        or claim.runtime_id != writer_identity.runtime_id
    ):
        raise ExecutionOwnershipError(
            "cannot verify Stop ownership: execution claim does not match writer"
        )
    return claim


def _locked(lock_path, owner_id, writer_identity):
    acquire_run_lock(
        lock_path, owner_id, writer_identity.runtime_id,
        writer_identity.pid, writer_identity.start_ticks,
    )


def _unlock(lock_path, owner_id, writer_identity):
    release_run_lock(
        lock_path, owner_id, writer_identity.runtime_id,
        writer_identity.pid, writer_identity.start_ticks,
    )


def persist_request_stop(
    state_path: str,
    lock_path: str,
    owner_id: str,
    writer_attempt: int,
    writer_identity: RuntimeIdentity,
) -> dict:
    """Durably and idempotently request Stop under the active claim fence."""
    _locked(lock_path, owner_id, writer_identity)
    try:
        _claim(state_path, writer_attempt, writer_identity)
        state = load_run_state(state_path)
        existing = load_stop_control(state_path)
        if existing is not None:
            return existing
        if state["status"] not in {STATUS_RUNNING, STATUS_READY}:
            raise StopControlError(
                f"cannot request Stop for a {state['status']} run"
            )
        return _save(state_path, {
            "version": 1,
            "request": "STOP",
            "requested_at": utc_now_iso(),
            "cancellation_status": "NOT_ATTEMPTED",
            "cancellation_detail": None,
            "termination_status": "REQUESTED",
            "termination_method": None,
            "actual_at": None,
        })
    finally:
        _unlock(lock_path, owner_id, writer_identity)


def persist_cancellation_result(
    state_path: str,
    lock_path: str,
    owner_id: str,
    writer_attempt: int,
    writer_identity: RuntimeIdentity,
    *,
    confirmed: bool,
    detail: str | None,
) -> dict:
    """Record server-cancellation evidence without claiming termination."""
    _locked(lock_path, owner_id, writer_identity)
    try:
        _claim(state_path, writer_attempt, writer_identity)
        control = load_stop_control(state_path)
        if control is None:
            raise StopControlError("cannot record cancellation without a Stop request")
        if control["cancellation_status"] == "CONFIRMED":
            return control
        updated = dict(control)
        updated["cancellation_status"] = "CONFIRMED" if confirmed else "FAILED"
        updated["cancellation_detail"] = detail
        return _save(state_path, updated)
    finally:
        _unlock(lock_path, owner_id, writer_identity)


def _settle_not_applied_locked(state_path: str, reason: str) -> None:
    """Resolve a Stop overtaken by completion/error, inside the caller's run lock.

    No new lock is taken here: the state transition and this sidecar update
    happen under the same verified writer/owner fence. CONFIRMED is never
    overwritten, and REQUESTED is the only status this helper can settle.
    """
    control = load_stop_control(state_path)
    if control is None or control["termination_status"] != "REQUESTED":
        return
    updated = dict(control)
    updated["termination_status"] = "NOT_APPLIED"
    updated["termination_method"] = reason
    updated["actual_at"] = utc_now_iso()
    _save(state_path, updated)


def persist_confirm_termination(
    state_path: str,
    lock_path: str,
    owner_id: str,
    writer_attempt: int,
    writer_identity: RuntimeIdentity,
    method: str,
) -> dict:
    """Confirm actual termination only after state.json is durably HALTED."""
    _locked(lock_path, owner_id, writer_identity)
    try:
        _claim(state_path, writer_attempt, writer_identity)
        state = load_run_state(state_path)
        control = load_stop_control(state_path)
        if control is None:
            raise StopControlError("cannot confirm termination without a Stop request")
        if control["termination_status"] == "CONFIRMED":
            return control
        if control["termination_status"] == "NOT_APPLIED":
            raise StopControlError("Stop was not applied: workflow ended independently")
        if state["status"] != STATUS_HALTED:
            raise StopControlError("cannot confirm termination before run is HALTED")
        updated = dict(control)
        updated["termination_status"] = "CONFIRMED"
        updated["termination_method"] = method
        updated["actual_at"] = utc_now_iso()
        return _save(state_path, updated)
    finally:
        _unlock(lock_path, owner_id, writer_identity)


def persist_stop_if_requested(
    state_path: str,
    lock_path: str,
    owner_id: str,
    writer_attempt: int,
    writer_identity: RuntimeIdentity,
    *,
    method: str,
) -> dict | None:
    """At a safe boundary, stop and retire the active claim if requested.

    Returns ``None`` when no request exists.  Otherwise state.json is first
    persisted as HALTED, then execution.json is retired, and only then is the
    control record marked CONFIRMED.  This ordering fails conservatively.
    """
    _locked(lock_path, owner_id, writer_identity)
    try:
        claim = _claim(state_path, writer_attempt, writer_identity)
        state = load_run_state(state_path)
        control = load_stop_control(state_path)
        if control is None:
            return None
        if state["status"] == STATUS_HALTED:
            return state
        if state["status"] not in {STATUS_RUNNING, STATUS_READY}:
            raise StopControlError(
                f"cannot apply Stop at a {state['status']} boundary"
            )
        halted = dict(state)
        halted["status"] = STATUS_HALTED
        halted["revision"] += 1
        save_run_state(state_path, halted)
        now = utc_now_iso()
        save_execution_metadata(
            execution_file_path(os.path.dirname(state_path)),
            replace(
                claim,
                halted_by=writer_identity.runtime_id,
                halted_at=now,
                halt_reason="operator-stop-" + method,
            ),
        )
        updated = dict(control)
        updated["termination_status"] = "CONFIRMED"
        updated["termination_method"] = method
        updated["actual_at"] = now
        _save(state_path, updated)
        return halted
    finally:
        _unlock(lock_path, owner_id, writer_identity)
