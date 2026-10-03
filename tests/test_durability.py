"""Crash-durability tests for the shared primitive in ``magazine.durability``.

These tests use fault injection (monkeypatched ``os.fsync`` / ``os.replace`` /
``os.open``) rather than a real power cut, to prove the explicit durability
protocol: file fsync *before* ``os.replace``, directory fsync *after* it,
temporary-file cleanup on any failure, exception propagation, and the
documented no-op degradation on platforms without directory fsync
(e.g. Windows).
"""

from __future__ import annotations

import errno
import os
import sys

import pytest

import magazine
from magazine.durability import (
    _probe_directory_fsync_support,
    atomic_write,
    directory_fsync_supported,
    fsync_directory,
)


def _assert_protocol(events, target_dir):
    """Assert the captured protocol events are in durable order:

    file fsync (on the temp file's open fd) -> ``os.replace`` ->
    directory fsync (on the O_RDONLY fd of the target's directory).
    (The file fd is closed before the directory open, so fd numbers may be
    reused; identity is proven via the open events, not fd-number inequality.)
    """
    kinds = [e[0] for e in events]
    first_fsync = kinds.index("fsync")
    i_replace = kinds.index("replace")
    last_fsync = len(kinds) - 1 - kinds[::-1].index("fsync")
    assert first_fsync < i_replace < last_fsync, f"protocol order violated: {events}"
    fsync_fds = [e[1] for e in events if e[0] == "fsync"]
    assert len(fsync_fds) == 2, (
        f"expected exactly file + directory fsync, got events: {events}"
    )
    # The file fd is closed before the directory open, so fd numbers may be
    # reused; identity is proven via the open events, not fd-number inequality.
    # The first fsync must be on the fd that opened the temp file (O_EXCL).
    tmp_open = next(
        (e for e in events if e[0] == "open" and e[2] & os.O_EXCL),
        "no O_EXCL temp-file open found",
    )
    assert fsync_fds[0] == tmp_open[3], (
        "file fsync must be on the fd that opened the temp file"
    )
    dir_opens = [e for e in events if e[0] == "open" and e[2] == os.O_RDONLY]
    assert len(dir_opens) == 1, f"expected one O_RDONLY directory open, got: {events}"
    assert dir_opens[0][1] == target_dir, "directory fsync must be on the target's directory"
    assert (
        fsync_fds[1] == dir_opens[0][3],
        "the directory fsync fd must be the O_RDONLY open of the containing directory",
    )


def _fake_protocol_hooks(monkeypatch, events):
    """Install spies on os.replace / os.open / os.fsync, forwarding to the
    real calls so the write actually succeeds, and log every protocol event.
    Callers must pre-warm ``directory_fsync_supported()`` first so the (cached)
    capability probe does not pollute the log."""
    real_replace = os.replace
    real_open = os.open

    def fake_replace(src, dst):
        events.append(("replace", src, dst))
        return real_replace(src, dst)

    def fake_open(path, flags, *a, **kw):
        fd = real_open(path, flags, *a, **kw)
        events.append(("open", path, flags, fd))
        return fd

    def fake_fsync(fd):
        events.append(("fsync", fd))

    monkeypatch.setattr(os, "replace", fake_replace)
    monkeypatch.setattr(os, "open", fake_open)
    monkeypatch.setattr(os, "fsync", fake_fsync)


def test_support_probe_returns_cached_bool():
    """The capability is probed and cached: every call is a bool and the
    probe does not re-run (repeated calls are cheap and stable)."""
    supported = directory_fsync_supported()
    assert isinstance(supported, bool)
    assert directory_fsync_supported() is supported


def test_support_probe_detects_linux_support():
    """On the current Linux runtime the probe must report directory fsync
    support (real fsync of a scratch directory works there)."""
    if sys.platform != "linux":
        pytest.skip("not the Linux runtime used for the main suite")
    assert directory_fsync_supported() is True


# --- capability probe: controlled error classification ----------------------


def _probe_with_errno(errno_code, monkeypatch):
    """Run the *real* capability probe (``_probe_directory_fsync_support``) with
    ``os.fsync`` forced to fail with a chosen ``errno``.

    The probe is invoked directly (bypassing the capability cache) and the
    cached ``_directory_fsync_support`` is reset to ``None`` first, so the
    actual error-classification branch is exercised with controlled fault
    injection -- deterministic and independent of the host filesystem.  Only
    the ``fsync`` step is faulted; the surrounding ``mkdtemp`` / ``os.open`` /
    ``rmtree`` are the real (POSIX-safe) operations.
    """
    monkeypatch.setattr(magazine.durability, "_directory_fsync_support", None)

    def boom(fd):
        raise OSError(errno_code, "fsync fault")

    monkeypatch.setattr(os, "fsync", boom)
    return _probe_directory_fsync_support()


