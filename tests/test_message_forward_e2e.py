# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — two real hubs exchanging agent messages over real mutual TLS
"""Cross-hub messaging between two real hubs, each serving native WSS with a client CA.

Each test starts a ``workstation`` and a ``laptop`` hub on loopback ports. Both present the
same server certificate, request client certificates from one test CA, and grant the other hub
one project namespace (``PROJ``) through a real federation bundle, mutual-TLS trust bundle and
serving grant. Agents connect with plain server-verified TLS, as production agents do.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import ssl
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from hub_e2e_helpers import _await_listening, _free_port, read_until_type
from multihub_tls_helpers import TLSIdentity, certificate_authority, issue_identity
from synapse_channel.core.federation import FederationBundle, FederationPeer
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.message_forward_origin import run_forward_retries
from synapse_channel.core.message_forward_transport import (
    MessageForwardPeer,
    forward_message,
    parse_message_peers,
)
from synapse_channel.core.message_forward_wire import MessageForwardRequest
from synapse_channel.core.multihub_serving import MultiHubServingGrant, MultiHubServingPolicy
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.tls import (
    MTLSPeerTrustBundle,
    MTLSTrustedPeer,
    build_server_ssl_context,
    certificate_sha256_pin,
)

_NS = "PROJ"
_KEY = "PROJ:hub:2026-09"
_READ_LIMIT = 60


@dataclass(frozen=True, slots=True)
class _Material:
    root: Path
    ca: Path
    server: TLSIdentity
    clients: dict[str, TLSIdentity]
    server_pin: str


@dataclass
class _Pair:
    material: _Material
    workstation: SynapseHub
    laptop: SynapseHub
    ws_uri: str
    lp_uri: str
    stores: dict[str, EventStore]


def _material(tmp_path: Path) -> _Material:
    ca_key, ca_cert = certificate_authority("cross-hub-test-ca")
    ca = tmp_path / "ca.pem"
    from cryptography.hazmat.primitives import serialization

    ca.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    server = issue_identity(tmp_path, "hub-server", ca_key=ca_key, ca_cert=ca_cert, server=True)
    clients = {
        name: issue_identity(tmp_path, f"{name}-client", ca_key=ca_key, ca_cert=ca_cert)
        for name in ("workstation", "laptop", "rogue")
    }
    return _Material(
        root=tmp_path,
        ca=ca,
        server=server,
        clients=clients,
        server_pin=certificate_sha256_pin(server.cert),
    )


def _policy(peer: str, client: TLSIdentity, namespaces: frozenset[str]) -> MultiHubServingPolicy:
    """Grant ``peer`` (presenting ``client``) the given local namespaces."""
    domain = f"{peer}.example"
    pin = certificate_sha256_pin(client.cert)
    return MultiHubServingPolicy(
        federation=FederationBundle(
            [
                FederationPeer(
                    domain_id=domain,
                    namespaces=namespaces,
                    certificate_pins=frozenset({pin}),
                    signing_key_ids=frozenset({_KEY}),
                )
            ]
        ),
        mtls=MTLSPeerTrustBundle(
            peers={
                domain: MTLSTrustedPeer(
                    peer_id=domain,
                    certificate_pins=frozenset({pin}),
                    signing_key_ids=frozenset({_KEY}),
                    projects=namespaces,
                )
            }
        ),
        grants={peer: MultiHubServingGrant(domain_id=domain, namespace=_NS, signing_key_id=_KEY)},
        clock=time.time,
    )


def _peers(material: _Material, *, local: str, remote: str, port: int) -> dict[str, Any]:
    client = material.clients[local]
    return parse_message_peers(
        [f"{remote}=wss://localhost:{port}"],
        pins={remote: material.server_pin},
        client_certificate_file=str(client.cert),
        client_key_file=str(client.key),
    )


def _build_hub(
    material: _Material,
    store: EventStore,
    *,
    name: str,
    remote: str,
    remote_port: int,
    namespaces: frozenset[str] = frozenset({_NS}),
    ttl: float = 86_400.0,
) -> SynapseHub:
    return SynapseHub(
        hub_id=name,
        journal=store,
        multihub_serving_policy=_policy(remote, material.clients[remote], namespaces),
        message_peers=_peers(material, local=name, remote=remote, port=remote_port),
        message_forward_ttl=ttl,
    )


async def _serve(hub: SynapseHub, material: _Material, port: int) -> asyncio.Task[None]:
    context = build_server_ssl_context(
        certfile=material.server.cert, keyfile=material.server.key, client_ca_file=material.ca
    )
    task = asyncio.create_task(hub.serve("localhost", port, ssl_context=context))
    await _await_listening(port)
    return task


async def _stop(task: asyncio.Task[None] | None) -> None:
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


@contextlib.asynccontextmanager
async def _two_hubs(
    tmp_path: Path,
    *,
    laptop_up: bool = True,
    laptop_namespaces: frozenset[str] = frozenset({_NS}),
    workstation_ttl: float = 86_400.0,
    corrupt_laptop_journal: bool = False,
) -> AsyncIterator[_Pair]:
    material = _material(tmp_path)
    ws_port, lp_port = _free_port(), _free_port()
    stores = {
        "workstation": EventStore(tmp_path / "workstation.db"),
        "laptop": EventStore(tmp_path / "laptop.db"),
    }
    if corrupt_laptop_journal:
        from synapse_channel.core.journal import record_chat

        seq = record_chat(stores["laptop"], {"sender": "PROJ/x", "target": "all", "payload": "p"})
        stores["laptop"]._conn.execute(
            "UPDATE events SET payload = 'not-json' WHERE seq = ?", (seq,)
        )
        stores["laptop"]._conn.commit()
    workstation = _build_hub(
        material,
        stores["workstation"],
        name="workstation",
        remote="laptop",
        remote_port=lp_port,
        ttl=workstation_ttl,
    )
    laptop = _build_hub(
        material,
        stores["laptop"],
        name="laptop",
        remote="workstation",
        remote_port=ws_port,
        namespaces=laptop_namespaces,
    )
    ws_task = await _serve(workstation, material, ws_port)
    lp_task = await _serve(laptop, material, lp_port) if laptop_up else None
    pair = _Pair(
        material=material,
        workstation=workstation,
        laptop=laptop,
        ws_uri=f"wss://localhost:{ws_port}",
        lp_uri=f"wss://localhost:{lp_port}",
        stores=stores,
    )
    try:
        yield pair
    finally:
        await _stop(lp_task)
        await _stop(ws_task)
        for store in stores.values():
            store.close()


async def _agent(
    uri: str,
    ca: Path,
    name: str,
    *,
    capabilities: dict[str, str] | None = None,
) -> ClientConnection:
    """Connect an agent over server-verified TLS and register it at wire version 3."""
    websocket = await connect(uri, ssl=ssl.create_default_context(cafile=str(ca)))
    await read_until_type(websocket, MessageType.WELCOME)
    frame: dict[str, Any] = {
        "sender": name,
        "type": MessageType.HEARTBEAT,
        "target": "System",
        "payload": "online",
        "protocol_version": 3,
    }
    if capabilities is not None:
        frame["delivery_session_token"] = "b" * 64
        frame["delivery_capabilities"] = capabilities
    await websocket.send(json.dumps(frame))
    if capabilities is not None:
        await read_until_type(websocket, MessageType.DELIVERY_SESSION)
    return websocket


async def _chat(
    websocket: ClientConnection,
    sender: str,
    target: str,
    payload: str,
    *,
    receipt: bool = True,
    client_msg_id: bool = True,
) -> None:
    frame: dict[str, Any] = {
        "sender": sender,
        "type": MessageType.CHAT,
        "target": target,
        "payload": payload,
    }
    if receipt:
        frame["receipt_requested"] = True
    if client_msg_id:
        frame["client_msg_id"] = f"cm-{payload}"
    await websocket.send(json.dumps(frame))


async def _chat_from(websocket: ClientConnection, sender: str) -> dict[str, Any]:
    """Read frames until a chat from ``sender`` arrives."""
    for _ in range(_READ_LIMIT):
        frame = await read_until_type(websocket, MessageType.CHAT, limit=_READ_LIMIT)
        if frame.get("sender") == sender:
            return frame
    raise AssertionError(f"no chat from {sender}")


async def _receipt(websocket: ClientConnection) -> dict[str, Any]:
    return await read_until_type(websocket, MessageType.DELIVERY_RECEIPT, limit=_READ_LIMIT)


async def test_chat_crosses_hubs_both_ways_with_authenticated_provenance(tmp_path: Path) -> None:
    """A chat and its reply cross real mTLS; each side names the other hub as origin."""
    async with _two_hubs(tmp_path) as pair:
        ca = pair.material.ca
        alice = await _agent(pair.ws_uri, ca, "PROJ/alice")
        bob = await _agent(pair.lp_uri, ca, "PROJ/bob")
        try:
            await _chat(alice, "PROJ/alice", "PROJ/bob@laptop", "hello")
            received = await _chat_from(bob, "PROJ/alice@workstation")
            assert received["payload"] == "hello"
            assert received["target"] == "PROJ/bob"
            assert received["forwarded_from"] == "workstation"
            assert received["client_msg_id"] == "cm-hello"
            assert received["hub_id"] == "laptop"
            receipt = await _receipt(alice)
            assert receipt["delivered"] is True
            assert receipt["recipients"] == ["PROJ/bob@laptop"]
            assert receipt["forward_state"] == "accepted"
            assert receipt["forwarded_to"] == "laptop"
            assert receipt["message_target"] == "PROJ/bob@laptop"
            assert receipt["client_msg_id"] == "cm-hello"

            await _chat(bob, "PROJ/bob", "PROJ/alice@workstation", "welcome back")
            reply = await _chat_from(alice, "PROJ/bob@laptop")
            assert reply["payload"] == "welcome back"
            assert (await _receipt(bob))["delivered"] is True
        finally:
            await alice.close()
            await bob.close()
        # The feed on each hub keeps what left and what arrived.
        sent = [e for e in pair.stores["workstation"].read_all() if e.kind == "chat"]
        assert any(e.payload.get("target") == "PROJ/bob@laptop" for e in sent)
        arrived = [e for e in pair.stores["laptop"].read_all() if e.kind == "chat"]
        assert any(e.payload.get("sender") == "PROJ/alice@workstation" for e in arrived)
        # A remote target is never tracked as a local pending mailbox.
        assert "PROJ/bob@laptop" not in pair.workstation.mailbox_pending.known_identities


async def test_forwarded_chat_to_offline_seat_waits_in_that_hubs_mailbox(tmp_path: Path) -> None:
    """The peer accepts a chat for an absent seat; the receipt says where it waits."""
    async with _two_hubs(tmp_path) as pair:
        alice = await _agent(pair.ws_uri, pair.material.ca, "PROJ/alice")
        try:
            await _chat(alice, "PROJ/alice", "PROJ/carol@laptop", "later")
            receipt = await _receipt(alice)
        finally:
            await alice.close()
        assert receipt["forward_state"] == "accepted"
        assert receipt["delivered"] is False
        assert receipt["dead_lettered"] is True
        assert "waits in that hub's mailbox" in receipt["payload"]
        assert "PROJ/carol" in pair.laptop.mailbox_pending.known_identities


async def test_ungranted_namespace_is_refused_by_the_receiving_hub(tmp_path: Path) -> None:
    """A peer reaches only the namespaces its federation peering lists."""
    async with _two_hubs(tmp_path) as pair:
        alice = await _agent(pair.ws_uri, pair.material.ca, "PROJ/alice")
        other = await _agent(pair.lp_uri, pair.material.ca, "OTHER/dave")
        try:
            await _chat(alice, "PROJ/alice", "OTHER/dave@laptop", "not yours")
            receipt = await _receipt(alice)
            assert receipt["forward_state"] == "refused"
            assert receipt["reason"] == "forward_refused"
            assert "namespace_not_granted" in receipt["payload"]
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(_chat_from(other, "PROJ/alice@workstation"), 0.5)
        finally:
            await alice.close()
            await other.close()


@pytest.mark.parametrize("client", ["rogue", None])
async def test_unpinned_or_missing_client_certificate_is_refused(
    tmp_path: Path, client: str | None
) -> None:
    """Only the pinned client certificate may forward; the hub id alone proves nothing."""
    async with _two_hubs(tmp_path) as pair:
        identity = pair.material.clients[client] if client else None
        from synapse_channel.core.multihub_transport import pinned_connector

        peer = MessageForwardPeer(
            uri=pair.lp_uri,
            connector=pinned_connector(
                pair.material.server_pin,
                client_certificate_file=identity.cert if identity else None,
                client_key_file=identity.key if identity else None,
            ),
        )
        request = MessageForwardRequest(
            forward_id="impostor-1",
            kind="chat",
            sender_seat="PROJ/mallory",
            target_seat="PROJ/bob",
            body={"payload": "spoof"},
        )
        result = await forward_message(request, peer=peer, local_id="workstation")
        assert result.disposition == "refused"
        assert result.reason_code == "namespace_not_granted"
        assert pair.laptop.message_forward_ledger.inbound("workstation", "impostor-1") is None


async def test_unknown_hub_and_malformed_targets_are_refused_locally(tmp_path: Path) -> None:
    """Nothing leaves the hub unless the target is one seat on a configured peer."""
    async with _two_hubs(tmp_path) as pair:
        alice = await _agent(pair.ws_uri, pair.material.ca, "PROJ/alice")
        try:
            for target, text in [
                ("PROJ/bob@elsewhere", "not a configured message peer"),
                ("PROJ/bob@laptop,PROJ/carol", "exactly one seat"),
                ("PROJ/*@laptop", "not an audience or a glob"),
                ("all@laptop", "not an audience or a glob"),
                ("PROJ/bob@", "not a valid hub-qualified seat"),
                ("PROJ/b[ob]@laptop", "not an audience or a glob"),
                # 205 characters pass the address parser but exceed the wire's 256-byte bound.
                ("PROJ/" + "é" * 200 + "@laptop", "cannot be forwarded"),
            ]:
                await _chat(alice, "PROJ/alice", target, "x")
                error = await read_until_type(alice, MessageType.ERROR, limit=_READ_LIMIT)
                assert text in error["payload"]
        finally:
            await alice.close()
        assert pair.workstation.message_forward_ledger.pending_counts() == {}


async def test_unreachable_peer_queues_retries_and_reports_the_final_outcome(
    tmp_path: Path,
) -> None:
    """A peer outage keeps the chat pending; the retry delivers and the sender is told."""
    async with _two_hubs(tmp_path, laptop_up=False) as pair:
        alice = await _agent(pair.ws_uri, pair.material.ca, "PROJ/alice")
        lp_port = int(pair.lp_uri.rsplit(":", 1)[1])
        lp_task: asyncio.Task[None] | None = None
        try:
            await _chat(alice, "PROJ/alice", "PROJ/bob@laptop", "queued")
            first = await _receipt(alice)
            assert first["forward_state"] == "pending"
            assert first["deferred"] is True
            assert first["reason"] == "forward_pending"
            assert pair.workstation.message_forward_ledger.pending_counts() == {"laptop": 1}

            lp_task = await _serve(pair.laptop, pair.material, lp_port)
            bob = await _agent(pair.lp_uri, pair.material.ca, "PROJ/bob")
            try:
                settled = await run_forward_retries(pair.workstation, now=time.time() + 3600)
                assert settled == 1
                received = await _chat_from(bob, "PROJ/alice@workstation")
                assert received["payload"] == "queued"
                final = await _receipt(alice)
                assert final["forward_state"] == "accepted"
                assert final["delivered"] is True
                assert final["deferred"] is True
            finally:
                await bob.close()
        finally:
            await alice.close()
            await _stop(lp_task)
        assert pair.workstation.message_forward_ledger.pending_counts() == {}


async def test_expiry_is_reported_on_the_senders_next_registration(tmp_path: Path) -> None:
    """An offline sender learns on reconnect that its forward expired unanswered."""
    async with _two_hubs(tmp_path, laptop_up=False, workstation_ttl=1.0) as pair:
        alice = await _agent(pair.ws_uri, pair.material.ca, "PROJ/alice")
        await _chat(alice, "PROJ/alice", "PROJ/bob@laptop", "too late")
        assert (await _receipt(alice))["forward_state"] == "pending"
        await alice.close()
        assert await run_forward_retries(pair.workstation, now=time.time() + 5) == 1
        alice = await _agent(pair.ws_uri, pair.material.ca, "PROJ/alice")
        try:
            final = await _receipt(alice)
        finally:
            await alice.close()
        assert final["forward_state"] == "expired"
        assert final["reason"] == "forward_expired"
        assert (
            pair.workstation.message_forward_ledger.pending_sender_notifications("PROJ/alice") == []
        )


async def test_retried_forward_is_answered_once_and_a_reused_id_is_refused(
    tmp_path: Path,
) -> None:
    """At-least-once transport, one mailbox entry: the peer deduplicates on its id."""
    async with _two_hubs(tmp_path) as pair:
        bob = await _agent(pair.lp_uri, pair.material.ca, "PROJ/bob")
        peers = pair.workstation.message_peers
        assert peers is not None
        peer = peers["laptop"]
        request = MessageForwardRequest(
            forward_id="retry-1",
            kind="chat",
            sender_seat="PROJ/alice",
            target_seat="PROJ/bob",
            body={"payload": "once"},
        )
        try:
            first = await forward_message(request, peer=peer, local_id="workstation")
            second = await forward_message(request, peer=peer, local_id="workstation")
            assert (first.disposition, second.disposition) == ("accepted", "duplicate")
            assert second.result == first.result
            await _chat_from(bob, "PROJ/alice@workstation")
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(_chat_from(bob, "PROJ/alice@workstation"), 0.5)
            altered = MessageForwardRequest(
                forward_id="retry-1",
                kind="chat",
                sender_seat="PROJ/alice",
                target_seat="PROJ/bob",
                body={"payload": "different"},
            )
            conflict = await forward_message(altered, peer=peer, local_id="workstation")
            assert conflict.disposition == "refused"
            assert conflict.reason_code == "forward_id_conflict"
        finally:
            await bob.close()


async def _who(websocket: ClientConnection, sender: str, hub: str) -> dict[str, Any]:
    await websocket.send(
        json.dumps(
            {"sender": sender, "type": MessageType.WHO_REQUEST, "target": "System", "hub": hub}
        )
    )
    return await read_until_type(websocket, MessageType.WHO_SNAPSHOT, limit=_READ_LIMIT)


async def test_delivery_intent_crosses_hubs_with_status_cancel_and_no_task_control(
    tmp_path: Path,
) -> None:
    """A v3 intent is admitted by the recipient's hub; follow-ups route back to it."""
    async with _two_hubs(tmp_path) as pair:
        ca = pair.material.ca
        bob = await _agent(
            pair.lp_uri, ca, "PROJ/bob", capabilities={"next_turn": "emulated", "steer": "native"}
        )
        other = await _agent(pair.lp_uri, ca, "OTHER/eve", capabilities={"next_turn": "native"})
        zed = await _agent(pair.lp_uri, ca, "PROJ/zed")
        alice = await _agent(pair.ws_uri, ca, "PROJ/alice")
        mallory = await _agent(pair.ws_uri, ca, "PROJ/mallory")
        try:
            roster = await _who(alice, "PROJ/alice", "laptop")
            assert roster["remote_hub"] == "laptop"
            assert "PROJ/bob@laptop" in roster["online_agents"]
            assert "PROJ/zed@laptop" in roster["online_agents"]
            assert "PROJ/zed@laptop" not in roster["delivery_sessions"]
            assert "OTHER/eve@laptop" not in roster["online_agents"]
            assert not any("workstation" in name for name in roster["online_agents"])
            session = roster["delivery_sessions"]["PROJ/bob@laptop"]
            assert session["hub_id"] == "laptop"

            request = {
                "sender": "PROJ/alice",
                "type": MessageType.DELIVERY_REQUEST,
                "target": "PROJ/bob@laptop",
                "protocol_version": 3,
                "request_id": "req-x",
                "idempotency_key": "idem-x",
                "target_incarnation": session["incarnation"],
                "mode": "follow_up",
                "allowed_fallbacks": ["next_turn"],
                "task_id": "T-X",
                "body": "Please review.",
                "deadline": time.time() + 120,
            }
            await alice.send(json.dumps(request))
            status = await read_until_type(alice, MessageType.DELIVERY_STATUS, limit=_READ_LIMIT)
            offer = await read_until_type(bob, MessageType.DELIVERY_OFFER, limit=_READ_LIMIT)
            assert status["stage"] == "queued"
            assert status["remote_hub"] == "laptop"
            assert status["target"] == "PROJ/alice"
            assert offer["sender"] == "PROJ/alice@workstation"
            assert offer["origin_hub"] == "workstation"
            assert offer["operation_key"] == status["operation_key"]

            await alice.send(
                json.dumps(
                    {
                        "sender": "PROJ/alice",
                        "type": MessageType.DELIVERY_STATUS_REQUEST,
                        "target": "System",
                        "protocol_version": 3,
                        "operation_key": status["operation_key"],
                    }
                )
            )
            queried = await read_until_type(alice, MessageType.DELIVERY_STATUS, limit=_READ_LIMIT)
            assert queried["operation_key"] == status["operation_key"]
            assert queried["remote_hub"] == "laptop"

            # Only the requesting seat may follow up a forwarded delivery.
            await mallory.send(
                json.dumps(
                    {
                        "sender": "PROJ/mallory",
                        "type": MessageType.DELIVERY_STATUS_REQUEST,
                        "target": "System",
                        "protocol_version": 3,
                        "operation_key": status["operation_key"],
                    }
                )
            )
            denied = await read_until_type(mallory, MessageType.DELIVERY_REFUSED, limit=_READ_LIMIT)
            assert denied["reason_code"] == "unauthorised_requester"

            cancel = {
                "sender": "PROJ/alice",
                "type": MessageType.DELIVERY_CANCEL,
                "target": "System",
                "protocol_version": 3,
                "operation_key": status["operation_key"],
                "mutation_id": "cancel-1",
            }
            await alice.send(json.dumps(cancel))
            cancelled = await read_until_type(alice, MessageType.DELIVERY_STATUS, limit=_READ_LIMIT)
            assert cancelled["cancel_requested"] is True
            # A retried cancellation replays the same answer without a second notification.
            await alice.send(json.dumps(cancel))
            replayed = await read_until_type(alice, MessageType.DELIVERY_STATUS, limit=_READ_LIMIT)
            assert replayed["cancel_requested"] is True
            assert replayed["operation_key"] == cancelled["operation_key"]

            steer = dict(request, request_id="req-s", idempotency_key="idem-s", mode="steer")
            steer["allowed_fallbacks"] = []
            await alice.send(json.dumps(steer))
            refused = await read_until_type(alice, MessageType.DELIVERY_REFUSED, limit=_READ_LIMIT)
            assert refused["reason_code"] == "unauthorised_requester"

            for target, key, code in [
                ("PROJ/*@laptop", "glob", "invalid_target"),
                ("PROJ/" + "é" * 200 + "@laptop", "long", "invalid_shape"),
            ]:
                bad = dict(request, target=target, request_id=f"req-{key}")
                bad["idempotency_key"] = f"idem-{key}"
                await alice.send(json.dumps(bad))
                answer = await read_until_type(
                    alice, MessageType.DELIVERY_REFUSED, limit=_READ_LIMIT
                )
                assert answer["reason_code"] == code
        finally:
            for websocket in (alice, bob, other, zed, mallory):
                await websocket.close()

    # The recipient's journal keeps the forwarded intent and reopens under the same hub id.
    store = EventStore(tmp_path / "laptop.db")
    try:
        store.delivery.verify_origin_hub("laptop")
    finally:
        store.close()


