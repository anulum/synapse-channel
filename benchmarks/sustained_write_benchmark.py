# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — profile the durable event store under sustained write load
"""Profile the durable event store under sustained write load.

The hub's coordination throughput is bounded by how fast it can *durably* append to
its SQLite WAL event log, and how read-side replay scales as that log grows. The
existing harnesses measure coordination throughput and lease/replay scaling; this one
measures the event store itself, on a real on-disk WAL database, so the durable-write
profile is data rather than a guess.

It measures three things:

* **Write latency under sustained load.** Append many events to a fresh store, timing
  each append, and report the latency distribution (mean / p50 / p95 / p99 / max) and
  the throughput. Two modes: the default ``synchronous=NORMAL`` commit, and the
  ``durable=True`` path that raises ``synchronous=FULL`` to fsync on commit — the cost
  of OS-crash durability, and where the WAL checkpoint and fsync show up.
* **Read-since cost versus log size.** Time a full ``read_since(0)`` replay at growing
  event counts: an ``O(events)`` scan-and-decode whose cost grows linearly with the
  retained log.
* **Compaction's effect on reads.** Build a log, time a replay, delete the oldest half
  (the compaction primitive), and time the replay again — quantifying how much
  retention GC buys read performance.

The wall-clock times are host-specific, so the host CPU and Python version are recorded
with every result; the *shape* — a stable per-event write latency, a linear read scan,
and a read cost that falls with compaction — is the reproducible finding. The durable
fsync latency in particular is dominated by the disk, so it is recorded as a
distribution, not a single number.

Run with ``python benchmarks/sustained_write_benchmark.py``; results are written to
``benchmarks/results/sustained_write_benchmark.json``.
"""

from __future__ import annotations

import argparse
import json
import platform
import tempfile
import time
from pathlib import Path
from typing import Any

from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore

BENCHMARK_DIR = Path(__file__).resolve().parent
DEFAULT_RESULTS = BENCHMARK_DIR / "results" / "sustained_write_benchmark.json"

SUSTAINED_WRITE_COUNT = 50_000
"""Events appended at ``synchronous=NORMAL`` to profile sustained write latency."""

DURABLE_WRITE_COUNT = 2_000
"""Events appended with ``durable=True`` (fsync per commit) — slower, so a smaller run."""

READ_COUNTS = (1_000, 10_000, 50_000)
"""Retained-event counts to profile the ``read_since(0)`` replay scan."""

COMPACTION_COUNT = 50_000
"""Log size at which to measure compaction's effect on read cost."""

_PAYLOAD = {"task_id": "T", "title": "build the wheel", "status": "open", "owner": "agent"}
"""A representative ledger-task payload, so the JSON encode/decode cost is realistic."""


def host_profile() -> dict[str, str]:
    """Return the host CPU, Python version, and platform so a result is attributable."""
    cpu = platform.processor()
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("model name"):
                cpu = line.split(":", 1)[1].strip()
                break
    except OSError:
        pass
    return {
        "cpu": cpu or "unknown",
        "python": platform.python_version(),
        "platform": platform.platform(),
    }


def _percentiles(latencies_seconds: list[float]) -> dict[str, float]:
    """Return mean and p50/p95/p99/max of latencies, in microseconds."""
    ordered = sorted(latencies_seconds)
    n = len(ordered)

    def at(fraction: float) -> float:
        return ordered[min(n - 1, int(fraction * n))]

    return {
        "mean_us": (sum(ordered) / n) * 1e6,
        "p50_us": at(0.50) * 1e6,
        "p95_us": at(0.95) * 1e6,
        "p99_us": at(0.99) * 1e6,
        "max_us": ordered[-1] * 1e6,
    }


def _fresh_store(directory: str) -> EventStore:
    """Open a fresh on-disk WAL event store under ``directory``."""
    return EventStore(str(Path(directory) / "sustained_write_bench.db"))


