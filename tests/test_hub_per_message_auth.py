# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real hub tests for per-message authentication
"""Real WebSocket tests for hub-side per-message authentication enforcement."""

from __future__ import annotations

import json
import time
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from websockets.asyncio.client import connect
from websockets.asyncio.connection import Connection

from hub_e2e_helpers import read_json, read_until_type, running_hub, send_json
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.message_auth import (
    EventSignatureKey,
    EventSignatureTrustBundle,
    MessageAuthKey,
    MessageReplayCache,
    sign_event_frame,
    sign_frame,
)
from synapse_channel.core.protocol import build_envelope
from synapse_channel.core.ratelimit import RateLimiter
from synapse_channel.core.secure import (
    SECURE_AGENT_BURST,
    SECURE_AGENT_RATE,
    SECURE_HOST_BURST,
    SECURE_HOST_RATE,
)


def _auth_hub() -> SynapseHub:
    return SynapseHub(
        hub_id="syn-test",
        require_per_message_auth=True,
        per_message_auth_keys=[
            MessageAuthKey(key_id="main", secret=b"shared-secret", senders=frozenset({"ALPHA"}))
        ],
        per_message_auth_window_seconds=30.0,
        per_message_auth_replay_capacity=16,
    )


def _auth_hub_with_capacity(capacity: int) -> SynapseHub:
    return SynapseHub(
        hub_id="syn-test",
        require_per_message_auth=True,
        per_message_auth_keys=[
            MessageAuthKey(key_id="main", secret=b"shared-secret", senders=frozenset({"ALPHA"}))
        ],
        per_message_auth_window_seconds=30.0,
        per_message_auth_replay_capacity=capacity,
    )


async def _read_claim_result(websocket: Connection) -> dict[str, Any]:
    """Read the next grant, denial, or error while ignoring presence broadcasts."""
    for _ in range(10):
        message = await read_json(websocket)
        if message.get("type") in {"claim_granted", "claim_denied", "error"}:
            return message
    raise TimeoutError("claim result did not arrive")


async def test_hub_rejects_unsigned_mutation_but_allows_chat() -> None:
    async with running_hub(_auth_hub()) as (_, uri):
        async with connect(uri) as websocket:
            await read_until_type(websocket, "welcome")
            await send_json(websocket, sender="ALPHA", type="chat", payload="hello")
            chat = await read_until_type(websocket, "chat")
            await send_json(websocket, sender="ALPHA", type="claim", task_id="T1")
            denied = await read_until_type(websocket, "error")

    assert chat["payload"] == "hello"
    assert denied["verification_result"] == "missing"
    assert "per-message authentication failed" in denied["payload"]


async def test_hub_accepts_signed_mutation_and_rejects_replay() -> None:
    key = MessageAuthKey(key_id="main", secret=b"shared-secret", senders=frozenset({"ALPHA"}))
    timestamp = time.time()
    signed = sign_frame(
        build_envelope("ALPHA", "claim", target="System", task_id="T1", now=1.0),
        key=key,
        nonce="n1",
        sequence=1,
        timestamp=timestamp,
    )
    hub = _auth_hub()
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as websocket:
            await read_until_type(websocket, "welcome")
            await websocket.send(json.dumps(signed))
            granted = await read_until_type(websocket, "claim_granted")
            await websocket.send(json.dumps(signed))
            denied = await read_until_type(websocket, "error")

    assert granted["task_id"] == "T1"
    assert denied["verification_result"] == "replayed"


async def test_hub_rejects_sender_not_bound_to_key() -> None:
    key = MessageAuthKey(key_id="main", secret=b"shared-secret", senders=frozenset({"ALPHA"}))
    signed = sign_frame(
        build_envelope("BETA", "claim", target="System", task_id="T1", now=1.0),
        key=key,
        nonce="n1",
        sequence=1,
        timestamp=time.time(),
    )
    hub = _auth_hub()
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as websocket:
            await read_until_type(websocket, "welcome")
            await websocket.send(json.dumps(signed))
            denied = await read_until_type(websocket, "error")

    assert denied["verification_result"] == "sender_mismatch"