async def test_who_for_an_unconfigured_hub_is_an_error(tmp_path: Path) -> None:
    """A roster request names only configured message peers."""
    async with _two_hubs(tmp_path) as pair:
        alice = await _agent(pair.ws_uri, pair.material.ca, "PROJ/alice")
        try:
            await alice.send(
                json.dumps(
                    {"sender": "PROJ/alice", "type": MessageType.WHO_REQUEST, "hub": "nowhere"}
                )
            )
            error = await read_until_type(alice, MessageType.ERROR, limit=_READ_LIMIT)
        finally:
            await alice.close()
        assert "unknown_hub" in error["payload"]


async def test_a_local_client_cannot_register_a_hub_qualified_name(tmp_path: Path) -> None:
    """The ``@`` form is reserved end to end, so a forwarded sender cannot be forged locally."""
    async with _two_hubs(tmp_path) as pair:
        websocket = await connect(
            pair.lp_uri, ssl=ssl.create_default_context(cafile=str(pair.material.ca))
        )
        try:
            await read_until_type(websocket, MessageType.WELCOME)
            await websocket.send(
                json.dumps(
                    {
                        "sender": "PROJ/alice@workstation",
                        "type": MessageType.CHAT,
                        "target": "PROJ/bob",
                        "payload": "forged",
                    }
                )
            )
            conflict = await read_until_type(websocket, MessageType.NAME_CONFLICT)
            assert "reserved for seats on peer hubs" in conflict["payload"]
            with pytest.raises(ConnectionClosed):
                await asyncio.wait_for(websocket.recv(), 2.0)
        finally:
            await websocket.close()


