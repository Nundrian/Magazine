"""Stage 1 regression tests for the execution-safety defects found in review.

Written against c345c19 BEFORE any production change.  Each test is tagged:

  [FAILS NOW]   reproduces a demonstrated defect; must pass after the repair.
  [PROTECTIVE]  passes now; guards behaviour the repair must NOT weaken.

Contracts these tests encode (agreed in review):

  * Lock mutual exclusion: at most one of two concurrent acquirers may win,
    and the loser must fail closed with MagazineLockError (never a raw OSError).
  * A crash during lock creation must never leave a malformed lock behind.
  * A lock that is already malformed is NEVER auto-removed (ownership safety).
  * ``None`` / non-str dispatch results are invalid; they must not be stored.
  * Core stores valid str results byte-for-byte (no stripping).  Empty
    assistant content is rejected by the Open WebUI adapter, not the core.
  * Without /proc, ownership code must fail explicitly or safe, and a missing
    /proc must never be read as proof that a process is dead.

Process tests use fork + events/barriers, not sleeps, so they are
deterministic (forced interleaving) or invariant-based (stress).
"""

import asyncio
import multiprocessing
import os
import pathlib
import signal
import sys
import threading

try:
    import resource
except ImportError:  # pragma: no cover - module is Linux-only at execution.
    resource = None

import pytest

import magazine.run_lock as run_lock
import magazine.runtime_identity as runtime_identity
from magazine.async_runner import run_magazine
from magazine.run_lock import MagazineLockError, acquire_run_lock

import test_webui_dispatch as twd  # sibling test module: reuse adapter fakes

linux_only = pytest.mark.skipif(
    sys.platform != "linux", reason="uses fork and /proc (Linux-only by design)"
)

EXAMPLE = pathlib.Path(__file__).resolve().parents[1] / "examples" / "simple"
STALE = "ghost|rt-dead|99999999|1"  # lock content left by a dead process
TIMEOUT = 30


def _ctx():
    return multiprocessing.get_context("fork")


# --------------------------------------------------------------------------
# 1. Stale-lock takeover race                                  [FAILS NOW]
# --------------------------------------------------------------------------

def _attempt(path, owner, runtime, pid, ticks, q, extinct):
    if extinct is not None:
        run_lock.process_is_extinct = extinct
    try:
        acquire_run_lock(path, owner, runtime, pid, ticks)
        q.put((owner, "ok"))
    except BaseException as exc:  # report exact type, incl. raw OSError
        q.put((owner, type(exc).__name__))


def _collect(q, procs, n):
    hung = False
    for p in procs:
        p.join(TIMEOUT)
        if p.is_alive():
            hung = True
            p.terminate()
    assert not hung, "worker hung"
    return dict(q.get(timeout=5) for _ in range(n))


@linux_only
def test_stale_takeover_forced_interleaving_has_single_owner(tmp_path):
    """B opens the stale inode; A replaces it; B then inspects its old inode.

    B must notice that the path now names A's replacement and must not unlink
    it.  Exactly A may acquire ownership.
    """
    path = str(tmp_path / "run.lock")
    pathlib.Path(path).write_text(STALE)
    ctx = _ctx()
    q = ctx.Queue()
    b_opened_old_inode = ctx.Event()
    a_done = ctx.Event()

    def extinct_only_ghost(pid, ticks):
        return pid == 99999999

    def run_b():
        real_flock = run_lock.fcntl.flock
        delayed = False
        checked_pids = []

        def delay_first_inode_lock(fd, operation):
            nonlocal delayed
            if not delayed and operation & run_lock.fcntl.LOCK_EX:
                delayed = True
                # acquire_run_lock has already opened the old public-lock inode.
                b_opened_old_inode.set()
                a_done.wait(TIMEOUT)
            return real_flock(fd, operation)

        def record_extinction_check(pid, ticks):
            checked_pids.append(pid)
            return extinct_only_ghost(pid, ticks)

        run_lock.fcntl.flock = delay_first_inode_lock
        _attempt(path, "ownerB", "rtB", 222, 6, q, record_extinction_check)
        q.put(("ownerB-checked", checked_pids))

    def run_a():
        b_opened_old_inode.wait(TIMEOUT)
        _attempt(path, "ownerA", "rtA", 111, 5, q, extinct_only_ghost)
        a_done.set()

    pa = ctx.Process(target=run_a)
    pb = ctx.Process(target=run_b)
    pb.start(); pa.start()
    out = _collect(q, [pa, pb], 3)

    assert out["ownerA"] == "ok", out
    assert out["ownerB"] == "MagazineLockError", out
    assert out["ownerB-checked"] == [99999999], out
    assert pathlib.Path(path).read_text().startswith("ownerA|")


