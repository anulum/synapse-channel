# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real procfs observation tests
"""Exercise process identity, consent and exit through Linux procfs."""

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import cast

import pytest

from _platform_caps import PROC_AVAILABLE, requires_proc
from synapse_channel.host_sessions_proc import (
    KernelClock,
    ProcessMetadata,
    discover_processes,
    kernel_clock,
    observe_process,
    process_metadata,
)


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
    init_owner = Path("/proc/1").stat().st_uid
    rows, status = discover_processes(pids=(1, os.getpid()))
    assert os.getpid() in rows
    if init_owner == os.geteuid():
        assert status == "complete" and 1 in rows
    else:
        assert status == "partial" and 1 not in rows
        with pytest.raises(PermissionError):
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
    if PROC_AVAILABLE:
        assert status == "complete"
        assert tuple(rows) == (os.getpid(),)
        assert rows[os.getpid()].start_ticks > 0
    else:
        assert status == "unavailable"
        assert rows == {}


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


@pytest.mark.parametrize("seconds", [-1, float("nan"), float("inf"), True, "1"])
def test_discovery_refuses_invalid_time_budgets(seconds: object) -> None:
    with pytest.raises(ValueError, match="seconds"):
        discover_processes(pids=(os.getpid(),), seconds=cast(float, seconds))


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
            deadline = time.monotonic() + 5
            while observe_process(child.pid).state != "Z":
                assert time.monotonic() < deadline, "thread leader failed to exit"
                time.sleep(0.01)
            assert child.poll() is None
            with pytest.raises(FileNotFoundError):
                os.readlink(f"/proc/{child.pid}/cwd")
            with pytest.raises(PermissionError), os.scandir(f"/proc/{child.pid}/fd") as entries:
                tuple(entries)
            assert process_metadata(
                child.pid, paths=True, context=True, context_root=tmp_path
            ) == ProcessMetadata(None, None, "unavailable", "denied")
        finally:
            assert child.stdin is not None
            child.stdin.close()
            child.wait(timeout=5)
