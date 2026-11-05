# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — per-socket frame-signing identity and session nonce
"""Exercise which key a hub records for a socket and the session nonce it hands out."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from websockets.asyncio.client import connect

from hub_e2e_helpers import read_until_type, running_hub
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.frame_signing import FrameSigningSessions
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.hub_identity_gate import HubIdentityGate
from synapse_channel.core.identity_pins import IdentityPinStore
from synapse_channel.core.message_auth import (
    EventSignatureKey,
    EventSignatureTrustBundle,
    MessageReplayCache,
    sign_event_frame,
)
from synapse_channel.machine_identity import machine_identity_agent_kwargs

_SENDER = "proj/claude"


def _bundle_hub(private_key: Ed25519PrivateKey) -> SynapseHub:
    key = EventSignatureKey.from_private_key(
        key_id="k", private_key=private_key, senders=frozenset({_SENDER})
    )
    bundle = EventSignatureTrustBundle(
        keys={"k": key}, replay_cache=MessageReplayCache(window_seconds=30.0, max_entries=64)
    )
    return SynapseHub(hub_id="fs", identity_trust_bundle=bundle, require_identity_binding=True)


def _signed_registration(private_key: Ed25519PrivateKey) -> str:
    frame = {"sender": _SENDER, "type": "heartbeat", "target": "System", "payload": "online"}
    return json.dumps(
        sign_event_frame(
            frame,
            key_id="k",
            private_key=private_key,
            nonce="reg-1",
            sequence=1,
            signed_at=time.time(),
        )
    )


async def _until(condition: Callable[[], bool], seconds: float = 5.0) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "condition not reached in time"
        await asyncio.sleep(0.01)


def test_sessions_keep_one_nonce_per_socket_and_one_signer_per_bound_name() -> None:
    sessions = FrameSigningSessions()
    first, second = object(), object()
    nonce = sessions.session_nonce(first)
    assert sessions.session_nonce(first) == nonce
    assert sessions.welcome_fields(first) == {"session_nonce": nonce}
    assert sessions.session_nonce(second) != nonce
    assert len(nonce) == 24
    sessions.record(first, "A", "k1", "pin")
    signer = sessions.signer(first, "A")
    assert signer is not None and (signer.key_id, signer.source, signer.last_seq) == (
        "k1",
        "pin",
        0,
    )
    assert sessions.signer(first, "B") is None
    assert sessions.signer(second, "A") is None
    assert len(sessions) == 1
    sessions.forget(first)
    sessions.forget(first)
    assert sessions.signer(first, "A") is None and len(sessions) == 0
    assert sessions.session_nonce(first) != nonce


async def test_a_bundle_bound_socket_records_its_key_and_gets_a_fresh_nonce() -> None:
    private_key = Ed25519PrivateKey.generate()
    hub = _bundle_hub(private_key)
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as ws:
            first = await read_until_type(ws, "welcome")
            await ws.send(_signed_registration(private_key))
            await ws.send(json.dumps({"sender": _SENDER, "type": "who_request"}))
            await read_until_type(ws, "who_snapshot")
            assert len(hub.frame_signing) == 1
            (socket,) = hub.clients.agent_sockets.values()
            signer = hub.frame_signing.signer(socket, _SENDER)
            assert signer is not None and (signer.key_id, signer.source) == ("k", "bundle")
            assert hub.frame_signing.session_nonce(socket) == first["session_nonce"]
        await _until(lambda: len(hub.frame_signing) == 0)
        async with connect(uri) as ws:
            second = await read_until_type(ws, "welcome")
    assert first["session_nonce"] != second["session_nonce"]


async def test_tofu_records_the_pinned_key_and_unsigned_names_record_nothing(
    tmp_path: Path,
) -> None:
    hub = SynapseHub(hub_id="fs-tofu", identity_pin_path=tmp_path / "pins.json")
    async with running_hub(hub) as (_, uri):
        kwargs = machine_identity_agent_kwargs(base=tmp_path / "machine")
        signed = SynapseAgent("PROJ/signed", None, uri=uri, verbose=False, **kwargs)
        unsigned = SynapseAgent("PROJ/plain", None, uri=uri, verbose=False, machine_identity=False)
        tasks = [asyncio.create_task(agent.connect()) for agent in (signed, unsigned)]
        try:
            await _until(lambda: {"PROJ/signed", "PROJ/plain"} <= set(hub.clients.agent_sockets))
            await _until(lambda: len(hub.frame_signing) == 1)
            pin = hub.identity_pins.pinned("PROJ/signed")
            assert pin is not None
            socket = hub.clients.agent_sockets["PROJ/signed"]
            signer = hub.frame_signing.signer(socket, "PROJ/signed")
            assert signer is not None and (signer.key_id, signer.source) == (pin.key_id, "pin")
            plain = hub.clients.agent_sockets["PROJ/plain"]
            assert hub.frame_signing.signer(plain, "PROJ/plain") is None
        finally:
            for agent in (signed, unsigned):
                agent.running = False
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


async def _ignore(_websocket: Any, _frame: dict[str, Any]) -> None:
    """Discard a frame the gate would send."""


def _gate(*, binding: bool, pins: IdentityPinStore | None) -> HubIdentityGate:
    return HubIdentityGate(
        require_identity_binding=binding,
        identity_trust_bundle=None,
        send_json=_ignore,
        system=lambda *args, **kwargs: {},
        pin_store=pins,
    )


def test_proven_key_names_only_a_key_the_registration_proved() -> None:
    signed = {"signature": {"key_id": "k1"}}
    pins = IdentityPinStore()
    public = base64.b64encode(Ed25519PrivateKey.generate().public_key().public_bytes_raw()).decode(
        "ascii"
    )
    pins.pin("A", key_id="k1", public_key=public)
    assert _gate(binding=True, pins=None).proven_key("A", signed) == ("k1", "bundle")
    assert _gate(binding=False, pins=pins).proven_key("A", signed) == ("k1", "pin")
    assert _gate(binding=False, pins=pins).proven_key("A", {"signature": {"key_id": "k2"}}) is None
    assert _gate(binding=False, pins=pins).proven_key("B", signed) is None
    assert _gate(binding=False, pins=None).proven_key("A", signed) is None
    for unsigned in ({}, {"signature": "x"}, {"signature": {"key_id": ""}}):
        assert _gate(binding=True, pins=pins).proven_key("A", unsigned) is None