async def test_plain_chat_needs_no_receipt_and_honours_private_routing(tmp_path: Path) -> None:
    """Without a receipt nothing is reported back; private routing skips the local fan-out."""
    async with _two_hubs(tmp_path) as pair:
        pair.workstation.private_directed_messages = True
        pair.workstation.max_history = 1
        ca = pair.material.ca
        alice = await _agent(pair.ws_uri, ca, "PROJ/alice")
        watcher = await _agent(pair.ws_uri, ca, "PROJ/watcher")
        bob = await _agent(pair.lp_uri, ca, "PROJ/bob")
        try:
            await _chat(
                alice, "PROJ/alice", "PROJ/bob@laptop", "quiet", receipt=False, client_msg_id=False
            )
            quiet = await _chat_from(bob, "PROJ/alice@workstation")
            assert quiet["payload"] == "quiet"
            assert "client_msg_id" not in quiet
            await _chat(alice, "PROJ/alice", "PROJ/bob@laptop", "asked", client_msg_id=False)
            receipt = await _receipt(alice)
            assert receipt["delivered"] is True
            assert "client_msg_id" not in receipt
            assert receipt["message_seq"] >= 1
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(_chat_from(watcher, "PROJ/alice"), 0.5)
        finally:
            for websocket in (alice, watcher, bob):
                await websocket.close()
        assert [entry["payload"] for entry in pair.workstation.chat_history] == ["asked"]