@linux_only
def test_owner_release_waits_for_competing_inspection(tmp_path, monkeypatch):
    """A losing acquirer must not make the legitimate owner's release fail."""
    path = str(tmp_path / "run.lock")
    identity = runtime_identity.get_runtime_identity()
    acquire_run_lock(
        path, "owner", identity.runtime_id, identity.pid, identity.start_ticks,
    )

    inspector_holds_flock = threading.Event()
    allow_inspector_to_finish = threading.Event()
    release_attempted = threading.Event()
    contender_result = []
    release_result = []
    real_flock = run_lock.fcntl.flock

    def observed_flock(fd, operation):
        if (
            threading.current_thread().name == "release-owner"
            and operation & run_lock.fcntl.LOCK_NB
        ):
            release_attempted.set()
        return real_flock(fd, operation)

    def paused_liveness_check(pid, ticks):
        inspector_holds_flock.set()
        if not allow_inspector_to_finish.wait(TIMEOUT):
            raise RuntimeError("test timeout waiting to finish inspection")
        return False

    monkeypatch.setattr(run_lock.fcntl, "flock", observed_flock)
    monkeypatch.setattr(run_lock, "process_is_extinct", paused_liveness_check)

    def contend():
        try:
            acquire_run_lock(
                path,
                "other",
                "different-runtime",
                identity.pid,
                identity.start_ticks,
            )
            contender_result.append("acquired")
        except BaseException as exc:
            contender_result.append(type(exc).__name__)

    def release_owner():
        try:
            run_lock.release_run_lock(
                path,
                "owner",
                identity.runtime_id,
                identity.pid,
                identity.start_ticks,
            )
            release_result.append("released")
        except BaseException as exc:
            release_result.append(type(exc).__name__)

    contender = threading.Thread(target=contend, name="inspect-contender")
    releaser = threading.Thread(target=release_owner, name="release-owner")
    contender.start()
    assert inspector_holds_flock.wait(TIMEOUT)
    releaser.start()
    assert release_attempted.wait(TIMEOUT)
    allow_inspector_to_finish.set()
    contender.join(TIMEOUT)
    releaser.join(TIMEOUT)

    assert not contender.is_alive() and not releaser.is_alive()
    assert contender_result == ["MagazineLockError"]
    assert release_result == ["released"]
    assert not pathlib.Path(path).exists()


@linux_only
def test_stale_takeover_stress_never_two_owners_or_raw_oserror(tmp_path):
    """Real child processes race to take over one positively stale lock."""
    ctx = _ctx()
    for round_no in range(12):
        path = str(tmp_path / f"run-{round_no}.lock")
        pathlib.Path(path).write_text(STALE)
        q, barrier = ctx.Queue(), ctx.Barrier(2)

        def attempt_live(owner, runtime):
            pid = os.getpid()
            ticks = runtime_identity._stat_start_ticks(pid)
            barrier.wait(TIMEOUT)
            _attempt(path, owner, runtime, pid, ticks, q, None)

        procs = [
            ctx.Process(target=attempt_live, args=(f"owner{i}", f"rt{i}"))
            for i in (1, 2)
        ]
        for p in procs:
            p.start()
        out = _collect(q, procs, 2)

        winners = [o for o, r in out.items() if r == "ok"]
        assert len(winners) == 1, f"round {round_no}: expected one owner, got {out}"
        assert {r for o, r in out.items() if r != "ok"} == {"MagazineLockError"}, f"round {round_no}: {out}"


