# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li — serving half of a cross-hub operator relay, over real sockets

from __future__ import annotations

import subprocess
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from websockets.asyncio.client import ClientConnection, connect

from hub_e2e_helpers import read_until_type, running_hub, send_json
from synapse_channel.core.federation import FederationBundle, FederationPeer, ScopeGrant
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.multihub_serving import MultiHubServingGrant, MultiHubServingPolicy
from synapse_channel.core.namespace_ownership import NamespaceOwnership
from synapse_channel.core.operator_relay_wire import (
    RelayActionRequest,
    RelayActionResult,
    decode_relay_result,
    encode_relay_request,
)
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.tls import (
    MTLSPeerTrustBundle,
    MTLSTrustedPeer,
    certificate_sha256_pin,
)

_REQUEST = MessageType.OPERATOR_RELAY_REQUEST
_REPLY = MessageType.OPERATOR_RELAY_RESULT
_NAMESPACE = "SYNAPSE-CHANNEL"
_ACTING = "syn-a"
_DOMAIN = "domain-b"
_KEY = "SYNAPSE-CHANNEL:main:2026-06"
_HOLDER = "SYNAPSE-CHANNEL/holder"


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
    """Build a serving policy trusting ``sender`` to relay a *release* into the namespace."""
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
            sender: MultiHubServingGrant(
                domain_id=_DOMAIN, namespace=_NAMESPACE, signing_key_id=_KEY
            )
        },
        clock=lambda: 0.0,
        cert_source=lambda _websocket: der,
    )


def _acting_hub(
    *,
    policy: MultiHubServingPolicy | None,
    ownership: NamespaceOwnership | None,
    journal: EventStore | None = None,
    require_relay_reason: bool = False,
) -> SynapseHub:
    """Return a hub configured with the given serving policy, ownership map, and journal."""
    return SynapseHub(
        hub_id=_ACTING,
        multihub_serving_policy=policy,
        namespace_ownership=ownership,
        journal=journal,
        require_relay_reason=require_relay_reason,
    )


def _owns() -> NamespaceOwnership:
    """Return an ownership map under which this hub authoritatively owns the namespace."""
    return NamespaceOwnership(owners={_NAMESPACE: _ACTING}, local_hub_id=_ACTING)


def _request(
    action: str = "release",
    task_id: str = "t1",
    *,
    reason: str = "",
    break_glass: bool = False,
) -> RelayActionRequest:
    return RelayActionRequest(
        action=action,
        namespace=_NAMESPACE,
        task_id=task_id,
        operator="ops-admin",
        origin_hub_id=_DOMAIN,
        reason=reason,
        break_glass=break_glass,
    )


async def _connect(uri: str, name: str) -> ClientConnection:
    """Open a raw client socket, drain the welcome, and register with a heartbeat."""
    websocket = await connect(uri)
    await read_until_type(websocket, "welcome")
    await send_json(websocket, sender=name, type="heartbeat")
    return websocket


async def _relay(uri: str, request: RelayActionRequest) -> RelayActionResult:
    """Relay one action as a peer hub and decode the result reply."""
    async with await _connect(uri, "peer") as ws:
        await send_json(ws, sender="peer", type=_REQUEST, **encode_relay_request(request))
        message = await read_until_type(ws, _REPLY)
    return decode_relay_result(message)


async def test_applies_a_relayed_release_and_audits_it(tmp_path: Path) -> None:
    pin, der = _write_peer_cert(tmp_path)
    journal = EventStore(tmp_path / "events.db")
    hub = _acting_hub(policy=_serving_policy(pin, der), ownership=_owns(), journal=journal)
    hub.state.claim(_HOLDER, "t1")
    async with running_hub(hub) as (_, uri):
        request = _request(reason="lease wedged by a crashed agent", break_glass=True)
        result = await _relay(uri, request)
    assert result.applied is True
    assert result.owner_hub_id == _ACTING
    assert "was held by" in result.detail
    assert "t1" not in hub.state.claims
    # Journalled twice: a release keeps state reconstruction correct, an operator_relay
    # event records the cross-hub provenance the release alone never carries.
    kinds = [event.kind for event in journal.read_all()]
    assert EventKind.RELEASE in kinds
    assert EventKind.OPERATOR_RELAY in kinds
    audit = next(e.payload for e in journal.read_all() if e.kind == EventKind.OPERATOR_RELAY)
    assert audit["action"] == "release"
    assert audit["direction"] == "in"  # the applying (owning) side of the two-hub trail
    assert audit["peer"] == "peer"
    assert audit["operator"] == "ops-admin"
    assert audit["origin_hub_id"] == _DOMAIN
    assert audit["reason"] == "lease wedged by a crashed agent"
    assert audit["break_glass"] is True
    assert audit["previous_owner"] == _HOLDER
    assert audit["applied"] is True