async def test_hub_rejects_capacity_pressure_without_reopening_replay() -> None:
    key = MessageAuthKey(key_id="main", secret=b"shared-secret", senders=frozenset({"ALPHA"}))
    timestamp = time.time()
    first = sign_frame(
        build_envelope("ALPHA", "claim", target="System", task_id="T1", now=1.0),
        key=key,
        nonce="n1",
        sequence=1,
        timestamp=timestamp,
    )
    second = sign_frame(
        build_envelope("ALPHA", "claim", target="System", task_id="T2", now=1.0),
        key=key,
        nonce="n2",
        sequence=2,
        timestamp=timestamp,
    )
    hub = _auth_hub_with_capacity(1)
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as websocket:
            await read_until_type(websocket, "welcome")
            await websocket.send(json.dumps(first))
            granted = await read_until_type(websocket, "claim_granted")
            await websocket.send(json.dumps(second))
            capacity_denied = await read_until_type(websocket, "error")
            await websocket.send(json.dumps(first))
            replay_denied = await read_until_type(websocket, "error")

    assert granted["task_id"] == "T1"
    assert capacity_denied["verification_result"] == "replayed"
    assert replay_denied["verification_result"] == "replayed"


async def test_secure_rate_compliant_principals_share_one_replay_capacity() -> None:
    """Prove that authenticated principals consume one global fail-closed cache."""
    admitted_senders = ("ALPHA", "BETA", "GAMMA", "DELTA", "EPSILON")
    all_senders = (*admitted_senders, "ZETA")
    key = MessageAuthKey(
        key_id="main",
        secret=b"shared-secret",
        senders=frozenset(all_senders),
    )
    hub = SynapseHub(
        hub_id="syn-test",
        require_per_message_auth=True,
        per_message_auth_keys=[key],
        per_message_auth_window_seconds=30.0,
        per_message_auth_replay_capacity=len(admitted_senders),
        rate_limiter=RateLimiter(
            rate_per_second=SECURE_AGENT_RATE,
            burst=SECURE_AGENT_BURST,
        ),
        host_rate_limiter=RateLimiter(
            rate_per_second=SECURE_HOST_RATE,
            burst=SECURE_HOST_BURST,
        ),
    )
    timestamp = time.time()
    frames = {
        sender: sign_frame(
            build_envelope(
                sender,
                "claim",
                target="System",
                task_id=f"TASK-{sequence}",
                paths=[f"src/{sender.lower()}.py"],
                now=1.0,
            ),
            key=key,
            nonce=f"nonce-{sequence}",
            sequence=sequence,
            timestamp=timestamp,
        )
        for sequence, sender in enumerate(all_senders, start=1)
    }

    async with running_hub(hub) as (_, uri):
        for sender in admitted_senders:
            async with connect(uri) as websocket:
                await read_until_type(websocket, "welcome")
                await websocket.send(json.dumps(frames[sender]))
                try:
                    granted = await _read_claim_result(websocket)
                except TimeoutError as exc:
                    raise AssertionError(
                        f"claim result for {sender} did not arrive; counters={hub.counters!r}"
                    ) from exc
                assert granted["type"] == "claim_granted", (sender, granted)
                assert granted["task_id"] == frames[sender]["task_id"]

        async with connect(uri) as websocket:
            await read_until_type(websocket, "welcome")
            await websocket.send(json.dumps(frames["ZETA"]))
            capacity_denied = await read_until_type(websocket, "error")

        async with connect(uri) as websocket:
            await read_until_type(websocket, "welcome")
            await websocket.send(json.dumps(frames["ALPHA"]))
            replay_denied = await read_until_type(websocket, "error")

    assert hub.counters.rate_limited == 0
    assert capacity_denied["verification_result"] == "replayed"
    assert replay_denied["verification_result"] == "replayed"