def measure_write_latency(count: int, *, durable: bool) -> dict[str, Any]:
    """Append ``count`` events to a fresh store and return the latency distribution.

    Each append is timed individually for the distribution; the throughput is the count
    over the wall-clock span. ``durable`` selects the ``synchronous=FULL`` fsync commit.
    """
    with tempfile.TemporaryDirectory() as directory:
        store = _fresh_store(directory)
        latencies: list[float] = []
        span_start = time.perf_counter()
        for _ in range(count):
            start = time.perf_counter()
            store.append(EventKind.LEDGER_TASK, _PAYLOAD, durable=durable)
            latencies.append(time.perf_counter() - start)
        span = time.perf_counter() - span_start
        store.close()
    return {
        "count": count,
        "durable": durable,
        "throughput_eps": count / span if span > 0 else float("inf"),
        **_percentiles(latencies),
    }


def _store_with_events(directory: str, count: int) -> EventStore:
    """Return a store holding ``count`` distinct ledger-task events."""
    store = _fresh_store(directory)
    for index in range(count):
        store.append(EventKind.LEDGER_TASK, {**_PAYLOAD, "task_id": f"T{index}"})
    return store


def measure_read_since_seconds(count: int) -> float:
    """Return the seconds a full ``read_since(0)`` replay takes over ``count`` events."""
    with tempfile.TemporaryDirectory() as directory:
        store = _store_with_events(directory, count)
        start = time.perf_counter()
        rows = store.read_since(0)
        elapsed = time.perf_counter() - start
        store.close()
    if len(rows) != count:
        msg = f"read_since returned {len(rows)} of {count} events"
        raise AssertionError(msg)
    return elapsed


def measure_compaction_read_impact(count: int) -> dict[str, Any]:
    """Return the read-since cost before and after deleting the oldest half of the log."""
    with tempfile.TemporaryDirectory() as directory:
        store = _store_with_events(directory, count)
        before_start = time.perf_counter()
        seqs = [event.seq for event in store.read_since(0)]
        before = time.perf_counter() - before_start
        removed = store.delete(seqs[: len(seqs) // 2])
        after_start = time.perf_counter()
        store.read_since(0)
        after = time.perf_counter() - after_start
        store.close()
    return {"count": count, "removed": removed, "before_seconds": before, "after_seconds": after}


def collect(
    *,
    sustained_count: int = SUSTAINED_WRITE_COUNT,
    durable_count: int = DURABLE_WRITE_COUNT,
    read_counts: tuple[int, ...] = READ_COUNTS,
    compaction_count: int = COMPACTION_COUNT,
) -> dict[str, Any]:
    """Run every measurement and return the result rows."""
    return {
        "write_latency": [
            measure_write_latency(sustained_count, durable=False),
            measure_write_latency(durable_count, durable=True),
        ],
        "read_since": [
            {"count": count, "seconds": measure_read_since_seconds(count)} for count in read_counts
        ],
        "compaction_read_impact": [measure_compaction_read_impact(compaction_count)],
    }


def run(
    results_path: Path = DEFAULT_RESULTS, *, write: bool = True, **kwargs: Any
) -> dict[str, Any]:
    """Collect the measurements, attach the host profile, and optionally write the JSON."""
    summary = {"host": host_profile(), **collect(**kwargs)}
    if write:
        results_path.parent.mkdir(parents=True, exist_ok=True)
        results_path.write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return summary


def main(argv: list[str] | None = None) -> int:
    """Run the sustained-write benchmark and write its results."""
    parser = argparse.ArgumentParser(description="Profile the event store under sustained writes.")
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--sustained-count", type=int, default=SUSTAINED_WRITE_COUNT)
    parser.add_argument("--durable-count", type=int, default=DURABLE_WRITE_COUNT)
    parser.add_argument("--read-counts", type=int, nargs="+", default=list(READ_COUNTS))
    parser.add_argument("--compaction-count", type=int, default=COMPACTION_COUNT)
    args = parser.parse_args(argv)
    summary = run(
        args.results,
        sustained_count=args.sustained_count,
        durable_count=args.durable_count,
        read_counts=tuple(args.read_counts),
        compaction_count=args.compaction_count,
    )
    write = summary["write_latency"][0]
    print(
        f"sustained write: {write['throughput_eps']:.0f} events/s, "
        f"p99 {write['p99_us']:.1f} us — results in {args.results}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