async def test_refuses_a_relay_without_a_reason_when_the_hub_requires_one(tmp_path: Path) -> None:
    pin, der = _write_peer_cert(tmp_path)
    hub = _acting_hub(
        policy=_serving_policy(pin, der), ownership=_owns(), require_relay_reason=True
    )
    hub.state.claim(_HOLDER, "t1")
    async with running_hub(hub) as (_, uri):
        refused = await _relay(uri, _request())  # no reason
        applied = await _relay(uri, _request(reason="freeing a wedged release"))
    assert refused.applied is False
    assert refused.detail == "reason_required"
    assert applied.applied is True  # the same relay with a reason is authorised
    assert "t1" not in hub.state.claims


async def test_notifies_the_hubs_own_agents_that_the_lease_was_revoked(tmp_path: Path) -> None:
    pin, der = _write_peer_cert(tmp_path)
    hub = _acting_hub(policy=_serving_policy(pin, der), ownership=_owns())
    hub.state.claim(_HOLDER, "t1")
    async with running_hub(hub) as (_, uri):
        async with await _connect(uri, "watcher") as watcher:
            await _relay(uri, _request())
            revoked = await read_until_type(watcher, MessageType.RELEASE_GRANTED)
    assert revoked["task_id"] == "t1"
    assert "released by operator relay" in revoked["payload"]


async def test_an_authorised_release_of_an_unclaimed_task_is_a_no_op(tmp_path: Path) -> None:
    pin, der = _write_peer_cert(tmp_path)
    journal = EventStore(tmp_path / "events.db")
    hub = _acting_hub(policy=_serving_policy(pin, der), ownership=_owns(), journal=journal)
    async with running_hub(hub) as (_, uri):
        result = await _relay(uri, _request(task_id="never-claimed"))
    assert result.applied is False
    assert "not currently claimed" in result.detail
    # A no-op mutates nothing, so it journals nothing.
    assert [e.kind for e in journal.read_all()] == []


async def test_refuses_a_relay_when_no_serving_policy_is_configured() -> None:
    hub = _acting_hub(policy=None, ownership=_owns())
    hub.state.claim(_HOLDER, "t1")
    async with running_hub(hub) as (_, uri):
        result = await _relay(uri, _request())
    assert result.applied is False
    assert result.detail == "peer_not_authorised"
    # The lease is untouched.
    assert hub.state.claims["t1"].owner == _HOLDER


async def test_refuses_a_relay_from_an_untrusted_certificate(tmp_path: Path) -> None:
    pin, _trusted = _write_peer_cert(tmp_path)
    _other_pin, stranger_der = _write_peer_cert(tmp_path / "other")
    hub = _acting_hub(policy=_serving_policy(pin, stranger_der), ownership=_owns())
    hub.state.claim(_HOLDER, "t1")
    async with running_hub(hub) as (_, uri):
        result = await _relay(uri, _request())
    assert result.applied is False
    assert result.detail == "peer_not_authorised"
    assert hub.state.claims["t1"].owner == _HOLDER


async def test_refuses_a_relay_when_this_hub_cannot_prove_it_owns_the_namespace(
    tmp_path: Path,
) -> None:
    # With no ownership map the origin-routing gate steps aside, and the serving handler still
    # refuses fail-closed: a hub that cannot prove it authoritatively owns the namespace never
    # applies a relayed release. (A remote-owned namespace is instead intercepted by the gate
    # and forwarded or refused there — see test_hub_operator_relay_forwarding.)
    pin, der = _write_peer_cert(tmp_path)
    hub = _acting_hub(policy=_serving_policy(pin, der), ownership=None)
    hub.state.claim(_HOLDER, "t1")
    async with running_hub(hub) as (_, uri):
        result = await _relay(uri, _request())
    assert result.applied is False
    assert result.detail == "namespace_not_owned"
    assert hub.state.claims["t1"].owner == _HOLDER  # the lease is untouched


async def test_refuses_an_unregistered_action(tmp_path: Path) -> None:
    pin, der = _write_peer_cert(tmp_path)
    hub = _acting_hub(policy=_serving_policy(pin, der), ownership=_owns())
    async with running_hub(hub) as (_, uri):
        result = await _relay(uri, _request(action="delete-everything"))
    assert result.applied is False
    assert result.detail == "unknown_action"


async def test_a_malformed_relay_request_is_answered_with_an_error(tmp_path: Path) -> None:
    pin, der = _write_peer_cert(tmp_path)
    hub = _acting_hub(policy=_serving_policy(pin, der), ownership=_owns())
    async with running_hub(hub) as (_, uri):
        async with await _connect(uri, "peer") as ws:
            # No ``operator`` field: the codec rejects it before authorisation runs.
            await send_json(
                ws,
                sender="peer",
                type=_REQUEST,
                action="release",
                namespace=_NAMESPACE,
                task_id="t1",
                origin_hub_id=_DOMAIN,
            )
            message = await read_until_type(ws, MessageType.ERROR)
    assert "Malformed operator relay request" in message["payload"]
