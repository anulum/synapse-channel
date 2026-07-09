# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li — the serving half of the multi-hub event-log pull, over real sockets

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from websockets.asyncio.client import ClientConnection, connect

from hub_e2e_helpers import read_until_type, running_hub, send_json
from synapse_channel.core.federation import FederationBundle, FederationPeer, ScopeGrant
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.multihub_serving import MultiHubServingGrant, MultiHubServingPolicy
from synapse_channel.core.multihub_wire import LogSnapshot, decode_log_snapshot
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.tls import (
    MTLSPeerTrustBundle,
    MTLSTrustedPeer,
    certificate_sha256_pin,
)

_SNAPSHOT = MessageType.MULTIHUB_LOG_SNAPSHOT
_DOMAIN = "domain-b"
_KEY = "SYNAPSE-CHANNEL:main:2026-06"


_NAMESPACE = "SYNAPSE-CHANNEL"


def _write_peer_cert(tmp_path: Path) -> tuple[str, bytes]:
    """Write a self-signed peer certificate; return its pin and live DER bytes."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    certfile = tmp_path / "peer-cert.pem"
    keyfile = tmp_path / "peer-key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=peer-b",
            "-keyout",
            str(keyfile),
            "-out",
            str(certfile),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    pin = certificate_sha256_pin(certfile)
    der = x509.load_pem_x509_certificate(certfile.read_bytes()).public_bytes(
        serialization.Encoding.DER
    )
    return pin, der


def _serving_policy(pin: str, der: bytes, *, sender: str = "peer") -> MultiHubServingPolicy:
    """Build a serving policy trusting ``sender`` under ``pin``, reading ``der`` off any socket.

    The end-to-end harness runs over plaintext ``ws://``, so a real client certificate is not
    available; injecting the certificate source proves the handler wiring (policy → empty
    snapshot on a deny, full log on an allow) without a mutual-TLS handshake, which the unit
    tests in ``test_multihub_serving`` cover separately.
    """
    return MultiHubServingPolicy(
        federation=FederationBundle(
            [
                FederationPeer(
                    domain_id=_DOMAIN,
                    namespaces=frozenset({_NAMESPACE}),
                    certificate_pins=frozenset({pin}),
                    signing_key_ids=frozenset({_KEY}),
                    scope_grants=(ScopeGrant(verb="read", namespace=_NAMESPACE),),
                )
            ]
        ),
        mtls=MTLSPeerTrustBundle(
            peers={
                _DOMAIN: MTLSTrustedPeer(
                    peer_id=_DOMAIN,
                    certificate_pins=frozenset({pin}),
                    signing_key_ids=frozenset({_KEY}),
                    projects=frozenset({_NAMESPACE}),
                )
            }
        ),
        grants={
            sender: MultiHubServingGrant(
                domain_id=_DOMAIN, namespace=_NAMESPACE, signing_key_id=_KEY
            )
        },
        clock=lambda: 0.0,
        cert_source=lambda _websocket: der,
    )


async def _connect(uri: str, name: str) -> ClientConnection:
    """Open a raw client socket, drain the welcome, and register with a heartbeat."""
    websocket = await connect(uri)
    await read_until_type(websocket, "welcome")
    await send_json(websocket, sender=name, type="heartbeat")
    return websocket


async def _seed_chats(uri: str, count: int) -> None:
    """Drive ``count`` chats so the hub journals one ``chat`` event per message."""
    async with await _connect(uri, "writer") as ws:
        for index in range(count):
            await send_json(ws, sender="writer", type="chat", payload=f"m{index}")
            await read_until_type(ws, "chat")


async def _pull(uri: str, **request: Any) -> LogSnapshot:
    """Send one multi-hub log request as a peer and decode the snapshot reply."""
    async with await _connect(uri, "peer") as ws:
        await send_json(ws, sender="peer", type=MessageType.MULTIHUB_LOG_REQUEST, **request)
        message = await read_until_type(ws, _SNAPSHOT)
    return decode_log_snapshot(message)


async def test_serves_the_whole_log_from_the_zero_cursor(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    hub = SynapseHub(hub_id="syn-a", journal=store)
    async with running_hub(hub) as (_, uri):
        await _seed_chats(uri, 3)
        snapshot = await _pull(uri, after_seq=0)
    store.close()
    assert [event.seq for event in snapshot.events] == [1, 2, 3]
    assert {event.kind for event in snapshot.events} == {"chat"}
    assert snapshot.next_cursor == 3
    assert snapshot.log_end_seq == 3


async def test_respects_the_batch_limit(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    hub = SynapseHub(hub_id="syn-a", journal=store)
    async with running_hub(hub) as (_, uri):
        await _seed_chats(uri, 3)
        snapshot = await _pull(uri, after_seq=0, limit=1)
    store.close()
    assert [event.seq for event in snapshot.events] == [1]
    assert snapshot.next_cursor == 1
    assert snapshot.log_end_seq == 3


async def test_serves_only_events_past_the_cursor(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    hub = SynapseHub(hub_id="syn-a", journal=store)
    async with running_hub(hub) as (_, uri):
        await _seed_chats(uri, 3)
        snapshot = await _pull(uri, after_seq=1)
    store.close()
    assert [event.seq for event in snapshot.events] == [2, 3]
    assert snapshot.next_cursor == 3
    assert snapshot.log_end_seq == 3


async def test_empty_batch_does_not_move_the_cursor(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    hub = SynapseHub(hub_id="syn-a", journal=store)
    async with running_hub(hub) as (_, uri):
        await _seed_chats(uri, 3)
        snapshot = await _pull(uri, after_seq=3)
    store.close()
    assert snapshot.events == ()
    assert snapshot.next_cursor == 3
    assert snapshot.log_end_seq == 3


async def test_hub_without_a_journal_serves_an_empty_snapshot() -> None:
    hub = SynapseHub(hub_id="syn-a")
    async with running_hub(hub) as (_, uri):
        snapshot = await _pull(uri, after_seq=5)
    assert snapshot.events == ()
    assert snapshot.next_cursor == 5
    assert snapshot.log_end_seq is None


async def test_a_malformed_request_is_answered_with_an_empty_snapshot(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    hub = SynapseHub(hub_id="syn-a", journal=store)
    async with running_hub(hub) as (_, uri):
        await _seed_chats(uri, 2)
        snapshot = await _pull(uri, after_seq="not-a-number")
    store.close()
    assert snapshot.events == ()
    assert snapshot.next_cursor == 0
    assert snapshot.log_end_seq is None


async def test_a_trusted_peer_is_served_under_a_serving_policy(tmp_path: Path) -> None:
    pin, der = _write_peer_cert(tmp_path)
    store = EventStore(tmp_path / "events.db")
    hub = SynapseHub(
        hub_id="syn-a", journal=store, multihub_serving_policy=_serving_policy(pin, der)
    )
    async with running_hub(hub) as (_, uri):
        await _seed_chats(uri, 3)
        snapshot = await _pull(uri, after_seq=0)
    store.close()
    assert [event.seq for event in snapshot.events] == [1, 2, 3]
    assert snapshot.next_cursor == 3
    assert snapshot.log_end_seq == 3


async def test_an_untrusted_certificate_is_refused_with_an_empty_snapshot(tmp_path: Path) -> None:
    pin, _trusted = _write_peer_cert(tmp_path)
    _other_pin, stranger_der = _write_peer_cert(tmp_path / "other")
    # The peer is pinned to ``pin`` but the live socket presents a different certificate.
    policy = _serving_policy(pin, stranger_der)
    store = EventStore(tmp_path / "events.db")
    hub = SynapseHub(hub_id="syn-a", journal=store, multihub_serving_policy=policy)
    async with running_hub(hub) as (_, uri):
        await _seed_chats(uri, 3)
        snapshot = await _pull(uri, after_seq=1)
    store.close()
    assert snapshot.events == ()
    assert snapshot.next_cursor == 1
    assert snapshot.log_end_seq is None


async def test_a_sender_without_a_grant_is_refused(tmp_path: Path) -> None:
    pin, der = _write_peer_cert(tmp_path)
    # The policy grants a different sender, so the requesting "peer" has no grant.
    policy = _serving_policy(pin, der, sender="someone-else")
    store = EventStore(tmp_path / "events.db")
    hub = SynapseHub(hub_id="syn-a", journal=store, multihub_serving_policy=policy)
    async with running_hub(hub) as (_, uri):
        await _seed_chats(uri, 2)
        snapshot = await _pull(uri, after_seq=0)
    store.close()
    assert snapshot.events == ()
    assert snapshot.next_cursor == 0
    assert snapshot.log_end_seq is None