def test_probe_success_is_supported(monkeypatch):
    """A *successful* directory fsync probe classifies the platform as
    supporting directory fsync (True).  Deterministic via a monkeypatched no-op
    fsync (no real fsync), independent of the host filesystem."""
    monkeypatch.setattr(magazine.durability, "_directory_fsync_support", None)
    monkeypatch.setattr(os, "fsync", lambda fd: None)
    assert _probe_directory_fsync_support() is True


def test_probe_enosys_is_unsupported(monkeypatch):
    """ENOSYS (function not implemented by the filesystem) means directory
    fsync is unavailable -> the documented no-op degradation path (False)."""
    assert _probe_with_errno(errno.ENOSYS, monkeypatch) is False


def test_probe_enotdir_is_unsupported(monkeypatch):
    """ENOTDIR (operand is not a directory) means directory fsync is
    unavailable (False)."""
    assert _probe_with_errno(errno.ENOTDIR, monkeypatch) is False


def test_probe_enotty_is_unsupported(monkeypatch):
    """ENOTTY (operand not fsync-able as a directory fd) means directory fsync
    is unavailable (False)."""
    assert _probe_with_errno(errno.ENOTTY, monkeypatch) is False


def test_probe_eio_is_supported(monkeypatch):
    """A genuine I/O fault (EIO) means the platform *does* support directory
    fsync but the probe hit a real operational error -> classified supported
    (True), so a real directory fsync failure is surfaced later as an error
    rather than being converted into the unsupported no-op."""
    assert _probe_with_errno(errno.EIO, monkeypatch) is True


def test_probe_eacces_is_supported(monkeypatch):
    """EACCES (permission denied) is likewise a genuine fault on a supported
    platform -> classified supported (True)."""
    assert _probe_with_errno(errno.EACCES, monkeypatch) is True


def test_atomic_write_roundtrip(tmp_path):
    target = str(tmp_path / "out.txt")
    payload = b"crash-durable payload"
    atomic_write(target, payload)
    assert open(target, "rb").read() == payload


def test_atomic_write_relative_target_temp_in_cwd(tmp_path, monkeypatch):
    """Backward-compat: with a relative target the temp file is created in the
    CWD so the atomic swap stays on the same filesystem (unchanged contract)."""
    target = "state.json"
    real_replace = os.replace
    calls = []

    def spy(src, dst):
        calls.append((src, dst))
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    old_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        atomic_write(target, b"x")
        src = calls[0][0]
        assert os.path.dirname(src) == str(tmp_path)
        assert (tmp_path / "state.json").read_bytes() == b"x"
    finally:
        os.chdir(old_cwd)


def test_protocol_file_fsync_before_replace_dir_fsync_after(
    tmp_path, monkeypatch,
):
    """The shared primitive runs: file fsync BEFORE os.replace, directory
    fsync AFTER it.  This is the crash-consistency fix: a successful return
    means both the new contents and the swap are on stable storage."""
    target = str(tmp_path / "out.txt")
    durability_pre = magazine.durability  # keep the real module handle for pre-warm
    durability_pre.directory_fsync_supported()  # pre-warm the capability probe
    events = []
    _fake_protocol_hooks(monkeypatch, events)
    atomic_write(target, b"payload")
    _assert_protocol(events, os.path.dirname(target))
    assert (tmp_path / "out.txt").read_bytes() == b"payload"


def test_file_fsync_failure_raises_no_temp_no_target(tmp_path, monkeypatch):
    """A failed file fsync (data durability, step 1) raises, unlinks the
    temp, and never swaps -- the target is never created with partial data."""
    target = str(tmp_path / "out.txt")
    magazine.durability.directory_fsync_supported()
    def boom(fd):
        raise OSError("no room for data")

    monkeypatch.setattr(os, "fsync", boom)
    with pytest.raises(OSError, match="no room for data"):
        atomic_write(target, b"payload")
    assert not os.path.exists(target)
    assert not any(p.suffix == ".tmp" for p in tmp_path.iterdir())


def test_file_fsync_failure_leaves_old_content_intact(tmp_path, monkeypatch):
    """Same, with an existing target: the previous contents survive the
    failed save (no silent corruption)."""
    target = str(tmp_path / "out.txt")
    magazine.durability.directory_fsync_supported()
    with open(target, "wb") as f:
        f.write(b"old-data")
    def boom(fd):
        raise OSError("no room for data")

    monkeypatch.setattr(os, "fsync", boom)
    with pytest.raises(OSError, match="no room for data"):
        atomic_write(target, b"new-data")
    assert open(target, "rb").read() == b"old-data"
    assert not any(p.suffix == ".tmp" for p in tmp_path.iterdir())


