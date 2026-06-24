# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — profile lease-expiry and event-replay cost at scale
"""Profile how lease expiry and event replay scale with the work the hub holds.

Two costs grow with scale, and this benchmark measures both at counts from the
local-first envelope to far past it, so the scaling profile is data rather than a
guess.

* **Lease expiry.** Every mutation lazily expires lapsed leases. Since 0.40.0 this
  pops a min-heap keyed by expiry rather than scanning the whole claim set, so the
  cost depends on how many leases are *actually due*, not on the total. The
  ``steady`` measurement (a heartbeat over many live claims that expires nothing)
  is the common case and is near-constant in the claim count; the ``mass`` case
  (every claim lapses at once) drains the heap in ``O(n log n)``, the amortised
  worst case.
* **Event replay.** A hub with a durable log rebuilds its state on start-up by
  replaying the log, an ``O(events)`` pass. The ``replay`` measurement times that
  rebuild at growing event counts.

The wall-clock times are host-specific, so the host CPU and Python version are
recorded with every result and the *shape* (near-flat steady expiry, linear
replay), not the absolute times, is the reproducible finding. Live-hub storm
scenarios (100-agent reconnect/wake storms, resource-offer floods) need an
integration harness with real sockets and are out of scope for this in-process
micro-benchmark.

Run with ``python benchmarks/scalability_benchmark.py``; results are written to
``benchmarks/results/scalability_benchmark.json``.
"""

from __future__ import annotations

import argparse
import json
import platform
import tempfile
import time
from pathlib import Path
from typing import Any

from synapse_channel.core.journal import EventKind, replay
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.state import SynapseState, TaskClaim

BENCHMARK_DIR = Path(__file__).resolve().parent
DEFAULT_RESULTS = BENCHMARK_DIR / "results" / "scalability_benchmark.json"

CLAIM_COUNTS = (10, 100, 1_000, 10_000, 100_000)
"""Active-claim counts to profile, from local-first to far past the design envelope."""

REPLAY_COUNTS = (100, 1_000, 10_000, 100_000)
"""Durable-event counts to profile for the start-up replay rebuild."""

DEFAULT_ITERATIONS = 200
"""Steady heartbeats timed per claim count; the mean over iterations smooths jitter."""

NEVER_EXPIRES = 1e18
"""A lease expiry so far ahead that a heartbeat visits the heap top but evicts nothing."""

EXPIRED_LEASE = 30.0
"""A lease used for the mass-expiry case; a far-future probe time drains every claim."""


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


def state_with_claims(count: int, *, lease: float = NEVER_EXPIRES) -> SynapseState:
    """Build a state holding ``count`` claims with the lease heap populated.

    The claims are inserted directly and the lease heap is rebuilt once
    (``O(count)``), rather than driven through :meth:`SynapseState.claim` whose
    per-call scope scan would make set-up quadratic — set-up is not the measured
    operation, the subsequent expiry is.

    Parameters
    ----------
    count : int
        Number of active claims to populate.
    lease : float, optional
        Lease expiry stamped on every claim.

    Returns
    -------
    SynapseState
        A registry with ``count`` claims and a matching lease heap.
    """
    state = SynapseState(default_ttl_seconds=1e12)
    for index in range(count):
        state.claims[f"T{index}"] = TaskClaim(
            task_id=f"T{index}",
            owner="A",
            note="",
            claimed_at=0.0,
            lease_expires_at=lease,
            epoch=index + 1,
        )
    state._epoch_seq = count
    state.reindex_leases()
    return state


def measure_steady_heartbeat_seconds(count: int, iterations: int = DEFAULT_ITERATIONS) -> float:
    """Return the mean seconds a heartbeat takes over ``count`` non-expiring claims.

    This is the common case: the heap top is far in the future, so the expiry pass
    pops nothing. With the heap it is near-constant in ``count``.
    """
    state = state_with_claims(count, lease=NEVER_EXPIRES)
    start = time.perf_counter()
    for _ in range(iterations):
        state.heartbeat("PROBE", now=1.0)
    return (time.perf_counter() - start) / iterations


def measure_mass_expiry_seconds(count: int) -> float:
    """Return the seconds to expire ``count`` claims that all lapse at once.

    The amortised worst case: one pass drains the whole heap in ``O(n log n)``.
    """
    state = state_with_claims(count, lease=EXPIRED_LEASE)
    start = time.perf_counter()
    state.heartbeat("PROBE", now=1e6)
    elapsed = time.perf_counter() - start
    assert not state.claims  # every lease was due and the heap drained them all
    return elapsed


