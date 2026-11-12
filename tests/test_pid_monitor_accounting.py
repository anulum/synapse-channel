# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — public pid-monitor binds the interpreter that does the work
"""Qualify birth, owner, source, counters, priority and bounded teardown."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from _platform_caps import requires_proc

_SRC = str(Path(__file__).resolve().parents[1] / "src")
_WINDOWS_SLEEPER = (
    "import ctypes, time\n"
    "from ctypes import wintypes\n"
    "kernel = ctypes.WinDLL('kernel32', use_last_error=True)\n"
    "kernel.GetCurrentProcess.restype = wintypes.HANDLE\n"
    "kernel.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]\n"
    "kernel.SetPriorityClass.restype = wintypes.BOOL\n"
    "current = kernel.GetCurrentProcess()\n"
    "if not kernel.SetPriorityClass(current, 0x4000):\n"
    "    raise SystemExit('priority')\n"
    "time.sleep(60)\n"
)
_LINUX_SLEEPER = "import os, time\nos.nice(5)\ntime.sleep(60)\n"


def _monitor(pid: int) -> tuple[int, dict[str, object], float]:
    env = dict(os.environ)
    env["PYTHONPATH"] = _SRC
    env["PYTHONUTF8"] = "1"
    started = time.monotonic()
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "synapse_channel.cli",
            "pid-monitor",
            "--json",
            "--samples",
            "1",
            "--pid",
            str(pid),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
        env=env,
    )
    elapsed = time.monotonic() - started
    document = json.loads(completed.stdout) if completed.returncode == 0 else {}
    return completed.returncode, document, elapsed


def _monitor_text(pid: int) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = _SRC
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "synapse_channel.cli",
            "pid-monitor",
            "--samples",
            "1",
            "--pid",
            str(pid),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
        env=env,
    )


def _row(document: dict[str, object], pid: int) -> dict[str, object]:
    rows = document.get("rows")
    assert isinstance(rows, list)
    found = [row for row in rows if row.get("pid") == pid]
    assert found, document
    row = found[0]
    assert isinstance(row, dict)
    return row


def _stop(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return


def test_pid_monitor_binds_the_working_interpreter_and_stops_within_a_bound() -> None:
    """The public monitor reports the process that does the work, then returns."""
    program = _WINDOWS_SLEEPER if sys.platform == "win32" else _LINUX_SLEEPER
    child = subprocess.Popen([sys.executable, "-c", program])
    bound = child.pid
    try:
        time.sleep(0.8)
        code, document, _elapsed = _monitor(child.pid)
        assert code == 0, document
        accounting = _row(document, child.pid)["accounting"]
        assert isinstance(accounting, dict)
        assert accounting["memory_guarantee"] == "none"
        assert accounting["requested_pid"] == child.pid
        assert accounting["status"] == "observed"
        assert accounting["birth_status"] == "observed"
        assert accounting["owner_status"] == "observed" and accounting["owner"]
        assert accounting["source_status"] == "observed" and accounting["source"]
        assert accounting["cpu_status"] == "observed" and accounting["cpu_seconds"] >= 0
        assert accounting["ram_status"] == "observed" and accounting["ram_kib"] > 0
        assert accounting["priority_status"] == "observed"
        assert accounting["gpu_status"] in {"observed", "unavailable"}
        assert isinstance(accounting["tree_pids"], list)
        assert accounting["bound_pid"] in accounting["tree_pids"]
        bound = int(accounting["bound_pid"])
        if sys.platform == "win32":
            assert accounting["binding"] == "venv-launcher-child"
            assert accounting["bound_pid"] != child.pid
            assert accounting["swap_status"] == "unavailable"
            assert accounting["priority"] == "below-normal"
            assert accounting["ram_kib"] > accounting["launcher_ram_kib"]
            assert str(accounting["source"]).lower().endswith("python.exe")
            assert "scripts" not in str(accounting["source"]).lower()
        else:
            assert accounting["binding"] == "requested-process"
            assert accounting["bound_pid"] == child.pid
            assert accounting["swap_status"] == "observed"
            assert isinstance(accounting["swap_kib"], int)
            assert accounting["priority"] == str(min(19, os.nice(0) + 5))
            assert accounting["memory_sampling"] == "proc-status"
        text = _monitor_text(child.pid)
        assert text.returncode == 0
        assert f"bound {bound}" in text.stdout
        assert "no job guarantee" in text.stdout
    finally:
        _stop(bound)
        _stop(child.pid)
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)
    code, document, elapsed = _monitor(child.pid)
    assert code == 0 and elapsed < 5
    rows = document.get("rows")
    assert isinstance(rows, list)
    assert all(row.get("pid") != child.pid for row in rows)


def test_accounting_refuses_a_bad_pid_and_states_discovery_bounds() -> None:
    """Bad PIDs raise, a dead PID stays unavailable, and discovery states its bound."""
    from synapse_channel.host_sessions_accounting import account_process, discover_windows_processes

    with pytest.raises(ValueError):
        account_process(True)
    with pytest.raises(ValueError):
        account_process(0)
    gone = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    gone_pid = gone.pid
    time.sleep(0.3)
    parent = account_process(os.getpid())
    tree = parent["tree_pids"]
    assert isinstance(tree, list)
    assert gone_pid in tree
    gone.kill()
    gone.wait(timeout=5)
    missing = account_process(gone_pid)
    assert missing["status"] == "unavailable"
    assert missing["memory_guarantee"] == "none"
    empty, empty_status = discover_windows_processes(pids=(), limit=4, seconds=1.0)
    assert empty == {}
    if sys.platform != "win32":
        assert empty_status == "unavailable"
        first = account_process(os.getpid())
        second = account_process(os.getpid())
        assert first["binding"] == "requested-process"
        assert second["tree_pids"] == first["tree_pids"]
        return
    assert empty_status == "unavailable"
    paused, paused_status = discover_windows_processes(pids=(os.getpid(),), limit=4, seconds=0)
    assert paused == {} and paused_status == "partial"
    capped, capped_status = discover_windows_processes(pids=(os.getpid(),), limit=0, seconds=1)
    assert capped == {} and capped_status == "partial"
    live, live_status = discover_windows_processes(pids=(os.getpid(),), limit=4, seconds=1)
    assert live_status == "complete" and os.getpid() in live
    absent, absent_status = discover_windows_processes(pids=(2**22,), limit=4, seconds=1)
    assert absent == {} and absent_status == "partial"
    other = subprocess.Popen(["ping", "-n", "6", "127.0.0.1"], stdout=subprocess.DEVNULL)
    try:
        counted = account_process(other.pid)
        assert counted["status"] == "observed"
        assert counted["binding"] == "requested-process"
    finally:
        other.kill()
        other.wait(timeout=5)
    own = account_process(os.getpid())
    assert own["binding"] == "requested-process"
    assert own["memory_guarantee"] == "none"


_FAKE_NVIDIA_SMI = """#!/bin/sh
echo run >> "$FAKE_GPU_RUNS"
echo "$FAKE_GPU_PID, 123 MiB"
"""


def _monitor_with_gpu_tool(
    tmp_path: Path, script: str | None, pids: list[int]
) -> tuple[dict[str, object], Path]:
    """Run the public monitor with ``script`` as the only ``nvidia-smi`` on PATH."""
    tools = tmp_path / "bin"
    tools.mkdir()
    if script is not None:
        tool = tools / "nvidia-smi"
        tool.write_text(script, encoding="utf-8")
        tool.chmod(0o755)
    runs = tmp_path / "runs"
    env = dict(os.environ)
    env["PYTHONPATH"] = _SRC
    env["PATH"] = str(tools)
    env["FAKE_GPU_RUNS"] = str(runs)
    env["FAKE_GPU_PID"] = str(pids[0])
    argv = [sys.executable, "-m", "synapse_channel.cli", "pid-monitor", "--json"]
    argv += ["--samples", "1"]
    for pid in pids:
        argv += ["--pid", str(pid)]
    completed = subprocess.run(
        argv, capture_output=True, text=True, check=False, timeout=20, env=env
    )
    assert completed.returncode == 0, completed.stderr
    document = json.loads(completed.stdout)
    assert isinstance(document, dict)
    return document, runs


@requires_proc
def test_one_gpu_query_counts_every_requested_process(tmp_path: Path) -> None:
    """The public monitor asks the GPU tool once and still counts every row."""
    children = [
        subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"]) for _ in range(6)
    ]
    try:
        time.sleep(0.5)
        pids = [child.pid for child in children]
        document, runs = _monitor_with_gpu_tool(tmp_path, _FAKE_NVIDIA_SMI, pids)
        for pid in pids:
            accounting = _row(document, pid)["accounting"]
            assert isinstance(accounting, dict)
            assert accounting["status"] == "observed"
            assert accounting["ram_status"] == "observed"
        first = _row(document, pids[0])["accounting"]
        assert isinstance(first, dict)
        assert (first["gpu"], first["gpu_status"]) == ("123 MiB", "observed")
        other = _row(document, pids[1])["accounting"]
        assert isinstance(other, dict)
        assert (other["gpu"], other["gpu_status"]) == (None, "unavailable")
        assert runs.read_text(encoding="utf-8").splitlines() == ["run"]
    finally:
        for child in children:
            child.kill()
        for child in children:
            child.wait(timeout=5)


@requires_proc
@pytest.mark.parametrize(
    ("script", "expected_runs"),
    [
        (None, 0),
        ('#!/bin/sh\necho run >> "$FAKE_GPU_RUNS"\nexit 3\n', 1),
        ('#!/bin/sh\necho run >> "$FAKE_GPU_RUNS"\nexec sleep 30\n', 1),
    ],
    ids=["tool-absent", "tool-fails", "tool-hangs"],
)
def test_a_broken_gpu_tool_leaves_the_other_counters(
    tmp_path: Path, script: str | None, expected_runs: int
) -> None:
    """No GPU tool, a failing one or a hanging one costs the GPU value only, once."""
    children = [
        subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"]) for _ in range(3)
    ]
    try:
        time.sleep(0.5)
        pids = [child.pid for child in children]
        started = time.monotonic()
        document, runs = _monitor_with_gpu_tool(tmp_path, script, pids)
        assert time.monotonic() - started < 10
        first = _row(document, pids[0])["accounting"]
        assert isinstance(first, dict)
        assert first["status"] == "observed" and first["ram_status"] == "observed"
        assert (first["gpu"], first["gpu_status"]) == (None, "unavailable")
        seen = runs.read_text(encoding="utf-8").splitlines() if runs.exists() else []
        assert len(seen) == expected_runs
    finally:
        for child in children:
            child.kill()
        for child in children:
            child.wait(timeout=5)


def test_a_monitor_counts_processes_only_when_asked() -> None:
    """Accounting is off unless the caller turns it on, and the flag is a boolean."""
    from synapse_channel.host_sessions import HostSessionMonitor

    with pytest.raises(TypeError):
        HostSessionMonitor(accounting=1)  # type: ignore[arg-type]  # refused on purpose
    missing = "/nonexistent/synapse-monitor-test.sock"
    quiet = HostSessionMonitor(pids=(os.getpid(),), tmux_socket=missing).snapshot()
    counted = HostSessionMonitor(
        pids=(os.getpid(),), tmux_socket=missing, accounting=True
    ).snapshot()
    assert all(row.accounting is None for row in quiet.rows)
    for row in counted.rows:
        assert isinstance(row.accounting, dict)
        assert row.accounting["requested_pid"] == row.pid
        assert row.accounting["memory_guarantee"] == "none"
    assert len(quiet.rows) == len(counted.rows)
