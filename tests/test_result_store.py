"""Focused unit tests for magazine.result_store (stage-4 durable-output
primitive).

Exercises the public boundary :mod:`magazine.result_store` in isolation:
the path convention, an exact UTF-8 atomic write, Unicode/multiline
round-trip, non-string coercion (matching the ``object()`` sentinels that
the orchestration happy-path tests feed through), re-dispatch overwrite,
on-demand creation of ``results/``, and atomic failure (no partial/corrupt
target, no orphaned temp file, exception propagated unchanged).
"""

from __future__ import annotations

from pathlib import Path

import pytest

import hashlib
import os

from magazine import result_store


# --- exact UTF-8 with Unicode, multi-byte chars, and multiple newlines ----
_UNICODE_PAYLOAD = (
    "\u4e2d\u6587\u4e86\u8f41\u671f\u3042\u3061\u3045\u3063\u300d"  # CJK + CJK + CJK
    "\n\u2029\u2028line separator + paragraph separator\n"
    "\U0001F600 emoji\n"
    "\u00a0nbsp\u00e9e-acute\u00f7o-tilde\n\n"
    "final line, no trailing newline"
)


def _read_bytes(path: Path) -> bytes:
    return path.read_bytes()


def test_result_path_returns_deterministic_convention(tmp_path: Path) -> None:
    """result_path derives <run_dir>/results/<cartridge_id>.txt exactly."""
    run_dir = str(tmp_path)
    assert result_store.result_path(run_dir, "cart-b") == str(
        tmp_path / "results" / "cart-b.txt"
    )
    assert result_store.result_path(run_dir, "cart-b") == os.path.join(
        run_dir, "results", "cart-b.txt"
    )


def test_save_cartridge_result_writes_exact_utf8_bytes(tmp_path: Path) -> None:
    """The exact bytes of the model result are persisted and returned at the
    canonical path."""
    text = "plain ASCII line"
    path = result_store.save_cartridge_result(
        str(tmp_path), "cart-a", text,
    )
    assert path == str(tmp_path / "results" / "cart-a.txt")
    assert Path(path).read_bytes() == text.encode("utf-8")
    # Return value equals the path derivation (caller can rely on it).
    assert path == result_store.result_path(str(tmp_path), "cart-a")


def test_save_cartridge_result_unicode_multiline_round_trip(tmp_path: Path) -> None:
    """Unicode (multi-byte CJK / emoji / accents) and multi-line text survive
    a full write/round-trip byte-for-byte (UTF-8, binary write, no newline
    translation)."""
    run_dir = str(tmp_path)
    result_store.save_cartridge_result(run_dir, "cart-u", _UNICODE_PAYLOAD)
    target = tmp_path / "results" / "cart-u.txt"
    # Round-trip: decode the stored bytes and compare with the exact source
    # string.
    stored_str = target.read_bytes().decode("utf-8")
    assert stored_str == _UNICODE_PAYLOAD
    # And byte-for-byte: the encoded payload matches exactly.
    assert _read_bytes(target) == _UNICODE_PAYLOAD.encode("utf-8")
    # Verify the specific line breaks are present (multi-line preserved).
    assert "\n" in stored_str
    assert "\u2029" in stored_str
    assert "\u2028" in stored_str
    assert "\U0001F600" in stored_str


def test_save_cartridge_result_non_str_sentinel_coerced(tmp_path: Path) -> None:
    """A non-string result (the ``object()`` sentinel the orchestration
    happy-path tests pass through) is coerced with str() and persists without
    error.  The real ``str`` contract is preserved byte-for-byte; only the
    non-str path is exercised here."""
    run_dir = str(tmp_path)
    sentinel = object()
    result_store.save_cartridge_result(run_dir, "cart-s", sentinel)
    target = tmp_path / "results" / "cart-s.txt"
    assert target.read_bytes() == str(sentinel).encode("utf-8")
    # A str value is written at the canonical path.
    assert result_store.save_cartridge_result(
        run_dir, "cart-str", "exact str",
    ) == str(tmp_path / "results" / "cart-str.txt")


