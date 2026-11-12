# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real procfs observation tests
"""Exercise process identity, consent and exit through Linux procfs."""

import contextlib
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import cast

import pytest

from _platform_caps import PROC_AVAILABLE, requires_proc
from _sandbox_coverage import coverage_data_bind
from synapse_channel.host_sessions_proc import (
    KernelClock,
    ProcessMetadata,
    discover_processes,
    kernel_clock,
    observe_process,
    process_metadata,
)


@requires_proc
@pytest.mark.skipif(shutil.which("bwrap") is None, reason="bubblewrap is not installed")
@pytest.mark.parametrize(
    ("source", "target", "call", "expected"),
    [
        ("/proc/version", "/proc/stat", "kernel_clock()", "kernel boot time unavailable"),
        ("/proc/kallsyms", "/proc/stat", "kernel_clock()", "kernel stat exceeds limit"),
        (
            "/proc/kallsyms",
            "process-stat",
            "observe_process(os.getppid())",
            "process stat exceeds limit",
        ),
    ],
)
def test_namespace_kernel_input_limits(source: str, target: str, call: str, expected: str) -> None:
    if source == "/proc/kallsyms":
        with Path(source).open("rb") as stream:
            assert len(stream.read(65537)) == 65537
    if target == "process-stat":
        target = f"/proc/{os.getpid()}/stat"
        call = f"observe_process({os.getpid()})"
    program = (
        "import os\n"
        "from synapse_channel.host_sessions_proc import kernel_clock, observe_process\n"
        "try:\n"
        f"    {call}\n"
        "except ValueError as exc:\n"
        "    print(str(exc))\n"
        "else:\n"
        "    raise AssertionError('invalid kernel input was accepted')\n"
    )
    result = subprocess.run(
        [
            "bwrap",
            "--ro-bind",
            "/",
            "/",
            # Writable only for the active coverage data directory, and only when measuring.
            *coverage_data_bind(),
            "--ro-bind",
            source,
            target,
            "--die-with-parent",
            "--",
            sys.executable,
            "-c",
            program,
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


@requires_proc
def test_kernel_clock_dates_a_fresh_child_within_boot_resolution() -> None:
    clock = kernel_clock()
    boot_lines = [
        line for line in Path("/proc/stat").read_text().splitlines() if line.startswith("btime ")
    ]
    assert clock == KernelClock(int(boot_lines[0].split()[1]), os.sysconf("SC_CLK_TCK"))
    assert clock.ticks_per_second > 0
    before = time.time()
    with subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE
    ) as child:
        try:
            started = clock.started_at(observe_process(child.pid).start_ticks)
        finally:
            assert child.stdin is not None
            child.stdin.close()
            child.wait(timeout=5)
    after = time.time()
    assert before - 2.0 <= started <= after + 2.0
    assert clock.started_at(0) == clock.boot_time
    for ticks in (-1, True, 1.5):
        with pytest.raises(ValueError, match="start ticks"):
            clock.started_at(cast(int, ticks))


@requires_proc
def test_explicit_foreign_process_is_withheld_as_partial() -> None:
    # PID 1 is foreign on a normal host; inside a user namespace it may be ours.
    foreign = Path("/proc/1").stat().st_uid != os.geteuid()
    rows, status = discover_processes(pids=(1, os.getpid()))
    assert os.getpid() in rows
    assert (status, 1 in rows) == (("partial", False) if foreign else ("complete", True))
    refusal = pytest.raises(PermissionError, match="^process belongs to another user$")
    with refusal if foreign else contextlib.nullcontext():
        observe_process(1)


@requires_proc
def test_self_identity_and_opt_in_cwd() -> None:
    before = observe_process(os.getpid())
    assert before.pid == os.getpid()
    assert before.parent_pid == os.getppid()
    assert before.start_ticks > 0
    assert process_metadata(os.getpid(), paths=False, context=False) == ProcessMetadata(
        None, None, "not_requested", "not_requested"
    )
    assert process_metadata(os.getpid(), paths=True, context=True) == ProcessMetadata(
        os.getcwd(), None, "observed", "unsupported"
    )
    assert observe_process(os.getpid()).start_ticks == before.start_ticks
    with pytest.raises(ProcessLookupError):
        process_metadata(
            os.getpid(), paths=True, context=True, expected_start_ticks=before.start_ticks + 1
        )


def test_discovery_reports_native_process_availability() -> None:
    rows, status = discover_processes(pids=(os.getpid(),))
    if PROC_AVAILABLE or sys.platform == "win32":
        assert (status, tuple(rows)) == ("complete", (os.getpid(),))
        assert all(row.start_ticks > 0 for row in rows.values())
        return
    assert (status, tuple(rows)) == ("unavailable", ())


@pytest.mark.parametrize("pid", [0, -1, True])
def test_invalid_process_identity_is_refused(pid: int) -> None:
    with pytest.raises(ValueError):
        observe_process(pid)


@requires_proc
def test_real_exit_and_scan_limits() -> None:
    with subprocess.Popen([sys.executable, "-c", "pass"]) as child:
        child.wait(timeout=5)
    with pytest.raises(FileNotFoundError):
        observe_process(child.pid)
    rows, status = discover_processes(pids=(child.pid, os.getpid()))
    assert status == "partial" and os.getpid() in rows and child.pid not in rows
    assert discover_processes(pids=(os.getpid(),), limit=0) == ({}, "partial")
    assert discover_processes(pids=(os.getpid(),), seconds=0) == ({}, "partial")


@pytest.mark.parametrize("limit", [-1, True, 1.5])
def test_discovery_refuses_invalid_entry_limits(limit: object) -> None:
    with pytest.raises(ValueError, match="limit"):
        discover_processes(pids=(os.getpid(),), limit=cast(int, limit))


@pytest.mark.parametrize(
    "seconds",
    [
        -1,
        float("nan"),
        float("inf"),
        True,
        "1",
        pytest.param(10**400, id="integer-overflow"),
        pytest.param(-(10**400), id="negative-integer-overflow"),
    ],
)
def test_discovery_refuses_invalid_time_budgets(seconds: object) -> None:
    with pytest.raises(ValueError, match="seconds"):
        discover_processes(pids=(os.getpid(),), seconds=cast(float, seconds))


@requires_proc
@pytest.mark.parametrize("seconds", [1, sys.float_info.max, int(sys.float_info.max)])
def test_discovery_accepts_representable_time_budgets(seconds: float) -> None:
    rows, status = discover_processes(pids=(os.getpid(),), seconds=seconds)
    assert status == "complete"
    assert rows[os.getpid()].pid == os.getpid()


@pytest.mark.parametrize("pid", [0, -1, True])
def test_discovery_refuses_invalid_explicit_pids(pid: int) -> None:
    with pytest.raises(ValueError, match="PID"):
        discover_processes(pids=(os.getpid(), pid))


@requires_proc
def test_default_discovery_observes_self_without_optional_metadata() -> None:
    before = observe_process(os.getpid())
    rows, status = discover_processes()
    assert status in {"complete", "partial"}
    assert rows[before.pid].start_ticks == before.start_ticks
    assert rows[before.pid].parent_pid == before.parent_pid
    assert len(rows) <= 4096
    assert discover_processes(limit=0) == ({}, "partial")


@pytest.mark.parametrize("name", ["worker ) ( end", "line\nbreak)"])
@requires_proc
def test_kernel_command_name_does_not_shift_stat_fields(name: str) -> None:
    program = (
        "import ctypes,sys; "
        "assert ctypes.CDLL(None).prctl(15,sys.argv[1].encode(),0,0,0)==0; "
        'print("ready",flush=True); sys.stdin.read()'
    )
    with subprocess.Popen(
        [sys.executable, "-c", program, name],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    ) as child:
        try:
            assert child.stdout is not None and child.stdout.readline() == "ready\n"
            before = observe_process(child.pid)
            rows, status = discover_processes(pids=(child.pid,))
            assert status == "complete"
            assert rows[child.pid].command_name == name
            assert rows[child.pid].parent_pid == os.getpid()
            assert rows[child.pid].start_ticks == before.start_ticks > 0
        finally:
            assert child.stdin is not None
            child.stdin.close()
            child.wait(timeout=5)


@requires_proc
def test_context_uses_exact_open_pathname_not_transcript_body(tmp_path: Path) -> None:
    context = "12345678-1234-1234-1234-123456789abc"
    rollout = tmp_path / f"rollout-fixture-{context}.jsonl"
    rollout.write_text("PRIVATE-TRANSCRIPT-BODY-MUST-NOT-BE-READ")
    program = (
        'import ctypes,sys; ctypes.CDLL(None).prctl(15,b"codex",0,0,0); '
        'stream=open(sys.argv[1],"rb"); print("ready",flush=True); sys.stdin.read()'
    )
    with subprocess.Popen(
        [sys.executable, "-c", program, str(rollout)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        cwd=tmp_path,
    ) as child:
        try:
            assert child.stdout is not None and child.stdout.readline() == "ready\n"
            assert process_metadata(
                child.pid, paths=True, context=True, context_root=tmp_path
            ) == ProcessMetadata(
                str(tmp_path),
                context,
                "observed",
                "observed",
            )
            assert process_metadata(
                child.pid, paths=False, context=False, context_root=tmp_path
            ) == ProcessMetadata(None, None, "not_requested", "not_requested")
            assert process_metadata(
                child.pid, paths=False, context=True, context_root=tmp_path / "other"
            ) == ProcessMetadata(None, None, "not_requested", "unavailable")
        finally:
            assert child.stdin is not None
            child.stdin.close()
            child.wait(timeout=5)


@pytest.mark.parametrize(
    "mode",
    [
        "conflict",
        "descriptor-limit",
        "denied",
        "deleted-cwd",
        "deleted-context",
        "non-rollout",
        "directory-context",
        "fifo-context",
    ],
)
@requires_proc
def test_context_ambiguity_and_unavailable_metadata(tmp_path: Path, mode: str) -> None:
    context = "12345678-1234-1234-1234-123456789abc"
    first = tmp_path / f"rollout-first-{context}.jsonl"
    second = tmp_path / "rollout-second-aaaaaaaa-1234-1234-1234-123456789abc.jsonl"
    first.write_text("TRANSCRIPT-MARKER-NOT-FOR-OBSERVATION")
    second.write_text("SECOND-TRANSCRIPT-MARKER")
    cwd = tmp_path / "working"
    cwd.mkdir()
    program = """
import ctypes, os, sys
libc = ctypes.CDLL(None)
assert libc.prctl(15, b"codex", 0, 0, 0) == 0
mode, first, second = sys.argv[1:]
streams = [open(first, "rb")]
if mode == "conflict":
    streams.append(open(second, "rb"))
elif mode == "descriptor-limit":
    streams.extend(open(os.devnull, "rb") for _ in range(130))
elif mode == "denied":
    assert libc.prctl(4, 0, 0, 0, 0) == 0
elif mode == "deleted-cwd":
    os.rmdir(os.getcwd())
elif mode == "deleted-context":
    os.unlink(first)
elif mode == "non-rollout":
    streams[0].close()
    streams = [open("ordinary.jsonl", "w+")]
elif mode in ("directory-context", "fifo-context"):
    streams[0].close()
    os.unlink(first)
    if mode == "directory-context":
        os.mkdir(first)
        descriptor = os.open(first, os.O_RDONLY | os.O_DIRECTORY)
    else:
        os.mkfifo(first)
        descriptor = os.open(first, os.O_RDWR | os.O_NONBLOCK)
print("ready", flush=True)
sys.stdin.read()
"""
    with subprocess.Popen(
        [sys.executable, "-c", program, mode, str(first), str(second)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        cwd=cwd,
    ) as child:
        try:
            assert child.stdout is not None and child.stdout.readline() == "ready\n"
            if mode == "denied":
                with pytest.raises(PermissionError):
                    os.readlink(f"/proc/{child.pid}/cwd")
                assert process_metadata(
                    child.pid, paths=True, context=True, context_root=tmp_path
                ) == ProcessMetadata(None, None, "denied", "denied")
                rows, status = discover_processes(pids=(child.pid, os.getpid()))
                assert status == "complete" and child.pid in rows and os.getpid() in rows
            else:
                metadata = process_metadata(
                    child.pid, paths=True, context=True, context_root=tmp_path
                )
                assert metadata.context_id == (context if mode == "deleted-cwd" else None)
                assert metadata.cwd == str(cwd) + (" (deleted)" if mode == "deleted-cwd" else "")
                assert metadata.cwd_status == "observed"
                assert (
                    metadata.context_status
                    == {
                        "conflict": "conflicting",
                        "descriptor-limit": "partial",
                        "deleted-cwd": "observed",
                        "deleted-context": "unavailable",
                        "non-rollout": "unavailable",
                        "directory-context": "unavailable",
                        "fifo-context": "unavailable",
                    }[mode]
                )
        finally:
            assert child.stdin is not None
            child.stdin.close()
            child.wait(timeout=5)


def _state_after_pause(pid: int) -> str:
    """Return the kernel state of ``pid`` after a 10 ms pause."""
    time.sleep(0.01)
    return observe_process(pid).state


@requires_proc
def test_exited_thread_leader_reports_unavailable_metadata(tmp_path: Path) -> None:
    program = """
import ctypes, os, sys, threading
libc = ctypes.CDLL(None)
assert libc.prctl(15, b"codex", 0, 0, 0) == 0
def worker():
    print("ready", flush=True)
    sys.stdin.read()
    os._exit(0)
threading.Thread(target=worker).start()
libc.pthread_exit(None)
"""
    with subprocess.Popen(
        [sys.executable, "-c", program],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    ) as child:
        try:
            assert child.stdout is not None and child.stdout.readline() == "ready\n"
            assert any(_state_after_pause(child.pid) == "Z" for _ in range(500)), (
                "thread leader failed to exit"
            )
            assert child.poll() is None
            with pytest.raises(FileNotFoundError):
                os.readlink(f"/proc/{child.pid}/cwd")
            with pytest.raises(PermissionError):
                os.listdir(f"/proc/{child.pid}/fd")
            assert process_metadata(
                child.pid, paths=True, context=True, context_root=tmp_path
            ) == ProcessMetadata(None, None, "unavailable", "denied")
        finally:
            assert child.stdin is not None
            child.stdin.close()
            child.wait(timeout=5)


def _metadata_or_changed(pid: int, root: Path) -> ProcessMetadata | None:
    """Read context metadata once; ``None`` means the process identity changed mid-read."""
    try:
        return process_metadata(pid, paths=False, context=True, context_root=root)
    except ProcessLookupError:
        return None


@requires_proc
@pytest.mark.parametrize("change", ["descriptors", "command"])
def test_concurrent_metadata_changes_never_claim_stable_context(
    tmp_path: Path, change: str
) -> None:
    context = "12345678-1234-1234-1234-123456789abc"
    rollout = tmp_path / f"rollout-changing-{context}.jsonl"
    rollout.write_text("CONTEXT-CONTENTS-ARE-NOT-OBSERVED")
    program = """
import ctypes, os, sys, threading
assert ctypes.CDLL(None).prctl(15, b"codex", 0, 0, 0) == 0
stop = threading.Event()
def churn():
    if sys.argv[1] == "descriptors":
        while not stop.is_set():
            streams = [open(sys.argv[2], "rb") for _ in range(32)]
            for stream in streams:
                stream.close()
    else:
        streams = [open(sys.argv[2], "rb") for _ in range(32)]
        with open("/proc/self/comm", "w", buffering=1) as comm:
            while not stop.is_set():
                comm.write("worker\\n")
                comm.write("codex\\n")
        for stream in streams:
            stream.close()
worker = threading.Thread(target=churn)
worker.start()
print("ready", flush=True)
sys.stdin.read()
stop.set()
worker.join()
"""
    with subprocess.Popen(
        [sys.executable, "-c", program, change, str(rollout)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    ) as child:
        try:
            assert child.stdout is not None and child.stdout.readline() == "ready\n"
            # A fixed sample, not a stop-at-first loop: both outcomes appear hundreds of
            # times in 400 reads, so every assertion below is exercised on every run.
            outcomes = [_metadata_or_changed(child.pid, tmp_path) for _ in range(400)]
            changed = [outcome for outcome in outcomes if outcome is None]
            observed = [outcome for outcome in outcomes if outcome is not None]
            partial = [outcome for outcome in observed if outcome.context_status == "partial"]
            # A command change refuses the read; a descriptor change marks it partial.
            assert (bool(changed), bool(partial)) == (change == "command", change == "descriptors")
            assert all(outcome.context_id is None for outcome in partial)
            assert all(outcome.context_id in (None, context) for outcome in observed)
        finally:
            assert child.stdin is not None
            child.stdin.close()
            child.wait(timeout=5)


@requires_proc
def test_metadata_observation_during_process_reaping(tmp_path: Path) -> None:
    program = """
import ctypes, sys, time
assert ctypes.CDLL(None).prctl(15, b"codex", 0, 0, 0) == 0
print("ready", flush=True)
sys.stdin.read(1)
time.sleep(0.001)
"""
    for _ in range(32):
        with subprocess.Popen(
            [sys.executable, "-c", program],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        ) as child:
            assert child.stdout is not None and child.stdout.readline() == "ready\n"
            assert child.stdin is not None
            child.stdin.write("x")
            child.stdin.flush()
            reaper = threading.Thread(target=child.wait, kwargs={"timeout": 5})
            reaper.start()
            try:
                deadline = time.monotonic() + 5
                while reaper.is_alive():
                    assert time.monotonic() < deadline
                    try:
                        metadata = process_metadata(
                            child.pid, paths=True, context=True, context_root=tmp_path
                        )
                    except (OSError, ProcessLookupError):
                        continue
                    assert metadata.context_id is None
                with pytest.raises(FileNotFoundError):
                    process_metadata(child.pid, paths=True, context=True, context_root=tmp_path)
            finally:
                child.stdin.close()
                reaper.join(timeout=5)
                assert not reaper.is_alive()


def test_kernel_clock_reports_the_host_or_its_absence() -> None:
    """The public clock is the kernel clock, or an explicit absence.

    A host without Linux procfs refuses with ``OSError``: macOS has ``sysconf``
    but no ``/proc/stat``, and Windows has neither and says so in words.
    """
    if PROC_AVAILABLE:
        clock = kernel_clock()
        assert isinstance(clock, KernelClock)
        assert clock.ticks_per_second > 0
        assert clock.boot_time > 0
        return
    with pytest.raises(OSError) as refused:
        kernel_clock()
    assert hasattr(os, "sysconf") or "kernel clock is unavailable" in str(refused.value)


def test_kernel_clock_refuses_a_non_positive_tick_rate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A kernel that reports no tick rate is not a usable clock."""
    if not hasattr(os, "sysconf"):
        with pytest.raises(OSError, match="kernel clock is unavailable"):
            kernel_clock()
        return
    real = os.sysconf

    def rate(name: str) -> int:
        if name == "SC_CLK_TCK":
            return 0
        return int(real(name))

    monkeypatch.setattr(os, "sysconf", rate)
    assert os.sysconf("SC_PAGE_SIZE") > 0
    with pytest.raises(ValueError, match="tick rate must be positive"):
        kernel_clock()


def _child_env() -> dict[str, str]:
    """Return an environment whose path can import this tree and the package."""
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(root / "src"), str(root / "tests"), env.get("PYTHONPATH", "")]
    )
    return env


def _assert_named_pid_without_proc() -> None:
    """State the one result a host without procfs returns for this process.

    Windows reports this process as running and complete. Every other host
    returns an empty map and ``unavailable`` and does not invent a scan.
    """
    rows, status = discover_processes(pids=(os.getpid(),))
    if sys.platform == "win32":
        assert status == "complete"
        assert set(rows) == {os.getpid()}
        identity = rows[os.getpid()]
        assert identity.pid == os.getpid()
        assert identity.state == "running"
        assert isinstance(identity.parent_pid, int)
        assert isinstance(identity.start_ticks, int) and identity.start_ticks > 0
        assert isinstance(identity.command_name, str) and identity.command_name
        return
    assert (rows, status) == ({}, "unavailable")


def _hidden_proc_names_the_gap(directory: Path) -> None:
    """Run the no-proc assertion where ``/proc`` is an empty mount."""
    empty = directory / "noproc"
    empty.mkdir()
    result = subprocess.run(
        [
            "bwrap",
            "--die-with-parent",
            "--ro-bind",
            "/",
            "/",
            "--ro-bind",
            str(empty),
            "/proc",
            *coverage_data_bind(),
            "--",
            sys.executable,
            "-c",
            "import test_host_sessions_proc as proc\n"
            "proc._assert_named_pid_without_proc()\n"
            "proc.test_discovery_reports_native_process_availability()\n",
        ],
        capture_output=True,
        text=True,
        timeout=20,
        env=_child_env(),
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(
    shutil.which("bwrap") is None and Path("/proc/self/stat").is_file(),
    reason="bubblewrap is not installed",
)
def test_root_discovery_does_not_scan_the_machine() -> None:
    """An unnamed scan as root is refused instead of listing every process."""
    if not hasattr(os, "geteuid") or not Path("/proc/self/stat").is_file():
        _assert_named_pid_without_proc()
        return
    result = subprocess.run(
        [
            "bwrap",
            "--die-with-parent",
            "--unshare-user",
            "--uid",
            "0",
            "--gid",
            "0",
            "--ro-bind",
            "/",
            "/",
            *coverage_data_bind(),
            "--",
            sys.executable,
            "-c",
            "from synapse_channel.host_sessions_proc import discover_processes\n"
            "assert discover_processes() == ({}, 'unavailable')\n",
        ],
        capture_output=True,
        text=True,
        timeout=20,
        env=_child_env(),
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_unreadable_process_scan_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """A scan the kernel refuses is unavailable, not an empty success."""
    if not hasattr(os, "geteuid") or not Path("/proc/self/stat").is_file():
        _assert_named_pid_without_proc()
        return

    def refused(path: str | os.PathLike[str]) -> object:
        raise OSError(13, "Permission denied", os.fspath(path))

    monkeypatch.setattr(os, "scandir", refused)
    rows, status = discover_processes()
    assert rows == {}
    assert status == "unavailable"


@pytest.mark.skipif(shutil.which("bwrap") is None, reason="bubblewrap is not installed")
def test_a_hidden_proc_mount_names_the_gap(tmp_path: Path) -> None:
    """An empty proc mount reports the same gap as a host without procfs."""
    _hidden_proc_names_the_gap(tmp_path)


def test_observe_process_refuses_when_the_caller_identity_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The second identity read refuses when the caller uid no longer matches."""
    if not PROC_AVAILABLE:
        with pytest.raises(OSError):
            observe_process(os.getpid())
        return
    real = os.geteuid
    seen = 0

    def changed() -> int:
        nonlocal seen
        seen += 1
        if seen < 2:
            return int(real())
        return int(real()) + 1

    monkeypatch.setattr(os, "geteuid", changed)
    with pytest.raises(PermissionError, match="ownership changed"):
        observe_process(os.getpid())
