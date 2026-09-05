# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded Linux process metadata
"""Observe same-user process identities without reading argv or environments."""

from __future__ import annotations

import os
import re
import stat
import sys
import time
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

PROVIDERS = frozenset({"codex", "claude", "kimi", "grok", "gemini", "opencode", "qwen"})

MetadataStatus = Literal[
    "not_requested", "observed", "unavailable", "denied", "conflicting", "partial", "unsupported"
]


@dataclass(frozen=True)
class ProcessMetadata:
    """Optional values paired with the evidence that permits their display.

    Attributes
    ----------
    cwd, context_id : str or None
        Observed directory and unique context pathname UUID. Values are null
        unless the corresponding status is observed.
    cwd_status, context_status : str
        not_requested, observed, unavailable, denied, conflicting, partial or
        unsupported. Partial descriptor enumeration never proves uniqueness.
    """

    cwd: str | None
    context_id: str | None
    cwd_status: MetadataStatus
    context_status: MetadataStatus


def process_metadata(
    pid: int,
    *,
    paths: bool,
    context: bool,
    context_root: Path | None = None,
    expected_start_ticks: int | None = None,
) -> ProcessMetadata:
    """Read opt-in cwd and open regular-file rollout UUID, never file contents.

    Parameters
    ----------
    pid : int
        Same-user process to observe and revalidate.
    paths, context : bool
        Explicit consent to inspect cwd or at most 128 descriptor symlinks.
    context_root : pathlib.Path or None, optional
        Owner-selected metadata root for a non-default Codex installation.
        Only descriptor pathnames below it are considered; files are not opened.
    expected_start_ticks : int or None, optional
        Refuse a lifetime different from the discovery observation.

    Returns
    -------
    ProcessMetadata
        Cwd and uniquely observed context UUID with per-field evidence status.
        Missing, denied, conflicting or partially observed context is null.
    """
    before = observe_process(pid)
    if expected_start_ticks is not None and before.start_ticks != expected_start_ticks:
        raise ProcessLookupError("process lifetime changed before metadata read")
    root = Path("/proc") / str(pid)
    cwd = None
    contexts: set[str] = set()
    cwd_status: MetadataStatus = "not_requested"
    context_status: MetadataStatus = "unsupported" if context else "not_requested"
    if paths:
        try:
            cwd = os.readlink(root / "cwd")
            cwd_status = "observed"
        except PermissionError:
            cwd_status = "denied"
        except OSError:
            cwd_status = "unavailable"
    if context and before.command_name == "codex":
        base = context_root if context_root is not None else Path.home() / ".codex" / "sessions"
        context_status = "unavailable"
        complete = True
        try:
            with os.scandir(root / "fd") as entries:
                for index, entry in enumerate(entries):
                    if index >= 128:
                        complete = False
                        break
                    try:
                        target = Path(os.readlink(entry.path))
                        if not target.is_relative_to(base) or ".." in target.parts:
                            continue
                        if not stat.S_ISREG(os.stat(entry.path).st_mode):
                            continue
                    except OSError:
                        complete = False
                        continue
                    match = re.fullmatch(
                        r"rollout-.*-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl",
                        target.name,
                    )
                    if match:
                        contexts.add(match[1])
            if not complete:
                context_status = "partial"
            elif len(contexts) > 1:
                context_status = "conflicting"
            elif contexts:
                context_status = "observed"
        except PermissionError:
            context_status = "denied"
        except OSError:
            context_status = "unavailable"
    after = observe_process(pid)
    if (before.pid, before.start_ticks, before.command_name) != (
        after.pid,
        after.start_ticks,
        after.command_name,
    ):
        raise ProcessLookupError("process changed during metadata read")
    return ProcessMetadata(
        cwd,
        next(iter(contexts)) if context_status == "observed" else None,
        cwd_status,
        context_status,
    )


@dataclass(frozen=True)
class KernelClock:
    """Boot reference that turns kernel start ticks into wall-clock seconds.

    Attributes
    ----------
    boot_time : int
        Unix seconds of the last boot from ``/proc/stat`` ``btime``. The kernel
        records it with one-second resolution against the wall clock, so a
        derived start time inherits that resolution and any later clock step.
    ticks_per_second : int
        Kernel clock ticks per second (``sysconf(_SC_CLK_TCK)``), the unit of
        ``ProcessIdentity.start_ticks``.
    """

    boot_time: int
    ticks_per_second: int

    def started_at(self, start_ticks: int) -> float:
        """Convert boot-relative start ticks into Unix seconds.

        Parameters
        ----------
        start_ticks : int
            Non-negative process start time in kernel ticks since boot.

        Returns
        -------
        float
            Estimated wall-clock start time in Unix seconds, accurate to about
            one second because the boot time itself is whole seconds.

        Raises
        ------
        ValueError
            If ``start_ticks`` is not a non-negative integer.
        """
        if type(start_ticks) is not int or start_ticks < 0:
            raise ValueError("start ticks must be a non-negative integer")
        return self.boot_time + start_ticks / self.ticks_per_second


