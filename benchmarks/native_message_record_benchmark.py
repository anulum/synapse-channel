# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — cost of recording a native vendor message
"""Measure what one ``native_message_record`` costs, in the parser and through a hub.

The verb stores the record of a message that travelled over a vendor's own
channel. Two costs matter to whoever calls it after every such message:

* **Validation.** ``parse_native_message_record`` checks the vocabulary, the
  bounds and that an inline text hashes to its digest. It is timed over a
  short and a long text, because the digest is the part that grows with size.
* **The durable round trip.** A real hub with an on-disk WAL journal serves on
  a loopback port; one real WebSocket connection sends distinct records one
  after another and waits for each ``native_message_recorded`` reply. The time
  per record covers the frame, the gates, the atomic journal commit with its
  idempotency row, and the reply.

Each round-trip run stays below the verb's own ingress quota (600 records and
8 MiB per minute and principal), which is the real ceiling of sustained
ingest; the quota is reported with the result rather than lifted for the
measurement. That is why the run with long texts sends fewer records.

Wall-clock times are host-specific. Each result records the host, the Python
version, the system load before and after, and that the run was not isolated
on reserved cores: it is a functional baseline, not a production claim.

Run with ``PYTHONPATH=src python benchmarks/native_message_record_benchmark.py``;
results are written to ``benchmarks/results/native_message_record_benchmark.json``.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import os
import platform
import socket
import tempfile
import time
from pathlib import Path
from typing import Any

from websockets.asyncio.client import connect

from synapse_channel.core.handlers.native_message import (
    QUOTA_BYTES,
    QUOTA_EVENTS,
    QUOTA_WINDOW_SECONDS,
)
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.native_message import parse_native_message_record
from synapse_channel.core.persistence import EventStore

BENCHMARK_DIR = Path(__file__).resolve().parent
DEFAULT_RESULTS = BENCHMARK_DIR / "results" / "native_message_record_benchmark.json"

PARSE_COUNT = 20_000
"""Frames validated per text size."""

TEXT_SIZES = (256, 32_768)
"""Message text sizes in bytes: a short note and half the inline bound."""

ROUND_TRIP_RUNS = ((256, 500), (32_768, 200))
"""``(text bytes, records)`` sent through the hub; each run stays inside the quota."""

SEAT = "BENCH/recorder"


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


def load_average() -> list[float]:
    """Return the 1, 5 and 15 minute system load, or an empty list where unsupported."""
    try:
        return [round(value, 2) for value in os.getloadavg()]
    except OSError:
        return []


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


def record_frame(index: int, text_bytes: int) -> dict[str, Any]:
    """Return one valid sender-side record frame whose text has ``text_bytes`` bytes.

    Parameters
    ----------
    index : int
        Distinguishes the frames of one run: it sets the retry key and the
        native message identifier.
    text_bytes : int
        Size of the ASCII message text.
    """
    text = ("x" * text_bytes)[:text_bytes]
    encoded = text.encode("utf-8")
    return {
        "sender": SEAT,
        "target": "System",
        "type": "native_message_record",
        "payload": "",
        "idem_key": f"bench-{index}",
        "channel": "claude_cross_session",
        "direction": "sent",
        "phase": "outcome",
        "outcome": "queued",
        "sender_seat": SEAT,
        "recipient_seat": "BENCH/peer",
        "native_message_id": f"bench-message-{index}",
        "sent_at": "2026-10-05T12:00:00.000000Z",
        "text_sha256": hashlib.sha256(encoded).hexdigest(),
        "text_bytes": len(encoded),
        "text": text,
    }


def measure_parse(count: int, text_bytes: int) -> dict[str, Any]:
    """Validate ``count`` frames of one text size and return the latency distribution."""
    frame = record_frame(0, text_bytes)
    latencies: list[float] = []
    span_start = time.perf_counter()
    for _ in range(count):
        start = time.perf_counter()
        parse_native_message_record(frame)
        latencies.append(time.perf_counter() - start)
    span = time.perf_counter() - span_start
    return {
        "count": count,
        "text_bytes": text_bytes,
        "throughput_per_second": count / span if span > 0 else float("inf"),
        **_percentiles(latencies),
    }


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


async def _listening(port: int, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            _, writer = await asyncio.open_connection("127.0.0.1", port)
        except OSError:
            await asyncio.sleep(0.02)
            continue
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()
        return
    raise TimeoutError(f"hub did not start listening on {port}")


async def _round_trips(count: int, text_bytes: int, directory: str) -> dict[str, Any]:
    store = EventStore(str(Path(directory) / "native_message_record_bench.db"))
    hub = SynapseHub(hub_id="bench-native", journal=store)
    port = _free_port()
    serving = asyncio.create_task(hub.serve("127.0.0.1", port))
    latencies: list[float] = []
    try:
        await _listening(port)
        async with connect(f"ws://127.0.0.1:{port}") as websocket:
            while json.loads(await websocket.recv()).get("type") != "welcome":
                pass
            span_start = time.perf_counter()
            for index in range(count):
                frame = json.dumps(record_frame(index, text_bytes))
                start = time.perf_counter()
                await websocket.send(frame)
                while True:
                    reply = json.loads(await websocket.recv())
                    if reply.get("type") == "native_message_recorded":
                        break
                    if reply.get("type") in {"native_message_rejected", "error"}:
                        raise RuntimeError(f"record {index} was refused: {reply}")
                latencies.append(time.perf_counter() - start)
            span = time.perf_counter() - span_start
    finally:
        serving.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await serving
    stored = sum(1 for event in store.read_all() if event.kind == EventKind.NATIVE_MESSAGE)
    store.close()
    return {
        "count": count,
        "text_bytes": text_bytes,
        "stored_events": stored,
        "throughput_per_second": count / span if span > 0 else float("inf"),
        **_percentiles(latencies),
    }


def measure_round_trip(count: int, text_bytes: int) -> dict[str, Any]:
    """Record ``count`` messages through a real hub and return the latency distribution.

    Parameters
    ----------
    count : int
        Records sent one after another over one connection. The run must stay
        inside the verb's event and byte quota, or the hub refuses the surplus
        and the run raises.
    text_bytes : int
        Size of each message text.

    Returns
    -------
    dict[str, Any]
        ``count``, ``text_bytes``, ``stored_events`` (read back from the
        journal), ``throughput_per_second`` and the latency percentiles of
        one request-to-reply round trip, in microseconds.
    """
    with tempfile.TemporaryDirectory() as directory:
        return asyncio.run(_round_trips(count, text_bytes, directory))


def collect(
    *,
    parse_count: int = PARSE_COUNT,
    text_sizes: tuple[int, ...] = TEXT_SIZES,
    round_trip_runs: tuple[tuple[int, int], ...] = ROUND_TRIP_RUNS,
) -> dict[str, Any]:
    """Run every measurement and return the structured result."""
    return {
        "parse": [measure_parse(parse_count, size) for size in text_sizes],
        "round_trip": [measure_round_trip(count, size) for size, count in round_trip_runs],
        "ingress_quota": {
            "events": QUOTA_EVENTS,
            "bytes": QUOTA_BYTES,
            "window_seconds": QUOTA_WINDOW_SECONDS,
        },
    }


def run(
    results_path: Path = DEFAULT_RESULTS, *, write: bool = True, **kwargs: Any
) -> dict[str, Any]:
    """Collect the measurements with their run context and optionally write the JSON.

    The context names the host, the load before and after, and that the run
    shared the machine with other work.
    """
    before = load_average()
    measurements = collect(**kwargs)
    summary = {
        "host": host_profile(),
        "isolation": "none: shared workstation, no reserved cores, no pinned affinity",
        "load_average_before": before,
        "load_average_after": load_average(),
        **measurements,
    }
    if write:
        results_path.parent.mkdir(parents=True, exist_ok=True)
        results_path.write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return summary


def main(argv: list[str] | None = None) -> int:
    """Run the native-message record benchmark and write its results."""
    parser = argparse.ArgumentParser(description="Measure the cost of a native-message record.")
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--parse-count", type=int, default=PARSE_COUNT)
    parser.add_argument("--text-sizes", type=int, nargs="+", default=list(TEXT_SIZES))
    parser.add_argument(
        "--round-trip",
        type=int,
        nargs=2,
        action="append",
        metavar=("TEXT_BYTES", "RECORDS"),
        help="One round-trip run; repeatable. Defaults to the documented runs.",
    )
    args = parser.parse_args(argv)
    runs = tuple((size, count) for size, count in args.round_trip or ROUND_TRIP_RUNS)
    summary = run(
        args.results,
        parse_count=args.parse_count,
        text_sizes=tuple(args.text_sizes),
        round_trip_runs=runs,
    )
    trip = summary["round_trip"][0]
    print(
        f"durable record round trip: p50 {trip['p50_us']:.0f} us, p99 {trip['p99_us']:.0f} us, "
        f"{trip['throughput_per_second']:.0f} records/s — results in {args.results}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