def test_save_cartridge_result_creates_results_dir_on_demand(tmp_path: Path) -> None:
    """A first write is self-contained: the results directory is created
    automatically (no pre-existing directory needed)."""
    run_dir = str(tmp_path)
    assert not (tmp_path / "results").exists()
    result_store.save_cartridge_result(run_dir, "cart-d", "x")
    assert (tmp_path / "results").exists()
    assert (tmp_path / "results" / "cart-d.txt").exists()


def test_save_cartridge_result_overwrites_on_redispatch(tmp_path: Path) -> None:
    """A re-dispatched cartridge overwrites its own deterministic file (a
    single canonical path per cartridge; the latest dispatch result wins)."""
    run_dir = str(tmp_path)
    result_store.save_cartridge_result(run_dir, "cart-r", "v1")
    assert (tmp_path / "results" / "cart-r.txt").read_bytes() == b"v1"
    result_store.save_cartridge_result(run_dir, "cart-r", "v2")
    assert (tmp_path / "results" / "cart-r.txt").read_bytes() == b"v2"


def test_save_cartridge_result_sequential_distinct_cartridges_distinct_files(
    tmp_path: Path,
) -> None:
    """Two sequential cartridges produce two distinct files (no collision, no
    overwriting each other)."""
    run_dir = str(tmp_path)
    result_store.save_cartridge_result(run_dir, "cart-x", "xx")
    result_store.save_cartridge_result(run_dir, "cart-y", "yy")
    assert (tmp_path / "results" / "cart-x.txt").read_bytes() == b"xx"
    assert (tmp_path / "results" / "cart-y.txt").read_bytes() == b"yy"
    # Exactly two files, distinct.
    assert set((tmp_path / "results").iterdir()) == {
        tmp_path / "results" / "cart-x.txt",
        tmp_path / "results" / "cart-y.txt",
    }