def test_dir_fsync_failure_raises_after_swap(tmp_path, monkeypatch):
    """On a supported platform a failed directory fsync (step 2, post-swap)
    also raises -- no silent success.  Post-swap semantics: the destination
    holds the new, file-fsynced data and the caller must treat the save as
    incomplete and retry."""
    target = str(tmp_path / "out.txt")
    magazine.durability.directory_fsync_supported()
    real_open = os.open
    dir_fd = {}

    def fake_open(path, flags, *a, **kw):
        fd = real_open(path, flags, *a, **kw)
        if flags == os.O_RDONLY:
            dir_fd["fd"] = fd
        return fd

    def fake_fsync(fd):
        if fd == dir_fd.get("fd"):
            raise OSError("directory fsync failed")

    monkeypatch.setattr(os, "open", fake_open)
    monkeypatch.setattr(os, "fsync", fake_fsync)
    with pytest.raises(OSError, match="directory fsync failed"):
        atomic_write(target, b"new-data")
    # Post-swap: destination holds the new (file-fsynced) data; temp is gone.
    assert open(target, "rb").read() == b"new-data"
    assert not any(p.suffix == ".tmp" for p in tmp_path.iterdir())


def test_unsupported_dirfsync_is_file_fsync_only_noop(tmp_path, monkeypatch):
    """Platform-degradation path (e.g. Windows): the directory fsync is a
    documented no-op, the save still succeeds, and only the file is fsync'd.
    Simulated by forcing the cached capability flag to False."""
    magazine.durability.directory_fsync_supported()
    monkeypatch.setattr(magazine.durability, "_directory_fsync_support", False)
    fsyncs = []
    monkeypatch.setattr(os, "fsync", lambda fd: fsyncs.append(fd))
    target = str(tmp_path / "out.txt")
    atomic_write(target, b"payload")
    assert len(fsyncs) == 1, "on unsupported platforms only the file is fsynced"
    assert open(target, "rb").read() == b"payload"


def test_exception_identity_propagates_unchanged(tmp_path, monkeypatch):
    """Failure exceptions escape with the same identity (store callers rely
    on this, e.g. test_result_store failure-identity tests)."""
    target = str(tmp_path / "out.txt")

    class _UniqueOSError(OSError):
        pass

    failure = _UniqueOSError("unique failure identity")

    def boom(a, b):
        raise failure

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(_UniqueOSError) as excinfo:
        atomic_write(target, b"x")
    assert excinfo.value is failure


def test_fsync_directory_fsyncs_directory_fd_when_supported(tmp_path, monkeypatch):
    """Where supported, fsync_directory opens the directory O_RDONLY and
    fsyncs that fd (nothing else)."""
    magazine.durability.directory_fsync_supported()
    real_open = os.open
    opened = []
    fsynced = []

    def fake_open(path, flags, *a, **kw):
        fd = real_open(path, flags, *a, **kw)
        opened.append((path, flags, fd))
        return fd

    monkeypatch.setattr(os, "open", fake_open)
    monkeypatch.setattr(os, "fsync", lambda fd: fsynced.append(fd))
    fsync_directory(str(tmp_path))
    assert len(opened) == 1 and opened[0][1] == os.O_RDONLY, opened
    assert len(fsynced) == 1 and fsynced[0] == opened[0][2]


def test_fsync_directory_noop_when_unsupported(tmp_path, monkeypatch):
    """Where unsupported (capability flag False), fsync_directory is a no-op:
    no fsync and not even a directory open."""
    monkeypatch.setattr(magazine.durability, "_directory_fsync_support", False)
    fsyncs = []
    opens = []

    def fail_open(*a, **kw):
        opens.append((a, kw))
        raise OSError("must not open when unsupported")

    monkeypatch.setattr(os, "open", fail_open)
    monkeypatch.setattr(os, "fsync", lambda fd: fsyncs.append(fd))
    fsync_directory(str(tmp_path))
    assert fsyncs == []
    assert opens == []


def test_fsync_directory_raises_on_failure_when_supported(tmp_path, monkeypatch):
    """On a supported platform a failing directory fsync propagates (never
    swallowed)."""
    magazine.durability.directory_fsync_supported()
    def boom(fd):
        raise OSError("fsync error")

    monkeypatch.setattr(os, "fsync", boom)
    with pytest.raises(OSError, match="fsync error"):
        fsync_directory(str(tmp_path))
