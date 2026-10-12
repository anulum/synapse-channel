# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — measure socket chat and atomic lease handler costs
"""Measure real two-client chat receipts and keyed lease lifecycles on loopback.

Both workloads use the public client and an isolated durable hub. These serial
shared-host measurements are regression evidence, not isolated throughput claims.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import math
import os
import platform
import statistics
import tempfile
import time
from pathlib import Path
from typing import Any

from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore

DEFAULT_RESULTS = Path(__file__).resolve().parent / "results/coordination_handlers_benchmark.json"
DEFAULT_CHATS = 100
DEFAULT_LEASE_CYCLES = 20


class _Peer:
    """One real client with a response queue and owned connection task."""

    def __init__(self, name: str, uri: str) -> None:
        self.frames: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.agent = SynapseAgent(
            name, self.receive, uri=uri, machine_identity=False, verbose=False
        )
        self.task = asyncio.create_task(self.agent.connect())

    async def receive(self, frame: dict[str, Any]) -> None:
        """Retain a frame received by the real client."""
        self.frames.put_nowait(frame)

    async def ready(self) -> None:
        """Wait until registration has reached the hub's live presence."""
        assert await self.agent.wait_until_ready(5.0)
        await self.expect("presence_update", agent=self.agent.name)

    async def expect(self, msg_type: str, **fields: Any) -> dict[str, Any]:
        """Consume earlier broadcasts until the requested response arrives."""
        while True:
            frame = await asyncio.wait_for(self.frames.get(), timeout=5.0)
            if frame.get("type") == msg_type and all(frame.get(k) == v for k, v in fields.items()):
                return frame

    async def close(self) -> None:
        """Close and join the owned client without affecting any installed session."""
        self.agent.running = False
        assert self.agent.connection is not None
        await self.agent.connection.close()
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)


def _distribution(seconds: list[float]) -> dict[str, float]:
    ordered = sorted(seconds)
    return {
        "min_us": min(ordered) * 1e6,
        "p50_us": statistics.median(ordered) * 1e6,
        "p95_us": ordered[math.ceil(len(ordered) * 0.95) - 1] * 1e6,
        "max_us": max(ordered) * 1e6,
    }


async def _lease_request(
    peer: _Peer, verb: str, response: str, timings: dict[str, list[float]], **fields: Any
) -> dict[str, Any]:
    start = time.perf_counter()
    await peer.agent.send_message(verb, **fields)
    frame = await peer.expect(response, task_id=fields["task_id"])
    timings[verb].append(time.perf_counter() - start)
    return frame


async def _lease_cycle(
    owner: _Peer, recipient: _Peer, index: int, timings: dict[str, list[float]]
) -> None:
    task_id = f"lease-{index}"
    common = {"task_id": task_id}
    granted = await _lease_request(
        owner, "claim", "claim_granted", timings, **common, idem_key=f"claim-{index}"
    )
    fenced = {**common, "epoch": granted["epoch"]}
    await _lease_request(
        owner,
        "task_update",
        "task_updated",
        timings,
        **fenced,
        note="measured update",
        idem_key=f"update-{index}",
    )
    await _lease_request(
        owner,
        "checkpoint",
        "checkpoint_saved",
        timings,
        **fenced,
        checkpoint="resume",
        idem_key=f"checkpoint-{index}",
    )
    start = time.perf_counter()
    await owner.agent.send_message(
        "handoff", **fenced, to_agent=recipient.agent.name, idem_key=f"handoff-{index}"
    )
    handed = await recipient.expect("handoff_granted", **common)
    timings["handoff"].append(time.perf_counter() - start)
    await _lease_request(
        recipient,
        "release",
        "release_granted",
        timings,
        **common,
        epoch=handed["epoch"],
        evidence=["measured wire lifecycle"],
        idem_key=f"release-{index}",
    )


