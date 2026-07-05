# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li — an origin hub forwards an operator relay to the owner, over real sockets

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
from synapse_channel.core.journal import EventKind
from synapse_channel.core.multihub_serving import MultiHubServingGrant, MultiHubServingPolicy
from synapse_channel.core.namespace_ownership import NamespaceOwnership
from synapse_channel.core.operator_relay_routing import NO_RELAY_ROUTE
from synapse_channel.core.operator_relay_transport import OperatorRelayPeer, RelayTransportError
from synapse_channel.core.operator_relay_wire import (
    RelayActionRequest,
    RelayActionResult,
    decode_relay_result,
    encode_relay_request,
)
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.tls import MTLSPeerTrustBundle, MTLSTrustedPeer, certificate_sha256_pin

_NAMESPACE = "OWNED"
_OWNER = "syn-owner"
_EDGE = "syn-edge"
_AGENT = "edge-operator"
_DOMAIN = "domain-edge"
_KEY = "OWNED:main:2026-06"


def _edge_ownership() -> NamespaceOwnership:
    """Return the origin hub's map: the namespace is owned by the owner hub, not here."""
    return NamespaceOwnership(owners={_NAMESPACE: _OWNER}, local_hub_id=_EDGE)


class _FakeRelayForwarder:
    """A stand-in relay forwarder returning a preset result or raising, recording its calls."""

    def __init__(
        self, *, result: RelayActionResult | None = None, error: Exception | None = None
    ) -> None:
        self._result = result
        self._error = error
        self.calls: list[tuple[RelayActionRequest, str, str, str | None]] = []

    async def __call__(
        self, request: RelayActionRequest, *, uri: str, local_id: str, token: str | None = None
    ) -> RelayActionResult:
        self.calls.append((request, uri, local_id, token))
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


def _applied(detail: str = "released by operator ops-admin (was held by x)") -> RelayActionResult:
    """Return the applied verdict the owner would relay back through the origin hub."""
    return RelayActionResult(
        applied=True,
        action="release",
        namespace=_NAMESPACE,
        task_id="t1",
        owner_hub_id=_OWNER,
        detail=detail,
    )


def _edge_hub(
    forwarder: _FakeRelayForwarder,
    *,
    peers: bool = True,
    journal: EventStore | None = None,
    ownership: NamespaceOwnership | None = None,
) -> SynapseHub:
    """Return a non-owning origin hub that relays a remote-owned action through ``forwarder``."""
    relay_peers = {_OWNER: OperatorRelayPeer(uri="ws://owner/", token="tok")} if peers else None
    return SynapseHub(
        hub_id=_EDGE,
        namespace_ownership=ownership if ownership is not None else _edge_ownership(),
        relay_peers=relay_peers,
        relay_forwarder=forwarder,
        journal=journal,
    )


async def _connect(uri: str, name: str) -> ClientConnection:
    """Open a raw client socket, drain the welcome, and register with a heartbeat."""
    websocket = await connect(uri)
    await read_until_type(websocket, "welcome")
    await send_json(websocket, sender=name, type="heartbeat")
    return websocket


def _request(*, task_id: str = "t1", operator: str = "ops-admin") -> RelayActionRequest:
    """A relay request as a local agent sends it, asserting an origin id the hub must override."""
    return RelayActionRequest(
        action="release",
        namespace=_NAMESPACE,
        task_id=task_id,
        operator=operator,
        origin_hub_id="agent-asserted-origin",
    )


async def _relay(uri: str, request: RelayActionRequest, *, agent: str = _AGENT) -> dict[str, Any]:
    """Relay one action as a local agent and read the result frame the origin hub relays back."""
    async with await _connect(uri, agent) as ws:
        await send_json(
            ws,
            sender=agent,
            type=MessageType.OPERATOR_RELAY_REQUEST,
            **encode_relay_request(request),
        )
        return await read_until_type(ws, MessageType.OPERATOR_RELAY_RESULT)


def _relay_audit(journal: EventStore) -> dict[str, Any]:
    """Return the single operator_relay audit payload recorded on the origin hub."""
    events = [e.payload for e in journal.read_all() if e.kind == EventKind.OPERATOR_RELAY]
    assert len(events) == 1
    return events[0]