def kernel_clock() -> KernelClock:
    """Read the boot time and tick rate needed to date a process start.

    Returns
    -------
    KernelClock
        Boot reference from ``/proc/stat`` and the ``SC_CLK_TCK`` tick rate.

    Raises
    ------
    OSError
        The platform has no ``sysconf`` or ``/proc/stat`` cannot be read.
    ValueError
        The tick rate is not positive or ``/proc/stat`` carries no ``btime``
        line within its first 64 KiB.
    """
    if not hasattr(os, "sysconf"):
        raise OSError("kernel clock is unavailable on this platform")
    ticks = os.sysconf("SC_CLK_TCK")
    if ticks <= 0:
        raise ValueError("kernel tick rate must be positive")
    with Path("/proc/stat").open("rb") as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError("kernel stat exceeds limit")
    for line in raw.decode("ascii", errors="replace").splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[0] == "btime" and fields[1].isdecimal():
            return KernelClock(int(fields[1]), ticks)
    raise ValueError("kernel boot time unavailable")


@dataclass(frozen=True)
class ProcessIdentity:
    """One kernel observation; start ticks disambiguate recycled PIDs.

    Attributes
    ----------
    pid, parent_pid : int
        Process and parent identifiers at the time of the kernel stat read.
    start_ticks : int
        Process start time in kernel clock ticks since boot, not Unix seconds.
    state, command_name : str
        Kernel process state and comm; neither proves provider activity.
    """

    pid: int
    parent_pid: int
    start_ticks: int
    state: str
    command_name: str


def observe_process(pid: int) -> ProcessIdentity:
    """Read a same-UID Linux process, raising on exit, denial or invalid metadata.

    Parameters
    ----------
    pid : int
        Positive local PID. No filesystem path is accepted.

    Returns
    -------
    ProcessIdentity
        Kernel state and boot-relative start ticks, not provider activity.
    """
    if type(pid) is not int or pid <= 0:
        raise ValueError("PID must be a positive integer")
    root = Path("/proc") / str(pid)
    if root.stat().st_uid != os.geteuid():
        raise PermissionError("process belongs to another user")
    with (root / "stat").open("rb") as stream:
        raw = stream.read(8193)
    if len(raw) > 8192:
        raise ValueError("process stat exceeds limit")
    text = raw.decode("utf-8", errors="replace")
    first, last = text.index("("), text.rindex(")")
    fields = text[last + 2 :].split()
    result = ProcessIdentity(
        pid, int(fields[1]), int(fields[19]), fields[0], text[first + 1 : last]
    )
    if root.stat().st_uid != os.geteuid():
        raise PermissionError("process ownership changed")
    return result


def _process_entries(pids: tuple[int, ...]) -> Generator[str, None, None]:
    if pids:
        yield from (str(pid) for pid in pids)
    else:
        with os.scandir("/proc") as entries:
            for entry in entries:
                yield entry.name


def discover_processes(
    *,
    pids: tuple[int, ...] = (),
    limit: int = 4096,
    seconds: float = 0.25,
) -> tuple[dict[int, ProcessIdentity], str]:
    """Collect bounded same-user metadata, marking incomplete observations.

    Parameters
    ----------
    pids : tuple of int, optional
        Explicit diagnostic scope; empty discovers same-user processes.
    limit : int, optional
        Non-negative maximum directory entries considered; zero does no scan.
    seconds : float, optional
        Finite non-negative monotonic scan budget representable as a float;
        zero does no scan.

    Returns
    -------
    tuple
        Process map and complete, partial or unavailable status. Completeness
        concerns this bounded observation, not an atomic system-wide snapshot.

    Raises
    ------
    ValueError
        An explicit PID is not a positive integer, limit is not a non-negative
        integer, or seconds is not a finite non-negative number representable
        as a float. Booleans are not accepted as process identifiers or budgets.
    """
    if any(type(pid) is not int or pid <= 0 for pid in pids):
        raise ValueError("PID must be a positive integer")
    if type(limit) is not int or limit < 0:
        raise ValueError("limit must be a non-negative integer")
    if type(seconds) not in (int, float) or not 0 <= seconds <= sys.float_info.max:
        raise ValueError("seconds must be a finite non-negative number")
    if not hasattr(os, "geteuid") or not Path("/proc/self/stat").exists():
        return {}, "unavailable"
    if not pids and os.geteuid() == 0:
        return {}, "unavailable"
    deadline = time.monotonic() + seconds
    result: dict[int, ProcessIdentity] = {}
    status = "complete"
    try:
        entries = _process_entries(pids)
        for index, entry in enumerate(entries):
            if index >= limit or time.monotonic() >= deadline:
                status = "partial"
                break
            if not entry.isdecimal():
                continue
            try:
                item = observe_process(int(entry))
            except PermissionError:
                if pids:
                    status = "partial"
                continue
            except (OSError, ValueError, IndexError):
                status = "partial"
                continue
            result[item.pid] = item
    except OSError:
        return result, "unavailable"
    finally:
        entries.close()
    return result, status