async def _chat_request(owner: _Peer, recipient: _Peer, index: int) -> float:
    identity = f"chat-{index}"
    start = time.perf_counter()
    await owner.agent.send_message(
        "chat",
        target=recipient.agent.name,
        payload="measured directed chat",
        client_msg_id=identity,
        receipt_requested=True,
    )
    received = await recipient.expect("chat", client_msg_id=identity)
    receipt = await owner.expect("delivery_receipt", client_msg_id=identity)
    assert receipt["delivered"] is True and receipt["message_seq"] == received["seq"]
    return time.perf_counter() - start


async def _measure(hub: SynapseHub, uri: str, chats: int, cycles: int) -> dict[str, Any]:
    owner = _Peer("BENCHMARK/owner", uri)
    recipient = _Peer("BENCHMARK/recipient", uri)
    try:
        await owner.ready()
        await recipient.ready()
        chat_times = [await _chat_request(owner, recipient, i) for i in range(chats)]
        lease_times: dict[str, list[float]] = {
            v: [] for v in ["claim", "task_update", "checkpoint", "handoff", "release"]
        }
        for index in range(cycles):
            await _lease_cycle(owner, recipient, index, lease_times)
        assert not hub.state.claims
        return {
            "chat": _distribution(chat_times),
            "leases": {v: _distribution(times) for v, times in lease_times.items()},
        }
    finally:
        await recipient.close()
        await owner.close()


async def profile(
    *, chats: int = DEFAULT_CHATS, cycles: int = DEFAULT_LEASE_CYCLES
) -> dict[str, Any]:
    """Measure completed chat receipts and every keyed lease lifecycle verb.

    Parameters
    ----------
    chats
        Positive count of receipt-requested directed chats.
    cycles
        Positive count of complete claim/update/checkpoint/handoff/release cycles.

    Returns
    -------
    dict[str, Any]
        Distributions, observed journal counts and joined-state evidence.
    """
    with tempfile.TemporaryDirectory(prefix="coordination-handlers-") as directory:
        store = EventStore(Path(directory) / "hub.db")
        hub = SynapseHub(
            journal=store,
            require_fencing_epoch=True,
            private_directed_messages=True,
            anti_rollback_checkpoint=False,
        )
        serving = asyncio.create_task(hub.serve("127.0.0.1", 0))
        try:
            host, port = await hub.wait_until_serving()
            result = await _measure(hub, f"ws://{host}:{port}", chats, cycles)
            events = store.read_all()
            result.update(
                chats=chats,
                lease_cycles=cycles,
                chat_events=sum(e.kind == EventKind.CHAT for e in events),
                claim_events=sum(e.kind == EventKind.CLAIM for e in events),
                operation_records=len(store.read_operations()),
                pending_outbox=store.pending_operation_outbox_count(),
            )
            assert result["chat_events"] == chats and result["claim_events"] == cycles
            assert result["pending_outbox"] == 0
            return result
        finally:
            serving.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await serving
            hub.close()
            store.close()


def _positive(value: str) -> int:
    count = int(value)
    if count < 1:
        raise argparse.ArgumentTypeError("count must be positive")
    return count


def main(argv: list[str] | None = None) -> int:
    """Run the isolated loopback workloads and retain their measured results.

    Parameters
    ----------
    argv
        Command arguments; None reads the process arguments.

    Returns
    -------
    int
        Zero after both workloads finish and their evidence is written.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chats", type=_positive, default=DEFAULT_CHATS)
    parser.add_argument("--lease-cycles", type=_positive, default=DEFAULT_LEASE_CYCLES)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    args = parser.parse_args(argv)
    result = asyncio.run(profile(chats=args.chats, cycles=args.lease_cycles))
    result["host"] = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "cpu": platform.processor(),
        "isolation": "shared host, serial loopback",
        "pid": os.getpid(),
    }
    args.results.parent.mkdir(parents=True, exist_ok=True)
    args.results.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
