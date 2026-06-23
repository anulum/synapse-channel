# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — profile how per-mutation cost scales with the active claim count
"""Profile how the hub's per-mutation cost scales with the active claim count.

Every state mutation (claim, release, heartbeat, …) lazily expires stale leases,
which scans the live claim set — an O(active_claims) step. This benchmark measures
that scan at growing claim counts, so the scaling profile is data rather than a
guess and the reviewers' "O(n·m)" note can be judged against real numbers.

The number of comparisons per scan is deterministic — it equals the active claim
count — and that is what the tests assert. The wall-clock time is host-specific, so
the host CPU and Python version are recorded with every result and the *linear
shape*, not the absolute times, is the reproducible finding.

Run with ``python benchmarks/scalability_benchmark.py``; results are written to
``benchmarks/results/scalability_benchmark.json``.
"""

from __future__ import annotations

import argparse
import json
import platform
import time
from pathlib import Path
from typing import Any

from synapse_channel.core.state import SynapseState, TaskClaim

BENCHMARK_DIR = Path(__file__).resolve().parent
DEFAULT_RESULTS = BENCHMARK_DIR / "results" / "scalability_benchmark.json"

CLAIM_COUNTS = (10, 100, 1_000, 10_000, 100_000)
"""Active-claim counts to profile, from local-first to far past the design envelope."""

DEFAULT_ITERATIONS = 200
"""Mutations timed per claim count; the mean over iterations smooths jitter."""

NEVER_EXPIRES = 1e18
"""A lease expiry so far ahead that the scan visits every claim but evicts none."""


def host_profile() -> dict[str, str]:
    """Return the host CPU, Python version, and platform so a result is attributable.

    Returns
    -------
    dict[str, str]
        ``cpu``, ``python``, and ``platform`` strings for the running host.
    """
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


def state_with_claims(count: int) -> SynapseState:
    """Build a state holding ``count`` live claims that do not expire during a run.

    Parameters
    ----------
    count : int
        Number of active claims to populate.

    Returns
    -------
    SynapseState
        A registry with ``count`` claims, ready to scan.
    """
    state = SynapseState(default_ttl_seconds=1e12)
    for index in range(count):
        state.claims[f"T{index}"] = TaskClaim(
            task_id=f"T{index}",
            owner="A",
            note="",
            claimed_at=0.0,
            lease_expires_at=NEVER_EXPIRES,
        )
    return state


def measure_mutation_seconds(count: int, iterations: int = DEFAULT_ITERATIONS) -> float:
    """Return the mean seconds one mutation takes over a state of ``count`` claims.

    A heartbeat is the most common mutation and triggers the same lazy claim-expiry
    scan as every claim/release, so it is the honest unit to time.

    Parameters
    ----------
    count : int
        Active claims the scan must visit.
    iterations : int, optional
        Mutations to time; the mean is returned.

    Returns
    -------
    float
        Mean seconds per mutation.
    """
    state = state_with_claims(count)
    start = time.perf_counter()
    for _ in range(iterations):
        state.heartbeat("PROBE", now=1.0)
    return (time.perf_counter() - start) / iterations


def profile(
    counts: tuple[int, ...] = CLAIM_COUNTS, iterations: int = DEFAULT_ITERATIONS
) -> list[dict[str, Any]]:
    """Measure the per-mutation cost at each claim count.

    Parameters
    ----------
    counts : tuple[int, ...], optional
        Active-claim counts to profile.
    iterations : int, optional
        Mutations timed per count.

    Returns
    -------
    list[dict[str, Any]]
        One row per count: the deterministic ``comparisons_per_scan`` (equal to the
        claim count), the measured ``scan_microseconds``, and the
        ``sustained_mutations_per_sec`` at which the scan alone saturates one core.
    """
    rows: list[dict[str, Any]] = []
    for count in counts:
        seconds = measure_mutation_seconds(count, iterations)
        rows.append(
            {
                "active_claims": count,
                "comparisons_per_scan": count,
                "scan_microseconds": round(seconds * 1e6, 2),
                "sustained_mutations_per_sec": int(1.0 / seconds) if seconds > 0 else 0,
            }
        )
    return rows


def run(
    results_path: Path | None = DEFAULT_RESULTS,
    iterations: int = DEFAULT_ITERATIONS,
    counts: tuple[int, ...] = CLAIM_COUNTS,
) -> dict[str, Any]:
    """Run the profile and, when given a path, write the results as JSON.

    Parameters
    ----------
    results_path : pathlib.Path or None, optional
        Where to write the JSON summary; ``None`` skips writing.
    iterations : int, optional
        Mutations timed per claim count.
    counts : tuple[int, ...], optional
        Active-claim counts to profile.

    Returns
    -------
    dict[str, Any]
        The host profile, iteration count, and one row per claim count.
    """
    summary: dict[str, Any] = {
        "host": host_profile(),
        "iterations_per_count": iterations,
        "rows": profile(counts, iterations),
    }
    if results_path is not None:
        results_path.parent.mkdir(parents=True, exist_ok=True)
        rendered = json.dumps(summary, indent=2, sort_keys=True)
        results_path.write_text(rendered + "\n", encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, run the profile, and print a short table."""
    parser = argparse.ArgumentParser(description="Profile per-mutation claim-expiry scan cost.")
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--iterations", type=int, default=DEFAULT_ITERATIONS)
    args = parser.parse_args(argv)

    summary = run(args.results, args.iterations)
    print(f"host: {summary['host']['cpu']} | Python {summary['host']['python']}")
    for row in summary["rows"]:
        print(
            f"  {row['active_claims']:>7} claims  "
            f"{row['scan_microseconds']:>9.2f} us/mutation  "
            f"~{row['sustained_mutations_per_sec']:>10} mutations/s on one core"
        )
    print(f"results written to {args.results}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
