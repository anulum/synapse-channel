# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — benchmark scorecard with honest host context
"""Scorecard wrapping benchmark results in the context that makes them honest.

A throughput number without its host context is marketing, not measurement.
The scorecard records the package version, interpreter, platform, CPU model
(read from ``/proc/cpuinfo`` where available, never guessed), logical CPU
count, CPU-frequency governor, and the 1/5/15-minute load averages before
and after the run — and it labels the isolation method explicitly. A run of
``synapse benchmark`` on a shared workstation is labelled exactly that:
functional and regression evidence, not an isolated-core production claim.
"""

from __future__ import annotations

import json
import os
import platform
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from synapse_channel import __version__
from synapse_channel.benchmark.probes import ProbeResult

NON_ISOLATED_LABEL = (
    "none: shared-workstation run — functional/regression evidence, "
    "not an isolated-core production benchmark"
)
"""Isolation label stamped on every scorecard this command produces."""

_CPUINFO_PATH = Path("/proc/cpuinfo")
_GOVERNOR_PATH = Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor")


@dataclass(frozen=True)
class HostContext:
    """The environment one benchmark run executed in.

    Attributes
    ----------
    package_version : str
        Installed ``synapse-channel`` version the probes exercised.
    python : str
        Interpreter version string.
    platform : str
        ``platform.platform()`` of the host.
    cpu_model : str
        CPU model name read from ``/proc/cpuinfo``, or ``"unknown"``.
    cpu_count : int
        Logical CPU count.
    governor : str
        CPU-frequency governor of cpu0, or ``"unknown"``.
    load_before, load_after : tuple[float, float, float]
        1/5/15-minute load averages around the run.
    isolation : str
        How the run was isolated. This command always reports the
        shared-workstation label; isolated-core runs are a separate,
        deliberate exercise.
    started_at : float
        UNIX timestamp the run started.
    """

    package_version: str
    python: str
    platform: str
    cpu_model: str
    cpu_count: int
    governor: str
    load_before: tuple[float, float, float]
    load_after: tuple[float, float, float]
    isolation: str
    started_at: float


@dataclass(frozen=True)
class Scorecard:
    """One benchmark run: the host context plus every probe result."""

    context: HostContext
    results: tuple[ProbeResult, ...]


def _cpu_model() -> str:
    """Return the CPU model name from ``/proc/cpuinfo``, or ``"unknown"``."""
    try:
        for line in _CPUINFO_PATH.read_text(encoding="utf-8").splitlines():
            if line.lower().startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return "unknown"


def _governor() -> str:
    """Return cpu0's frequency governor, or ``"unknown"``."""
    try:
        return _GOVERNOR_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"


def _load_average() -> tuple[float, float, float]:
    """Return the 1/5/15-minute load averages, or zeros when unavailable.

    ``os.getloadavg`` is POSIX-only; Windows and other hosts report zeros
    rather than raising ``AttributeError``.
    """
    getloadavg = getattr(os, "getloadavg", None)
    if getloadavg is None:
        return (0.0, 0.0, 0.0)
    try:
        one, five, fifteen = getloadavg()
    except OSError:
        return (0.0, 0.0, 0.0)
    return (one, five, fifteen)


def capture_host_context() -> HostContext:
    """Capture the pre-run host context (load_after is filled at finish)."""
    load = _load_average()
    return HostContext(
        package_version=__version__,
        python=sys.version.split()[0],
        platform=platform.platform(),
        cpu_model=_cpu_model(),
        cpu_count=os.cpu_count() or 0,
        governor=_governor(),
        load_before=load,
        load_after=load,
        isolation=NON_ISOLATED_LABEL,
        started_at=time.time(),
    )


def finish_scorecard(context: HostContext, results: tuple[ProbeResult, ...]) -> Scorecard:
    """Stamp the post-run load average and assemble the scorecard."""
    finished = HostContext(
        package_version=context.package_version,
        python=context.python,
        platform=context.platform,
        cpu_model=context.cpu_model,
        cpu_count=context.cpu_count,
        governor=context.governor,
        load_before=context.load_before,
        load_after=_load_average(),
        isolation=context.isolation,
        started_at=context.started_at,
    )
    return Scorecard(context=finished, results=results)


def scorecard_to_json(scorecard: Scorecard) -> dict[str, object]:
    """Return a stable JSON-compatible representation of one run."""
    context = scorecard.context
    return {
        "context": {
            "package_version": context.package_version,
            "python": context.python,
            "platform": context.platform,
            "cpu_model": context.cpu_model,
            "cpu_count": context.cpu_count,
            "governor": context.governor,
            "load_before": list(context.load_before),
            "load_after": list(context.load_after),
            "isolation": context.isolation,
            "started_at": context.started_at,
        },
        "results": [
            {
                "name": result.name,
                "iterations": result.iterations,
                "duration_seconds": result.duration_seconds,
                "metrics": result.metrics,
                "notes": list(result.notes),
            }
            for result in scorecard.results
        ],
        "note": "installed-version scorecard; numbers are host-dependent",
    }


def write_scorecard(path: Path, scorecard: Scorecard) -> None:
    """Write the scorecard JSON to ``path``, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(scorecard_to_json(scorecard), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def render_scorecard_human(scorecard: Scorecard) -> str:
    """Render one run as compact terminal text."""
    context = scorecard.context
    lines = [
        f"synapse-channel {context.package_version} benchmark scorecard",
        f"python {context.python} · {context.platform}",
        f"cpu: {context.cpu_model} ({context.cpu_count} logical) · governor: {context.governor}",
        (
            f"load 1/5/15 before: {context.load_before[0]:.2f}/{context.load_before[1]:.2f}/"
            f"{context.load_before[2]:.2f} · after: {context.load_after[0]:.2f}/"
            f"{context.load_after[1]:.2f}/{context.load_after[2]:.2f}"
        ),
        f"isolation: {context.isolation}",
        "",
    ]
    for result in scorecard.results:
        metrics = "  ".join(
            f"{name}={value:,.2f}" for name, value in sorted(result.metrics.items())
        )
        lines.append(
            f"{result.name}: {result.iterations} iterations in "
            f"{result.duration_seconds:.3f}s  {metrics}"
        )
        lines.extend(f"  note: {note}" for note in result.notes)
    return "\n".join(lines)