async def test_hub_rejects_bad_signature_and_expired_signed_mutation() -> None:
    key = MessageAuthKey(key_id="main", secret=b"shared-secret", senders=frozenset({"ALPHA"}))
    signed = sign_frame(
        build_envelope("ALPHA", "claim", target="System", task_id="T1", now=1.0),
        key=key,
        nonce="n1",
        sequence=1,
        timestamp=time.time() - 60.0,
    )
    hub = _auth_hub()
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as websocket:
            await read_until_type(websocket, "welcome")
            await websocket.send(json.dumps(signed))
            expired = await read_until_type(websocket, "error")
            tampered = signed | {"task_id": "T2", "auth": signed["auth"] | {"nonce": "n2"}}
            tampered["auth"]["timestamp"] = time.time()
            await websocket.send(json.dumps(tampered))
            bad = await read_until_type(websocket, "error")

    assert expired["verification_result"] == "expired"
    assert bad["verification_result"] == "bad_authentication"


async def test_open_hub_keeps_per_message_authentication_off_by_default() -> None:
    async with running_hub(SynapseHub(hub_id="syn-test")) as (_, uri):
        async with connect(uri) as websocket:
            await read_until_type(websocket, "welcome")
            await send_json(websocket, sender="ALPHA", type="claim", task_id="T1")
            granted = await read_until_type(websocket, "claim_granted")

    assert granted["task_id"] == "T1"


async def test_hub_accepts_signed_event_mutation_and_reports_failures() -> None:
    private_key = Ed25519PrivateKey.generate()
    key = EventSignatureKey.from_private_key(
        key_id="SYNAPSE-CHANNEL:main:2026-06",
        private_key=private_key,
        senders=frozenset({"ALPHA"}),
        projects=frozenset({"SYNAPSE-CHANNEL"}),
    )
    timestamp = time.time()
    signed = sign_event_frame(
        build_envelope(
            "ALPHA",
            "claim",
            target="System",
            task_id="T1",
            project="SYNAPSE-CHANNEL",
            now=1.0,
        ),
        key_id=key.key_id,
        private_key=private_key,
        nonce="event-n1",
        sequence=1,
        signed_at=timestamp,
    )
    hub = SynapseHub(
        hub_id="syn-test",
        require_per_message_auth=True,
        signed_event_trust_bundle=EventSignatureTrustBundle(
            keys={key.key_id: key},
            replay_cache=MessageReplayCache(window_seconds=30.0, max_entries=16),
        ),
    )
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as websocket:
            await read_until_type(websocket, "welcome")
            await websocket.send(json.dumps(signed))
            granted = await read_until_type(websocket, "claim_granted")
            await websocket.send(json.dumps(signed | {"task_id": "T2"}))
            bad = await read_until_type(websocket, "error")
            replay = sign_event_frame(
                build_envelope(
                    "ALPHA",
                    "claim",
                    target="System",
                    task_id="T3",
                    project="SYNAPSE-CHANNEL",
                    now=1.0,
                ),
                key_id=key.key_id,
                private_key=private_key,
                nonce="event-n1",
                sequence=2,
                signed_at=timestamp,
            )
            await websocket.send(json.dumps(replay))
            replayed = await read_until_type(websocket, "error")

    assert granted["task_id"] == "T1"
    assert bad["verification_result"] == "bad_signature"
    assert replayed["verification_result"] == "replayed"


def test_hub_accepts_auth_keys_as_a_prebuilt_mapping() -> None:
    """A mapping of key_id to key is adopted as-is, not rebuilt from a list."""
    key = MessageAuthKey(key_id="main", secret=b"shared-secret", senders=frozenset({"ALPHA"}))
    hub = SynapseHub(hub_id="syn-test", per_message_auth_keys={"main": key})
    assert hub.per_message_auth_keys == {"main": key}
