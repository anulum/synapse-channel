# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — where the time of a claim grant and a directed chat goes
"""Profile the claim-grant and mailbox hot paths, stage by stage.

The workload is ``coordination_handlers_benchmark``: receipt-requested
directed chats and complete keyed lease cycles, run through two real clients
and an isolated durable loopback hub. The hub and both clients share one
process, so a deterministic profile of the whole workload shows where its time
goes. Each function's own time is assigned to one fixed stage: durable SQLite
commits, other SQLite statements, ``fsync``, socket I/O, WebSocket framing,
JSON, hub logic, client logic, ``asyncio`` and everything else.

The question it answers is what a faster core would buy. A Rust core speeds up
Python code, not durable commits. The stage shares therefore bound what a
rewrite could save on these paths at the local-first scale.

These are serial measurements on a shared host: regression and decision
evidence, not isolated throughput claims.
"""

from __future__ import annotations

import argparse
import asyncio
import cProfile
import importlib.util
import json
import os
import platform
import pstats
import statistics
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, cast

HERE = Path(__file__).resolve().parent
DEFAULT_RESULTS = HERE / "results" / "hot_path_profile.json"
DEFAULT_RUNS = 3
DEFAULT_CHATS = 100
DEFAULT_LEASE_CYCLES = 20

STAGES = (
    "sqlite durable commit",
    "sqlite statements",
    "fsync",
    "socket io",
    "websocket framing",
    "json",
    "hub logic",
    "client logic",
    "asyncio",
    "other",
)
"""Every stage a function's own time can be assigned to."""

PYTHON_STAGES = ("hub logic", "client logic", "websocket framing", "json", "asyncio")
"""The stages a faster core could shorten: Python code on these paths."""


def classify(filename: str, function: str) -> str:
    """Return the stage of one profiled function.

    ``filename`` is ``~`` for built-in functions, as :mod:`cProfile` reports
    them; the stage then follows the built-in's name.
    """
    path = filename.replace("\\", "/")
    if filename == "~":
        if "sqlite3.Connection" in function and "commit" in function:
            return "sqlite durable commit"
        if "sqlite3" in function:
            return "sqlite statements"
        if "fsync" in function:
            return "fsync"
        if "_socket.socket" in function or "select" in function or "epoll" in function:
            return "socket io"
        return "other"
    for marker, stage in (
        ("/websockets/", "websocket framing"),
        ("/json/", "json"),
        ("/synapse_channel/client/", "client logic"),
        ("/synapse_channel/", "hub logic"),
        ("/asyncio/", "asyncio"),
        ("/sqlite3/", "sqlite statements"),
    ):
        if marker in path:
            return stage
    return "other"


Entries = dict[tuple[str, int, str], tuple[Any, ...]]
"""``pstats`` entries, ``(file, line, function)`` to ``(calls, primitive calls, own, cumulative,
callers)``."""


def entries(stats: pstats.Stats) -> Entries:
    """Return the raw entries of a profile, which ``pstats`` keeps in ``stats``."""
    return cast(Entries, vars(stats)["stats"])


def stage_seconds(stats: pstats.Stats) -> dict[str, float]:
    """Return each stage's total own time from one profile."""
    totals = dict.fromkeys(STAGES, 0.0)
    for (filename, _line, function), entry in entries(stats).items():
        totals[classify(filename, function)] += float(entry[2])
    return totals


def _workload() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "coordination_handlers_benchmark", HERE / "coordination_handlers_benchmark.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def profile_once(*, chats: int, cycles: int) -> dict[str, Any]:
    """Run the workload once under the profiler; return its stages and latencies."""
    workload = _workload()
    profiler = cProfile.Profile()
    profiler.enable()
    measured = asyncio.run(workload.profile(chats=chats, cycles=cycles))
    profiler.disable()
    stats = pstats.Stats(profiler)
    seconds = stage_seconds(stats)
    total = sum(seconds.values())
    commits = sum(
        entry[1]
        for (filename, _line, function), entry in entries(stats).items()
        if classify(filename, function) == "sqlite durable commit"
    )
    operations = chats + 5 * cycles
    return {
        "profiled_seconds": total,
        "stage_share": {stage: seconds[stage] / total for stage in STAGES},
        "durable_commits_per_operation": commits / operations,
        "claim_p50_us": measured["leases"]["claim"]["p50_us"],
        "chat_receipt_p50_us": measured["chat"]["p50_us"],
    }


def summarise(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the median of each figure over the runs, and the Python-code bound."""
    share = {
        stage: statistics.median(run["stage_share"][stage] for run in runs) for stage in STAGES
    }
    return {
        "runs": len(runs),
        "median_stage_share": share,
        "median_python_share": statistics.median(
            sum(run["stage_share"][stage] for stage in PYTHON_STAGES) for run in runs
        ),
        "median_durable_commits_per_operation": statistics.median(
            run["durable_commits_per_operation"] for run in runs
        ),
        "median_claim_p50_us": statistics.median(run["claim_p50_us"] for run in runs),
        "median_chat_receipt_p50_us": statistics.median(run["chat_receipt_p50_us"] for run in runs),
    }


def _positive(value: str) -> int:
    count = int(value)
    if count < 1:
        raise argparse.ArgumentTypeError("count must be positive")
    return count


def main(argv: list[str] | None = None) -> int:
    """Profile the workload ``--runs`` times and write the stage summary."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=_positive, default=DEFAULT_RUNS)
    parser.add_argument("--chats", type=_positive, default=DEFAULT_CHATS)
    parser.add_argument("--lease-cycles", type=_positive, default=DEFAULT_LEASE_CYCLES)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    args = parser.parse_args(argv)
    runs = [profile_once(chats=args.chats, cycles=args.lease_cycles) for _ in range(args.runs)]
    result = summarise(runs)
    result.update(
        chats=args.chats,
        lease_cycles=args.lease_cycles,
        per_run=runs,
        host={
            "python": platform.python_version(),
            "platform": platform.platform(),
            "cpu": platform.processor(),
            "cpus": os.cpu_count(),
            "load_average": list(os.getloadavg()) if hasattr(os, "getloadavg") else None,
            "isolation": "shared host, serial loopback, deterministic profiler",
            "pid": os.getpid(),
        },
    )
    args.results.parent.mkdir(parents=True, exist_ok=True)
    args.results.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result["median_stage_share"], sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