async def test_a_retried_cross_hub_chat_is_forwarded_once(tmp_path: Path) -> None:
    """K4-WF8: the origin answers the retry itself; the peer never sees a second copy."""
    async with _two_hubs(tmp_path) as pair:
        ca = pair.material.ca
        alice = await _agent(pair.ws_uri, ca, "PROJ/alice")
        bob = await _agent(pair.lp_uri, ca, "PROJ/bob")
        try:
            await _chat(alice, "PROJ/alice", "PROJ/bob@laptop", "retry", receipt=False)
            first = await _chat_from(bob, "PROJ/alice@workstation")
            await _chat(alice, "PROJ/alice", "PROJ/bob@laptop", "retry", receipt=False)
            notice = await read_until_type(alice, MessageType.SYSTEM, limit=_READ_LIMIT)
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(_chat_from(bob, "PROJ/alice@workstation"), 0.5)
        finally:
            for websocket in (alice, bob):
                await websocket.close()
        forwarded = [
            entry for entry in pair.workstation.chat_history if entry.get("payload") == "retry"
        ]
    assert first["payload"] == "retry"
    assert notice["duplicate"] is True
    assert notice["client_msg_id"] == "cm-retry"
    assert notice["msg_id"] == forwarded[0]["msg_id"]
    assert len(str(notice["forward_id"])) == 32
    assert len(forwarded) == 1