# --------------------------------------------------------------------------
# 2. Interrupted lock creation                         [FAILS NOW] + [PROTECTIVE]
# --------------------------------------------------------------------------

def _die_during_file_creation(path):
    # A zero file-size limit causes the child to terminate at the kernel write
    # boundary, without coupling the regression to a particular Python write
    # helper or monkeypatching Magazine internals.
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    signal.signal(signal.SIGXFSZ, signal.SIG_DFL)
    acquire_run_lock(path, "owner", "rt", 1, 1)


@linux_only
def test_crash_during_lock_creation_never_leaves_malformed_lock(tmp_path):
    path = tmp_path / "run.lock"
    p = _ctx().Process(target=_die_during_file_creation, args=(str(path),))
    p.start(); p.join(TIMEOUT)
    assert p.exitcode not in (None, 0), "child should terminate during lock creation"

    # Either no lock exists, or it is complete and well-formed -- never empty/partial.
    if path.exists():
        assert len(path.read_text().split("|")) == 4, f"malformed lock left behind: {path.read_text()!r}"
    # ...and the run must not be blocked forever by the interrupted attempt.
    acquire_run_lock(str(path), "owner2", "rt2", 2, 2)


@pytest.mark.parametrize("content", ["", "garbage", "a|b|c"])
def test_existing_malformed_lock_is_never_auto_removed(tmp_path, content):
    """[PROTECTIVE] Refusing to remove a lock of unknown ownership is intentional."""
    path = tmp_path / "run.lock"
    path.write_text(content)
    with pytest.raises(MagazineLockError):
        acquire_run_lock(str(path), "owner", "rt", 5, 5)
    assert path.exists() and path.read_text() == content


# --------------------------------------------------------------------------
# 3. Dispatch results                                       [FAILS NOW] + [PROTECTIVE]
# --------------------------------------------------------------------------

def _run(tmp_path, result):
    async def dispatch(prompt, model_id):
        return result

    try:
        final = asyncio.run(
            run_magazine(
                magazine_path=str(EXAMPLE), state_dir=str(tmp_path), owner_id="op",
                model_map={"concise": "a", "reasoning": "b"}, dispatch=dispatch,
            )
        )
    except Exception as exc:
        final = exc
    results_dir = tmp_path / "run" / "results"
    stored = {p.name: p.read_bytes() for p in results_dir.glob("*.txt")} if results_dir.exists() else {}
    return final, stored


@pytest.mark.parametrize("bad", [None, 123, b"bytes"], ids=["None", "int", "bytes"])
def test_non_str_dispatch_result_is_invalid_and_not_stored(tmp_path, bad):
    final, stored = _run(tmp_path, bad)
    completed = not isinstance(final, BaseException) and final.get("status") == "COMPLETED"
    assert not completed, "a non-str result must not complete the run"
    assert stored == {}, f"nothing may be persisted for an invalid result: {stored}"


def test_valid_str_results_are_stored_byte_for_byte(tmp_path):
    """[PROTECTIVE] The core must not strip or normalise valid results."""
    text = "  padded result\n\n"
    final, stored = _run(tmp_path, text)
    assert final["status"] == "COMPLETED"
    assert set(stored.values()) == {text.encode("utf-8")}


@pytest.mark.parametrize("content", ["", "  \n "], ids=["empty", "whitespace"])
def test_adapter_rejects_empty_assistant_content(monkeypatch, content):
    twd._pin_ids(monkeypatch)
    responses = twd._responses(twd.MODEL_A)
    responses[-1][1]["chat"]["history"]["messages"][twd.ASSISTANT_ID]["content"] = content
    dispatch = twd._dispatch(monkeypatch, twd._FakeClient(responses))
    with pytest.raises(ValueError):
        asyncio.run(dispatch(twd.PROMPT, twd.MODEL_A))


