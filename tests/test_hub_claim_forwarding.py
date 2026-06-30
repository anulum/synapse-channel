# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li — a non-owning hub forwards a claim to its owner, over real sockets

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
from synapse_channel.core.multihub_claim_transport import ClaimForwardError, ClaimForwardPeer
from synapse_channel.core.multihub_claim_wire import ClaimForwardRequest, ClaimForwardResult
from synapse_channel.core.multihub_serving import MultiHubServingGrant, MultiHubServingPolicy
from synapse_channel.core.namespace_ownership import NamespaceOwnership
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.tls import MTLSPeerTrustBundle, MTLSTrustedPeer, certificate_sha256_pin

_NAMESPACE = "OWNED"
_OWNER = "syn-owner"
_EDGE = "syn-edge"
_AGENT = "OWNED/alice"
_DOMAIN = "domain-owner"
_KEY = "OWNED:main:2026-06"


def _edge_ownership() -> NamespaceOwnership:
    """Return the non-owning hub's map: the namespace is owned by the owner hub."""
    return NamespaceOwnership(owners={_NAMESPACE: _OWNER}, local_hub_id=_EDGE)


class _FakeForwarder:
    """A stand-in claim forwarder returning a preset result or raising, recording its calls."""

    def __init__(
        self, *, result: ClaimForwardResult | None = None, error: Exception | None = None
    ) -> None:
        self._result = result
        self._error = error
        self.calls: list[tuple[ClaimForwardRequest, str, str, str | None]] = []

    async def __call__(
        self, request: ClaimForwardRequest, *, uri: str, local_id: str, token: str | None = None
    ) -> ClaimForwardResult:
        self.calls.append((request, uri, local_id, token))
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


def _granted(task_id: str = "t1") -> ClaimForwardResult:
    """Return a granted result with the lease fields the owner would relay."""
    return ClaimForwardResult(
        granted=True,
        task_id=task_id,
        namespace=_NAMESPACE,
        owner_hub_id=_OWNER,
        detail="claimed",
        grant={
            "task_id": task_id,
            "owner": _AGENT,
            "status": "claimed",
            "lease_expires_at": 999.0,
            "paths": [],
        },
    )


def _edge_hub(forwarder: _FakeForwarder, *, peers: bool = True) -> SynapseHub:
    """Return a non-owning hub that forwards a remote-owned claim through ``forwarder``."""
    claim_peers = {_OWNER: ClaimForwardPeer(uri="ws://owner/")} if peers else None
    return SynapseHub(
        hub_id=_EDGE,
        namespace_ownership=_edge_ownership(),
        claim_peers=claim_peers,
        claim_forwarder=forwarder,
    )


async def _connect(uri: str, name: str) -> ClientConnection:
    """Open a raw client socket, drain the welcome, and register with a heartbeat."""
    websocket = await connect(uri)
    await read_until_type(websocket, "welcome")
    await send_json(websocket, sender=name, type="heartbeat")
    return websocket


async def _claim(uri: str, agent: str, expected: str, *, task_id: str = "t1") -> dict[str, Any]:
    """Claim a task as ``agent`` and read the reply of ``expected`` type back."""
    async with await _connect(uri, agent) as ws:
        await send_json(ws, sender=agent, type=MessageType.CLAIM, task_id=task_id)
        return await read_until_type(ws, expected)


async def test_forwards_a_remote_owned_claim_and_relays_the_grant() -> None:
    forwarder = _FakeForwarder(result=_granted())
    hub = _edge_hub(forwarder)
    async with running_hub(hub) as (_, uri):
        reply = await _claim(uri, _AGENT, MessageType.CLAIM_GRANTED)
    assert reply["owner"] == _AGENT
    assert reply["task_id"] == "t1"
    # The claim was forwarded with the claimant and namespace, not granted locally.
    request, peer_uri, local_id, _token = forwarder.calls[0]
    assert request.namespace == _NAMESPACE
    assert request.claimant == _AGENT
    assert peer_uri == "ws://owner/"
    assert local_id == _EDGE
    assert "t1" not in hub.state.claims


async def test_relays_the_owners_denial() -> None:
    denied = ClaimForwardResult(
        granted=False,
        task_id="t1",
        namespace=_NAMESPACE,
        owner_hub_id=_OWNER,
        detail="task already held",
    )
    hub = _edge_hub(_FakeForwarder(result=denied))
    async with running_hub(hub) as (_, uri):
        reply = await _claim(uri, _AGENT, MessageType.CLAIM_DENIED)
    assert reply["payload"] == "task already held"


