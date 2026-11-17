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

``--workload key-set`` runs instead a real hub with a journal and a rotating
per-message key set (``--message-auth-key-set``). Claims signed with one key
arrive at a fixed pace while a loop-lag probe, a task that sleeps 5 ms, records
how late it wakes. It runs twice: with no key change, and while a writer
replaces another key's secret file at a fixed interval, so the hub reads the
set again and journals each applied set. The probe's lateness shows how long
the serving loop was held.

Every claim is confirmed granted before its time counts. Measurements share
the host and establish no production throughput claim.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import math
import os
import statistics
import tempfile
import time
import uuid
from pathlib import Path
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
    MessageAuthKey,
    MessageReplayCache,
    sign_event_frame,
    sign_frame,
)
from synapse_channel.core.message_auth_key_set import MessageAuthKeySet
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.state import MAX_CLAIMS_PER_AGENT

SENDER = "BENCH/signer"

KEYED_SENDER = "BENCH/keyed"
"""The sender that signs with the key-set workload's key."""

LAG_STEP = 0.005
"""How long the loop-lag probe sleeps each time, in seconds."""


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


def _owner_only(path: Path, text: str) -> None:
    """Replace ``path`` with ``text``, owner-only and as a new file, as an operator's tool does."""
    staging = path.with_name(path.name + ".new")
    staging.write_text(text, "utf-8")
    staging.chmod(0o600)
    os.replace(staging, path)


def _write_key_set(directory: Path) -> Path:
    """Write a set of two keys: ``use`` for the benchmark sender and ``spare`` for nobody here."""
    _owner_only(directory / "use.key", "secret-use\n")
    _owner_only(directory / "spare.key", "secret-spare-0\n")
    entries = [
        {"key_id": "use", "secret_file": "use.key", "senders": [KEYED_SENDER]},
        {"key_id": "spare", "secret_file": "spare.key", "senders": ["BENCH/spare"]},
    ]
    path = directory / "keys.json"
    _owner_only(path, json.dumps({"version": 1, "keys": entries}))
    return path


async def _probe(lateness: list[float], stop: asyncio.Event) -> None:
    """Record, in milliseconds, how late each 5 ms sleep on the loop wakes."""
    while not stop.is_set():
        start = time.perf_counter()
        await asyncio.sleep(LAG_STEP)
        lateness.append((time.perf_counter() - start - LAG_STEP) * 1e3)


async def _rotate(directory: Path, every: float, stop: asyncio.Event) -> int:
    """Replace the spare key's secret file every ``every`` seconds, writing off the loop."""
    rotations = 0
    while not stop.is_set():
        await asyncio.sleep(every)
        rotations += 1
        secret = f"secret-spare-{rotations}\n"
        await asyncio.to_thread(_owner_only, directory / "spare.key", secret)
    return rotations


