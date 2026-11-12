# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — process accounting for the public pid monitor
"""Bind a requested PID to the interpreter that does the work, and count it.

A Windows venv ``python.exe`` is a launcher. Its working set is not the
interpreter's. Counters name their source. A working set is not a Job Object
RAM or swap guarantee, so ``memory_guarantee`` stays ``none``.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess  # nosec B404 # one fixed nvidia-smi argv, never a shell
import sys
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any

from synapse_channel.host_sessions_proc import ProcessIdentity

_GPU_ARGV = (
    "nvidia-smi",
    "--query-compute-apps=pid,used_gpu_memory",
    "--format=csv,noheader",
)
_PRIORITY = {
    0x100: "realtime",
    0x80: "high",
    0x8000: "above-normal",
    0x20: "normal",
    0x4000: "below-normal",
    0x40: "idle",
}


def account_process(pid: int) -> dict[str, object]:
    """Return birth, owner, source and counters for the process that does the work.

    Parameters
    ----------
    pid : int
        Requested process. A Windows venv launcher binds its interpreter child.

    Returns
    -------
    dict
        Observed counters, or ``status`` ``unavailable`` when the PID is gone.
        ``memory_guarantee`` is always ``none``.

    Raises
    ------
    ValueError
        ``pid`` is not a positive integer. Booleans are refused.
    """
    if type(pid) is not int or pid <= 0:
        raise ValueError("PID must be a positive integer")
    if sys.platform == "win32":
        return _account_windows(pid)
    if Path("/proc/self/stat").exists():
        return _account_linux(pid)
    return _unavailable(pid)


def discover_windows_processes(
    *, pids: tuple[int, ...], limit: int, seconds: float
) -> tuple[dict[int, ProcessIdentity], str]:
    """Read explicit Windows PIDs into the Linux-shaped process map.

    An empty PID list does no whole-machine scan. ``seconds`` of zero returns
    a partial empty map, the same bound as the procfs scanner.
    """
    if sys.platform != "win32":
        return {}, "unavailable"
    if seconds == 0 or limit == 0:
        return {}, "partial"
    if not pids:
        return {}, "unavailable"
    found = _windows_snapshot()
    result: dict[int, ProcessIdentity] = {}
    for pid in pids[:limit]:
        entry = found.get(pid)
        birth = _windows_birth(pid) if entry is not None else None
        if entry is None or birth is None:
            continue
        result[pid] = ProcessIdentity(pid, entry[0], birth, "running", entry[1])
    status = "complete" if len(result) == len(pids) else "partial"
    return result, status


def _unavailable(pid: int) -> dict[str, object]:
    return {
        "status": "unavailable",
        "requested_pid": pid,
        "bound_pid": None,
        "binding": "unavailable",
        "birth": None,
        "birth_status": "unavailable",
        "owner": None,
        "owner_status": "unavailable",
        "source": None,
        "source_status": "unavailable",
        "cpu_seconds": None,
        "cpu_status": "unavailable",
        "ram_kib": None,
        "ram_status": "unavailable",
        "swap_kib": None,
        "swap_status": "unavailable",
        "gpu": None,
        "gpu_status": "unavailable",
        "priority": None,
        "priority_status": "unavailable",
        "tree_pids": [],
        "launcher_ram_kib": None,
        "memory_sampling": "none",
        "memory_guarantee": "none",
    }


_NVIDIA = shutil.which("nvidia-smi")
_CHILDREN: tuple[float, dict[int, list[int]]] | None = None
_GPU: tuple[float, dict[str, str] | None] | None = None


def _read_gpu_table() -> dict[str, str] | None:
    """Run ``nvidia-smi`` once and map each listed PID to its used GPU memory."""
    if _NVIDIA is None:
        return None
    try:
        completed = subprocess.run(  # nosec B603 # fixed _GPU_ARGV, no shell, bounded
            _GPU_ARGV, capture_output=True, text=True, timeout=1, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    table: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        pid_text, _separator, memory = line.partition(",")
        if pid_text.strip() and memory.strip():
            table.setdefault(pid_text.strip(), memory.strip())
    return table


def _gpu_table() -> dict[str, str] | None:
    """Return the GPU table, read at most once in half a second.

    One observation counts many rows inside one short budget. A query per row
    spent that budget on process starts and left most rows uncounted. A failed
    query is kept for the same half second, so it is not repeated per row.
    """
    global _GPU
    now = time.monotonic()
    if _GPU is not None and now < _GPU[0]:
        return _GPU[1]
    table = _read_gpu_table()
    _GPU = (now + 0.5, table)
    return table


def _gpu_for(pid: int) -> tuple[str | None, str]:
    table = _gpu_table()
    if table is None:
        return None, "unavailable"
    memory = table.get(str(pid))
    if memory is None:
        return None, "unavailable"
    return memory, "observed"


def _account_linux(pid: int) -> dict[str, object]:
    """Read one Linux process. A missing or unparsable proc file is a gap dict."""
    root = Path("/proc") / str(pid)
    try:
        stat_text = (root / "stat").read_text(encoding="utf-8", errors="replace")
        status = (root / "status").read_text(encoding="utf-8", errors="replace")
        source = os.readlink(root / "exe")
    except OSError:
        return _unavailable(pid)
    try:
        return _linux_document(pid, stat_text, status, source)
    except (OSError, ValueError, IndexError):
        return _unavailable(pid)


def _linux_document(pid: int, stat_text: str, status: str, source: str) -> dict[str, object]:
    """Build the observed document. Callers turn a parse error into a gap."""
    fields = stat_text[stat_text.rindex(")") + 2 :].split()
    if len(fields) < 20:
        return _unavailable(pid)
    ticks = os.sysconf("SC_CLK_TCK")
    cpu = (int(fields[11]) + int(fields[12])) / ticks
    values = _status_map(status)
    uid = values.get("Uid", "").split()
    owner = uid[0] if uid else ""
    if owner:
        try:
            import pwd

            owner = pwd.getpwuid(int(owner)).pw_name
        except (ImportError, KeyError, ValueError):
            pass
    ram = _kib(values.get("VmRSS", ""))
    swap = _kib(values.get("VmSwap", ""))
    gpu, gpu_status = _gpu_for(pid)
    document = _unavailable(pid)
    document.update(
        {
            "status": "observed",
            "bound_pid": pid,
            "binding": "requested-process",
            "birth": _linux_birth(int(fields[19])),
            "birth_status": "observed",
            "owner": owner or None,
            "owner_status": "observed" if owner else "unavailable",
            "source": source,
            "source_status": "observed",
            "cpu_seconds": cpu,
            "cpu_status": "observed",
            "ram_kib": ram,
            "ram_status": "observed" if ram is not None else "unavailable",
            "swap_kib": swap,
            "swap_status": "observed" if swap is not None else "unavailable",
            "gpu": gpu,
            "gpu_status": gpu_status,
            "priority": fields[16],
            "priority_status": "observed",
            "tree_pids": _linux_tree(pid),
            "memory_sampling": "proc-status",
        }
    )
    return document


def _linux_birth(start_ticks: int) -> str | None:
    """Return the wall-clock start, or None when the kernel clock cannot be read."""
    try:
        from synapse_channel.host_sessions_proc import kernel_clock

        return str(kernel_clock().started_at(start_ticks))
    except (OSError, ValueError):
        return None


def _status_map(text: str) -> dict[str, str]:
    """Return proc status fields. A line without a colon is not a field."""
    found: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.partition(":")
        if separator:
            found[key] = value.strip()
    return found


def _kib(value: str) -> int | None:
    """Return the leading decimal from a procfs ``N kB`` field, or None."""
    number, _separator, _unit = value.partition(" ")
    if number.isdecimal():
        return int(number)
    return None


def _linux_children() -> dict[int, list[int]]:
    global _CHILDREN
    now = time.monotonic()
    if _CHILDREN is not None and now < _CHILDREN[0]:
        return _CHILDREN[1]
    children: dict[int, list[int]] = {}
    entries = os.scandir("/proc")
    try:
        for index, entry in enumerate(entries):
            if index >= 4096 or not entry.name.isdecimal():
                continue
            try:
                raw = Path(entry.path, "stat").read_text(encoding="utf-8", errors="replace")
                parent = int(raw[raw.rindex(")") + 2 :].split()[1])
            except (OSError, ValueError, IndexError):
                continue
            children.setdefault(parent, []).append(int(entry.name))
    finally:
        entries.close()
    _CHILDREN = (now + 0.5, children)
    return children


def _linux_tree(pid: int) -> list[int]:
    """Return ``pid`` and each descendant once.

    A reused parent id can point back into this set. Those repeats are left
    out, and the walk stops at 64 ids.
    """
    children = _linux_children()
    ordered = [pid]
    pending = [pid]
    while pending and len(ordered) < 64:
        current = pending.pop(0)
        for child in children.get(current, []):
            if child not in ordered:
                ordered.append(child)
                pending.append(child)
    return ordered


def _account_windows(pid: int) -> dict[str, object]:
    """Read one Windows process. Any other host gets an explicit gap."""
    if sys.platform != "win32":
        return _unavailable(pid)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    found = _windows_snapshot()
    if pid not in found:
        return _unavailable(pid)
    bound, binding = _bind_windows(pid, found)
    handle = _open_process(kernel, bound)
    launcher = _open_process(kernel, pid) if bound != pid else handle
    if not handle:
        return _unavailable(pid)
    try:
        birth, cpu = _windows_times(kernel, handle)
        ram = _working_set_kib(handle)
        launcher_ram = _working_set_kib(launcher) if launcher and bound != pid else None
        source = _image_path(kernel, handle)
        priority = _priority_name(kernel, handle)
        owner = _owner_sid(kernel, handle)
    finally:
        _close(kernel, handle)
        if launcher and launcher != handle:
            _close(kernel, launcher)
    gpu, gpu_status = _gpu_for(bound)
    document = _unavailable(pid)
    document.update(
        {
            "status": "observed",
            "bound_pid": bound,
            "binding": binding,
            "birth": birth,
            "birth_status": "observed" if birth else "unavailable",
            "owner": owner,
            "owner_status": "observed" if owner else "unavailable",
            "source": source,
            "source_status": "observed" if source else "unavailable",
            "cpu_seconds": cpu,
            "cpu_status": "observed" if cpu is not None else "unavailable",
            "ram_kib": ram,
            "ram_status": "observed" if ram is not None else "unavailable",
            "swap_status": "unavailable",
            "gpu": gpu,
            "gpu_status": gpu_status,
            "priority": priority,
            "priority_status": "observed" if priority else "unavailable",
            "tree_pids": _windows_tree(bound, found),
            "launcher_ram_kib": launcher_ram,
            "memory_sampling": "working-set",
        }
    )
    return document


def _bind_windows(pid: int, found: dict[int, tuple[int, str]]) -> tuple[int, str]:
    image = _image_of(pid)
    if not _venv_launcher(image):
        return pid, "requested-process"
    children = [child for child, (parent, _name) in found.items() if parent == pid]
    interpreters = [
        child
        for child in children
        if _image_of(child).lower().endswith("python.exe") and _image_of(child) != image
    ]
    if len(interpreters) == 1:
        return interpreters[0], "venv-launcher-child"
    return pid, "requested-process"


def _venv_launcher(image: str) -> bool:
    if not image:
        return False
    path = Path(image)
    if path.name.lower() not in {"python.exe", "pythonw.exe"}:
        return False
    if path.parent.name.lower() != "scripts":
        return False
    return (path.parent.parent / "pyvenv.cfg").is_file()


def _windows_tree(pid: int, found: dict[int, tuple[int, str]]) -> list[int]:
    """Return ``pid`` and each descendant once.

    Windows does not clear a parent id when that parent exits, and the number
    can be reused inside the same set. Those repeats are left out, and the
    walk stops at 64 ids.
    """
    children: dict[int, list[int]] = {}
    for child, (parent, _name) in found.items():
        children.setdefault(parent, []).append(child)
    ordered = [pid]
    pending = [pid]
    while pending and len(ordered) < 64:
        current = pending.pop(0)
        for child in children.get(current, []):
            if child not in ordered:
                ordered.append(child)
                pending.append(child)
    return ordered


def _windows_snapshot() -> dict[int, tuple[int, str]]:
    """Read the Windows process table. Any other host has no table."""
    if sys.platform != "win32":
        return {}
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    kernel.Process32FirstW.restype = wintypes.BOOL
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    kernel.Process32NextW.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]

    class _Entry(ctypes.Structure):
        _fields_ = (
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        )

    snapshot = kernel.CreateToolhelp32Snapshot(0x2, 0)
    found: dict[int, tuple[int, str]] = {}
    try:
        entry = _Entry()
        entry.dwSize = ctypes.sizeof(_Entry)
        more = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while more and len(found) < 4096:
            found[int(entry.th32ProcessID)] = (
                int(entry.th32ParentProcessID),
                str(entry.szExeFile),
            )
            more = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    return found


def _open_process(kernel: Any, pid: int) -> wintypes.HANDLE | None:
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    handle = kernel.OpenProcess(0x1000, False, pid)
    return handle or None


def _close(kernel: Any, handle: wintypes.HANDLE) -> None:
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle(handle)


def _birth_ticks(birth: str | None) -> int | None:
    """Convert a creation time to clock ticks. A missing time has no birth."""
    if birth is None:
        return None
    return int(float(birth) * 10_000_000)


def _windows_birth(pid: int) -> int | None:
    """Return a Windows creation time. Any other host has none."""
    if sys.platform != "win32":
        return None
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = _open_process(kernel, pid)
    if not handle:
        return None
    try:
        birth, _cpu = _windows_times(kernel, handle)
    finally:
        _close(kernel, handle)
    return _birth_ticks(birth)


def _windows_times(kernel: Any, handle: wintypes.HANDLE) -> tuple[str | None, float | None]:
    class _FileTime(ctypes.Structure):
        _fields_ = (("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD))

    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(_FileTime)] * 4
    kernel.GetProcessTimes.restype = wintypes.BOOL
    created, _exited, kernel_time, user_time = (_FileTime() for _ in range(4))
    if not kernel.GetProcessTimes(
        handle,
        ctypes.byref(created),
        ctypes.byref(_exited),
        ctypes.byref(kernel_time),
        ctypes.byref(user_time),
    ):
        return None, None

    def _ticks(item: _FileTime) -> int:
        return int((item.dwHighDateTime << 32) | item.dwLowDateTime)

    unix = _ticks(created) / 10_000_000 - 11644473600
    cpu = (_ticks(kernel_time) + _ticks(user_time)) / 10_000_000
    return str(unix), cpu


def _working_set_kib(handle: wintypes.HANDLE) -> int | None:
    """Return the Windows working set in KiB. Any other host has none."""
    if sys.platform != "win32":
        return None
    psapi = ctypes.WinDLL("psapi", use_last_error=True)

    class _Counters(ctypes.Structure):
        _fields_ = (
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        )

    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_Counters),
        wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    counters = _Counters()
    counters.cb = ctypes.sizeof(_Counters)
    if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
        return None
    return int(counters.WorkingSetSize) // 1024


def _image_path(kernel: Any, handle: wintypes.HANDLE) -> str | None:
    kernel.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    buffer = ctypes.create_unicode_buffer(32768)
    length = wintypes.DWORD(len(buffer))
    if not kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)):
        return None
    return buffer.value


def _image_of(pid: int) -> str:
    """Return a Windows executable path. Any other host has none."""
    if sys.platform != "win32":
        return ""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = _open_process(kernel, pid)
    if not handle:
        return ""
    try:
        return _image_path(kernel, handle) or ""
    finally:
        _close(kernel, handle)


def _priority_name(kernel: Any, handle: wintypes.HANDLE) -> str | None:
    kernel.GetPriorityClass.argtypes = [wintypes.HANDLE]
    kernel.GetPriorityClass.restype = wintypes.DWORD
    value = int(kernel.GetPriorityClass(handle))
    if not value:
        return None
    return _PRIORITY.get(value, str(value))


def _sid_string(advapi: Any, kernel: Any, sid: int | None) -> str | None:
    """Render one SID. A missing or rejected SID is an absent owner."""
    advapi.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi.ConvertSidToStringSidW.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    text = ctypes.c_void_p()
    if not sid or not advapi.ConvertSidToStringSidW(sid, ctypes.byref(text)):
        return None
    try:
        return ctypes.wstring_at(text) if text.value else None
    finally:
        if text.value:
            kernel.LocalFree(text)


def _sid_from_sized_token(
    advapi: Any, kernel: Any, token: wintypes.HANDLE, size: int
) -> str | None:
    """Read TokenUser into the buffer size the API requested."""
    advapi.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi.GetTokenInformation.restype = wintypes.BOOL
    buffer = ctypes.create_string_buffer(size)
    needed = wintypes.DWORD()
    if not advapi.GetTokenInformation(token, 1, buffer, size, ctypes.byref(needed)):
        return None
    sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
    return _sid_string(advapi, kernel, sid)


def _token_user_sid(advapi: Any, kernel: Any, token: wintypes.HANDLE) -> str | None:
    """Read a token's user SID. An unusable token has no owner."""
    advapi.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi.GetTokenInformation.restype = wintypes.BOOL
    needed = wintypes.DWORD()
    advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
    if not needed.value:
        return None
    return _sid_from_sized_token(advapi, kernel, token, int(needed.value))


def _owner_sid(kernel: Any, handle: wintypes.HANDLE) -> str | None:
    """Read the process token SID. Token APIs live in advapi32, not kernel32."""
    if sys.platform != "win32":
        return None
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi.OpenProcessToken.restype = wintypes.BOOL
    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(handle, 0x8, ctypes.byref(token)):
        return None
    try:
        return _token_user_sid(advapi, kernel, token)
    finally:
        _close(kernel, token)
