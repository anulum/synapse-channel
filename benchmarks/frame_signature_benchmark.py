# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — cost of requiring signed frames
"""Measure what ``--require-signed-frames`` costs per frame and per claim.

Two workloads run on fresh state with a fixed in-process key:

* ``gate``: signing one claim frame and the hub gate's full check of it
  (canonical bytes, Ed25519 verification, session and sequence binding);
* ``hub``: claims sent over a real loopback WebSocket to a real hub, each
  answered with ``claim_granted``, once with the requirement on and signed
  frames, once with it off and plain frames.

Every claim is confirmed granted before its time counts. Measurements share
the host and establish no production throughput claim.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import statistics
import time
import uuid
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from websockets.asyncio.client import ClientConnection, connect

from synapse_channel.core.frame_signature import sign_session_frame
from synapse_channel.core.frame_signature_gate import FrameSignatureGate, GateVerdict
from synapse_channel.core.frame_signing import FrameSigningSessions
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.message_auth import (
    EventSignatureKey,
    EventSignatureTrustBundle,
    MessageReplayCache,
    sign_event_frame,
)
from synapse_channel.core.state import MAX_CLAIMS_PER_AGENT

SENDER = "BENCH/signer"


def _bundle(key: Ed25519PrivateKey) -> EventSignatureTrustBundle:
    entry = EventSignatureKey.from_private_key(
        key_id="k", private_key=key, senders=frozenset({SENDER})
    )
    return EventSignatureTrustBundle(
        keys={"k": entry}, replay_cache=MessageReplayCache(window_seconds=30.0, max_entries=64)
    )


async def _ignore(_websocket: Any, _frame: dict[str, Any]) -> None:
    """Drop a frame the gate would send."""


async def _no_close(_websocket: Any, *, code: int, reason: str) -> None:
    """Pretend to close a connection."""


def measure_gate(count: int) -> dict[str, Any]:
    """Measure signing and checking ``count`` claim frames on one session.

    Raises
    ------
    ValueError
        If ``count`` is not positive.
    """
    if count < 1:
        raise ValueError("frame count must be positive")
    key = Ed25519PrivateKey.generate()
    sessions = FrameSigningSessions()
    socket = object()
    sessions.record(socket, SENDER, "k", "bundle")
    nonce = sessions.session_nonce(socket)
    bundle = _bundle(key)
    gate = FrameSignatureGate(
        sessions=sessions,
        hub_id="bench",
        enabled=lambda: True,
        bundle=lambda: bundle,
        pins=None,
        send_json=_ignore,
        system=lambda *args, **kwargs: {},
        close_socket=_no_close,
        count_refusal=lambda: None,
    )
    signing: list[float] = []
    checking: list[float] = []
    for seq in range(1, count + 1):
        frame = {"sender": SENDER, "type": "claim", "target": "System", "task_id": f"t-{seq}"}
        start = time.perf_counter()
        signed = sign_session_frame(
            frame, key_id="k", private_key=key, hub_id="bench", session_nonce=nonce, seq=seq
        )
        signing.append((time.perf_counter() - start) * 1e6)
        start = time.perf_counter()
        outcome = gate.check(socket, SENDER, signed)
        checking.append((time.perf_counter() - start) * 1e6)
        if outcome.verdict is not GateVerdict.ADMIT:
            raise RuntimeError(f"benchmark frame {seq} was refused: {outcome.reason}")
    return {
        "workload": "gate",
        "count": count,
        "sign_p50_us": statistics.median(signing),
        "check_p50_us": statistics.median(checking),
        "check_mean_us": statistics.mean(checking),
        "admitted": count,
    }


async def _register(websocket: ClientConnection, key: Ed25519PrivateKey) -> dict[str, Any]:
    welcome: dict[str, Any] = json.loads(await websocket.recv())
    registration = sign_event_frame(
        {"sender": SENDER, "type": "heartbeat", "target": "System", "payload": "online"},
        key_id="k",
        private_key=key,
        nonce=uuid.uuid4().hex,
        sequence=1,
        signed_at=time.time(),
    )
    await websocket.send(json.dumps(registration))
    return welcome


async def _granted(websocket: ClientConnection, task: str) -> None:
    while True:
        message = json.loads(await websocket.recv())
        if message.get("type") == "claim_granted" and message.get("task_id") == task:
            return
        if message.get("type") in ("error", "claim_denied"):
            raise RuntimeError(f"benchmark claim {task} was refused: {message.get('payload')}")


async def _measure_hub(count: int, *, signed: bool) -> dict[str, Any]:
    key = Ed25519PrivateKey.generate()
    hub = SynapseHub(
        hub_id="bench",
        identity_trust_bundle=_bundle(key),
        require_identity_binding=True,
        require_signed_frames=signed,
    )
    server = asyncio.create_task(hub.serve("127.0.0.1", 0))
    host, port = await hub.wait_until_serving()
    latencies: list[float] = []
    try:
        async with connect(f"ws://{host}:{port}") as websocket:
            welcome = await _register(websocket, key)
            for seq in range(1, count + 1):
                task = f"t-{seq}"
                frame: dict[str, Any] = {
                    "sender": SENDER,
                    "type": "claim",
                    "target": "System",
                    "task_id": task,
                }
                start = time.perf_counter()
                if signed:
                    frame = sign_session_frame(
                        frame,
                        key_id="k",
                        private_key=key,
                        hub_id=str(welcome["hub_id"]),
                        session_nonce=str(welcome["session_nonce"]),
                        seq=seq,
                    )
                await websocket.send(json.dumps(frame))
                await _granted(websocket, task)
                latencies.append((time.perf_counter() - start) * 1e6)
    finally:
        server.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await server
    if len(hub.state.claims) != count:
        raise RuntimeError("the hub did not hold every benchmark claim")
    return {
        "workload": "hub",
        "signed_frames": signed,
        "count": count,
        "p50_us": statistics.median(latencies),
        "mean_us": statistics.mean(latencies),
        "granted": count,
    }


def measure_hub(count: int, *, signed: bool) -> dict[str, Any]:
    """Measure ``count`` granted claims on a real hub, signed or not.

    Raises
    ------
    ValueError
        If ``count`` is not positive or exceeds the per-agent claim cap.
    """
    if not 1 <= count <= MAX_CLAIMS_PER_AGENT:
        raise ValueError(f"claim count must be positive and at most {MAX_CLAIMS_PER_AGENT}")
    return asyncio.run(_measure_hub(count, signed=signed))


def main(argv: list[str] | None = None) -> int:
    """Run the gate workload and both hub workloads; print their JSON metrics.

    Parameters
    ----------
    argv : list of str or None, optional
        Explicit CLI arguments, or the process arguments.

    Returns
    -------
    int
        Zero after every frame was admitted and every claim granted.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gate-count", type=int, default=2000)
    parser.add_argument("--hub-count", type=int, default=120)
    args = parser.parse_args(argv)
    print(
        json.dumps(
            [
                measure_gate(args.gate_count),
                measure_hub(args.hub_count, signed=False),
                measure_hub(args.hub_count, signed=True),
            ]
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
