# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — benchmark probes over real production surfaces
"""Benchmark probes measuring the installed package's production surfaces.

Every probe exercises the same code path production uses — no mocks, no
synthetic shortcuts: durable :class:`~synapse_channel.core.persistence.EventStore`
appends and journal replay against a real temporary SQLite file, the lite
relay encoding over realistic envelopes, and request/response plus
claim-grant round-trips over a real WebSocket connection to an in-process
:class:`~synapse_channel.core.hub.SynapseHub` on a loopback port.

Latencies are wall-clock ``time.perf_counter`` samples; each probe reports
throughput plus p50/p95 per-operation latency so a tail regression is not
hidden by an average.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import statistics
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind, replay
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType, build_envelope
from synapse_channel.core.relay import encode_lite


@dataclass(frozen=True)
class ProbeResult:
    """One probe's measured outcome.

    Attributes
    ----------
    name : str
        Probe identifier, matching the ``--probe`` CLI value.
    iterations : int
        Operations measured.
    duration_seconds : float
        Wall-clock time spent inside the measured section.
    metrics : dict[str, float]
        Named measurements — throughput in operations per second and, where
        per-operation samples exist, ``p50_ms``/``p95_ms`` latencies.
    notes : tuple[str, ...]
        Probe-specific context a reader needs to interpret the numbers.
    """

    name: str
    iterations: int
    duration_seconds: float
    metrics: dict[str, float]
    notes: tuple[str, ...] = ()


def _percentiles_ms(samples: list[float]) -> dict[str, float]:
    """Return p50/p95 of per-operation samples, converted to milliseconds."""
    ordered = sorted(samples)
    return {
        "p50_ms": statistics.median(ordered) * 1000.0,
        "p95_ms": ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))] * 1000.0,
    }


def _claim_payload(index: int) -> dict[str, Any]:
    """Return one realistic durable claim payload with a live lease."""
    now = time.time()
    return {
        "task_id": f"BENCH-{index}",
        "owner": "bench-agent",
        "note": "benchmark write",
        "claimed_at": now,
        "lease_expires_at": now + 3600.0,
        "status": "claimed",
        "worktree": "bench-repo",
        "paths": [f"src/module_{index % 7}.py"],
        "epoch": 1,
    }


def probe_event_store_append(iterations: int) -> ProbeResult:
    """Measure durable event-store append throughput and latency.

    Appends ``iterations`` claim-shaped events with ``durable=True`` — the
    write path every authoritative hub mutation takes — into a fresh SQLite
    store in a temporary directory.
    """
    samples: list[float] = []
    with TemporaryDirectory(prefix="synapse-bench-") as scratch:
        store = EventStore(Path(scratch) / "bench.db")
        try:
            started = time.perf_counter()
            for index in range(iterations):
                before = time.perf_counter()
                store.append(EventKind.CLAIM, _claim_payload(index), ts=float(index), durable=True)
                samples.append(time.perf_counter() - before)
            duration = time.perf_counter() - started
        finally:
            store.close()
    return ProbeResult(
        name="event-store-append",
        iterations=iterations,
        duration_seconds=duration,
        metrics={"events_per_second": iterations / duration, **_percentiles_ms(samples)},
        notes=("durable claim-shaped appends into a fresh temporary SQLite store",),
    )


def probe_event_store_replay(iterations: int) -> ProbeResult:
    """Measure journal replay throughput over a seeded durable log.

    Seeds ``iterations`` claim events, then measures one full
    :func:`~synapse_channel.core.journal.replay` — the restart-recovery path
    that rebuilds hub state from the log.
    """
    with TemporaryDirectory(prefix="synapse-bench-") as scratch:
        store = EventStore(Path(scratch) / "bench.db")
        try:
            for index in range(iterations):
                store.append(EventKind.CLAIM, _claim_payload(index), ts=float(index), durable=True)
            started = time.perf_counter()
            replayed = replay(store)
            duration = time.perf_counter() - started
        finally:
            store.close()
    live_claims = len(replayed.state.claims)
    return ProbeResult(
        name="event-store-replay",
        iterations=iterations,
        duration_seconds=duration,
        metrics={
            "events_per_second": iterations / duration,
            "live_claims_rebuilt": float(live_claims),
        },
        notes=("one full journal replay of a seeded claim log (restart-recovery path)",),
    )


def probe_encode_lite(iterations: int) -> ProbeResult:
    """Measure lite relay encoding throughput and byte reduction.

    Encodes ``iterations`` realistic broadcast envelopes with
    :func:`~synapse_channel.core.relay.encode_lite` and reports messages per
    second plus the byte ratio against the full wire envelope.
    """
    envelopes = [
        build_envelope(
            msg_type=MessageType.CHAT,
            sender=f"agent-{index % 5}",
            target="all",
            payload=f"benchmark message {index}: unit finished, claims released cleanly",
            msg_id=index,
            hub_id="syn-bench",
            task_id=f"BENCH-{index % 11}",
        )
        for index in range(iterations)
    ]
    raw_bytes = sum(len(json.dumps(envelope).encode("utf-8")) for envelope in envelopes)
    started = time.perf_counter()
    encoded = [encode_lite(envelope) for envelope in envelopes]
    duration = time.perf_counter() - started
    # Serialised exactly as the relay mirror writes each line (append_jsonl).
    lite_bytes = sum(
        len(json.dumps(lite, ensure_ascii=True, separators=(",", ":")).encode("utf-8"))
        for lite in encoded
    )
    return ProbeResult(
        name="encode-lite",
        iterations=iterations,
        duration_seconds=duration,
        metrics={
            "messages_per_second": iterations / duration,
            "raw_bytes": float(raw_bytes),
            "lite_bytes": float(lite_bytes),
            "lite_to_raw_ratio": lite_bytes / raw_bytes,
        },
        notes=("chat envelopes with task ids; ratio is lite bytes over full wire bytes",),
    )


class _MessageWaiter:
    """Async message sink that lets a probe await one predicate match."""

    def __init__(self) -> None:
        self._event = asyncio.Event()
        self._predicate: Callable[[dict[str, Any]], bool] | None = None

    async def __call__(self, message: dict[str, Any]) -> None:
        """Record one hub message and wake the waiter on a match."""
        if self._predicate is not None and self._predicate(message):
            self._event.set()

    async def wait_for(
        self, predicate: Callable[[dict[str, Any]], bool], *, timeout: float = 5.0
    ) -> None:
        """Arm ``predicate`` and block until a matching message arrives."""
        self._event = asyncio.Event()
        self._predicate = predicate
        await asyncio.wait_for(self._event.wait(), timeout=timeout)


def _free_port() -> int:
    """Return an OS-assigned free loopback TCP port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("localhost", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


async def _connect_ready_agent(
    waiter: _MessageWaiter, port: int, *, attempts: int = 50
) -> tuple[SynapseAgent, asyncio.Task[None]]:
    """Connect a fresh agent to the hub, retrying until it is accepting.

    ``SynapseAgent.connect`` is once-only — a refused connection ends its
    task — so each attempt constructs a new agent. Retrying the real client
    handshake (instead of probing the port with a bare TCP connect) keeps
    the hub's error log clean.
    """
    for _attempt in range(attempts):
        agent = SynapseAgent(
            "bench-agent",
            waiter,
            uri=f"ws://localhost:{port}",
            heartbeat_interval=60.0,
            verbose=False,
        )
        connection = asyncio.create_task(agent.connect())
        if await agent.wait_until_ready(0.5):
            return agent, connection
        agent.running = False
        connection.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await connection
        await asyncio.sleep(0.05)
    raise TimeoutError("benchmark agent did not receive the hub welcome")


async def _measure_over_live_hub(
    iterations: int,
    exercise: Callable[[SynapseAgent, _MessageWaiter, int], Awaitable[None]],
) -> tuple[list[float], float]:
    """Run ``exercise`` per iteration against a real hub over a real socket.

    Starts an in-process :class:`SynapseHub` on a loopback port, connects one
    :class:`SynapseAgent` through the production WebSocket client, and times
    each iteration of ``exercise``.
    """
    hub = SynapseHub(hub_id="syn-bench")
    port = _free_port()
    server = asyncio.create_task(hub.serve("localhost", port))
    samples: list[float] = []
    try:
        waiter = _MessageWaiter()
        agent, connection = await _connect_ready_agent(waiter, port)
        try:
            started = time.perf_counter()
            for index in range(iterations):
                before = time.perf_counter()
                await exercise(agent, waiter, index)
                samples.append(time.perf_counter() - before)
            duration = time.perf_counter() - started
        finally:
            agent.running = False
            connection.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await connection
    finally:
        server.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await server
    return samples, duration


def probe_hub_roundtrip(iterations: int) -> ProbeResult:
    """Measure request/response round-trips over a real hub socket.

    Each iteration issues one ``who`` request and awaits the matching
    ``who_snapshot`` reply — the same path ``synapse who`` takes.
    """

    async def exercise(agent: SynapseAgent, waiter: _MessageWaiter, _index: int) -> None:
        request = asyncio.ensure_future(
            waiter.wait_for(lambda message: message.get("type") == MessageType.WHO_SNAPSHOT)
        )
        await agent.request_who()
        await request

    samples, duration = asyncio.run(_measure_over_live_hub(iterations, exercise))
    return ProbeResult(
        name="hub-roundtrip",
        iterations=iterations,
        duration_seconds=duration,
        metrics={"roundtrips_per_second": iterations / duration, **_percentiles_ms(samples)},
        notes=("who request to who_snapshot reply over a real loopback WebSocket",),
    )


def probe_claim_grant(iterations: int) -> ProbeResult:
    """Measure the claim-to-grant round-trip over a real hub socket.

    Each iteration claims a fresh task and awaits its ``claim_granted``
    reply — the hot path of the coordination core — then releases it.
    """

    async def exercise(agent: SynapseAgent, waiter: _MessageWaiter, index: int) -> None:
        task_id = f"BENCH-{index}"
        granted = asyncio.ensure_future(
            waiter.wait_for(
                lambda message: (
                    message.get("type") == MessageType.CLAIM_GRANTED
                    and message.get("task_id") == task_id
                )
            )
        )
        await agent.claim(task_id, note="benchmark", paths=(f"src/bench_{index}.py",))
        await granted
        await agent.release(task_id)

    samples, duration = asyncio.run(_measure_over_live_hub(iterations, exercise))
    return ProbeResult(
        name="claim-grant",
        iterations=iterations,
        duration_seconds=duration,
        metrics={"claims_per_second": iterations / duration, **_percentiles_ms(samples)},
        notes=("claim to claim_granted round-trip plus release, fresh task per iteration",),
    )


#: Probe registry: name → (default iterations, implementation).
PROBES: dict[str, tuple[int, Callable[[int], ProbeResult]]] = {
    "event-store-append": (500, probe_event_store_append),
    "event-store-replay": (2000, probe_event_store_replay),
    "encode-lite": (2000, probe_encode_lite),
    "hub-roundtrip": (100, probe_hub_roundtrip),
    "claim-grant": (100, probe_claim_grant),
}


def run_probes(names: list[str], *, iterations: int | None = None) -> tuple[ProbeResult, ...]:
    """Run the named probes in order and return their results.

    Parameters
    ----------
    names : list[str]
        Probe names from :data:`PROBES`.
    iterations : int or None, optional
        Override every probe's default iteration count; must be positive.

    Raises
    ------
    ValueError
        On an unknown probe name or a non-positive override.
    """
    if iterations is not None and iterations < 1:
        msg = f"iterations must be positive, got {iterations}"
        raise ValueError(msg)
    unknown = [name for name in names if name not in PROBES]
    if unknown:
        msg = f"unknown probe(s): {', '.join(unknown)} (available: {', '.join(sorted(PROBES))})"
        raise ValueError(msg)
    results: list[ProbeResult] = []
    for name in names:
        default_iterations, implementation = PROBES[name]
        results.append(implementation(iterations if iterations is not None else default_iterations))
    return tuple(results)