def test_save_cartridge_result_failure_leaves_no_target_and_no_temp(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """If the atomic replace fails, no target remains (no partial/corrupt
    file), no orphaned temporary file remains, and the exception propagates
    unchanged (OSError)."""

    def _raise(src, dst):
        raise OSError("replace failed")

    # result_store imports os and calls os.replace; patch the same attribute.
    monkeypatch.setattr(os, "replace", _raise)
    run_dir = str(tmp_path)
    with pytest.raises(OSError, match="replace failed"):
        result_store.save_cartridge_result(run_dir, "cart-f", "partial")
    # No target file (no partial/corrupt write survived).
    assert not (tmp_path / "results" / "cart-f.txt").exists()
    # No orphaned temp file in results/.
    temps = [
        p for p in (tmp_path / "results").iterdir()
        if p.name.startswith(".result.") and p.name.endswith(".tmp")
    ]
    assert temps == []


def test_save_cartridge_result_failure_propagates_exception_identity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """The exact exception instance from the write path reaches the caller
    (no wrapping / re-raising / conversion by any boundary)."""
    class _UniqueOSError(OSError):
        pass

    failure = _UniqueOSError("unique failure identity")

    def _raise_failure(exc):
        raise exc

    monkeypatch.setattr(
        os, "replace", lambda a, b: _raise_failure(failure),
    )

    run_dir = str(tmp_path)
    with pytest.raises(_UniqueOSError) as excinfo:
        result_store.save_cartridge_result(run_dir, "cart-i", "x")
    assert excinfo.value is failure


# --- path safety: cartridge IDs are logical, untrusted identifiers ----------


def _assert_in_results(path: str, run_dir: Path) -> None:
    """The path must resolve to a child of <run_dir>/results (the invariant)."""
    resolved = Path(path).resolve()
    root = (Path(run_dir) / "results").resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise AssertionError(f"escaped results/: {resolved} ! in {root}") from exc


def _no_subdirs_under_results(path: str, run_dir: Path) -> None:
    """There must be no subdirectory beneath <run_dir>/results."""
    results = os.path.join(str(run_dir), "results")
    prefix = results + os.sep
    assert path.startswith(prefix), f"path not under {results}: {path}"
    assert path[len(prefix):].count(os.sep) == 0, f"subdir under {results}: {path}"


def test_result_path_normal_cartridge_id_is_in_results(tmp_path: Path) -> None:
    """A normal (practical) cartridge ID maps to a readable, in-results file."""
    run_dir = str(tmp_path)
    path = result_store.result_path(run_dir, "cart-b")
    assert path == str(tmp_path / "results" / "cart-b.txt")
    _assert_in_results(path, tmp_path)
    _no_subdirs_under_results(path, tmp_path)


def test_result_path_dotdotdot_id_stays_in_results(tmp_path: Path) -> None:
    """IDs containing ``..`` / ``../`` must never escape ``results/``."""
    run_dir = str(tmp_path)
    for bad in ("..", "../", "../evil", "a/../b", "../cart-b", "..\\..\\..\\etc"):
        path = result_store.result_path(run_dir, bad)
        # The would-be escape target (run_dir or run_dir/evil) must NOT be hit.
        assert path != os.path.join(run_dir, "evil")
        assert path != os.path.join(run_dir, "evil.txt")
        _assert_in_results(path, tmp_path)
        _no_subdirs_under_results(path, tmp_path)
        # deterministic.
        assert result_store.result_path(run_dir, bad) == path


def test_result_path_id_with_path_separators_stays_in_results(
    tmp_path: Path,
) -> None:
    """Path separators inside an ID become a single component (no subdirs)."""
    run_dir = str(tmp_path)
    for bad in ("a/b", "a\\b", "a/b/c", "foo/../bar.txt", "x//y"):
        path = result_store.result_path(run_dir, bad)
        _assert_in_results(path, tmp_path)
        _no_subdirs_under_results(path, tmp_path)
        assert path.endswith(".txt")
        assert result_store.result_path(run_dir, bad) == path


def test_result_path_absolute_or_traversal_escape_neutralized(
    tmp_path: Path,
) -> None:
    """An absolute or deep traversal ID is neutralized inside ``results/``."""
    run_dir = str(tmp_path)
    for bad in (
        "/etc/passwd",
        "/abs/escape.txt",
        "../..",
        "..\\..\\windows\\..\\etc",
        "C:\\evil",
        "a/../b/../../etc/passwd",
    ):
        path = result_store.result_path(run_dir, bad)
        # Never an escape target; never a bare absolute path.
        assert path != "/etc/passwd"
        assert path != os.path.join(run_dir, "evil.txt")
        _assert_in_results(path, tmp_path)
        _no_subdirs_under_results(path, tmp_path)
        # deterministic.
        assert result_store.result_path(run_dir, bad) == path


def test_result_path_distinct_ids_distinct_deterministic_paths(
    tmp_path: Path,
) -> None:
    """Distinct practical IDs -> distinct deterministic paths; same ID -> same path."""
    run_dir = str(tmp_path)
    ids = ("cart-a", "cart-b", "cart-c", "job.1", "job-2", "a/b/c")
    paths = [result_store.result_path(run_dir, cid) for cid in ids]
    # deterministic: same ID -> same path.
    for cid, path in zip(ids, paths):
        assert result_store.result_path(run_dir, cid) == path
    # distinct IDs -> distinct paths (all practical/safe, no collisions).
    assert len(set(paths)) == len(ids)
    # each stays inside results/.
    for path in paths:
        _assert_in_results(path, tmp_path)
        _no_subdirs_under_results(path, tmp_path)


def test_result_path_containment_check_rejects_escape(
    tmp_path: Path, monkeypatch,
) -> None:
    """If the safe-naming ever produced a separator-bearing token, the
    containment check (not just the sanitizer) would reject the escape with a
    ValueError (the rigor the user asked for)."""
    import magazine.result_store as rs
    monkeypatch.setattr(rs, "_safe_file_name", lambda _id: "../escape")
    with pytest.raises(ValueError, match="escapes root"):
        rs.result_path(str(tmp_path), "anything")


def test_save_cartridge_result_hostile_id_stays_in_results(tmp_path: Path) -> None:
    """Even a hostile ID, when actually written, lands inside ``results/`` and
    the run dir itself is never touched."""
    for bad in ("../evil", "/etc/passwd"):
        dest = result_store.save_cartridge_result(str(tmp_path), bad, "payload")
        _assert_in_results(dest, tmp_path)
        _no_subdirs_under_results(dest, tmp_path)
        assert Path(dest).read_bytes() == b"payload"
        # No leak outside results/.
        assert not (tmp_path / "evil").exists()
        assert not (tmp_path / "evil.txt").exists()


# --- collision safety: sanitisation must be injective over distinct IDs -----


def test_result_path_injective_for_previously_colliding_pairs(
    tmp_path: Path,
) -> None:
    """Distinct cartridge IDs that the pre-fix scheme collapsed to a single
    token (``"a/b"`` / ``"a b"`` / ``"a_b"`` -> ``"a_b"``) must now map to
    distinct, deterministic, in-results paths."""
    run_dir = str(tmp_path)
    ids = ("a/b", "a b", "a_b")
    paths = [result_store.result_path(run_dir, cid) for cid in ids]
    # distinct IDs -> distinct paths (the pre-fix collision is gone).
    assert len(set(paths)) == len(ids)
    # deterministic: same ID -> same path.
    for cid, path in zip(ids, paths):
        assert result_store.result_path(run_dir, cid) == path
    for path in paths:
        _assert_in_results(path, tmp_path)
        _no_subdirs_under_results(path, tmp_path)


def test_colliding_ids_persist_to_independent_files(tmp_path: Path) -> None:
    """Regression: the historical collision, where two distinct cartridge IDs
    wrote to the same ``<token>.txt`` and one result overwrote the other.
    Both results must now survive independently, byte-exact, in distinct files."""
    run_dir = str(tmp_path)
    pair = ("a/b", "a_b")
    payloads = {"a/b": "slash-payload", "a_b": "underscore-payload"}
    for cid in pair:
        result_store.save_cartridge_result(run_dir, cid, payloads[cid])

    # The two IDs must land on distinct files.
    p_slash = result_store.result_path(run_dir, "a/b")
    p_underscore = result_store.result_path(run_dir, "a_b")
    assert p_slash != p_underscore
    assert Path(p_slash).name != Path(p_underscore).name

    # Each result survived, byte-exact, in its own file (no overwriting).
    assert Path(p_slash).read_bytes() == payloads["a/b"].encode("utf-8")
    assert Path(p_underscore).read_bytes() == payloads["a_b"].encode("utf-8")

    # Exactly two files exist; nothing was clobbered.
    assert set((tmp_path / "results").iterdir()) == {Path(p_slash), Path(p_underscore)}


def test_mixed_safe_and_unsafe_ids_never_collide(tmp_path: Path) -> None:
    """A safe ID and an unsafe ID that would have collapsed to the same token
    (e.g. ``"x_y"`` vs ``"x y"`` / ``"x/y"``) must never land on the same
    file, and a mixed batch of distinct IDs must all be distinct."""
    run_dir = str(tmp_path)
    ids = (
        "x y", "x/y", "a:b", "a/b", "a b",
        "x_y", "x-y", "x.y", "x", "x.", ".x",
    )
    paths = [result_store.result_path(run_dir, cid) for cid in ids]
    assert len(set(paths)) == len(ids)
    for cid, path in zip(ids, paths):
        assert result_store.result_path(run_dir, cid) == path
    for path in paths:
        _assert_in_results(path, tmp_path)
        _no_subdirs_under_results(path, tmp_path)


# --- cross-namespace: the reserved __sha256__ namespace must be exclusive ---


def test_unsafe_id_and_literal_digest_id_do_not_collide(tmp_path: Path) -> None:
    """Cross-namespace regression (pre-fix): an unsafe ID U hashed to a bare
    64-hex digest collided with the literal safe ID that is exactly that
    digest.  With the reserved ``__sha256__`` namespace the two must resolve
    to different files, both persisting independently."""
    run_dir = str(tmp_path)
    unsafe_id = "a/b"
    digest = hashlib.sha256(unsafe_id.encode("utf-8")).hexdigest()
    assert len(digest) == 64 and digest.isalnum()  # a valid literal safe ID

    p_unsafe = result_store.result_path(run_dir, unsafe_id)
    p_digest = result_store.result_path(run_dir, digest)

    # The unsafe ID is fingerprinted into the reserved namespace.
    assert Path(p_unsafe).name.startswith("__sha256__")
    assert Path(p_unsafe).name == "__sha256__" + digest + ".txt"
    # The digest itself is an ordinary safe ID and stays readable.
    assert Path(p_digest).name == digest + ".txt"
    # They must no longer collide.
    assert p_unsafe != p_digest

    # Both results survive independently, byte-exact.
    payloads = {unsafe_id: "unsafe-payload", digest: "digest-payload"}
    for cid in (unsafe_id, digest):
        result_store.save_cartridge_result(run_dir, cid, payloads[cid])
    assert Path(p_unsafe).read_bytes() == payloads[unsafe_id].encode("utf-8")
    assert Path(p_digest).read_bytes() == payloads[digest].encode("utf-8")
    assert set((tmp_path / "results").iterdir()) == {Path(p_unsafe), Path(p_digest)}


def test_literal_reserved_prefix_id_is_reencoded_and_distinct(tmp_path: Path) -> None:
    """A literal, all-safe ID that begins with the reserved ``__sha256__``
    prefix must itself be fingerprinted (not returned unchanged), so it cannot
    occupy the hashed namespace and collide with the hash of another ID."""
    run_dir = str(tmp_path)
    unsafe_id = "a/b"
    digest = hashlib.sha256(unsafe_id.encode("utf-8")).hexdigest()
    reserved_literal = "__sha256__" + digest  # a valid safe cartridge ID

    p_unsafe = result_store.result_path(run_dir, unsafe_id)
    p_literal = result_store.result_path(run_dir, reserved_literal)

    # The reserved-prefix literal is re-encoded, never returned verbatim.
    assert Path(p_literal).name != reserved_literal + ".txt"
    assert Path(p_literal).name.startswith("__sha256__")
    # It must not collide with the unsafe ID's hash (the new failure mode).
    assert p_unsafe != p_literal
    assert Path(p_unsafe).name != Path(p_literal).name

    # Both results survive independently, byte-exact.
    payloads = {unsafe_id: "unsafe-payload", reserved_literal: "reserved-lit-payload"}
    for cid in (unsafe_id, reserved_literal):
        result_store.save_cartridge_result(run_dir, cid, payloads[cid])
    assert Path(p_unsafe).read_bytes() == payloads[unsafe_id].encode("utf-8")
    assert Path(p_literal).read_bytes() == payloads[reserved_literal].encode("utf-8")
    assert set((tmp_path / "results").iterdir()) == {Path(p_unsafe), Path(p_literal)}


def test_original_ab_vs_ab_still_fixed_and_safe_ids_unchanged(tmp_path: Path) -> None:
    """Guard: the original ``a/b`` vs ``a_b`` collision remains fixed, ordinary
    safe IDs keep their readable filenames, and all results persist."""
    run_dir = str(tmp_path)
    ids = ("a/b", "a_b", "cart-b", "job.1")
    paths = [result_store.result_path(run_dir, cid) for cid in ids]
    assert len(set(paths)) == len(ids)
    # ordinary safe IDs are untouched.
    assert Path(paths[2]).name == "cart-b.txt"
    assert Path(paths[3]).name == "job.1.txt"
    # the unsafe ID is in the reserved namespace.
    assert Path(paths[0]).name.startswith("__sha256__")
    payloads = {cid: "payload-for-" + cid.replace("/", "-") for cid in ids}
    for cid in ids:
        result_store.save_cartridge_result(run_dir, cid, payloads[cid])
    for cid, path in zip(ids, paths):
        assert Path(path).read_bytes() == payloads[cid].encode("utf-8")
    assert set((tmp_path / "results").iterdir()) == {Path(p) for p in paths}