async def test_forwards_a_remote_owned_relay_and_relays_the_applied_verdict(tmp_path: Path) -> None:
    forwarder = _FakeRelayForwarder(result=_applied())
    journal = EventStore(tmp_path / "events.db")
    hub = _edge_hub(forwarder, journal=journal)
    async with running_hub(hub) as (_, uri):
        reply = await _relay(uri, _request())
    result = decode_relay_result(reply)
    assert result.applied is True
    assert result.owner_hub_id == _OWNER
    # Forwarded with this hub stamped as origin and sender, the operator preserved, over the route.
    forwarded, peer_uri, local_id, token = forwarder.calls[0]
    assert forwarded.origin_hub_id == _EDGE  # the agent-asserted origin was overridden
    assert forwarded.operator == "ops-admin"
    assert forwarded.namespace == _NAMESPACE
    assert forwarded.task_id == "t1"
    assert peer_uri == "ws://owner/"
    assert local_id == _EDGE
    assert token == "tok"
    # The origin hub records the outbound half of the two-hub audit trail.
    audit = _relay_audit(journal)
    assert audit["direction"] == "out"
    assert audit["agent"] == _AGENT
    assert audit["owner_hub_id"] == _OWNER
    assert audit["origin_hub_id"] == _EDGE
    assert audit["applied"] is True


async def test_relays_the_owners_refusal_back_to_the_requester() -> None:
    refused = RelayActionResult(
        applied=False,
        action="release",
        namespace=_NAMESPACE,
        task_id="t1",
        owner_hub_id=_OWNER,
        detail="namespace_not_owned",
    )
    hub = _edge_hub(_FakeRelayForwarder(result=refused))
    async with running_hub(hub) as (_, uri):
        reply = await _relay(uri, _request())
    result = decode_relay_result(reply)
    assert result.applied is False
    assert result.detail == "namespace_not_owned"


async def test_a_failed_forward_reports_unapplied_and_audits_the_attempt(tmp_path: Path) -> None:
    journal = EventStore(tmp_path / "events.db")
    forwarder = _FakeRelayForwarder(error=RelayTransportError("owner unreachable"))
    hub = _edge_hub(forwarder, journal=journal)
    async with running_hub(hub) as (_, uri):
        reply = await _relay(uri, _request())
    result = decode_relay_result(reply)
    assert result.applied is False
    assert result.detail == "relay to the owning hub failed"
    assert result.owner_hub_id == _OWNER
    # A relay that reached the wire but not a verdict is still audited outbound, as unapplied.
    audit = _relay_audit(journal)
    assert audit["direction"] == "out"
    assert audit["applied"] is False


async def test_a_remote_relay_without_a_route_is_refused_and_names_the_owner(
    tmp_path: Path,
) -> None:
    journal = EventStore(tmp_path / "events.db")
    forwarder = _FakeRelayForwarder(result=_applied())
    hub = _edge_hub(forwarder, peers=False, journal=journal)
    async with running_hub(hub) as (_, uri):
        reply = await _relay(uri, _request())
    result = decode_relay_result(reply)
    assert result.applied is False
    assert result.detail == NO_RELAY_ROUTE
    assert result.owner_hub_id == _OWNER
    assert forwarder.calls == []  # no route, so nothing was forwarded
    # A relay refused before it ever reached the wire leaves no outbound audit event.
    assert [e for e in journal.read_all() if e.kind == EventKind.OPERATOR_RELAY] == []


async def test_an_ungoverned_namespace_is_refused_without_forwarding() -> None:
    forwarder = _FakeRelayForwarder(result=_applied())
    hub = _edge_hub(
        forwarder,
        ownership=NamespaceOwnership(owners={}, local_hub_id=_EDGE),
    )
    async with running_hub(hub) as (_, uri):
        reply = await _relay(uri, _request())
    result = decode_relay_result(reply)
    assert result.applied is False
    assert result.detail == "ungoverned"
    assert forwarder.calls == []


async def test_a_malformed_remote_relay_is_answered_with_an_error(tmp_path: Path) -> None:
    forwarder = _FakeRelayForwarder(result=_applied())
    hub = _edge_hub(forwarder, journal=EventStore(tmp_path / "events.db"))
    async with running_hub(hub) as (_, uri):
        async with await _connect(uri, _AGENT) as ws:
            # No ``operator`` field: the codec rejects the forward before it reaches the owner.
            await send_json(
                ws,
                sender=_AGENT,
                type=MessageType.OPERATOR_RELAY_REQUEST,
                action="release",
                namespace=_NAMESPACE,
                task_id="t1",
                origin_hub_id="agent-asserted-origin",
            )
            message = await read_until_type(ws, MessageType.ERROR)
    assert "Malformed operator relay request" in message["payload"]
    assert forwarder.calls == []


