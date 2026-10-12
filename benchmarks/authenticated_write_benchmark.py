# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — authenticated event-write latency probe
"""Measure real row-authenticated writes, including MAC-index maintenance.

Use a fresh on-disk store and a public fixed fixture key; no enrolled operator
key is read. Every measured row is verified after the run. Measurements share
the host and disk and establish no production throughput claim.
"""

from __future__ import annotations

import argparse
import json
import statistics
import tempfile
import time
from pathlib import Path
from typing import Any

from synapse_channel.core.event_row_mac import RowMacKey
from synapse_channel.core.persistence import EventStore


def measure(count: int, *, durable: bool) -> dict[str, Any]:
    """Measure and verify one authenticated write workload.

    Parameters
    ----------
    count : int
        Positive number of distinct events committed to a fresh WAL store.
    durable : bool
        Select FULL synchronization rather than NORMAL.

    Returns
    -------
    dict
        Count, durability, median/mean microseconds and verified row count.

    Raises
    ------
    ValueError
        If the workload has no events.
    """
    if count < 1:
        raise ValueError("authenticated write count must be positive")
    with tempfile.TemporaryDirectory() as directory:
        with EventStore(Path(directory) / "mac.db") as store:
            key = RowMacKey(b"b" * 32, 0)
            assert not store.enable_row_mac(key)
            latencies = []
            for index in range(count):
                start = time.perf_counter()
                store.append(
                    "chat", {"sender": "BENCH/mac", "payload": str(index)}, durable=durable
                )
                latencies.append((time.perf_counter() - start) * 1e6)
            assert store.count() == count and not store.enable_row_mac(key)
    return {
        "durable": durable,
        "count": count,
        "p50_us": statistics.median(latencies),
        "mean_us": statistics.mean(latencies),
        "authenticated_rows_verified": count,
    }


def main(argv: list[str] | None = None) -> int:
    """Run both synchronization modes and print their verified JSON metrics.

    Parameters
    ----------
    argv : list of str or None, optional
        Explicit CLI arguments, or the process arguments.

    Returns
    -------
    int
        Zero after both real workloads complete and authenticate.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--normal-count", type=int, default=500)
    parser.add_argument("--durable-count", type=int, default=100)
    args = parser.parse_args(argv)
    print(
        json.dumps(
            [measure(args.normal_count, durable=False), measure(args.durable_count, durable=True)]
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
