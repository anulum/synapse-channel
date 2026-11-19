# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the restore's pacing: headroom on the host before each seat starts
"""Headroom on the host before a restored seat starts, and the spacing between starts.

An agent's resident memory grows for tens of seconds after its start, so
starts compound. Before each start the restore waits until the host has:

* ``MemAvailable`` of at least the larger of 4 GiB and 15 % of ``MemTotal``;
* a 1-minute load of at most 0.75 times the CPU count;
* memory pressure (``/proc/pressure/memory``, ``some avg10``) below 10, where
  the kernel reports it;

and at least 30 s have passed since the previous start. When there is no
headroom within 300 s, the restore stops.

A host may set stricter values in ``$XDG_CONFIG_HOME/synapse/restore.json``
(``~/.config/synapse/restore.json``), under ``hosts`` and its host name::

    {"hosts": {"ws": {"min_available_gib": 8, "max_load_per_cpu": 0.5}}}

A value looser than the default is ignored and named, never applied.
"""

from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any

GIB = 1024**3

TIMEOUT_RANGE = (30.0, 3600.0)
"""The bounds a host's headroom timeout may take."""


@dataclass(frozen=True)
class Limits:
    """What the host must have before a start, and how the starts are paced."""

    min_available_gib: float = 4.0
    min_available_share: float = 0.15
    max_load_per_cpu: float = 0.75
    max_memory_pressure: float = 10.0
    spacing_seconds: float = 30.0
    timeout_seconds: float = 300.0


DEFAULT_LIMITS = Limits()

_FLOORS = ("min_available_gib", "min_available_share", "spacing_seconds")
"""Values a host may only raise."""

_CEILINGS = ("max_load_per_cpu", "max_memory_pressure")
"""Values a host may only lower."""


def config_path(environ: Mapping[str, str]) -> Path | None:
    """Return the restore configuration file the environment names, if any."""
    base = environ.get("XDG_CONFIG_HOME", "").strip()
    home = environ.get("HOME", "").strip()
    if base and Path(base).is_absolute():
        return Path(base) / "synapse" / "restore.json"
    return Path(home) / ".config" / "synapse" / "restore.json" if home else None


def load_limits(path: Path | None, host: str) -> tuple[Limits, list[str]]:
    """Return this host's limits and a note for each value that was not applied.

    A missing file gives the defaults. A file that cannot be read as JSON is an
    error: the owner wrote it to make the restore stricter.

    Raises
    ------
    ValueError
        When the file exists but is not a JSON object.
    """
    if path is None or not path.exists():
        return DEFAULT_LIMITS, []
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"{path} cannot be read: {exc}") from exc
    hosts = document.get("hosts") if isinstance(document, dict) else None
    if not isinstance(hosts, dict):
        raise ValueError(f"{path} must hold an object with a 'hosts' object")
    given = hosts.get(host, {})
    if not isinstance(given, dict):
        raise ValueError(f"{path}: hosts.{host} must be an object")
    return _applied(given)


def _applied(given: Mapping[str, Any]) -> tuple[Limits, list[str]]:
    """Apply only the stricter values; name every value that was not applied."""
    known = {item.name for item in fields(Limits)}
    limits, notes = DEFAULT_LIMITS, []
    for name, value in given.items():
        default = getattr(DEFAULT_LIMITS, name, None)
        if name not in known or isinstance(value, bool) or not isinstance(value, int | float):
            notes.append(f"{name}: not a known number, ignored")
        elif _stricter(name, float(value), float(default or 0.0)):
            limits = replace(limits, **{name: float(value)})
        else:
            notes.append(f"{name}: {value} is looser than the default {default}, ignored")
    return limits, notes


def _stricter(name: str, value: float, default: float) -> bool:
    if name in _FLOORS:
        return value >= default
    if name in _CEILINGS:
        return 0.0 < value <= default
    return TIMEOUT_RANGE[0] <= value <= TIMEOUT_RANGE[1]


@dataclass(frozen=True)
class Reading:
    """The host's memory, load and memory pressure at one moment."""

    available_bytes: int
    total_bytes: int
    load1: float
    cpus: int
    pressure: float | None


def read_host(proc_root: Path = Path("/proc"), cpus: int | None = None) -> Reading:
    """Read memory, load and memory pressure from ``/proc``.

    Raises
    ------
    OSError
        When ``/proc/meminfo`` or ``/proc/loadavg`` cannot be read.
    ValueError
        When either cannot be parsed, or names no ``MemTotal`` or ``MemAvailable``.
    """
    memory: dict[str, int] = {}
    for line in (proc_root / "meminfo").read_text(encoding="ascii").splitlines():
        name, _, rest = line.partition(":")
        memory[name] = int(rest.split()[0]) * 1024 if rest.split() else 0
    load1 = float((proc_root / "loadavg").read_text(encoding="ascii").split()[0])
    try:
        some = (proc_root / "pressure" / "memory").read_text(encoding="ascii").splitlines()[0]
        pressure: float | None = float(some.split("avg10=")[1].split()[0])
    except (OSError, IndexError, ValueError):
        pressure = None
    if "MemAvailable" not in memory or "MemTotal" not in memory:
        raise ValueError("/proc/meminfo names no MemTotal or MemAvailable")
    return Reading(
        memory["MemAvailable"], memory["MemTotal"], load1, cpus or os.cpu_count() or 1, pressure
    )


def shortfall(limits: Limits, reading: Reading) -> str | None:
    """Return what the host is short of, or ``None`` when a start may go."""
    floor = max(limits.min_available_gib * GIB, limits.min_available_share * reading.total_bytes)
    if reading.available_bytes < floor:
        return (
            f"memory available {reading.available_bytes / GIB:.1f} GiB "
            f"is below {floor / GIB:.1f} GiB"
        )
    ceiling = limits.max_load_per_cpu * reading.cpus
    if reading.load1 > ceiling:
        return f"load {reading.load1:.2f} is above {ceiling:.2f}"
    if reading.pressure is not None and reading.pressure >= limits.max_memory_pressure:
        return f"memory pressure {reading.pressure:.2f} is not below {limits.max_memory_pressure:g}"
    return None


async def wait_for_headroom(
    limits: Limits,
    *,
    read: Callable[[], Reading],
    clock: Callable[[], float],
    sleep: Callable[[float], Awaitable[None]],
    last_start: float | None,
    poll_seconds: float = 5.0,
) -> str | None:
    """Wait out the spacing, then for headroom; return ``None``, or what stayed short.

    The headroom timeout starts once the spacing since ``last_start`` has passed.
    """
    if last_start is not None:
        await sleep(max(0.0, last_start + limits.spacing_seconds - clock()))
    deadline = clock() + limits.timeout_seconds
    while True:
        short = shortfall(limits, read())
        if short is None or clock() >= deadline:
            return short
        await sleep(min(poll_seconds, max(0.0, deadline - clock())))


def describe(limits: Limits) -> str:
    """Return the limits as one line, for the dry run."""
    return (
        f"MemAvailable >= max({limits.min_available_gib:g} GiB, "
        f"{limits.min_available_share:.0%} of MemTotal); "
        f"load1 <= {limits.max_load_per_cpu:g} x CPUs; "
        f"memory PSI some avg10 < {limits.max_memory_pressure:g}; "
        f"{limits.spacing_seconds:g} s between starts; "
        f"stop after {limits.timeout_seconds:g} s without headroom"
    )