async def test_an_empty_target_namespace_is_left_to_the_serving_handler_to_reject() -> None:
    # The gate cannot route without a namespace, so it steps aside and the serving handler
    # rejects the malformed frame — one owner of the malformed-error semantics, not two.
    forwarder = _FakeRelayForwarder(result=_applied())
    hub = _edge_hub(forwarder)
    async with running_hub(hub) as (_, uri):
        async with await _connect(uri, _AGENT) as ws:
            await send_json(
                ws,
                sender=_AGENT,
                type=MessageType.OPERATOR_RELAY_REQUEST,
                action="release",
                namespace="",
                task_id="t1",
                operator="ops-admin",
                origin_hub_id="agent-asserted-origin",
            )
            message = await read_until_type(ws, MessageType.ERROR)
    assert "Malformed operator relay request" in message["payload"]
    assert forwarder.calls == []


async def test_a_partitioned_namespace_is_refused_using_the_asserting_hubs_feed() -> None:
    # This hub believes it owns the namespace, but a live feed reports a peer asserting the same,
    # so ownership resolves partitioned and the relay is refused rather than applied or forwarded.
    forwarder = _FakeRelayForwarder(result=_applied())
    hub = SynapseHub(
        hub_id=_EDGE,
        namespace_ownership=NamespaceOwnership(owners={_NAMESPACE: _EDGE}, local_hub_id=_EDGE),
        relay_peers={_OWNER: OperatorRelayPeer(uri="ws://owner/")},
        relay_forwarder=forwarder,
        observed_asserting_hubs=lambda namespace: (
            ("syn-contender",) if namespace == _NAMESPACE else ()
        ),
    )
    async with running_hub(hub) as (_, uri):
        reply = await _relay(uri, _request())
    result = decode_relay_result(reply)
    assert result.applied is False
    assert result.detail == "partitioned"
    assert forwarder.calls == []


async def test_a_locally_owned_relay_is_not_forwarded_but_reaches_the_serving_handler() -> None:
    # This hub owns the namespace, so the gate steps aside; with no serving policy the serving
    # handler refuses the local agent as an unauthorised peer — proof it took the local path.
    forwarder = _FakeRelayForwarder(result=_applied())
    hub = _edge_hub(
        forwarder,
        ownership=NamespaceOwnership(owners={_NAMESPACE: _EDGE}, local_hub_id=_EDGE),
    )
    async with running_hub(hub) as (_, uri):
        reply = await _relay(uri, _request())
    result = decode_relay_result(reply)
    assert result.applied is False
    assert result.detail == "peer_not_authorised"  # the serving handler's own refusal
    assert forwarder.calls == []  # never forwarded — the gate stepped aside


# --- real two-hub relay: a live origin hub relays to a live owning hub --------------------


def _write_peer_cert(tmp_path: Path) -> tuple[str, bytes]:
    """Write a self-signed certificate for the origin hub; return its pin and live DER bytes."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    certfile = tmp_path / "edge-cert.pem"
    keyfile = tmp_path / "edge-key.pem"
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
            "/CN=syn-edge",
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


def _owner_serving_policy(pin: str, der: bytes) -> MultiHubServingPolicy:
    """Build the owner's policy trusting the origin hub to relay a *release* in the namespace."""
    return MultiHubServingPolicy(
        federation=FederationBundle(
            [
                FederationPeer(
                    domain_id=_DOMAIN,
                    namespaces=frozenset({_NAMESPACE}),
                    certificate_pins=frozenset({pin}),
                    signing_key_ids=frozenset({_KEY}),
                    scope_grants=(ScopeGrant(verb="release", namespace=_NAMESPACE),),
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
            _EDGE: MultiHubServingGrant(
                domain_id=_DOMAIN, namespace=_NAMESPACE, signing_key_id=_KEY
            )
        },
        clock=lambda: 0.0,
        cert_source=lambda _websocket: der,
    )


async def test_a_live_origin_hub_relays_a_release_to_a_live_owning_hub(tmp_path: Path) -> None:
    pin, der = _write_peer_cert(tmp_path)
    owner = SynapseHub(
        hub_id=_OWNER,
        namespace_ownership=NamespaceOwnership(owners={_NAMESPACE: _OWNER}, local_hub_id=_OWNER),
        multihub_serving_policy=_owner_serving_policy(pin, der),
    )
    owner.state.claim("OWNED/holder", "t1")
    async with running_hub(owner) as (_, owner_uri):
        edge = SynapseHub(
            hub_id=_EDGE,
            namespace_ownership=_edge_ownership(),
            relay_peers={_OWNER: OperatorRelayPeer(uri=owner_uri)},
        )
        async with running_hub(edge) as (_, edge_uri):
            reply = await _relay(edge_uri, _request())
    result = decode_relay_result(reply)
    # The origin hub reached the owner, which force-released the lease and answered applied.
    assert result.applied is True
    assert result.owner_hub_id == _OWNER
    assert "t1" not in owner.state.claims  # the lease lives on — and was freed on — the owner