@pytest.mark.parametrize("content", ["4", "OK", "0"])
def test_adapter_accepts_short_but_valid_content(monkeypatch, content):
    """[PROTECTIVE] Rejecting 'empty' must not reject legitimately short output."""
    twd._pin_ids(monkeypatch)
    responses = twd._responses(twd.MODEL_A)
    responses[-1][1]["chat"]["history"]["messages"][twd.ASSISTANT_ID]["content"] = content
    dispatch = twd._dispatch(monkeypatch, twd._FakeClient(responses))
    assert asyncio.run(dispatch(twd.PROMPT, twd.MODEL_A)) == content


# --------------------------------------------------------------------------
# 4. /proc dependency                                        [FAILS NOW]
# --------------------------------------------------------------------------

@pytest.fixture
def no_proc(monkeypatch, tmp_path):
    """Make everything under '/proc' not exist for runtime_identity (as on
    Windows/macOS or a restricted mount).

    The configured procfs root is a stable test seam; the test does not depend
    on whether production code uses ``Path``, ``open`` or another reader.
    """
    live_ticks = runtime_identity._stat_start_ticks(os.getpid())

    runtime_identity._reset_runtime_identity_cache()
    monkeypatch.setattr(runtime_identity, "_PROC_ROOT", tmp_path / "missing-proc")
    yield live_ticks
    runtime_identity._reset_runtime_identity_cache()


@linux_only
def test_missing_proc_is_never_proof_a_live_process_is_dead(no_proc):
    """A lock holder must not be displaced just because /proc is unavailable."""
    try:
        verdict = runtime_identity.process_is_extinct(os.getpid(), no_proc)
    except Exception:
        return  # failing explicitly is acceptable; claiming 'dead' is not
    assert verdict is False


@linux_only
def test_runtime_identity_without_proc_fails_explicitly(no_proc):
    """Unsupported platforms get a clear error, not a raw FileNotFoundError.

    (Exact exception type is a design choice; adjust the assertion to match.)
    """
    with pytest.raises(runtime_identity.RuntimeIdentityError) as info:
        runtime_identity.get_runtime_identity()
    assert "linux /proc" in str(info.value).lower()


@linux_only
def test_cached_runtime_identity_is_rebuilt_after_fork(tmp_path, monkeypatch):
    """A forked child gets its own stable PID/start-time/runtime identity."""
    runtime_identity._reset_runtime_identity_cache()
    monkeypatch.setattr(
        runtime_identity, "RUNTIME_GUARD_PATH", tmp_path / "runtime-guard",
    )
    parent_identity = runtime_identity.get_runtime_identity()
    parent_connection, child_connection = _ctx().Pipe()

    def inspect_child(connection):
        first = runtime_identity.get_runtime_identity()
        second = runtime_identity.get_runtime_identity()
        connection.send(
            (
                os.getpid(),
                first.runtime_id,
                first.pid,
                first.start_ticks,
                second.runtime_id,
                second.pid,
                second.start_ticks,
            )
        )
        connection.recv()
        connection.close()

    child = _ctx().Process(target=inspect_child, args=(child_connection,))
    child.start()
    try:
        assert parent_connection.poll(TIMEOUT), "child did not report its identity"
        (
            actual_pid,
            child_runtime_id,
            child_reported_pid,
            child_start_ticks,
            repeated_runtime_id,
            repeated_pid,
            repeated_start_ticks,
        ) = parent_connection.recv()
        assert child_reported_pid == actual_pid
        assert child_start_ticks == runtime_identity._stat_start_ticks(actual_pid)
        assert child_runtime_id != parent_identity.runtime_id
        assert (repeated_runtime_id, repeated_pid, repeated_start_ticks) == (
            child_runtime_id,
            child_reported_pid,
            child_start_ticks,
        )
        parent_connection.send("done")
        child.join(TIMEOUT)
        assert child.exitcode == 0
    finally:
        if child.is_alive():
            child.terminate()
            child.join(TIMEOUT)
        parent_connection.close()
        runtime_identity._reset_runtime_identity_cache()

# End of regression coverage.