def _workstation_peer(pair: _Pair) -> MessageForwardPeer:
    peers = pair.workstation.message_peers
    assert peers is not None
    return peers["laptop"]


def _forward(forward_id: str, **fields: Any) -> MessageForwardRequest:
    values: dict[str, Any] = {
        "kind": "chat",
        "sender_seat": "PROJ/alice",
        "target_seat": "PROJ/bob",
        "body": {"payload": "hi"},
    }
    values.update(fields)
    return MessageForwardRequest(forward_id=forward_id, **values)


async def test_the_serving_hub_refuses_what_it_cannot_prove_or_place(tmp_path: Path) -> None:
    """Malformed frames, forged origins, unowned targets and unknown operations are refused."""
    async with _two_hubs(tmp_path) as pair:
        peer = _workstation_peer(pair)
        assert peer.connector is not None
        async with peer.connector(pair.lp_uri) as socket:
            await socket.send(
                json.dumps(
                    {
                        "sender": "workstation",
                        "type": MessageType.MULTIHUB_MESSAGE_FORWARD,
                        "forward_id": "broken",
                    }
                )
            )
            error = await read_until_type(socket, MessageType.ERROR, limit=_READ_LIMIT)
        assert error["payload"] == "Malformed multi-hub message forward"

        cases = [
            (_forward("o-1"), "PROJ/forged", "invalid_origin"),
            (_forward("o-2", kind="who", target_seat="", body={}), "PROJ/forged", "invalid_origin"),
            (_forward("n-1", target_seat="bob"), "workstation", "namespace_not_granted"),
            (_forward("s-1"), "stranger", "namespace_not_granted"),
            # A modified peer must not widen a forward past the authorised namespace.
            (_forward("w-1", target_seat="PROJ/bob,OTHER/dave"), "workstation", "invalid_target"),
            (_forward("w-2", target_seat="PROJ/*"), "workstation", "invalid_target"),
            (
                _forward("w-3", kind="delivery_request", target_seat="PROJ/b[ob]"),
                "workstation",
                "invalid_target",
            ),
            (
                _forward(
                    "v-1",
                    kind="delivery_status",
                    target_seat="",
                    body={"operation_key": "a" * 64, "protocol_version": 2},
                ),
                "workstation",
                "unsupported_protocol",
            ),
            (_forward("p-1", body={"payload": 5}), "workstation", "invalid_shape"),
            (
                _forward(
                    "u-1",
                    kind="delivery_status",
                    target_seat="",
                    body={"operation_key": "a" * 64, "protocol_version": 3},
                ),
                "workstation",
                "unknown_request",
            ),
        ]
        for request, local_id, code in cases:
            result = await forward_message(request, peer=peer, local_id=local_id)
            assert (result.disposition, result.reason_code) == ("refused", code), request
            assert pair.laptop.message_forward_ledger.inbound(local_id, request.forward_id) is None