def _p99(values: list[float]) -> float:
    """Return the 99th percentile of ``values`` by the nearest-rank method."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.99 * len(ordered)) - 1)]


async def _answered(websocket: ClientConnection, kind: str, task: str) -> None:
    """Wait for the ``kind`` answer to ``task``; any refusal stops the benchmark."""
    while True:
        message = json.loads(await websocket.recv())
        if message.get("type") == kind and message.get("task_id") == task:
            return
        if message.get("type") in ("error", "claim_denied", "release_denied"):
            raise RuntimeError(f"benchmark frame for {task} was refused: {message.get('payload')}")


def _keyed(key: MessageAuthKey, frame_type: str, task: str, sequence: int) -> dict[str, Any]:
    """Return a claim or release of ``task`` signed with ``key``."""
    frame: dict[str, Any] = {
        "sender": KEYED_SENDER,
        "type": frame_type,
        "target": "System",
        "payload": "",
        "task_id": task,
        "idem_key": f"{frame_type}-{task}",
    }
    if frame_type == "claim":
        frame.update(worktree="/bench", paths=[task])
    return sign_frame(frame, key=key, nonce=uuid.uuid4().hex, sequence=sequence)


async def _measure_key_set(
    seconds: float, pace: float, rotate_every: float | None, directory: Path
) -> dict[str, Any]:
    claims = max(1, int(seconds / pace))
    keys = MessageAuthKeySet(_write_key_set(directory))
    key = MessageAuthKey(key_id="use", secret=b"secret-use", senders=frozenset({KEYED_SENDER}))
    journal = EventStore(directory / "hub.db")
    hub = SynapseHub(
        hub_id="bench-keys",
        journal=journal,
        per_message_auth_keys=keys,
        require_per_message_auth=True,
    )
    server = asyncio.create_task(hub.serve("127.0.0.1", 0))
    host, port = await hub.wait_until_serving()
    stop = asyncio.Event()
    lateness: list[float] = []
    latencies: list[float] = []
    probe = asyncio.create_task(_probe(lateness, stop))
    rotator = None
    if rotate_every is not None:
        rotator = asyncio.create_task(_rotate(directory, rotate_every, stop))
    rotations = 0
    try:
        async with connect(f"ws://{host}:{port}") as websocket:
            started = time.perf_counter()
            for number in range(claims):
                await asyncio.sleep(max(0.0, started + number * pace - time.perf_counter()))
                task = f"k-{number}"
                claim = _keyed(key, "claim", task, 2 * number + 1)
                start = time.perf_counter()
                await websocket.send(json.dumps(claim))
                await _answered(websocket, "claim_granted", task)
                latencies.append((time.perf_counter() - start) * 1e6)
                await websocket.send(json.dumps(_keyed(key, "release", task, 2 * number + 2)))
                await _answered(websocket, "release_granted", task)
    finally:
        stop.set()
        if rotator is not None:
            rotations = await rotator
        await probe
        server.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await server
    try:
        applied = sum(1 for _ in journal.iter_events(kinds=("message_auth_key_set_applied",)))
    finally:
        journal.close()
    return {
        "workload": "key_set",
        "rotate_every_s": rotate_every,
        "claims": claims,
        "granted": len(latencies),
        "claim_p50_us": statistics.median(latencies),
        "claim_p99_us": _p99(latencies),
        "lag_samples": len(lateness),
        "lag_max_ms": max(lateness),
        "lag_p99_ms": _p99(lateness),
        "rotations": rotations,
        "applied_sets": applied,
    }


def measure_key_set(seconds: float, pace: float, rotate_every: float | None) -> dict[str, Any]:
    """Measure a hub verifying against a key set, with or without rotations.

    Parameters
    ----------
    seconds : float
        How long claims are sent.
    pace : float
        Seconds between two claims.
    rotate_every : float or None
        Seconds between two replacements of the spare key's secret file;
        ``None`` changes no key.

    Raises
    ------
    ValueError
        If a duration is not positive.
    """
    if seconds <= 0 or pace <= 0 or (rotate_every is not None and rotate_every <= 0):
        raise ValueError("durations must be positive")
    with tempfile.TemporaryDirectory(prefix="synapse-key-set-bench-") as directory:
        os.chmod(directory, 0o700)
        return asyncio.run(_measure_key_set(seconds, pace, rotate_every, Path(directory)))


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
    parser.add_argument("--workload", choices=("signed-frames", "key-set"), default="signed-frames")
    parser.add_argument("--gate-count", type=int, default=2000)
    parser.add_argument("--hub-count", type=int, default=120)
    parser.add_argument("--key-set-seconds", type=float, default=8.0)
    parser.add_argument("--key-set-pace-ms", type=float, default=20.0)
    parser.add_argument("--rotate-every", type=float, default=0.5)
    args = parser.parse_args(argv)
    if args.workload == "key-set":
        pace = args.key_set_pace_ms / 1000
        runs = [
            measure_key_set(args.key_set_seconds, pace, None),
            measure_key_set(args.key_set_seconds, pace, args.rotate_every),
        ]
        print(json.dumps(runs))
        return 0
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
