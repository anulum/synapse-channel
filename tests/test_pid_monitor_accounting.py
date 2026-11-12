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
import shutil
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
    assert tree.count(gone_pid) == 1
    assert account_process(os.getpid())["tree_pids"] == tree
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
        assert first["binding"] == "requested-process"
        assert isinstance(first["owner"], str) and first["owner"]
        assert isinstance(first["ram_kib"], int) and first["ram_kib"] > 0
        assert isinstance(first["birth"], str) and first["birth"]
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
echo "no separator on this line"
echo ", 7 MiB"
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
        ('#!/bin/sh\necho run >> "$FAKE_GPU_RUNS"\nwhile :; do :; done\n', 1),
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
        counted = []
        for pid in pids:
            row_accounting = _row(document, pid)["accounting"]
            assert isinstance(row_accounting, dict)
            counted.append(row_accounting["status"])
        # A tool that hangs is stopped after one second, which spends the shared
        # accounting budget: the rows after the first say so instead of waiting.
        hung = script is not None and "while" in script
        assert counted == (["observed", "unavailable", "unavailable"] if hung else ["observed"] * 3)
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


def _account_in_namespace(binds: list[tuple[str, str]]) -> dict[str, object]:
    """Run the public accounting call in a bubblewrap mount and return its document."""
    from _sandbox_coverage import coverage_data_bind

    argv = ["bwrap", "--die-with-parent", "--ro-bind", "/", "/"]
    for source, target in binds:
        argv.extend(["--ro-bind", source, target])
    argv.extend(
        [
            *coverage_data_bind(),
            "--",
            sys.executable,
            "-c",
            "import json, os\n"
            "from synapse_channel.host_sessions_accounting import account_process\n"
            "print(json.dumps(account_process(os.getpid())))\n",
        ]
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = _SRC + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(argv, capture_output=True, text=True, timeout=15, env=env, check=False)
    assert result.returncode == 0, result.stderr
    document = json.loads(result.stdout)
    assert isinstance(document, dict)
    return document


@pytest.mark.skipif(shutil.which("bwrap") is None, reason="bubblewrap is not installed")
def test_linux_accounting_reports_gaps_in_proc_fields(tmp_path: Path) -> None:
    """Unreadable, short, or non-numeric proc fields stay explicit gaps."""
    if sys.platform == "win32":
        pytest.skip("procfs field gaps are the Linux lane")
    short = tmp_path / "short-stat"
    short.write_text("1 (x) R 1\n", encoding="utf-8")
    corrupt = tmp_path / "corrupt-stat"
    corrupt.write_text("no parenthesis\n", encoding="utf-8")
    partial = tmp_path / "partial-status"
    partial.write_text(
        "not a field\nUid:\t4294967294\t0\t0\t0\nVmRSS:\t12 kB\n",
        encoding="utf-8",
    )
    nameless = tmp_path / "nameless-status"
    nameless.write_text("VmRSS:\tnope kB\nVmSwap:\t4 kB\n", encoding="utf-8")

    assert _account_in_namespace([(str(short), "/proc/self/stat")])["status"] == "unavailable"
    assert _account_in_namespace([(str(corrupt), "/proc/self/stat")])["status"] == "unavailable"
    unknown = _account_in_namespace([(str(partial), "/proc/self/status")])
    assert unknown["status"] == "observed"
    assert unknown["owner"] == "4294967294" and unknown["owner_status"] == "observed"
    assert unknown["ram_kib"] == 12 and unknown["ram_status"] == "observed"
    assert unknown["swap_kib"] is None and unknown["swap_status"] == "unavailable"
    unnamed = _account_in_namespace([(str(nameless), "/proc/self/status")])
    assert unnamed["owner"] is None and unnamed["owner_status"] == "unavailable"
    assert unnamed["ram_kib"] is None and unnamed["ram_status"] == "unavailable"
    assert unnamed["swap_kib"] == 4 and unnamed["swap_status"] == "observed"
    undated = _account_in_namespace([("/dev/null", "/proc/stat")])
    assert undated["status"] == "observed"
    assert undated["birth"] is None and undated["birth_status"] == "observed"


def test_windows_readers_refuse_any_other_host() -> None:
    """Windows readers return an explicit gap on a host that has no Windows API."""
    if sys.platform == "win32":
        pytest.skip("the Windows bodies are measured on Windows")
    from synapse_channel.host_sessions_accounting import (
        _account_windows,
        _image_of,
        _owner_sid,
        _windows_birth,
        _windows_snapshot,
        _working_set_kib,
    )

    gap = _account_windows(os.getpid())
    assert gap["status"] == "unavailable" and gap["requested_pid"] == os.getpid()
    assert _windows_snapshot() == {}
    assert _windows_birth(os.getpid()) is None
    assert _working_set_kib(0) is None  # type: ignore[arg-type]  # refused before use
    assert _image_of(os.getpid()) == ""
    assert _owner_sid(None, 0) is None  # type: ignore[arg-type]  # refused before use


@pytest.mark.skipif(shutil.which("bwrap") is None, reason="bubblewrap is not installed")
def test_accounting_without_procfs_is_unavailable(tmp_path: Path) -> None:
    """A host with no procfs returns the same gap a Mac would, without inventing one."""
    if sys.platform == "win32":
        pytest.skip("procfs absence is measured by hiding the directory")
    empty = tmp_path / "noproc"
    empty.mkdir()
    document = _account_in_namespace([(str(empty), "/proc")])
    assert document["status"] == "unavailable"
    assert document["birth_status"] == "unavailable"
    assert isinstance(document["requested_pid"], int)


def test_windows_query_failures_and_a_launcher_child_are_explicit(tmp_path: Path) -> None:
    """A closed handle is a gap, and one venv child is the bound interpreter."""
    if sys.platform != "win32":
        pytest.skip("Windows process queries are measured on Windows")
    import ctypes

    from synapse_channel.host_sessions_accounting import (
        _birth_ticks,
        _close,
        _image_path,
        _open_process,
        _owner_sid,
        _priority_name,
        _sid_from_sized_token,
        _sid_string,
        _token_user_sid,
        _venv_launcher,
        _windows_times,
        _working_set_kib,
        account_process,
    )

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = _open_process(kernel, os.getpid())
    assert handle
    _close(kernel, handle)
    closed_birth, _cpu = _windows_times(kernel, handle)
    assert closed_birth is None
    assert _birth_ticks(closed_birth) is None
    assert _working_set_kib(handle) is None
    assert _image_path(kernel, handle) is None
    assert _priority_name(kernel, handle) is None
    assert _owner_sid(kernel, handle) is None
    assert _venv_launcher("") is False
    live = _open_process(kernel, os.getpid())
    assert live
    try:
        live_birth, _cpu = _windows_times(kernel, live)
    finally:
        _close(kernel, live)
    assert _birth_ticks(live_birth) is not None
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi.OpenProcessToken.argtypes = [
        ctypes.wintypes.HANDLE,
        ctypes.wintypes.DWORD,
        ctypes.POINTER(ctypes.wintypes.HANDLE),
    ]
    advapi.OpenProcessToken.restype = ctypes.wintypes.BOOL
    advapi.GetTokenInformation.argtypes = [
        ctypes.wintypes.HANDLE,
        ctypes.wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.wintypes.DWORD,
        ctypes.POINTER(ctypes.wintypes.DWORD),
    ]
    advapi.GetTokenInformation.restype = ctypes.wintypes.BOOL
    process = _open_process(kernel, os.getpid())
    assert process
    token = ctypes.wintypes.HANDLE()
    assert advapi.OpenProcessToken(process, 0x8, ctypes.byref(token))
    try:
        owner = _token_user_sid(advapi, kernel, token)
    finally:
        _close(kernel, token)
    assert isinstance(owner, str) and owner.startswith("S-1-")
    assert advapi.OpenProcessToken(process, 0x8, ctypes.byref(token))
    sized = ctypes.wintypes.DWORD()
    assert not advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(sized))
    assert sized.value
    _close(kernel, token)
    assert _token_user_sid(advapi, kernel, token) is None
    assert _sid_from_sized_token(advapi, kernel, token, int(sized.value)) is None
    _close(kernel, process)

    class _RejectedSid(ctypes.Structure):
        """A SID header whose revision the token API refuses."""

        _fields_ = (
            ("Revision", ctypes.c_byte),
            ("SubAuthorityCount", ctypes.c_byte),
            ("IdentifierAuthority", ctypes.c_byte * 6),
        )

    rejected = _RejectedSid()
    rejected.Revision = 0
    rejected.SubAuthorityCount = 0
    assert _sid_string(advapi, kernel, ctypes.addressof(rejected)) is None
    empty = ctypes.create_string_buffer(ctypes.sizeof(ctypes.c_void_p))
    missing_sid = ctypes.cast(empty, ctypes.POINTER(ctypes.c_void_p))[0]
    assert _sid_string(advapi, kernel, missing_sid) is None
    system = account_process(4)
    assert system["status"] == "observed"
    assert system["owner"] is None
    assert system["owner_status"] == "unavailable"
    from synapse_channel import host_sessions_accounting as accounting

    ghost = 2_000_000_001
    real_snapshot = accounting._windows_snapshot

    def _snapshot_with_ghost() -> dict[int, tuple[int, str]]:
        table = real_snapshot()
        table[ghost] = (0, "ghost.exe")
        return table

    accounting._windows_snapshot = _snapshot_with_ghost
    try:
        missing = account_process(ghost)
    finally:
        accounting._windows_snapshot = real_snapshot
    assert missing["status"] == "unavailable"
    assert missing["requested_pid"] == ghost
    assert accounting._windows_birth(ghost) is None
    base = Path(r"C:\Program Files\Python312\python.exe")
    alone = subprocess.Popen([str(base), "-c", "import time; time.sleep(20)"])
    try:
        plain = account_process(alone.pid)
    finally:
        alone.kill()
        alone.wait(timeout=5)
    scripts = tmp_path / "Scripts"
    scripts.mkdir()
    (tmp_path / "pyvenv.cfg").write_text("home = C:\\Program Files\\Python312\n", encoding="utf-8")
    launcher = scripts / "python.exe"
    shutil.copy(base, launcher)
    idle = subprocess.Popen([str(launcher), "-c", "import time; time.sleep(20)"])
    try:
        requested = account_process(idle.pid)
    finally:
        idle.kill()
        idle.wait(timeout=5)
    assert requested["binding"] == "requested-process"
    assert plain["status"] == "observed"
    parent = (
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.argv[1], '-c', 'import time; time.sleep(30)'])\n"
        "time.sleep(30)\n"
    )
    child = subprocess.Popen([str(launcher), "-c", parent, str(base)])
    try:
        time.sleep(1)
        bound = account_process(child.pid)
    finally:
        child.kill()
        child.wait(timeout=5)
    assert bound["binding"] == "venv-launcher-child"
    assert bound["status"] == "observed"