def measure_replay_seconds(count: int) -> float:
    """Return the seconds to replay a durable log of ``count`` claim events.

    The events are written non-durably (set-up is not the measured cost); only the
    :func:`~synapse_channel.core.journal.replay` rebuild is timed.
    """
    with tempfile.TemporaryDirectory() as directory:
        store = EventStore(Path(directory) / "events.db")
        for index in range(count):
            claim = TaskClaim(
                task_id=f"T{index}",
                owner="A",
                note="",
                claimed_at=0.0,
                lease_expires_at=NEVER_EXPIRES,
                epoch=index + 1,
            )
            store.append(EventKind.CLAIM, claim.as_dict(), durable=False)
        start = time.perf_counter()
        replay(store, now=1.0)
        elapsed = time.perf_counter() - start
        store.close()
    return elapsed


def profile(
    claim_counts: tuple[int, ...] = CLAIM_COUNTS,
    replay_counts: tuple[int, ...] = REPLAY_COUNTS,
    iterations: int = DEFAULT_ITERATIONS,
) -> dict[str, list[dict[str, Any]]]:
    """Measure the expiry and replay costs at each count.

    Returns
    -------
    dict[str, list[dict[str, Any]]]
        ``expiry`` rows (per claim count: the steady-heartbeat and mass-expiry
        microseconds) and ``replay`` rows (per event count: the replay milliseconds).
    """
    expiry_rows: list[dict[str, Any]] = []
    for count in claim_counts:
        steady = measure_steady_heartbeat_seconds(count, iterations)
        mass = measure_mass_expiry_seconds(count)
        expiry_rows.append(
            {
                "active_claims": count,
                "steady_heartbeat_microseconds": round(steady * 1e6, 3),
                "mass_expiry_microseconds": round(mass * 1e6, 2),
            }
        )
    replay_rows: list[dict[str, Any]] = []
    for count in replay_counts:
        seconds = measure_replay_seconds(count)
        replay_rows.append(
            {
                "events": count,
                "replay_milliseconds": round(seconds * 1e3, 3),
                "events_per_sec": int(count / seconds) if seconds > 0 else 0,
            }
        )
    return {"expiry": expiry_rows, "replay": replay_rows}


def run(
    results_path: Path | None = DEFAULT_RESULTS,
    iterations: int = DEFAULT_ITERATIONS,
    claim_counts: tuple[int, ...] = CLAIM_COUNTS,
    replay_counts: tuple[int, ...] = REPLAY_COUNTS,
) -> dict[str, Any]:
    """Run the profile and, when given a path, write the results as JSON."""
    rows = profile(claim_counts, replay_counts, iterations)
    summary: dict[str, Any] = {
        "host": host_profile(),
        "iterations_per_count": iterations,
        "expiry": rows["expiry"],
        "replay": rows["replay"],
    }
    if results_path is not None:
        results_path.parent.mkdir(parents=True, exist_ok=True)
        rendered = json.dumps(summary, indent=2, sort_keys=True)
        results_path.write_text(rendered + "\n", encoding="utf-8")
    return summary


def _counts(raw: str | None, default: tuple[int, ...]) -> tuple[int, ...]:
    """Parse a comma-separated count list, falling back to ``default`` when unset."""
    if not raw:
        return default
    return tuple(int(part) for part in raw.split(",") if part.strip())


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, run the profile, and print a short table."""
    parser = argparse.ArgumentParser(description="Profile lease-expiry and event-replay cost.")
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--iterations", type=int, default=DEFAULT_ITERATIONS)
    parser.add_argument(
        "--claim-counts", default=None, help="Comma-separated active-claim counts (e.g. 10,100)."
    )
    parser.add_argument(
        "--replay-counts", default=None, help="Comma-separated durable-event counts to replay."
    )
    args = parser.parse_args(argv)

    summary = run(
        args.results,
        args.iterations,
        claim_counts=_counts(args.claim_counts, CLAIM_COUNTS),
        replay_counts=_counts(args.replay_counts, REPLAY_COUNTS),
    )
    print(f"host: {summary['host']['cpu']} | Python {summary['host']['python']}")
    print("lease expiry (heap-based since 0.40.0):")
    for row in summary["expiry"]:
        print(
            f"  {row['active_claims']:>7} claims  "
            f"steady {row['steady_heartbeat_microseconds']:>8.3f} us  "
            f"mass {row['mass_expiry_microseconds']:>10.2f} us"
        )
    print("event replay (start-up rebuild):")
    for row in summary["replay"]:
        print(
            f"  {row['events']:>7} events  "
            f"{row['replay_milliseconds']:>9.3f} ms  ~{row['events_per_sec']:>9} events/s"
        )
    print(f"results written to {args.results}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