async def test_a_refusal_is_not_remembered_so_a_retry_succeeds_once_the_cause_clears(
    tmp_path: Path,
) -> None:
    """A quota refusal is transient: the same forward is accepted after the window frees up."""
    from synapse_channel.core.durable_ingress import DurableIngressQuota

    async with _two_hubs(tmp_path) as pair:
        peer = _workstation_peer(pair)
        pair.laptop.durable_ingress_quota = DurableIngressQuota(max_events=1, window_seconds=3600)
        first = await forward_message(_forward("q-1"), peer=peer, local_id="workstation")
        blocked = await forward_message(_forward("q-2"), peer=peer, local_id="workstation")
        assert first.disposition == "accepted"
        assert (blocked.disposition, blocked.reason_code) == ("refused", "chat_refused")
        assert "quota" in blocked.detail
        pair.laptop.durable_ingress_quota = None
        retried = await forward_message(_forward("q-2"), peer=peer, local_id="workstation")
        assert retried.disposition == "accepted"


async def test_a_hub_needing_journal_recovery_refuses_forwarded_mutations(tmp_path: Path) -> None:
    """Forwards obey the fail-closed recovery rule, and only after the peer is authorised."""
    async with _two_hubs(tmp_path, corrupt_laptop_journal=True) as pair:
        assert pair.laptop.journal_corrupt_rows
        peer = _workstation_peer(pair)
        cases = [
            (_forward("d-1"), "workstation", "journal_recovery_required"),
            (
                _forward(
                    "d-2",
                    kind="delivery_request",
                    body={"protocol_version": 3, "request_id": "r", "idempotency_key": "i"},
                ),
                "workstation",
                "journal_recovery_required",
            ),
            (_forward("d-3"), "stranger", "namespace_not_granted"),
        ]
        for request, local_id, code in cases:
            result = await forward_message(request, peer=peer, local_id=local_id)
            assert (result.disposition, result.reason_code) == ("refused", code), request
        roster = await forward_message(
            _forward("d-4", kind="who", target_seat="", body={}), peer=peer, local_id="workstation"
        )
        assert roster.disposition == "accepted"