async def test_a_failed_forward_falls_back_to_refusing_and_naming_the_owner() -> None:
    hub = _edge_hub(_FakeForwarder(error=ClaimForwardError("owner unreachable")))
    async with running_hub(hub) as (_, uri):
        reply = await _claim(uri, _AGENT, MessageType.CLAIM_DENIED)
    assert reply["owner_hub_id"] == _OWNER
    assert reply["ownership"] == "remote"


async def test_a_remote_claim_without_a_route_is_refused_and_names_the_owner() -> None:
    forwarder = _FakeForwarder(result=_granted())
    hub = _edge_hub(forwarder, peers=False)
    async with running_hub(hub) as (_, uri):
        reply = await _claim(uri, _AGENT, MessageType.CLAIM_DENIED)
    assert reply["owner_hub_id"] == _OWNER
    assert forwarder.calls == []  # no route configured, so nothing was forwarded


async def test_a_remote_claim_without_a_task_id_is_refused() -> None:
    forwarder = _FakeForwarder(result=_granted())
    hub = _edge_hub(forwarder)
    async with running_hub(hub) as (_, uri):
        reply = await _claim(uri, _AGENT, MessageType.CLAIM_DENIED, task_id="")
    assert reply["ownership"] == "remote"
    assert forwarder.calls == []  # no task id to forward


async def test_an_ungoverned_namespace_is_still_refused_without_forwarding() -> None:
    forwarder = _FakeForwarder(result=_granted())
    hub = SynapseHub(
        hub_id=_EDGE,
        namespace_ownership=NamespaceOwnership(owners={}, local_hub_id=_EDGE),
        claim_peers={_OWNER: ClaimForwardPeer(uri="ws://owner/")},
        claim_forwarder=forwarder,
    )
    async with running_hub(hub) as (_, uri):
        reply = await _claim(uri, _AGENT, MessageType.CLAIM_DENIED)
    assert reply["ownership"] == "ungoverned"
    assert forwarder.calls == []


async def test_a_locally_owned_claim_still_grants_without_forwarding() -> None:
    forwarder = _FakeForwarder(result=_granted())
    hub = SynapseHub(
        hub_id=_EDGE,
        namespace_ownership=NamespaceOwnership(owners={_NAMESPACE: _EDGE}, local_hub_id=_EDGE),
        claim_peers={_OWNER: ClaimForwardPeer(uri="ws://owner/")},
        claim_forwarder=forwarder,
    )
    async with running_hub(hub) as (_, uri):
        reply = await _claim(uri, _AGENT, MessageType.CLAIM_GRANTED)
    assert reply["owner"] == _AGENT
    assert hub.state.claims["t1"].owner == _AGENT  # granted on the local path
    assert forwarder.calls == []


# --- real two-hub forwarding: a live edge hub forwards to a live owning hub ---------------


def _write_peer_cert(tmp_path: Path) -> tuple[str, bytes]:
    """Write a self-signed certificate for the edge hub; return its pin and live DER bytes."""
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
    """Build the owner's serving policy trusting the edge hub under ``pin`` off any socket."""
    return MultiHubServingPolicy(
        federation=FederationBundle(
            [
                FederationPeer(
                    domain_id=_DOMAIN,
                    namespaces=frozenset({_NAMESPACE}),
                    certificate_pins=frozenset({pin}),
                    signing_key_ids=frozenset({_KEY}),
                    scope_grants=(ScopeGrant(verb="claim", namespace=_NAMESPACE),),
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


async def test_a_live_edge_hub_forwards_a_claim_to_a_live_owning_hub(tmp_path: Path) -> None:
    pin, der = _write_peer_cert(tmp_path)
    owner = SynapseHub(
        hub_id=_OWNER,
        namespace_ownership=NamespaceOwnership(owners={_NAMESPACE: _OWNER}, local_hub_id=_OWNER),
        multihub_serving_policy=_owner_serving_policy(pin, der),
    )
    async with running_hub(owner) as (_, owner_uri):
        edge = SynapseHub(
            hub_id=_EDGE,
            namespace_ownership=_edge_ownership(),
            claim_peers={_OWNER: ClaimForwardPeer(uri=owner_uri)},
        )
        async with running_hub(edge) as (_, edge_uri):
            reply = await _claim(edge_uri, _AGENT, MessageType.CLAIM_GRANTED)
    # The grant relayed to the claimant is the owner's authoritative lease.
    assert reply["owner"] == _AGENT
    assert reply["task_id"] == "t1"
    # The lease lives on the owning hub, not the edge.
    assert owner.state.claims["t1"].owner == _AGENT
    assert "t1" not in edge.state.claims