async def test_a_forwarded_cancel_is_refused_after_the_owning_hub_restarts_degraded(
    tmp_path: Path,
) -> None:
    """A delivery admitted before a journal fault cannot be mutated until recovery."""
    async with _two_hubs(tmp_path) as pair:
        ca = pair.material.ca
        bob = await _agent(pair.lp_uri, ca, "PROJ/bob", capabilities={"next_turn": "emulated"})
        alice = await _agent(pair.ws_uri, ca, "PROJ/alice")
        try:
            session = (await _who(alice, "PROJ/alice", "laptop"))["delivery_sessions"][
                "PROJ/bob@laptop"
            ]
            await alice.send(
                json.dumps(
                    {
                        "sender": "PROJ/alice",
                        "type": MessageType.DELIVERY_REQUEST,
                        "target": "PROJ/bob@laptop",
                        "protocol_version": 3,
                        "request_id": "req-r",
                        "idempotency_key": "idem-r",
                        "target_incarnation": session["incarnation"],
                        "mode": "follow_up",
                        "allowed_fallbacks": ["next_turn"],
                        "task_id": "T-R",
                        "body": "Before the fault.",
                        "deadline": time.time() + 120,
                    }
                )
            )
            key = (await read_until_type(alice, MessageType.DELIVERY_STATUS, limit=_READ_LIMIT))[
                "operation_key"
            ]
        finally:
            await alice.close()
            await bob.close()

    async with _two_hubs(tmp_path, corrupt_laptop_journal=True) as pair:
        alice = await _agent(pair.ws_uri, pair.material.ca, "PROJ/alice")
        try:
            await alice.send(
                json.dumps(
                    {
                        "sender": "PROJ/alice",
                        "type": MessageType.DELIVERY_CANCEL,
                        "target": "System",
                        "protocol_version": 3,
                        "operation_key": key,
                        "mutation_id": "cancel-r",
                    }
                )
            )
            refused = await read_until_type(alice, MessageType.DELIVERY_REFUSED, limit=_READ_LIMIT)
        finally:
            await alice.close()
        assert refused["reason_code"] == "journal_recovery_required"
